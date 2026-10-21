"""Read-only, debounced Linux optical-drive tray-state monitoring."""

import fcntl
import os
import time


CDROM_DRIVE_STATUS = 0x5326
CDSL_CURRENT = 0xFFFF
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4


class TrayMonitor:
    """Poll CDROM_DRIVE_STATUS only; never issue tray, media, or scan commands."""

    def __init__(self, device, *, interval=0.5, stable_samples=3, reader=None,
                 opener=os.open, closer=os.close, clock=time.monotonic):
        if not isinstance(device, str) or not device.startswith('/'):
            raise ValueError('Tray monitor device must be an absolute path')
        if interval <= 0 or stable_samples < 1:
            raise ValueError('Invalid tray-monitor polling/debounce settings')
        self.device = device
        self.interval = float(interval)
        self.stable_samples = int(stable_samples)
        self.reader = reader
        self.opener = opener
        self.closer = closer
        self.clock = clock
        self.fd = None
        self.last_poll = float('-inf')
        self.state = 'unknown'
        self.initialized = False
        self.candidate = None
        self.candidate_count = 0
        self.error = 'Waiting for first tray-state sample'
        self.raw_status = None

    @staticmethod
    def classify(raw):
        if raw == CDS_TRAY_OPEN:
            return 'open'
        # NO_DISC means a closed drive with no medium, not an open tray.
        if raw in (CDS_NO_DISC, CDS_DISC_OK):
            return 'closed'
        if raw == CDS_DRIVE_NOT_READY:
            return 'unknown'
        return 'unknown'

    def _read(self):
        if self.reader is not None:
            return self.reader()
        if self.fd is None:
            self.fd = self.opener(self.device, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        return fcntl.ioctl(self.fd, CDROM_DRIVE_STATUS, CDSL_CURRENT)

    def poll(self, now=None):
        now = self.clock() if now is None else now
        result = {'state': self.state, 'transition': None, 'initial': False,
                  'error': self.error, 'raw_status': self.raw_status}
        if now - self.last_poll < self.interval:
            return result
        self.last_poll = now
        try:
            raw = self._read()
            candidate = self.classify(raw)
            self.raw_status = raw
            self.error = None if candidate != 'unknown' else f'Unrecognized/not-ready drive status {raw}'
        except (OSError, ValueError) as exc:
            candidate = 'unknown'
            self.raw_status = None
            self.error = f'Tray state unavailable for {self.device}: {exc}'
            if self.fd is not None:
                try:
                    self.closer(self.fd)
                except OSError:
                    pass
                self.fd = None

        # A failed/unrecognized query is not a tray transition to debounce:
        # fail closed immediately so an uncertain drive cannot auto-capture or
        # receive a close request based on stale state.
        if candidate == 'unknown':
            changed = self.state != 'unknown'
            self.state = 'unknown'
            self.candidate = None
            self.candidate_count = 0
            result.update(state=self.state, error=self.error, raw_status=self.raw_status)
            if changed:
                result['unknown'] = True
            return result

        if candidate == self.candidate:
            self.candidate_count += 1
        else:
            self.candidate = candidate
            self.candidate_count = 1

        if self.candidate_count >= self.stable_samples and candidate != self.state:
            old = self.state
            self.state = candidate
            if not self.initialized:
                self.initialized = True
                result['initial'] = candidate in ('open', 'closed')
            elif old in ('open', 'closed') and candidate in ('open', 'closed'):
                result['transition'] = candidate
        result.update(state=self.state, error=self.error, raw_status=self.raw_status)
        return result

    def close(self):
        if self.fd is not None:
            self.closer(self.fd)
            self.fd = None
