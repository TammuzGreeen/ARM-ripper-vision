import copy
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.arm import job_identity, plan
from app.camera import PresentationGate
from app.config import Settings
from app.formats import Masterlist, destination
from app.handover import beneath, checksum, copy_verified, publish_json, validate_probe
from app.main import create_app
from app.recognition import consensus, extract, match
from app.state import Store, encode

ROOT = Path(__file__).parents[1]


def master():
    data = yaml.safe_load((ROOT/'examples/ds9-season-2-part-1.yaml').read_text('utf-8'))
    data['approved']=True
    data['unresolved']=[]
    return Masterlist.model_validate(data)


def observation(disc=1,text=None):
    return extract(text or f'Star Trek Deep Space Nine\nStaffel 2\nDisc {disc}\nTeil 1',.96)


def detail():
    return {'job':{'job_id':17,'label':'EU_103539'},'tracks':[
        {'track_id':800+i,'track_number':str(i),'length':2610+i,'fps':25,'aspect_ratio':'4:3'} for i in range(4)]}


class RecognitionTests(unittest.TestCase):
    def test_clear_match_and_out_of_order(self):
        for number in (1,3,2):
            result = consensus([observation(number) for _ in range(3)])
            self.assertEqual(match(result,[master()])[0]['disc'],f'disc-{number}')

    def test_wrong_series_or_edition_never_advances(self):
        for text in ('Another Series\nStaffel 2\nDisc 1\nTeil 1','Star Trek Deep Space Nine\nStaffel 2\nDisc 1\nTeil 2'):
            self.assertEqual(match(consensus([observation(text=text)]*3),[master()]),[])

    def test_conflict_low_confidence_missing_label_and_single_photo(self):
        self.assertFalse(consensus([observation(1),observation(2)])['accepted'])
        self.assertFalse(consensus([extract('Unreadable glare',.9)]*3)['accepted'])
        self.assertFalse(consensus([extract('Staffel 2 Disc 1',.5)]*3)['accepted'])
        self.assertFalse(consensus([observation()])['accepted'])
        self.assertFalse(consensus([observation(text='Season 2 Season 3 Disc 1')]*3)['accepted'])

    def test_multiple_editions_are_ambiguous(self):
        other = master().model_copy(update={'id':'other-edition'})
        self.assertEqual(len(match(consensus([observation()]*3),[master(),other])),2)

    def test_presented_episode_conflict_rejected(self):
        f = observation(text='Star Trek Deep Space Nine\nStaffel 2 Disc 1 Teil 1\nEpisodes 5-8')
        self.assertEqual(match(consensus([f]*3),[master()]),[])

    def test_printed_episode_title_conflict_rejected(self):
        f=observation(text='Star Trek Deep Space Nine\nStaffel 2 Disc 1 Teil 1\nS02E01 - A conflicting title')
        self.assertEqual(match(consensus([f]*3),[master()]),[])

    def test_continuously_visible_disc_only_captures_once(self):
        gate = PresentationGate()
        events = [gate.observe(True,True,True) for _ in range(100)]
        self.assertEqual(events.count('capture'),1)
        self.assertNotIn('release',events)
        self.assertEqual([gate.observe(False,True,True) for _ in range(8)].count('release'),1)
        self.assertEqual([gate.observe(True,True,True) for _ in range(8)].count('capture'),1)

    def test_moving_or_blurred_object_does_not_capture(self):
        gate=PresentationGate()
        self.assertNotIn('capture',[gate.observe(True,False,True) for _ in range(20)])
        self.assertNotIn('capture',[gate.observe(True,True,False) for _ in range(20)])


