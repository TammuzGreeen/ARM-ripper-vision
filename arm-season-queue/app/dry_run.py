"""Information-only MakeMKV inventory parsing and production-equivalent preview."""
import csv
from collections import Counter
from pathlib import PurePosixPath

from .arm import plan


LANGUAGE = {'ger':'deu', 'fre':'fra', 'dut':'nld', 'gre':'ell', 'chi':'zho'}


def parse_info(text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise ValueError('MakeMKV info report is missing or exceeds the 2 MB limit')
    disc = {'type': None, 'label': None, 'titles': {}}
    title_count = None
    for line in text.splitlines():
        if ':' not in line:
            continue
        prefix, payload = line.split(':', 1)
        if prefix not in ('TCOUNT', 'CINFO', 'TINFO', 'SINFO'):
            continue
        try:
            row = next(csv.reader([payload], strict=True))
            if prefix == 'TCOUNT':
                title_count = int(row[0])
            elif prefix == 'CINFO':
                code, _attr, value = int(row[0]), int(row[1]), row[2]
                if code == 1: disc['type'] = value
                if code in (2, 30): disc['label'] = disc['label'] or value
            elif prefix == 'TINFO':
                tid, code, _attr, value = int(row[0]), int(row[1]), int(row[2]), row[3]
                title = disc['titles'].setdefault(tid, {'makemkv_id':tid,'streams':[]})
                if code == 8: title['chapters'] = int(value)
                elif code == 9: title['duration'] = value
                elif code == 10: title['size'] = value
                elif code == 11: title['size_bytes'] = int(value)
                elif code == 24: title['dvd_title'] = int(value)
                elif code == 25: title['angle_count'] = int(value)
                elif code == 26: title['chapter_range'] = value
            else:
                tid, stream_id, code, _attr, value = int(row[0]), int(row[1]), int(row[2]), int(row[3]), row[4]
                title = disc['titles'].setdefault(tid, {'makemkv_id':tid,'streams':[]})
                while len(title['streams']) <= stream_id:
                    title['streams'].append({'id':len(title['streams'])})
                stream = title['streams'][stream_id]
                if code == 1: stream['type'] = value
                elif code == 2: stream['audio_type'] = value
                elif code == 3: stream['language'] = LANGUAGE.get(value.casefold(), value.casefold())
                elif code == 4: stream['language_name'] = value
                elif code == 5: stream['codec_id'] = value
                elif code == 6: stream['codec'] = value
                elif code == 14: stream['channels'] = int(value) if value.isdigit() else None
                elif code == 19: stream['resolution'] = value
                elif code == 20: stream['aspect_ratio'] = value
                elif code == 21: stream['fps'] = value
        except (ValueError, IndexError, csv.Error):
            continue
    if title_count is None:
        raise ValueError('MakeMKV report has no TCOUNT; it is not a complete info scan')
    ids = sorted(disc['titles'])
    if title_count != len(ids) or ids != list(range(title_count)):
        raise ValueError(f'MakeMKV TCOUNT={title_count} but parsed contiguous title IDs are {ids}')
    for title in disc['titles'].values():
        title['streams'] = [s for s in title['streams'] if s.get('type')]
        title['audio'] = [s for s in title['streams'] if s['type'].casefold() == 'audio']
        title['subtitles'] = [s for s in title['streams'] if s['type'].casefold() in ('subtitles','subtitle')]
        title['video'] = next((s for s in title['streams'] if s['type'].casefold() == 'video'), None)
    return {'disc':disc, 'title_count':title_count}


def _duration_seconds(value):
    parts = [int(p) for p in value.split(':')]
    if len(parts) != 3 or parts[1] > 59 or parts[2] > 59:
        raise ValueError('MakeMKV title duration is malformed')
    return parts[0]*3600 + parts[1]*60 + parts[2]


def _languages(streams):
    return Counter(s.get('language', 'und') for s in streams)


def build_plan(master, disc, info, destination_root='/media/completed'):
    """Use the ordinary ARM mapping/filename planner, but never create a job."""
    scan_disc = info['disc']
    # JSON-backed dry-run state converts integer object keys to strings on
    # persistence. Normalize both fresh parser output and restored state here.
    titles = {int(tid): title for tid, title in info['disc']['titles'].items()}
    blockers = []
    if scan_disc.get('label') not in (disc.labels or []):
        blockers.append(f"Scanned disc label {scan_disc.get('label')!r} does not match the approved disc labels {disc.labels!r}")
    if info['title_count'] != disc.expected_title_count:
        blockers.append(f"Scan found {info['title_count']} titles; approved mapping expects {disc.expected_title_count}")
    expected_ids = set(disc.selection_ids or [m.makemkv_id for m in disc.title_map])
    actual_ids = set(titles)
    if actual_ids != expected_ids:
        blockers.append(f'Scanned MakeMKV IDs {sorted(actual_ids)} differ from approved selected IDs {sorted(expected_ids)}')
    for item in disc.title_map:
        source = titles.get(item.makemkv_id)
        if not source:
            continue
        if item.dvd_title is not None and source.get('dvd_title') != item.dvd_title:
            blockers.append(f"MakeMKV ID {item.makemkv_id} DVD title is {source.get('dvd_title')}; approved mapping expects {item.dvd_title}")
        inventory = disc.inventory_for(item)
        if _languages(source['audio']) != Counter(inventory.audio):
            blockers.append(f"MakeMKV ID {item.makemkv_id} audio language inventory differs from the approved scan")
        if _languages(source['subtitles']) != Counter(inventory.subtitles):
            blockers.append(f"MakeMKV ID {item.makemkv_id} subtitle inventory differs from the approved scan")
        channels = sorted(s['channels'] for s in source['audio'] if s.get('channels') is not None)
        if sorted(inventory.audio_channels) != channels:
            blockers.append(f"MakeMKV ID {item.makemkv_id} audio channel inventory differs from the approved scan")
    detail = {'job':{'label':scan_disc.get('label')}, 'tracks':[]}
    for tid, title in sorted(titles.items()):
        try:
            duration = _duration_seconds(title['duration'])
        except (KeyError, ValueError):
            blockers.append(f'MakeMKV ID {tid} has no valid scanned duration')
            continue
        video = title.get('video') or {}
        detail['tracks'].append({'track_id':tid, 'track_number':str(tid), 'length':duration,
                                 'aspect_ratio':video.get('aspect_ratio'),'fps':video.get('fps')})
    outputs = []
    try:
        outputs = plan(master, disc, detail)
    except ValueError as exc:
        blockers.append(str(exc))
    for output in outputs:
        title = titles[output['makemkv_id']]
        output['destination'] = str(PurePosixPath(destination_root) / output['destination'])
        output['arm_track_id'] = None
        output['dvd_title'] = title.get('dvd_title')
        output['angle'] = next((m.angle for m in disc.title_map if m.makemkv_id == output['makemkv_id']), None)
        output['angle_count'] = title.get('angle_count')
        output['duration'] = title.get('duration')
        output['chapters'] = title.get('chapters')
        output['size'] = title.get('size')
        output['video'] = title.get('video')
        output['audio_streams'] = title['audio']
        output['subtitle_streams'] = title['subtitles']
        output['destination_kind'] = 'would be created'
    exclusions = [
        {'makemkv_id': tid, 'dvd_title': title.get('dvd_title'), 'duration':title.get('duration'),
         'reason': 'No approved output mapping for this scanned MakeMKV title'}
        for tid, title in sorted(titles.items()) if tid not in expected_ids
    ]
    return {'mode':'DRY RUN ONLY', 'ready_for_ripping':False,
            'plan_status':'blocked' if blockers else 'provisional dry-run preview',
            'blockers':list(dict.fromkeys(blockers)), 'disc':scan_disc,
            'title_count':info['title_count'], 'outputs':outputs, 'excluded_titles':exclusions}
