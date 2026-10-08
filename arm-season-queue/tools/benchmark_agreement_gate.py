#!/usr/bin/env python3
"""Score the two-model agreement-only rip/eject gate on retained exact runs.

This is offline only: it reads an existing direct-model report and independent
priority ground truth, performs no inference, and creates exclusive report files.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from score_priority_disc_identification import classify, usable_run
from benchmark_chain_order import MODEL_A, MODEL_B, PROMPT, validate_request
from app.agreement import evaluate as evaluate_production_gate
from app.formats import Masterlist

STATE = Path(os.environ.get('STATE_DIR', '/state'))
REPORT_DIR = Path(os.environ.get('REPORT_DIR', str(STATE)))
DIRECT = 'diagnostic-disc-direct-all-models-v4.json'
TRUTH = 'diagnostic-disc-priority-ground-truth-v1.1.json'
PRIMARY = 'qwen3-vl:30b-a3b-instruct-q4_K_M'
SECONDARY = 'qwen2.5vl:7b-q8_0'


def norm(value: str) -> str:
    value = unicodedata.normalize('NFKD', str(value or '').casefold())
    return ' '.join(''.join(c for c in value if not unicodedata.combining(c)).split())


def numbers(value) -> set[int]:
    found = re.fullmatch(r'\s*(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?\s*', str(value or ''))
    if not found:
        return set()
    first, last = int(found[1]), int(found[2] or found[1])
    return set(range(first, last + 1)) if first <= last and last - first <= 100 else set()


def ground_truth_master_matches(image: dict, masters: list[Masterlist]) -> list[dict]:
    """Independent ground-truth match oracle; never used to make gate decisions."""
    fields = image.get('priority_fields', [])
    by_category = {field['category']: field for field in fields if field['category'] != 'TITLES'}
    series_field = by_category.get('SERIES', {})
    allowed_series = series_field.get('accepted_ground_truth_values') or [series_field.get('ground_truth_value', '')]
    titles_expected = {norm(field['ground_truth_value']) for field in fields if field['category'] == 'TITLES'}
    episodes_expected = [numbers(field['ground_truth_value']) for field in fields if field['category'] == 'EPISODES']
    matches = []
    for master in masters:
        if not master.approved or norm(master.series) not in {norm(x) for x in allowed_series}:
            continue
        if str(master.season) != str(by_category.get('SEASON', {}).get('ground_truth_value')):
            continue
        for disc in master.discs:
            episode_numbers = set()
            titles = set()
            for episode in disc.episodes:
                episode_numbers.update(numbers(episode.printed))
                titles.add(norm(episode.title))
            if not episode_numbers or set().union(*episodes_expected) != episode_numbers:
                continue
            if titles_expected and titles_expected != titles:
                continue
            matches.append({'master': master.id, 'disc': disc.id,
                            'series': master.series, 'season': master.season})
    return matches


def load_masters(state: Path) -> list[Masterlist]:
    path = state / 'queue.sqlite3'
    if not path.is_file():
        raise SystemExit(f'Cannot benchmark production matching: state database is missing: {path.name}')
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            return [Masterlist.model_validate_json(row[0]) for row in db.execute('SELECT body FROM masters')]
    except (sqlite3.Error, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f'Cannot benchmark production matching: state masterlists could not be loaded ({type(exc).__name__})') from None


def classify_policy(direct: dict, truth: dict, masters: list[Masterlist] | None = None) -> dict:
    masters = masters or []
    images = truth['priority_images']
    runs = index_model_image_runs(direct['runs'])
    known_series = list(dict.fromkeys(
        field['ground_truth_value'] for image in images for field in image['priority_fields']
        if field['category'] == 'SERIES'))
    records = []
    for image in images:
        event = image['event_id']
        pair = {tag: runs.get((tag, event)) for tag in (PRIMARY, SECONDARY)}
        operational = [tag for tag, run in pair.items() if not run or not usable_run(run)
                       or run.get('_retained_evidence_valid') is False]
        operational_kinds = {}
        for tag, run in pair.items():
            if run and usable_run(run) and run.get('_retained_evidence_valid') is not False:
                continue
            error = str((run or {}).get('error') or '').casefold()
            evidence_error = (run or {}).get('_retained_evidence_error')
            if evidence_error in ('missing_response', 'malformed_response', 'truncated', 'timed_out', 'unusable_response'):
                kind = evidence_error
            elif run is None:
                kind = 'missing_response'
            elif 'timeout' in error or 'timed out' in error:
                kind = 'timed_out'
            elif (run or {}).get('done_reason') == 'length':
                kind = 'truncated'
            elif (run or {}).get('http_status') not in (None, 200):
                kind = 'http_or_runtime_failure'
            elif 'malformed' in error or 'invalid json' in error:
                kind = 'malformed_response'
            elif not (run or {}).get('transcription', '').strip():
                kind = 'missing_response'
            else:
                kind = 'unusable_response'
            operational_kinds[tag] = kind
        scored = {}
        for tag, run in pair.items():
            text = (run or {}).get('transcription', '')
            scored[tag] = []
            for field in image['priority_fields']:
                classification, alternative = classify(field, text, run or {}, known_series)
                scored[tag].append({'field_id': field['field_id'], 'category': field['category'],
                                    'expected': field['ground_truth_value'],
                                    'classification': classification, 'candidate_wrong_value': alternative})
        primary_classes = [x['classification'] for x in scored[PRIMARY]]
        secondary_classes = [x['classification'] for x in scored[SECONDARY]]
        disagreement = not operational and any(
            (a['classification'], a['candidate_wrong_value']) != (b['classification'], b['candidate_wrong_value'])
            for a, b in zip(scored[PRIMARY], scored[SECONDARY]))
        correctly_agreed_fields = sum(a['classification'] == b['classification'] == 'CORRECT'
                                      for a, b in zip(scored[PRIMARY], scored[SECONDARY]))
        incorrectly_agreed_fields = sum(
            a['classification'] == b['classification'] == 'WRONG_BUT_PLAUSIBLE'
            and a['candidate_wrong_value'] == b['candidate_wrong_value']
            for a, b in zip(scored[PRIMARY], scored[SECONDARY]))
        field_agreement_details = []
        for left, right in zip(scored[PRIMARY], scored[SECONDARY]):
            same = (left['classification'], left['candidate_wrong_value']) == (right['classification'], right['candidate_wrong_value'])
            if operational:
                outcome = 'operational_failure'
            elif left['classification'] == right['classification'] == 'CORRECT':
                outcome = 'both_correct'
            elif same and left['classification'] == 'WRONG_BUT_PLAUSIBLE':
                outcome = 'both_agree_incorrectly'
            elif not same:
                outcome = 'disagreement'
            elif left['classification'] == 'OMITTED':
                outcome = 'both_missing'
            else:
                outcome = 'both_unusable'
            field_agreement_details.append({'field_id': left['field_id'], 'category': left['category'],
                'outcome': outcome, 'primary': left, 'secondary': right})
        missing = any(value == 'OMITTED' for value in primary_classes + secondary_classes)
        ambiguous = any(value == 'WRONG_BUT_PLAUSIBLE' for value in primary_classes + secondary_classes)
        # Ground truth is used solely as the scoring oracle. Production makes the
        # equivalent match against the user's approved masterlist, never this file.
        recognition_pass = (not operational and not disagreement and not missing and not ambiguous
                            and bool(primary_classes) and all(x == 'CORRECT' for x in primary_classes + secondary_classes))
        truth_master_matches = ground_truth_master_matches(image, masters)
        production_runs = {
            tag: {'usable': bool(pair[tag] and usable_run(pair[tag])
                             and pair[tag].get('_retained_evidence_valid') is not False),
                  'transcription': (pair[tag] or {}).get('transcription', '')}
            for tag in (PRIMARY, SECONDARY)
        }
        production_result = evaluate_production_gate(
            {tag: production_runs[tag]['transcription'] for tag in (PRIMARY, SECONDARY)},
            production_runs, masters)
        passed = bool(production_result.get('accepted') and len(production_result.get('matches', [])) == 1)
        unique_truth_match = truth_master_matches[0] if len(truth_master_matches) == 1 else None
        production_match = production_result.get('matches', [None])[0] if passed else None
        matches_ground_truth_disc = bool(
            unique_truth_match and production_match
            and production_match.get('master') == unique_truth_match.get('master')
            and production_match.get('disc') == unique_truth_match.get('disc'))
        reasons = []
        if operational: reasons.append('operational_failure')
        if disagreement: reasons.append('model_disagreement')
        if missing: reasons.append('missing_priority_field')
        if ambiguous: reasons.append('ambiguous_or_wrong_priority_field')
        if not passed:
            reasons.extend(production_result.get('reasons') or ['production_gate_rejected'])
        if not reasons and not passed: reasons.append('incomplete_or_malformed_priority_fields')
        records.append({
            'event_id': event, 'disc_key': image.get('disc_key'), 'camera': image.get('camera'),
            'decision': 'WOULD_AUTO_RIP' if passed else 'WOULD_REJECT_REQUEST_EJECT_MANUAL_REVIEW',
            'recognition_agreement': 'OPERATIONAL_FAILURE' if operational else
                'DISAGREEMENT' if disagreement else
                'BOTH_AGREE_CORRECTLY' if recognition_pass else
                'BOTH_AGREE_MIXED_CORRECT_AND_INCORRECT' if (incorrectly_agreed_fields and correctly_agreed_fields) else
                'BOTH_AGREE_INCORRECTLY' if (incorrectly_agreed_fields and not missing) else
                'MISSING_FIELDS' if missing else 'INCOMPLETE_OR_UNUSABLE',
            'priority_fields_correctly_agreed': correctly_agreed_fields,
            'priority_fields_incorrectly_agreed': incorrectly_agreed_fields,
            'field_agreement_details': field_agreement_details,
            'production_gate': production_result,
            'ground_truth_approved_masterlist_matches': truth_master_matches,
            'reject_reasons': reasons, 'operational_failures': operational,
            'operational_failure_kinds': operational_kinds,
            'field_results': scored,
            'false_accept': bool(passed and (
                any(x['classification'] != 'CORRECT' for rows in scored.values() for x in rows)
                or (unique_truth_match is not None and not matches_ground_truth_disc))),
            'wrong_disc_false_accept': bool(passed and unique_truth_match is not None and not matches_ground_truth_disc),
            'production_match_matches_unique_ground_truth_disc': matches_ground_truth_disc if unique_truth_match else None,
            'false_reject': bool(not passed and len(truth_master_matches) == 1),
        })
    return records


def build_report(direct: dict, truth: dict, masters: list[Masterlist] | None = None) -> dict:
    masters = masters or []
    records = classify_policy(direct, truth, masters)
    rip = sum(x['decision'] == 'WOULD_AUTO_RIP' for x in records)
    rejects = [x for x in records if x['decision'] != 'WOULD_AUTO_RIP']
    all_reasons = sorted({reason for row in rejects for reason in row['reject_reasons']})
    reason_counts = {reason: sum(reason in row['reject_reasons'] for row in rejects)
                     for reason in all_reasons}
    approved_count = sum(master.approved for master in masters)
    by_camera = {}
    for camera in sorted({row.get('camera') for row in records}):
        group = [row for row in records if row.get('camera') == camera]
        eligible_false_rejects = sum(len(r['ground_truth_approved_masterlist_matches']) == 1 for r in group)
        by_camera[camera] = {'images': len(group),
                               'would_auto_rip': sum(r['decision'] == 'WOULD_AUTO_RIP' for r in group),
                               'would_reject_request_eject': sum(r['decision'] != 'WOULD_AUTO_RIP' for r in group),
                              'false_accepts': sum(r['false_accept'] for r in group),
                               'false_rejects': (sum(r['false_reject'] for r in group)
                                                 if eligible_false_rejects else None),
                               'false_reject_denominator_unique_ground_truth_disc_matches': eligible_false_rejects}
    false_accepts = sum(row['false_accept'] for row in records)
    disc_groups = {}
    for row in records:
        disc_groups.setdefault(row.get('disc_key'), []).append(row)
    by_disc = {key: {
        'image_count': len(group), 'events': [row['event_id'] for row in group],
        'camera_decisions': {row['camera']: row['decision'] for row in group},
        'camera_recognition_agreement': {row['camera']: row['recognition_agreement'] for row in group},
        'any_camera_would_auto_rip': any(row['decision'] == 'WOULD_AUTO_RIP' for row in group),
        'false_accept_on_any_image': any(row['false_accept'] for row in group),
        'ground_truth_master_match_count_by_image': {row['event_id']: len(row['ground_truth_approved_masterlist_matches']) for row in group},
    } for key, group in disc_groups.items()}
    failure_kind_counts = {}
    for row in records:
        for kind in row['operational_failure_kinds'].values():
            failure_kind_counts[kind] = failure_kind_counts.get(kind, 0) + 1
    for kind in ('missing_response','malformed_response','truncated','timed_out','http_or_runtime_failure','unusable_response'):
        failure_kind_counts.setdefault(kind, 0)
    recognition_counts = {}
    field_counts = {}
    for row in records:
        category = row['recognition_agreement']
        recognition_counts[category] = recognition_counts.get(category, 0) + 1
        for field in row['field_agreement_details']:
            outcome = field['outcome']
            field_counts[outcome] = field_counts.get(outcome, 0) + 1
    unique_truth_match_count = sum(len(row['ground_truth_approved_masterlist_matches']) == 1 for row in records)
    false_reject_count = (sum(row['false_reject'] for row in records)
                          if unique_truth_match_count else None)
    return {
        'schema_version': 1, 'created_at': time.time(),
        'policy': 'production app.agreement.evaluate; model agreement and a unique approved-masterlist match required, otherwise reject/eject/manual review',
        'models': [PRIMARY, SECONDARY], 'source_report': DIRECT, 'ground_truth': TRUTH,
        'ollama_version_reported_by_source_report': direct.get('ollama_version'),
        'runtime_note': 'Version is source-report metadata and is not asserted as a per-inference-run attestation.',
        'dataset_images': len(records), 'physical_disc_count': len({r.get('disc_key') for r in records}),
        'would_auto_rip': rip, 'would_reject_request_eject': len(rejects),
        'would_auto_rip_rate': rip / len(records) if records else None,
        'would_reject_rate': len(rejects) / len(records) if records else None,
        'reject_reason_case_counts_nonexclusive': reason_counts,
        'false_accept_count': false_accepts,
        'false_accept_denominator_simulated_auto_rips': rip,
        'false_accept_rate_among_simulated_auto_rips': false_accepts / rip if rip else None,
        'false_reject_count_where_determinable': false_reject_count,
        'false_reject_denominator_unique_approved_masterlist_matches': unique_truth_match_count,
        'wrong_disc_false_accept_count_where_determinable': sum(row['wrong_disc_false_accept'] for row in records),
        'wrong_disc_false_accept_denominator_unique_ground_truth_disc_matches': sum(
            len(row['ground_truth_approved_masterlist_matches']) == 1 for row in records),
        'by_camera': by_camera, 'cases': records,
        'by_physical_disc': by_disc,
        'operational_failure_kind_counts': failure_kind_counts,
        'recognition_agreement_image_counts': recognition_counts,
        'priority_field_agreement_counts': field_counts,
        'priority_field_agreement_denominator': sum(len(row['field_agreement_details']) for row in records),
        'validated_model_image_output_denominator': len(records) * 2,
        'approved_masterlist_count': approved_count,
        'state_masterlist_database_loaded': True,
        'benchmark_qualification': ('NOT_ASSESSABLE_NO_APPROVED_MASTERLISTS' if not approved_count
                                    else 'NOT_PRODUCTION_QUALIFIED_SMALL_DATASET'),
        'decision_method': 'app.agreement.evaluate production evaluator, with approved state masterlists; ground truth used only for field scoring and false-reject oracle',
        'interpretation': 'False rejects and wrong-disc false accepts require a unique approved masterlist disc match and are not determinable when none is loaded. The retained ground truth contains printed-priority fields, not independent source-title/edition signatures; it cannot establish technical disc mapping or production safety.',
        'source_evidence': getattr(build_report, '_evidence_index', {}),
    }


def index_model_image_runs(rows: list[dict]) -> dict[tuple[str, str], dict]:
    indexed = {}
    for row in rows:
        if row.get('model') not in (PRIMARY, SECONDARY):
            continue
        key = (row.get('model'), row.get('event_id'))
        if key in indexed:
            raise SystemExit(f'Duplicate retained model/image run; refusing ambiguous benchmark row for {key[1]}')
        indexed[key] = dict(row)
    return indexed


def validate_scored_transcription(state: Path, row: dict, validated: dict) -> None:
    """Verify the transcription being scored is reproduced by both retained response artifacts."""
    stream_path = state / validated['raw_stream_file']
    response_path = state / validated['raw_response_file']
    if row.get('raw_stream_sha256') != validated['raw_stream_sha256']:
        raise ValueError('retained_stream_digest_mismatch')
    stream_parts = []
    stream_rows = []
    for raw_line in stream_path.read_bytes().splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        stream_rows.append(payload)
        part = (payload.get('message') or {}).get('content')
        if isinstance(part, str):
            stream_parts.append(part)
    response = json.loads(response_path.read_bytes())
    response_text = (response.get('message') or {}).get('content')
    terminal = stream_rows[-1] if stream_rows else {}
    if (not isinstance(response_text, str)
            or ''.join(stream_parts).strip() != str(row.get('transcription') or '').strip()
            or response_text.strip() != str(row.get('transcription') or '').strip()
            or terminal.get('done') is not True
            or terminal.get('done_reason') != row.get('done_reason')
            or response.get('done') is not True
            or response.get('done_reason') != row.get('done_reason')):
        raise ValueError('scored_transcription_does_not_match_retained_responses')


def markdown(report: dict) -> str:
    false_reject_summary = (f"{report['false_reject_count_where_determinable']}/{report['false_reject_denominator_unique_approved_masterlist_matches']}"
                            if report['false_reject_count_where_determinable'] is not None else
                            f"N/A (0 eligible unique ground-truth/masterlist matches; denominator 0)")
    wrong_disc_summary = (f"{report['wrong_disc_false_accept_count_where_determinable']}/{report['wrong_disc_false_accept_denominator_unique_ground_truth_disc_matches']}"
                          if report['wrong_disc_false_accept_denominator_unique_ground_truth_disc_matches'] else
                          'N/A (0 eligible unique ground-truth/masterlist matches; denominator 0)')
    return '\n'.join([
        '# Agreement-only rip-or-eject benchmark', '',
        f"Dataset: {report['dataset_images']} images / {report['physical_disc_count']} physical discs.", '',
        f"- Would auto-rip: **{report['would_auto_rip']}** ({report['would_auto_rip_rate']:.1%})",
        f"- Would reject / request eject / manual review: **{report['would_reject_request_eject']}** ({report['would_reject_rate']:.1%})",
        f"- False accepts among simulated auto-rips: **{report['false_accept_count']}**; rate: **{report['false_accept_rate_among_simulated_auto_rips']:.1%}" if report['false_accept_rate_among_simulated_auto_rips'] is not None else '- False accepts: **0 observed**; rate is **not measurable (zero simulated automatic accepts)**',
        f"- False rejects where determinable: **{false_reject_summary}**.",
        f"- Wrong-disc false accepts where determinable: **{wrong_disc_summary}**; oracle is limited to the independent printed priority fields and does not prove edition or source-title identity.", '',
        f"- Approved masterlists loaded: **{report['approved_masterlist_count']}**; images the policy would reject: **{report['would_reject_request_eject']}/{report['dataset_images']}**.",
        f"- Recognition image outcomes: {json.dumps(report['recognition_agreement_image_counts'], sort_keys=True)}",
        f"- Priority-field outcomes: {json.dumps(report['priority_field_agreement_counts'], sort_keys=True)} / {report['priority_field_agreement_denominator']} field/capture pairs.",
        f"- Operational failures among {report['validated_model_image_output_denominator']} model-image runs: {json.dumps(report['operational_failure_kind_counts'], sort_keys=True)}.", '',
        'Reject-reason counts are non-exclusive: ' + json.dumps(report['reject_reason_case_counts_nonexclusive'], sort_keys=True), '',
        '## Results by camera', '', '| Camera | Images | Auto-rip | Reject/eject | False accept | False reject |',
        '|---|---:|---:|---:|---:|---:|',
        *[f"| {camera} | {row['images']} | {row['would_auto_rip']} | {row['would_reject_request_eject']} | {row['false_accepts']} | {row['false_rejects']} |"
          for camera, row in report['by_camera'].items()], '',
        f"## Physical-disc rollup ({report['physical_disc_count']} discs)", '',
        '| Disc key | Captures | Recognition by camera | Would authorize auto-rip in any camera | False accept in any camera |',
        '|---|---:|---|---|---|',
        *[f"| {disc} | {row['image_count']} | {json.dumps(row['camera_recognition_agreement'], sort_keys=True)} | {row['any_camera_would_auto_rip']} | {row['false_accept_on_any_image']} |"
          for disc,row in report['by_physical_disc'].items()], '',
        '## Qualification', '', report['interpretation'],
        report['decision_method'],
        '“Would request eject” is a simulated policy outcome; no ARM cancellation or physical ejection occurred. Model agreement can still be jointly wrong.', '',
    ])


def main() -> None:
    direct_bytes = (STATE / DIRECT).read_bytes()
    truth_bytes = (STATE / TRUTH).read_bytes()
    direct, truth = json.loads(direct_bytes), json.loads(truth_bytes)
    truth_policy = str(truth.get('policy', '')).casefold()
    manifest_name = truth.get('source_manifest')
    manifest_path = STATE / str(manifest_name or '')
    if ('no model output was used to set truth' not in truth_policy or not manifest_path.is_file()
            or __import__('hashlib').sha256(manifest_path.read_bytes()).hexdigest() != truth.get('source_manifest_sha256')):
        raise SystemExit('Independent ground-truth provenance or source-manifest digest validation failed')
    if direct.get('disc_notes_sent_to_models') is not False:
        raise SystemExit('Source direct report does not confirm evaluator-only notes')
    if direct.get('prompt') != PROMPT or direct.get('settings', {}).get('temperature') != 0:
        raise SystemExit('Retained outputs do not match the established prompt/settings')
    if '1024px' not in direct.get('image_preprocessing', '') or len(truth.get('priority_images', [])) != 8:
        raise SystemExit('Expected the exact two requested models and eight retained priority-ground-truth images')
    model_map = direct.get('models', {})
    expected = {PRIMARY: MODEL_B, SECONDARY: MODEL_A}
    for tag, source_tag in expected.items():
        if tag != source_tag or model_map.get(tag, {}).get('resolved_local_tag') != tag:
            raise SystemExit(f'Exact model tag/digest metadata is not present for {tag}')
    run_map = index_model_image_runs(direct['runs'])
    evidence = {}
    for image in truth['priority_images']:
        event = image['event_id']
        pair = [run_map.get((tag, event)) for tag in (PRIMARY, SECONDARY)]
        available = [row for row in pair if row is not None]
        if any(not row.get('inference_image_sha256') for row in available):
            raise SystemExit(f'Retained model/image row lacks an inference image digest for {event}')
        if len(available) == 2 and pair[0]['inference_image_sha256'] != pair[1]['inference_image_sha256']:
            raise SystemExit(f'Model image bytes differ for {event}')
        if any(row.get('inference_dimensions', '').split('x', 1)[0] != '1024' for row in available):
            raise SystemExit(f'Expected retained 1024px inference copy for {event}')
        for tag, row in zip((PRIMARY, SECONDARY), pair):
            key = f'{tag}/{event}'
            if row is None:
                evidence[key] = {'validation': 'missing_model_image_run'}
                continue
            try:
                validated = validate_request(STATE, row, model_map[tag])
                request_path = STATE / validated['request_file']
                request = json.loads(request_path.read_bytes())
                messages = request.get('messages', [])
                if (len(messages) != 1 or messages[0].get('role') != 'user'
                        or set(messages[0]) != {'role', 'content', 'images'}
                        or messages[0].get('content') != PROMPT or len(messages[0].get('images', [])) != 1):
                    raise ValueError('retained_request_contains_unexpected_context')
                validate_scored_transcription(STATE, row, validated)
                validated['validation'] = 'request_image_and_scored_transcription_verified'
                evidence[key] = validated
            except (SystemExit, OSError, ValueError, json.JSONDecodeError) as exc:
                # The run remains in the report as an unusable response; never score
                # unverified text as a recognition success or authorize a simulated rip.
                row['_retained_evidence_valid'] = False
                message = str(exc).casefold()
                if isinstance(exc, json.JSONDecodeError):
                    failure_kind = 'malformed_response'
                elif isinstance(exc, FileNotFoundError) or 'missing retained evidence file' in message:
                    failure_kind = 'missing_response'
                else:
                    failure_kind = 'unusable_response'
                row['_retained_evidence_error'] = failure_kind
                row['error'] = row.get('error') or f'retained_evidence_{failure_kind}'
                evidence[key] = {'validation': 'failed_retained_evidence_check'}
    build_report._evidence_index = evidence
    approved_masters = load_masters(STATE)
    direct_for_scoring = dict(direct)
    direct_for_scoring['runs'] = [
        run_map.get((row.get('model'), row.get('event_id')), row) for row in direct['runs']
    ]
    report = build_report(direct_for_scoring, truth, approved_masters)
    report['source_direct_sha256'] = __import__('hashlib').sha256(direct_bytes).hexdigest()
    report['ground_truth_sha256'] = __import__('hashlib').sha256(truth_bytes).hexdigest()
    report['ground_truth_source_manifest_sha256'] = truth.get('source_manifest_sha256')
    report['models'] = {tag: {'resolved_tag': model_map[tag].get('resolved_local_tag'),
                              'digest': model_map[tag].get('digest'),
                              'quantization': model_map[tag].get('quantization'),
                              'size_bytes': model_map[tag].get('size_bytes')}
                        for tag in (PRIMARY, SECONDARY)}
    report['answers'] = {
        'would_auto_rip': report['would_auto_rip'], 'would_reject_request_eject': report['would_reject_request_eject'],
        'zero_false_accepts_observed_among_simulated_auto_rips': (report['false_accept_count'] == 0 if report['would_auto_rip'] else None),
        'unnecessarily_rejected_correct_cases_where_determinable': report['false_reject_count_where_determinable'],
        'images_where_both_models_agreed_correctly_on_all_ground_truth_fields': sum(row['recognition_agreement'] == 'BOTH_AGREE_CORRECTLY' for row in report['cases']),
        'physical_discs_with_at_least_one_correct_camera_capture': sum(any(value == 'BOTH_AGREE_CORRECTLY' for value in row['camera_recognition_agreement'].values()) for row in report['by_physical_disc'].values()),
        'physical_discs_with_both_camera_captures_correct': sum(all(value == 'BOTH_AGREE_CORRECTLY' for value in row['camera_recognition_agreement'].values()) for row in report['by_physical_disc'].values()),
        'qualification_note': ('No approved masterlists were loaded; policy decisions reject all captures, so this run cannot measure false-accept safety or false-reject rate.' if not report['approved_masterlist_count'] else 'Four physical discs/eight images are insufficient to establish production safety.'),
    }
    validated_count = sum(value.get('validation') == 'request_image_and_scored_transcription_verified'
                          for value in evidence.values())
    report['run_reuse'] = {
        'inference_calls': 0, 'model_image_run_denominator': len(truth['priority_images']) * 2,
        'validated_model_image_outputs': validated_count,
        'all_requests_images_and_scored_transcriptions_validated_exactly': validated_count == len(truth['priority_images']) * 2,
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    versions = [int(match.group(1)) for path in REPORT_DIR.glob('diagnostic-disc-agreement-gate-benchmark-v*.json')
                if (match := re.fullmatch(r'diagnostic-disc-agreement-gate-benchmark-v(\d+)\.json', path.name))]
    version = max(versions, default=0) + 1
    json_path = REPORT_DIR / f'diagnostic-disc-agreement-gate-benchmark-v{version}.json'
    md_path = REPORT_DIR / f'diagnostic-disc-agreement-gate-benchmark-v{version}.md'
    # Exclusive creation protects every prior report revision.
    with json_path.open('xb') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2).encode() + b'\n')
    try:
        with md_path.open('xb') as stream:
            stream.write(markdown(report).encode())
    except FileExistsError:
        json_path.unlink()
        raise SystemExit('Report revision collision; no report was overwritten')
    print(f'Wrote {json_path} and {md_path}')


if __name__ == '__main__':
    main()
