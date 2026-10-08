import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def env_bool(name, default=True):
    value = os.getenv(name)
    return default if value is None else value.casefold() in ('1', 'true', 'yes', 'on')


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
    camera_mode: str = os.getenv('CAMERA_MODE', '')
    width: int = int(os.getenv('CAMERA_WIDTH', '3840'))
    height: int = int(os.getenv('CAMERA_HEIGHT', '2160'))
    camera_fps: int = int(os.getenv('CAMERA_FPS', '15'))
    camera_rotation: int = int(os.getenv('CAMERA_ROTATION', '0'))
    roi: str = os.getenv('CAMERA_ROI', '0.15,0.1,0.85,0.9')
    sharpness: float = float(os.getenv('CAMERA_SHARPNESS', '100'))
    ocr_lang: str = os.getenv('OCR_LANG', 'deu+eng')
    recognition_backend: str = os.getenv('RECOGNITION_BACKEND', 'tesseract')
    vision_url: str = os.getenv('VISION_BASE_URL', '').rstrip('/')
    vision_model: str = os.getenv('VISION_MODEL', '')
    vision_key: str = os.getenv('VISION_API_KEY', '')
    vision_timeout: int = int(os.getenv('VISION_TIMEOUT_SECONDS', '300'))
    vision_short_num_predict: int = int(os.getenv('VISION_SHORT_NUM_PREDICT', '768'))
    vision_short_timeout: int = int(os.getenv('VISION_SHORT_TIMEOUT_SECONDS', '240'))
    ttl: int = int(os.getenv('CAPTURE_TTL_SECONDS', '180'))
    retention: int = int(os.getenv('EVIDENCE_RETENTION_DAYS', '30'))
    drive: str = os.getenv('ARM_DRIVE', '/dev/sr0')
    user: str = os.getenv('QUEUE_USER', 'operator')
    password: str = os.getenv('QUEUE_PASSWORD', '')
    switch_policy: str = os.getenv('BATCH_SWITCH_POLICY', 'review')
    fileflows_enabled: bool = env_bool('FILEFLOWS_ENABLED', True)
    dry_run_only: bool = env_bool('DRY_RUN_ONLY', False)
    dry_run_output_root: str = os.getenv('DRY_RUN_OUTPUT_ROOT', '/media/completed')

    def __post_init__(self):
        if self.recognition_backend not in ('tesseract', 'ollama', 'ollama-agreement', 'llamacpp', 'openai-compatible'):
            raise ValueError('RECOGNITION_BACKEND must be tesseract, ollama-agreement, ollama, llamacpp or openai-compatible')
        if not 10 <= self.vision_timeout <= 600:
            raise ValueError('VISION_TIMEOUT_SECONDS must be between 10 and 600')
        if not 128 <= self.vision_short_num_predict <= 2048:
            raise ValueError('VISION_SHORT_NUM_PREDICT must be between 128 and 2048')
        if not 10 <= self.vision_short_timeout <= 600:
            raise ValueError('VISION_SHORT_TIMEOUT_SECONDS must be between 10 and 600')
        if self.recognition_backend != 'tesseract':
            parsed = urlsplit(self.vision_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Set VISION_BASE_URL to an HTTP(S) API base without embedded credentials')
            if self.recognition_backend != 'ollama-agreement' and not self.vision_model.strip():
                raise ValueError('Set VISION_MODEL to the exact image-capable model ID served by your server')
        if self.camera_mode not in ('', 'manual', 'auto'):
            raise ValueError('CAMERA_MODE must be manual or auto')
        if self.camera_rotation not in (0, 180):
            raise ValueError('CAMERA_ROTATION must be 0 or 180')
        if self.arm_url:
            parsed = urlsplit(self.arm_url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('ARM_URL must be an HTTP(S) base URL without credentials, query or fragment')
        if self.switch_policy not in ('review','auto') or self.ttl<10 or self.retention<1:
            raise ValueError('Invalid switch policy, capture TTL or evidence retention')


REFERENCE_SHA = 'f6ec2e3fd47cf951e89094e0a9d6999ab94d781f'
