#!/usr/bin/env python3
"""Additive direct-recognition benchmark runner for retained diagnostic images.

Run inside a container that can reach Ollama and has the queue-state directory
mounted at /state. Existing exact five-model results are reused only when their
prompt/settings and inference-image hashes match; all other outputs are saved
under new exclusive filenames. No timeout is configured.
"""
from __future__ import annotations

import base64
import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

STATE = Path(os.environ.get("STATE_DIR", "/state"))
BASE = os.environ.get("VISION_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
MANIFEST = STATE / "diagnostic-disc-capture-log.json"
PREVIOUS = STATE / "diagnostic-disc-model-retest-expanded-1024.json"
OUT = STATE / "diagnostic-disc-direct-all-models-v1.json"
PROMPT = (
    "Transcribe only the readable text visibly printed in this image. "
    "Preserve the original language and line breaks. Do not infer missing text. "
    "Return only the transcription, or an empty string if no text is readable."
)
MODELS = [
    "qwen3-vl:4b", "qwen3-vl:4b-instruct", "qwen3-vl:2b-instruct",
    "qwen2.5vl:3b", "qwen2.5vl:7b-q8_0",
    "openbmb/minicpm-v4:q8_0", "minicpm-v:8b-2.6-q8_0", "minicpm-v4.6",
    "qwen3-vl:8b-instruct-q8_0", "qwen3-vl:30b-a3b-instruct-q4_K_M",
    "llama3.2-vision:11b-instruct-q8_0", "qwen2.5vl:32b-q4_K_M",
]


def api(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def write_exclusive(path: Path, content: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def save_report(report: dict) -> None:
    temp = OUT.with_name(OUT.name + f".tmp-{os.getpid()}")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, 0o600)
    os.replace(temp, OUT)
    os.chmod(OUT, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", action="store_true", help="resume this additive report without reusing evidence filenames")
    args = parser.parse_args()
    if OUT.exists() and not args.resume:
        raise SystemExit(f"Refusing to overwrite existing report: {OUT}")
    manifest = json.loads(MANIFEST.read_bytes())
    prior = json.loads(PREVIOUS.read_bytes())
    if prior.get("prompt") != PROMPT:
        raise SystemExit("Prior prompt differs; cannot reuse its runs")
    tags = {item["name"]: item for item in api("/api/tags").get("models", [])}
    resolved: dict[str, tuple[str, dict]] = {}
    missing: list[str] = []
    # Complete preflight before creating a benchmark report or making inference calls.
    for requested in MODELS:
        choices = [requested]
        if ":" not in requested:
            choices.append(requested + ":latest")
        tag = next((tags[name] for name in choices if name in tags), None)
        if tag is None:
            missing.append(requested)
            continue
        resolved[requested] = (tag["name"], api("/api/show", {"model": tag["name"]}))
    report = json.loads(OUT.read_bytes()) if OUT.exists() else {
        "schema_version": 1,
        "report_type": "direct_recognition_all_models",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ollama_version": api("/api/version").get("version"),
        "prompt": PROMPT,
        "settings": {
            "temperature": 0, "num_ctx": 4096, "num_predict": 1536,
            "think": False, "stream": True, "keep_alive": 0,
            "timeout": None, "num_gpu": 0, "execution": "CPU-only Ollama",
        },
        "dataset": "same eight retained daylight + room-light diagnostic captures",
        "image_preprocessing": "reuse unchanged saved 1024px, JPEG-quality-92 inference copies",
        "disc_notes_sent_to_models": False,
        "sequential": True,
        "requested_models": MODELS,
        "models": {},
        "runs": [],
    }
    prior_models = prior.get("models", {})
    prior_runs = prior.get("runs", [])
    report.setdefault("models", {})
    report.setdefault("runs", [])
    if report.get("prompt") != PROMPT or report.get("requested_models") != MODELS:
        raise SystemExit("Existing report does not match prompt/model list; refusing resume")
    for requested, (local, show) in resolved.items():
        metadata = show.get("model_info", {})
        current_identity = {
            "requested_tag": requested, "resolved_local_tag": local,
            "digest": tags[local].get("digest"), "size_bytes": tags[local].get("size"),
            "parameter_count": metadata.get("general.parameter_count"),
            "parameter_size": show.get("details", {}).get("parameter_size"),
            "quantization": show.get("details", {}).get("quantization_level"),
            "model_details": show.get("details"), "model_info": metadata,
        }
        existing_identity = report["models"].get(requested, {})
        if existing_identity.get("digest") not in (None, current_identity["digest"]):
            raise SystemExit(f"Model digest changed; refusing resume for {requested}")
        current_identity["status"] = existing_identity.get("status", "pending")
        report["models"][requested] = current_identity
    for requested in missing:
        report["models"].setdefault(requested, {
            "requested_tag": requested, "resolved_local_tag": None,
            "digest": None, "size_bytes": None, "parameter_count": None,
            "parameter_size": None, "quantization": None,
            "status": "unavailable_exact_tag_no_substitution",
        })

    # Reuse only the exact valid-size runs from the preserved five-model report.
    reusable = {"qwen2.5vl:3b", "qwen2.5vl:7b-q8_0", "openbmb/minicpm-v4:q8_0",
                "minicpm-v:8b-2.6-q8_0", "minicpm-v4.6"}
    for run in ([] if OUT.exists() else prior_runs):
        model = run["model"]
        if model not in reusable:
            continue
        if model not in resolved:
            continue
        expected = report["models"][model]
        if run.get("model_digest") != expected["digest"]:
            raise SystemExit(f"Digest mismatch; refusing reuse for {model}")
        evidence = STATE / "evidence" / run["event_id"]
        inference_path = evidence / "diagnostic-inference-qwen-1024.jpg"
        source_path = evidence / "0.jpg"
        inference_sha = hashlib.sha256(inference_path.read_bytes()).hexdigest()
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if run.get("inference_image_sha256") != inference_sha:
            raise SystemExit(f"Inference-image SHA mismatch; refusing reuse for {model}/{run['event_id']}")
        if run.get("source_roi_sha256") != source_sha:
            raise SystemExit(f"Source ROI SHA mismatch; refusing reuse for {model}/{run['event_id']}")
        reused = dict(run)
        reused["thinking"] = reused.get("thinking", "")
        response_path = STATE / run["raw_response_file"] if run.get("raw_response_file") else None
        if response_path and response_path.is_file():
            response_record = json.loads(response_path.read_bytes())
            reused["thinking"] = response_record.get("message", {}).get("thinking", reused["thinking"])
        reused["natural_completion"] = run.get("done_reason") == "stop"
        reused["output_cap_failure"] = run.get("done_reason") == "length"
        report["runs"].append({
            **reused,
            "lighting_condition": run.get("lighting_condition", manifest["lighting_condition"]),
            "benchmark_run_source": "reused exact valid-size direct run",
            "source_report": PREVIOUS.name,
        })
    for model in reusable:
        if model not in resolved:
            continue
        count = sum(row["model"] == model for row in report["runs"])
        if count != 8:
            raise SystemExit(f"Expected 8 reusable runs for {model}, found {count}")
        report["models"][model]["status"] = "reused_exact_prior_runs"
    save_report(report)

    for requested in MODELS:
        if requested not in resolved:
            print(json.dumps({"model": requested, "status": "unavailable_exact_tag_no_substitution"}), flush=True)
            continue
        local = report["models"][requested]["resolved_local_tag"]
        if requested in reusable:
            continue
        report["models"][requested]["status"] = "running"
        save_report(report)
        complete_events = {row.get("event_id") for row in report["runs"] if row.get("model") == requested}
        for batch in manifest["camera_batches"]:
            for capture in batch["captures"]:
                event = capture["event_id"]
                if event in complete_events:
                    continue
                folder = STATE / "evidence" / event
                image_path = folder / "diagnostic-inference-qwen-1024.jpg"
                image = image_path.read_bytes()
                image_sha = hashlib.sha256(image).hexdigest()
                source_image_sha = hashlib.sha256((folder / "0.jpg").read_bytes()).hexdigest()
                slug = requested.replace("/", "-").replace(":", "-")
                attempt = 1
                while True:
                    prefix = f"field-risk-direct-{slug}-attempt{attempt}"
                    request_path = folder / f"{prefix}-request.json"
                    stream_path = folder / f"{prefix}-stream.jsonl"
                    response_path = folder / f"{prefix}-response.json"
                    if not any(path.exists() for path in (request_path, stream_path, response_path)):
                        break
                    attempt += 1
                body = {
                    "model": local,
                    "messages": [{"role": "user", "content": PROMPT,
                                  "images": [base64.b64encode(image).decode("ascii")]}],
                    "stream": True, "think": False, "keep_alive": 0,
                    "options": {"temperature": 0, "num_ctx": 4096,
                                "num_predict": 1536, "num_gpu": 0},
                }
                payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
                write_exclusive(request_path, payload)
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
                    os.close(fd)
                    status = exc.code
                    data = exc.read()
                    error = f"HTTP {exc.code}: {data.decode('utf-8', 'replace')}"
                    with stream_path.open("ab") as stream:
                        stream.write(data)
                    stream_hash.update(data)
                except Exception as exc:  # retain detectable failures; no timeout is set
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                    error = f"{type(exc).__name__}: {exc}"
                elapsed = time.monotonic() - started
                final = {"model": local, "message": {"role": "assistant", "content": "", "thinking": ""}}
                for chunk in chunks:
                    message = chunk.get("message") or {}
                    for part in ("content", "thinking"):
                        final["message"][part] += message.get(part) or ""
                    final.update({k: v for k, v in chunk.items() if k not in ("message", "done")})
                    if "done" in chunk:
                        final["done"] = chunk["done"]
                if chunks:
                    write_exclusive(response_path, json.dumps(final, ensure_ascii=False, indent=2).encode() + b"\n")
                inference_ns = final.get("eval_duration")
                eval_count = final.get("eval_count")
                run = {
                    "camera": batch["camera"], "sequence": capture["sequence"],
                    "lighting_condition": manifest["lighting_condition"],
                    "event_id": event, "user_supplied_disc_note": capture["user_supplied_disc_note"],
                    "source_roi_sha256": source_image_sha,
                    "inference_image_sha256": image_sha, "image_sha256": image_sha,
                    "inference_dimensions": "1024x658",
                    "image_file": str(image_path.relative_to(STATE)),
                    "model": requested, "resolved_local_tag": local,
                    "model_digest": report["models"][requested]["digest"],
                    "http_status": status, "wall_clock_seconds": round(elapsed, 3),
                    "total_duration_seconds": final.get("total_duration", 0) / 1e9 if final.get("total_duration") else None,
                    "prompt_eval_count": final.get("prompt_eval_count"),
                    "eval_count": eval_count, "done_reason": final.get("done_reason"),
                    "natural_completion": final.get("done_reason") == "stop",
                    "output_cap_failure": final.get("done_reason") == "length",
                    "tokens_per_second": round(eval_count / (inference_ns / 1e9), 3)
                    if eval_count and inference_ns else None,
                    "thinking": final["message"].get("thinking", ""),
                    "transcription": final["message"].get("content", ""),
                    "error": error,
                    "request_file": str(request_path.relative_to(STATE)),
                    "raw_stream_file": str(stream_path.relative_to(STATE)),
                    "raw_response_file": str(response_path.relative_to(STATE)) if chunks else None,
                    "raw_stream_sha256": stream_hash.hexdigest(),
                    "disc_notes_sent_to_model": False,
                }
                report["runs"].append(run)
                complete_events.add(event)
                save_report(report)
                print(json.dumps({k: run[k] for k in
                                  ("model", "camera", "sequence", "wall_clock_seconds", "done_reason", "error")}),
                      flush=True)
        report["models"][requested]["status"] = "complete"
        save_report(report)
        try:
            api("/api/generate", {"model": local, "prompt": "", "keep_alive": 0, "stream": False})
        except Exception:
            pass
    report["completed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_report(report)
    print(f"COMPLETE: {len(report['runs'])} direct model/image records at {OUT}", flush=True)


if __name__ == "__main__":
    main()
