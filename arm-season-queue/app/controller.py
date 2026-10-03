import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from uuid import uuid4

from .arm import ARM, job_identity, plan, structure
from .formats import Masterlist, digest
from .handover import beneath, canonical, inspect_file, publish_json
from .recognition import match
from .state import Store, encode


class Controller:
    def __init__(self, settings):
        self.s = settings
        self.db = Store(settings.state/'queue.sqlite3')
        self.arm = ARM(settings)
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.validation = ThreadPoolExecutor(max_workers=1,thread_name_prefix='output-validation')
        self.futures = {}
        self.status = 'Not connected; camera and masterlist tools are available'
        self.initialized = False
        self.db.invalidate_pending()
        self.db.execute("UPDATE reservations SET state='ripping' WHERE state='validating'")

    def start(self):
        self.thread = threading.Thread(target=self.run,daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        self.validation.shutdown(wait=False,cancel_futures=True)

    def masters(self):
        return [Masterlist.model_validate_json(r['body']) for r in self.db.rows('SELECT body FROM masters')]

    def import_master(self, master):
        with self.lock:
            # Batches snapshot their masterlist; edits apply only to a new batch.
            self.db.execute('INSERT OR REPLACE INTO masters VALUES (?,?)',(master.id,master.model_dump_json()))
        return {'id':master.id,'digest':digest(master),'approved':master.approved}

    def baseline(self):
        if self.db.rows("SELECT 1 FROM batches WHERE state='running'"):
            report = self.arm.inspect()
            self.db.put('preflight',report)
            if report['issues']:
                raise ValueError('; '.join(report['issues']))
        jobs = self.arm.jobs()
        with self.db.connect() as db:
            for job in jobs:
                identity = job_identity(job)
                old = db.execute('SELECT identity FROM seen WHERE job=?',(job['job_id'],)).fetchone()
                if old and old['identity']!=identity:
                    raise ValueError('ARM job identifiers changed; use a fresh queue state for this deployment')
                db.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?)',(job['job_id'],identity,time.time()))
        self.db.invalidate_pending()
        self.initialized = True

    def activate(self, master_id):
        with self.lock:
            report = self.arm.inspect()
            self.db.put('preflight',report)
            if report['issues']:
                raise ValueError('; '.join(report['issues']))
            master = next((m for m in self.masters() if m.id==master_id),None)
            if master is None or not master.approved:
                raise ValueError('Import and approve this masterlist first')
            self.baseline()
            return self.select_batch(master)

    def select_batch(self, master):
        existing = [r for r in self.db.rows("SELECT * FROM batches WHERE state!='cancelled'") if digest(json.loads(r['master']))==digest(master)]
        batch = existing[0]['id'] if existing else str(uuid4())
        with self.db.connect() as db:
            db.execute("UPDATE batches SET state='paused' WHERE state='running'")
            db.execute('INSERT OR REPLACE INTO batches VALUES (?,?,?)',(batch,master.model_dump_json(),'running'))
        self.db.put('active_batch',batch)
        return batch

    def begin_event(self, source):
        return self.db.new_event(source)

    def release_event(self, event):
        self.db.execute('UPDATE events SET released=1 WHERE id=?',(event,))

    def recognition_done(self, event, result):
        matches = match(result,self.masters())
        status = 'ready' if len(matches)==1 and result.get('accepted') else 'review'
        if result.get('accepted') and len(matches)!=1:
            result['reason'] = 'No unique approved edition match; review printed evidence or recapture'
        with self.db.connect() as db:
            old = db.execute('SELECT body,status FROM events WHERE id=?',(event,)).fetchone()
            if old and old['status'] in ('processing','invalidated','expired'):
                body = json.loads(old['body'])
                # Keep late OCR as inspectable evidence, without restoring eligibility.
                eligible = old['status']=='processing'
                body.update(result=result,matches=matches if eligible else [])
                db.execute('UPDATE events SET body=?,status=? WHERE id=?',
                           (encode(body),status if eligible else old['status'],event))

    def review_event(self, event, master_id, disc_id, note, job=None):
        if not note.strip():
            raise ValueError('Record what you confirmed from the retained physical-media evidence')
        with self.lock:
            master = next((m for m in self.masters() if m.id==master_id and m.approved),None)
            if not master or not any(d.id==disc_id for d in master.discs):
                raise ValueError('Select an approved masterlist and its disc')
            with self.db.connect() as db:
                row = db.execute('SELECT * FROM events WHERE id=?',(event,)).fetchone()
                if not row or row['status'] not in ('ready','review'):
                    raise ValueError('This event cannot be reviewed; capture again')
                body = json.loads(row['body'])
                if body.get('source') == 'manual_test' or body.get('result',{}).get('test_only'):
                    raise ValueError('Manual test snapshots cannot authorize ripping; make a fresh automatic capture')
                if not body.get('result',{}).get('frames'):
                    raise ValueError('No retained frame available; recapture')
                target = job if job is not None else row['job']
                if job is not None:
                    detail = self.arm.detail(job)
                    if detail['job']['status']!='manual_paused' or detail['job']['devpath']!=self.s.drive:
                        raise ValueError('Target job is not waiting in the configured drive')
                    if row['job'] not in (None,job):
                        raise ValueError('Evidence already belongs to another insertion')
                    prior = db.execute('SELECT id FROM events WHERE job=? AND id!=?',(job,event)).fetchone()
                    if prior:
                        if db.execute('SELECT 1 FROM reservations WHERE job=?',(job,)).fetchone():
                            raise ValueError('Job already reserved; resolve its reservation first')
                        db.execute("UPDATE events SET job=NULL,status='invalidated' WHERE id=?",(prior['id'],))
                body.update(matches=[{'master':master_id,'disc':disc_id,'confidence':None,'masterlist_sha256':digest(master)}],review={'note':note,'at':time.time(),'authority':'human-confirmed physical evidence'})
                db.execute("UPDATE events SET body=?,status='ready',job=?,released=1 WHERE id=?",(encode(body),target,event))

    def action(self, action, job=None, disc=None):
        with self.lock:
            batch = self.db.get('active_batch')
            if action in ('pause','resume','cancel'):
                if not batch:
                    raise ValueError('No active batch')
                if action=='resume':
                    report = self.arm.inspect()
                    if report['issues']:
                        raise ValueError('; '.join(report['issues']))
                    self.baseline()
                self.db.execute('UPDATE batches SET state=? WHERE id=?',({'pause':'paused','resume':'running','cancel':'cancelled'}[action],batch))
                self.db.invalidate_pending()
            elif action in ('skip','unskip'):
                rows = self.db.rows('SELECT * FROM batches WHERE id=?',(batch,))
                if not rows or disc not in {d['id'] for d in json.loads(rows[0]['master'])['discs']}:
                    raise ValueError('Disc does not belong to active batch')
                if action=='skip':
                    if self.db.rows("SELECT 1 FROM reservations WHERE batch=? AND disc=? AND state NOT IN ('failed','cancelled')",(batch,disc)):
                        raise ValueError('Cannot skip a reserved or completed disc')
                    self.db.execute('INSERT OR IGNORE INTO skipped VALUES (?,?)',(batch,disc))
                else:
                    self.db.execute('DELETE FROM skipped WHERE batch=? AND disc=?',(batch,disc))
            elif action=='retry':
                rows = self.db.rows('SELECT * FROM reservations WHERE job=?',(job,))
                if not rows:
                    raise ValueError('Only queue-owned jobs can be retried')
                detail = self.arm.detail(job)
                if detail['job']['status']=='fail':
                    self.db.execute("UPDATE reservations SET state='failed',error='Re-present and reinsert disc to create a new ARM job' WHERE job=?",(job,))
                elif detail['job']['status']=='success':
                    self.db.execute("UPDATE reservations SET state='ripping',error='' WHERE job=? AND state!='ripped'",(job,))
                elif detail['job']['status']=='manual_paused' and not detail['job'].get('manual_start'):
                    self.db.execute("UPDATE reservations SET state='reserved',error='' WHERE job=? AND state!='ripped'",(job,))
                else:
                    raise ValueError('Wait for a terminal/waiting ARM state before retrying')
            elif action=='cancel_job':
                rows = self.db.rows('SELECT * FROM reservations WHERE job=?',(job,))
                if not rows:
                    raise ValueError('Unrelated jobs cannot be cancelled here')
                detail = self.arm.detail(job)
                if detail['job']['status']!='manual_paused':
                    raise ValueError('Running rip preserved; use ARM to stop it, then retry or reinsert')
                self.arm.call('POST',f'/jobs/{job}/cancel')
                self.db.execute("UPDATE reservations SET state='cancelled' WHERE job=?",(job,))
            else:
                raise ValueError('Unknown action')

    def run(self):
        while not self.stop.is_set():
            self.poll_once()
            self.stop.wait(3)

    def poll_once(self):
        # Camera-only setup is a supported mode, not an ARM polling outage.
        if not self.s.arm_url:
            self.status = 'Camera-only mode; ARM is not configured'
            try:
                self.prune_evidence()
            except Exception as exc:
                self.status = 'Evidence cleanup failed: '+str(exc)
            return
        try:
            with self.lock:
                if not self.initialized:
                    self.baseline()
                self.tick()
                self.status = 'Connected; monitoring the configured drive'
            self.prune_evidence()
        except Exception as exc:
            self.status = str(exc)
            # Recognition captured during a polling outage cannot identify a new insertion reliably.
            self.db.invalidate_pending()
            self.initialized = False

    def tick(self):
        self.db.execute("UPDATE events SET status='expired' WHERE job IS NULL AND created<? AND status IN ('processing','ready','review')",(time.time()-self.s.ttl,))
        drives = self.arm.call('GET','/drives')['drives']
        drive = next((d for d in drives if d['mount']==self.s.drive),None)
        current = drive.get('job_id_current') if drive else None
        if current:
            detail = self.arm.detail(current)
            self.db.pair_insertion(current,job_identity(detail['job']),self.s.ttl)
        batch_rows = self.db.rows('SELECT * FROM batches WHERE id=?',(self.db.get('active_batch',''),))
        running = batch_rows and batch_rows[0]['state']=='running'
        if running:
            self.arm.held()
            if not drive or drive.get('drive_mode')!='auto':
                raise ValueError('Configured ARM drive mode changed')
            if current:
                paired = self.db.rows("SELECT * FROM events WHERE job=? AND status='ready'",(current,))
                if paired and not self.db.rows('SELECT 1 FROM reservations WHERE job=?',(current,)):
                    self.reserve(paired[0],batch_rows[0],detail)
        for reservation in self.db.rows("SELECT * FROM reservations WHERE state NOT IN ('failed','cancelled','review') OR publication='pending' AND state='ripped'"):
            try:
                self.advance(reservation,bool(running),current)
            except Exception as exc:
                # Preserve a newly persisted start intent across a transport error. Next tick reconciles ARM.
                keep_start = reservation['state']=='reserved'
                self.db.execute("UPDATE reservations SET state=CASE WHEN state='ripped' OR (state='starting' AND ?) THEN state ELSE 'review' END,error=? WHERE job=?",(keep_start,str(exc),reservation['job']))

    def reserve(self, event, batch, detail):
        if detail['job']['status']!='manual_paused':
            return
        body = json.loads(event['body'])
        matched = body['matches'][0]
        master = Masterlist.model_validate_json(batch['master'])
        if matched['master']==master.id and matched.get('masterlist_sha256')!=digest(master):
            body['result']['reason']='Recognition matched an edited masterlist, not this batch snapshot; cancel this batch and start the updated list'
            self.db.execute("UPDATE events SET status='review',body=? WHERE id=?",(encode(body),event['id']))
            return
        if matched['master']!=master.id:
            if self.s.switch_policy!='auto':
                self.db.execute("UPDATE events SET status='review' WHERE id=?",(event['id'],))
                body['result']['reason']='Recognised another loaded masterlist; select its batch and explicitly link this waiting job in review'
                self.db.execute('UPDATE events SET body=? WHERE id=?',(encode(body),event['id']))
                return
            master = next(m for m in self.masters() if m.id==matched['master'])
            batch = {'id':self.select_batch(master)}
        disc = next(d for d in master.discs if d.id==matched['disc'])
        try:
            mapping = plan(master,disc,detail)
            payload = {'masterlist':master.model_dump(),'masterlist_sha256':digest(master),
                       'recognition':body,'identity':job_identity(detail['job']), 'observed_label':detail['job'].get('label'),
                       'structure_signature':structure(detail),'source_titles':detail['tracks'],'mapping':mapping}
            self.db.claim(detail['job']['job_id'],batch['id'],disc.id,event['id'],payload)
        except (ValueError, StopIteration) as exc:
            body['result']['reason']=str(exc)
            self.db.execute("UPDATE events SET status='review',body=? WHERE id=?",(encode(body),event['id']))
        except Exception as exc:
            body['result']['reason']='Disc already reserved/completed, or reservation failed: '+str(exc)
            self.db.execute("UPDATE events SET status='review',body=? WHERE id=?",(encode(body),event['id']))

    def advance(self, row, running, current):
        job = row['job']
        payload = json.loads(row['body'])
        detail = self.arm.detail(job)
        if job_identity(detail['job'])!=payload['identity']:
            raise ValueError('ARM job identity changed')
        status = detail['job']['status']
        if row['state']=='ripped':
            self.read_ack(row)
            return
        if status=='fail':
            self.db.execute("UPDATE reservations SET state='failed',error=? WHERE job=?",(str(detail['job'].get('errors') or 'ARM failed; re-present and reinsert to retry'),job))
            return
        if status=='success':
            if row['state'] not in ('ripping','starting','validating'):
                raise ValueError('Job completed outside the queue start sequence; review required')
            if job not in self.futures or self.futures[job].done():
                self.db.execute("UPDATE reservations SET state='validating' WHERE job=?",(job,))
                self.futures[job] = self.validation.submit(self.finish,row,detail)
            return
        if row['state']=='starting':
            if status=='manual_paused' and not detail['job'].get('manual_start'):
                raise ValueError('Start outcome uncertain; inspect ARM and use Retry if it is still waiting')
            self.db.execute("UPDATE reservations SET state='ripping' WHERE job=?",(job,))
            return
        if row['state']=='reserved' and running and current==job and row['batch']==self.db.get('active_batch'):
            if structure(detail)!=payload['structure_signature']:
                raise ValueError('Prescanned title structure changed before configuration')
            master = Masterlist.model_validate(payload['masterlist'])
            preview = self.arm.configure(job,master,payload['mapping'])
            payload['naming_preview']=preview
            self.db.execute("UPDATE reservations SET body=?,state='starting' WHERE job=?",(encode(payload),job))
            # Persist intent before a non-idempotent network operation; never blindly retry start.
            self.arm.held()
            self.arm.call('POST',f'/jobs/{job}/start')
            self.db.execute("UPDATE reservations SET state='ripping' WHERE job=?",(job,))

    def finish(self,row,detail):
        try:
            job = row['job']
            payload = json.loads(self.db.rows('SELECT body FROM reservations WHERE job=?',(job,))[0]['body'])
            master = Masterlist.model_validate(payload['masterlist'])
            disc = next(d for d in master.discs if d.id==row['disc'])
            tracks = {t['track_id']:t for t in detail['tracks']}
            preview = {int(t['track_number']):t for t in payload['naming_preview']['tracks']}
            outputs = []
            base = PurePosixPath(detail['job']['path'])
            prefix = PurePosixPath(self.s.arm_media)
            for entry in payload['mapping']:
                track = tracks[entry['arm_track_id']]
                if not track['ripped'] or track.get('error') or track.get('process') is False:
                    raise ValueError('A required title did not complete successfully')
                rendered = preview[entry['makemkv_id']]
                source = base/PurePosixPath(rendered['rendered_folder'])/(rendered['rendered_title']+'.mkv')
                relative = str(source.relative_to(prefix))
                path = beneath(self.s.media,relative)
                checked = inspect_file(path,disc.inventory.model_dump(),entry['scan_duration'])
                outputs.append(dict(entry,arm_output_path=str(source),media_relative_path=relative,**checked))
            # Recheck authoritative terminal state after reading every file.
            terminal = self.arm.detail(job)
            if terminal['job']['status']!='success' or job_identity(terminal['job'])!=payload['identity']:
                raise ValueError('ARM completion changed during output verification')
            manifest = {'schema_version':1,'status':'ready','errors':[], 'arm_status':'success',
                'batch_id':row['batch'],'arm_job_id':job,'disc_id':row['disc'],
                'masterlist':payload['masterlist'],'masterlist_sha256':payload['masterlist_sha256'],
                'recognition_event':row['event'],'recognition':payload['recognition'],
                'observed_label':payload['observed_label'],'structure_signature':payload['structure_signature'],
                'source_titles':payload['source_titles'],'source_inventory':disc.inventory.model_dump(),
                'outputs':outputs,'library_root_hint':'LIBRARY_HOST configuration', 'preserve_sources':True}
            publish_json(self.s.handover/'ready'/f'{job}.json',manifest)
            self.db.execute("UPDATE reservations SET state='ripped',error='' WHERE job=?",(job,))
        except Exception as exc:
            self.db.execute("UPDATE reservations SET state='review',error=? WHERE job=?",(str(exc),row['job']))

    def read_ack(self,row):
        path = self.s.handover/'acks'/f"{row['job']}.json"
        if not path.exists():
            return
        ack = json.loads(path.read_text('utf-8'))
        manifest = json.loads((self.s.handover/'ready'/f"{row['job']}.json").read_text('utf-8'))
        expected = [{'destination':o['destination'],'sha256':o['sha256']} for o in manifest['outputs']]
        if (ack.get('schema_version')!=1 or ack.get('manifest_sha256')!=hashlib.sha256(canonical(manifest)).hexdigest()
            or ack.get('job')!=row['job'] or ack.get('batch')!=row['batch'] or ack.get('status')!='published' or ack.get('outputs')!=expected):
            raise ValueError('Publication acknowledgement does not match the ready manifest')
        self.db.execute("UPDATE reservations SET publication='published',error='' WHERE job=?",(row['job'],))

    def prune_evidence(self):
        # References used by any reservation are protected, including failed/review jobs.
        cutoff = time.time()-self.s.retention*86400
        protected = set()
        for master in self.masters():
            protected.update(p.removeprefix('recognition:') for p in master.provenance if p.startswith('recognition:'))
        for batch in self.db.rows('SELECT master FROM batches'):
            protected.update(p.removeprefix('recognition:') for p in json.loads(batch['master']).get('provenance',[]) if p.startswith('recognition:'))
        for row in self.db.rows('SELECT id FROM events WHERE created<? AND id NOT IN (SELECT event FROM reservations)',(cutoff,)):
            if row['id'] in protected:
                continue
            directory = self.s.state/'evidence'/row['id']
            if directory.is_dir():
                for path in directory.glob('*.jpg'):
                    path.unlink()
                directory.rmdir()
            self.db.execute("UPDATE events SET status='evidence_expired' WHERE id=?",(row['id'],))

    def snapshot(self):
        events = self.db.rows('SELECT * FROM events ORDER BY created DESC LIMIT 30')
        for e in events:
            e['body']=json.loads(e['body'])
        reservations = self.db.rows('SELECT * FROM reservations ORDER BY job DESC')
        for r in reservations:
            body = json.loads(r.pop('body'))
            r['mapping']=body['mapping']
        batches = self.db.rows('SELECT * FROM batches')
        for b in batches:
            b['master']=json.loads(b['master'])
        return {'controller':self.status,'active_batch':self.db.get('active_batch'),
                'masters':[m.model_dump() for m in self.masters()], 'events':events,'batches':batches,
                'reservations':reservations,'skipped':self.db.rows('SELECT * FROM skipped'),
                'preflight':self.db.get('preflight')}

