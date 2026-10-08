import httpx

from .config import REFERENCE_SHA
from .formats import digest, output_destination, rip_readiness


def enabled(value):
    return str(value).casefold() in ('true','1','yes')


class ARM:
    def __init__(self, settings):
        self.s = settings
        headers = {'X-Api-Version':'2'}
        if settings.arm_token:
            headers['Authorization'] = 'Bearer '+settings.arm_token
        self.client = httpx.Client(timeout=20, headers=headers, follow_redirects=False, trust_env=False)

    def call(self, method, path, body=None):
        if not self.s.arm_url:
            raise ValueError('Set ARM_URL to the ARM Neu API base address')
        response = self.client.request(method,self.s.arm_url+'/api/v1'+path,json=body)
        response.raise_for_status()
        data = response.json()
        if isinstance(data,dict) and data.get('success') is False:
            raise ValueError('ARM rejected operation: '+str(data.get('error','unknown error')))
        return data

    def inspect(self):
        version = self.call('GET','/system/version')
        pause = self.call('GET','/system/ripping-enabled')
        config = self.call('GET','/settings/config')['config']
        drives = self.call('GET','/drives')['drives']
        selected = [d for d in drives if d['mount']==self.s.drive]
        issues = []
        if version.get('arm_version') != self.s.expected_version or self.s.expected_version != '19.1.0':
            issues.append('This adapter requires the inspected ARM 19.1.0 source/API profile')
        if self.s.source_verified != REFERENCE_SHA:
            issues.append('Deployed source has not been checked against the reference: see docs/compatibility.md')
        if len(selected)!=1 or selected[0].get('drive_mode')!='auto':
            issues.append('Select one existing ARM drive in auto mode; manual mode introduces a second timed wait')
        if not enabled(config.get('SKIP_TRANSCODE')) or config.get('RIPMETHOD')!='mkv':
            issues.append('ARM must use RIPMETHOD=mkv and SKIP_TRANSCODE=true')
        if enabled(config.get('DELRAWFILES')):
            issues.append('Set DELRAWFILES=false for testing')
        if enabled(config.get('MAINFEATURE')) or str(config.get('MINLENGTH'))!='0' or str(config.get('MAXLENGTH'))!='99998':
            issues.append('Set MAINFEATURE=false, MINLENGTH=0 and MAXLENGTH=99998 before creating queue jobs')
        if pause.get('ripping_enabled') is not False:
            issues.append('ARM global ripping pause must be enabled before activating a batch')
        return {'version':version, 'drives':drives, 'ripping_enabled':pause.get('ripping_enabled'),
                'issues':issues, 'ready':not issues, 'reference_sha':REFERENCE_SHA}

    def held(self):
        if self.call('GET','/system/ripping-enabled').get('ripping_enabled') is not False:
            raise ValueError('ARM global pause was released; controller stopped assigning jobs')

    def cancel_waiting(self, job):
        """Cancel only the inspected ARM waiting job; never touch optical devices locally."""
        detail = self.detail(job)
        if detail['job'].get('status') != 'manual_paused' or detail['job'].get('manual_start'):
            raise ValueError('Rejected disc job is no longer safely waiting; eject was not attempted')
        return self.call('POST', f'/jobs/{job}/cancel')

    def eject_drive(self, drive_id):
        """Use the source-verified ARM drive API for an explicit tray eject."""
        if isinstance(drive_id, bool) or not isinstance(drive_id, int) or drive_id < 1:
            raise ValueError('ARM did not provide a valid drive identifier; eject was not attempted')
        self.held()
        return self.call('POST', f'/drives/{drive_id}/eject', {'method': 'eject'})

    def detail(self, job):
        return self.call('GET',f'/jobs/{job}/detail')

    def jobs(self):
        page, result = 1, []
        while True:
            data = self.call('GET',f'/jobs/paginated?page={page}&per_page=100')
            result.extend(data['jobs'])
            if page>=data['pages']:
                return result
            page += 1

    def configure(self, job, master, mapping):
        self.held()
        detail = self.detail(job)
        if detail['job']['status']!='manual_paused' or detail['job'].get('manual_start'):
            raise ValueError('Job is no longer safely waiting')
        if not enabled(detail['config'].get('SKIP_TRANSCODE')):
            raise ValueError('Per-job configuration would transcode this rip')
        config = detail['config']
        if config.get('RIPMETHOD')!='mkv' or enabled(config.get('MAINFEATURE')) or int(config.get('MINLENGTH',-1))!=0 or int(config.get('MAXLENGTH',-1))!=99998:
            raise ValueError('Job was created with incompatible ripping settings; correct ARM settings and reinsert')
        self.call('POST',f'/jobs/{job}/pause',{'paused':True})
        # Never PATCH job config: the inspected handler also modifies global config.
        self.call('PUT',f'/jobs/{job}/title',{'title':master.series,'season':master.season,'video_type':'series'})
        self.call('POST',f'/jobs/{job}/multi-title',{'enabled':True})
        # Unique staging directory even when final destination episode names repeat on a retry.
        self.call('PATCH',f'/jobs/{job}/naming',{'folder_pattern_override':f'arm-season-queue/job-{job}'})
        selected = {m['arm_track_id']:m for m in mapping}
        for track in detail['tracks']:
            payload = {'enabled':track['track_id'] in selected}
            if track['track_id'] in selected:
                entry = selected[track['track_id']]
                ep = entry['episode']
                payload.update(episode_number=str(ep['number']) if ep else '0',
                               episode_name=entry['content_name'],custom_filename=entry['basename'])
            self.call('PATCH',f"/jobs/{job}/tracks/{track['track_id']}",payload)
        reread = self.detail(job)
        for track in reread['tracks']:
            expected = selected.get(track['track_id'])
            if bool(track['enabled']) != bool(expected):
                raise ValueError('ARM did not retain title selection')
            if expected and (track['custom_filename'] != expected['basename'] or str(track['episode_number'])!=str(expected['episode']['number'])):
                raise ValueError('ARM metadata readback mismatch')
        preview = self.call('GET',f'/jobs/{job}/naming-preview')
        by_id = {int(t['track_number']):t for t in preview['tracks']}
        for entry in mapping:
            rendered = by_id.get(entry['makemkv_id'],{})
            if rendered.get('rendered_title')!=entry['basename'] or rendered.get('rendered_folder')!=f'arm-season-queue/job-{job}':
                raise ValueError('ARM naming preview differs from the staging plan')
        return preview


