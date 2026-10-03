import json
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

from app.camera import Camera
from app.config import Settings
from app.main import create_app
from app.recognition import extract
from test_core import master


class ManualCameraTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = create_app(Settings(state=Path(temp.name), arm_url='', password='test-only'), start_workers=False)
        self.camera = self.app.state.camera
        self.controller = self.app.state.controller
        self.addCleanup(self.camera.pool.shutdown, wait=True)
        self.addCleanup(self.controller.validation.shutdown, wait=True)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.camera.frame = np.zeros((80, 100, 3), dtype=np.uint8)
        self.camera.frame_at = time.monotonic()
        self.camera.status['connected'] = True

    def post(self, path, body=None):
        return self.client.post('/api/camera/'+path, json=body or {},
                                auth=('operator', 'test-only'), headers={'X-Queue-Request':'1'})

    def test_empty_confirmation_never_starts_ocr_and_resets_stuck_detector(self):
        self.camera.mode = 'auto'
        old = self.controller.begin_event('camera')
        self.camera.event = old
        self.camera.gate.latched = True
        with patch.object(self.camera.pool, 'submit') as submit:
            self.assertEqual(self.post('calibrate').status_code, 200)
            submit.assert_not_called()
        self.assertIsNone(self.camera.event)
        self.assertFalse(self.camera.gate.latched)
        self.assertEqual(self.controller.db.rows('SELECT status FROM events')[0]['status'], 'invalidated')

    def test_snapshot_is_frozen_and_repeatable_without_removal_or_restart(self):
        self.camera.calibrate()
        self.camera.frame[:] = 123
        calls = []
        def submit(fn, *args):
            future = Future()
            calls.append((fn, args, future))
            return future
        with patch.object(self.camera.pool, 'submit', side_effect=submit):
            first = self.post('capture')
            self.assertEqual(first.status_code, 200)
            self.camera.frame[:] = 0
            np.testing.assert_array_equal(calls[0][1][1][0], np.full((80,100,3),123,dtype=np.uint8))
            self.assertEqual(self.post('capture').status_code, 400)
            self.assertEqual(self.post('mode', {'mode':'auto'}).status_code, 400)
            calls[0][2].set_result(None)
            second = self.post('capture')
            self.assertEqual(second.status_code, 200)
            self.assertNotEqual(first.json()['event'], second.json()['event'])
            calls[1][2].set_result(None)
        self.assertTrue(all(row['released']==0 for row in self.controller.db.rows('SELECT released FROM events')))

    def test_stale_disconnected_and_uncalibrated_frames_rejected(self):
        self.assertEqual(self.post('capture').status_code, 400)
        self.camera.calibrate()
        self.camera.frame_at -= 3
        self.assertEqual(self.post('capture').status_code, 400)
        self.assertEqual(self.post('calibrate').status_code, 400)
        self.camera.frame_at = time.monotonic()
        self.camera.status['connected'] = False
        self.assertEqual(self.post('capture').status_code, 400)
        self.assertEqual(self.controller.db.rows('SELECT id FROM events'), [])

    def test_test_snapshot_never_pairs_or_authorizes_ripping(self):
        c = self.controller
        m = master()
        c.import_master(m)
        event = c.begin_event('manual_test')
        frame = extract('Star Trek Deep Space Nine\nStaffel 2 Disc 1 Teil 1', .99)
        with patch('app.camera.ocr', return_value=frame):
            self.camera.process(event, [self.camera.frame]*3, True)
        row = c.db.rows('SELECT * FROM events WHERE id=?', (event,))[0]
        result = json.loads(row['body'])['result']
        self.assertTrue(result['test_only'])
        self.assertFalse(result['accepted'])
        c.db.pair_insertion(42, 'insertion', 180)
        self.assertIsNone(c.db.rows('SELECT job FROM events WHERE id=?', (event,))[0]['job'])
        with self.assertRaisesRegex(ValueError, 'Manual test'):
            c.review_event(event, m.id, m.discs[0].id, 'Confirmed text')

    def test_mode_change_clears_baseline_and_invalidates_old_evidence(self):
        self.camera.calibrate()
        self.controller.begin_event('camera')
        self.assertEqual(self.post('mode', {'mode':'auto'}).status_code, 200)
        self.assertIsNone(self.camera.background)
        self.assertFalse(self.camera.status['calibrated'])
        self.assertEqual(self.controller.db.rows('SELECT status FROM events')[0]['status'], 'invalidated')
        self.assertEqual(self.post('mode', {'mode':'unknown'}).status_code, 400)

    def test_manual_preview_never_triggers_automatic_capture(self):
        self.camera.calibrate()
        class Capture:
            def set(self, *args): pass
            def read(self): return True, np.full((80,100,3), 255, dtype=np.uint8)
            def release(self): pass
        iterations = []
        def wait(seconds):
            iterations.append(seconds)
            if len(iterations) == 20:
                self.camera.stop.set()
        with patch('app.camera.cv2.VideoCapture', return_value=Capture()), patch.object(self.camera.stop, 'wait', side_effect=wait):
            self.camera.run()
        self.assertEqual(self.controller.db.rows('SELECT id FROM events'), [])

    def test_failed_recapture_does_not_invalidate_existing_evidence(self):
        event = self.controller.begin_event('manual_test')
        self.assertEqual(self.post('recapture').status_code, 400)
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'processing')

    def test_capture_endpoints_require_authentication_and_action_header(self):
        for endpoint in ('capture', 'mode', 'calibrate'):
            self.assertEqual(self.client.post('/api/camera/'+endpoint, json={}).status_code, 401)
            self.assertEqual(self.client.post('/api/camera/'+endpoint, json={}, auth=('operator','test-only')).status_code, 403)

    def test_mode_defaults_follow_arm_configuration(self):
        self.assertEqual(self.camera.mode, 'manual')
        for configured, expected in (('', 'auto'), ('manual', 'manual')):
            camera = Camera(Settings(arm_url='http://arm:8080', camera_mode=configured), None, None, None)
            self.addCleanup(camera.pool.shutdown, wait=True)
            self.assertEqual(camera.mode, expected)
