import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from app.arm import ARM
from app.config import Settings
from app.controller import Controller
from app.state import Store, encode


class LocalBetaTests(unittest.TestCase):
    def test_local_beta_configuration_requires_downstream_off_and_live_execution(self):
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY=false and FILEFLOWS_ENABLED=false'):
            Settings(local_beta=True,dry_run_only=True,fileflows_enabled=False,password='test')
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY=false and FILEFLOWS_ENABLED=false'):
            Settings(local_beta=True,dry_run_only=False,fileflows_enabled=True,password='test')
        valid=Settings(local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test')
        self.assertTrue(valid.local_beta)

    def test_fresh_nonempty_capture_can_proceed_with_model_failure_as_provisional(self):
        self.assertTrue(Controller.local_beta_capture_usable({
            'camera':'/dev/video0','backend':'ollama-agreement','frames':[{'evidence':'event/0.jpg'}],
            'agreement':{'operational_failures':['primary']},
        }))
        self.assertFalse(Controller.local_beta_capture_usable({'camera':'/dev/video0','frames':[]}))
        self.assertFalse(Controller.local_beta_capture_usable({'camera':'/dev/video0','test_only':True,
                                                                'frames':[{'evidence':'event/0.jpg'}]}))

    def test_provisional_arm_configuration_selects_every_scanned_source(self):
        arm=ARM.__new__(ARM)
        arm.held=Mock()
        initial={'job':{'status':'manual_paused','manual_start':False},
                 'config':{'SKIP_TRANSCODE':'true','RIPMETHOD':'mkv','MAINFEATURE':False,
                           'MINLENGTH':0,'MAXLENGTH':99998},
                 'tracks':[{'track_id':10,'track_number':'0','length':1200},
                           {'track_id':11,'track_number':'1','length':1600}]}
        reread={'tracks':[dict(t,enabled=True,episode_number='0',custom_filename=f'Source Title {i:03d} - PROVISIONAL')
                          for i,t in enumerate(initial['tracks'])]}
        arm.detail=Mock(side_effect=[initial,reread])
        arm.call=Mock(side_effect=lambda method,path,body=None:
                      {'tracks':[{'track_number':'0','rendered_folder':'arm-season-queue/job-8','rendered_title':'Source Title 000 - PROVISIONAL'},
                                 {'track_number':'1','rendered_folder':'arm-season-queue/job-8','rendered_title':'Source Title 001 - PROVISIONAL'}]}
                      if path.endswith('naming-preview') else {})
        mapping=[{'arm_track_id':10,'makemkv_id':0,'basename':'Source Title 000 - PROVISIONAL','content_name':'Source Title 000'},
                 {'arm_track_id':11,'makemkv_id':1,'basename':'Source Title 001 - PROVISIONAL','content_name':'Source Title 001'}]
        preview=arm.configure_provisional(8,{},mapping)
        self.assertEqual(len(preview['tracks']),2)
        calls=[c.args for c in arm.call.call_args_list]
        self.assertTrue(any(args[1].endswith('/tracks/10') for args in calls))
        self.assertTrue(any(args[1].endswith('/tracks/11') for args in calls))
        self.assertEqual(arm.held.call_count,1)

    def test_unmatched_beta_reservation_records_all_titles_and_uses_provisional_names(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';(media/'unreviewed').mkdir(parents=True)
            c=Controller.__new__(Controller);c.lock=threading.RLock();c.s=SimpleNamespace(
                local_beta=True,drive='/dev/sr0',media=media,beta_output_root=str(media/'unreviewed'),
                beta_min_free_bytes=1)
            c.db=Store(root/'state'/'queue.sqlite3');c.masters=lambda:[]
            c.arm=SimpleNamespace(held=Mock(),jobs=Mock(return_value=[{'job_id':8,'status':'manual_paused'}]),
                call=Mock(return_value={'drives':[{'mount':'/dev/sr0','job_id_current':8,'drive_mode':'auto'}]}))
            event_id=c.db.new_event('camera')
            c.db.execute("UPDATE events SET status='ready',released=1,job=8 WHERE id=?",(event_id,))
            detail={'job':{'job_id':8,'status':'manual_paused','manual_start':False,
                           'devpath':'/dev/sr0','start_time':'2026-10-19T12:00:00'},
                    'tracks':[{'track_id':21,'track_number':'0','length':1200},
                              {'track_id':22,'track_number':'1','length':1600}]}
            body={'source':'camera','result':{'camera':'/dev/video0','backend':'ollama-agreement',
                  'frames':[{'evidence':f'{event_id}/0.jpg'}],
                  'agreement':{'working_identification':{'series_display':'Unknown series'},
                               'field_disagreements':['season']}}}
            c.reserve_local_beta({'id':event_id,'body':encode(body)},{'id':'batch'},detail,body)
            row=c.db.rows('SELECT * FROM reservations WHERE job=8')[0]
            payload=json.loads(row['body'])
            self.assertEqual(row['state'],'reserved')
            self.assertEqual([x['makemkv_id'] for x in payload['selected_titles']],[0,1])
            self.assertEqual(payload['excluded_titles'],[])
            self.assertTrue(all(x['provisional'] for x in payload['mapping']))
            self.assertTrue(all('PROVISIONAL' in x['basename'] for x in payload['mapping']))

    def _controller(self, root, outputs):
        c=Controller.__new__(Controller)
        c.lock=threading.RLock()
        c.s=SimpleNamespace(local_beta=True,state=root/'state',media=root/'media')
        c.db=Store(root/'state'/'queue.sqlite3')
        payload={'workflow':'local-beta-v1','local_review':{
            'schema_version':1,'job':7,'event':'event-7','status':'awaiting_review',
            'identity_fields':{'series_display':'Series','season':2},'outputs':outputs,
            'review_history':[],'feedback':{'reviewed_fields':[],'corrections':[]}}}
        c.db.execute('INSERT INTO reservations(job,batch,disc,event,state,publication,body,error) VALUES (?,?,?,?,?,?,?,?)',
                     (7,'batch','disc','event-7','awaiting_review','local_review',encode(payload),''))
        return c

    def test_review_renames_without_overwrite_and_preserves_original_feedback(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media'/'unreviewed'/'job-7';media.mkdir(parents=True)
            source=media/'Source Title 000 - PROVISIONAL.mkv';source.write_bytes(b'local beta output')
            output={'makemkv_id':0,'actual_path':str(source),'actual_filename':source.name,
                    'original_filename':source.name,'bytes':source.stat().st_size,'provisional':True}
            c=self._controller(root,[output])
            saved=c.review_local_beta(7,'save_reviewed',{
                'corrections':{'0':{'filename':'Series S02E03 - Corrected.mkv','episode_number':3,
                                    'episode_name':'Corrected'}},
                'reviewed_fields':['identity','episodes','files'],'notes':'Checked against disc label.'})
            corrected=media/'Series S02E03 - Corrected.mkv'
            self.assertTrue(corrected.is_file());self.assertFalse(source.exists())
            self.assertEqual(saved['status'],'reviewed')
            self.assertEqual(saved['outputs'][0]['original_filename'],source.name)
            self.assertEqual(saved['review_history'][0]['renames'][0]['to'],str(corrected))
            self.assertEqual(c.db.rows('SELECT state FROM reservations WHERE job=7')[0]['state'],'reviewed')
            persisted=json.loads((root/'state'/'reviews'/'7.json').read_text())
            self.assertEqual(persisted['feedback']['reviewed_fields'],['episodes','files','identity'])

    def test_review_collision_leaves_source_and_job_unmodified(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media'/'unreviewed'/'job-7';media.mkdir(parents=True)
            source=media/'Source Title 000 - PROVISIONAL.mkv';source.write_bytes(b'source')
            (media/'Taken.mkv').write_bytes(b'other')
            c=self._controller(root,[{'makemkv_id':0,'actual_path':str(source),
                                      'actual_filename':source.name,'original_filename':source.name}])
            with self.assertRaisesRegex(ValueError,'already exists'):
                c.review_local_beta(7,'save_reviewed',{'corrections':{'0':{'filename':'Taken.mkv'}}})
            self.assertTrue(source.is_file())
            self.assertEqual(c.db.rows('SELECT state FROM reservations WHERE job=7')[0]['state'],'awaiting_review')


if __name__=='__main__':
    unittest.main()
