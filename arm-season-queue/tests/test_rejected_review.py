import json
import sqlite3
import tempfile
import time
from dataclasses import replace
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.agreement import MODEL_ORDER
from app.arm import job_identity
from app.config import Settings
from app.formats import digest
from app.main import create_app
from app.state import Store
from test_core import master


class RejectedReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = create_app(Settings(state=Path(self.temp.name), arm_url='', password='test-only',
                                       recognition_backend='ollama-agreement',
                                       vision_url='http://ollama:11434'))
        self.controller = self.app.state.controller
        self.addCleanup(self.controller.validation.shutdown, wait=True)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.master = master()
        self.controller.import_master(self.master)

    def complete_run(self):
        return {tag: {'usable': True, 'transcription': 'machine text', 'model_tag': tag,
                      'model_digest': 'a' * 64, 'runtime_seconds': 1.0} for tag in MODEL_ORDER}

    def passing_result(self):
        disc = self.master.discs[0]
        fields = {name: {'status':'CORRECT', 'required':True} for name in ('series','season','episodes','titles')}
        return {
            'accepted': True, 'policy': 'two-model-priority-field-agreement-v1',
            'backend': 'ollama-agreement', 'camera': '/dev/video0', 'model_tags': list(MODEL_ORDER),
            'runs': self.complete_run(), 'frames': [{'evidence': 'event/0.jpg'}],
            'agreement': {'status': 'PASS', 'accepted': True, 'reasons': [],
                          'field_disagreements': [], 'operational_failures': [],
                          'validated_candidates': [{'master': self.master.id, 'disc': disc.id,
                                                    'primary': fields, 'secondary': fields}]},
            'matches': [{'master': self.master.id, 'disc': disc.id,
                         'masterlist_sha256': digest(self.master)}],
            'reason': 'Both models agree on all required priority fields',
        }

    def test_full_agreement_is_the_only_automatic_ready_path(self):
        event = self.controller.begin_event('camera')
        self.controller.recognition_done(event, self.passing_result())
        row = self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]
        self.assertEqual(row['status'], 'ready')
        self.assertEqual(self.controller.db.rejections(), [])

    def test_completion_after_ttl_without_expiry_sweep_stays_expired_and_ineligible(self):
        event = self.controller.begin_event('camera')
        self.controller.db.execute('UPDATE events SET created=? WHERE id=?',
                                   (time.time()-self.controller.s.ttl-1,event))
        result = self.passing_result()
        self.controller.recognition_done(event, result)
        row = self.controller.db.rows('SELECT status,body FROM events WHERE id=?',(event,))[0]
        body = json.loads(row['body'])
        self.assertEqual(row['status'],'expired')
        self.assertEqual(body['matches'],[])
        self.assertEqual(body['recognition_lifecycle']['reason'],
                         'capture_expired_before_recognition_completed')
        self.assertEqual(body['result'],result)
        self.assertEqual(body['result']['runs'][MODEL_ORDER[0]]['transcription'],'machine text')
        self.assertEqual(self.controller.db.rejections(),[])
        self.assertEqual(self.controller.db.rows('SELECT * FROM reservations'),[])

    def rejected_event(self, *, operational=False):
        event = self.controller.begin_event('camera')
        result = self.passing_result()
        if operational:
            result['runs'][MODEL_ORDER[1]].update(usable=False, error='timeout')
        else:
            result['agreement'].update(status='REJECTED_FOR_REVIEW', accepted=False,
                                       reasons=['model_disagreement_on_priority_fields'],
                                       field_disagreements=[{'field': 'episodes'}])
            result['matches'] = []
        self.controller.recognition_done(event, result)
        return event

    def test_disagreement_and_operational_failure_are_rejected_and_preserved(self):
        for operational in (False, True):
            event = self.rejected_event(operational=operational)
            record = self.controller.db.rejections('pending')[0]
            self.assertEqual(record['event'], event)
            self.assertEqual(record['review_status'], 'pending')
            self.assertTrue(record['body']['original_machine_result']['runs'])
            self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'rejected_for_review')

    def test_missing_required_priority_field_cannot_advance(self):
        event = self.controller.begin_event('camera')
        result = self.passing_result()
        result['agreement'].update(status='REJECTED_FOR_REVIEW', accepted=False,
                                   reasons=['missing_required_priority_fields'],
                                   missing_fields=[{'field':'episodes'}], validated_candidates=[])
        result['matches'] = []
        self.controller.recognition_done(event, result)
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'rejected_for_review')
        self.assertEqual(self.controller.db.rows('SELECT * FROM reservations'), [])

    def test_human_correction_is_authoritative_and_original_result_survives(self):
        event = self.rejected_event()
        record = self.controller.db.rejections('pending')[0]
        original = record['body']['original_machine_result']
        saved = self.controller.save_rejection_review(record['id'],
            {'series': 'Human Series', 'season': '2', 'episodes': '3-4', 'titles': 'One, Two'})
        self.assertEqual(saved['body']['field_sources']['series'], 'human_verified')
        self.assertEqual(saved['body']['final_metadata']['series'], 'Human Series')
        self.assertEqual(saved['body']['original_machine_result'], original)
        self.assertEqual(saved['body']['original_machine_result']['runs'][MODEL_ORDER[0]]['transcription'], 'machine text')
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'rejected_for_review')
        self.assertEqual(self.controller.db.rows('SELECT * FROM reservations'), [])

    def test_corrected_retry_requires_fresh_capture_and_explicit_review_then_can_start(self):
        event = self.rejected_event()
        rejection = next(item for item in self.controller.db.rejections('pending') if item['event'] == event)
        correction = {'series': self.master.series, 'season': str(self.master.season),
                      'episodes': '1-4', 'titles': ', '.join(ep.title for ep in self.master.discs[0].episodes),
                      'edition': self.master.edition_name, 'disc_number': '1'}
        self.controller.save_rejection_review(rejection['id'], correction)
        self.assertFalse(self.controller.human_retry_authorized(self.passing_result()))
        with self.assertRaisesRegex(ValueError, 'fresh, unpaired camera capture'):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed', job=17)

        with patch.object(self.controller.arm, 'held'), \
             patch.object(self.controller.arm, 'call', return_value={'drives':[{
                 'mount':self.controller.s.drive, 'drive_mode':'auto', 'job_id_current':None}]}):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Compared fresh frame to the physical disc')
        ready = self.controller.db.rows('SELECT * FROM events WHERE id=?', (event,))[0]
        self.assertEqual(ready['status'], 'ready')
        event_body = json.loads(ready['body'])
        self.assertTrue(self.controller.human_retry_authorized(event_body['result']))
        self.assertEqual(self.controller.db.rejection(rejection['id'])['ejection_status'], 'not_requested')
        self.assertEqual(self.controller.db.rows('SELECT * FROM reservations'), [])
        with self.assertRaisesRegex(ValueError, 'single-use'):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Attempt to relink', job=48)

        job = 47
        detail = {'job': {'job_id': job, 'start_time': '2026-01-01T00:00:00', 'devpath': '/dev/sr0',
                          'source_type': 'disc', 'label': 'EU_103539', 'status': 'manual_paused',
                          'manual_start': False},
                  'tracks': [{'track_id': 800+i, 'track_number': str(i), 'length': 2610+i,
                              'fps': 25, 'aspect_ratio': '4:3'} for i in range(4)]}
        self.controller.release_event(event)
        from app.arm import job_identity
        self.controller.db.pair_insertion(job, job_identity(detail['job']), self.controller.s.ttl)
        batch_id = self.controller.select_batch(self.master)
        batch = self.controller.db.rows('SELECT * FROM batches WHERE id=?', (batch_id,))[0]
        paired = self.controller.db.rows('SELECT * FROM events WHERE id=?', (event,))[0]
        self.controller.reserve(paired, batch, detail)
        reservation = self.controller.db.rows('SELECT * FROM reservations WHERE job=?', (job,))[0]
        with patch.object(self.controller.arm, 'detail', return_value=detail), \
             patch.object(self.controller.arm, 'configure', return_value={'tracks': []}), \
             patch.object(self.controller.arm, 'held'), \
             patch.object(self.controller.arm, 'call') as arm_call:
            self.controller.advance(reservation, True, job)
        arm_call.assert_called_once_with('POST', f'/jobs/{job}/start')
        self.assertEqual(self.controller.db.rows('SELECT state FROM reservations WHERE job=?', (job,))[0]['state'], 'ripping')

    def test_confirmed_correction_keeps_same_verified_insertion_and_survives_restart(self):
        event=self.rejected_event()
        rejection=self.controller.db.rejections('pending')[0]
        metadata={'series':self.master.series,'season':str(self.master.season),'episodes':'1-4',
                  'titles':', '.join(ep.title for ep in self.master.discs[0].episodes),
                  'edition':self.master.edition_name,'disc_number':'1'}
        saved=self.controller.save_rejection_review(rejection['id'],metadata)
        original=saved['body']['original_machine_result']
        self.assertEqual(len(saved['body']['correction_history']),1)
        job=481
        detail={'job':{'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':self.controller.s.drive,
                       'source_type':'disc','label':'EU_103539','status':'manual_paused','manual_start':False},
                'tracks':[{'track_id':800+i,'track_number':str(i),'length':2610+i,
                           'fps':25,'aspect_ratio':'4:3'} for i in range(4)]}
        self.controller.release_event(event)
        self.controller.db.pair_insertion(job,job_identity(detail['job']),self.controller.s.ttl)
        with patch.object(self.controller.arm,'held'), \
             patch.object(self.controller.arm,'detail',return_value=detail), \
             patch.object(self.controller.arm,'call',return_value={'drives':[{
                 'mount':self.controller.s.drive,'drive_mode':'auto','job_id_current':job}]}), \
             patch.object(self.controller.arm,'cancel_waiting') as cancel, \
             patch.object(self.controller.arm,'eject_drive') as eject:
            self.controller.review_event(event,self.master.id,'disc-1','Compared held disc with its label',job=job)
        cancel.assert_not_called();eject.assert_not_called()
        event_row=self.controller.db.rows('SELECT job,status,body FROM events WHERE id=?',(event,))[0]
        self.assertEqual((event_row['job'],event_row['status']),(job,'ready'))
        self.assertEqual(json.loads(event_row['body'])['review']['authority'],'human-confirmed-associated-insertion')
        restarted=type(self.controller)(self.controller.s)
        self.addCleanup(restarted.validation.shutdown,wait=True)
        retained=restarted.db.rejection(rejection['id'])['body']
        self.assertEqual(retained['original_machine_result'],original)
        self.assertEqual(retained['final_metadata'],metadata)
        self.assertEqual(restarted.db.rows('SELECT job,status FROM events WHERE id=?',(event,)),
                         [{'job':job,'status':'ready'}])

    def test_editing_correction_after_confirmation_revokes_authorization(self):
        event=self.rejected_event();rejection=self.controller.db.rejections('pending')[0]
        metadata={'series':self.master.series,'season':str(self.master.season),'episodes':'1-4',
                  'titles':', '.join(ep.title for ep in self.master.discs[0].episodes),
                  'edition':self.master.edition_name,'disc_number':'1'}
        self.controller.save_rejection_review(rejection['id'],metadata)
        job=483
        detail={'job':{'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':self.controller.s.drive,
                       'source_type':'disc','label':'EU_103539','status':'manual_paused','manual_start':False},
                'tracks':[]}
        self.controller.release_event(event);self.controller.db.pair_insertion(job,job_identity(detail['job']),self.controller.s.ttl)
        with patch.object(self.controller.arm,'held'),patch.object(self.controller.arm,'detail',return_value=detail), \
             patch.object(self.controller.arm,'call',return_value={'drives':[{'mount':self.controller.s.drive,
                    'drive_mode':'auto','job_id_current':job}]}):
            self.controller.review_event(event,self.master.id,'disc-1','Confirmed physical label',job=job)
        self.controller.save_rejection_review(rejection['id'],dict(metadata,series='Different Series'))
        row=self.controller.db.rows('SELECT status,released,body FROM events WHERE id=?',(event,))[0]
        self.assertEqual((row['status'],row['released']),('rejected_for_review',0))
        self.assertFalse(json.loads(row['body']).get('review'))

    def test_resolving_after_confirmation_revokes_unreserved_authorization(self):
        event=self.rejected_event();rejection=self.controller.db.rejections('pending')[0]
        metadata={'series':self.master.series,'season':str(self.master.season),'episodes':'1-4'}
        self.controller.save_rejection_review(rejection['id'],metadata)
        job=484;detail={'job':{'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':self.controller.s.drive,
                               'source_type':'disc','label':'EU_103539','status':'manual_paused','manual_start':False},'tracks':[]}
        self.controller.release_event(event);self.controller.db.pair_insertion(job,job_identity(detail['job']),self.controller.s.ttl)
        with patch.object(self.controller.arm,'held'),patch.object(self.controller.arm,'detail',return_value=detail), \
             patch.object(self.controller.arm,'call',return_value={'drives':[{'mount':self.controller.s.drive,
                    'drive_mode':'auto','job_id_current':job}]}):
            self.controller.review_event(event,self.master.id,'disc-1','Confirmed physical label',job=job)
        self.controller.resolve_rejection(rejection['id'])
        row=self.controller.db.rows('SELECT status,released,body FROM events WHERE id=?',(event,))[0]
        self.assertEqual((row['status'],row['released']),('rejected_for_review',0))
        self.assertFalse(json.loads(row['body']).get('review'))

    def test_correction_cannot_change_after_execution_reservation(self):
        event=self.rejected_event();rejection=self.controller.db.rejections('pending')[0]
        metadata={'series':self.master.series,'season':str(self.master.season),'episodes':'1-4'}
        self.controller.save_rejection_review(rejection['id'],metadata)
        job=485;detail={'job':{'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':self.controller.s.drive,
                               'source_type':'disc','label':'EU_103539','status':'manual_paused','manual_start':False},'tracks':[]}
        self.controller.release_event(event);self.controller.db.pair_insertion(job,job_identity(detail['job']),self.controller.s.ttl)
        with patch.object(self.controller.arm,'held'),patch.object(self.controller.arm,'detail',return_value=detail), \
             patch.object(self.controller.arm,'call',return_value={'drives':[{'mount':self.controller.s.drive,
                    'drive_mode':'auto','job_id_current':job}]}):
            self.controller.review_event(event,self.master.id,'disc-1','Confirmed physical label',job=job)
        self.controller.db.claim(job,'batch','disc-1',event,{'test_only':True})
        with self.assertRaisesRegex(ValueError,'already bound to a reservation'):
            self.controller.save_rejection_review(rejection['id'],dict(metadata,series='Changed'))

    def test_waiting_recognition_rejection_is_held_for_review_not_auto_ejected(self):
        event=self.rejected_event();self.controller.release_event(event)
        job=482;detail={'job':{'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':self.controller.s.drive,
                               'source_type':'disc','label':'EU_103539','status':'manual_paused','manual_start':False},
                        'tracks':[]}
        self.controller.db.pair_insertion(job,job_identity(detail['job']),self.controller.s.ttl)
        self.controller.initialized=True
        with patch.object(self.controller.arm,'jobs',return_value=[]), \
             patch.object(self.controller.arm,'call',return_value={'drives':[{
                 'mount':self.controller.s.drive,'drive_mode':'auto','job_id_current':job}]}), \
             patch.object(self.controller.arm,'detail',return_value=detail), \
             patch.object(self.controller.arm,'cancel_waiting') as cancel, \
             patch.object(self.controller.arm,'eject_drive') as eject:
            self.controller.tick()
        cancel.assert_not_called();eject.assert_not_called()
        self.assertEqual(self.controller.db.rows('SELECT job,status FROM events WHERE id=?',(event,)),
                         [{'job':job,'status':'rejected_for_review'}])

    def test_fileflows_disabled_keeps_finished_manifest_in_private_state_only(self):
        root=Path(self.temp.name)/'local-finished';state=root/'state';media=root/'media';handover=root/'handover'
        media.mkdir(parents=True);handover.mkdir(parents=True)
        settings=replace(self.controller.s,state=state,media=media,handover=handover,fileflows_enabled=False)
        controller=type(self.controller)(settings);self.addCleanup(controller.validation.shutdown,wait=True)
        output=media/'job'/'Disc1'/'Title.mkv';output.parent.mkdir(parents=True);output.write_bytes(b'fixture')
        event='local-event';job=586;master=self.master
        arm_job={'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':settings.drive,
                 'source_type':'disc','status':'success','path':'/home/arm/media/job'}
        detail={'job':arm_job,'tracks':[{'track_id':80,'track_number':'0','ripped':True}]}
        payload={'masterlist':master.model_dump(),'masterlist_sha256':digest(master),'identity':job_identity(arm_job),
                 'mapping':[{'arm_track_id':80,'makemkv_id':0,'scan_duration':10,'inventory':master.discs[0].inventory.model_dump(),
                             'destination':'tv/Test/Season 01/Test.mkv'}],
                 'naming_preview':{'tracks':[{'track_number':'0','rendered_folder':'Disc1','rendered_title':'Title'}]},
                 'recognition':{'result':{}},'observed_label':'fixture','structure_signature':'fixture',
                 'source_titles':detail['tracks']}
        controller.db.execute("INSERT INTO reservations(job,batch,disc,event,state,publication,body,error) VALUES(?,?,?,?,?,?,?,?)",
                              (job,'batch','disc-1',event,'validating','pending',json.dumps(payload),'') )
        with patch('app.controller.inspect_file',return_value={'sha256':'a'*64,'validation':{'decode':'passed'}}), \
             patch.object(controller.arm,'detail',return_value=detail):
            controller.finish({'job':job,'batch':'batch','disc':'disc-1','event':event},detail)
        row=controller.db.rows('SELECT state,publication FROM reservations WHERE job=?',(job,))[0]
        self.assertEqual(row,{'state':'ripped','publication':'disabled'})
        self.assertTrue((state/'finished'/f'{job}.json').is_file())
        self.assertFalse((handover/'ready'/f'{job}.json').exists())
        (handover/'acks').mkdir(parents=True);(handover/'acks'/f'{job}.json').write_text('{}')
        controller.read_ack({'job':job,'batch':'batch','publication':'disabled'})
        self.assertEqual(controller.db.rows('SELECT publication FROM reservations WHERE job=?',(job,))[0]['publication'],'disabled')

    def test_correction_cannot_be_authorized_for_the_wrong_disc(self):
        event = self.rejected_event()
        rejection = next(item for item in self.controller.db.rejections('pending') if item['event'] == event)
        self.controller.save_rejection_review(rejection['id'], {
            'series': self.master.series, 'season': str(self.master.season), 'episodes': '5-8'})
        with self.assertRaisesRegex(ValueError, 'matching this approved masterlist disc'):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed fresh capture')

    def test_reviewed_not_corrected_metadata_cannot_authorize_retry(self):
        event = self.rejected_event()
        rejection = next(item for item in self.controller.db.rejections('pending') if item['event'] == event)
        metadata = {'series': self.master.series, 'season': str(self.master.season), 'episodes': '1-4'}
        self.controller.save_rejection_review(rejection['id'], metadata, status='reviewed')
        with self.assertRaisesRegex(ValueError, 'Save a corrected identification'):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed fresh capture')

    def test_stale_rejected_capture_cannot_authorize_retry(self):
        event = self.rejected_event()
        rejection = next(item for item in self.controller.db.rejections('pending') if item['event'] == event)
        self.controller.save_rejection_review(rejection['id'], {
            'series': self.master.series, 'season': str(self.master.season), 'episodes': '1-4'})
        self.controller.db.execute('UPDATE events SET created=? WHERE id=?', (time.time()-self.controller.s.ttl-1, event))
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed fresh capture')

    def test_retry_authorization_requires_global_pause_and_idle_configured_drive(self):
        event = self.rejected_event()
        rejection = next(item for item in self.controller.db.rejections('pending') if item['event'] == event)
        self.controller.save_rejection_review(rejection['id'], {
            'series': self.master.series, 'season': str(self.master.season), 'episodes': '1-4'})
        with patch.object(self.controller.arm, 'held', side_effect=ValueError('pause released')):
            with self.assertRaisesRegex(ValueError, 'pause released'):
                self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed fresh capture')
        with patch.object(self.controller.arm, 'held'), \
             patch.object(self.controller.arm, 'call', return_value={'drives':[{
                 'mount':self.controller.s.drive, 'drive_mode':'auto', 'job_id_current':99}]}):
            with self.assertRaisesRegex(ValueError, 'drive to be idle'):
                self.controller.review_event(event, self.master.id, 'disc-1', 'Confirmed fresh capture')
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'rejected_for_review')

    def test_arm_success_in_uncertain_starting_state_requires_review(self):
        from app.state import encode
        job=991
        identity={'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':'/dev/sr0',
                  'source_type':'disc','label':'EU_103539'}
        row={'job':job,'state':'starting','body':encode({'identity':job_identity(identity)})}
        with patch.object(self.controller.arm, 'detail', return_value={'job':{**identity,'status':'success'}}), \
             patch.object(self.controller.validation, 'submit') as submit:
            with self.assertRaisesRegex(ValueError, 'outside the queue start sequence'):
                self.controller.advance(row, False, job)
        submit.assert_not_called()

    def test_starting_manual_start_and_success_after_restart_need_acknowledgement(self):
        from app.state import encode
        job=992
        identity={'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':'/dev/sr0',
                  'source_type':'disc','label':'EU_103539'}
        row={'job':job,'state':'starting','body':encode({'identity':job_identity(identity)})}
        for status, manual_start in (('manual_paused',True),('success',False)):
            with self.subTest(status=status), patch.object(self.controller.arm, 'detail',
                    return_value={'job':{**identity,'status':status,'manual_start':manual_start}}):
                with self.assertRaisesRegex(ValueError, 'acknowledgement|outside the queue start sequence'):
                    self.controller.advance(row, False, job)

    def test_retry_cannot_turn_unacknowledged_arm_success_into_ripping(self):
        from app.state import encode
        job=993
        identity={'job_id':job,'start_time':'2026-01-01T00:00:00','devpath':'/dev/sr0',
                  'source_type':'disc','label':'EU_103539'}
        with self.controller.db.connect() as db:
            db.execute("INSERT INTO reservations(job,batch,disc,event,state,body) VALUES (?,?,?,?,?,?)",
                       (job,'batch','disc-1','event','starting',encode({'identity':job_identity(identity)})))
        with patch.object(self.controller.arm, 'detail',
                          return_value={'job':{**identity,'status':'success'}}):
            with self.assertRaisesRegex(ValueError, 'no durable queue-start acknowledgement'):
                self.controller.action('retry', job=job)
        self.assertEqual(self.controller.db.rows('SELECT state FROM reservations WHERE job=?',(job,))[0]['state'],'starting')

    def test_expired_unreferenced_evidence_prunes_json_and_image_files(self):
        event = self.controller.begin_event('camera')
        evidence = Path(self.temp.name) / 'evidence' / event
        evidence.mkdir(parents=True)
        (evidence / '0.jpg').write_bytes(b'frame')
        (evidence / 'agreement-primary-30b-stream.jsonl').write_text('{"partial":true}')
        self.controller.db.execute('UPDATE events SET created=? WHERE id=?',
                                   (time.time() - (self.controller.s.retention + 1) * 86400, event))
        self.controller.prune_evidence()
        self.assertFalse(evidence.exists())
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (event,))[0]['status'], 'evidence_expired')

    def test_existing_state_database_additively_gets_rejection_table(self):
        path = Path(self.temp.name) / 'older-state.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('''CREATE TABLE events (
                id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
                released INTEGER NOT NULL DEFAULT 0, job INTEGER UNIQUE, body TEXT NOT NULL)''')
            db.execute("INSERT INTO events(id,created,status,body) VALUES ('legacy-event',1,'review','{}')")
        migrated = Store(path)
        self.assertEqual(migrated.rows('SELECT id,status FROM events'), [{'id':'legacy-event','status':'review'}])
        self.assertEqual(migrated.rows("SELECT name FROM sqlite_master WHERE type='table' AND name='rejected_rips'"),
                         [{'name':'rejected_rips'}])

    def test_controller_restart_retires_unpaired_rejection(self):
        stale = self.rejected_event()
        record = next(x for x in self.controller.db.rejections('pending') if x['event'] == stale)
        restarted = type(self.controller)(self.controller.s)
        self.addCleanup(restarted.validation.shutdown, wait=True)
        self.assertEqual(restarted.db.rows('SELECT status FROM events WHERE id=?', (stale,))[0]['status'], 'invalidated')
        self.assertEqual(restarted.db.rejection(record['id'])['ejection_status'], 'capture_invalidated')

    def test_polling_outage_retires_unpaired_rejection(self):
        stale = self.rejected_event()
        record = next(x for x in self.controller.db.rejections('pending') if x['event'] == stale)
        object.__setattr__(self.controller.s, 'arm_url', 'http://arm.invalid')
        with patch.object(self.controller.arm, 'call', side_effect=ValueError('API unavailable')):
            self.controller.poll_once()
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (stale,))[0]['status'], 'invalidated')
        self.assertEqual(self.controller.db.rejection(record['id'])['ejection_status'], 'capture_invalidated')

    def test_resolution_keeps_audit_and_pending_filter_separates_records(self):
        self.rejected_event()
        record = self.controller.db.rejections('pending')[0]
        self.controller.save_rejection_review(record['id'], {'series':'A', 'season':'1', 'episodes':'1'})
        self.controller.resolve_rejection(record['id'])
        self.assertEqual(self.controller.db.rejections('pending'), [])
        history = self.controller.db.rejections('resolved')
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['review_status'], 'resolved')
        self.assertEqual(history[0]['body']['audit_trail'][-1]['action'], 'resolved')
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (history[0]['event'],))[0]['status'], 'rejected_for_review')
        self.assertEqual(self.controller.db.rows('SELECT * FROM reservations'), [])

    def test_web_api_lists_pending_and_resolved(self):
        self.rejected_event()
        pending = self.client.get('/api/rejected-rips?status=pending', auth=('operator','test-only'))
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(len(pending.json()['items']), 1)
        self.assertEqual(self.client.get('/api/rejected-rips?status=resolved', auth=('operator','test-only')).json()['items'], [])

    def test_ejection_is_requested_only_after_safe_waiting_job_check(self):
        event = self.rejected_event()
        record = self.controller.db.rejections('pending')[0]
        self.controller.release_event(event)
        job_detail = {'job_id':47, 'start_time':'2026-01-01T00:00:00',
                      'devpath':'/dev/sr0', 'source_type':'disc', 'status':'manual_paused'}
        self.controller.db.pair_insertion(47, job_identity(job_detail), 180)
        drive = {'drive_id':3, 'mount':'/dev/sr0', 'job_id_current':47}
        with patch.object(self.controller.arm, 'held') as held, \
             patch.object(self.controller.arm, 'cancel_waiting') as cancel, \
             patch.object(self.controller.arm, 'eject_drive') as eject, \
             patch.object(self.controller.arm, 'detail', return_value={'job':job_detail}), \
             patch.object(self.controller.arm, 'call', return_value={'drives':[drive]}):
            self.controller.reject_inserted(record, drive, 47)
        held.assert_called_once()
        cancel.assert_called_once_with(47)
        eject.assert_called_once_with(3)
        self.assertEqual(self.controller.db.rejection(record['id'])['ejection_status'], 'ejected')

    def test_changed_drive_job_after_cancel_prevents_ejecting_replacement(self):
        event = self.rejected_event()
        record = self.controller.db.rejections('pending')[0]
        self.controller.release_event(event)
        job_detail = {'job_id':47, 'start_time':'2026-01-01T00:00:00',
                      'devpath':'/dev/sr0', 'source_type':'disc', 'status':'manual_paused'}
        self.controller.db.pair_insertion(47, job_identity(job_detail), 180)
        original_drive = {'drive_id':3, 'mount':'/dev/sr0', 'job_id_current':47}
        replacement_drive = {'drive_id':3, 'mount':'/dev/sr0', 'job_id_current':48}
        with patch.object(self.controller.arm, 'held'), \
             patch.object(self.controller.arm, 'cancel_waiting') as cancel, \
             patch.object(self.controller.arm, 'eject_drive') as eject, \
             patch.object(self.controller.arm, 'detail', return_value={'job':job_detail}), \
             patch.object(self.controller.arm, 'call', side_effect=[
                 {'drives':[original_drive]}, {'drives':[replacement_drive]}]):
            self.controller.reject_inserted(record, original_drive, 47)
        cancel.assert_called_once_with(47)
        eject.assert_not_called()
        self.assertEqual(self.controller.db.rejection(record['id'])['ejection_status'], 'failed')

    def test_rejection_pairing_survives_restart_without_changing_job_association(self):
        event = self.rejected_event()
        record = self.controller.db.rejections('pending')[0]
        self.controller.release_event(event)
        job_detail = {'job_id':47, 'start_time':'2026-01-01T00:00:00',
                      'devpath':'/dev/sr0', 'source_type':'disc', 'status':'manual_paused'}
        self.controller.db.pair_insertion(47, job_identity(job_detail), 180)
        drive = {'drive_id':3, 'mount':'/dev/sr0', 'job_id_current':47}
        restarted = type(self.controller)(self.controller.s)
        self.addCleanup(restarted.validation.shutdown, wait=True)
        with patch.object(restarted.arm, 'held'), \
             patch.object(restarted.arm, 'cancel_waiting') as cancel, \
             patch.object(restarted.arm, 'eject_drive') as eject, \
             patch.object(restarted.arm, 'detail', return_value={'job':job_detail}), \
             patch.object(restarted.arm, 'call', return_value={'drives':[drive]}):
            restarted.reject_inserted(record, drive, 47)
        cancel.assert_called_once_with(47)
        eject.assert_called_once_with(3)
        self.assertEqual(restarted.db.rejection(record['id'])['ejection_status'], 'ejected')

    def test_invalidated_or_duplicate_rejections_cannot_pair_to_a_later_disc(self):
        first = self.rejected_event()
        self.controller.release_event(first)
        first_record = self.controller.db.rejections('pending')[0]
        self.controller.db.invalidate_pending(include_rejections=True)
        self.controller.db.pair_insertion(47, 'insert-one', 180)
        event_row = self.controller.db.rows('SELECT job,status FROM events WHERE id=?', (first,))[0]
        self.assertIsNone(event_row['job'])
        self.assertEqual(event_row['status'], 'invalidated')
        self.assertEqual(self.controller.db.rejection(first_record['id'])['ejection_status'], 'capture_invalidated')

        second = self.rejected_event()
        second_record = next(x for x in self.controller.db.rejections('pending') if x['event'] == second)
        self.controller.db.update_rejection(second_record['id'], ejection_status='failed')
        third = self.rejected_event()
        self.controller.release_event(second)
        self.controller.release_event(third)
        third_record = next(x for x in self.controller.db.rejections('pending') if x['event'] == third)
        self.controller.db.pair_insertion(48, 'insert-two', 180)
        self.assertIsNone(self.controller.db.rows('SELECT job FROM events WHERE id=?', (second,))[0]['job'])
        self.assertEqual(self.controller.db.rows('SELECT job FROM events WHERE id=?', (third,))[0]['job'], 48)
        self.assertEqual(self.controller.db.rejection(second_record['id'])['ejection_status'], 'superseded_before_insertion')

    def test_failed_ejection_is_retired_before_next_capture_can_pair(self):
        failed = self.rejected_event()
        failed_record = next(x for x in self.controller.db.rejections('pending') if x['event'] == failed)
        self.controller.db.update_rejection(failed_record['id'], ejection_status='failed')
        fresh = self.rejected_event()
        fresh_record = next(x for x in self.controller.db.rejections('pending') if x['event'] == fresh)
        self.controller.release_event(fresh)
        self.controller.db.pair_insertion(93, 'new-insertion', 180)
        self.assertEqual(self.controller.db.rows('SELECT status FROM events WHERE id=?', (failed,))[0]['status'], 'invalidated')
        self.assertEqual(self.controller.db.rejection(failed_record['id'])['ejection_status'], 'superseded_before_insertion')
        self.assertEqual(self.controller.db.rows('SELECT job FROM events WHERE id=?', (failed,))[0]['job'], None)
        self.assertEqual(self.controller.db.rows('SELECT job FROM events WHERE id=?', (fresh,))[0]['job'], 93)


if __name__ == '__main__':
    unittest.main()
