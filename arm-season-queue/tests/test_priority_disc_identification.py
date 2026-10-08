from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
from score_priority_disc_identification import classify, usable_run  # noqa: E402
from benchmark_chain_order import outcome, stage_complete  # noqa: E402
import rerun_failed_llama_vision as rerun  # noqa: E402
from benchmark_agreement_gate import (  # noqa: E402
    build_report as agreement_report, index_model_image_runs, validate_scored_transcription,
)


class PriorityDiscIdentificationTests(unittest.TestCase):
    def classify(self, category: str, truth: str, text: str, **field_options: object) -> str:
        field = {"category": category, "ground_truth_value": truth, **field_options}
        run = {"transcription": text, "http_status": 200, "done_reason": "stop"}
        return classify(field, text, run, [
            "STAR TREK VOYAGER",
            "STAR TREK THE ORIGINAL SERIES",
            "STAR TREK DEEP SPACE NINE",
        ])[0]

    def test_series_case_and_punctuation_are_normalized(self) -> None:
        self.assertEqual(
            self.classify("SERIES", "STAR TREK VOYAGER", "Star Trek—Voyager"),
            "CORRECT",
        )

    def test_only_explicit_series_aliases_are_accepted(self) -> None:
        self.assertEqual(
            self.classify("SERIES", "STAR TREK DEEP SPACE NINE", "DS9",),
            "CORRECT",
        )
        self.assertEqual(
            self.classify("SERIES", "STAR TREK VOYAGER", "Star Trek Deep Space Nine"),
            "WRONG_BUT_PLAUSIBLE",
        )

    def test_season_equivalences_and_wrong_season(self) -> None:
        self.assertEqual(self.classify("SEASON", "2", "2. Staffel"), "CORRECT")
        self.assertEqual(self.classify("SEASON", "2", "Season 1"), "WRONG_BUT_PLAUSIBLE")
        self.assertEqual(self.classify("SEASON", "2", "Season"), "OMITTED")

    def test_episode_range_formatting_and_substantive_errors(self) -> None:
        self.assertEqual(self.classify("EPISODES", "1-4", "Episodes 1 - 4"), "CORRECT")
        self.assertEqual(self.classify("EPISODES", "1-4", "Episodes 1-3"), "WRONG_BUT_PLAUSIBLE")
        self.assertEqual(self.classify("EPISODES", "1-4", "Episodes"), "OMITTED")

    def test_title_case_punctuation_and_wrong_title(self) -> None:
        self.assertEqual(self.classify("TITLES", "Mirror Mirror", "MIRROR—MIRROR"), "CORRECT")
        self.assertEqual(self.classify("TITLES", "Mirror Mirror", "Episode 4: Mirror Mirror"), "CORRECT")
        self.assertEqual(self.classify("TITLES", "Mirror Mirror", "Mirror Mirror Reversed"), "WRONG_BUT_PLAUSIBLE")

    def test_failed_or_truncated_generation_is_detectably_failed(self) -> None:
        field = {"category": "SERIES", "ground_truth_value": "STAR TREK VOYAGER"}
        self.assertEqual(classify(field, "", {"error": "runtime error"}, [])[0], "DETECTABLY_FAILED")
        self.assertEqual(classify(field, "STAR TREK VOYAGER", {
            "transcription": "STAR TREK VOYAGER", "done_reason": "length",
        }, [])[0], "DETECTABLY_FAILED")

    def test_runtime_failures_are_not_usable_for_recognition_precision(self) -> None:
        self.assertFalse(usable_run({"http_status": 500, "error": "load failed", "transcription": ""}))
        self.assertFalse(usable_run({"http_status": 200, "done_reason": "length", "transcription": "text"}))
        self.assertFalse(usable_run({"http_status": 200, "done_reason": "stop", "transcription": ""}))
        self.assertTrue(usable_run({"http_status": 200, "done_reason": "stop", "transcription": "visible text"}))

    def test_chain_outcomes_and_fallback_gate_are_priority_only(self) -> None:
        correct = [{"classification": "CORRECT"}, {"classification": "CORRECT"}]
        partial = [{"classification": "CORRECT"}, {"classification": "OMITTED"}]
        wrong = [{"classification": "CORRECT"}, {"classification": "WRONG_BUT_PLAUSIBLE"}]
        self.assertTrue(stage_complete(correct, []))
        self.assertFalse(stage_complete(partial, []))
        self.assertFalse(stage_complete(correct, ["conflicting_labeled_season_values"]))
        self.assertEqual(outcome(correct, True), "PASS")
        self.assertEqual(outcome(partial, True), "PARTIAL")
        self.assertEqual(outcome(wrong, True), "FAIL")
        self.assertEqual(outcome(wrong, False), "OPERATIONAL_FAILURE")

    def test_agreement_gate_rejects_disagreement_and_counts_false_accepts(self) -> None:
        from benchmark_agreement_gate import PRIMARY as primary, SECONDARY as secondary
        image = {"event_id":"e1", "disc_key":"disc1", "camera":"camera-a", "priority_fields":[
            {"field_id":"series", "category":"SERIES", "ground_truth_value":"STAR TREK VOYAGER"},
            {"field_id":"season", "category":"SEASON", "ground_truth_value":"2"},
            {"field_id":"episodes", "category":"EPISODES", "ground_truth_value":"1-4"},
            {"field_id":"title", "category":"TITLES", "ground_truth_value":"Mirror Mirror"},
        ]}
        direct = {"requested_models":[primary, secondary], "runs":[]}
        for model, transcription in ((primary, 'Star Trek Voyager Season 2 Episodes 1-4\nMirror Mirror'),
                                     (secondary, 'Star Trek Voyager Season 2 Episodes 1-3\nMirror Mirror')):
            direct['runs'].append({"model":model,"event_id":"e1","http_status":200,
                                   "done_reason":"stop","transcription":transcription})
        report = agreement_report(direct, {"priority_images":[image]})
        self.assertEqual(report['would_auto_rip'], 0)
        self.assertEqual(report['would_reject_request_eject'], 1)
        self.assertEqual(report['reject_reason_case_counts_nonexclusive']['model_disagreement'], 1)
        self.assertEqual(report['false_accept_count'], 0)

    def test_operational_failure_cannot_auto_rip_even_when_text_is_correct(self) -> None:
        from benchmark_agreement_gate import PRIMARY as primary, SECONDARY as secondary
        image = {"event_id":"e1", "disc_key":"disc1", "camera":"camera-a", "priority_fields":[
            {"field_id":"series", "category":"SERIES", "ground_truth_value":"STAR TREK VOYAGER"}]}
        direct = {"requested_models":[primary, secondary], "runs":[
            {"model":primary,"event_id":"e1","http_status":200,"done_reason":"stop","transcription":"Star Trek Voyager"},
            {"model":secondary,"event_id":"e1","http_status":200,"done_reason":"length","transcription":"Star Trek Voyager"}]}
        report = agreement_report(direct, {"priority_images":[image]})
        self.assertEqual(report['would_auto_rip'], 0)
        self.assertEqual(report['reject_reason_case_counts_nonexclusive']['operational_failure'], 1)

    def test_duplicate_retained_model_image_rows_fail_instead_of_collapsing(self) -> None:
        from benchmark_agreement_gate import PRIMARY as primary
        with self.assertRaisesRegex(SystemExit, 'Duplicate retained model/image run'):
            index_model_image_runs([{'model':primary,'event_id':'same'}, {'model':primary,'event_id':'same'}])

    def test_scored_transcription_must_match_both_retained_response_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            stream=root/'stream.jsonl'
            response=root/'response.json'
            stream_bytes=b'{"message":{"content":"verified text"},"done":true,"done_reason":"stop"}\n'
            stream.write_bytes(stream_bytes)
            response.write_text(json.dumps({'message':{'content':'verified text'},'done':True,'done_reason':'stop'}))
            validated={'raw_stream_file':'stream.jsonl','raw_response_file':'response.json',
                       'raw_stream_sha256':hashlib.sha256(stream_bytes).hexdigest()}
            validate_scored_transcription(root, {'transcription':'verified text',
                'raw_stream_sha256':validated['raw_stream_sha256'],'done_reason':'stop'}, validated)
            with self.assertRaisesRegex(ValueError, 'does_not_match_retained_responses'):
                validate_scored_transcription(root, {'transcription':'changed text',
                    'raw_stream_sha256':validated['raw_stream_sha256'],'done_reason':'stop'}, validated)
            with self.assertRaisesRegex(ValueError, 'does_not_match_retained_responses'):
                validate_scored_transcription(root, {'transcription':'verified text',
                    'raw_stream_sha256':validated['raw_stream_sha256'],'done_reason':'length'}, validated)

    def test_unverified_retained_response_cannot_be_simulated_as_usable_agreement(self) -> None:
        from benchmark_agreement_gate import PRIMARY as primary, SECONDARY as secondary, classify_policy
        from test_core import master
        approved=master()
        titles='\n'.join(episode.title for episode in approved.discs[0].episodes)
        text=f'{approved.series}\nSeason 2\nDisc 1\nTeil 1\nEpisodes 1-4\n{titles}'
        image={'event_id':'e1','disc_key':'test-disc','camera':'camera-a','priority_fields':[
            {'field_id':'series','category':'SERIES','ground_truth_value':approved.series},
            {'field_id':'season','category':'SEASON','ground_truth_value':'2'},
            {'field_id':'episodes','category':'EPISODES','ground_truth_value':'1-4'},
            *[{'field_id':f'title-{i}','category':'TITLES','ground_truth_value':ep.title}
              for i,ep in enumerate(approved.discs[0].episodes)],
        ]}
        runs=[{'model':primary,'event_id':'e1','http_status':200,'done_reason':'stop','transcription':text},
              {'model':secondary,'event_id':'e1','http_status':200,'done_reason':'stop','transcription':text,
               '_retained_evidence_valid':False}]
        result=classify_policy({'runs':runs},{'priority_images':[image]},[approved])[0]
        self.assertEqual(result['decision'],'WOULD_REJECT_REQUEST_EJECT_MANUAL_REVIEW')
        self.assertIn(secondary,result['operational_failures'])

    def test_camera_false_reject_is_unavailable_when_its_truth_match_denominator_is_zero(self) -> None:
        from benchmark_agreement_gate import PRIMARY as primary, SECONDARY as secondary
        from test_core import master
        image={'event_id':'e1','disc_key':'unrelated-disc','camera':'camera-a','priority_fields':[
            {'field_id':'series','category':'SERIES','ground_truth_value':'STAR TREK VOYAGER'},
            {'field_id':'season','category':'SEASON','ground_truth_value':'2'},
            {'field_id':'episodes','category':'EPISODES','ground_truth_value':'1-4'},
        ]}
        runs=[{'model':tag,'event_id':'e1','http_status':200,'done_reason':'stop',
               'transcription':'Star Trek Voyager Season 2 Episodes 1-4'} for tag in (primary,secondary)]
        report=agreement_report({'runs':runs},{'priority_images':[image]},[master()])
        self.assertEqual(report['false_reject_denominator_unique_approved_masterlist_matches'],0)
        self.assertIsNone(report['false_reject_count_where_determinable'])
        self.assertEqual(report['by_camera']['camera-a']['false_reject_denominator_unique_ground_truth_disc_matches'],0)
        self.assertIsNone(report['by_camera']['camera-a']['false_rejects'])

    def test_correction_report_revisions_are_exclusive_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original_state = rerun.STATE
            rerun.STATE = Path(directory)
            try:
                first = {"status": "smoke_pending"}
                first_path = rerun.save_revision(first)
                first_bytes = first_path.read_bytes()
                second = {"status": "smoke_failed"}
                second_path = rerun.save_revision(second)
                self.assertEqual(first_path.name, "diagnostic-disc-llama32-vision-correction-v1.json")
                self.assertEqual(second_path.name, "diagnostic-disc-llama32-vision-correction-v2.json")
                self.assertEqual(first_path.read_bytes(), first_bytes)
                self.assertEqual(rerun.report_versions(), [(1, first_path), (2, second_path)])
            finally:
                rerun.STATE = original_state


if __name__ == "__main__":
    unittest.main()
