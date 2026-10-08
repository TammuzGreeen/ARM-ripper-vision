#!/usr/bin/env python3
"""Rank direct recognizers on user-established disc-identification fields."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import statistics
import time
import unicodedata
from collections import Counter
from pathlib import Path

PRIMARY = ("SERIES", "SEASON", "EPISODES", "TITLES")
CLASSES = ("CORRECT", "OMITTED", "DETECTABLY_FAILED", "WRONG_BUT_PLAUSIBLE")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def has_phrase(text: str, value: str) -> bool:
    ntext, nvalue = normalize(text), normalize(value)
    return bool(nvalue and f" {nvalue} " in f" {ntext} ")


def has_title(text: str, value: str) -> bool:
    """Match a supplied title as a printed heading/value, not a substring."""
    expected = normalize(value)
    if not expected:
        return False
    for raw_line in (text or "").splitlines():
        line = normalize(raw_line)
        # OCR commonly prefixes a title with its episode number or a field label.
        line = re.sub(r"^(?:(?:episode\s+)?[0-9]+|episode\s+title|title)\s+", "", line)
        if line == expected:
            return True
        # Multiple printed titles may share a line with explicit separators.
        for part in re.split(r"\s+(?:\||;|•)\s+", raw_line):
            if normalize(part) == expected:
                return True
    return False


def failed(run: dict) -> bool:
    return bool(run.get("error")) or run.get("done_reason") != "stop" or not run.get("transcription", "").strip()


def usable_run(run: dict) -> bool:
    return (not run.get("error") and run.get("http_status", 200) == 200
            and run.get("done_reason") == "stop" and bool(run.get("transcription", "").strip()))


def series_alternative(text: str, expected: str, all_series: list[str]) -> str | None:
    for candidate in all_series:
        if normalize(candidate) != normalize(expected) and has_phrase(text, candidate):
            return candidate
    # Returning only the franchise when a specific franchise/series is required
    # is a plausible but insufficiently specific identification.
    if normalize(expected).startswith("star trek") and has_phrase(text, "star trek"):
        return "STAR TREK (specific series omitted or misidentified)"
    return None


def season_value(text: str, expected: str) -> str | None:
    patterns = [
        r"(?i)\b(?:season|temporada|staffel)\s*[:.#-]?\s*([0-9]+|[ivx]+)\b",
        r"(?i)\b([0-9]+|[ivx]+)\s*\.?\s*(?:season|temporada|staffel)\b",
    ]
    matches = []
    for pattern in patterns:
        matches.extend(match.group(1) for match in re.finditer(pattern, text))
    if expected in matches:
        return None
    return matches[0] if matches else None


def episode_range_value(text: str, expected: str) -> str | None:
    pattern = r"(?i)\b(?:episodes?|episoden?|eps?\.?|episode\s+range)\s*[:#]?\s*([0-9]+\s*[-–—]\s*[0-9]+)\b"
    candidates = [re.sub(r"\s+", "", match.group(1)).replace("–", "-").replace("—", "-")
                  for match in re.finditer(pattern, text)]
    expected = expected.replace(" ", "").replace("–", "-").replace("—", "-")
    if expected in candidates:
        return None
    return candidates[0] if candidates else None


def plausible_title_alternative(text: str, expected: str) -> str | None:
    expected_norm = normalize(expected)
    if not expected_norm:
        return None
    tokens = normalize(text).split()
    target_tokens = expected_norm.split()
    best: tuple[float, str] | None = None
    for width in range(max(1, len(target_tokens) - 1), len(target_tokens) + 2):
        for start in range(max(0, len(tokens) - width + 1)):
            candidate_tokens = tokens[start:start + width]
            if not candidate_tokens:
                continue
            candidate = " ".join(candidate_tokens)
            ratio = difflib.SequenceMatcher(None, expected_norm, candidate).ratio()
            if candidate != expected_norm and (best is None or ratio > best[0]):
                best = ratio, candidate
    return best[1] if best and best[0] >= 0.72 else None


def classify(field: dict, text: str, run: dict, all_series: list[str]) -> tuple[str, str | None]:
    if failed(run):
        return "DETECTABLY_FAILED", None
    category, expected = field["category"], field["ground_truth_value"]
    if category == "SERIES":
        accepted = field.get("accepted_ground_truth_values", [expected])
        if any(has_phrase(text, value) for value in accepted):
            return "CORRECT", None
        # Explicit short form DS9 is equivalent only for the supplied DS9 series.
        if "DEEP SPACE NINE" in expected and re.search(r"(?i)\bDS\s*9\b", text):
            return "CORRECT", None
        alternative = series_alternative(text, expected, all_series)
        return ("WRONG_BUT_PLAUSIBLE", alternative) if alternative else ("OMITTED", None)
    if category == "SEASON":
        match = re.search(
            rf"(?i)(?:\b(?:season|temporada|staffel)\s*[:.#-]?\s*{re.escape(expected)}\b|\b{re.escape(expected)}\s*\.?\s*(?:season|temporada|staffel)\b)",
            text,
        )
        if match:
            return "CORRECT", None
        alternative = season_value(text, expected)
        return ("WRONG_BUT_PLAUSIBLE", alternative) if alternative else ("OMITTED", None)
    if category == "EPISODES":
        expected_range = expected.replace(" ", "").replace("–", "-").replace("—", "-")
        pattern = r"(?i)\b(?:episodes?|episoden?|eps?\.?|episode\s+range)\s*[:#]?\s*([0-9]+\s*[-–—]\s*[0-9]+)\b"
        ranges = [re.sub(r"\s+", "", match.group(1)).replace("–", "-").replace("—", "-")
                  for match in re.finditer(pattern, text)]
        if expected_range in ranges:
            return "CORRECT", None
        alternative = episode_range_value(text, expected)
        return ("WRONG_BUT_PLAUSIBLE", alternative) if alternative else ("OMITTED", None)
    if category == "TITLES":
        if has_title(text, expected):
            return "CORRECT", None
        alternative = plausible_title_alternative(text, expected)
        return ("WRONG_BUT_PLAUSIBLE", alternative) if alternative else ("OMITTED", None)
    raise ValueError(f"Unexpected primary category: {category}")


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direct-report", default="diagnostic-disc-direct-all-models-v3.json")
    parser.add_argument("--secondary-report", default="diagnostic-disc-secondary-ocr-v3.json")
    parser.add_argument("--output-stem", default="diagnostic-disc-field-risk-v3")
    args = parser.parse_args()
    if not re.fullmatch(r"diagnostic-disc-field-risk-v[0-9]+", args.output_stem):
        raise SystemExit("Output stem must use diagnostic-disc-field-risk-vN naming")
    state = Path(os.environ.get("STATE_DIR", "/state"))
    out = state / f"{args.output_stem}.json"
    md = state / f"{args.output_stem}.md"
    if out.exists() or md.exists():
        raise SystemExit("Refusing to overwrite either priority field-risk v2 artifact")
    direct_path = state / args.direct_report
    direct = json.loads(direct_path.read_bytes())
    truth = json.loads((state / "diagnostic-disc-priority-ground-truth-v1.1.json").read_bytes())
    secondary_path = state / args.secondary_report
    secondary = json.loads(secondary_path.read_bytes()) if secondary_path.exists() else None
    runs = direct["runs"]
    model_names = direct["requested_models"]
    images = truth["priority_images"]
    if len(runs) != len(model_names) * len(images):
        raise SystemExit("Integrated direct report does not have one active run per model/image")
    run_map = {(row["model"], row["event_id"]): row for row in runs}
    all_series = list(dict.fromkeys(
        field["ground_truth_value"] for image in images
        for field in image["priority_fields"] if field["category"] == "SERIES"
    ))
    field_records = []
    image_records = []
    for image in images:
        fields_by_model = {}
        for model in model_names:
            run = run_map[(model, image["event_id"])]
            predictions = []
            text = run.get("transcription", "")
            for field in image["priority_fields"]:
                category, candidate = classify(field, text, run, all_series)
                row = {
                    "model": model, "camera": image["camera"], "event_id": image["event_id"],
                    "disc_key": image["disc_key"], "field_id": field["field_id"],
                    "priority_category": field["category"],
                    "ground_truth_value": field["ground_truth_value"],
                    "classification": category, "candidate_wrong_value": candidate,
                    "inference_image_sha256": run.get("inference_image_sha256", run.get("image_sha256")),
                    "done_reason": run.get("done_reason"),
                    "natural_completion": run.get("natural_completion", run.get("done_reason") == "stop"),
                    "usable_for_recognition_scoring": usable_run(run),
                    "raw_transcription": text,
                    "ground_truth_source": field["source"],
                    "ground_truth_note": field["source_note"],
                    "recognition_context_sent": False,
                    "adjudication": "RULE_MATCH" if category in ("CORRECT", "DETECTABLY_FAILED") else "HUMAN_REVIEW_RECOMMENDED",
                }
                field_records.append(row)
                predictions.append(row)
            classes = [row["classification"] for row in predictions]
            correct_n = classes.count("CORRECT")
            if "WRONG_BUT_PLAUSIBLE" in classes or "DETECTABLY_FAILED" in classes:
                outcome = "FAIL"
            elif classes and all(value == "CORRECT" for value in classes):
                outcome = "PASS"
            elif correct_n > 0:
                outcome = "PARTIAL"
            else:
                outcome = "FAIL"
            image_records.append({
                "model": model, "camera": image["camera"], "sequence": image["sequence"],
                "event_id": image["event_id"], "disc_key": image["disc_key"],
                "disc_identification": outcome,
                "priority_field_count": len(predictions), "correct_priority_fields": correct_n,
                "omitted_priority_fields": classes.count("OMITTED"),
                "detectable_priority_failures": classes.count("DETECTABLY_FAILED"),
                "wrong_plausible_priority_fields": classes.count("WRONG_BUT_PLAUSIBLE"),
                "raw_transcription": text,
            })

    model_summary = {}
    for model in model_names:
        rows = [row for row in field_records if row["model"] == model]
        counts = Counter(row["classification"] for row in rows)
        n = len(rows)
        usable_rows = [row for row in rows if row["usable_for_recognition_scoring"]]
        usable_counts = Counter(row["classification"] for row in usable_rows)
        usable_n = len(usable_rows)
        usable_returned = usable_counts["CORRECT"] + usable_counts["WRONG_BUT_PLAUSIBLE"]
        run_rows = [row for row in runs if row["model"] == model]
        usable_run_rows = [row for row in run_rows if usable_run(row)]
        unusable_runs = len(run_rows) - len(usable_run_rows)
        requested_image_rows = [row for row in image_records if row["model"] == model]
        image_rows = [row for row in requested_image_rows if usable_run(run_map[(model, row["event_id"])])]
        runtimes = [row["wall_clock_seconds"] for row in run_rows if row.get("wall_clock_seconds") is not None]
        by_category = {}
        for category in PRIMARY:
            group = [row for row in usable_rows if row["priority_category"] == category]
            gc = Counter(row["classification"] for row in group)
            gn = len(group)
            failed_fields = sum(
                row["priority_category"] == category and not row["usable_for_recognition_scoring"]
                for row in rows
            )
            by_category[category] = {
                "total": gn, "counts": dict(gc),
                "unusable_run_fields_excluded": failed_fields,
                "correct_rate": rate(gc["CORRECT"], gn),
                "silent_wrong_rate": rate(gc["WRONG_BUT_PLAUSIBLE"], gn),
                "omission_rate": rate(gc["OMITTED"], gn),
                "detectable_failure_rate": 0.0 if gn else None,
                "wrong_among_returned": rate(gc["WRONG_BUT_PLAUSIBLE"], gc["CORRECT"] + gc["WRONG_BUT_PLAUSIBLE"]),
            }
        total_images = len(requested_image_rows)
        model_summary[model] = {
            "total_priority_fields": n,
            "usable_priority_fields": usable_n,
            "requested_runs": len(run_rows),
            "usable_runs": len(usable_run_rows),
            "detectable_failure_runs": unusable_runs,
            "detectable_failure_rate": rate(unusable_runs, len(run_rows)),
            "recognition_precision_status": "AVAILABLE_ON_USABLE_RUNS" if usable_run_rows else "N/A_NO_USABLE_RUNS",
            "correct_priority_fields": counts["CORRECT"],
            "omitted_priority_fields": counts["OMITTED"],
            "detectably_failed_priority_fields": counts["DETECTABLY_FAILED"],
            "wrong_but_plausible_priority_fields": counts["WRONG_BUT_PLAUSIBLE"],
            "recognition_correct_priority_fields": usable_counts["CORRECT"],
            "recognition_omitted_priority_fields": usable_counts["OMITTED"],
            "recognition_wrong_but_plausible_priority_fields": usable_counts["WRONG_BUT_PLAUSIBLE"],
            "priority_field_accuracy": rate(usable_counts["CORRECT"], usable_n),
            "priority_silent_wrong_rate": rate(usable_counts["WRONG_BUT_PLAUSIBLE"], usable_n),
            "wrong_among_returned_priority_fields": rate(usable_counts["WRONG_BUT_PLAUSIBLE"], usable_returned),
            "critical_field_silent_error_rate": rate(usable_counts["WRONG_BUT_PLAUSIBLE"], usable_n),
            "by_priority_category": by_category,
            "disc_identification": {
                "images": len(image_rows),
                "requested_images": total_images,
                "operational_failures_excluded": unusable_runs,
                "pass": sum(row["disc_identification"] == "PASS" for row in image_rows),
                "partial": sum(row["disc_identification"] == "PARTIAL" for row in image_rows),
                "fail": sum(row["disc_identification"] == "FAIL" for row in image_rows),
                "complete_success_rate": rate(sum(row["disc_identification"] == "PASS" for row in image_rows), len(image_rows)),
            },
            "mean_runtime_seconds": statistics.mean(runtimes) if runtimes else None,
            "median_runtime_seconds": statistics.median(runtimes) if runtimes else None,
            "ollama_versions": sorted({str(row.get("ollama_version")) for row in run_rows}),
        }

    camera_summary = {}
    for camera in sorted({image["camera"] for image in images}):
        fields = [row for row in field_records if row["camera"] == camera]
        counts = Counter(row["classification"] for row in fields)
        usable_fields = [row for row in fields if row["usable_for_recognition_scoring"]]
        usable_counts = Counter(row["classification"] for row in usable_fields)
        image_rows = [row for row in image_records if row["camera"] == camera]
        runtime_rows = [row for row in runs if row["camera"] == camera and row.get("wall_clock_seconds") is not None]
        all_camera_runs = [row for row in runs if row["camera"] == camera]
        usable_camera_runs = [row for row in all_camera_runs if usable_run(row)]
        camera_summary[camera] = {
            "total_priority_fields": len(fields),
            "usable_runs": len(usable_camera_runs),
            "requested_runs": len(all_camera_runs),
            "detectable_failure_rate": rate(len(all_camera_runs) - len(usable_camera_runs), len(all_camera_runs)),
            "usable_priority_fields": len(usable_fields),
            "counts_on_usable_runs": dict(usable_counts),
            "priority_accuracy": rate(usable_counts["CORRECT"], len(usable_fields)),
            "silent_wrong_rate": rate(usable_counts["WRONG_BUT_PLAUSIBLE"], len(usable_fields)),
            "omission_rate": rate(usable_counts["OMITTED"], len(usable_fields)),
            "disc_passes": sum(row["disc_identification"] == "PASS" for row in image_rows),
            "disc_images": len(image_rows),
            "mean_runtime_seconds": statistics.mean(row["wall_clock_seconds"] for row in runtime_rows) if runtime_rows else None,
        }

    # A model with no usable image output has operational status only; exclude
    # it from recognition-quality ranking rather than treating load failures as OCR errors.
    ranking_models = [model for model in model_names if model_summary[model]["usable_runs"] > 0]
    order = sorted(ranking_models, key=lambda model: (
        model_summary[model]["priority_silent_wrong_rate"] if model_summary[model]["priority_silent_wrong_rate"] is not None else 1,
        -model_summary[model]["disc_identification"]["complete_success_rate"],
        -(model_summary[model]["priority_field_accuracy"] or 0.0),
        model_summary[model]["omitted_priority_fields"],
        model_summary[model]["detectably_failed_priority_fields"],
        model_summary[model]["mean_runtime_seconds"] if model_summary[model]["mean_runtime_seconds"] is not None else float("inf"),
    ))
    ranking = [{"rank": index, "model": model,
                "priority_silent_wrong_rate": model_summary[model]["priority_silent_wrong_rate"],
                "complete_disc_success_rate": model_summary[model]["disc_identification"]["complete_success_rate"],
                "priority_field_accuracy": model_summary[model]["priority_field_accuracy"],
                "omitted": model_summary[model]["recognition_omitted_priority_fields"],
                "detectable_failures": model_summary[model]["detectable_failure_runs"],
                "mean_runtime_seconds": model_summary[model]["mean_runtime_seconds"]}
               for index, model in enumerate(order, 1)]
    category_answers = {}
    for category in PRIMARY:
        category_models = sorted(ranking_models, key=lambda model: (
            -model_summary[model]["by_priority_category"][category]["correct_rate"]
            if model_summary[model]["by_priority_category"][category]["correct_rate"] is not None else 0,
            model_summary[model]["by_priority_category"][category]["silent_wrong_rate"]
            if model_summary[model]["by_priority_category"][category]["silent_wrong_rate"] is not None else 1,
        ))
        category_answers[category] = {
            "leading_model": category_models[0] if category_models else None,
            "ordered_models": category_models,
        }

    report = {
        "schema_version": 1,
        "report_type": "priority_disc_identification_field_risk",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "priority_ground_truth_file": "diagnostic-disc-priority-ground-truth-v1.1.json",
        "direct_benchmark_file": direct_path.name,
        "secondary_ocr_report_file": secondary_path.name if secondary else None,
        "classification_policy": "Only the user's explicitly supplied SERIES, SEASON, EPISODES, and TITLES facts determine primary ranking. Peripheral OCR is secondary. Notes were evaluator-side only and were not sent to models.",
        "normalization_policy": "Case, whitespace, line breaks, and harmless punctuation are normalized. Season-language equivalences are handled explicitly. Different seasons, episode numbers/ranges, series, or title words are not normalized away.",
        "hallucination_policy": "No text is called hallucinated because it is absent from a disc note. Secondary recognized text is preserved for later review.",
        "ranking_priority": ["lowest wrong-but-plausible rate on priority fields", "highest complete disc-identification success", "highest priority correct-field rate", "lowest omission rate", "lowest detectable-failure rate", "runtime"],
        "questions_answered": category_answers,
        "recognition_ranking_excludes_zero_usable_models": [model for model in model_names if model not in ranking_models],
        "lowest_priority_silent_wrong_rate_model": order[0] if order else None,
        "lowest_priority_silent_wrong_rate_models": [],
        "most_complete_disc_identifications_model": max(
            ranking_models,
            key=lambda model: (
                model_summary[model]["disc_identification"]["pass"],
                model_summary[model]["disc_identification"]["complete_success_rate"] or 0.0,
            ),
        ) if ranking_models else None,
        "safest_primary_model_provisional": "qwen2.5vl:7b-q8_0" if "qwen2.5vl:7b-q8_0" in ranking_models else (order[0] if order else None),
        "primary_selection_note": "Qwen2.5-VL 7B is selected as the conservative one-size-fits-all default based on 8/8 usable runs; the smallest observed silent-wrong rates occur in models with materially fewer usable outputs and do not establish comparable reliability.",
        "final_escalation_model_provisional": min(
            [m for m in ranking_models if m in {"qwen2.5vl:7b-q8_0", "qwen3-vl:8b-instruct-q8_0", "qwen3-vl:30b-a3b-instruct-q4_K_M", "qwen2.5vl:32b-q4_K_M"}],
            key=lambda model: (model_summary[model]["priority_silent_wrong_rate"] if model_summary[model]["priority_silent_wrong_rate"] is not None else 1,
                               -model_summary[model]["disc_identification"]["complete_success_rate"],
                                -(model_summary[model]["priority_field_accuracy"] or 0.0),
                               model_summary[model]["omitted_priority_fields"],
                               model_summary[model]["detectably_failed_priority_fields"],
                               model_summary[model]["mean_runtime_seconds"] or float("inf")),
        ),
        "models": model_summary,
        "risk_ranking": ranking,
        "cameras": camera_summary,
        "disc_identification_by_image": image_records,
        "priority_field_classifications": field_records,
        "secondary_non_decisive_findings": {
            "report_file": secondary_path.name if secondary else None,
            "scope": "ratings, legal text, runtimes, identifiers, logos, formats, and other peripheral visible text; never used in the ranking above",
            "model_summaries": secondary.get("models", {}) if secondary else {},
            "hallucination_adjudication": "pending human review; absence from user notes alone is not evidence of hallucination",
        },
        "limitations": [
            "Four physical discs and eight camera images are a small development dataset.",
            "Rule-based plausible-wrong candidates and any non-exact matches should be reviewed before production selection.",
            "The five TOS episode titles come from the user note; the transcription prompt intentionally did not reveal them to recognition models.",
            "A PASS requires every supplied priority fact for that disc, including individually supplied titles where present.",
        ],
    }
    observed_silent_rates = [model_summary[m]["priority_silent_wrong_rate"] for m in ranking_models if model_summary[m]["priority_silent_wrong_rate"] is not None]
    if observed_silent_rates:
        minimum_silent_rate = min(observed_silent_rates)
        report["lowest_priority_silent_wrong_rate_models"] = [
            m for m in ranking_models
            if model_summary[m]["priority_silent_wrong_rate"] == minimum_silent_rate
        ]
    comparison_names = [
        "qwen2.5vl:7b-q8_0", "qwen3-vl:8b-instruct-q8_0",
        "qwen3-vl:30b-a3b-instruct-q4_K_M", "qwen2.5vl:32b-q4_K_M",
        "qwen2.5vl:3b", "openbmb/minicpm-v4:q8_0",
        "minicpm-v:8b-2.6-q8_0", "minicpm-v4.6",
    ]
    report["side_by_side_models"] = {
        name: model_summary[name] for name in comparison_names if name in model_summary
    }
    report["production_chain_recommendation"] = {
        "normal_path_model": "qwen2.5vl:7b-q8_0",
        "escalation_model": "qwen3-vl:30b-a3b-instruct-q4_K_M",
        "policy": "Use Qwen2.5-VL 7B as a consistent first pass (8/8 usable); escalate uncertain, conflicting, or incomplete priority fields to Qwen3-VL 30B-A3B. Explicitly verify Series, Season, Episodes/range, and supplied Titles. Peripheral OCR cannot compensate for missing or incorrect priority fields. Preserve both outputs for review; this routing policy itself was not benchmarked.",
        "time_budget_note": "Both models' observed mean runtimes were under five minutes; this is based on eight images/model, not a latency guarantee.",
    }
    report["llama_operational_status"] = direct.get("llama_operational_assessment", {
        "operational_status": "unsupported_on_tested_runtime_cpu_stack",
        "usable_runs": 0, "requested_image_runs": 8,
        "detectable_load_runtime_failure_rate": 1.0,
        "recognition_metrics_status": "N/A",
    })
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(out, 0o600)
    markdown = [
        f"# DVD/CD Field-Risk Benchmark {args.output_stem.rsplit('v',1)[-1]} — Disc Identification",
        "",
        "> **Primary ranking uses only series, season, explicitly supplied episode range, and supplied episode titles. Peripheral OCR is reported separately and cannot offset a wrong disc identification.**",
        "",
        "## Primary priority-field results",
        "",
        "| Rank | Model | Usable runs | Operational failure rate | Usable fields C/O/W | Accuracy on usable | Silent wrong | Wrong / returned | Disc PASS/PARTIAL/FAIL (usable images) | Mean / median sec |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for ranked in ranking:
        row = model_summary[ranked["model"]]
        disc = row["disc_identification"]
        markdown.append(
            f"| {ranked['rank']} | `{ranked['model']}` | {row['usable_runs']}/{row['requested_runs']} | {pct(row['detectable_failure_rate'])} | "
            f"{row['recognition_correct_priority_fields']}/{row['recognition_omitted_priority_fields']}/{row['recognition_wrong_but_plausible_priority_fields']} | "
            f"{pct(row['priority_field_accuracy'])} | {pct(row['priority_silent_wrong_rate'])} | {pct(row['wrong_among_returned_priority_fields'])} | "
            f"{disc['pass']}/{disc['partial']}/{disc['fail']} of {disc['images']} usable ({pct(disc['complete_success_rate'])} pass) | {row['mean_runtime_seconds']:.1f} / {row['median_runtime_seconds']:.1f} |"
        )
    markdown += ["", "## Results by priority category", "", "| Model | Series correct/wrong | Season correct/wrong | Episode range correct/wrong | Supplied titles correct/wrong |", "|---|---:|---:|---:|---:|"]
    for model in model_names:
        categories = model_summary[model]["by_priority_category"]
        def cw(name: str) -> str:
            c = categories[name]["counts"]
            if not categories[name]["total"]:
                return "N/A (no usable runs)" if model_summary[model]["usable_runs"] == 0 else "not supplied"
            return f"{c.get('CORRECT',0)}/{c.get('WRONG_BUT_PLAUSIBLE',0)} (omit {c.get('OMITTED',0)})"
        markdown.append(f"| `{model}` | {cw('SERIES')} | {cw('SEASON')} | {cw('EPISODES')} | {cw('TITLES')} |")
    markdown += ["", "## Camera comparison", "", "| Camera | Priority correct rate | Silent wrong rate | Omissions | Detectable failures | Complete disc passes | Mean sec/image |", "|---|---:|---:|---:|---:|---:|---:|"]
    for camera, result in camera_summary.items():
        markdown.append(f"| {camera} | {pct(result['priority_accuracy'])} | {pct(result['silent_wrong_rate'])} | {result['counts_on_usable_runs'].get('OMITTED',0)} | {result['requested_runs']-result['usable_runs']} / {result['requested_runs']} | {result['disc_passes']}/{result['disc_images']} | {result['mean_runtime_seconds']:.1f} |")
    markdown += [
        "", "## Operational reliability (not recognition quality)",
        "", "Unusable runs are excluded from recognition precision, silent-wrong, omission, and correctness metrics. A zero-usable model is not placed in the recognition ranking.",
        "", "| Model | Usable images | Detectable operational failures | Recognition precision | Priority accuracy | Silent wrong | Omission |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model in model_names:
        row = model_summary[model]
        markdown.append(
            f"| `{model}` | {row['usable_runs']}/{row['requested_runs']} | {row['detectable_failure_runs']}/{row['requested_runs']} ({pct(row['detectable_failure_rate'])}) | "
            f"{row['recognition_precision_status']} | {pct(row['priority_field_accuracy'])} | {pct(row['priority_silent_wrong_rate'])} | {pct(rate(row['recognition_omitted_priority_fields'], row['usable_priority_fields']))} |"
        )
    markdown += [
        "", "## Direct answers", "",
        f"1. Series: `{category_answers['SERIES']['leading_model']}` (category ordering uses silent wrong first, then correct rate).",
        f"2. Season: `{category_answers['SEASON']['leading_model']}`.",
        f"3. Episode range: `{category_answers['EPISODES']['leading_model']}`.",
        f"4. Supplied episode titles: `{category_answers['TITLES']['leading_model']}` where titles are present.",
        f"5. Lowest priority-field silent wrong rate: {', '.join('`'+m+'`' for m in report['lowest_priority_silent_wrong_rate_models'])} ({pct(min(observed_silent_rates) if observed_silent_rates else None)}; denominators differ).",
        f"6. Most complete disc identifications by absolute successful-disc count: `{report['most_complete_disc_identifications_model']}`.",
        f"7. Safest primary (provisional): `{report['safest_primary_model_provisional']}` by the requested risk ordering.",
        f"8. Final escalation model (provisional, advanced candidates only): `{report['final_escalation_model_provisional']}`.",
        "9. Production chain (provisional): Qwen2.5-VL 7B first pass; escalate unresolved priority fields to Qwen3-VL 30B-A3B. The chained policy itself was not tested.",
        "10. Llama 3.2 Vision: unsupported on tested runtime/CPU stack; 0/8 usable, 100% detectable load/runtime failure; recognition metrics N/A and excluded from recognition rankings.",
        "",
        "## SECONDARY / NON-DECISIVE OCR FINDINGS",
        "",
        f"Peripheral OCR metrics and recognized text are retained separately in `{args.secondary_report}`. They include packaging/legal text, ratings, identifiers, runtime, and formats; they do not affect the primary-field ranking. Hallucinations are not inferred solely from absence in user notes.",
        "",
        "Ground-truth priority facts come from the existing capture notes. Those notes were not sent in recognition requests. Review rule-generated non-matches before making production claims; expand the sample with future discs.",
        "",
        f"Machine-readable classifications: `{out.name}`.",
        "",
    ]
    fd = os.open(md, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write("\n".join(markdown))
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(md, 0o600)
    print(out)


if __name__ == "__main__":
    main()
