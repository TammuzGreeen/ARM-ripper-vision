import threading
import unittest
from types import SimpleNamespace

from app.dry_run import build_plan, parse_info
from app.formats import Disc, Episode, Inventory, Masterlist, TitleMap, digest
from app.controller import Controller
from app.camera import Camera
from app.state import Store
from tempfile import TemporaryDirectory
from pathlib import Path


INFO = '''
CINFO:1,6206,"DVD disc"
CINFO:2,0,"EU_104279"
TCOUNT:2
TINFO:0,8,0,"8"
TINFO:0,9,0,"0:43:47"
TINFO:0,10,0,"1.8 GB"
TINFO:0,11,0,"1932735283"
TINFO:0,24,0,"01"
TINFO:0,25,0,"1"
SINFO:0,0,1,6201,"Video"
SINFO:0,0,5,0,"V_MPEG2"
SINFO:0,0,6,0,"Mpeg2"
SINFO:0,0,19,0,"720x576"
SINFO:0,0,20,0,"4:3"
SINFO:0,0,21,0,"25"
SINFO:0,1,1,6202,"Audio"
SINFO:0,1,3,0,"eng"
SINFO:0,1,4,0,"English"
SINFO:0,1,14,0,"2"
SINFO:0,2,1,6203,"Subtitles"
SINFO:0,2,3,0,"ger"
SINFO:0,2,4,0,"German"
TINFO:1,8,0,"8"
TINFO:1,9,0,"0:44:00"
TINFO:1,24,0,"02"
TINFO:1,25,0,"1"
SINFO:1,0,1,6201,"Video"
SINFO:1,0,5,0,"V_MPEG2"
SINFO:1,0,19,0,"720x576"
SINFO:1,0,20,0,"4:3"
SINFO:1,0,21,0,"25"
SINFO:1,1,1,6202,"Audio"
SINFO:1,1,3,0,"eng"
SINFO:1,1,14,0,"2"
SINFO:1,2,1,6203,"Subtitles"
SINFO:1,2,3,0,"ger"
'''


def master():
    inventory = Inventory(audio=['eng'], subtitles=['deu'], audio_channels=[2],
                          complete=True, evidence='retained MakeMKV info scan')
    return Masterlist(schema_version=1, id='voyager_s2', series='Star Trek Voyager',
        season=2, edition='split-dvd', edition_name='German DVD split-season release',
        edition_tokens=['EU_104279'], approved=True, provenance=['user confirmed ID order'],
        discs=[Disc(id='disc_1', number=1, labels=['EU_104279'],
            episodes=[Episode(printed='1',number=1,title='Die 37er'),Episode(printed='2',number=2,title='Der Namenlose')],
            expected_title_count=2, order='explicit', selection_ids=[0,1],
            title_map=[TitleMap(makemkv_id=0,dvd_title=1,episode_index=0,evidence='user-confirmed ID order',inventory=inventory),
                       TitleMap(makemkv_id=1,dvd_title=2,episode_index=1,evidence='user-confirmed ID order',inventory=inventory)])])


