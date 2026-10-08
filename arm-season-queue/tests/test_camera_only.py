import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.controller import Controller
from app.recognition import extract, read_words


class CameraOnlyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.controller = Controller(Settings(state=Path(self.temp.name), arm_url=''))
        self.addCleanup(self.controller.validation.shutdown, wait=True)

    def result(self):
        return {'accepted': False, 'reason': 'Identity incomplete',
                'frames': [extract('Example printed title', .95)]}

    def event(self, event):
        return self.controller.db.rows('SELECT * FROM events WHERE id=?', (event,))[0]

    def test_camera_only_poll_preserves_pending_and_completed_ocr(self):
        c = self.controller
        event = c.begin_event('camera')
        for _ in range(3):
            c.poll_once()
        self.assertEqual(self.event(event)['status'], 'processing')
        c.recognition_done(event, self.result())
        c.poll_once()
        row = self.event(event)
        self.assertEqual(row['status'], 'rejected_for_review')
        self.assertEqual(json.loads(row['body'])['result'], self.result())
        self.assertEqual(len(c.db.rejections('pending')), 1)
        self.assertIn('Camera-only', c.status)
        self.assertFalse(c.initialized)

    def test_late_ocr_survives_invalidation_but_cannot_pair(self):
        c = self.controller
        event = c.begin_event('camera')
        c.release_event(event)
        c.db.invalidate_pending()
        c.recognition_done(event, self.result())
        c.db.pair_insertion(42, 'new-insertion', 180)
        row = self.event(event)
        self.assertEqual(row['status'], 'invalidated')
        self.assertIsNone(row['job'])
        body = json.loads(row['body'])
        self.assertEqual(body['result'], self.result())
        self.assertEqual(body['matches'], [])

    def test_late_result_does_not_revive_expired_capture(self):
        c = self.controller
        event = c.begin_event('camera')
        c.db.execute("UPDATE events SET status='expired' WHERE id=?", (event,))
        c.recognition_done(event, self.result())
        self.assertEqual(self.event(event)['status'], 'expired')

    def test_uncertain_words_are_visible_but_not_matching_input(self):
        data = {'text': ['', 'Example', 'Season', '2'], 'conf': [-1, 95, 45, 30],
                'block_num': [0, 1, 1, 1], 'par_num': [0, 1, 1, 1],
                'line_num': [0, 1, 1, 1]}
        trusted, observed, scores = read_words(data)
        self.assertEqual(observed, 'Example Season 2')
        self.assertEqual(trusted, 'Example')
        self.assertIsNone(extract(trusted)['season'])
        self.assertEqual(scores, [95])
