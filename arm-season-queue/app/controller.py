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
from .agreement import (MODEL_ORDER, evaluate as evaluate_agreement,
                        normalize as normalize_printed, preflight as agreement_preflight)
from .formats import Masterlist, digest
from .dry_run import build_plan as build_dry_run_plan, parse_info
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
        if not settings.dry_run_only:
            self.db.invalidate_pending(include_rejections=True)
            self.db.execute("UPDATE reservations SET state='ripping' WHERE state='validating'")
        else:
            self.recover_interrupted_dry_run_capture()

    def recover_interrupted_dry_run_capture(self):
        """Re-arm only a dry-run presentation interrupted before any result."""
        run = self.db.get('active_dry_run')
        if not run or run.get('status') != 'recognizing' or run.get('recognition') is not None:
            return
        event = run.get('event')
        if event:
            rows = self.db.rows('SELECT body FROM events WHERE id=?', (event,))
            if rows:
                body = json.loads(rows[0]['body'])
                body['dry_run_capture_lifecycle'] = {
                    'eligible':False, 'reason':'service_restarted_before_camera_capture_or_recognition',
                    'recorded_at':time.time(),
                }
                self.db.execute("UPDATE events SET body=?,status='invalidated' WHERE id=?",
                                (encode(body), event))
        run.setdefault('capture_attempts', []).append({
            'event':event, 'status':'interrupted',
            'reason':'service restarted before camera capture or recognition completed',
            'recorded_at':time.time(),
        })
        run['event'] = None
        run['recognition'] = None
        run['status'] = 'awaiting_capture'
        self.db.put('active_dry_run', run)

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
            if self.s.dry_run_only:
                raise ValueError('This deployment is locked to dry-run mode; ARM jobs cannot be configured or started')
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
        run = self.db.get('active_dry_run')
        dry_camera = bool(self.s.dry_run_only and run and run.get('status') == 'awaiting_capture'
                          and source == 'camera')
        event_source = ('dry_run_camera' if dry_camera else f'dry_run_{source}'
                        if self.s.dry_run_only else source)
        event = self.db.new_event(event_source, isolated=self.s.dry_run_only)
        if dry_camera:
            run.update(event=event, status='recognizing', capture_source='live_camera', created=time.time())
            self.db.put('active_dry_run', run)
        return event

    def release_event(self, event):
        run = self.db.get('active_dry_run')
        if run and run.get('event') == event:
            return
        self.db.execute('UPDATE events SET released=1 WHERE id=?',(event,))

    def start_dry_run(self, master_id, disc_id):
        with self.lock:
            if not self.s.dry_run_only:
                raise ValueError('DRY_RUN_ONLY must be enabled before beginning a supervised dry run')
            if self.s.fileflows_enabled:
                raise ValueError('FileFlows must be disabled for a dry run')
            if self.db.get('active_batch') or self.db.rows("SELECT 1 FROM reservations WHERE state NOT IN ('failed','cancelled')"):
                raise ValueError('A production batch or reservation exists; dry run refused')
            current = self.db.get('active_dry_run')
            if current and current.get('status') not in ('assessed','abandoned'):
                raise ValueError('Finish or explicitly abandon the current dry run first')
            readiness = self.arm.inspect()
            self.db.put('preflight', readiness)
            if readiness['issues']:
                raise ValueError('; '.join(readiness['issues']))
            if self.arm.jobs():
                raise ValueError('ARM has jobs; dry run requires an empty job queue')
            models = agreement_preflight(self.s)
            self.db.put('agreement_preflight', models)
            if not models['ready']:
                raise ValueError('Configured recognition pipeline is not ready: ' + '; '.join(models['issues']))
            master = next((m for m in self.masters() if m.id == master_id and m.approved), None)
            disc = next((d for d in master.discs if d.id == disc_id), None) if master else None
            if not master or not disc:
                raise ValueError('Choose an approved masterlist and one of its discs')
            if self.s.recognition_backend != 'ollama-agreement':
                raise ValueError('Dry run requires the configured production agreement recognition pipeline')
            run = {'id':str(uuid4()), 'status':'awaiting_capture', 'created':time.time(),
                   'master':master_id, 'masterlist_sha256':digest(master), 'disc':disc_id,
                   'assessment':None, 'recognition':None, 'correction':None, 'scan':None, 'plan':None}
            self.db.put('active_dry_run', run)
            return run

    def save_dry_run_correction(self, run_id, correction):
        allowed = {'series','season','disc_number','episodes','titles','edition','notes'}
        if not isinstance(correction, dict) or set(correction)-allowed:
            raise ValueError('Unsupported dry-run correction fields')
        with self.lock:
            run = self._dry_run(run_id)
            if run.get('status') not in ('review','scan_received','plan_blocked'):
                raise ValueError('Corrections are available only after camera recognition has completed')
            if not all(str(correction.get(k,'')).strip() for k in ('series','season','disc_number','episodes','notes')):
                raise ValueError('An authoritative correction requires series, season, disc number, episodes and review notes')
            master, disc = self._dry_run_master(run)
            if not self._correction_matches_disc(correction, master, disc):
                raise ValueError('Human correction conflicts with the approved masterlist/disc selected for this dry run')
            run['correction'] = {'source':'human', 'entered_at':time.time(), 'fields':correction}
            run['status'] = 'scan_received' if run.get('scan') else 'review'
            if run.get('scan'):
                self._rebuild_dry_run_plan(run)
            self.db.put('active_dry_run', run)
            return run

    def receive_dry_run_scan(self, run_id, text, context):
        if not isinstance(context, dict) or context.get('device') != self.s.drive:
            raise ValueError('Scan must identify the configured optical device')
        if context.get('tray_closed') is not True or context.get('medium_ready') is not True:
            raise ValueError('Scan evidence does not verify a closed tray and ready medium')
        if context.get('media_changed_during_scan') is not False:
            raise ValueError('Drive reports removal/replacement during scan; association invalidated')
        if (context.get('same_insertion') is not True or context.get('operation') != 'makemkvcon-info-only'
                or context.get('media_output_created') is not False):
            raise ValueError('Scan is not attested as the same insertion and MakeMKV info-only with no media output')
        with self.lock:
            run = self._dry_run(run_id)
            if context.get('capture_event') != run.get('event'):
                raise ValueError('Scan belongs to another camera event; dry-run association invalidated')
            marker = f"# DRY_RUN_CAPTURE_EVENT:{run.get('event')}\n"
            if not isinstance(text, str) or not text.startswith(marker):
                raise ValueError('Info report is not bound to this fresh dry-run camera event')
            info = parse_info(text[len(marker):])
            if run.get('status') not in ('review','recognizing','awaiting_scan','scan_received','plan_blocked'):
                raise ValueError('Dry run is not in a state that accepts scan evidence')
            if run.get('event') is None or run.get('recognition') is None:
                # Failed recognition remains a valid dry-run outcome; callers may
                # explicitly use a separate non-empty failure record.
                raise ValueError('Wait for recognition to complete or record its bounded failure first')
            run['scan'] = {'received_at':time.time(), 'context':context, 'summary':{
                'disc_type':info['disc'].get('type'), 'disc_label':info['disc'].get('label'),
                'title_count':info['title_count'],
                'titles':[{'makemkv_id':t['makemkv_id'],'dvd_title':t.get('dvd_title'),
                           'angle_count':t.get('angle_count'),'duration':t.get('duration'),
                           'chapters':t.get('chapters'),'chapter_range':t.get('chapter_range'),
                           'size':t.get('size'),'size_bytes':t.get('size_bytes'),
                           'video':t.get('video'),'audio':t['audio'],'subtitles':t['subtitles']}
                          for _,t in sorted(info['disc']['titles'].items())]},
                'info':info}
            run['status'] = 'scan_received'
            self._rebuild_dry_run_plan(run)
            self.db.put('active_dry_run', run)
            return run

    def _dry_run(self, run_id):
        run = self.db.get('active_dry_run')
        if not run or run.get('id') != run_id:
            raise ValueError('Dry-run record not found')
        return run

    def _dry_run_master(self, run):
        master = next((m for m in self.masters() if m.id == run['master']), None)
        disc = next((d for d in master.discs if d.id == run['disc']), None) if master else None
        if not master or digest(master) != run['masterlist_sha256'] or not disc:
            raise ValueError('Approved masterlist changed during dry run; plan invalidated')
        return master, disc

    def _rebuild_dry_run_plan(self, run):
        master, disc = self._dry_run_master(run)
        recognition = run.get('recognition') or {}
        matches = recognition.get('matches') or []
        matched = len(matches) == 1 and matches[0].get('master') == master.id and matches[0].get('disc') == disc.id
        human = run.get('correction') and run['correction'].get('source') == 'human'
        blockers = []
        if not matched and not human:
            blockers.append('Camera recognition has no unique match to this approved masterlist disc; manual review required')
        output = build_dry_run_plan(master, disc, run['scan']['info'], self.s.dry_run_output_root)
        output['identity_source'] = 'human correction' if human and not matched else 'camera agreement' if matched else 'unresolved camera result'
        output['camera_matches'] = matches
        output['human_correction'] = run.get('correction')
        output['blockers'] = list(dict.fromkeys(blockers + output['blockers']))
        output['plan_status'] = 'blocked' if output['blockers'] else 'provisional dry-run preview'
        run['plan'] = output
        run['status'] = 'review'

    def reevaluate_dry_run_recognition(self, run_id):
        """Re-run deterministic agreement/matching over retained model text only."""
        with self.lock:
            run = self._dry_run(run_id)
            if not run.get('scan') or run.get('assessment'):
                raise ValueError('Re-evaluation requires an unassessed dry run with its scan attached')
            recognition = run.get('recognition') or {}
            event = run.get('event')
            if recognition.get('event') != event or not recognition.get('result'):
                raise ValueError('Retained recognition evidence is missing or no longer associated')
            result = dict(recognition['result'])
            runs = result.get('runs') or {}
            if (tuple(result.get('model_tags') or ()) != MODEL_ORDER
                    or set(runs) != set(MODEL_ORDER)
                    or any(not runs[tag].get('usable') or not runs[tag].get('transcription')
                           for tag in MODEL_ORDER)):
                raise ValueError('Both original usable model transcriptions are required')
            evidence = self.s.state / 'evidence' / event
            filenames = ('agreement-primary-30b-stream.jsonl',
                         'agreement-secondary-7b-stream.jsonl')
            for tag, filename in zip(MODEL_ORDER, filenames):
                if runs[tag].get('response_file') != filename:
                    raise ValueError('Retained response file association is invalid')
                chunks = []
                for line in (evidence / filename).read_text('utf-8').splitlines():
                    payload = json.loads(line)
                    content = (payload.get('message') or {}).get('content')
                    if isinstance(content, str):
                        chunks.append(content)
                if ''.join(chunks).strip() != runs[tag]['transcription']:
                    raise ValueError('Retained raw response does not match its original transcription')
            prior = result.get('agreement')
            history = list(result.get('agreement_revisions') or [])
            if prior:
                history.append({'policy':prior.get('policy'), 'status':prior.get('status'),
                                'reasons':prior.get('reasons'), 'normalized_priority_fields':prior.get('normalized_priority_fields'),
                                'field_disagreements':prior.get('field_disagreements')})
            result['agreement'] = evaluate_agreement(
                {tag: runs[tag]['transcription'] for tag in MODEL_ORDER}, runs, self.masters())
            result['agreement_revisions'] = history
            result['accepted'] = result['agreement']['accepted']
            result['matches'] = result['agreement'].get('matches') or []
            result['policy'] = result['agreement']['policy']
            result['reason'] = '; '.join(result['agreement']['reasons']) or 'Both models agree on identifying fields'
            # Build and validate the full preview before changing the stored
            # recognition result, so a planner failure cannot partially apply
            # the reevaluation.
            proposed = dict(run)
            proposed['recognition'] = {'matches': result['agreement'].get('matches') or []}
            self._rebuild_dry_run_plan(proposed)
            self.recognition_done(event, result)
            refreshed = self._dry_run(run_id)
            proposed_matches = proposed['recognition']['matches']
            stored_matches = (refreshed.get('recognition') or {}).get('matches') or []
            if proposed_matches != stored_matches:
                self._rebuild_dry_run_plan(refreshed)
            else:
                refreshed['plan'] = proposed['plan']
            self.db.put('active_dry_run', refreshed)
            return refreshed

    def assess_dry_run(self, run_id, outcome, notes=''):
        if outcome not in ('test_successful','needs_changes'):
            raise ValueError('Assessment must be Test successful or Needs changes')
        with self.lock:
            run = self._dry_run(run_id)
            if not run.get('scan') or not run.get('plan'):
                raise ValueError('Final review requires capture, recognition outcome, scan and proposed plan')
            run['assessment'] = {'outcome':outcome,'notes':str(notes)[:2000],'assessed_at':time.time()}
            run['status'] = 'assessed'
            self.db.put('active_dry_run', run)
            return run

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
        event_rows = self.db.rows('SELECT body,status,created FROM events WHERE id=?',(event,))
        if event_rows and str(json.loads(event_rows[0]['body']).get('source', '')).startswith('dry_run_'):
            old_body = json.loads(event_rows[0]['body'])
            run = self.db.get('active_dry_run')
            if not run or run.get('event') != event or old_body.get('source') != 'dry_run_camera':
                old_body.update(result=result, matches=[])
                old_body['dry_run_association'] = {'eligible':False,'reason':'dry-run session no longer active'}
                self.db.execute("UPDATE events SET body=?,status='invalidated' WHERE id=?",(encode(old_body),event))
                return
            old_body.update(result=result, matches=matches)
            self.db.execute("UPDATE events SET body=?,status='review' WHERE id=?",(encode(old_body),event))
            run['recognition'] = {'event':event,'status':'matched' if len(matches)==1 else 'unmatched',
                                  'matches':matches,'result':result,'completed_at':time.time()}
            run['status'] = 'review'
            self.db.put('active_dry_run',run)
            return
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
            old = db.execute('SELECT body,status,created FROM events WHERE id=?',(event,)).fetchone()
            if old and old['status'] in ('processing','invalidated','expired'):
                body = json.loads(old['body'])
                event_source = body.get('source')
                if event_source in ('vision_test', 'manual_test'):
                    status = 'review'
                elif event_source == 'camera' and not pass_result:
                    status = 'rejected_for_review'
                # A slow inference must not revive a capture whose TTL elapsed
                # before the periodic expiry sweep ran. Retain its full model
                # evidence, but make the late result unambiguously ineligible.
                expired_during_processing = (old['status']=='processing'
                                             and time.time()-old['created'] >= self.s.ttl)
                eligible = old['status']=='processing' and not expired_during_processing
                if not eligible:
                    lifecycle_reason = ('capture_expired_before_recognition_completed'
                                        if expired_during_processing or old['status']=='expired'
                                        else 'capture_invalidated_before_recognition_completed')
                    body['recognition_lifecycle'] = {
                        'eligible': False, 'reason': lifecycle_reason,
                        'completed_at': time.time(),
                    }
                # Keep the model result byte-for-byte semantically intact for
                # audit. Event status and outer matches are the eligibility gate.
                body.update(result=result,matches=matches if eligible else [])
                db.execute('UPDATE events SET body=?,status=? WHERE id=?',
                           (encode(body),status if eligible else ('expired' if expired_during_processing else old['status']),event))
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
        if self.s.dry_run_only:
            raise ValueError('DRY_RUN_ONLY blocks human authorization and queue reservation')
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
            if self.s.dry_run_only:
                raise ValueError('DRY_RUN_ONLY blocks queue actions; this deployment cannot dispatch ARM work')
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
        if self.s.dry_run_only:
            self.status = 'Dry-run-only; ARM insertion polling and production queue mutations are disabled'
            try:
                self.prune_evidence()
            except Exception as exc:
                self.status = 'Dry-run evidence cleanup failed: ' + str(exc)
            return
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
            if not self.s.dry_run_only:
                self.db.invalidate_pending(include_rejections=True)
            self.initialized = False

    def tick(self):
        # Local qualification deployments set DRY_RUN_ONLY at the execution
        # boundary. Do not pair insertions, configure jobs, or start work.
        if self.s.dry_run_only:
            return
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
        dry_run = self.db.get('active_dry_run')
        if dry_run and dry_run.get('event'):
            protected.add(dry_run['event'])
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
        events = [e for e in events if not str(e['body'].get('source', '')).startswith('dry_run_')]
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
                 'storage':storage, 'fileflows_enabled':self.s.fileflows_enabled,
                 'dry_run_only':self.s.dry_run_only,
                 'dry_run':self.db.get('active_dry_run')}
