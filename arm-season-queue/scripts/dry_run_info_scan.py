#!/usr/bin/env python3
"""Host-side, network-isolated MakeMKV info scan for supervised dry runs.

This helper is deliberately separate from the queue container. It closes the
selected drive with a kernel tray ioctl (never ARM's processing-triggering
drive API), runs MakeMKV's `info` command only, and stores only its private text
report. It requires Docker on the host and does not mount the Docker socket into
the companion.
"""
import argparse
import fcntl
import json
import os
import re
import selectors
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

DRIVE_STATUS = 0x5326       # CDROM_DRIVE_STATUS
DISC_STATUS = 0x5327        # CDROM_DISC_STATUS
MEDIA_CHANGED = 0x5325      # CDROM_MEDIA_CHANGED
CLOSE_TRAY = 0x5319         # CDROMCLOSETRAY
DATA_MEDIA_STATUSES = {4, 101, 102, 103, 104, 105}


def api_json(base, path):
    with urllib.request.urlopen(base.rstrip('/') + path, timeout=8) as response:
        return json.load(response)


def held_current_job(drives, jobs, device):
    selected = [d for d in drives if d.get('mount') == device]
    if len(selected) != 1:
        raise RuntimeError('Configured ARM drive is missing or ambiguous')
    current = selected[0].get('job_id_current')
    if not jobs and current is None:
        return None
    if (len(jobs) != 1 or current is None or jobs[0].get('job_id') != current
            or jobs[0].get('status') != 'manual_paused' or jobs[0].get('manual_start') is not False):
        raise RuntimeError('ARM must have no jobs or exactly one current manual_paused job with no start request')
    return jobs[0]


def check_arm(base, device):
    pause = api_json(base, '/system/ripping-enabled')
    if pause.get('ripping_enabled') is not False:
        raise RuntimeError('ARM global pause is not verified; scan refused')
    jobs = api_json(base, '/jobs/paginated?page=1&per_page=100')
    drive_data = api_json(base, '/drives')
    count = jobs.get('total', len(jobs.get('jobs', [])))
    if count != len(jobs.get('jobs', [])):
        raise RuntimeError('ARM job listing is incomplete; refusing drive scan')
    held = held_current_job(drive_data.get('drives', []), jobs.get('jobs', []), device)
    selected = [d for d in drive_data.get('drives', []) if d.get('mount') == device]
    if selected[0].get('drive_mode') != 'auto':
        raise RuntimeError('ARM drive is not in the inspected automatic mode')
    return {'drive':selected[0], 'held_job':held}


def drive_ioctl(device, operation):
    fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    try:
        return fcntl.ioctl(fd, operation, 0)
    finally:
        os.close(fd)


def data_medium_ready(drive_status, disc_status):
    """Linux CDROM_DISC_STATUS uses CDS_DATA_* values 101–105 for data discs."""
    return drive_status == 4 and disc_status in DATA_MEDIA_STATUSES


