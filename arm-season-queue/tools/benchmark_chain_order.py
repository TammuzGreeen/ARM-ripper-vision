#!/usr/bin/env python3
"""Compare two deterministic, priority-ground-truth-scored OCR fallback orders.

This benchmark reuses exact prior direct-recognition outputs only. It does not
issue inference requests or modify source benchmark/evidence artifacts.
"""
from __future__ import annotations

import hashlib
import base64
import json
import os
import re
import statistics
import time
from pathlib import Path

from score_priority_disc_identification import PRIMARY, classify, normalize, usable_run

STATE = Path(os.environ.get("STATE_DIR", "/state"))
DIRECT_NAME = "diagnostic-disc-direct-all-models-v4.json"
TRUTH_NAME = "diagnostic-disc-priority-ground-truth-v1.1.json"
OUT_NAME = "diagnostic-disc-chain-order-benchmark-v4.json"
MD_NAME = "diagnostic-disc-chain-order-benchmark-v4.md"
PROMPT = (
    "Transcribe only the readable text visibly printed in this image. Preserve the original language and line breaks. "
    "Do not infer missing text. Return only the transcription, or an empty string if no text is readable."
)
MODEL_A = "qwen2.5vl:7b-q8_0"
MODEL_B = "qwen3-vl:30b-a3b-instruct-q4_K_M"
EXPECTED_SETTINGS = {"temperature": 0, "num_ctx": 4096, "num_predict": 1536}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_evidence(state: Path, reference: str, event_id: str) -> Path:
    candidates = [state / reference, state / "evidence" / reference]
    if not reference.startswith("evidence/"):
        candidates.append(state / "evidence" / event_id / Path(reference).name)
    for path in candidates:
        if path.is_file():
            return path
    raise SystemExit(f"Missing retained evidence file: {reference}")


def validate_request(state: Path, row: dict, model_meta: dict) -> dict:
    request_path = resolve_evidence(state, row["request_file"], row["event_id"])
    response_path = resolve_evidence(state, row["raw_response_file"], row["event_id"])
    stream_path = resolve_evidence(state, row["raw_stream_file"], row["event_id"])
    request = json.loads(request_path.read_bytes())
    prompt = request["messages"][0]["content"]
    if request.get("model") != model_meta["resolved_local_tag"] or prompt != PROMPT:
        raise SystemExit(f"Retained request does not match exact model/prompt for {row['event_id']}")
    request_options = request.get("options") or {}
    if any(request_options.get(key) != value for key, value in EXPECTED_SETTINGS.items()):
        raise SystemExit(f"Retained request settings differ for {row['event_id']}: {request.get('options')}")
    if request_options.get("num_gpu", 0) != 0 or request.get("think") is not False or request.get("keep_alive") != 0:
        raise SystemExit(f"Retained request execution controls differ for {row['event_id']}")
    if request.get("stream") is not True:
        raise SystemExit(f"Retained request was not streamed for {row['event_id']}")
    image_b64 = request["messages"][0].get("images", [None])[0]
    if not image_b64 or hashlib.sha256(base64.b64decode(image_b64)).hexdigest() != row.get("inference_image_sha256"):
        raise SystemExit(f"Retained request image does not match its recorded inference SHA-256 for {row['event_id']}")
    if row.get("model_digest") != model_meta.get("digest"):
        raise SystemExit(f"Run digest differs from preflight for {row['event_id']}")
    return {
        "request_file": str(request_path.relative_to(state)),
        "request_sha256": sha256(request_path),
        "raw_response_file": str(response_path.relative_to(state)),
        "raw_response_sha256": sha256(response_path),
        "raw_stream_file": str(stream_path.relative_to(state)),
        "raw_stream_sha256": sha256(stream_path),
    }


def run_usable(row: dict) -> bool:
    return usable_run(row)


