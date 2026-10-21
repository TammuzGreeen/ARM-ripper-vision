import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.tray_monitor import TrayMonitor, CDS_NO_DISC, CDS_TRAY_OPEN, CDS_DRIVE_NOT_READY
from app.tray_preparation import TrayPreparation


class TrayMonitorTests(unittest.TestCase):
    def test_no_disc_is_closed_not_open_and_manual_open_is_debounced_once(self):
        self.assertEqual(TrayMonitor.classify(CDS_NO_DISC),'closed')
        self.assertEqual(TrayMonitor.classify(CDS_TRAY_OPEN),'open')
        self.assertEqual(TrayMonitor.classify(CDS_DRIVE_NOT_READY),'unknown')
        samples=iter([CDS_NO_DISC,CDS_NO_DISC,CDS_NO_DISC,
                      CDS_TRAY_OPEN,CDS_TRAY_OPEN,CDS_TRAY_OPEN,CDS_TRAY_OPEN,CDS_TRAY_OPEN])
        monitor=TrayMonitor('/dev/test',reader=lambda:next(samples),interval=.5,stable_samples=3)
        self.assertEqual(monitor.poll(now=0)['state'],'unknown')
        self.assertEqual(monitor.poll(now=.5)['state'],'unknown')
        initial=monitor.poll(now=1.0)
        self.assertEqual(initial['state'],'closed');self.assertTrue(initial['initial'])
        self.assertIsNone(monitor.poll(now=1.5)['transition'])
        self.assertIsNone(monitor.poll(now=2.0)['transition'])
        opened=monitor.poll(now=2.5)
        self.assertEqual(opened['transition'],'open')
        for now in (3.0,3.5):
            self.assertIsNone(monitor.poll(now=now)['transition'])

    def test_unavailable_and_noisy_state_is_explicit_unknown(self):
        monitor=TrayMonitor('/dev/missing',reader=lambda:(_ for _ in ()).throw(OSError('gone')),
                            interval=.5,stable_samples=2)
        monitor.poll(now=0)
        reading=monitor.poll(now=.5)
        self.assertEqual(reading['state'],'unknown')
        self.assertIn('unavailable',reading['error'])

    def test_monitor_opens_device_read_only_and_polling_is_bounded(self):
        calls=[]
        monitor=TrayMonitor('/dev/test',reader=lambda:calls.append('read') or CDS_NO_DISC,
                            interval=1,stable_samples=2)
        monitor.poll(now=0);monitor.poll(now=.2);monitor.poll(now=.9)
        self.assertEqual(len(calls),1)
        monitor.poll(now=1.0)
        self.assertEqual(len(calls),2)


class TrayPreparationTests(unittest.TestCase):
    @staticmethod
    def frame(value=20):
        return np.full((100,160),value,dtype=np.float32)

    def calibrate(self,prep,now=0,value=20):
        background=self.frame(value)
        prep.tray_state='open'
        prep._start_calibration(now)
        prep.previous_frame=background.copy()
        for i in range(prep.calibration_samples+2):
            prep.observe_frame(background,stable=True,now=now+prep.settle_seconds+.2+i*.2)
        return background

    def test_manual_open_starts_one_calibration_cycle_and_freezes_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,calibration_samples=3)
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            cycle=prep.cycle_id
            self.assertEqual(prep.stage,'calibrating_empty_tray')
            base=self.calibrate(prep,now=1)
            self.assertEqual(prep.stage,'ready_for_disc')
            frozen=prep.background.copy()
            for i in range(20):
                prep.observe_frame(self.frame(220),stable=True,now=10+i)
            np.testing.assert_array_equal(prep.background,frozen)
            self.assertEqual(prep.cycle_id,cycle)

    def test_post_rip_eject_waits_for_disc_removal_before_refreshing_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,calibration_samples=3)
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            self.calibrate(prep,now=1)
            original=prep.background.copy()
            prep.tray_observation('closed',transition='closed',now=10)
            prep.note_arm_ejected(42)
            prep.tray_observation('open',transition='open',now=11)
            for i in range(10):
                prep.observe_frame(self.frame(220),stable=True,now=12+i*.2)
            self.assertEqual(prep.stage,'tray_open_waiting_for_disc_removal')
            np.testing.assert_array_equal(prep.background,original)
            t=15
            for i in range(10):
                prep.observe_frame(original,stable=True,now=t+i*.2)
            self.assertEqual(prep.stage,'calibrating_empty_tray')
            for i in range(10):
                prep.observe_frame(original,stable=True,now=20+i*.2)
            self.assertEqual(prep.stage,'ready_for_disc')
            self.assertIsNone(prep.post_eject_job)

    def test_early_placement_and_reset_require_an_empty_stable_scene(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,calibration_samples=3)
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            prep.observe_frame(self.frame(50),stable=True,disc_like=True,now=2)
            self.assertEqual(prep.stage,'early_disc_during_calibration')
            with self.assertRaisesRegex(ValueError,'remove it'):
                prep.reset_empty_view(self.frame(),disc_like=True,now=3)
            prep.reset_empty_view(self.frame(),disc_like=True,user_confirmed_empty=True,now=4)
            self.assertEqual(prep.stage,'calibrating_empty_tray')
            self.assertIsNone(prep.background)

    def test_manual_empty_confirmation_suppresses_fixed_tray_ring_but_not_scene_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,calibration_samples=3)
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            empty=self.frame(20)
            prep.reset_empty_view(empty,disc_like=True,user_confirmed_empty=True,now=2)
            prep.observe_frame(empty,stable=True,disc_like=True,now=3)
            self.assertEqual(prep.stage,'calibrating_empty_tray')
            prep.observe_frame(self.frame(220),stable=True,disc_like=True,now=4)
            self.assertEqual(prep.stage,'early_disc_during_calibration')

    def test_startup_open_requires_persisted_context_or_manual_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor')
            prep.tray_observation('open',initial=True,now=0)
            self.assertEqual(prep.stage,'empty_view_preparation_needed')
            self.assertIn('already open',prep.message)
            prep.reset_empty_view(self.frame(),disc_like=False,now=1)
            cycle=prep.cycle_id
            prep2=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,calibration_samples=3)
            prep2.tray_observation('open',initial=True,now=2)
            self.assertEqual(prep2.stage,'calibrating_empty_tray')
            self.assertEqual(prep2.cycle_id,cycle)

    def test_capture_is_retained_when_expected_tray_close_occurs(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor')
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            prep.set_stage('capturing','Capture frozen','event-1')
            prep.tray_observation('closed',transition='closed',now=2)
            self.assertEqual(prep.stage,'capturing')
            self.assertEqual(prep.event_id,'event-1')

    def test_calibration_timeout_and_unknown_tray_do_not_enable_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            prep=TrayPreparation(tmp,'/dev/arm-monitor',settle_seconds=.5,
                                 calibration_timeout=2,calibration_samples=100)
            prep.tray_observation('closed',initial=True,now=0)
            prep.tray_observation('open',transition='open',now=1)
            prep.observe_frame(self.frame(),stable=True,now=1)
            prep.observe_frame(self.frame(),stable=True,now=4)
            self.assertEqual(prep.stage,'empty_view_preparation_needed')
            prep.tray_observation('unknown',now=5)
            self.assertEqual(prep.stage,'tray_status_unavailable')


if __name__=='__main__':
    unittest.main()
