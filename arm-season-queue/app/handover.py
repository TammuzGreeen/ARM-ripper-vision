"""Durable manifest contract and lossless, non-overwriting publication primitives."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path, PurePosixPath


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def beneath(root, relative):
    if '\\' in relative or ':' in relative:
        raise ValueError('Invalid portable path')
    rel = PurePosixPath(relative)
    if rel.is_absolute() or '..' in rel.parts or not rel.parts:
        raise ValueError('Path must be relative and contained')
    root = Path(root).resolve()
    candidate = root.joinpath(*rel.parts)
    if not candidate.resolve().is_relative_to(root):
        raise ValueError('Path or symlink escapes configured root')
    return candidate


def sync_dir(path):
    if os.name == 'posix':
        fd = os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def mkdir_durable(path):
    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True,exist_ok=True)
    for created in reversed(missing):
        sync_dir(created.parent)


def publish_json(path, body):
    """First writer wins; equal retries are accepted, differing content is rejected."""
    mkdir_durable(path.parent)
    data = canonical(body)
    if path.exists():
        if path.read_bytes()!=data:
            raise ValueError('Existing manifest/ack differs; refusing overwrite')
        return
    fd, name = tempfile.mkstemp(prefix='.pending-',dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp,path)
        except FileExistsError:
            if path.read_bytes()!=data:
                raise ValueError('Concurrent publication conflict')
        sync_dir(path.parent)
    finally:
        temp.unlink(missing_ok=True)


LANG = {'deu':'deu','ger':'deu','fre':'fra','fra':'fra','dut':'nld','nld':'nld'}


def validate_probe(probe, required, duration):
    if not required.get('complete') or not required.get('evidence'):
        raise ValueError('A complete, evidenced source inventory is required before unattended handover')
    streams = probe.get('streams',[])
    video = [s for s in streams if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')]
    if len(video)!=1:
        raise ValueError('Expected one source video stream')
    v = video[0]
    for key in ('width','height'):
        if required.get(key) is not None and v.get(key)!=required[key]:
            raise ValueError('Video '+key+' differs from source')
    if required.get('video_codec') and v.get('codec_name')!=required['video_codec']:
        raise ValueError('Video codec differs from source')
    if required.get('fps'):
        a,b = v.get('r_frame_rate','0/1').split('/')
        if abs(float(a)/float(b)-required['fps'])>.02:
            raise ValueError('Video frame rate differs from source')
    for typ, field in (('audio','audio'),('subtitle','subtitles')):
        actual = Counter(LANG.get(s.get('tags',{}).get('language','und'),s.get('tags',{}).get('language','und')) for s in streams if s.get('codec_type')==typ)
        expected = Counter(LANG.get(l,l) for l in required[field])
        if actual!=expected:
            raise ValueError(f'{typ} language/count mismatch: expected {dict(expected)}, got {dict(actual)}')
    if required.get('audio_channels'):
        channels = sorted(s.get('channels',0) for s in streams if s.get('codec_type')=='audio')
        if channels!=sorted(required['audio_channels']):
            raise ValueError('Audio channel inventory differs from source')
    if required.get('chapters') is not None and len(probe.get('chapters',[]))!=required['chapters']:
        raise ValueError('Chapter count differs from source')
    actual_duration = float(probe.get('format',{}).get('duration',0))
    if not actual_duration or abs(actual_duration-duration)>max(15,duration*.01):
        raise ValueError('Output duration outside tolerance (15 seconds or 1%)')
    return {'stream_inventory':'passed','duration_tolerance_seconds':max(15,duration*.01),'duration':actual_duration}


def inspect_file(path, required, duration, decode=True):
    if not path.is_file() or path.stat().st_size==0:
        raise ValueError('Completed output is missing or empty')
    before = (path.stat().st_size,path.stat().st_mtime_ns)
    proc = subprocess.run(['ffprobe','-v','error','-show_streams','-show_chapters','-show_format','-of','json',str(path)],
                          check=True,capture_output=True,timeout=120)
    probe = json.loads(proc.stdout)
    result = validate_probe(probe,required,duration)
    if decode:
        subprocess.run(['ffmpeg','-nostdin','-v','error','-xerror','-i',str(path),'-map','0:v:0','-map','0:a?',
                        '-f','null','-'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=7200)
        result['decode']='passed'
    hashed = checksum(path)
    if before!=(path.stat().st_size,path.stat().st_mtime_ns):
        raise ValueError('Source changed during validation')
    return {'bytes':before[0],'sha256':hashed,'probe':probe,'validation':result}


def copy_verified(src,dest,expected):
    if checksum(src)!=expected:
        raise ValueError('Source changed since manifest creation')
    if dest.exists():
        if checksum(dest)!=expected:
            raise ValueError('Destination already exists with different content')
        return
    mkdir_durable(dest.parent)
    fd,name = tempfile.mkstemp(prefix='.queue-',suffix='.partial',dir=dest.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd,'wb') as out, src.open('rb') as inp:
            shutil.copyfileobj(inp,out,4*1024*1024)
            out.flush()
            os.fsync(out.fileno())
        if checksum(temp)!=expected:
            raise ValueError('Copied file failed checksum')
        try:
            os.link(temp,dest)  # atomic per-file publish with no replacement
        except FileExistsError:
            if checksum(dest)!=expected:
                raise ValueError('Destination conflict during copy')
        sync_dir(dest.parent)
        if checksum(dest)!=expected:
            raise ValueError('Final destination verification failed')
    finally:
        temp.unlink(missing_ok=True)


def publish_manifest(manifest_path,media,library,handover):
    body = json.loads(manifest_path.read_text('utf-8'))
    if body.get('schema_version')!=1 or body.get('status')!='ready' or body.get('errors'):
        raise ValueError('Not a ready v1 manifest')
    if not body.get('outputs') or body.get('arm_status')!='success':
        raise ValueError('Manifest lacks successful ARM completion / outputs')
    manifest_hash = hashlib.sha256(canonical(body)).hexdigest()
    names = [o['destination'] for o in body['outputs']]
    if len(set(names))!=len(names):
        raise ValueError('Manifest destination collision')
    results = []
    for output in body['outputs']:
        if not output['destination'].startswith('tv/'):
            raise ValueError('v1 publishes television season media only')
        src = beneath(media,output['media_relative_path'])
        dest = beneath(library,output['destination'])
        # New manifests carry the effective per-title inventory on each output.
        # Older manifests remain compatible through their disc-level default.
        inventory = output.get('inventory', body['source_inventory'])
        inspected = inspect_file(src,inventory,output['scan_duration'])
        if inspected['sha256']!=output['sha256'] or inspected['bytes']!=output['bytes']:
            raise ValueError('Manifest source hash/size no longer matches')
        copy_verified(src,dest,output['sha256'])
        results.append({'destination':output['destination'],'sha256':output['sha256']})
    ack = {'schema_version':1,'manifest_sha256':manifest_hash,'job':body['arm_job_id'],
           'batch':body['batch_id'],'status':'published','outputs':results,'sources_deleted':False}
    publish_json(Path(handover)/'acks'/f"{body['arm_job_id']}.json",ack)
    return ack