class MappingTests(unittest.TestCase):
    def test_three_different_id_spaces(self):
        mapping=plan(master(),master().discs[0],detail())
        self.assertEqual((mapping[0]['arm_track_id'],mapping[0]['makemkv_id'],mapping[0]['dvd_title']),(800,0,1))
        self.assertEqual(mapping[3]['episode']['number'],4)

    def test_unknown_dvd_titles_do_not_become_index_plus_one(self):
        d=master().discs[0].model_copy(update={'title_map':[]})
        with self.assertRaisesRegex(ValueError,'DVD title numbers'):
            plan(master(),d,detail())

    def test_unexpected_count_and_missing_title_block(self):
        value=detail()
        value['tracks'].append(dict(value['tracks'][0],track_id=900,track_number='9'))
        with self.assertRaisesRegex(ValueError,'count'):
            plan(master(),master().discs[0],value)
        value['tracks']=value['tracks'][1:]
        with self.assertRaises(ValueError):
            plan(master(),master().discs[0],value)

    def test_runtime_does_not_override_order(self):
        value=detail()
        for i,t in enumerate(value['tracks']):t['length']=2800-i*20
        self.assertEqual([m['makemkv_id'] for m in plan(master(),master().discs[0],value)],[0,1,2,3])

    def test_other_discs_remain_unresolved(self):
        with self.assertRaisesRegex(ValueError,'initial review'):
            plan(master(),master().discs[1],detail())

    def test_incomplete_source_inventory_blocks_before_ripping(self):
        m=master();m.discs[0].inventory.complete=False
        with self.assertRaisesRegex(ValueError,'source stream inventory'):
            plan(m,m.discs[0],detail())

    def test_known_label_conflict(self):
        value=detail();value['job']['label']='OTHER'
        with self.assertRaisesRegex(ValueError,'label conflicts'):
            plan(master(),master().discs[0],value)

    def test_versions_and_combined_episode_names(self):
        m=master();ep=m.discs[0].episodes[0]
        name=destination(m,ep)
        self.assertNotEqual(name,destination(m.model_copy(update={'edition_name':'Original Effects'}),ep))
        self.assertIn('S02E01-E02',destination(m,ep.model_copy(update={'end':2})))
        self.assertIn('part2',destination(m,ep.model_copy(update={'part':2})))

    def test_mutable_scan_metadata_not_part_of_insertion_identity(self):
        j={'job_id':1,'start_time':'2026-01-01','devpath':'/dev/sr0','disctype':'unknown'}
        self.assertEqual(job_identity(j),job_identity(dict(j,disctype='dvd',crc_id='123')))
        self.assertNotEqual(job_identity(j),job_identity(dict(j,start_time='2026-01-02')))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.sqlite';self.store=Store(self.path)

    def ready(self):
        event=self.store.new_event()
        self.store.execute("UPDATE events SET status='ready',released=1 WHERE id=?",(event,))
        return event

    def test_expired_or_unreleased_capture_never_pairs(self):
        e=self.ready()
        self.store.execute('UPDATE events SET created=? WHERE id=?',(time.time()-1000,e))
        self.store.pair_insertion(1,'identity',180)
        self.assertIsNone(self.store.rows('SELECT * FROM events')[0]['job'])
        e=self.store.new_event()
        self.store.pair_insertion(2,'second',180)
        self.assertIsNone(self.store.rows('SELECT * FROM events WHERE id=?',(e,))[0]['job'])

    def test_one_capture_cannot_be_reused(self):
        e=self.ready();self.store.pair_insertion(1,'one',180);self.store.pair_insertion(2,'two',180)
        self.assertEqual(self.store.rows('SELECT * FROM events')[0]['job'],1)
        self.store.claim(1,'batch','disc1',e,{})
        with self.assertRaises(ValueError):self.store.claim(2,'batch','disc2',e,{})

    def test_new_unreadable_presentation_invalidates_old_capture(self):
        old=self.ready();self.store.new_event()
        self.assertEqual(self.store.rows('SELECT status FROM events WHERE id=?',(old,))[0]['status'],'invalidated')

    def test_failed_rip_does_not_consume_disc(self):
        e=self.ready();self.store.pair_insertion(1,'one',180);self.store.claim(1,'batch','disc1',e,{})
        self.store.execute("UPDATE reservations SET state='failed' WHERE job=1")
        e=self.ready();self.store.pair_insertion(2,'two',180);self.store.claim(2,'batch','disc1',e,{})
        self.assertEqual(len(self.store.rows('SELECT * FROM reservations')),2)

    def test_transactional_duplicate_claim_and_restart(self):
        e=self.ready();self.store.pair_insertion(1,'one',180)
        successes=[]
        def claim():
            try:self.store.claim(1,'batch','disc1',e,{});successes.append(True)
            except (ValueError,sqlite3.IntegrityError):pass
        threads=[threading.Thread(target=claim) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(successes),1)
        reopened=Store(self.path)
        self.assertEqual(reopened.rows('SELECT * FROM reservations')[0]['state'],'reserved')

    def test_completed_disc_cannot_be_claimed_again(self):
        e=self.ready();self.store.pair_insertion(1,'one',180);self.store.claim(1,'b','d',e,{})
        self.store.execute("UPDATE reservations SET state='ripped' WHERE job=1")
        e2=self.ready();self.store.pair_insertion(2,'two',180)
        with self.assertRaises(sqlite3.IntegrityError):self.store.claim(2,'b','d',e2,{})
        self.assertEqual(self.store.rows('SELECT status FROM events WHERE id=?',(e2,))[0]['status'],'ready')

    def test_baselined_job_cannot_consume_later_event(self):
        self.store.pair_insertion(1,'one',180)
        e=self.ready();self.store.pair_insertion(1,'one',180)
        self.assertIsNone(self.store.rows('SELECT job FROM events WHERE id=?',(e,))[0]['job'])

    def test_job_id_reuse_detected(self):
        self.store.pair_insertion(1,'original',180)
        with self.assertRaisesRegex(ValueError,'reused'):self.store.pair_insertion(1,'new',180)


