#!/usr/bin/env python3
"""Create an auditable, human-reviewable field-risk score report.

The deterministic matcher uses only the separately verified ground-truth JSON
and raw direct outputs. Candidate non-matches are flagged for human adjudication;
no model is used as evaluator or source of ground truth.
"""
from __future__ import annotations

import json
import os
import argparse
import re
import statistics
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def contains_value(text: str, value: str) -> bool:
    normalized = norm(text)
    expected = norm(value)
    if not expected:
        return False
    return f" {expected} " in f" {normalized} "


def correct_field_value(field: str, text: str, value: str) -> bool:
    """Compare a field semantically while requiring its field context."""
    if field == "season":
        number = re.search(r"\d+|[IVX]+", value, re.I)
        if not number:
            return contains_value(text, value)
        n = re.escape(number.group(0))
        return bool(re.search(
            rf"(?i)(?:\b(?:season|temporada|staffel)\s*[:.#-]?\s*{n}\b|\b{n}\s*\.?\s*(?:season|temporada|staffel)\b)", text
        ))
    if field == "disc_number":
        number = re.search(r"\d+", value)
        return bool(number and re.search(
            rf"(?i)\b(?:disc|disk)\s*[#:]?\s*{re.escape(number.group(0))}\b", text
        ))
    if field == "episode_range":
        nums = re.search(r"(\d+)\s*[-–—]\s*(\d+)", value)
        return bool(nums and re.search(
            rf"(?i)\b(?:episodes?|episoden?)\s*[:#]?\s*{nums.group(1)}\s*[-–—]\s*{nums.group(2)}\b", text
        ))
    if field == "runtime":
        minutes = re.search(r"\d{2,4}", value)
        return bool(minutes and re.search(
            rf"(?i)\b{minutes.group(0)}\s*(?:min(?:ute?s?)?\.?|h(?:ours?)?)", text
        ))
    if field == "rating_germany":
        age = re.search(r"\d{1,2}", value)
        return bool(age and re.search(rf"(?i)\bFSK\b.{{0,24}}\b{age.group(0)}\b", text))
    if field == "region":
        return bool(re.search(r"\bregion\s*[:#-]?\s*B\b", text, re.I)
                    or re.search(r"^\s*B\s*$", text, re.M))
    if field in {"distributor_code", "spanish_certification", "release_identifier", "product_identifier"}:
        # Identifiers are exact/high-risk: retain all alphanumeric components.
        return contains_value(text, value)
    return contains_value(text, value)


def plausible_wrong_candidate(field: str, text: str, truth: str) -> str | None:
    """Return a generically typed alternative value for this field, if present."""
    patterns: dict[str, str] = {
        "season": r"(?i)\b(?:season|temporada|staffel)\s*[:.#-]?\s*([0-9]+|[ivx]+)\b",
        "disc_number": r"(?i)\b(?:disc|disk|volume|vol\.?|dvd|blu.?ray)\s*[#:]?\s*([0-9]+)\b",
        "episode_range": r"(?i)\b(?:episodes?|episoden?)\s*[:#]?\s*([0-9]+\s*[-–—]\s*[0-9]+)\b",
        "runtime": r"(?i)\b(?:runtime|laufzeit)?\s*([0-9]{2,4})\s*(?:min(?:ute?s?)?\.?|h(?:ours?)?)\b",
        "release_year": r"\b(19[0-9]{2}|20[0-3][0-9])\b",
        "distributor_code": r"(?i)\b(VF[A-Z]\s*[0-9]{4,7})\b",
        "spanish_certification": r"(?i)\bICAA\s*[:#]?\s*([0-9]{4,7})\b",
        "release_identifier": r"(?i)\b(EU\s*[0-9]{5,7}(?:\s+[A-Z0-9]+){0,3})\b",
        "product_identifier": r"\b([0-9]{5,7}\s+[A-Z0-9]{1,4})\b",
        "rating_us": r"(?i)\b(PG|G|PG-?13|R|NC-?17|PC)\b",
        "rating_germany": r"(?i)\bFSK\s*(?:ab\s*)?([0-9]{1,2})\b",
        "episode_range": r"(?i)\b(?:episodes?|episoden?)\s*[:#]?\s*([0-9]+\s*[-–—]\s*[0-9]+)\b",
    }
    if field == "title":
        # A wrong title is a phrase-like printed heading, not arbitrary prose.
        candidates = re.findall(r"(?m)^\s*([A-Z][A-Z0-9 '&:.-]{4,70})\s*$", text)
        for candidate in candidates:
            candidate = candidate.strip(" .:-")
            if candidate and not contains_value(candidate, truth):
                return candidate
        return None
    if field == "series":
        candidates = re.findall(r"(?i)\b(?:THE\s+)?[A-Z][A-Z ]{4,35}\b", text)
        for candidate in candidates:
            if not contains_value(candidate, truth) and len(norm(candidate).split()) >= 2:
                return candidate.strip()
        return None
    pattern = patterns.get(field)
    if pattern is None:
        return None
    expected = norm(truth)
    for match in re.finditer(pattern, text):
        candidate = next((group for group in match.groups() if group), match.group(0)).strip()
        candidate_norm = norm(candidate)
        # For split code values, compare normalized forms while allowing the
        # field label (for example ICAA) to be present outside the capture.
        if candidate_norm and candidate_norm not in expected and expected not in candidate_norm:
            return candidate
    return None


