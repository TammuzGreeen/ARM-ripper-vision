import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath
from uuid import uuid4

from .arm import ARM, job_identity, plan, structure
from .agreement import MODEL_ORDER, normalize as normalize_printed, preflight as agreement_preflight
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
        self.db.invalidate_pending(include_rejections=True)
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
        self.db.invalidate_pending(include_rejections=True)
        self.initialized = True

    def readiness(self):
        if self.s.arm_url:
            arm = self.arm.inspect()
        else:
            arm = {'ready':False,'issues':['ARM_URL is not configured'],'version':None,
                   'drives':[],'ripping_enabled':None}
        if self.s.recognition_backend == 'ollama-agreement':
            models = agreement_preflight(self.s)
        else:
            models = {'ready':False,'issues':['Automatic workflow requires the two-model agreement backend'],
                      'models':{},'ollama_version':None}
        self.db.put('preflight',arm)
        self.db.put('agreement_preflight',models)
        storage = {}
        for name, path, writable in (('state',self.s.state,True),('queue_media',self.s.media,False),
                                     ('handover',self.s.handover,self.s.fileflows_enabled)):
            try:
                fs=os.statvfs(path)
                storage[name]={'available':path.is_dir(),'writable':os.access(path,os.W_OK),
                               'required_writable':writable,'free_bytes':fs.f_bavail*fs.f_frsize}
            except OSError:
                storage[name]={'available':False,'writable':False,'required_writable':writable,'free_bytes':0}
        storage_issues=[f'{name} storage is unavailable' for name,item in storage.items() if not item['available']]
        storage_issues += [f'{name} storage is not writable' for name,item in storage.items()
                           if item['required_writable'] and not item['writable']]
        return {'arm':arm,'models':models,'storage':storage,'storage_issues':storage_issues,
                'camera_mode':self.s.camera_mode,'fileflows_enabled':self.s.fileflows_enabled,
                'ready':bool(arm.get('ready') and models.get('ready') and not storage_issues)}

    def activate(self, master_id):
        with self.lock:
            if self.s.recognition_backend != 'ollama-agreement':
                raise ValueError('Automatic ripping requires RECOGNITION_BACKEND=ollama-agreement')
            model_check = agreement_preflight(self.s)
            self.db.put('agreement_preflight', model_check)
            if not model_check['ready']:
                raise ValueError('Exact primary/secondary recognition models are not available; batch remains stopped')
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

    def agreement_authorized(self, result):
        if not isinstance(result, dict) or result.get('policy') != 'two-model-priority-field-agreement-v1':
            return False
        agreement = result.get('agreement') or {}
        runs = result.get('runs') or {}
        proofs = agreement.get('validated_candidates') or []
        proof_fields = [proof.get(name) or {} for proof in proofs for name in ('primary','secondary')]
        proof_ok = (len(proofs) == 1 and len(proof_fields) == 2
                    and all(all((not field.get('required', True)) or field.get('status') == 'CORRECT'
                                for field in proof.values())
                              and all(name in proof for name in ('series','season','episodes','titles'))
                              for proof in proof_fields))
        matches = result.get('matches') or []
        match_ok = (len(matches) == 1 and len(proofs) == 1
                    and matches[0].get('master') == proofs[0].get('master')
                    and matches[0].get('disc') == proofs[0].get('disc')
                    and isinstance(matches[0].get('masterlist_sha256'), str))
        return (self.s.recognition_backend == 'ollama-agreement'
                and tuple(result.get('model_tags') or ()) == MODEL_ORDER
                and set(runs) == set(MODEL_ORDER)
                and all(runs.get(tag, {}).get('usable') for tag in MODEL_ORDER)
                and agreement.get('status') == 'PASS' and agreement.get('accepted') is True
                and result.get('accepted') is True and match_ok
                and not agreement.get('reasons') and not agreement.get('field_disagreements')
                and not agreement.get('operational_failures') and proof_ok)

    @staticmethod
    def human_retry_authorized(recognition):
        review = (recognition or {}).get('review') or {}
        matches = (recognition or {}).get('matches') or []
        return (review.get('authority') in ('explicit-human-confirmation-of-fresh-capture',
                                            'human-confirmed-associated-insertion')
                and review.get('capture_event') == (recognition or {}).get('event_id')
                and review.get('masterlist_sha256')
                and len(matches) == 1
                and matches[0].get('master') == review.get('master')
                and matches[0].get('disc') == review.get('disc')
                and matches[0].get('masterlist_sha256') == review.get('masterlist_sha256'))

    @staticmethod
    def _correction_matches_disc(metadata, master, disc):
        if normalize_printed(str(metadata.get('series', ''))) not in {
                normalize_printed(master.series),
                *(normalize_printed(alias) for alias in master.title_aliases)}:
            return False
        try:
            if int(str(metadata.get('season', '')).strip()) != master.season:
                return False
        except (TypeError, ValueError):
            return False
        supplied = str(metadata.get('episodes', '')).strip()
        if not re.fullmatch(r'\d{1,3}(?:\s*[-–—]\s*\d{1,3})?(?:\s*[,;/]\s*\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)*', supplied):
            return False
        observed = set()
        for part in re.split(r'\s*[,;/]\s*', supplied):
            numbers = [int(value) for value in re.findall(r'\d{1,3}', part)]
            first, last = numbers[0], numbers[-1]
            if last < first or last - first > 100:
                return False
            observed.update(range(first, last + 1))
        expected = set()
        for episode in disc.episodes:
            found = re.fullmatch(r'\s*(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?\s*', episode.printed)
            if not found:
                return False
            first, last = int(found[1]), int(found[2] or found[1])
            expected.update(range(first, last + 1))
        if observed != expected:
            return False
        if metadata.get('titles'):
            supplied_titles = {normalize_printed(value) for value in re.split(r'\s*[,;\n]\s*', str(metadata['titles'])) if value.strip()}
            expected_titles = {normalize_printed(episode.title) for episode in disc.episodes if episode.title}
            if supplied_titles != expected_titles:
                return False
        if metadata.get('disc_number'):
            try:
                if int(metadata['disc_number']) != disc.number:
                    return False
            except (TypeError, ValueError):
                return False
        if metadata.get('edition'):
            edition = normalize_printed(str(metadata['edition']))
            if edition not in {normalize_printed(master.edition), normalize_printed(master.edition_name)}:
                return False
        return True

    def recognition_done(self, event, result):
        results = self.masters()
        agreement = result.get('agreement') or {}
        runs = result.get('runs') or {}
        candidate_proofs = agreement.get('validated_candidates') or []
        proof_fields = [proof.get(model_name) or {} for proof in candidate_proofs
                        for model_name in ('primary', 'secondary')]
        complete_proof = (len(candidate_proofs) == 1 and len(proof_fields) == 2
                          and all(all((not field.get('required', True)) or field.get('status') == 'CORRECT'
                                      for field in proof.values())
                                  and all(name in proof for name in ('series', 'season', 'episodes', 'titles'))
                                  for proof in proof_fields))
        exact_pair = (result.get('policy') == 'two-model-priority-field-agreement-v1'
                      and tuple(result.get('model_tags') or ()) == MODEL_ORDER
                      and set(runs) == set(MODEL_ORDER)
                      and all(runs.get(tag, {}).get('usable') for tag in MODEL_ORDER))
        matches = result.get('matches', []) if (
            self.s.recognition_backend == 'ollama-agreement'
            and exact_pair and agreement.get('status') == 'PASS'
            and agreement.get('accepted') is True and not agreement.get('reasons')
            and not agreement.get('field_disagreements') and not agreement.get('operational_failures')
            and complete_proof
        ) else []
        if matches:
            valid_matches = []
            for item in matches:
                master = next((m for m in results if m.id == item.get('master') and m.approved), None)
                disc = next((d for d in master.discs if d.id == item.get('disc')), None) if master else None
                proof_match = (len(candidate_proofs) == 1
                               and candidate_proofs[0].get('master') == item.get('master')
                               and candidate_proofs[0].get('disc') == item.get('disc'))
                if master and disc and proof_match and item.get('masterlist_sha256') == digest(master):
                    valid_matches.append(item)
            matches = valid_matches
        pass_result = len(matches) == 1
        automatic_source = result.get('camera') is not None or result.get('backend') == 'ollama-agreement'
        status = 'ready' if pass_result else 'rejected_for_review' if automatic_source else 'review'
        if not pass_result and status == 'rejected_for_review':
            result['accepted'] = False
            if agreement.get('status') == 'PASS':
                agreement['status'] = 'REJECTED_FOR_REVIEW'
                agreement['accepted'] = False
                invalid_runs = [tag for tag in MODEL_ORDER if not runs.get(tag, {}).get('usable')]
                if invalid_runs:
                    agreement['operational_failures'] = sorted(set(agreement.get('operational_failures', []) + invalid_runs))
                    agreement.setdefault('reasons', []).append('operational_recognition_failure')
                agreement.setdefault('reasons', []).append('controller_fail_closed_validation')
            if not result.get('reason') or result.get('reason') == 'Both models agree on all required priority fields':
                result['reason'] = 'Agreement-only priority-field validation failed; rejected for manual review'
        create_rejection = False
        with self.db.connect() as db:
            old = db.execute('SELECT body,status FROM events WHERE id=?',(event,)).fetchone()
            if old and old['status'] in ('processing','invalidated','expired'):
                body = json.loads(old['body'])
                event_source = body.get('source')
                if event_source in ('vision_test', 'manual_test'):
                    status = 'review'
                elif event_source == 'camera' and not pass_result:
                    status = 'rejected_for_review'
                # Keep late OCR as inspectable evidence, without restoring eligibility.
                eligible = old['status']=='processing'
                body.update(result=result,matches=matches if eligible else [])
                db.execute('UPDATE events SET body=?,status=? WHERE id=?',
                           (encode(body),status if eligible else old['status'],event))
                if eligible and status == 'rejected_for_review':
                    create_rejection = True
        if create_rejection:
            self._ensure_rejection(event, result)

    def _ensure_rejection(self, event, result, *, job=None, drive=None):
        """Persist original model evidence separately from human-entered corrections."""
        event_rows = self.db.rows('SELECT job FROM events WHERE id=?', (event,))
        if event_rows and job is None:
            job = event_rows[0]['job']
        runs = result.get('runs') or {}
        agreement = result.get('agreement') or {}
        body = {
            'schema_version': 1, 'recognition_status': agreement.get('status', 'REJECTED_FOR_REVIEW'),
            'reject_reason': result.get('reason') or '; '.join(agreement.get('reasons') or ['Recognition not authorized']),
            'camera': result.get('camera'), 'image_reference': (result.get('frames') or [{}])[0].get('evidence'),
            'disc_identifier': event,
            'primary_model': runs.get(MODEL_ORDER[0], {}),
            'secondary_model': runs.get(MODEL_ORDER[1], {}),
            'normalized_priority_fields': agreement.get('normalized_priority_fields', {}),
            'field_disagreements': agreement.get('field_disagreements', []),
            'missing_fields': agreement.get('missing_fields', []),
            'wrong_fields': agreement.get('wrong_fields', []),
            'operational_failures': agreement.get('operational_failures', []),
            'reason_codes': agreement.get('reasons', []),
            'model_tags': result.get('model_tags', list(MODEL_ORDER)),
            'model_digests': {tag: runs.get(tag, {}).get('model_digest') for tag in MODEL_ORDER},
            'runtime': {'ollama_version': (result.get('ollama_preflight') or {}).get('ollama_version'),
                        'checked_at': (result.get('ollama_preflight') or {}).get('checked_at'),
                        'backend': result.get('backend')},
            'runtime_seconds': {tag: runs.get(tag, {}).get('runtime_seconds') for tag in MODEL_ORDER},
            'evidence_references': {
                tag: {key: runs.get(tag, {}).get(key) for key in ('request_file', 'response_file')}
                for tag in MODEL_ORDER
            },
            'original_machine_result': result,
            'manual_correction': None, 'correction_history': [], 'field_sources': {},
            'ejection': {'status': 'not_requested', 'error': None},
            'audit_trail': [{'at': time.time(), 'action': 'rejection_created',
                             'source': 'model_agreement_gate', 'reason': result.get('reason')}],
        }
        row = self.db.add_rejection(event, drive or self.s.drive, body, job=job)
        if job is not None and row['job'] is None:
            self.db.update_rejection(row['id'], job=job, ejection_status='not_requested')
        return row

    def save_rejection_review(self, rejection_id, metadata, status='corrected'):
        allowed = {'series', 'season', 'episodes', 'titles', 'edition', 'disc_number',
                   'optical_title', 'selected_content', 'versions', 'notes'}
        if not isinstance(metadata, dict) or set(metadata) - allowed:
            raise ValueError('Unsupported manual metadata fields')
        if status not in ('reviewed', 'corrected'):
            raise ValueError('Review status must be reviewed or corrected')
        if status == 'corrected' and not all(str(metadata.get(k, '')).strip() for k in ('series', 'season', 'episodes')):
            raise ValueError('Corrected metadata requires series, season, and episodes')
        with self.lock:
            row = self.db.rejection(rejection_id)
            if not row:
                raise ValueError('Rejected-rip record not found')
            body = row['body']
            event_rows = self.db.rows('SELECT body FROM events WHERE id=?', (row['event'],))
            event_body = json.loads(event_rows[0]['body']) if event_rows else {}
            prior_auth = event_body.get('review') or {}
            if prior_auth.get('authority') in ('explicit-human-confirmation-of-fresh-capture',
                                                'human-confirmed-associated-insertion'):
                if self.db.rows('SELECT 1 FROM reservations WHERE event=?', (row['event'],)):
                    raise ValueError('Correction is already bound to a reservation; resolve that job before editing')
                event_body.pop('review', None)
                event_body['matches'] = []
                event_body.setdefault('result', {}).pop('review', None)
                event_body['result']['matches'] = []
                self.db.execute("UPDATE events SET body=?,status='rejected_for_review',released=0 WHERE id=?",
                                (encode(event_body), row['event']))
                body.setdefault('audit_trail', []).append({'at': time.time(), 'action': 'prior_human_authorization_revoked_by_correction_edit',
                                                            'revision': len(body.get('correction_history', [])) + 1})
            now = time.time()
            manual = {key: value for key, value in metadata.items()}
            revision = {'fields': manual, 'saved_at': now, 'authority': 'human_verified',
                        'revision': len(body.setdefault('correction_history', [])) + 1}
            body['manual_correction'] = revision
            body['correction_history'].append(revision)
            body['reviewer_timestamp'] = now
            body['final_metadata'] = dict(manual)
            body['field_sources'] = {key: 'human_verified' for key in manual}
            body.setdefault('audit_trail', []).append({'at': now, 'action': 'metadata_corrected' if status == 'corrected' else 'reviewed',
                                                       'source': 'human_verified', 'fields': sorted(manual),
                                                       'revision': revision['revision']})
            self.db.update_rejection(rejection_id, review_status=status, body=body)
            return self.db.rejection(rejection_id)

    def resolve_rejection(self, rejection_id, status='resolved'):
        if status not in ('resolved', 'ignored'):
            raise ValueError('Status must be resolved or ignored')
        with self.lock:
            row = self.db.rejection(rejection_id)
            if not row:
                raise ValueError('Rejected-rip record not found')
            body = row['body']
            if status == 'resolved' and not body.get('final_metadata'):
                raise ValueError('Save reviewed or corrected metadata before resolving this item')
            event_rows = self.db.rows('SELECT body FROM events WHERE id=?', (row['event'],))
            event_body = json.loads(event_rows[0]['body']) if event_rows else {}
            review = event_body.get('review') or {}
            if review.get('authority') in ('explicit-human-confirmation-of-fresh-capture',
                                           'human-confirmed-associated-insertion'):
                if self.db.rows('SELECT 1 FROM reservations WHERE event=?', (row['event'],)):
                    raise ValueError('Authorized correction is already bound to a reservation; resolve that job first')
                event_body.pop('review', None);event_body['matches'] = []
                event_body.setdefault('result', {}).pop('review', None);event_body['result']['matches'] = []
                self.db.execute("UPDATE events SET body=?,status='rejected_for_review',released=0 WHERE id=?",
                                (encode(event_body), row['event']))
                body.setdefault('audit_trail', []).append({'at': time.time(), 'action': 'prior_human_authorization_revoked_by_resolution'})
            now = time.time()
            body.setdefault('audit_trail', []).append({'at': now, 'action': status,
                                                       'source': 'operator',
                                                       'final_metadata': body.get('final_metadata')})
            body['reviewer_timestamp'] = now
            self.db.update_rejection(rejection_id, review_status=status, body=body)
            return self.db.rejection(rejection_id)

    def review_event(self, event, master_id, disc_id, note, job=None):
        if not note.strip():
            raise ValueError('Record what you confirmed from the retained physical-media evidence')
        with self.lock:
            master = next((m for m in self.masters() if m.id==master_id and m.approved),None)
            if not master or not any(d.id==disc_id for d in master.discs):
                raise ValueError('Select an approved masterlist and its disc')
            with self.db.connect() as db:
                row = db.execute('SELECT * FROM events WHERE id=?',(event,)).fetchone()
                if not row or row['status'] not in ('ready','review','rejected_for_review','expired'):
                    raise ValueError('This event cannot be reviewed; capture again')
                body = json.loads(row['body'])
                existing_review = body.get('review') or {}
                if existing_review.get('authority') in ('explicit-human-confirmation-of-fresh-capture',
                                                       'human-confirmed-associated-insertion'):
                    raise ValueError('This human retry authorization is single-use; capture and review a new event instead')
                if body.get('source') in ('manual_test','vision_test') or body.get('result',{}).get('test_only'):
                    raise ValueError('Manual test snapshots cannot authorize ripping; make a fresh automatic capture')
                if not body.get('result',{}).get('frames'):
                    raise ValueError('No retained frame available; recapture')
                rejected_rows = list(db.execute('SELECT id,review_status,body FROM rejected_rips WHERE event=?', (event,)))
                is_rejected_retry = row['status'] in ('rejected_for_review','expired') and body.get('source') == 'camera'
                associated_insertion = bool(is_rejected_retry and row['job'] is not None)
                if is_rejected_retry:
                    if len(rejected_rows) != 1:
                        raise ValueError('Rejected capture audit record is missing or ambiguous')
                    rejection_body = json.loads(rejected_rows[0]['body'])
                    correction = rejection_body.get('final_metadata') or {}
                    if (rejected_rows[0]['review_status'] != 'corrected'
                            or not self._correction_matches_disc(correction, master, next(d for d in master.discs if d.id==disc_id))):
                        raise ValueError('Save a corrected identification matching this approved masterlist disc before continuing')
                    if associated_insertion:
                        if job not in (None, row['job']):
                            raise ValueError('This correction is bound to a different ARM insertion')
                        target_job = row['job']
                        detail = self.arm.detail(target_job)
                        seen = db.execute('SELECT identity FROM seen WHERE job=?',(target_job,)).fetchone()
                        if (not seen or job_identity(detail['job']) != seen['identity']
                                or detail['job'].get('devpath') != self.s.drive
                                or detail['job'].get('status') != 'manual_paused'
                                or detail['job'].get('manual_start')):
                            raise ValueError('The same insertion is no longer verifiably waiting in ARM; remove/re-present and insert a fresh disc')
                        self.arm.held()
                        drives = self.arm.call('GET','/drives').get('drives',[])
                        configured = [drive for drive in drives if drive.get('mount') == self.s.drive]
                        if (len(configured) != 1 or configured[0].get('drive_mode') != 'auto'
                                or configured[0].get('job_id_current') != target_job):
                            raise ValueError('ARM no longer associates this waiting job with the configured drive; review needs a fresh insertion')
                        job = target_job
                    else:
                        if row['job'] is not None or job is not None or time.time() - row['created'] > self.s.ttl:
                            raise ValueError('Rejected capture expired or is not a fresh, unpaired camera capture; present and insert a fresh disc')
                        self.arm.held()
                        drives = self.arm.call('GET','/drives').get('drives',[])
                        configured = [drive for drive in drives if drive.get('mount') == self.s.drive]
                        if (len(configured) != 1 or configured[0].get('drive_mode') != 'auto'
                                or configured[0].get('job_id_current') is not None):
                            raise ValueError('Fresh-capture confirmation requires the configured auto drive to be idle under ARM global pause')
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
                match_row = {'master':master_id,'disc':disc_id,'confidence':None,'masterlist_sha256':digest(master)}
                authority = ('human-confirmed-associated-insertion' if associated_insertion
                             else 'explicit-human-confirmation-of-fresh-capture' if is_rejected_retry
                             else 'human-confirmed physical evidence')
                review = {'note':note,'at':time.time(),'authority':authority,
                          'capture_event':event,'master':master_id,'disc':disc_id,'masterlist_sha256':digest(master)}
                if is_rejected_retry:
                    review['correction_revision'] = (rejection_body.get('manual_correction') or {}).get('revision')
                body.setdefault('result', {})['review'] = review
                body['result']['matches'] = [match_row]
                body['result']['event_id'] = event
                body.update(matches=[match_row],review=review)
                db.execute("UPDATE events SET body=?,status='ready',job=?,released=1 WHERE id=?",(encode(body),target,event))
                if is_rejected_retry:
                    rejection_body.setdefault('audit_trail', []).append({
                        'at': review['at'], 'action': 'human_corrections_confirmed_for_associated_insertion' if associated_insertion else 'fresh_capture_human_retry_authorized',
                        'source_event': event, 'master': master_id, 'disc': disc_id,
                        'masterlist_sha256': digest(master), 'note': note,
                    })
                    rejection_body.setdefault('ejection', {})['status'] = 'not_requested'
                    db.execute("UPDATE rejected_rips SET job=?,ejection_status='not_requested',body=? WHERE id=?",
                               (job,
                                 encode(rejection_body), rejected_rows[0]['id']))

    def human_authorization_current(self, event, result):
        review = (result or {}).get('review') or {}
        if review.get('authority') not in ('explicit-human-confirmation-of-fresh-capture',
                                           'human-confirmed-associated-insertion'):
            return True
        rows = self.db.rows('SELECT review_status,body FROM rejected_rips WHERE event=?', (event,))
        if len(rows) != 1 or rows[0]['review_status'] != 'corrected':
            return False
        body = json.loads(rows[0]['body'])
        revision = (body.get('manual_correction') or {}).get('revision')
        return bool(revision and revision == review.get('correction_revision'))

    def action(self, action, job=None, disc=None):
        with self.lock:
            batch = self.db.get('active_batch')
            if action in ('pause','resume','cancel'):
                if not batch:
                    raise ValueError('No active batch')
                if action=='resume':
                    if self.s.recognition_backend != 'ollama-agreement':
                        raise ValueError('Automatic ripping requires RECOGNITION_BACKEND=ollama-agreement')
                    model_check = agreement_preflight(self.s)
                    self.db.put('agreement_preflight', model_check)
                    if not model_check['ready']:
                        raise ValueError('Exact primary/secondary recognition models are not available; batch remains paused')
                    report = self.arm.inspect()
                    if report['issues']:
                        raise ValueError('; '.join(report['issues']))
                    self.baseline()
                self.db.execute('UPDATE batches SET state=? WHERE id=?',({'pause':'paused','resume':'running','cancel':'cancelled'}[action],batch))
                self.db.invalidate_pending(include_rejections=True)
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
                    payload = json.loads(rows[0]['body'])
                    if not payload.get('start_request_acknowledged_at'):
                        raise ValueError('ARM success has no durable queue-start acknowledgement; manual reconciliation required')
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
            self.db.invalidate_pending(include_rejections=True)
            self.initialized = False

    def tick(self):
        self.db.execute("UPDATE events SET status='expired' WHERE job IS NULL AND created<? AND status IN ('processing','ready','review')",(time.time()-self.s.ttl,))
        drives = self.arm.call('GET','/drives')['drives']
        drive = next((d for d in drives if d['mount']==self.s.drive),None)
        current = drive.get('job_id_current') if drive else None
        if current:
            detail = self.arm.detail(current)
            self.db.pair_insertion(current,job_identity(detail['job']),self.s.ttl)
            rejected = self.db.rows("SELECT r.* FROM rejected_rips r JOIN events e ON e.id=r.event WHERE e.job=? AND r.review_status NOT IN ('resolved','ignored')", (current,))
            if rejected:
                # Keep the same ARM insertion under the existing global pause.
                # A human may correct this event and explicitly continue; setup
                # must not cancel/eject a disc merely because models disagreed.
                pass
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
        for reservation in self.db.rows("SELECT * FROM reservations WHERE state NOT IN ('failed','cancelled','review') OR publication IN ('pending','failed') AND state='ripped'"):
            try:
                self.advance(reservation,bool(running),current)
            except Exception as exc:
                # Preserve a newly persisted start intent across a transport error. Next tick reconciles ARM.
                keep_start = reservation['state']=='reserved'
                self.db.execute("UPDATE reservations SET state=CASE WHEN state='ripped' OR (state='starting' AND ?) THEN state ELSE 'review' END,publication=CASE WHEN state='ripped' AND publication IN ('pending','failed') THEN 'failed' ELSE publication END,error=? WHERE job=?",(keep_start,str(exc),reservation['job']))

    def reject_inserted(self, row, drive, job):
        """Stop an unauthorized waiting ARM job, then request tray ejection via ARM."""
        record = self.db.rejection(row['id'])
        body = record['body']
        try:
            event_rows = self.db.rows('SELECT job,status FROM events WHERE id=?', (record['event'],))
            seen_rows = self.db.rows('SELECT identity FROM seen WHERE job=?', (job,))
            if (not event_rows or event_rows[0]['job'] != job
                    or event_rows[0]['status'] != 'rejected_for_review' or not seen_rows):
                raise ValueError('Rejected capture is not uniquely associated with this ARM insertion; eject was not attempted')
            expected_identity = seen_rows[0]['identity']
            current_detail = self.arm.detail(job)
            if job_identity(current_detail['job']) != expected_identity:
                raise ValueError('ARM job identity differs from the paired physical insertion; eject was not attempted')
            fresh_drives = self.arm.call('GET', '/drives').get('drives', [])
            same_mount = [item for item in fresh_drives if item.get('mount') == self.s.drive]
            if (len(same_mount) != 1 or same_mount[0].get('drive_id') != drive.get('drive_id')
                    or same_mount[0].get('job_id_current') != job):
                raise ValueError('Configured drive no longer reports the paired job; eject was not attempted')
            drive_id = same_mount[0].get('drive_id')
            self.arm.held()
            self.arm.cancel_waiting(job)
            # ARM eject is addressed by drive ID, not job ID. Reconfirm the
            # association after cancellation so a replaced disc is never targeted.
            refreshed = self.arm.call('GET', '/drives').get('drives', [])
            current_drive = next((item for item in refreshed if item.get('mount') == self.s.drive), None)
            post_cancel = self.arm.detail(job)
            if (not current_drive or current_drive.get('drive_id') != drive_id
                    or current_drive.get('job_id_current') != job
                    or job_identity(post_cancel['job']) != expected_identity):
                raise ValueError('Drive/job association changed after cancellation; eject was not attempted')
            self.arm.eject_drive(drive_id)
            body.setdefault('ejection', {}).update(status='ejected', error=None, drive_id=drive_id,
                                                   completed_at=time.time())
            body.setdefault('audit_trail', []).append({'at': time.time(), 'action': 'waiting_job_cancelled_and_drive_ejected',
                                                        'job': job, 'drive_id': drive_id,
                                                        'source': 'verified_arm_api'})
            self.db.update_rejection(row['id'], job=job, ejection_status='ejected', body=body)
        except Exception as exc:
            body.setdefault('ejection', {}).update(status='failed', error=str(exc), job=job)
            body.setdefault('audit_trail', []).append({'at': time.time(), 'action': 'ejection_failed',
                                                        'job': job, 'error': str(exc)})
            self.db.update_rejection(row['id'], job=job, ejection_status='failed', body=body)
            self.status = 'Rejected disc is held for safety; ARM cancel/eject failed: ' + str(exc)

    def reserve(self, event, batch, detail):
        if detail['job']['status']!='manual_paused':
            return
        body = json.loads(event['body'])
        if not self.human_authorization_current(event['id'], body.get('result')):
            body['result']['reason'] = 'Human correction changed or was resolved after authorization; confirm the current correction again'
            self.db.execute("UPDATE events SET status='review',released=0,body=? WHERE id=?", (encode(body),event['id']))
            return
        if not (self.agreement_authorized(body.get('result')) or self.human_retry_authorized(body.get('result'))):
            body['result'] = body.get('result') or {}
            body['result']['reason'] = 'Stored recognition does not contain a complete exact-model agreement proof; automatic rip blocked'
            self.db.execute("UPDATE events SET status='review',body=? WHERE id=?", (encode(body),event['id']))
            return
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
            # `starting` is persisted before the non-idempotent POST. A success
            # observed in that state may have been initiated outside this queue,
            # or the response may have been lost; never treat intent as proof.
            if (row['state'] not in ('ripping','validating')
                    or not payload.get('start_request_acknowledged_at')):
                raise ValueError('Job completed outside the queue start sequence; review required')
            if job not in self.futures or self.futures[job].done():
                self.db.execute("UPDATE reservations SET state='validating' WHERE job=?",(job,))
                self.futures[job] = self.validation.submit(self.finish,row,detail)
            return
        if row['state']=='starting':
            if status=='manual_paused' and not detail['job'].get('manual_start'):
                raise ValueError('Start outcome uncertain; inspect ARM and use Retry if it is still waiting')
            if not payload.get('start_request_acknowledged_at'):
                raise ValueError('ARM start intent has no returned queue-start acknowledgement; manual review required')
            self.db.execute("UPDATE reservations SET state='ripping' WHERE job=?",(job,))
            return
        if row['state']=='reserved' and running and current==job and row['batch']==self.db.get('active_batch'):
            saved_result = (payload.get('recognition') or {}).get('result')
            if not self.human_authorization_current(row['event'], saved_result):
                raise ValueError('Human correction changed or was resolved after reservation; rip start blocked')
            if not (self.agreement_authorized(saved_result) or self.human_retry_authorized(saved_result)):
                raise ValueError('Stored recognition lacks exact-model agreement or explicit fresh-capture human authorization; rip start blocked')
            if structure(detail)!=payload['structure_signature']:
                raise ValueError('Prescanned title structure changed before configuration')
            master = Masterlist.model_validate(payload['masterlist'])
            preview = self.arm.configure(job,master,payload['mapping'])
            payload['naming_preview']=preview
            self.db.execute("UPDATE reservations SET body=?,state='starting' WHERE job=?",(encode(payload),job))
            # Persist intent before a non-idempotent network operation; never blindly retry start.
            self.arm.held()
            self.arm.call('POST',f'/jobs/{job}/start')
            payload['start_request_acknowledged_at'] = time.time()
            self.db.execute("UPDATE reservations SET body=?,state='ripping' WHERE job=?",(encode(payload),job))

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
                # A pre-upgrade reservation has no output-level inventory. Preserve
                # its legacy disc-default semantics while new plans persist the
                # effective per-title inventory explicitly.
                inventory = entry.get('inventory',disc.inventory.model_dump())
                checked = inspect_file(path,inventory,entry['scan_duration'])
                outputs.append(dict(entry,inventory=inventory,arm_output_path=str(source),
                                    media_relative_path=relative,**checked))
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
            if self.s.fileflows_enabled:
                publish_json(self.s.handover/'ready'/f'{job}.json',manifest)
            else:
                manifest['handoff_status'] = 'disabled_for_local_test'
                publish_json(self.s.state/'finished'/f'{job}.json',manifest)
            publication = 'pending' if self.s.fileflows_enabled else 'disabled'
            self.db.execute("UPDATE reservations SET state='ripped',publication=?,error='' WHERE job=?",(publication,job))
        except Exception as exc:
            self.db.execute("UPDATE reservations SET state='review',error=? WHERE job=?",(str(exc),row['job']))

    def read_ack(self,row):
        if not self.s.fileflows_enabled:
            return
        path = self.s.handover/'acks'/f"{row['job']}.json"
        if not path.exists():
            if not self.s.fileflows_enabled and row['publication'] == 'pending':
                self.db.execute("UPDATE reservations SET publication='disabled' WHERE job=?",(row['job'],))
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
        protected.update(row['event'] for row in self.db.rows('SELECT event FROM rejected_rips'))
        for row in self.db.rows('SELECT id FROM events WHERE created<? AND id NOT IN (SELECT event FROM reservations)',(cutoff,)):
            if row['id'] in protected:
                continue
            directory = self.s.state/'evidence'/row['id']
            if directory.is_dir():
                for path in directory.iterdir():
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                if directory.is_dir() and not any(directory.iterdir()):
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
            r['masterlist']=body.get('masterlist')
            r['recognition']=body.get('recognition')
            r['arm_start_acknowledged']=bool(body.get('start_request_acknowledged_at'))
            r['naming_preview']=body.get('naming_preview')
            manifest_path=((self.s.handover/'ready') if self.s.fileflows_enabled else
                           (self.s.state/'finished'))/f"{r['job']}.json"
            if manifest_path.is_file():
                try:
                    manifest=json.loads(manifest_path.read_text('utf-8'))
                    r['finished_summary']={key:manifest.get(key) for key in
                        ('status','arm_status','recognition_event','recognition','outputs','errors','disc_id')}
                except (OSError,ValueError):
                    r['finished_summary']={'status':'manifest_unreadable'}
            else:
                r['finished_summary']=None
        batches = self.db.rows('SELECT * FROM batches')
        for b in batches:
            b['master']=json.loads(b['master'])
        storage = {}
        for name, path, writable in (('state', self.s.state, True),
                                     ('queue_media', self.s.media, False),
                                     ('handover', self.s.handover, self.s.fileflows_enabled)):
            try:
                stat = path.stat()
                fs = os.statvfs(path)
                storage[name] = {'available': path.is_dir(), 'writable': bool(os.access(path, os.W_OK)),
                                 'required_writable': writable,
                                 'free_bytes': fs.f_bavail * fs.f_frsize,
                                 'device': stat.st_dev}
            except OSError:
                storage[name] = {'available': False, 'writable': False,
                                 'required_writable': writable, 'free_bytes': 0}
        return {'controller':self.status,'active_batch':self.db.get('active_batch'),
                'masters':[m.model_dump() for m in self.masters()], 'events':events,'batches':batches,
                'reservations':reservations,'skipped':self.db.rows('SELECT * FROM skipped'),
                'preflight':self.db.get('preflight'),
                'agreement_preflight':self.db.get('agreement_preflight'),
                'storage':storage, 'fileflows_enabled':self.s.fileflows_enabled}