class PublicationTests(unittest.TestCase):
    def test_manifest_atomic_retry_conflict_and_path_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'ready'/'1.json'
            publish_json(path,{'a':1});publish_json(path,{'a':1})
            with self.assertRaises(ValueError):publish_json(path,{'a':2})
            self.assertFalse(list(root.rglob('.pending-*')))
            for rel in ('../escape','/etc/passwd','C:/file','a\\b'):
                with self.assertRaises(ValueError):beneath(root,rel)

    def test_copy_idempotent_no_overwrite_source_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);src=root/'source';dst=root/'library'/'file'
            src.write_bytes(b'original');h=checksum(src)
            copy_verified(src,dst,h);copy_verified(src,dst,h)
            self.assertEqual(dst.read_bytes(),b'original')
            src.write_bytes(b'changed')
            with self.assertRaises(ValueError):copy_verified(src,dst,h)
            with self.assertRaises(ValueError):copy_verified(src,dst,checksum(src))
            self.assertEqual(dst.read_bytes(),b'original')

    def test_duration_tolerance_and_missing_duplicate_subtitle(self):
        inventory=master().discs[0].inventory.model_dump()
        probe={'streams':[{'codec_type':'video','codec_name':'mpeg2video','width':720,'height':576,'r_frame_rate':'25/1'}]+[
            {'codec_type':'audio','channels':c,'tags':{'language':lang}} for c,lang in zip([6,6,2,2,2],inventory['audio'])]+[
            {'codec_type':'subtitle','tags':{'language':lang}} for lang in inventory['subtitles']],
            'chapters':[{}]*8,'format':{'duration':'2618'}}
        validate_probe(probe,inventory,2614)
        probe['streams'].pop()
        with self.assertRaisesRegex(ValueError,'subtitle'):validate_probe(probe,inventory,2614)


class WebTests(unittest.TestCase):
    def test_auth_csrf_import_status_and_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Settings(state=Path(tmp)/'state',handover=Path(tmp)/'handover',password='unit-test-only')
            app=create_app(s,start_workers=False)
            with TestClient(app) as client:
                self.assertEqual(client.get('/').status_code,401)
                self.assertEqual(client.get('/health').status_code,200)
                client.auth=('operator','unit-test-only')
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(client.get('/api/schema/masterlist').status_code,200)
                self.assertEqual(client.post('/api/masters',content=master().model_dump_json()).status_code,403)
                r=client.post('/api/masters',content=master().model_dump_json(),headers={'X-Queue-Request':'1'})
                self.assertEqual(r.status_code,200,r.text)
                self.assertEqual(len(client.get('/api/state').json()['masters']),1)
                r=client.post('/api/masters',content='bad: list',headers={'X-Queue-Request':'1'})
                self.assertEqual(r.status_code,400)


if __name__=='__main__':unittest.main()
