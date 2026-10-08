#!/usr/bin/env python3
"""Create an additive direct benchmark revision with corrected Llama runs."""
from __future__ import annotations

import json
import os
import argparse
import re
import time
from pathlib import Path


def exclusive_json(path: Path, obj: dict) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def main() -> None:
    state = Path(os.environ.get("STATE_DIR", "/state"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--correction-report", help="immutable correction-vN JSON; defaults to latest version")
    args = parser.parse_args()
    original_path = state / "diagnostic-disc-direct-all-models-v1.json"
    if args.correction_report:
        correction_path = state / args.correction_report
        if correction_path.parent != state or not correction_path.is_file():
            raise SystemExit("Correction report must be an existing filename in STATE_DIR")
    else:
        candidates = []
        for path in state.glob("diagnostic-disc-llama32-vision-correction-v[0-9]*.json"):
            match = re.fullmatch(r"diagnostic-disc-llama32-vision-correction-v([0-9]+)\.json", path.name)
            if match:
                candidates.append((int(match.group(1)), path))
        if not candidates:
            raise SystemExit("No immutable versioned Llama correction report is available")
        correction_path = max(candidates)[1]
    output_versions = []
    for path in state.glob("diagnostic-disc-direct-all-models-v[0-9]*.json"):
        match = re.fullmatch(r"diagnostic-disc-direct-all-models-v([0-9]+)\.json", path.name)
        if match:
            output_versions.append(int(match.group(1)))
    output_path = state / f"diagnostic-disc-direct-all-models-v{max(output_versions, default=1) + 1}.json"
    original = json.loads(original_path.read_bytes())
    correction = json.loads(correction_path.read_bytes())
    if (correction.get("status") != "complete"
            or correction.get("smoke_test", {}).get("status") != "passed"
            or len(correction.get("runs", [])) != 8):
        raise SystemExit("Corrected Llama batch must have eight completed image records")
    old = [row for row in original["runs"] if row.get("model") == correction["requested_tag"]]
    corrected = correction["runs"]
    if len(old) != 8 or len({row["event_id"] for row in corrected}) != 8:
        raise SystemExit("Expected eight unique historical and corrected Llama records")
    old_by_event = {row["event_id"]: row for row in old}
    for row in corrected:
        previous = old_by_event.get(row["event_id"])
        if previous is None:
            raise SystemExit(f"New Llama event not in historical run set: {row['event_id']}")
        if row.get("model_digest") != previous.get("model_digest"):
            raise SystemExit(f"Llama model digest changed for {row['event_id']}")
        if row.get("inference_image_sha256") != previous.get("inference_image_sha256", previous.get("image_sha256")):
            raise SystemExit(f"Llama inference image changed for {row['event_id']}")
        row["usable_for_recognition_scoring"] = (
            row.get("http_status") == 200
            and row.get("done_reason") == "stop"
            and bool(row.get("transcription", "").strip())
            and not row.get("error")
        )
    active = []
    for row in original["runs"]:
        if row.get("model") != correction["requested_tag"]:
            item = dict(row)
            item.setdefault("ollama_version", original.get("ollama_version"))
            active.append(item)
    active.extend(corrected)
    if len(active) != len(original["runs"]) or len(active) != 96:
        raise SystemExit(f"Integrated active record count changed unexpectedly: {len(active)}")
    history = []
    for row in old:
        item = dict(row)
        item["historical_status"] = "SUPERSEDED_INFRASTRUCTURE_FAILURE"
        item["superseded_by_report"] = correction_path.name
        item["superseded_reason"] = "Ollama 0.35.1 predates Mllama support; this was a runtime compatibility error, not a recognition result."
        history.append(item)
    integrated = dict(original)
    integrated.update({
        "report_type": "direct_recognition_all_models_integrated_runtime_correction",
        "report_revision": max(output_versions, default=1) + 1,
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_report_preserved": original_path.name,
        "corrected_llama_report": correction_path.name,
        "ollama_version": "mixed; see per-run ollama_version",
        "ollama_versions_used": sorted({str(row.get("ollama_version")) for row in active}),
        "runs": active,
        "superseded_historical_llama_failures": history,
        "correction_note": "Only the eight Llama records were rerun. All other 88 direct result records were carried forward byte-for-field from revision 1 and were not rerun. Operational failures remain failures, not OCR-quality errors; only nonempty natural completions are usable for recognition scoring.",
    })
    model_metadata = dict(integrated["models"][correction["requested_tag"]])
    model_metadata.update({
        "status": "correction_batch_complete",
        "runtime_version": correction["ollama_version_after"],
        "corrected_run_count": len(corrected),
        "usable_run_count": sum(row["usable_for_recognition_scoring"] for row in corrected),
        "detectable_failure_count": sum(not row["usable_for_recognition_scoring"] for row in corrected),
        "historical_failed_run_count": len(history),
        "architecture": correction["architecture"],
    })
    integrated["models"] = dict(integrated["models"])
    integrated["models"][correction["requested_tag"]] = model_metadata
    exclusive_json(output_path, integrated)
    print(output_path)


if __name__ == "__main__":
    main()
