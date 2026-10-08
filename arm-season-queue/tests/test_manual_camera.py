import json
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import cv2
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

    def test_diagnostic_snapshot_saves_original_without_running_recognition(self):
        object.__setattr__(self.camera.s, 'recognition_backend', 'ollama')
        self.camera.calibrate()
        self.camera.frame[:] = 123
        with patch.object(self.camera.pool, 'submit') as submit:
            response = self.post('snapshot')
        self.assertEqual(response.status_code, 200)
        submit.assert_not_called()
        event = response.json()['event']
        row = self.controller.db.rows('SELECT body,status,job FROM events WHERE id=?', (event,))[0]
        self.assertEqual(row['status'], 'processing')
        self.assertIsNone(row['job'])
        self.assertEqual(json.loads(row['body'])['source'], 'vision_test')
        evidence = Path(self.app.state.controller.s.state)/'evidence'/event
        self.assertTrue((evidence/'0.jpg').is_file())
        self.assertTrue((evidence/'original.jpg').is_file())
        self.assertEqual(self.client.get(f'/api/evidence/{event}/original.jpg', auth=('operator','test-only')).status_code, 200)
        (evidence/'qwen-input.jpg').write_bytes(b'private inference image')
        self.assertEqual(self.client.get(f'/api/evidence/{event}/qwen-input.jpg', auth=('operator','test-only')).content,
                         b'private inference image')
        self.controller.db.execute("UPDATE events SET status='invalidated' WHERE id=?", (event,))
        with patch.object(self.camera, 'transcribe_snapshot', return_value={'text':'printed text','diagnostics':{'kind':'success'}}) as transcribe:
            response = self.post(f'transcribe/{event}')
        self.assertEqual(response.status_code, 200)
        transcribe.assert_called_once_with(event)

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

    def test_camera_rotation_orients_processing_frame_but_preserves_raw_original(self):
        settings = Settings(state=Path(self.app.state.controller.s.state), arm_url='',
                            camera_mode='manual', camera_rotation=180, roi='0,0,1,1')
        camera = Camera(settings, None, None, None)
        self.addCleanup(camera.pool.shutdown, wait=True)
        raw = np.arange(6*8*3, dtype=np.uint8).reshape((6,8,3))

        class Capture:
            def set(self, *args): pass
            def read(self): return True, raw.copy()
            def release(self): pass

        with patch('app.camera.cv2.VideoCapture', return_value=Capture()), \
             patch.object(camera.stop, 'wait', side_effect=lambda _: camera.stop.set()):
            camera.run()

        np.testing.assert_array_equal(camera.full_frame, raw)
        np.testing.assert_array_equal(camera.frame, cv2.rotate(raw, cv2.ROTATE_180))
        self.assertEqual(camera.snapshot()['rotation_degrees'], 180)

    def test_failed_recapture_does_not_invalidate_existing_evidence(self):
        event = self.controller.begin_event('manual_test')
        self.assertEqual(self.post('recapture').status_code, 400)
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'processing')

    def test_capture_endpoints_require_authentication_and_action_header(self):
        for endpoint in ('capture', 'snapshot', 'mode', 'calibrate'):
            self.assertEqual(self.client.post('/api/camera/'+endpoint, json={}).status_code, 401)
            self.assertEqual(self.client.post('/api/camera/'+endpoint, json={}, auth=('operator','test-only')).status_code, 403)

    def test_mode_defaults_follow_arm_configuration(self):
        self.assertEqual(self.camera.mode, 'manual')
        for configured, expected in (('', 'auto'), ('manual', 'manual')):
            camera = Camera(Settings(arm_url='http://arm:8080', camera_mode=configured), None, None, None)
            self.addCleanup(camera.pool.shutdown, wait=True)
            self.assertEqual(camera.mode, expected)
