import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.arm import ARM
from app.config import Settings
from app.controller import Controller
from app.main import create_app, current_workflow_stage
from app.state import Store, encode
from app.formats import arm_output_path, output_destination
from app.ordinal import infer_ordinal_mapping


class LocalBetaTests(unittest.TestCase):
    @staticmethod
    def ordinal_result():
        return {'accepted':True,'agreement':{
            'accepted':True,'status':'PASS','field_disagreements':[],'operational_failures':[],
            'working_identification':{'series_display':'Example Series','season':1,'episodes':[1,2],
                                      'disc_format':['dvd']},
            'field_combinations':{'episodes':{'classification':'corroborated'},
                                   'season':{'classification':'single-source'}}}}

    def test_current_gui_stage_ignores_completed_reservations_and_stale_events(self):
        camera={'preparation_stage':'tray_closed'}
        snapshot={'reservations':[{'workflow':'local-beta-v1','state':'accepted','job':5}],
                  'events':[{'status':'ready','created':1,'body':{'tray_cycle':{'tray_cycle_id':'old'}}}]}
        self.assertEqual(current_workflow_stage(snapshot,camera,ttl=60,now=1000),'tray_closed')
        snapshot['events']=[{'status':'processing','created':995,'body':{'tray_cycle':{'tray_cycle_id':'new'}}}]
        self.assertEqual(current_workflow_stage(snapshot,camera,ttl=60,now=1000),'recognising_scanning')
        snapshot['reservations']=[{'workflow':'local-beta-v1','state':'awaiting_review','job':6}]
        self.assertEqual(current_workflow_stage(snapshot,camera,ttl=60,now=1000),'awaiting_review')

    def test_ordinal_mapping_is_disabled_by_default_and_requires_local_beta_to_enable(self):
        self.assertFalse(Settings().auto_ordinal_mapping)
        with self.assertRaisesRegex(ValueError,'requires LOCAL_BETA_ENABLED'):
            Settings(auto_ordinal_mapping=True,local_beta=False)
        result,reason=infer_ordinal_mapping(self.ordinal_result(),[],enabled=False)
        self.assertIsNone(result)
        self.assertIn('disabled',reason)

    def test_ordinal_mapping_requires_matching_relevant_title_count_and_preserves_ids(self):
        tracks=[{'track_number':8,'track_id':80,'length':1500,'source':'first'},
                {'track_number':2,'track_id':20,'length':42,'source':'second'},
                {'track_number':3,'track_id':30,'length':60,'source':'third'},
                {'track_number':1,'track_id':10,'length':12,'source':'extra'}]
        result,reason=infer_ordinal_mapping(self.ordinal_result(),tracks,enabled=True)
        self.assertIsNone(result)
        self.assertIn('Count mismatch',reason)
        tracks=[tracks[0],tracks[1],tracks[3]]
        result,reason=infer_ordinal_mapping(self.ordinal_result(),tracks,enabled=True)
        self.assertIsNone(reason)
        self.assertEqual(result['eligible_makemkv_ids'],[2,8])
        self.assertEqual([(item['makemkv_id'],item['episode_number']) for item in result['assignments']],
                         [(2,1),(8,2)])
        self.assertEqual(result['status'],'inferred_unverified')
        self.assertFalse(result['verified'])
        self.assertIn('do not prove',result['warning'])

    def test_ordinal_mapping_rejects_incomplete_ranges_and_bad_recognition(self):
        result_data=self.ordinal_result()
        result_data['agreement']['working_identification']['episodes']=[1,3]
        inferred,reason=infer_ordinal_mapping(result_data,[{'track_number':1,'length':50},
                                                           {'track_number':3,'length':50}],enabled=True)
        self.assertIsNone(inferred)
        self.assertIn('contiguous',reason)
        inferred,reason=infer_ordinal_mapping({'accepted':False},[],enabled=True)
        self.assertIsNone(inferred)
        self.assertIn('field-evidence',reason)

    def test_owner_confirmed_ordinal_scope_maps_actual_ids_to_disc_episode_list(self):
        result_data=self.ordinal_result()
        result_data['agreement']['working_identification']['episodes']=[5,6,7,8]
        tracks=[{'track_number':i,'track_id':100+i,'length':1200+i*60,
                 'dvd_title_number':i+1} for i in range(4)]
        mapping,reason=infer_ordinal_mapping(result_data,tracks,enabled=True)
        self.assertIsNone(reason)
        self.assertEqual([(x['makemkv_id'],x['episode_number']) for x in mapping['assignments']],
                         [(0,5),(1,6),(2,7),(3,8)])
        self.assertEqual([x['dvd_title_number'] for x in mapping['assignments']],[None]*4)
        self.assertEqual([x['ordinal_position'] for x in mapping['assignments']],[1,2,3,4])
        self.assertTrue(mapping['user_confirmed_scope']['source_ids_restart_each_disc'])
        self.assertFalse(mapping['verified'])

    def test_ordinal_scope_blocks_non_dvd_angles_and_duplicate_looking_titles(self):
        data=self.ordinal_result()
        data['agreement']['working_identification']['disc_format']=['blu-ray']
        mapping,reason=infer_ordinal_mapping(data,[{'track_number':0,'length':50},
            {'track_number':1,'length':60}],enabled=True)
        self.assertIsNone(mapping);self.assertIn('DVDs',reason)
        data=self.ordinal_result()
        mapping,reason=infer_ordinal_mapping(data,[{'track_number':0,'length':50,'angle_count':2},
            {'track_number':1,'length':60}],enabled=True)
        self.assertIsNone(mapping);self.assertIn('angles',reason)
        mapping,reason=infer_ordinal_mapping(data,[{'track_number':0,'length':50},
            {'track_number':1,'length':50}],enabled=True)
        self.assertIsNone(mapping);self.assertIn('Duplicate-looking',reason)

    def test_local_beta_configuration_requires_downstream_off_and_live_execution(self):
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY=false and FILEFLOWS_ENABLED=false'):
            Settings(local_beta=True,dry_run_only=True,fileflows_enabled=False,password='test')
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY=false and FILEFLOWS_ENABLED=false'):
            Settings(local_beta=True,dry_run_only=False,fileflows_enabled=True,password='test')
        valid=Settings(local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test')
        self.assertTrue(valid.local_beta)

    def test_automatic_ordinal_and_tray_close_require_local_beta_monitor(self):
        with self.assertRaisesRegex(ValueError,'requires TRAY_MONITOR_ENABLED=true'):
            Settings(local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test',
                     tray_close_enabled=True)
        active=Settings(local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test',
            auto_ordinal_mapping=True,tray_monitor_enabled=True,tray_monitor_device='/dev/sr0',
            tray_close_enabled=True)
        self.assertTrue(active.auto_ordinal_mapping)
        self.assertTrue(active.tray_monitor_enabled)
        self.assertTrue(active.tray_close_enabled)
        with self.assertRaisesRegex(ValueError,'must match configured ARM_DRIVE'):
            Settings(local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test',
                tray_monitor_enabled=True,tray_monitor_device='/dev/sr1',drive='/dev/sr0')

    def test_arm_tray_action_uses_explicit_arm_api_method(self):
        arm=ARM.__new__(ARM);arm.held=Mock();arm.call=Mock(return_value={'success':True})
        arm.tray(1,'close')
        arm.call.assert_called_once_with('POST','/drives/1/eject',{'method':'close'})
        with self.assertRaisesRegex(ValueError,'explicit eject or close'):
            arm.tray(1,'toggle')

    def test_tray_cycle_configuration_requires_local_beta(self):
        with self.assertRaisesRegex(ValueError,'Tray cycling requires LOCAL_BETA_ENABLED=true'):
            Settings(tray_cycle_enabled=True,local_beta=False,dry_run_only=False,
                     fileflows_enabled=False,password='test')
        self.assertTrue(Settings(tray_cycle_enabled=True,local_beta=True,dry_run_only=False,
                                 fileflows_enabled=False,password='test').tray_cycle_enabled)

    def test_fresh_nonempty_capture_can_proceed_with_model_failure_as_provisional(self):
        self.assertTrue(Controller.local_beta_capture_usable({
            'camera':'/dev/video0','backend':'ollama-agreement','frames':[{'evidence':'event/0.jpg'}],
            'agreement':{'operational_failures':['primary']},
        }))
        self.assertFalse(Controller.local_beta_capture_usable({'camera':'/dev/video0','frames':[]}))
        self.assertFalse(Controller.local_beta_capture_usable({'camera':'/dev/video0','test_only':True,
                                                                'frames':[{'evidence':'event/0.jpg'}]}))
        self.assertTrue(Controller.local_beta_capture_usable({'camera':'/dev/video0','backend':'ollama-agreement',
            'capture_source':'webcam','frames':[],'reason':'camera frame could not be retained'}))
        self.assertFalse(Controller.local_beta_capture_usable({'camera':'/dev/video0','backend':'ollama-agreement',
            'capture_source':'vision_test','test_only':True,'frames':[],'reason':'no image'}))

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
        self.assertFalse(any(call.args[1].endswith('/naming') for call in arm.call.call_args_list))

    def test_arm_configuration_sends_readable_masterlist_name_without_folder_override(self):
        arm=ARM.__new__(ARM);arm.held=Mock()
        initial={'job':{'status':'manual_paused','manual_start':False},
                 'config':{'SKIP_TRANSCODE':'true','RIPMETHOD':'mkv','MAINFEATURE':False,'MINLENGTH':0,'MAXLENGTH':99998},
                 'tracks':[{'track_id':13,'track_number':'0','enabled':True}]}
        reread={'tracks':[{'track_id':13,'track_number':'0','enabled':True,'custom_filename':'Star Trek - Voyager S02E01 - Die 37er','episode_number':'1'}]}
        arm.detail=Mock(side_effect=[initial,reread])
        arm.call=Mock(return_value={'tracks':[{'track_number':'0','rendered_title':'Star Trek - Voyager S02E01 - Die 37er','rendered_folder':'Star Trek - Voyager/Season 02'}]})
        mapping=[{'arm_track_id':13,'makemkv_id':0,'basename':'Star Trek - Voyager S02E01 - Die 37er',
                  'content_name':'Die 37er','episode':{'number':1}}]
        arm.configure(5,SimpleNamespace(series='Star Trek: Voyager',season=2),mapping)
        calls=[call.args for call in arm.call.call_args_list]
        self.assertTrue(any('/tracks/13' in args[1] and args[2]['custom_filename']==mapping[0]['basename'] for args in calls))
        self.assertFalse(any(args[1].endswith('/naming') for args in calls))

    def test_unmatched_beta_reservation_records_all_titles_and_uses_provisional_names(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';media.mkdir(parents=True)
            c=Controller.__new__(Controller);c.lock=threading.RLock();c.s=SimpleNamespace(
                local_beta=True,drive='/dev/sr0',media=media,beta_output_root=str(media/'unreviewed'),
                arm_media='/home/arm/media',beta_min_free_bytes=1)
            c.db=Store(root/'state'/'queue.sqlite3');c.masters=lambda:[]
            c.arm=SimpleNamespace(held=Mock(),completed_directory=Mock(return_value=Path('/home/arm/media/completed/tv/Unknown series/Season 02')),
                jobs=Mock(return_value=[{'job_id':8,'status':'manual_paused'}]),
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
            self.assertTrue(all(x['naming_attribution']['episode_title_source']=='unknown' for x in payload['mapping']))
            self.assertTrue(all(str(x['destination']).startswith(str(media/'completed'/'tv')) for x in payload['mapping']))
            self.assertTrue(all('arm-season-queue/job-' not in x['destination'] for x in payload['mapping']))

    def test_consecutive_different_series_and_no_match_keep_fresh_per_insertion_identity(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);media=root/'media';media.mkdir(parents=True)
            c=Controller.__new__(Controller);c.lock=threading.RLock();c.s=SimpleNamespace(
                local_beta=True,drive='/dev/sr0',media=media,beta_min_free_bytes=1,
                arm_media='/home/arm/media')
            c.db=Store(root/'state'/'queue.sqlite3');c.masters=lambda:[]
            current={'job':None}
            c.arm=SimpleNamespace(held=Mock(),
                completed_directory=lambda job,master:Path(f"/home/arm/media/completed/job-{job['job_id']}"),
                jobs=lambda:[{'job_id':current['job'],'status':'manual_paused'}],
                call=lambda method,path,body=None:{'drives':[{'mount':'/dev/sr0',
                    'job_id_current':current['job'],'drive_mode':'auto'}]})
            identities=[{'series_display':'Star Trek: Deep Space Nine','season':2},
                        {'series_display':'The Expanse','season':1},{}]
            for job,identity in enumerate(identities,20):
                current['job']=job
                event_id=c.db.new_event('camera')
                c.db.execute("UPDATE events SET status='ready',released=1,job=? WHERE id=?",(job,event_id))
                detail={'job':{'job_id':job,'status':'manual_paused','manual_start':False,
                               'devpath':'/dev/sr0','start_time':f'2026-10-21T07:00:{job}'},
                        'tracks':[{'track_id':job*10,'track_number':'0','length':1200}]}
                body={'source':'camera','result':{'camera':'/dev/video0','backend':'ollama-agreement',
                    'frames':[{'evidence':f'{event_id}/0.jpg'}],
                    'agreement':{'working_identification':identity}},'matches':[]}
                c.reserve_local_beta({'id':event_id,'body':encode(body),'created':time.time()},
                                     {'id':'fresh-session'},detail,body)
                reservation=c.db.rows('SELECT body FROM reservations WHERE job=?',(job,))[0]
                saved=json.loads(reservation['body'])
                self.assertEqual(saved['recognition']['result']['agreement']['working_identification'],identity)
                self.assertEqual(saved['mapping'][0]['mapping_status'],'provisional')
                self.assertEqual(saved['mapping'][0]['makemkv_id'],0)
            self.assertIn('Deep Space Nine',json.loads(c.db.rows('SELECT body FROM reservations WHERE job=20')[0]['body'])['mapping'][0]['basename'])
            self.assertIn('The Expanse',json.loads(c.db.rows('SELECT body FROM reservations WHERE job=21')[0]['body'])['mapping'][0]['basename'])
            self.assertTrue(json.loads(c.db.rows('SELECT body FROM reservations WHERE job=22')[0]['body'])['mapping'][0]['basename'].startswith('Source Title'))

    def test_fresh_unrestricted_session_has_no_masterlist_or_cursor_and_is_unique(self):
        with TemporaryDirectory() as tmp:
            c=Controller.__new__(Controller);c.db=Store(Path(tmp)/'queue.sqlite3')
            c.db.execute('INSERT INTO batches VALUES (?,?,?)',
                         ('old-voyager','{"id":"voyager","season":2,"discs":[]}', 'running'))
            c.db.put('active_batch','old-voyager')
            c.db.put('batch_contexts',{'old-voyager':{'start':{'disc':'voyager-s2-d1','entry_index':2}}})
            first=c.select_batch(None,{'disc':None,'entry_index':None},fresh=True)
            first_row=c.db.rows('SELECT master,state FROM batches WHERE id=?',(first,))[0]
            first_context=c.db.get('batch_contexts')[first]
            second=c.select_batch(None,{'disc':None,'entry_index':None},fresh=True)
            self.assertNotEqual(first,'old-voyager');self.assertNotEqual(first,second)
            self.assertEqual(json.loads(first_row['master']),{'schema_version':1,'batch_context':None})
            self.assertEqual(first_context['start'],{'disc':None,'entry_index':None})
            self.assertEqual(c.db.rows('SELECT state FROM batches WHERE id=?',('old-voyager',))[0]['state'],'paused')
            self.assertEqual(c.db.get('active_batch'),second)

    def test_fresh_unrestricted_activation_requires_closed_tray_and_no_live_arm_job(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            c=Controller.__new__(Controller);c.lock=threading.RLock()
            c.s=SimpleNamespace(dry_run_only=False,recognition_backend='ollama-agreement',local_beta=True)
            c.db=Store(Path(tmp)/'queue.sqlite3');c.masters=lambda:[]
            c.arm=SimpleNamespace(inspect=Mock(return_value={'issues':[]}),
                call=Mock(return_value={'drives':[{'mount':'/dev/sr0','job_id_current':None}]}),
                jobs=Mock(return_value=[]))
            c.get_tray_status=lambda:{'connected':True,'mode':'auto','tray_state':'open',
                                      'preparation_stage':'ready_for_disc'}
            c.baseline=Mock();c.select_batch=Mock(return_value='new-session')
            with patch('app.controller.agreement_preflight',return_value={'ready':True}):
                with self.assertRaisesRegex(ValueError,'physically closed'):
                    c.activate(fresh_unrestricted=True)
            c.get_tray_status=lambda:{'connected':True,'mode':'auto','tray_state':'closed',
                                      'preparation_stage':'tray_closed'}
            with patch('app.controller.agreement_preflight',return_value={'ready':True}):
                self.assertEqual(c.activate(fresh_unrestricted=True),'new-session')
            c.select_batch.assert_called_once_with(None,{'disc':None,'entry_index':None},fresh=True)

    def test_fresh_unrestricted_activation_requires_prior_batch_cancelled(self):
        from unittest.mock import patch
        with TemporaryDirectory() as tmp:
            c=Controller.__new__(Controller);c.lock=threading.RLock()
            c.s=SimpleNamespace(dry_run_only=False,recognition_backend='ollama-agreement',local_beta=True)
            c.db=Store(Path(tmp)/'queue.sqlite3');c.db.put('active_batch','old-target')
            c.db.execute('INSERT INTO batches VALUES (?,?,?)',('old-target','{}','running'))
            c.masters=lambda:[];c.arm=SimpleNamespace(inspect=Mock(return_value={'issues':[]}))
            with patch('app.controller.agreement_preflight',return_value={'ready':True}):
                with self.assertRaisesRegex(ValueError,'Cancel batch'):
                    c.activate(fresh_unrestricted=True)

    def test_local_review_can_record_incorrect_without_claiming_unassessed_output_correct(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'media').mkdir()
            c=self._controller(root,[])
            saved=c.review_local_beta(7,'mark_incorrect',{'notes':'Identity does not match the disc.'})
            self.assertEqual(saved['status'],'needs_attention')
            self.assertEqual(saved['feedback']['assessment'],'incorrect')
            self.assertEqual(saved['review_history'][-1]['action'],'mark_incorrect')
            self.assertEqual(c.db.rows('SELECT state FROM reservations WHERE job=7')[0]['state'],'needs_attention')

    def test_snapshot_close_requires_saved_event_and_explicitly_calls_arm_close(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';media=root/'media'
            c=Controller.__new__(Controller);c.lock=threading.RLock();c.s=SimpleNamespace(local_beta=True,tray_cycle_enabled=True,tray_close_enabled=True,
                state=state,ttl=180,batch_switch_policy='review',drive='/dev/sr0')
            c.db=Store(state/'queue.sqlite3');event=c.db.new_event('camera')
            (state/'evidence'/event).mkdir(parents=True)
            evidence=state/'evidence'/event/'0.jpg';evidence.write_bytes(b'captured')
            c.db.execute("UPDATE events SET status='processing',created=?,body=? WHERE id=?",
                         (time.time(),encode({'source':'camera'}),event))
            c.db.put('active_batch','batch-1')
            c.db.execute('INSERT INTO batches VALUES (?,?,?)',('batch-1','{}','running'))
            c.arm=ARM.__new__(ARM);c.arm.held=Mock()
            c.arm.call=Mock(side_effect=[{'drives':[{'mount':'/dev/sr0','drive_mode':'auto','drive_id':1,'job_id_current':None}]},
                                         {'success':True}])
            result=c.close_tray_after_snapshot(event,[{'evidence':f'{event}/0.jpg'}])
            self.assertEqual(result['status'],'closed')
            self.assertEqual(c.arm.call.call_args_list[-1].args,
                             ('POST','/drives/1/eject',{'method':'close'}))
            saved=json.loads(c.db.rows('SELECT body FROM events WHERE id=?',(event,))[0]['body'])
            self.assertEqual(saved['tray_action']['status'],'closed')
            repeated=c.close_tray_after_snapshot(event,[{'evidence':f'{event}/0.jpg'}])
            self.assertEqual(repeated['status'],'closed')
            self.assertEqual(c.arm.call.call_count,2)

    def test_snapshot_never_closes_when_arm_reports_an_existing_job(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state'
            c=Controller.__new__(Controller);c.lock=threading.RLock();c.s=SimpleNamespace(local_beta=True,tray_cycle_enabled=True,tray_close_enabled=True,
                state=state,ttl=180,drive='/dev/sr0')
            c.db=Store(state/'queue.sqlite3');event=c.db.new_event('camera')
            (state/'evidence'/event).mkdir(parents=True)
            (state/'evidence'/event/'0.jpg').write_bytes(b'captured')
            c.db.execute("UPDATE events SET status='processing',created=?,body=? WHERE id=?",
                         (time.time(),encode({'source':'camera'}),event))
            c.db.put('active_batch','batch-1');c.db.execute('INSERT INTO batches VALUES (?,?,?)',('batch-1','{}','running'))
            c.arm=SimpleNamespace(held=Mock(),call=Mock(return_value={'drives':[
                {'mount':'/dev/sr0','drive_mode':'auto','drive_id':1,'job_id_current':44}]}))
            result=c.close_tray_after_snapshot(event,[{'evidence':f'{event}/0.jpg'}])
            self.assertEqual(result['status'],'failed')
            self.assertEqual(c.arm.call.call_count,1)
            self.assertNotIn('POST',[call.args[0] for call in c.arm.call.call_args_list])

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

    def test_arm_output_path_deduplicates_existing_job_folder(self):
        self.assertEqual(str(arm_output_path('/media/completed/tv/Show/Season 02','Show/Season 02','Show S02E01 - Pilot')),
                         '/media/completed/tv/Show/Season 02/Show S02E01 - Pilot.mkv')
        self.assertEqual(str(arm_output_path('/media/completed/tv/Show/Season 02','Extras','Special')),
                         '/media/completed/tv/Show/Season 02/Extras/Special.mkv')

    def test_known_masterlist_output_is_readable_and_not_generic(self):
        from tests.test_core import master
        m=master().model_copy(update={'schema_version':2});disc=m.discs[0];item=disc.title_map[0]
        planned=output_destination(m,disc,item)
        self.assertIn('S02E01',planned)
        self.assertIn(disc.episodes[item.episode_index].title,planned)
        self.assertNotIn(m.edition_name,planned)
        self.assertNotIn('ASQ-',planned)

    def test_arm_completed_directory_uses_live_config_and_normal_pattern(self):
        arm=ARM.__new__(ARM);arm.s=SimpleNamespace(arm_media='/home/arm/media')
        arm.call=Mock(return_value={'config':{'COMPLETED_PATH':'/home/arm/media/completed/',
            'TV_SUBDIR':'tv','TV_FOLDER_PATTERN':'{show}/Season {season}'}})
        master=SimpleNamespace(series='Star Trek: Voyager',season=2)
        self.assertEqual(str(arm.completed_directory({'video_type':'series'},master)),
                         '/home/arm/media/completed/tv/Star Trek - Voyager/Season 02')

    def test_starting_entry_context_is_advisory_and_does_not_mark_prior_entries(self):
        from tests.test_core import master
        c=Controller.__new__(Controller);c.db=Store(Path(tempfile.mkdtemp())/'queue.sqlite3')
        m=master();batch=c.select_batch(m,{'disc':m.discs[0].id,'entry_index':2})
        saved=c.db.get('batch_contexts')[batch]
        self.assertTrue(saved['advisory_only']);self.assertEqual(saved['start']['entry_index'],2)
        self.assertNotIn('completed',json.dumps(saved).lower())
        with self.assertRaisesRegex(ValueError,'different advisory starting hint'):
            c.select_batch(m,{'disc':m.discs[0].id,'entry_index':3})
        self.assertEqual(c.db.get('batch_contexts')[batch],saved)

    def test_successful_completion_uses_persisted_job_not_current_drive_association(self):
        import hashlib
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';media=root/'media';relative='completed/tv/Show/Season 02/Show S02E01 - Pilot.mkv'
            path=media/relative;path.parent.mkdir(parents=True);path.write_bytes(b'verified media fixture')
            identity={'job_id':5,'start_time':'start','devpath':'/dev/sr0','source_type':'dvd'}
            payload={'workflow':'local-beta-v1','identity':__import__('app.arm',fromlist=['job_identity']).job_identity(identity),
                'mapping':[{'arm_track_id':13,'makemkv_id':0,'basename':'Show S02E01 - Pilot','episode':{'number':1,'title':'Pilot'},'scan_duration':100,'inventory':None}],
                'naming_preview':{'tracks':[{'track_number':'0','rendered_title':'Show S02E01 - Pilot','rendered_folder':'Show/Season 02'}]},
                'selected_titles':[],'excluded_titles':[],'source_titles':[],'selection_policy':'fixture','mapping_source':'fixture',
                'identity_fields':{},'warnings':[],'recognition':{},'observed_label':'disc'}
            c=Controller.__new__(Controller);c.s=SimpleNamespace(state=state,media=media,arm_media='/home/arm/media',media_host_prefix='',app_version='test',camera='/dev/video0',camera_rotation=0,roi='0,0,1,1',sharpness=0,vision_timeout=10,fileflows_enabled=False)
            c.db=Store(state/'queue.sqlite3');c.arm=SimpleNamespace(
                detail=Mock(return_value={'job':dict(identity,status='success')}),
                call=Mock(return_value={'config':{'AUTO_EJECT':'False'}}))
            c.db.execute('''INSERT INTO reservations(job,batch,disc,event,state,publication,body,error)
                VALUES (5,'b','d','event','validating','local_review',?,'')''',(encode(payload),))
            import app.controller as controller_module
            original=controller_module.inspect_beta_file;controller_module.inspect_beta_file=lambda p,d:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'validation':{'decode':'passed','stream_inventory':'passed'}}
            try:c.finish_local_beta({'job':5,'batch':'b','disc':'d','event':'event'}, {'job':dict(identity,status='success',path='/home/arm/media/completed/tv/Show/Season 02'),'tracks':[{'track_id':13,'ripped':True,'enabled':True}]},payload)
            finally:controller_module.inspect_beta_file=original
            row=c.db.rows('SELECT state,body FROM reservations WHERE job=5')[0]
            self.assertEqual(row['state'],'awaiting_review')
            self.assertEqual(c.arm.detail.call_count,1)
            self.assertEqual(payload['local_review']['outputs'][0]['host_path'],str(path))

    def test_arm_eject_failure_is_warning_and_does_not_hide_successful_outputs(self):
        import hashlib
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';media=root/'media';relative='completed/tv/Show/Season 02/Show S02E01 - Pilot.mkv'
            path=media/relative;path.parent.mkdir(parents=True);path.write_bytes(b'verified media fixture')
            identity={'job_id':6,'start_time':'start','devpath':'/dev/sr0','source_type':'dvd'}
            from app.arm import job_identity
            payload={'workflow':'local-beta-v1','identity':job_identity(identity),'mapping':[{'arm_track_id':9,'makemkv_id':0,'basename':'Show S02E01 - Pilot','episode':{'number':1,'title':'Pilot'},'scan_duration':100,'inventory':None}],
                'naming_preview':{'tracks':[{'track_number':'0','rendered_title':'Show S02E01 - Pilot','rendered_folder':'Show/Season 02'}]},
                'selected_titles':[],'excluded_titles':[],'source_titles':[],'selection_policy':'fixture','mapping_source':'fixture','identity_fields':{},'warnings':[],'recognition':{},'observed_label':'disc'}
            c=Controller.__new__(Controller);c.s=SimpleNamespace(state=state,media=media,arm_media='/home/arm/media',media_host_prefix='',app_version='test',camera='/dev/video0',camera_rotation=0,roi='0,0,1,1',sharpness=0,vision_timeout=10,fileflows_enabled=False)
            c.db=Store(state/'queue.sqlite3');terminal={'job':dict(identity,status='success',errors='AUTO_EJECT failed: tray error',ejected=False)}
            c.arm=SimpleNamespace(detail=Mock(return_value=terminal),call=Mock(return_value={'config':{'AUTO_EJECT':'True'}}))
            c.db.execute("INSERT INTO reservations(job,batch,disc,event,state,publication,body,error) VALUES (6,'b','d','event','validating','local_review',?,'')",(encode(payload),))
            import app.controller as controller_module
            original=controller_module.inspect_beta_file;controller_module.inspect_beta_file=lambda p,d:{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'validation':{'decode':'passed','stream_inventory':'passed'}}
            try:c.finish_local_beta({'job':6,'batch':'b','disc':'d','event':'event'}, {'job':dict(identity,status='success',path='/home/arm/media/completed/tv/Show/Season 02'),'tracks':[{'track_id':9,'ripped':True,'enabled':True}]},payload)
            finally:controller_module.inspect_beta_file=original
            row=c.db.rows('SELECT state,error,body FROM reservations WHERE job=6')[0];saved=json.loads(row['body'])['local_review']
            self.assertEqual(row['state'],'awaiting_review')
            self.assertIn('AUTO_EJECT failed',row['error'])
            self.assertEqual(saved['tray_action']['status'],'failed')
            self.assertEqual(len(saved['outputs']),1)

    def test_diagnostic_assessment_only_persists_feedback(self):
        with TemporaryDirectory() as tmp:
            settings=Settings(state=Path(tmp),local_beta=True,dry_run_only=False,fileflows_enabled=False,password='test')
            app=create_app(settings,start_workers=False);client=TestClient(app);client.auth=('operator','test')
            event=app.state.controller.db.new_event('vision_test')
            app.state.controller.db.execute("UPDATE events SET status='review',body=? WHERE id=?",(encode({'source':'vision_test','result':{'series':'Observed'}}),event))
            before=app.state.controller.db.rows('SELECT state FROM batches')
            for outcome in ('correct','incorrect'):
                response=client.post(f'/api/events/{event}/assessment',headers={'X-Queue-Request':'1'},json={'outcome':outcome,'note':'checked'})
                self.assertEqual(response.status_code,200)
            after=json.loads(app.state.controller.db.rows('SELECT body FROM events WHERE id=?',(event,))[0]['body'])
            self.assertEqual([x['revision'] for x in after['diagnostic_assessments']],[1,2])
            self.assertEqual([x['outcome'] for x in after['diagnostic_assessments']],['correct','incorrect'])
            self.assertEqual(before,app.state.controller.db.rows('SELECT state FROM batches'))

    def test_queue_open_does_not_eject_even_with_legacy_cycle_flag(self):
        c=Controller.__new__(Controller);c.s=SimpleNamespace(local_beta=True,tray_cycle_enabled=True)
        c.arm=SimpleNamespace(tray=Mock())
        self.assertIsNone(c.open_tray_for_queue())
        c.arm.tray.assert_not_called()

    def test_terminal_local_review_is_listed_when_post_rip_task_failed(self):
        with TemporaryDirectory() as tmp:
            settings=Settings(state=Path(tmp),local_beta=True,dry_run_only=False,
                              fileflows_enabled=False,password='test')
            app=create_app(settings,start_workers=False)
            client=TestClient(app)
            self.addCleanup(client.close)
            client.auth=('operator','test')
            controller=app.state.controller
            payload={'workflow':'local-beta-v1','mapping':[],
                     'local_review':{'status':'awaiting_review','outputs':[{'actual_path':'/actual/job/file.mkv'}]}}
            controller.db.execute('''INSERT INTO reservations
                (job,batch,disc,event,state,publication,body,error) VALUES (5,'batch','disc','capture',
                'review','local_review',?,'tray action failed')''',(encode(payload),))
            response=client.get('/api/local-beta/reviews?status=awaiting_review')
            self.assertEqual(response.status_code,200)
            self.assertEqual([item['job'] for item in response.json()['items']],[5])
            self.assertEqual(response.json()['items'][0]['error'],'tray action failed')
            self.assertEqual(response.json()['items'][0]['local_review']['outputs'][0]['actual_path'],
                             '/actual/job/file.mkv')


if __name__=='__main__':
    unittest.main()