def failed(run: dict) -> bool:
    return bool(run.get("error")) or run.get("done_reason") == "length" or not run.get("transcription", "").strip()


def percent(value: float | None) -> str:
    return "pending" if value is None else f"{value * 100:.1f}%"


def markdown_report(report: dict) -> str:
    lines = [
        f"# {report.get('section_title', 'DVD field-risk / precision benchmark')}",
        "",
        f"Generated: {report['created_utc']}",
        "",
        "> **Interpretation:** deterministic field matching is provisional until non-matches and unsupported assertions are manually checked against the retained images. Hallucination rates are intentionally withheld rather than inferred from an incomplete transcript ground truth.",
        "",
        "## Secondary / non-decisive OCR findings" if report.get("section_only") == "secondary" else "## Direct recognition results",
        "",
        "| Model | Scorable fields | Correct | Omitted | Detectable failure | Wrong but plausible | Hallucinated | Silent wrong rate | Wrong / returned | Mean sec/image |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model, result in report["models"].items():
        c = result["counts"]
        runtime = result["mean_runtime_seconds"]
        lines.append(
            f"| `{model}` | {result['scorable_fields']} | {c.get('CORRECT',0)} ({percent(result['correct_field_rate'])}) | "
            f"{c.get('OMITTED',0)} ({percent(result['omission_rate'])}) | "
            f"{c.get('DETECTABLY_FAILED',0)} ({percent(result['detectable_failure_rate'])}) | "
            f"{c.get('WRONG_BUT_PLAUSIBLE',0)} ({percent(result['silent_wrong_field_rate'])}) | "
            f"{result['hallucinated_fields'] if result['hallucinated_fields'] is not None else 'pending'} | {percent(result['silent_wrong_field_rate'])} | "
            f"{percent(result['silent_wrong_among_returned'])} | {runtime:.1f} |" if runtime is not None else
            f"| `{model}` | {result['scorable_fields']} | {c.get('CORRECT',0)} ({percent(result['correct_field_rate'])}) | "
            f"{c.get('OMITTED',0)} ({percent(result['omission_rate'])}) | {c.get('DETECTABLY_FAILED',0)} | "
            f"{c.get('WRONG_BUT_PLAUSIBLE',0)} | pending | {percent(result['silent_wrong_field_rate'])} | "
            f"{percent(result['silent_wrong_among_returned'])} | n/a |"
        )
    ranked = sorted(
        report["models"].items(),
        key=lambda pair: (
            pair[1]["silent_wrong_field_rate"] if pair[1]["silent_wrong_field_rate"] is not None else 1.0,
            -(pair[1]["correct_field_rate"] if pair[1]["correct_field_rate"] is not None else 0.0),
            pair[1]["omission_rate"] if pair[1]["omission_rate"] is not None else 1.0,
            pair[1]["detectable_failure_rate"] if pair[1]["detectable_failure_rate"] is not None else 1.0,
            pair[1]["mean_runtime_seconds"] if pair[1]["mean_runtime_seconds"] is not None else float("inf"),
        ),
    )
    if report.get("section_only") != "secondary":
        lines += [
        "", "## Provisional production-risk ordering", "",
        "Sorted by observed plausible-wrong rate, correct rate, omission, detectable failure, then runtime. Hallucinations remain unadjudicated, so this is not a final production ranking.",
        "", "| Provisional order | Model | Wrong plausible | Correct | Omitted | Detectable failure | Mean sec/image |", "|---:|---|---:|---:|---:|---:|---:|",
        ]
        for rank, (model, result) in enumerate(ranked, 1):
            runtime = result["mean_runtime_seconds"]
            lines.append(
            f"| {rank} | `{model}` | {percent(result['silent_wrong_field_rate'])} | "
            f"{percent(result['correct_field_rate'])} | {percent(result['omission_rate'])} | "
            f"{percent(result['detectable_failure_rate'])} | {runtime:.1f} |" if runtime is not None else
            f"| {rank} | `{model}` | {percent(result['silent_wrong_field_rate'])} | "
            f"{percent(result['correct_field_rate'])} | {percent(result['omission_rate'])} | "
            f"{percent(result['detectable_failure_rate'])} | n/a |"
            )
    lines += ["", "## Critical fields", "", "| Model | Critical fields | Correct | Omitted | Detectable failure | Wrong but plausible | Critical silent wrong rate |", "|---|---:|---:|---:|---:|---:|---:|"]
    for model, result in report["models"].items():
        c = result["critical_counts"]
        lines.append(
            f"| `{model}` | {result['critical_scored_fields']} | {c.get('CORRECT',0)} | {c.get('OMITTED',0)} | "
            f"{c.get('DETECTABLY_FAILED',0)} | {c.get('WRONG_BUT_PLAUSIBLE',0)} | {percent(result['critical_silent_wrong_rate'])} |"
        )
    lines += ["", "## Camera aggregate", "", "| Camera | Fields | Correct rate | Omission rate | Detectable-failure rate | Silent wrong rate | Hallucination rate | Mean / median sec |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for camera, result in report["cameras"].items():
        c = result["counts"]
        lines.append(
            f"| {camera} | {result['scorable_fields']} | {percent(result['correct_field_rate'])} | "
            f"{percent(result['omission_rate'])} | {percent(result['detectable_failure_rate'])} | "
            f"{percent(result['silent_wrong_field_rate'])} | pending | "
            f"{result['mean_runtime_seconds']:.1f} / {result['median_runtime_seconds']:.1f} |"
        )
    lines += ["", "## Pairwise agreement and verifier signal", "", "Counts compare the normalized field decisions; agreement is not itself evidence of correctness. Review the per-field records and raw image before using agreement for confidence.", "", "| Model pair | Fields | Agreement | Agree correct | Agree wrong | Left catches wrong | Right catches wrong |", "|---|---:|---:|---:|---:|---:|---:|"]
    preferred = [key for key in report["pairwise_model_agreement"] if "qwen2.5vl:7b-q8_0" in key]
    for key in preferred:
        result = report["pairwise_model_agreement"][key]
        lines.append(
            f"| {key} | {result['fields_compared']} | {result['agreement_count']} ({percent(result['agreement_rate'])}) | "
            f"{result['agreement_and_correct']} | {result['agreement_and_wrong']} | "
            f"{result['left_corrects_right_wrong']} | {result['right_corrects_left_wrong']} |"
        )
    lines += [
        "", "## Review status and limitations", "",
        "- The ground-truth set contains four physical discs photographed by two cameras; it is not a broad production sample.",
        "- Each scorable field has a primary classification. Non-matches are rule-based candidates and remain flagged for human adjudication.",
        "- Hallucination counts/rates remain pending until every extra assertion is checked against the full retained image; unreadable or unannotated text is not automatically called hallucinated.",
        "- Existing direct benchmark artifacts and the two-stage pipeline report are separate and unchanged.",
        "- Revisit model roles, camera ranking, and escalation choices as verified CDs/DVDs are added.",
        "",
        f"Machine-readable per-field report: `{report['direct_report_file'].replace('direct-all-models', 'field-risk').replace('.json', '.json')}` (stored beside this summary).",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-report", default="diagnostic-disc-direct-all-models-v1.json")
    parser.add_argument("--output-stem", default="diagnostic-disc-field-risk-v1")
    parser.add_argument("--exclude-fields", default="", help="comma-separated ground-truth field names to exclude")
    parser.add_argument("--section-only", choices=["secondary"])
    args = parser.parse_args()
    if not re.fullmatch(r"diagnostic-disc-(?:field-risk|secondary-ocr)-v[0-9]+", args.output_stem):
        raise SystemExit("Output stem must use diagnostic-disc-field-risk-vN or diagnostic-disc-secondary-ocr-vN naming")
    state = Path(os.environ.get("STATE_DIR", "/state"))
    direct_path = state / args.direct_report
    if direct_path.parent != state:
        raise SystemExit("Direct report path must be a filename in STATE_DIR")
    direct = json.loads(direct_path.read_bytes())
    truth = json.loads((state / "diagnostic-disc-ground-truth-v1.1.json").read_bytes())
    out = state / f"{args.output_stem}.json"
    if out.exists():
        raise SystemExit(f"Refusing to overwrite existing report: {out}")
    truth_by_id = {row["event_id"]: row for row in truth["images"]}
    by_pair = {(row["model"], row["event_id"]): row for row in direct["runs"]}
    model_names = [name for name in direct["requested_models"]
                   if direct.get("models", {}).get(name, {}).get("status")
                   != "unavailable_exact_tag_no_substitution"]
    excluded_fields = {name.strip() for name in args.exclude_fields.split(",") if name.strip()}
    if args.section_only == "secondary" and not excluded_fields:
        excluded_fields = {"title", "series", "season", "disc_number", "episode_range"}
    expected_images = len(truth["images"])
    for model in model_names:
        count = sum(row.get("model") == model for row in direct.get("runs", []))
        if count != expected_images:
            raise SystemExit(
                f"Refusing to score incomplete model {model}: {count}/{expected_images} image runs"
            )
    records = []
    for gt_image in truth["images"]:
        for model in model_names:
            run = by_pair.get((model, gt_image["event_id"]))
            if run is None:
                run = {"model": model, "camera": gt_image["camera"],
                       "event_id": gt_image["event_id"], "transcription": "",
                       "error": "model/tag unavailable or run not completed"}
            text = run.get("transcription", "")
            for field in gt_image["fields"]:
                if field["field"] in excluded_fields:
                    continue
                if field["ground_truth_status"] != "VERIFIED":
                    classification = "UNCERTAIN_GROUND_TRUTH"
                    candidate = None
                elif failed(run):
                    classification = "DETECTABLY_FAILED"
                    candidate = None
                elif correct_field_value(field["field"], text, field["ground_truth_value"]):
                    classification = "CORRECT"
                    candidate = None
                else:
                    candidate = plausible_wrong_candidate(
                        field["field"], text, field["ground_truth_value"]
                    )
                    classification = "WRONG_BUT_PLAUSIBLE" if candidate else "OMITTED"
                records.append({
                    "model": model, "camera": gt_image["camera"],
                    "sequence": gt_image["sequence"], "event_id": gt_image["event_id"],
                    "disc_key": gt_image["disc_key"], "field": field["field"],
                    "critical": field["critical"],
                    "ground_truth_value": field["ground_truth_value"],
                    "classification": classification,
                    "candidate_wrong_value": candidate,
                    "inference_image_sha256": run.get("inference_image_sha256", run.get("image_sha256")),
                    "raw_transcription": text,
                    "done_reason": run.get("done_reason"),
                    "detectable_run_failure": failed(run),
                    "source_report": direct["report_type"],
                    "ground_truth_evidence": field["evidence"],
                    "adjudication": "REVIEW_REQUIRED" if classification in
                    ("WRONG_BUT_PLAUSIBLE", "OMITTED") else "RULE_MATCH",
                })

    summary: dict = {}
    for model in model_names:
        rows = [row for row in records if row["model"] == model]
        counts = Counter(row["classification"] for row in rows)
        critical = [row for row in rows if row["critical"]]
        critical_counts = Counter(row["classification"] for row in critical)
        valid_n = sum(counts[name] for name in
                      ("CORRECT", "OMITTED", "DETECTABLY_FAILED", "WRONG_BUT_PLAUSIBLE", "HALLUCINATED"))
        returned = counts["CORRECT"] + counts["WRONG_BUT_PLAUSIBLE"]
        model_runs = [row for row in direct["runs"] if row["model"] == model]
        runtimes = [row["wall_clock_seconds"] for row in model_runs if row.get("wall_clock_seconds") is not None]
        summary[model] = {
            "scorable_fields": valid_n,
            "counts": dict(counts),
            "correct_field_rate": counts["CORRECT"] / valid_n if valid_n else None,
            "omission_rate": counts["OMITTED"] / valid_n if valid_n else None,
            "detectable_failure_rate": counts["DETECTABLY_FAILED"] / valid_n if valid_n else None,
            "silent_wrong_field_rate": counts["WRONG_BUT_PLAUSIBLE"] / valid_n if valid_n else None,
            "silent_wrong_among_returned": counts["WRONG_BUT_PLAUSIBLE"] / returned if returned else None,
            "hallucinated_fields": None,
            "hallucination_adjudication_complete": False,
            "hallucination_rate": None,
            "hallucination_rate_status": "requires exhaustive human adjudication of unsupported assertions",
            "critical_scored_fields": sum(critical_counts.values()),
            "critical_counts": dict(critical_counts),
            "critical_silent_wrong_rate": critical_counts["WRONG_BUT_PLAUSIBLE"] / len(critical) if critical else None,
            "mean_runtime_seconds": statistics.mean(runtimes) if runtimes else None,
            "median_runtime_seconds": statistics.median(runtimes) if runtimes else None,
        }
    camera_summary = {}
    for camera in sorted({row["camera"] for row in records}):
        rows = [row for row in records if row["camera"] == camera]
        counts = Counter(row["classification"] for row in rows)
        n = sum(counts[name] for name in
                ("CORRECT", "OMITTED", "DETECTABLY_FAILED", "WRONG_BUT_PLAUSIBLE", "HALLUCINATED"))
        associated = [run for run in direct["runs"] if run["camera"] == camera]
        times = [run["wall_clock_seconds"] for run in associated if run.get("wall_clock_seconds") is not None]
        camera_summary[camera] = {
            "scorable_fields": n, "counts": dict(counts),
            "correct_field_rate": counts["CORRECT"] / n if n else None,
            "omission_rate": counts["OMITTED"] / n if n else None,
            "detectable_failure_rate": counts["DETECTABLY_FAILED"] / n if n else None,
            "silent_wrong_field_rate": counts["WRONG_BUT_PLAUSIBLE"] / n if n else None,
            "hallucination_rate": None,
            "mean_runtime_seconds": statistics.mean(times) if times else None,
            "median_runtime_seconds": statistics.median(times) if times else None,
        }
    pairwise = {}
    indexed = {(row["model"], row["event_id"], row["field"]): row for row in records}
    for i, left in enumerate(model_names):
        for right in model_names[i + 1:]:
            compared = []
            for gt_image in truth["images"]:
                for field in gt_image["fields"]:
                    if field["field"] in excluded_fields:
                        continue
                    a = indexed.get((left, gt_image["event_id"], field["field"]))
                    b = indexed.get((right, gt_image["event_id"], field["field"]))
                    if a is None or b is None:
                        continue
                    def signature(row: dict) -> str:
                        category = row["classification"]
                        if category == "CORRECT":
                            return "CORRECT:" + norm(row["ground_truth_value"])
                        if category == "WRONG_BUT_PLAUSIBLE":
                            return "WRONG:" + norm(row.get("candidate_wrong_value") or "")
                        return category
                    sa, sb = signature(a), signature(b)
                    compared.append((a, b, sa == sb, sa.startswith("CORRECT:"),
                                     sb.startswith("CORRECT:")))
            n = len(compared)
            agreement = [row for row in compared if row[2]]
            pairwise[f"{left} + {right}"] = {
                "fields_compared": n,
                "agreement_count": len(agreement),
                "agreement_rate": len(agreement) / n if n else None,
                "agreement_and_correct": sum(row[3] and row[4] for row in agreement),
                "agreement_and_wrong": sum(row[0]["classification"] == "WRONG_BUT_PLAUSIBLE"
                                            and row[1]["classification"] == "WRONG_BUT_PLAUSIBLE"
                                            for row in agreement),
                "agreement_and_omitted": sum(row[0]["classification"] == "OMITTED"
                                              and row[1]["classification"] == "OMITTED"
                                              for row in agreement),
                "agreement_and_detectable_failure": sum(row[0]["classification"] == "DETECTABLY_FAILED"
                                                        and row[1]["classification"] == "DETECTABLY_FAILED"
                                                        for row in agreement),
                "disagreement_count": n - len(agreement),
                "left_corrects_right_wrong": sum(row[3] and row[1]["classification"] == "WRONG_BUT_PLAUSIBLE" for row in compared),
                "right_corrects_left_wrong": sum(row[4] and row[0]["classification"] == "WRONG_BUT_PLAUSIBLE" for row in compared),
                "left_corrects_right_omission": sum(row[3] and row[1]["classification"] == "OMITTED" for row in compared),
                "right_corrects_left_omission": sum(row[4] and row[0]["classification"] == "OMITTED" for row in compared),
                "signature_method": "normalized expected value for correct; type-aware candidate for plausible wrong; common class for omission/failure",
            }
    hallucination_review = []
    for run in direct["runs"]:
        if run.get("model") not in model_names:
            continue
        hallucination_review.append({
            "model": run["model"], "camera": run["camera"],
            "event_id": run["event_id"],
            "transcription": run.get("transcription", ""),
            "unsupported_assertions": [],
            "hallucination_count": None,
            "review_status": "HUMAN_IMAGE_ADJUDICATION_REQUIRED",
            "note": "Do not treat text absent from the scorable field list as hallucinated without checking whether it is visibly printed elsewhere on the image.",
        })
    report = {
        "schema_version": 1, "report_type": "field_risk_precision",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ground_truth_file": truth["report_type"],
        "direct_report": direct["report_type"],
        "direct_report_file": direct_path.name,
        "section_title": "SECONDARY / NON-DECISIVE OCR FINDINGS" if args.section_only == "secondary" else "DVD field-risk / precision benchmark",
        "section_only": args.section_only,
        "excluded_ground_truth_fields": sorted(excluded_fields),
        "method": "Deterministic value matching and generic type-aware wrong-value candidate extraction; all nonmatches remain explicitly human-reviewable. No model is used to score truth.",
        "limitations": [
            "Automatic matches and candidate classifications require manual adjudication before final production claims.",
            "Hallucination rate is withheld until all unsupported assertions are exhaustively reviewed against the image.",
            "Field denominator includes only verified visible fields recorded in the independent ground-truth file.",
            "This is a small four-disc dataset; do not tune future routing to these labels.",
        ],
        "models": summary, "cameras": camera_summary, "pairwise_model_agreement": pairwise,
        "hallucination_review": hallucination_review,
        "field_classifications": records,
    }
    temp = out.with_suffix(".tmp")
    with temp.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
    temp.chmod(0o600)
    temp.replace(out)
    out.chmod(0o600)
    summary_path = state / f"{args.output_stem}.md"
    summary = markdown_report(report).encode("utf-8")
    fd = os.open(summary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(summary)
        stream.flush()
    print(out)


if __name__ == "__main__":
    main()