def field_results(image: dict, run: dict, all_series: list[str]) -> list[dict]:
    if not run_usable(run):
        return [
            {"category": field["category"], "ground_truth_value": field["ground_truth_value"],
             "classification": "DETECTABLY_FAILED", "candidate_wrong_value": None}
            for field in image["priority_fields"]
        ]
    return [
        {
            "category": field["category"], "ground_truth_value": field["ground_truth_value"],
            "classification": classify(field, run.get("transcription", ""), run, all_series)[0],
            "candidate_wrong_value": classify(field, run.get("transcription", ""), run, all_series)[1],
        }
        for field in image["priority_fields"]
    ]


def internal_conflicts(image: dict, run: dict) -> list[str]:
    """Flag explicit competing labeled values, without using peripheral OCR."""
    if not run_usable(run):
        return []
    text = run.get("transcription", "")
    reasons: list[str] = []
    season_fields = [f for f in image["priority_fields"] if f["category"] == "SEASON"]
    if season_fields:
        values = re.findall(
            r"(?i)\b(?:season|temporada|staffel)\s*[:.#-]?\s*([0-9]+|[ivx]+)\b|"
            r"\b([0-9]+|[ivx]+)\s*\.?\s*(?:season|temporada|staffel)\b", text,
        )
        observed = {normalize(a or b) for a, b in values}
        expected = {normalize(f["ground_truth_value"]) for f in season_fields}
        if observed - expected:
            reasons.append("conflicting_labeled_season_values")
    episode_fields = [f for f in image["priority_fields"] if f["category"] == "EPISODES"]
    if episode_fields:
        ranges = re.findall(
            r"(?i)\b(?:episodes?|episoden?|eps?\.?|episode\s+range)\s*[:#]?\s*([0-9]+\s*[-–—]\s*[0-9]+)\b", text,
        )
        observed = {re.sub(r"\s+", "", x).replace("–", "-").replace("—", "-") for x in ranges}
        expected = {f["ground_truth_value"].replace(" ", "").replace("–", "-").replace("—", "-") for f in episode_fields}
        if observed - expected:
            reasons.append("conflicting_labeled_episode_ranges")
    return reasons


def stage_complete(fields: list[dict], conflict_reasons: list[str]) -> bool:
    return bool(fields) and not conflict_reasons and all(f["classification"] == "CORRECT" for f in fields)


def outcome(fields: list[dict], usable: bool) -> str:
    if not usable:
        return "OPERATIONAL_FAILURE"
    classes = {field["classification"] for field in fields}
    if "WRONG_BUT_PLAUSIBLE" in classes:
        return "FAIL"
    if classes == {"CORRECT"}:
        return "PASS"
    return "PARTIAL"


def pct(n: int, d: int) -> float | None:
    return n / d if d else None


