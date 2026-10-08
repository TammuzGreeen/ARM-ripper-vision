"""Fail-closed two-model recognition gate used only for rip authorization.

The recognizers return transcription text only. Deterministic extraction is
compared with independently maintained, approved masterlists; no evaluator or
model-generated structured metadata can authorize a rip.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import unicodedata
from pathlib import Path

import cv2
import httpx

from .formats import digest
from .text_normalization import normalize_structural_labels

PRIMARY_MODEL = "qwen3-vl:30b-a3b-instruct-q4_K_M"
SECONDARY_MODEL = "qwen2.5vl:7b-q8_0"
MODEL_ORDER = (PRIMARY_MODEL, SECONDARY_MODEL)
PROMPT = (
    "Read this as a DVD or Blu-ray disc label. Return separate plain-text lines for the visible fields: "
    "Series/title:, Season:, Disc:, Printed episodes/range:, and Edition/version:. "
    "Do not omit a legible field; copy its original-language marker and exact names/numbers, "
    "and include episode titles only when printed. Omit a field if it is not readable; never infer, "
    "translate, normalize, or repeat text. Ignore ratings, copyright or rights notices, age classifications, "
    "catalog/barcode codes, credits, decorative slogans, and repeated legal text. "
    "Return at most 12 short lines."
)
# Three full camera crops require >7k prompt tokens for the secondary Qwen VL tag.
# Keep all three independent frames at original configured resolution; do not
# truncate images or weaken the two-model gate to fit a smaller context.
OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 1536, "num_gpu": 0}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _phrase(text: str, value: str) -> bool:
    needle = normalize(value)
    haystack = normalize(text)
    return bool(needle and f" {needle} " in f" {haystack} ")


def _resolve_tag(tag_rows: list[dict], requested: str) -> dict | None:
    return next((row for row in tag_rows if row.get("name") == requested or row.get("model") == requested), None)


def preflight(settings, *, transport=None) -> dict:
    """Verify exact local Ollama tags before enabling a batch."""
    headers = {"Authorization": "Bearer " + settings.vision_key} if settings.vision_key else {}
    try:
        with httpx.Client(timeout=settings.vision_timeout, follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            version_response = client.get(settings.vision_url + "/api/version", headers=headers)
            tags_response = client.get(settings.vision_url + "/api/tags", headers=headers)
        version_response.raise_for_status()
        tags_response.raise_for_status()
        version = version_response.json().get("version")
        models = tags_response.json().get("models", [])
        resolved = {}
        issues = []
        for tag in MODEL_ORDER:
            row = _resolve_tag(models, tag)
            if not row or not isinstance(row.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["digest"]):
                issues.append(f"Exact model tag or digest unavailable: {tag}")
            else:
                resolved[tag] = {
                    "requested_tag": tag, "resolved_tag": row.get("name") or row.get("model"),
                    "digest": row["digest"], "size_bytes": row.get("size"),
                    "quantization": (row.get("details") or {}).get("quantization_level"),
                }
        return {"ollama_version": version, "models": resolved, "issues": issues,
                "ready": not issues, "checked_at": time.time()}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return {"ollama_version": None, "models": {}, "issues": ["Could not verify exact Ollama model tags"],
                "ready": False, "checked_at": time.time()}


def _save_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _encoded_images(images, evidence_dir: Path) -> tuple[list[str], list[dict]]:
    encoded, refs = [], []
    for index, frame in enumerate(images):
        height, width = frame.shape[:2]
        if max(width, height) > 1024:
            frame = cv2.resize(frame, (round(width * 1024 / max(width, height)),
                                      round(height * 1024 / max(width, height))))
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            raise ValueError("Could not encode recognition evidence")
        content = jpeg.tobytes()
        name = f"agreement-input-{index}.jpg"
        _save_exclusive(evidence_dir / name, content)
        encoded.append(base64.b64encode(content).decode("ascii"))
        refs.append({"file": name, "sha256": hashlib.sha256(content).hexdigest(),
                     "bytes": len(content), "width": int(frame.shape[1]), "height": int(frame.shape[0])})
    return encoded, refs


def _run_model(settings, tag: str, meta: dict, encoded: list[str], evidence_dir: Path,
               *, transport=None) -> dict:
    slug = "primary-30b" if tag == PRIMARY_MODEL else "secondary-7b"
    request_path = evidence_dir / f"agreement-{slug}-request.json"
    response_path = evidence_dir / f"agreement-{slug}-stream.jsonl"
    body = {
        "model": tag, "stream": True, "think": False, "keep_alive": 0, "options": dict(OPTIONS),
        "messages": [{"role": "user", "content": PROMPT, "images": encoded}],
    }
    request_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    started = time.monotonic()
    result = {
        "model_tag": tag, "model_digest": meta.get("digest"), "quantization": meta.get("quantization"),
        "size_bytes": meta.get("size_bytes"), "request_file": request_path.name,
        "response_file": response_path.name, "http_status": None, "done": False,
        "done_reason": None, "transcription": "", "usable": False, "error": None,
    }
    stream_bytes = bytearray()
    pending_line = bytearray()
    text_parts = []
    last_payload = None

    def parse_line(raw_line: bytes) -> None:
        nonlocal last_payload
        if not raw_line.strip():
            return
        payload = json.loads(raw_line)
        last_payload = payload
        part = (payload.get("message") or {}).get("content")
        if isinstance(part, str):
            text_parts.append(part)
        if payload.get("done") is True:
            result["done"] = True
            result["done_reason"] = payload.get("done_reason")

    try:
        _save_exclusive(request_path, request_bytes)
        headers = {"Authorization": "Bearer " + settings.vision_key} if settings.vision_key else {}
        with httpx.Client(timeout=settings.vision_timeout, follow_redirects=False,
                          trust_env=False, transport=transport) as client:
            with client.stream("POST", settings.vision_url + "/api/chat", json=body, headers=headers) as response:
                result["http_status"] = response.status_code
                for chunk in response.iter_bytes():
                    stream_bytes.extend(chunk)
                    if len(stream_bytes) > MAX_RESPONSE_BYTES:
                        raise ValueError("Ollama response exceeded the retained response limit")
                    pending_line.extend(chunk)
                    while b"\n" in pending_line:
                        raw_line, _, rest = pending_line.partition(b"\n")
                        pending_line[:] = rest
                        parse_line(raw_line)
                if pending_line:
                    parse_line(bytes(pending_line))
                if response.status_code != 200:
                    result["error"] = f"Ollama returned HTTP {response.status_code}"
    except httpx.TimeoutException:
        result["error"] = "Recognition request timed out"
    except httpx.HTTPError:
        result["error"] = "Could not reach the configured Ollama service"
    except (ValueError, json.JSONDecodeError):
        result["error"] = "Recognition returned malformed or oversized output"
    finally:
        # Keep partial response bytes on operational failure, too.
        try:
            _save_exclusive(response_path, bytes(stream_bytes))
        except FileExistsError:
            result["error"] = result["error"] or "Response evidence path already exists"
    result["transcription"] = "".join(text_parts).strip()
    result["prompt_eval_count"] = (last_payload or {}).get("prompt_eval_count")
    result["eval_count"] = (last_payload or {}).get("eval_count")
    result["runtime_seconds"] = round(time.monotonic() - started, 3)
    result["usable"] = bool(result["http_status"] == 200 and result["done"]
                            and result["done_reason"] == "stop" and result["transcription"]
                            and not result["error"])
    if not result["usable"] and not result["error"]:
        result["error"] = "No nonempty natural completion was produced"
    return result


def _parse_text(text: str, masters: list) -> dict:
    # Normalize field labels only; identity-bearing text is matched against the
    # untouched transcription below and is never translated.
    label_text = normalize_structural_labels(text)
    approved = [master for master in masters if master.approved]
    series = set()
    known_editions = {}
    known_identifiers = {}
    for master in approved:
        for value in [master.series, *master.title_aliases]:
            if _phrase(text, value):
                series.add(normalize(master.series))
        for value in master.edition_tokens:
            if _phrase(text, value):
                known_editions.setdefault(master.id, set()).add(normalize(value))
        for disc in master.discs:
            for value in disc.printed_identifiers:
                if _phrase(text, value):
                    known_identifiers.setdefault((master.id, disc.id), set()).add(normalize(value))
    seasons = {int(a or b or c) for a, b, c in re.findall(
        r"(?i)\b(?:season|temporada|staffel|saison)\s*[:.#-]?\s*(\d{1,2})\b|"
        r"\b(\d{1,2})\s*\.?\s*(?:season|temporada|staffel|saison)\b|\bS(\d{1,2})E\d{1,3}\b", label_text
    )}
    episode_numbers = set()
    for start, end in re.findall(
        r"(?i)\b(?:episode|eps?\.?|episode\s+range)\s*[:#]?\s*(\d{1,3})\s*(?:[-–—]\s*(\d{1,3}))?", label_text
    ):
        first, last = int(start), int(end or start)
        if last < first or last - first > 100:
            continue
        episode_numbers.update(range(first, last + 1))
    for season, first, last in re.findall(r"(?i)\bS(\d{1,2})E(\d{1,3})(?:\s*[-–—]\s*(?:S\d{1,2})?E?(\d{1,3}))?", label_text):
        episode_numbers.update(range(int(first), int(last or first) + 1))
        seasons.add(int(season))
    titles = set()
    for master in approved:
        for disc in master.discs:
            for episode in disc.episodes:
                if _phrase(text, episode.title):
                    titles.add(normalize(episode.title))
    disc_numbers = {int(value) for value in re.findall(
        r"(?i)\b(?:disc|disk)\s*(?:number\s*)?(?:no\.?\s*)?#?\s*(\d{1,2})\b", label_text)}
    return {"series": sorted(series), "seasons": sorted(seasons),
            "episodes": sorted(episode_numbers), "titles": sorted(titles),
            "disc_numbers": sorted(disc_numbers),
            "edition_master_ids": sorted(known_editions),
            "printed_identifier_disc_ids": sorted(f"{master_id}/{disc_id}" for master_id, disc_id in known_identifiers)}


def _expected_episodes(disc) -> set[int]:
    result: set[int] = set()
    for episode in disc.episodes:
        found = re.fullmatch(r"\s*(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?\s*", episode.printed)
        if found:
            first, last = int(found[1]), int(found[2] or found[1])
            result.update(range(first, last + 1))
    return result


def _candidate_fields(parsed: dict, master, disc) -> dict:
    expected_titles = sorted({normalize(episode.title) for episode in disc.episodes if episode.title})
    expected_episodes = sorted(_expected_episodes(disc))
    return {
        "series": {"value": master.series, "observed": parsed["series"],
                   "status": "CORRECT" if normalize(master.series) in parsed["series"] else "OMITTED"},
        "season": {"value": master.season, "observed": parsed["seasons"],
                   "status": "CORRECT" if parsed["seasons"] == [master.season] else "WRONG_BUT_PLAUSIBLE" if parsed["seasons"] else "OMITTED"},
        "episodes": {"value": expected_episodes, "observed": parsed["episodes"],
                     "status": "CORRECT" if parsed["episodes"] == expected_episodes and expected_episodes else "WRONG_BUT_PLAUSIBLE" if parsed["episodes"] else "OMITTED"},
        "titles": {"value": expected_titles, "observed": parsed["titles"],
                   "required": bool(expected_titles),
                   "status": "CORRECT" if set(expected_titles) == set(parsed["titles"]) and expected_titles else "WRONG_BUT_PLAUSIBLE" if parsed["titles"] else "OMITTED"},
    }


def evaluate(transcriptions: dict[str, str], run_results: dict[str, dict], masters: list) -> dict:
    parsed = {tag: _parse_text(transcriptions.get(tag, ""), masters) for tag in MODEL_ORDER}
    operational_failures = [tag for tag in MODEL_ORDER if not run_results.get(tag, {}).get("usable")]
    disagreements = []
    for field in ("series", "seasons", "episodes", "titles", "disc_numbers",
                  "edition_master_ids", "printed_identifier_disc_ids"):
        if parsed[PRIMARY_MODEL][field] != parsed[SECONDARY_MODEL][field]:
            disagreements.append({"field": field, "primary": parsed[PRIMARY_MODEL][field],
                                  "secondary": parsed[SECONDARY_MODEL][field]})

    candidates = []
    all_approved = [master for master in masters if master.approved]
    edition_observations = [parsed[tag]["edition_master_ids"] for tag in MODEL_ORDER]
    identifier_observations = [parsed[tag]["printed_identifier_disc_ids"] for tag in MODEL_ORDER]
    explicit_edition_conflict = any(len(values) > 1 for values in edition_observations)
    explicit_identifier_conflict = any(len(values) > 1 for values in identifier_observations)
    if all(edition_observations) and edition_observations[0] != edition_observations[1]:
        explicit_edition_conflict = True
    if all(identifier_observations) and identifier_observations[0] != identifier_observations[1]:
        explicit_identifier_conflict = True
    for master in all_approved:
        for disc in master.discs:
            if explicit_edition_conflict or explicit_identifier_conflict:
                continue
            explicit_disc_numbers = [parsed[tag]["disc_numbers"] for tag in MODEL_ORDER if parsed[tag]["disc_numbers"]]
            if any(values != [disc.number] for values in explicit_disc_numbers):
                continue
            explicitly_identified_editions = set(parsed[PRIMARY_MODEL]["edition_master_ids"] + parsed[SECONDARY_MODEL]["edition_master_ids"])
            if explicitly_identified_editions and master.id not in explicitly_identified_editions:
                continue
            explicitly_identified_discs = set(parsed[PRIMARY_MODEL]["printed_identifier_disc_ids"] + parsed[SECONDARY_MODEL]["printed_identifier_disc_ids"])
            if explicitly_identified_discs and f"{master.id}/{disc.id}" not in explicitly_identified_discs:
                continue
            field_sets = {tag: _candidate_fields(parsed[tag], master, disc) for tag in MODEL_ORDER}
            fields_equal = all(field_sets[PRIMARY_MODEL][name]["status"] == "CORRECT"
                               and field_sets[SECONDARY_MODEL][name]["status"] == "CORRECT"
                               for name in ("series", "season", "episodes", "titles")
                               if field_sets[PRIMARY_MODEL][name].get("required", True))
            if fields_equal:
                candidates.append((master, disc, field_sets))

    missing = []
    wrong = []
    for master in all_approved:
        for disc in master.discs:
            for tag in MODEL_ORDER:
                fields = _candidate_fields(parsed[tag], master, disc)
                for name, value in fields.items():
                    if not value.get("required", True) or value["status"] == "CORRECT":
                        continue
                    entry = {"model": tag, "field": name, "expected": value["value"],
                             "observed": value["observed"], "status": value["status"],
                             "candidate_master": master.id, "candidate_disc": disc.id}
                    (missing if value["status"] == "OMITTED" else wrong).append(entry)
    # Keep diagnostics scoped to plausible candidates (series/season evidence); if
    # none are found, retain one concise set rather than multiplying every master.
    if candidates:
        missing, wrong = [], []

    reasons = []
    if operational_failures:
        reasons.append("operational_recognition_failure")
    if disagreements:
        reasons.append("model_disagreement_on_priority_fields")
    if explicit_edition_conflict:
        reasons.append("conflicting_or_ambiguous_edition_identifiers")
    if explicit_identifier_conflict:
        reasons.append("conflicting_or_ambiguous_printed_disc_identifiers")
    if len(parsed[PRIMARY_MODEL]["series"]) > 1 or len(parsed[SECONDARY_MODEL]["series"]) > 1:
        reasons.append("ambiguous_series")
    if len(parsed[PRIMARY_MODEL]["seasons"]) > 1 or len(parsed[SECONDARY_MODEL]["seasons"]) > 1:
        reasons.append("conflicting_or_ambiguous_season")
    if not candidates:
        if missing:
            reasons.append("missing_required_priority_fields")
        if wrong:
            reasons.append("wrong_or_malformed_priority_fields")
        if not reasons:
            reasons.append("no_unique_complete_approved_disc_match")
    if len(candidates) > 1:
        reasons.append("ambiguous_multiple_disc_matches")
    passed = not reasons and len(candidates) == 1 and not operational_failures and not disagreements
    match_rows = []
    normalized_candidates = []
    if passed:
        master, disc, fields = candidates[0]
        match_rows = [{"master": master.id, "disc": disc.id, "series": master.series,
                       "season": master.season, "disc_number": disc.number, "edition": master.edition_name,
                       "masterlist_sha256": digest(master), "confidence": 1.0}]
        normalized_candidates = [{"master": master.id, "disc": disc.id,
                                  "primary": fields[PRIMARY_MODEL], "secondary": fields[SECONDARY_MODEL]}]
    return {
        "policy": "two-model-priority-field-agreement-v1",
        "status": "PASS" if passed else "REJECTED_FOR_REVIEW",
        "accepted": passed, "reasons": sorted(set(reasons)), "operational_failures": operational_failures,
        "normalized_priority_fields": parsed, "field_disagreements": disagreements,
        "missing_fields": missing, "wrong_fields": wrong,
        "validated_candidates": normalized_candidates, "matches": match_rows,
    }


def recognize(settings, images: list, event_id: str, masters: list, *, transport=None) -> dict:
    evidence_dir = settings.state / "evidence" / event_id
    result = {
        "accepted": False, "test_only": False, "policy": "two-model-priority-field-agreement-v1",
        "backend": settings.recognition_backend, "camera": settings.camera,
        "model_tags": list(MODEL_ORDER), "frames": [
            {"evidence": f"{event_id}/{index}.jpg"} for index in range(len(images))
        ], "runs": {}, "reason": "Agreement gate has not completed",
    }
    try:
        gate = preflight(settings, transport=transport)
        result["ollama_preflight"] = gate
        encoded, image_refs = _encoded_images(images, evidence_dir)
        result["inference_images"] = image_refs
        if not gate["ready"]:
            for tag in MODEL_ORDER:
                result["runs"][tag] = {
                    "model_tag": tag, "model_digest": (gate.get("models", {}).get(tag) or {}).get("digest"),
                    "usable": False, "error": "Exact model tag/digest preflight failed",
                    "runtime_seconds": 0.0, "transcription": "", "done_reason": None,
                }
        else:
            # Always attempt both exact recognizers, even if the primary fails.
            for tag in MODEL_ORDER:
                result["runs"][tag] = _run_model(settings, tag, gate["models"][tag], encoded,
                                                 evidence_dir, transport=transport)
        run_results = result["runs"]
        gate_result = evaluate({tag: run_results[tag].get("transcription", "") for tag in MODEL_ORDER},
                               run_results, masters)
        result["agreement"] = gate_result
        result["matches"] = gate_result["matches"]
        result["accepted"] = gate_result["accepted"]
        result["reason"] = ("Both models agree on all required priority fields" if result["accepted"]
                            else "; ".join(gate_result["reasons"]))
    except Exception as exc:
        # Preserve failures as reviewable evidence, never convert them to a pass.
        result["agreement"] = {
            "policy": "two-model-priority-field-agreement-v1", "status": "REJECTED_FOR_REVIEW",
            "accepted": False, "reasons": ["recognition_pipeline_failure"],
            "operational_failures": list(MODEL_ORDER), "normalized_priority_fields": {},
            "field_disagreements": [], "missing_fields": [], "wrong_fields": [], "matches": [],
        }
        result["matches"] = []
        result["reason"] = "Agreement recognition failed; ejection and manual review required"
        result["pipeline_error"] = type(exc).__name__
    return result
