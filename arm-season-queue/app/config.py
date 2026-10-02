import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    state: Path = Path(os.getenv('STATE_DIR', '/state'))
    handover: Path = Path(os.getenv('HANDOVER_DIR', '/handover'))
    media: Path = Path(os.getenv('MEDIA_DIR', '/media'))
    arm_media: str = os.getenv('ARM_MEDIA_PREFIX', '/home/arm/media')
    arm_url: str = os.getenv('ARM_URL', '').rstrip('/')
    arm_token: str = os.getenv('ARM_BEARER_TOKEN', '')
    source_verified: str = os.getenv('ARM_VERIFIED_SOURCE_SHA', '')
    expected_version: str = os.getenv('ARM_EXPECTED_VERSION', '19.1.0')
    camera: str = os.getenv('CAMERA_DEVICE', '/dev/video0')
    width: int = int(os.getenv('CAMERA_WIDTH', '3840'))
    height: int = int(os.getenv('CAMERA_HEIGHT', '2160'))
    camera_fps: int = int(os.getenv('CAMERA_FPS', '15'))
    roi: str = os.getenv('CAMERA_ROI', '0.15,0.1,0.85,0.9')
    sharpness: float = float(os.getenv('CAMERA_SHARPNESS', '100'))
    ocr_lang: str = os.getenv('OCR_LANG', 'deu+eng')
    ttl: int = int(os.getenv('CAPTURE_TTL_SECONDS', '180'))
    retention: int = int(os.getenv('EVIDENCE_RETENTION_DAYS', '30'))
    drive: str = os.getenv('ARM_DRIVE', '/dev/sr0')
    user: str = os.getenv('QUEUE_USER', 'operator')
    password: str = os.getenv('QUEUE_PASSWORD', '')
    switch_policy: str = os.getenv('BATCH_SWITCH_POLICY', 'review')

    def __post_init__(self):
        if self.arm_url:
            parsed = urlsplit(self.arm_url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('ARM_URL must be an HTTP(S) base URL without credentials, query or fragment')
        if self.switch_policy not in ('review','auto') or self.ttl<10 or self.retention<1:
            raise ValueError('Invalid switch policy, capture TTL or evidence retention')


REFERENCE_SHA = 'f6ec2e3fd47cf951e89094e0a9d6999ab94d781f'