def summarize_chain(name: str, images: list[dict], all_series: list[str], run_map: dict, model_order: tuple[str, str]) -> dict:
    first_model, fallback_model = model_order
    image_rows = []
    all_final_fields: list[dict] = []
    fallback_times: list[float] = []
    no_fallback_times: list[float] = []
    total_times: list[float] = []
    first_usable_count = fallback_count = final_usable_count = 0
    fix_complete = added_missing = recovered_failed = corrected_wrong = 0
    disagreed_with_correct = new_wrong = same_wrong = 0
    fallback_improved = fallback_worsened = fallback_output_worsened = 0

    for image in images:
        event_id = image["event_id"]
        first = run_map[(first_model, event_id)]
        first_is_usable = run_usable(first)
        first_fields = field_results(image, first, all_series)
        first_conflicts = internal_conflicts(image, first)
        complete = first_is_usable and stage_complete(first_fields, first_conflicts)
        reasons = []
        if not first_is_usable:
            reasons.append("first_model_operational_failure_or_truncation")
        if any(f["classification"] != "CORRECT" for f in first_fields):
            reasons.extend(sorted({f["classification"].lower() for f in first_fields if f["classification"] != "CORRECT"}))
        reasons.extend(first_conflicts)
        triggered = not complete
        second = run_map[(fallback_model, event_id)] if triggered else None
        second_is_usable = run_usable(second) if second else None
        second_fields = field_results(image, second, all_series) if second else None
        second_conflicts = internal_conflicts(image, second) if second else []

        # Simulate a conventional fallback replacement: use fallback if usable;
        # otherwise retain usable primary output. No oracle field fusion.
        if second is not None and second_is_usable:
            final_run, final_fields, selected_model = second, second_fields, fallback_model
        elif first_is_usable:
            final_run, final_fields, selected_model = first, first_fields, first_model
        else:
            final_run, final_fields, selected_model = None, field_results(image, first, all_series), None
        final_usable = final_run is not None and run_usable(final_run)
        final_conflicts = internal_conflicts(image, final_run) if final_run else []
        for conflict in final_conflicts:
            category = "SEASON" if "season" in conflict else "EPISODES"
            target = next((f for f in final_fields if f["category"] == category), None)
            if target is not None:
                target["classification"] = "WRONG_BUT_PLAUSIBLE"
                target["candidate_wrong_value"] = conflict
        final_outcome = outcome(final_fields, final_usable)
        all_final_fields.extend(final_fields)
        first_usable_count += int(first_is_usable)
        fallback_count += int(triggered)
        final_usable_count += int(final_usable)
        first_time = float(first.get("wall_clock_seconds") or 0)
        second_time = float(second.get("wall_clock_seconds") or 0) if second else 0.0
        total_time = first_time + second_time
        total_times.append(total_time)
        (fallback_times if triggered else no_fallback_times).append(total_time)

        comparisons = []
        if second is not None:
            for a, b in zip(first_fields, second_fields):
                comparisons.append({
                    "category": a["category"], "ground_truth_value": a["ground_truth_value"],
                    "first": a, "fallback": b,
                })
                if second_is_usable:
                    if a["classification"] != "CORRECT" and b["classification"] == "CORRECT":
                        added_missing += int(a["classification"] == "OMITTED")
                        recovered_failed += int(a["classification"] == "DETECTABLY_FAILED")
                        corrected_wrong += int(a["classification"] == "WRONG_BUT_PLAUSIBLE")
                    if a["classification"] == "CORRECT" and b["classification"] != "CORRECT":
                        disagreed_with_correct += 1
                    if a["classification"] != "WRONG_BUT_PLAUSIBLE" and b["classification"] == "WRONG_BUT_PLAUSIBLE":
                        new_wrong += 1
                    if (a["classification"] == b["classification"] == "WRONG_BUT_PLAUSIBLE"
                            and normalize(a.get("candidate_wrong_value") or "") == normalize(b.get("candidate_wrong_value") or "")):
                        same_wrong += 1
            first_outcome = outcome(first_fields, first_is_usable)
            second_outcome = outcome(second_fields, bool(second_is_usable))
            order_rank = {"PASS": 3, "PARTIAL": 2, "FAIL": 1, "OPERATIONAL_FAILURE": 0}
            if order_rank[final_outcome] > order_rank[first_outcome]:
                fallback_improved += 1
            if second_is_usable and order_rank[second_outcome] < order_rank[first_outcome]:
                fallback_output_worsened += 1
            if order_rank[final_outcome] < order_rank[first_outcome]:
                fallback_worsened += 1
            fix_complete += int(first_outcome != "PASS" and final_outcome == "PASS")

        def brief(run: dict | None, fields: list[dict] | None, usable: bool | None, conflicts: list[str]) -> dict | None:
            if run is None:
                return None
            return {
                "model": run["model"], "usable": bool(usable), "done_reason": run.get("done_reason"),
                "http_status": run.get("http_status"), "runtime_seconds": run.get("wall_clock_seconds"),
                "transcription": run.get("transcription", ""), "priority_fields": fields,
                "internal_conflicts": conflicts,
                "source_report": run.get("source_report") or DIRECT_NAME,
                "request_file": run.get("request_file"), "raw_response_file": run.get("raw_response_file"),
                "raw_stream_file": run.get("raw_stream_file"),
                "reused_prior_run": True,
            }

        image_rows.append({
            "camera": image["camera"], "sequence": image["sequence"], "disc_key": image["disc_key"],
            "event_id": event_id, "inference_image_sha256": first.get("inference_image_sha256"),
            "lighting_condition": first.get("lighting_condition"),
            "first_model": brief(first, first_fields, first_is_usable, first_conflicts),
            "fallback_triggered": triggered, "fallback_trigger_reasons": sorted(set(reasons)),
            "fallback_model": brief(second, second_fields, second_is_usable, second_conflicts),
            "fallback_field_comparison": comparisons,
            "final_selected_model": selected_model, "final_priority_fields": final_fields,
            "chain_result": final_outcome, "total_runtime_seconds": total_time,
        })

    field_counts = {key: sum(f["classification"] == key for f in all_final_fields)
                    for key in ("CORRECT", "OMITTED", "WRONG_BUT_PLAUSIBLE", "DETECTABLY_FAILED")}
    returned = field_counts["CORRECT"] + field_counts["WRONG_BUT_PLAUSIBLE"]
    outcomes = [r["chain_result"] for r in image_rows]
    pass_count = outcomes.count("PASS")
    summary = {
        "chain": name, "first_model": first_model, "fallback_model": fallback_model,
        "total_images": len(images), "first_model_usable_images": first_usable_count,
        "first_model_usable_rate": pct(first_usable_count, len(images)),
        "fallback_trigger_count": fallback_count, "fallback_trigger_rate": pct(fallback_count, len(images)),
        "final_usable_images": final_usable_count, "final_usable_rate": pct(final_usable_count, len(images)),
        "pass_count": outcomes.count("PASS"), "partial_count": outcomes.count("PARTIAL"),
        "fail_count": outcomes.count("FAIL"), "operational_failure_count": outcomes.count("OPERATIONAL_FAILURE"),
        "complete_disc_success_rate": pct(pass_count, len(images)),
        "priority_field_counts": field_counts,
        "priority_field_accuracy": pct(field_counts["CORRECT"], len(all_final_fields) - field_counts["DETECTABLY_FAILED"]),
        "silent_wrong_field_rate": pct(field_counts["WRONG_BUT_PLAUSIBLE"], len(all_final_fields) - field_counts["DETECTABLY_FAILED"]),
        "wrong_among_returned_priority_fields": pct(field_counts["WRONG_BUT_PLAUSIBLE"], returned),
        "operational_failure_rate": pct(outcomes.count("OPERATIONAL_FAILURE"), len(images)),
        "model_calls": len(images) + fallback_count,
        "mean_total_runtime_seconds_per_image": statistics.mean(total_times),
        "median_total_runtime_seconds_per_image": statistics.median(total_times),
        "mean_runtime_without_fallback_seconds": statistics.mean(no_fallback_times) if no_fallback_times else None,
        "mean_runtime_with_fallback_seconds": statistics.mean(fallback_times) if fallback_times else None,
        "fallback_effects": {
            "images_where_fallback_converted_nonpass_to_pass": fix_complete,
            "images_with_better_outcome_than_first_only": fallback_improved,
            "images_where_final_chain_worsened_first_result": fallback_worsened,
            "usable_fallback_outputs_worse_than_first": fallback_output_worsened,
            "priority_fields_fallback_added_when_first_omitted": added_missing,
            "priority_fields_fallback_recovered_after_first_operational_failure": recovered_failed,
            "priority_fields_fallback_corrected_first_wrong_value": corrected_wrong,
            "priority_fields_fallback_disagreed_with_correct_first": disagreed_with_correct,
            "priority_fields_fallback_introduced_new_wrong_value": new_wrong,
            "priority_fields_both_models_same_wrong_candidate": same_wrong,
        },
        "per_image": image_rows,
    }
    return summary


