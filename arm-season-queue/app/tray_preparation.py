"""Persisted camera/tray preparation state for one configured optical drive."""

import json
import os
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np


class TrayPreparation:
    STAGES = {
        'tray_status_unavailable', 'tray_closed', 'empty_view_preparation_needed',
        'tray_open_waiting_for_disc_removal', 'calibrating_empty_tray',
        'early_disc_during_calibration', 'ready_for_disc', 'capturing',
        'recognising_scanning', 'ripping', 'awaiting_review',
    }

    def __init__(self, state_dir, device, *, settle_seconds=2.0, calibration_timeout=60,
                 calibration_samples=12, empty_difference_fraction=0.06):
        self.state_dir = Path(state_dir)
        self.device = device
        self.settle_seconds = float(settle_seconds)
        self.calibration_timeout = float(calibration_timeout)
        self.calibration_samples = int(calibration_samples)
        self.empty_difference_fraction = float(empty_difference_fraction)
        self.state_path = self.state_dir / 'tray-preparation.json'
        self.background_path = self.state_dir / 'tray-empty-reference.png'
        self.tray_state = 'unknown'
        self.stage = 'tray_status_unavailable'
        self.message = 'Waiting for a reliable tray-state reading'
        self.cycle_id = None
        self.cycle_started_at = None
        self.post_eject_job = None
        self.updated_at = time.time()
        self.background = None
        self.previous_frame = None
        self.calibration_started = None
        self.calibration_frames = []
        self.stable_empty_frames = 0
        self.event_id = None
        self.manual_empty_reference = None
        self.calibration_reference = None
        self.post_eject_empty_seen = False
        self._load()

    def _load(self):
        try:
            saved = json.loads(self.state_path.read_text('utf-8'))
            if saved.get('schema_version') != 1 or saved.get('device') != self.device:
                return
            stage = saved.get('stage')
            if stage not in self.STAGES:
                return
            if self.background_path.is_file():
                image = cv2.imread(str(self.background_path), cv2.IMREAD_GRAYSCALE)
                if image is not None and image.shape == (100, 160):
                    self.background = image.astype(np.float32)
            self.tray_state = saved.get('tray_state', 'unknown')
            self.stage = stage
            self.message = str(saved.get('message') or self.message)
            self.cycle_id = saved.get('cycle_id')
            self.cycle_started_at = saved.get('cycle_started_at')
            self.post_eject_job = saved.get('post_eject_job')
            self.post_eject_empty_seen = bool(saved.get('post_eject_empty_seen', False))
            self.event_id = saved.get('event_id')
            self.updated_at = float(saved.get('updated_at') or time.time())
        except (OSError, ValueError, TypeError):
            return

    def _save(self):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.updated_at = time.time()
        body = {
            'schema_version': 1, 'device': self.device, 'tray_state': self.tray_state,
            'stage': self.stage, 'message': self.message, 'cycle_id': self.cycle_id,
            'cycle_started_at': self.cycle_started_at, 'post_eject_job': self.post_eject_job,
            'post_eject_empty_seen': self.post_eject_empty_seen,
            'event_id':self.event_id,'updated_at': self.updated_at,
        }
        fd, name = tempfile.mkstemp(prefix='.tray-preparation-', dir=self.state_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(body, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.state_path)
            dfd = os.open(self.state_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def scene_matches(frame, background, threshold=25, fraction=0.06):
        if background is None or frame is None or frame.shape != background.shape:
            return False
        return float(np.mean(np.abs(frame.astype(np.float32) - background) > threshold)) <= fraction

    def tray_observation(self, state, *, transition=None, initial=False, now=None):
        now = time.monotonic() if now is None else now
        if state not in ('open', 'closed', 'unknown'):
            state = 'unknown'
        previous_state = self.tray_state
        self.tray_state = state
        if state == 'unknown':
            self.stage = 'tray_status_unavailable'
            self.message = 'Tray status unavailable; automatic capture and tray closure are suspended'
            self._save()
            return

        if state == 'closed' and self.stage == 'tray_status_unavailable':
            # The debounced monitor has recovered a reliable closed state after
            # an ioctl outage; do not leave a stale unavailable stage behind.
            self.stage = 'tray_closed'
            self.message = 'Tray closed; reliable tray-state monitoring restored'
            self._save()
            return

        if initial:
            if state == 'closed':
                if self.post_eject_empty_seen:
                    self.post_eject_job = None
                    self.post_eject_empty_seen = False
                self.stage = 'tray_closed'
                self.message = 'Tray closed'
            elif (previous_state == 'open' and state == 'open'
                  and self.stage in ('ready_for_disc', 'tray_open_waiting_for_disc_removal',
                                     'calibrating_empty_tray', 'early_disc_during_calibration')
                  and self.cycle_id):
                # The persisted open cycle is reliable; resume it, but never
                # invent a new opening event on service startup.
                self.message = 'Resuming the persisted open-tray preparation cycle'
                if self.stage == 'calibrating_empty_tray':
                    if self.background is None:
                        self.stage = 'empty_view_preparation_needed'
                        self.message = ('Service restarted before an empty baseline was saved; '
                                        'close and reopen the tray empty to start a fresh calibration')
                    else:
                        self.calibration_started = now
                        self.calibration_frames = []
                        self.previous_frame = None
                        self.calibration_reference = self.background.copy()
            else:
                self.stage = 'empty_view_preparation_needed'
                self.message = ('Tray was already open at service startup; no new empty-opening '
                                'event was observed. Close and reopen it empty, or use Reset empty view.')
            self._save()
            return

        if transition == 'open':
            self.cycle_id = str(uuid4())
            self.cycle_started_at = time.time()
            self.event_id = None
            self.manual_empty_reference = None
            self.calibration_reference = None
            self.calibration_frames = []
            self.previous_frame = None
            self.stable_empty_frames = 0
            self.calibration_started = None
            if self.background is not None:
                if self.post_eject_job is not None:
                    self.stage = 'tray_open_waiting_for_disc_removal'
                    self.message = ('Completed disc may still be in the open tray; remove it. '
                                    'The next manual empty-tray opening will prepare the baseline')
                else:
                    self._start_calibration(now,
                        'Tray opened; validating the saved empty view before refreshing calibration')
                    self.calibration_reference = self.background.copy()
            else:
                self.stage = 'calibrating_empty_tray'
                self.calibration_started = now
                self.message = 'Tray opened; settling and checking the empty view before calibration'
        elif transition == 'closed':
            self.previous_frame = None
            self.stable_empty_frames = 0
            if self.post_eject_empty_seen:
                self.post_eject_job = None
                self.post_eject_empty_seen = False
            if self.stage not in ('capturing', 'recognising_scanning', 'ripping', 'awaiting_review'):
                self.stage = 'tray_closed'
                self.message = 'Tray closed'
        self._save()

    def note_arm_ejected(self, job):
        self.post_eject_job = int(job)
        self.post_eject_empty_seen = False
        if self.tray_state == 'open' and self.background is not None:
            self.stage = 'tray_open_waiting_for_disc_removal'
            self.message = 'ARM ejected the completed disc; remove it before preparing the next disc'
        self._save()

    def _start_calibration(self, now, message='Calibrating empty tray from stable frames'):
        self.stage = 'calibrating_empty_tray'
        self.message = message
        self.calibration_started = now
        self.calibration_frames = []
        self.calibration_reference = (self.previous_frame.copy()
                                      if self.previous_frame is not None else None)
        self.previous_frame = None
        self._save()

    def observe_frame(self, frame, *, stable, now=None):
        now = time.monotonic() if now is None else now
        if self.tray_state != 'open':
            return self.snapshot()
        if self.stage == 'tray_open_waiting_for_disc_removal':
            matches = self.scene_matches(frame, self.background,
                                         fraction=self.empty_difference_fraction)
            frame_stable = (self.previous_frame is not None and
                            float(np.mean(np.abs(frame.astype(np.float32) - self.previous_frame))) < 4)
            self.stable_empty_frames = self.stable_empty_frames + 1 if matches and frame_stable and stable else 0
            self.previous_frame = frame.copy()
            if self.stable_empty_frames >= 8:
                if self.post_eject_job is not None:
                    if not self.post_eject_empty_seen:
                        self.post_eject_empty_seen = True
                        self.message = ('Completed disc removal confirmed; close the tray manually. '
                                        'The next empty-tray opening will prepare the baseline')
                        self._save()
                else:
                    self._start_calibration(now, 'Empty scene confirmed; refreshing the baseline once for this opening')
            elif not self.post_eject_empty_seen:
                self.message = ('Completed disc is still visible or the scene differs from the saved empty view; '
                                'remove the disc and wait' if self.post_eject_job is not None else
                                'Tray open; waiting for a stable match to the saved empty view')
            return self.snapshot()

        if self.stage == 'calibrating_empty_tray':
            if self.manual_empty_reference is not None:
                confirmed_scene_unchanged=self.scene_matches(
                    frame,self.manual_empty_reference,threshold=35,fraction=0.20)
                if not confirmed_scene_unchanged:
                    self.stage = 'early_disc_during_calibration'
                    self.message = ('Scene changed after the operator confirmed the empty view; '
                                    'remove the object, then use Reset empty view again')
                    self.calibration_frames = []
                    self.manual_empty_reference = None
                    self._save()
                    return self.snapshot()
            elif self.calibration_reference is None:
                # Fixed molded tray rings are not evidence of a disc. Anchor the
                # first open-tray frame and detect scene changes during calibration.
                self.calibration_reference = frame.copy()
            elif not self.scene_matches(frame,self.calibration_reference,
                                        threshold=35,fraction=0.12):
                self.stage = 'early_disc_during_calibration'
                self.message = ('The tray scene changed before calibration completed; '
                                'remove the disc and reopen the empty tray')
                self.calibration_frames = []
                self._save()
                return self.snapshot()
            if self.calibration_started is None:
                self.calibration_started = now
            if now-self.calibration_started > self.calibration_timeout:
                self.stage='empty_view_preparation_needed'
                self.message='Empty-tray calibration timed out; clear the scene and use Reset empty view'
                self.calibration_frames=[]
                self._save()
                return self.snapshot()
            frame_stable = (self.previous_frame is not None and
                            float(np.mean(np.abs(frame.astype(np.float32) - self.previous_frame))) < 4)
            self.previous_frame = frame.copy()
            if now - self.calibration_started < self.settle_seconds or not stable or not frame_stable:
                self.calibration_frames = []
                return self.snapshot()
            self.calibration_frames.append(frame.astype(np.uint8).copy())
            if len(self.calibration_frames) >= self.calibration_samples:
                background = np.median(np.stack(self.calibration_frames[-self.calibration_samples:]), axis=0).astype(np.uint8)
                self._save_background(background)
                self.background = background.astype(np.float32)
                self.stage = 'ready_for_disc'
                self.post_eject_job = None
                self.message = 'Ready—place disc'
                self.calibration_frames = []
                self._save()
            return self.snapshot()
        return self.snapshot()

    def _save_background(self, image):
        ok, encoded = cv2.imencode('.png', image)
        if not ok:
            raise OSError('Could not encode the empty-tray reference image')
        fd, name = tempfile.mkstemp(prefix='.tray-empty-', suffix='.png', dir=self.state_dir)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(encoded.tobytes())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.background_path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def reset_empty_view(self, frame, *, user_confirmed_empty=False, now=None):
        if self.tray_state != 'open':
            raise ValueError('Reset empty view requires the tray to be reliably open')
        if not user_confirmed_empty:
            raise ValueError('Explicit operator confirmation is required to reset the empty view')
        if not self.cycle_id:
            self.cycle_id=str(uuid4())
            self.cycle_started_at=time.time()
        self._start_calibration(time.monotonic() if now is None else now,
                                'Manual Reset empty view requested; waiting for a stable empty scene')
        self.previous_frame = frame.copy()
        self.manual_empty_reference = frame.copy() if user_confirmed_empty else None
        self.calibration_reference = frame.copy()

    def set_stage(self, stage, message=None, event_id=None):
        if stage not in self.STAGES:
            raise ValueError('Unknown tray preparation stage')
        self.stage = stage
        if message:
            self.message = str(message)
        if event_id is not None:
            self.event_id = str(event_id)
        self._save()

    def snapshot(self):
        return {
            'tray_state': self.tray_state,
            'preparation_stage': self.stage,
            'message': self.message,
            'tray_cycle_id': self.cycle_id,
            'tray_cycle_started_at': self.cycle_started_at,
            'post_eject_job': self.post_eject_job,
            'event_id': self.event_id,
            'calibrated': self.background is not None,
            'device': self.device,
            'updated_at': self.updated_at,
        }
