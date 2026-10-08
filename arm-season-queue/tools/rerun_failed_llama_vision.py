#!/usr/bin/env python3
"""Rerun only the eight historical Llama 3.2 Vision infrastructure failures.

Use after upgrading Ollama to a release that supports Mllama. The first exact
benchmark image is also the required image smoke test; its result is reused as
the first corrected benchmark record, avoiding an extra inference call.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE = Path(os.environ.get("STATE_DIR", "/state"))
BASE = os.environ.get("VISION_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLD_REPORT = STATE / "diagnostic-disc-direct-all-models-v1.json"
PREFLIGHT = STATE / "diagnostic-disc-model-preflight-v2.json"
LEGACY_CORRECTION = STATE / "diagnostic-disc-llama32-vision-corrected-v1.json"
REPORT_PREFIX = "diagnostic-disc-llama32-vision-correction-v"
MODEL = "llama3.2-vision:11b-instruct-q8_0"


def api(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    request = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def exclusive(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def report_versions() -> list[tuple[int, Path]]:
    found = []
    for path in STATE.glob(f"{REPORT_PREFIX}[0-9]*.json"):
        suffix = path.stem.removeprefix(REPORT_PREFIX)
        if suffix.isdigit():
            found.append((int(suffix), path))
    return sorted(found)


def save_revision(report: dict) -> Path:
    """Publish an immutable, exclusive versioned report snapshot."""
    versions = report_versions()
    revision = versions[-1][0] + 1 if versions else 1
    path = STATE / f"{REPORT_PREFIX}{revision}.json"
    report["report_revision"] = revision
    report["report_file"] = path.name
    report["previous_report"] = versions[-1][1].name if versions else None
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    versions = report_versions()
    if versions and not args.resume:
        raise SystemExit(f"Immutable correction reports already exist; use --resume to continue from {versions[-1][1].name}")
    if args.resume and not versions:
        raise SystemExit("No versioned correction report exists to resume")

    original = json.loads(OLD_REPORT.read_bytes())
    preflight = json.loads(PREFLIGHT.read_bytes())
    prior_model = next(row for row in preflight["model_list"] if row["requested_tag"] == MODEL)
    prior_rows = [row for row in original["runs"] if row.get("model") == MODEL]
    if len(prior_rows) != 8 or any("unknown model architecture: 'mllama'" not in (row.get("error") or "") for row in prior_rows):
        raise SystemExit("Expected exactly eight preserved historical mllama architecture failures")
    if prior_model.get("digest") != prior_rows[0].get("model_digest"):
        raise SystemExit("Exact model digest differs from the original failed benchmark")

    version = api("/api/version").get("version")
    if tuple(int(n) for n in version.split(".")[:2]) < (0, 4):
        raise SystemExit(f"Ollama {version} is below the documented 0.4.0 Mllama minimum")
    tags = {row["name"]: row for row in api("/api/tags").get("models", [])}
    tag = tags.get(MODEL)
    if not tag:
        raise SystemExit(f"Exact model tag is missing after upgrade: {MODEL}")
    if tag.get("digest") != prior_model.get("digest"):
        raise SystemExit("Exact model digest changed; refusing to silently replace the candidate")
    show = api("/api/show", {"model": MODEL})
    architecture = show.get("model_info", {}).get("general.architecture")
    if architecture != "mllama":
        raise SystemExit(f"Expected mllama architecture, received {architecture!r}")

    prompt = original["prompt"]
    settings = {"temperature": 0, "num_ctx": 4096, "num_predict": 1536,
                "think": False, "stream": True, "keep_alive": 0,
                "timeout": None, "num_gpu": 0, "execution": "CPU-only Ollama"}
    if args.resume:
        report = json.loads(versions[-1][1].read_bytes())
        if report.get("status") == "complete":
            raise SystemExit("The latest immutable correction report is complete; start a separately approved new correction series")
    else:
        report = {
            "schema_version": 1,
            "report_type": "corrected_llama32_vision_direct_rerun",
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "supersedes_only": "the eight Llama runtime-error rows in diagnostic-disc-direct-all-models-v1.json",
            "historical_report_preserved": OLD_REPORT.name,
            "ollama_version_before": original.get("ollama_version"),
            "ollama_version_after": version,
            "requested_tag": MODEL,
            "resolved_local_tag": tag["name"],
            "model_digest": tag["digest"],
            "model_size_bytes": tag.get("size"),
            "parameter_count": show.get("model_info", {}).get("general.parameter_count"),
            "parameter_size": show.get("details", {}).get("parameter_size"),
            "quantization": show.get("details", {}).get("quantization_level"),
            "architecture": architecture,
            "prompt": prompt,
            "settings": settings,
            "disc_notes_sent_to_model": False,
            "sequential": True,
            "smoke_test": {"event_id": None, "status": "pending; first of eight corrected image calls doubles as smoke test"},
            "failed_smoke_attempts": [],
            "runs": [],
        }
        # Preserve the already-recorded pre-v2 loader attempts as provenance;
        # they are not active benchmark runs and are never overwritten.
        if LEGACY_CORRECTION.exists():
            legacy = json.loads(LEGACY_CORRECTION.read_bytes())
            report["legacy_attempt_report"] = LEGACY_CORRECTION.name
            report["legacy_runtime_attempts"] = (
                legacy.get("failed_smoke_attempts", []) + legacy.get("runs", [])
            )
    if report.get("model_digest") != tag["digest"] or report.get("prompt") != prompt:
        raise SystemExit("Existing correction report does not match exact model/prompt")
    report["ollama_version_after"] = version
    report.setdefault("ollama_versions_used", [])
    if version not in report["ollama_versions_used"]:
        report["ollama_versions_used"].append(version)
    report.setdefault("failed_smoke_attempts", [])
    # A failed HTTP/runtime smoke attempt must not count as completing the image
    # or allow --resume to bypass the smoke gate. Keep its full metadata in the
    # additive attempt history and retry that same first image before proceeding.
    if report.get("smoke_test", {}).get("status") == "failed":
        failed_event = report["smoke_test"].get("event_id")
        failed_rows = [row for row in report["runs"] if row.get("event_id") == failed_event]
        report["failed_smoke_attempts"].extend(failed_rows)
        report["runs"] = [row for row in report["runs"] if row.get("event_id") != failed_event]
        report["smoke_test"] = {
            "event_id": failed_event,
            "status": "retry_pending",
            "note": "Prior smoke attempt failed at runtime and is retained in failed_smoke_attempts; retry the same first benchmark image before any other image.",
        }
        report["status"] = "retrying_smoke_test"
        report.pop("blocked_reason", None)
        save_revision(report)
    else:
        save_revision(report)
    completed = {row["event_id"] for row in report["runs"]}
    events = sorted(prior_rows, key=lambda row: (row["camera"], row["sequence"]))
    for old in events:
        event = old["event_id"]
        if event in completed:
            continue
        folder = STATE / "evidence" / event
        image_path = folder / "diagnostic-inference-qwen-1024.jpg"
        image = image_path.read_bytes()
        image_sha = hashlib.sha256(image).hexdigest()
        if image_sha != old.get("inference_image_sha256", old.get("image_sha256")):
            raise SystemExit(f"Inference-image SHA mismatch for {event}; not rerunning")
        slug = MODEL.replace("/", "-").replace(":", "-")
        attempt = 2
        while True:
            prefix = f"field-risk-direct-{slug}-attempt{attempt}"
            request_path = folder / f"{prefix}-request.json"
            stream_path = folder / f"{prefix}-stream.jsonl"
            response_path = folder / f"{prefix}-response.json"
            if not any(path.exists() for path in (request_path, stream_path, response_path)):
                break
            attempt += 1
        payload_obj = {
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt,
                          "images": [base64.b64encode(image).decode("ascii")]}],
            "stream": True, "think": False, "keep_alive": 0,
            "options": {"temperature": 0, "num_ctx": 4096,
                        "num_predict": 1536, "num_gpu": 0},
        }
        payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")).encode()
        exclusive(request_path, payload)
        request = urllib.request.Request(
            BASE + "/api/chat", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        started = time.monotonic()
        chunks: list[dict] = []
        stream_hash = hashlib.sha256()
        status = None
        error = None
        fd = os.open(stream_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with urllib.request.urlopen(request) as response, os.fdopen(fd, "wb") as stream:
                status = response.status
                for line in response:
                    stream.write(line)
                    stream_hash.update(line)
                    if line.strip():
                        try:
                            chunks.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                stream.flush()
                os.fsync(stream.fileno())
        except urllib.error.HTTPError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            status = exc.code
            data = exc.read()
            error = f"HTTP {exc.code}: {data.decode('utf-8', 'replace')}"
            with stream_path.open("ab") as stream:
                stream.write(data)
            stream_hash.update(data)
        except Exception as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            error = f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        final = {"model": MODEL, "message": {"role": "assistant", "content": "", "thinking": ""}}
        for chunk in chunks:
            message = chunk.get("message") or {}
            for part in ("content", "thinking"):
                final["message"][part] += message.get(part) or ""
            final.update({key: value for key, value in chunk.items() if key not in ("message", "done")})
            if "done" in chunk:
                final["done"] = chunk["done"]
        if chunks:
            exclusive(response_path, json.dumps(final, ensure_ascii=False, indent=2).encode() + b"\n")
        eval_ns = final.get("eval_duration")
        eval_count = final.get("eval_count")
        row = {
            "model": MODEL, "resolved_local_tag": tag["name"], "model_digest": tag["digest"],
            "ollama_version": version, "architecture": architecture,
            "camera": old["camera"], "sequence": old["sequence"], "event_id": event,
            "lighting_condition": old.get("lighting_condition", "daylight + roomlight"),
            "inference_image_sha256": image_sha, "image_sha256": image_sha,
            "image_file": str(image_path.relative_to(STATE)), "inference_dimensions": "1024x658",
            "http_status": status, "wall_clock_seconds": round(elapsed, 3),
            "total_duration_seconds": final.get("total_duration", 0) / 1e9 if final.get("total_duration") else None,
            "prompt_eval_count": final.get("prompt_eval_count"), "eval_count": eval_count,
            "done_reason": final.get("done_reason"), "natural_completion": final.get("done_reason") == "stop",
            "output_cap_failure": final.get("done_reason") == "length",
            "tokens_per_second": round(eval_count / (eval_ns / 1e9), 3) if eval_count and eval_ns else None,
            "thinking": final["message"].get("thinking", ""),
            "transcription": final["message"].get("content", ""), "error": error,
            "request_file": str(request_path.relative_to(STATE)),
            "raw_stream_file": str(stream_path.relative_to(STATE)),
            "raw_response_file": str(response_path.relative_to(STATE)) if chunks else None,
            "raw_stream_sha256": stream_hash.hexdigest(),
            "disc_notes_sent_to_model": False,
            "smoke_test": len(report["runs"]) == 0,
            "supersedes_historical_event_run": event,
        }
        if row["smoke_test"]:
            report["smoke_test"] = {
                "event_id": event,
                "status": "passed" if (
                    status == 200 and final.get("done") is True
                    and final.get("done_reason") == "stop"
                    and bool(row["transcription"].strip()) and not error
                ) else "failed",
                "http_status": status, "done_reason": row["done_reason"],
                "transcription_nonempty": bool(row["transcription"].strip()),
                "note": "This response is also corrected benchmark image run 1; no duplicate smoke inference was made.",
            }
            if report["smoke_test"]["status"] != "passed":
                report["runs"].append(row)
                report["status"] = "blocked_smoke_test"
                report["blocked_reason"] = error or "Smoke test did not produce a nonempty natural completion"
                report["remaining_images_not_run"] = max(0, 8 - len({item["event_id"] for item in report["runs"]}))
                save_revision(report)
                raise SystemExit("Llama image smoke test failed; stopping before the remaining images")
            report["status"] = "smoke_passed_in_progress"
        report["runs"].append(row)
        save_revision(report)
        completed.add(event)
        print(json.dumps({"camera": row["camera"], "sequence": row["sequence"],
                          "seconds": row["wall_clock_seconds"], "reason": row["done_reason"],
                          "status": status, "error": error, "smoke": row["smoke_test"]}), flush=True)
    report["completed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["status"] = "complete" if len(report["runs"]) == 8 else "incomplete"
    save_revision(report)
    print(f"Llama corrected runs: {len(report['runs'])}/8", flush=True)


if __name__ == "__main__":
    main()