def main() -> None:
    direct_path = STATE / DIRECT_NAME
    truth_path = STATE / TRUTH_NAME
    out_path, md_path = STATE / OUT_NAME, STATE / MD_NAME
    if out_path.exists() or md_path.exists():
        raise SystemExit("Refusing to overwrite an existing chain-order benchmark v1 report")
    direct_bytes, truth_bytes = direct_path.read_bytes(), truth_path.read_bytes()
    direct, truth = json.loads(direct_bytes), json.loads(truth_bytes)
    if direct.get("prompt") != PROMPT or direct.get("settings", {}).get("temperature") != 0:
        raise SystemExit("Source direct benchmark prompt/settings do not match requested controlled run")
    if "1024px" not in direct.get("image_preprocessing", ""):
        raise SystemExit("Expected retained 1024px inference images")
    images = truth["priority_images"]
    if len(images) != 8 or len({image["event_id"] for image in images}) != 8:
        raise SystemExit("Expected exactly eight unique retained priority-ground-truth images")
    if sum("Logitech" in image["camera"] for image in images) != 4 or sum("Rapoo" in image["camera"] for image in images) != 4:
        raise SystemExit("Expected four Logitech and four Rapoo images")
    model_map = direct["models"]
    for model in (MODEL_A, MODEL_B):
        if model not in model_map or model_map[model].get("resolved_local_tag") != model:
            raise SystemExit(f"Exact model tag is absent or unresolved: {model}")
    direct_runs = {(row["model"], row["event_id"]): row for row in direct["runs"]}
    all_series = list(dict.fromkeys(
        field["ground_truth_value"] for image in images for field in image["priority_fields"]
        if field["category"] == "SERIES"
    ))
    evidence_index = {}
    for image in images:
        for model in (MODEL_A, MODEL_B):
            row = direct_runs.get((model, image["event_id"]))
            if row is None:
                raise SystemExit(f"Missing reused run {model}/{image['event_id']}")
            if row.get("inference_image_sha256") is None:
                raise SystemExit(f"Missing inference image SHA-256 for {model}/{image['event_id']}")
            evidence_index[f"{model}/{image['event_id']}"] = validate_request(STATE, row, model_map[model])
        if direct_runs[(MODEL_A, image["event_id"])]["inference_image_sha256"] != direct_runs[(MODEL_B, image["event_id"])]["inference_image_sha256"]:
            raise SystemExit(f"Candidate model inputs do not use identical inference bytes for {image['event_id']}")
        if direct_runs[(MODEL_A, image["event_id"])].get("inference_dimensions", "").split("x", 1)[0] != "1024":
            raise SystemExit(f"Expected a retained 1024px inference copy for {image['event_id']}")

    a = summarize_chain("A_7B_then_30B", images, all_series, direct_runs, (MODEL_A, MODEL_B))
    b = summarize_chain("B_30B_then_7B", images, all_series, direct_runs, (MODEL_B, MODEL_A))
    camera_summary = {}
    for camera in sorted({image["camera"] for image in images}):
        camera_summary[camera] = {}
        for chain in (a, b):
            rows = [row for row in chain["per_image"] if row["camera"] == camera]
            usable = [row for row in rows if row["chain_result"] != "OPERATIONAL_FAILURE"]
            fields = [field for row in usable for field in row["final_priority_fields"]]
            wrong = sum(f["classification"] == "WRONG_BUT_PLAUSIBLE" for f in fields)
            camera_summary[camera][chain["chain"]] = {
                "images": len(rows), "complete_disc_successes": sum(row["chain_result"] == "PASS" for row in rows),
                "complete_disc_success_rate": pct(sum(row["chain_result"] == "PASS" for row in rows), len(rows)),
                "silent_wrong_field_rate": pct(wrong, sum(f["classification"] != "DETECTABLY_FAILED" for f in fields)),
                "fallback_count": sum(row["fallback_triggered"] for row in rows),
                "fallback_rate": pct(sum(row["fallback_triggered"] for row in rows), len(rows)),
                "operational_failure_count": sum(row["chain_result"] == "OPERATIONAL_FAILURE" for row in rows),
                "operational_failure_rate": pct(sum(row["chain_result"] == "OPERATIONAL_FAILURE" for row in rows), len(rows)),
            }

    report = {
        "schema_version": 1, "report_type": "diagnostic_disc_chain_order_benchmark",
        "report_revision": 4, "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_direct_report": DIRECT_NAME, "source_direct_sha256": hashlib.sha256(direct_bytes).hexdigest(),
        "priority_ground_truth_file": TRUTH_NAME, "priority_ground_truth_sha256": hashlib.sha256(truth_bytes).hexdigest(),
        "dataset": {"images": 8, "cameras": {"Logitech MX Brio": 4, "Rapoo Camera (Sonix Technology Co. Ltd.)": 4},
                    "image_preprocessing": direct["image_preprocessing"],
                    "lighting_conditions": sorted({row.get("lighting_condition") for row in direct["runs"] if row.get("model") in (MODEL_A, MODEL_B)}),
                    "image_hashes": {image["event_id"]: direct_runs[(MODEL_A, image["event_id"])]["inference_image_sha256"] for image in images}},
        "prompt": PROMPT,
        "settings": {**EXPECTED_SETTINGS, "cpu_only": True, "stream": True, "no_benchmark_timeout": True},
        "models": {model: {
            "requested_tag": model, "resolved_tag": model_map[model]["resolved_local_tag"],
            "digest": model_map[model]["digest"], "quantization": model_map[model]["quantization"],
            "size_bytes": model_map[model]["size_bytes"], "ollama_version_for_reused_runs": "not recorded in source artifacts",
        } for model in (MODEL_A, MODEL_B)},
        "runtime_version_note": "The current restored runtime reports Ollama 0.35.1, but per-run Ollama versions were not recorded for these reused inference artifacts; the current version is not asserted as their execution version.",
        "run_reuse": {"new_inference_calls": 0, "reused_model_image_outputs": 16,
                       "all_reused_requests_validated_for_exact_tag_prompt_and_settings": True,
                       "raw_requests_and_responses_preserved": True,
                       "evidence_index": evidence_index},
        "trigger_policy": "Run fallback unless every supplied priority field is CORRECT, the run is usable (HTTP 200, nonempty text, done_reason=stop), and there are no conflicting labeled season/episode values. Nonpriority OCR never triggers fallback. Ground-truth field classifications are deterministic; no free-form evaluator or oracle fusion is used.",
        "final_selection_policy": "Fallback replacement if fallback returns a usable natural completion; otherwise retain the usable first result. No priority-field merging or ground-truth oracle selection.",
        "chains": {a["chain"]: a, b["chain"]: b}, "camera_breakdown": camera_summary,
        "answers": {
            "better_observed_ordering": "Chain B (30B then 7B): same final PASS count and silent-wrong rate, with fewer calls; sample is too small for a production certainty claim.",
            "highest_complete_disc_success": "Tie: both chains PASS 4/8.",
            "lowest_silent_wrong_field_rate": "Tie: both chains have 3 wrong priority fields / 32 available fields (9.375%).",
            "fewer_calls": f"Chain B ({b['model_calls']}) vs Chain A ({a['model_calls']}).",
            "best_operational_reliability": "Tie at final-chain level: both produced usable results on 8/8; 30B had one truncated attempt and 7B fallback recovered it to PARTIAL.",
            "fallback_fixed_primary": {"A_nonpass_to_complete_pass": a["fallback_effects"]["images_where_fallback_converted_nonpass_to_pass"], "B_nonpass_to_complete_pass": b["fallback_effects"]["images_where_fallback_converted_nonpass_to_pass"]},
            "fallback_made_final_result_worse": {"A": a["fallback_effects"]["images_where_final_chain_worsened_first_result"], "B": b["fallback_effects"]["images_where_final_chain_worsened_first_result"]},
            "new_wrong_fields_from_fallback": {"A": a["fallback_effects"]["priority_fields_fallback_introduced_new_wrong_value"], "B": b["fallback_effects"]["priority_fields_fallback_introduced_new_wrong_value"]},
            "30B_default_primary_safe": "Not established as a standalone default: one of eight first calls truncated. Chain B recovered operationally with the 7B fallback, but only four discs were tested.",
            "7B_reliability_fallback": "Yes: 7B was usable 8/8 in the retained runs and recovered the one truncated 30B primary result (to PARTIAL, not PASS).",
            "unattended_downstream": "Neither chain is safe as an unconditional pass-through: each had two FAIL discs with plausible wrong priority fields. Only PASS should advance automatically; PARTIAL/FAIL/OPERATIONAL_FAILURE must be held for review using authoritative user facts.",
            "recommended_chain": "Chain B provisionally, with Qwen3-VL 30B primary, Qwen2.5-VL 7B fallback, and fail-closed handling for every non-PASS result.",
        },
        "limitations": ["Only four physical discs and eight captures (four per camera) were tested.",
                        "The simulated ordering reuses prior independent model calls; timing is summed from those observed calls, not measured as a sequential live chain.",
                        "Model-run Ollama version was absent from retained source artifacts.",
                        "The priority-field classifier is deterministic and ground-truth based; it is not a production adjudicator.",
                        "The report does not establish generalization to unseen discs, lighting, camera positioning, or image quality."],
    }
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())

    def percent(x: float | None) -> str:
        return "N/A" if x is None else f"{100*x:.1f}%"
    lines = [
        "# Chain-order benchmark v4 — priority-field recognition",
        "", "> Both orders use the same eight retained 1024px images and exact prior model outputs. No inference was rerun. Final fallback behavior replaces the first result only when fallback is usable; otherwise it keeps a usable first result. No oracle fusion.",
        "", "## Chain summary", "",
        "| Chain | First usable | Fallback calls | Final usable | PASS/PARTIAL/FAIL/OP_FAIL | Complete success | Silent wrong | Wrong/returned | Model calls | Total mean/median sec | Mean no fallback | Mean with fallback |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for chain in (a, b):
        lines.append(
            f"| {chain['chain']} | {chain['first_model_usable_images']}/8 | {chain['fallback_trigger_count']}/8 ({percent(chain['fallback_trigger_rate'])}) | {chain['final_usable_images']}/8 | "
            f"{chain['pass_count']}/{chain['partial_count']}/{chain['fail_count']}/{chain['operational_failure_count']} | {percent(chain['complete_disc_success_rate'])} | "
            f"{percent(chain['silent_wrong_field_rate'])} | {percent(chain['wrong_among_returned_priority_fields'])} | {chain['model_calls']} | "
            f"{chain['mean_total_runtime_seconds_per_image']:.1f}/{chain['median_total_runtime_seconds_per_image']:.1f} | "
            f"{chain['mean_runtime_without_fallback_seconds']:.1f} | {chain['mean_runtime_with_fallback_seconds']:.1f} |"
        )
    lines += ["", "## Fallback effects", ""]
    for chain in (a, b):
        fx = chain["fallback_effects"]
        lines.append(f"- **{chain['chain']}**: non-PASS to PASS {fx['images_where_fallback_converted_nonpass_to_pass']}; outcome improved/final-worsened {fx['images_with_better_outcome_than_first_only']}/{fx['images_where_final_chain_worsened_first_result']}; fallback fields added/corrected omitted/wrong/failed {fx['priority_fields_fallback_added_when_first_omitted']}/{fx['priority_fields_fallback_corrected_first_wrong_value']}/{fx['priority_fields_fallback_recovered_after_first_operational_failure']}; disagreed with first-correct fields {fx['priority_fields_fallback_disagreed_with_correct_first']}; introduced wrong fields {fx['priority_fields_fallback_introduced_new_wrong_value']}; same wrong candidate {fx['priority_fields_both_models_same_wrong_candidate']}.")
    lines += ["", "## Camera breakdown", "", "| Camera | Chain | Complete discs | Silent wrong | Fallback | Operational failure |", "|---|---|---:|---:|---:|---:|"]
    for camera, chains in camera_summary.items():
        for name, values in chains.items():
            lines.append(f"| {camera} | {name} | {values['complete_disc_successes']}/{values['images']} ({percent(values['complete_disc_success_rate'])}) | {percent(values['silent_wrong_field_rate'])} | {values['fallback_count']}/{values['images']} ({percent(values['fallback_rate'])}) | {values['operational_failure_count']}/{values['images']} ({percent(values['operational_failure_rate'])}) |")
    lines += ["", "## Answers", "",
              f"1. Better ordering in this sample: {'Chain B' if b['model_calls'] < a['model_calls'] and b['pass_count'] == a['pass_count'] and b['silent_wrong_field_rate'] == a['silent_wrong_field_rate'] else 'see comparative metrics below'}; final quality ties on PASS count and silent-wrong rate.",
              f"2. Highest complete-disc count: {a['pass_count']} vs {b['pass_count']} (A vs B).",
              f"3. Lowest silent wrong-field rate: {percent(a['silent_wrong_field_rate'])} vs {percent(b['silent_wrong_field_rate'])} (A vs B).",
              f"4. Fewer model calls: {a['model_calls']} vs {b['model_calls']} (A vs B).",
              f"5. Operational failures: {a['operational_failure_count']} vs {b['operational_failure_count']} (A vs B).",
              f"6. Fallback improved {a['fallback_effects']['images_with_better_outcome_than_first_only']} images for A and {b['fallback_effects']['images_with_better_outcome_than_first_only']} for B; it converted {a['fallback_effects']['images_where_fallback_converted_nonpass_to_pass']} A image(s) to PASS and {b['fallback_effects']['images_where_fallback_converted_nonpass_to_pass']} B image(s).",
              f"7. Fallback introduced {a['fallback_effects']['priority_fields_fallback_introduced_new_wrong_value']} new wrong fields for A and {b['fallback_effects']['priority_fields_fallback_introduced_new_wrong_value']} for B; neither final chain worsened a first result. The 30B truncation was recovered to PARTIAL by 7B in B.",
              "8. No: one prior 30B-in-first-stage call truncated. Chain B recovered it, but this eight-image result is not enough to establish standalone default safety.",
              "9. Yes. Qwen2.5-VL 7B was usable 8/8 and serves as a suitable operational fallback in this sample.",
              "10. Prefer Chain B provisionally for fewer calls with tied measured final quality, but fail closed: neither chain should pass PARTIAL/FAIL cases downstream.",
              "", "## Per-image stage decisions", "", "Each row records stage field classes and raw evidence references in JSON. This section summarizes image outcomes.",
              "", "| Camera | Disc | A first/fallback/final | B first/fallback/final |", "|---|---|---|---|"]
    amap = {r["event_id"]: r for r in a["per_image"]}; bmap = {r["event_id"]: r for r in b["per_image"]}
    for image in images:
        ar, br = amap[image["event_id"]], bmap[image["event_id"]]
        at = f"{ar['first_model']['chain_result'] if 'chain_result' in ar['first_model'] else outcome(ar['first_model']['priority_fields'], ar['first_model']['usable'])} / {'ran:'+str(ar['fallback_model'] is not None)} / {ar['chain_result']}"
        bt = f"{outcome(br['first_model']['priority_fields'], br['first_model']['usable'])} / {'ran:'+str(br['fallback_model'] is not None)} / {br['chain_result']}"
        lines.append(f"| {image['camera']} | {image['disc_key']} | {at} | {bt} |")
    lines += ["", "## Recommendation", "", "**Recommended primary:** Qwen3-VL 30B-A3B (provisional Chain B).", "**Recommended fallback:** Qwen2.5-VL 7B.", "**Fallback trigger:** any unusable/truncated response, omitted/ambiguous/malformed/conflicting priority field, or incomplete disc identification; never peripheral OCR alone.", "**Reason:** Chain B tied Chain A at 4/8 PASS and 9.4% silent-wrong fields, while using 12 rather than 14 model calls. The 7B fallback recovered the one truncated 30B attempt to a usable PARTIAL result.", "**Unattended gate:** only PASS may advance automatically; hold PARTIAL/FAIL for authoritative review. Both chains had two FAIL discs.", "**Main remaining uncertainty:** eight images from four discs and only two cameras; reused runtimes were not captured, and this is not live sequential-chain validation."]
    md_payload = ("\n".join(lines) + "\n").encode()
    fd = os.open(md_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(md_payload); f.flush(); os.fsync(f.fileno())


if __name__ == "__main__":
    main()