def wait_ready(device, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if data_medium_ready(drive_ioctl(device, DRIVE_STATUS), drive_ioctl(device, DISC_STATUS)):
            return
        time.sleep(.5)
    raise RuntimeError(f'Drive did not report a ready data medium within {timeout} seconds')


def clear_media_change(device):
    # Opening/closing the tray itself sets this bit. Clear it after the medium
    # is ready; any later change means the scan no longer belongs to this disc.
    for _ in range(4):
        if drive_ioctl(device, MEDIA_CHANGED) == 0:
            return
    raise RuntimeError('Drive media-change status did not settle after tray closure')


def require_all_stream_profile(config_dir):
    path = config_dir / 'settings.conf'
    if not path.is_file():
        raise RuntimeError('Persistent MakeMKV settings.conf is missing; stream-selection policy is unverified')
    found = re.search(r'^\s*app_DefaultSelectionString\s*=\s*["\']?([^"\'\s]+)',
                      path.read_text(encoding='utf-8', errors='replace'), re.MULTILINE)
    if not found or found.group(1) != '+sel:all':
        raise RuntimeError('Persistent MakeMKV app_DefaultSelectionString is not exactly +sel:all')


def stop_scan(proc, message):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    raise RuntimeError(message)


def scan(command, report, device, timeout, arm_api):
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            bufsize=4096)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    last_check = time.monotonic()
    last_arm_check = last_check
    deadline = last_check + timeout
    with report.open('wb') as output:
        os.chmod(report, 0o600)
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise RuntimeError(f'MakeMKV info-only scan exceeded its {timeout}-second bound')
            for key, _ in selector.select(timeout=.5):
                chunk = key.fileobj.read1(8192)
                if chunk:
                    output.write(chunk)
                    output.flush()
            if time.monotonic() - last_check >= 1:
                if drive_ioctl(device, MEDIA_CHANGED):
                    stop_scan(proc,'Drive reported media removal/replacement during scan; report is invalid')
                if time.monotonic() - last_arm_check >= 5:
                    try:
                        check_arm(arm_api, device)
                    except (OSError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
                        stop_scan(proc,f'ARM pause/job safety check failed during scan: {exc}')
                    last_arm_check = time.monotonic()
                last_check = time.monotonic()
        tail = proc.stdout.read()
        if tail:
            output.write(tail)
        output.flush()
    if proc.returncode != 0:
        raise RuntimeError(f'MakeMKV info-only scan exited with status {proc.returncode}; inspect private report')
    if not any(line.startswith('TCOUNT:') for line in report.read_text(encoding='utf-8', errors='replace').splitlines()):
        raise RuntimeError('MakeMKV returned no TCOUNT; no title inventory was established')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--device', default='/dev/sr0')
    p.add_argument('--sg-device', default='/dev/sg0')
    p.add_argument('--arm-api', required=True, help='Read-only ARM API base, e.g. http://127.0.0.1:18080/api/v1')
    p.add_argument('--makemkv-image', required=True, help='Prequalified scan-only image containing MakeMKV')
    p.add_argument('--makemkv-config', type=Path, required=True, help='Persistent MakeMKV config directory, mounted read-only')
    p.add_argument('--report-dir', type=Path, required=True, help='Private local directory outside Git')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--scan-timeout', type=int, default=600)
    p.add_argument('--confirm-physical-disc', action='store_true', help='Confirm the camera-reviewed intended disc is on the open tray')
    p.add_argument('--capture-event', required=True, help='Exact fresh camera event UUID shown by the dry-run review screen')
    a = p.parse_args()
    if not a.confirm_physical_disc:
        p.error('refusing to close tray without --confirm-physical-disc after camera review')
    try:
        capture_event = str(UUID(a.capture_event))
    except ValueError:
        p.error('--capture-event must be the UUID of the current dry-run camera capture')
    if not (1 <= a.timeout <= 600 and 1 <= a.scan_timeout <= 1800):
        p.error('readiness timeout must be 1–600 seconds and info-scan timeout 1–1800 seconds')
    if not (a.device.startswith('/dev/sr') and a.sg_device.startswith('/dev/sg')):
        p.error('only explicit /dev/srN and /dev/sgN optical nodes are accepted')
    if (not Path(a.device).is_block_device() or not Path(a.sg_device).is_char_device()
            or not a.makemkv_config.is_dir()):
        p.error('optical device, generic SCSI node, or MakeMKV config is missing')
    a.report_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    a.report_dir.chmod(0o700)
    os.umask(0o077)
    report_fd, name = tempfile.mkstemp(prefix='makemkv-info-dry-run-', suffix='.txt', dir=a.report_dir)
    os.close(report_fd)
    report = Path(name)
    try:
        arm_state = check_arm(a.arm_api, a.device)
        require_all_stream_profile(a.makemkv_config)
        if drive_ioctl(a.device, DRIVE_STATUS) == 2:
            drive_ioctl(a.device, CLOSE_TRAY)
        wait_ready(a.device, a.timeout)
        clear_media_change(a.device)
        # The ARM source profile confirms its /drives/{id}/scan route launches
        # processing. Never call it; stop if its local worker started anyway.
        arm_state = check_arm(a.arm_api, a.device)
        cmd = ['docker','run','--rm','--network','none','--read-only',
               '--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--user','0:24','--cap-drop','ALL',
               '--env','HOME=/root','--device',f'{a.device}:/dev/sr0:rw',
               '--device',f'{a.sg_device}:/dev/sg0:rw',
               '--mount',f'type=bind,source={a.makemkv_config.resolve()},target=/root/.MakeMKV,readonly',
               '--entrypoint','/opt/makemkv/bin/makemkvcon',a.makemkv_image,
               '-r','info','--cache=1','dev:/dev/sr0','--minlength=0']
        print('Operation: MakeMKV info only; network disabled; no media output path is mounted.')
        print('ARM global pause was verified; any current ARM job was required to be manual_paused with no start request. No ARM scan route was called.')
        if arm_state['held_job']:
            print('ARM held job:',arm_state['held_job'].get('job_id'),'manual_paused')
        print(f'Private report: {report}')
        scan(cmd, report, a.device, a.scan_timeout, a.arm_api)
        if drive_ioctl(a.device, MEDIA_CHANGED):
            raise RuntimeError('Drive media-change status changed during the scan; association invalidated')
        if drive_ioctl(a.device, DRIVE_STATUS) != 4:
            raise RuntimeError('Drive is no longer closed/ready; association invalidated')
        check_arm(a.arm_api, a.device)
        raw_report = report.read_text(encoding='utf-8', errors='replace')
        report.write_text(f'# DRY_RUN_CAPTURE_EVENT:{capture_event}\n' + raw_report, encoding='utf-8')
        report.chmod(0o600)
        print('Scan complete: info-only report retained; no media output was created.')
        return 0
    except (OSError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(f'DRY RUN SCAN BLOCKED: {exc}', file=sys.stderr)
        print(f'Private diagnostic report (may be incomplete): {report}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