def job_identity(job):
    return digest({key:job.get(key) for key in ('job_id','start_time','devpath','source_type')})


def structure(detail):
    return digest([{'makemkv_id':int(t['track_number']), 'length':t['length'], 'aspect_ratio':t.get('aspect_ratio'),'fps':t.get('fps')}
                   for t in sorted(detail['tracks'],key=lambda t:int(t['track_number']))])


def plan(master, disc, detail):
    if disc.unresolved:
        raise ValueError('Masterlist disc needs initial review: ' + '; '.join(disc.unresolved))
    if disc.labels and detail['job'].get('label') not in disc.labels:
        raise ValueError('Scanned label conflicts with the recognised edition; review evidence')
    if disc.structural_signature and structure(detail)!=disc.structural_signature:
        raise ValueError('Disc title structure differs from the approved signature')
    if master.schema_version == 1 and not disc.title_map:
        raise ValueError('ARM does not expose DVD title numbers; approve an evidenced MakeMKV-to-DVD-title mapping first')
    readiness = rip_readiness(master, disc)
    if readiness:
        raise ValueError('Disc is metadata-only or technically incomplete: ' + '; '.join(readiness))
    tracks = {int(t['track_number']):t for t in detail['tracks']}
    if len(tracks)!=len(detail['tracks']):
        raise ValueError('Duplicate source title IDs')
    eligible = disc.selection_ids or list(tracks)
    if (len(set(eligible))!=disc.expected_title_count
            or set(eligible)!={t.makemkv_id for t in disc.title_map}):
        raise ValueError('Scanned source-title count/selection does not match the explicit scan-backed output mapping')
    if not set(eligible).issubset(tracks):
        raise ValueError('Required source title is missing')
    if len({item.makemkv_id for item in disc.title_map}) != len(disc.title_map):
        raise ValueError('ARM API cannot create multiple outputs from one MakeMKV title; split angles/versions need separately selectable source titles')
    result = []
    for item in disc.title_map:
        inventory = disc.inventory_for(item)
        if not inventory.complete or not inventory.evidence:
            raise ValueError(f'Approve the complete source stream inventory for MakeMKV ID {item.makemkv_id} before starting this disc')
        track = tracks[item.makemkv_id]
        if not 0 < track['length'] <= 99998:
            raise ValueError('Title length outside the supported per-title ripping range')
        ep = disc.episodes[item.episode_index] if item.episode_index is not None else None
        extra = next((extra for extra in disc.extras if extra.id == item.extra_id),None) if item.extra_id else None
        content_name = ep.title if ep else (item.output_name or extra.title if extra else None)
        dest = output_destination(master,disc,item)
        # ARM's filename sanitizer is stricter than the destination filesystem.
        # A short ASCII staging name avoids edition/title punctuation changing the preview.
        variant = ''.join(ch for ch in (item.version or (f'A{item.angle:02d}' if item.angle else '')) if ch.isalnum())[:16]
        basename = f'ASQ-S{master.season:02d}-D{disc.number:02d}-T{item.makemkv_id:03d}' + (f'-{variant}' if variant else '')
        result.append({'arm_track_id':track['track_id'], 'makemkv_id':item.makemkv_id,
                        'dvd_title':item.dvd_title,'angle':item.angle,'mapping_evidence':item.evidence,
                        'episode':ep.model_dump() if ep else None,
                        'extra':extra.model_dump() if extra else None,
                        'content_name':content_name,'version':item.version,
                        'destination':dest,'basename':basename,
                        'inventory':inventory.model_dump(),
                        'scan_duration':track['length'],'scan':track})
    if len({item['destination'] for item in result}) != len(result):
        raise ValueError('Selected episode/extra/version outputs collide at the destination')
    return result