class DryRunTests(unittest.TestCase):
    def test_parser_retains_title_layout_and_individual_streams(self):
        parsed=parse_info(INFO)
        title=parsed['disc']['titles'][0]
        self.assertEqual(parsed['title_count'],2)
        self.assertEqual(parsed['disc']['label'],'EU_104279')
        self.assertEqual((title['dvd_title'],title['angle_count'],title['duration'],title['chapters']),(1,1,'0:43:47',8))
        self.assertEqual(title['audio'][0]['language'],'eng')
        self.assertEqual(title['audio'][0]['channels'],2)
        self.assertEqual(title['subtitles'][0]['language'],'deu')
        self.assertEqual(title['video']['resolution'],'720x576')

    def test_proposal_uses_normal_arm_planner_and_creates_no_media(self):
        m=master(); d=m.discs[0]
        proposal=build_plan(m,d,parse_info(INFO),'/local-ssd/completed')
        self.assertEqual(proposal['mode'],'DRY RUN ONLY')
        self.assertFalse(proposal['ready_for_ripping'])
        self.assertEqual(proposal['blockers'],[])
        self.assertEqual([x['makemkv_id'] for x in proposal['outputs']],[0,1])
        self.assertEqual(proposal['outputs'][0]['dvd_title'],1)
        self.assertEqual(proposal['outputs'][0]['episode']['title'],'Die 37er')
        self.assertEqual(proposal['outputs'][0]['destination'],
                         '/local-ssd/completed/tv/Star Trek Voyager/Season 02/Star Trek Voyager S02E01 - Die 37er - German DVD split-season release.mkv')
        self.assertEqual(proposal['outputs'][0]['destination_kind'],'would be created')
        self.assertEqual(proposal['outputs'][0]['audio_streams'][0]['language'],'eng')

    def test_conflicting_dvd_title_or_stream_inventory_blocks_preview(self):
        changed=INFO.replace('TINFO:0,24,0,"01"','TINFO:0,24,0,"03"')
        proposal=build_plan(master(),master().discs[0],parse_info(changed))
        self.assertEqual(proposal['plan_status'],'blocked')
        self.assertTrue(any('DVD title' in text for text in proposal['blockers']))
        changed=INFO.replace('SINFO:0,1,3,0,"eng"','SINFO:0,1,3,0,"fra"')
        proposal=build_plan(master(),master().discs[0],parse_info(changed))
        self.assertTrue(any('audio language inventory differs' in text for text in proposal['blockers']))

    def test_extra_scanned_title_is_shown_excluded_and_blocks_ready_plan(self):
        extra=INFO.replace('TCOUNT:2','TCOUNT:3')+'TINFO:2,9,0,"0:01:00"\n'
        proposal=build_plan(master(),master().discs[0],parse_info(extra))
        self.assertEqual(proposal['excluded_titles'][0]['makemkv_id'],2)
        self.assertIn('No approved output mapping',proposal['excluded_titles'][0]['reason'])
        self.assertFalse(proposal['ready_for_ripping'])

    def test_incomplete_scan_and_non_dryrun_paths_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'no TCOUNT'):
            parse_info('MSG:5010,0,0,"Failed to open disc"')
        controller=Controller.__new__(Controller)
        controller.s=SimpleNamespace(dry_run_only=True)
        controller.lock=threading.RLock()
        with self.assertRaisesRegex(ValueError,'locked to dry-run'):
            controller.activate('voyager_s2')
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY blocks'):
            controller.action('resume')
        with self.assertRaisesRegex(ValueError,'DRY_RUN_ONLY blocks human'):
            controller.review_event('event','master','disc','confirmed')

    def test_dry_run_scan_requires_same_capture_and_info_only_attestation(self):
        controller=Controller.__new__(Controller)
        controller.s=SimpleNamespace(dry_run_only=True,drive='/dev/sr0')
        controller.lock=threading.RLock()
        controller.db=SimpleNamespace(get=lambda _key:{'id':'run','event':'event','status':'review'})
        context={'device':'/dev/sr0','capture_event':'different-event','tray_closed':True,
                 'medium_ready':True,'media_changed_during_scan':False,'same_insertion':True,
                 'operation':'makemkvcon-info-only','media_output_created':False}
        with self.assertRaisesRegex(ValueError,'another camera event'):
            controller.receive_dry_run_scan('run','# DRY_RUN_CAPTURE_EVENT:event\n'+INFO,context)
        context['capture_event']='event';context['media_output_created']=True
        with self.assertRaisesRegex(ValueError,'info-only'):
            controller.receive_dry_run_scan('run','# DRY_RUN_CAPTURE_EVENT:event\n'+INFO,context)

    def test_queue_database_file_is_private(self):
        with TemporaryDirectory() as temp:
            path=Path(temp)/'queue.sqlite3'
            Store(path)
            self.assertEqual(path.stat().st_mode & 0o777,0o600)

    def test_camera_capture_for_dry_run_never_enters_production_pairing_or_rejection(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from app.state import Store
        with TemporaryDirectory() as temp:
            c=Controller.__new__(Controller)
            c.s=SimpleNamespace(dry_run_only=True,recognition_backend='ollama-agreement',ttl=180)
            c.db=Store(Path(temp)/'queue.sqlite3');c.lock=threading.RLock()
            m=master();c.db.execute('INSERT INTO masters VALUES (?,?)',(m.id,m.model_dump_json()))
            c.db.put('active_dry_run',{'id':'dry-run','status':'awaiting_capture','master':m.id,
                'masterlist_sha256':digest(m),'disc':'disc_1'})
            event=c.begin_event('camera')
            result={'accepted':False,'reason':'recognition unavailable','frames':[{'evidence':event+'/0.jpg'}],
                    'backend':'ollama-agreement','runs':{},'agreement':{'status':'FAILED'}}
            c.recognition_done(event,result)
            run=c.db.get('active_dry_run')
            self.assertEqual(run['event'],event)
            self.assertEqual(run['recognition']['status'],'unmatched')
            self.assertEqual(c.db.rows('SELECT * FROM reservations'),[])
            self.assertEqual(c.db.rows('SELECT * FROM rejected_rips'),[])
            row=c.db.rows('SELECT status,released,job FROM events WHERE id=?',(event,))[0]
            self.assertEqual(row,{'status':'review','released':0,'job':None})

    def test_dryrun_tick_never_queries_arm_or_production_database(self):
        c=Controller.__new__(Controller);c.s=SimpleNamespace(dry_run_only=True)
        c.arm=SimpleNamespace(call=lambda *_:(_ for _ in ()).throw(AssertionError('ARM must not be queried')))
        c.db=SimpleNamespace(execute=lambda *_:(_ for _ in ()).throw(AssertionError('queue must not be mutated')))
        c.tick()

    def test_dryrun_poll_never_baselines_or_invalidates_existing_production_events(self):
        c=Controller.__new__(Controller)
        c.s=SimpleNamespace(dry_run_only=True,retention=30)
        c.status='';c.prune_evidence=lambda:None
        c.arm=SimpleNamespace(jobs=lambda:(_ for _ in ()).throw(AssertionError('baseline must not query ARM')))
        c.poll_once()
        self.assertIn('production queue mutations are disabled',c.status)

    def test_isolated_camera_event_does_not_invalidate_production_evidence(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from app.state import Store
        with TemporaryDirectory() as temp:
            db=Store(Path(temp)/'queue.sqlite3')
            old=db.new_event('camera')
            db.execute("UPDATE events SET status='ready' WHERE id=?",(old,))
            fresh=db.new_event('dry_run_camera',isolated=True)
            self.assertEqual(db.rows('SELECT status FROM events WHERE id=?',(old,))[0]['status'],'ready')
            self.assertEqual(db.rows('SELECT status FROM events WHERE id=?',(fresh,))[0]['status'],'processing')

    def test_dryrun_restart_records_interrupted_attempt_and_rearms_capture(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from app.state import Store
        with TemporaryDirectory() as temp:
            c=Controller.__new__(Controller);c.db=Store(Path(temp)/'queue.sqlite3')
            event=c.db.new_event('dry_run_camera',isolated=True)
            c.db.put('active_dry_run',{'id':'run','status':'recognizing','event':event,'recognition':None})
            c.recover_interrupted_dry_run_capture()
            run=c.db.get('active_dry_run')
            self.assertEqual(run['status'],'awaiting_capture')
            self.assertIsNone(run['event'])
            self.assertEqual(run['capture_attempts'][0]['event'],event)
            self.assertEqual(c.db.rows('SELECT status FROM events WHERE id=?',(event,))[0]['status'],'invalidated')

    def test_camera_capture_gate_has_a_bounded_failure_deadline(self):
        c=Camera.__new__(Camera);c.event='event';c.event_started_at=100.0;c.submitted=False
        c.s=SimpleNamespace(presentation_timeout=60)
        self.assertFalse(c.capture_deadline_reached(159.9))
        self.assertTrue(c.capture_deadline_reached(160.0))
        c.submitted=True
        self.assertFalse(c.capture_deadline_reached(200.0))

    def test_dryrun_restart_preserves_production_event_and_reservation_rows(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from app.config import Settings
        from app.state import Store
        with TemporaryDirectory() as temp:
            state=Path(temp)/'state';db=Store(state/'queue.sqlite3')
            event=db.new_event('camera');db.execute("UPDATE events SET status='ready' WHERE id=?",(event,))
            db.execute("INSERT INTO reservations(job,batch,disc,event,state,body) VALUES (?,?,?,?,?,?)",
                       (42,'batch','disc',event,'validating','{}'))
            c=Controller(Settings(state=state,dry_run_only=True,password='test'))
            self.assertEqual(c.db.rows('SELECT status FROM events WHERE id=?',(event,))[0]['status'],'ready')
            self.assertEqual(c.db.rows('SELECT state FROM reservations WHERE job=42')[0]['state'],'validating')
            c.validation.shutdown(wait=False,cancel_futures=True)


if __name__ == '__main__':
    unittest.main()
