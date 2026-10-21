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
    presentation_timeout: int = int(os.getenv('PRESENTATION_CAPTURE_TIMEOUT_SECONDS', '60'))
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
    local_beta: bool = env_bool('LOCAL_BETA_ENABLED', False)
    tray_cycle_enabled: bool = env_bool('LOCAL_BETA_TRAY_CYCLE_ENABLED', False)
    tray_close_enabled: bool = env_bool('LOCAL_BETA_TRAY_CLOSE_ENABLED', False)
    tray_monitor_enabled: bool = env_bool('TRAY_MONITOR_ENABLED', False)
    tray_monitor_device: str = os.getenv('TRAY_MONITOR_DEVICE', '/dev/arm-monitor')
    tray_state_poll_seconds: float = float(os.getenv('TRAY_STATE_POLL_SECONDS', '0.5'))
    tray_state_debounce_samples: int = int(os.getenv('TRAY_STATE_DEBOUNCE_SAMPLES', '3'))
    tray_calibration_settle_seconds: float = float(os.getenv('TRAY_CALIBRATION_SETTLE_SECONDS', '2'))
    tray_calibration_timeout_seconds: int = int(os.getenv('TRAY_CALIBRATION_TIMEOUT_SECONDS', '60'))
    auto_ordinal_mapping: bool = env_bool('ARM_AUTO_ORDINAL_MAPPING', False)
    app_version: str = os.getenv('APP_VERSION', 'development')
    beta_output_root: str = os.getenv('BETA_OUTPUT_ROOT', '/media/unreviewed')
    media_host_prefix: str = os.getenv('MEDIA_HOST_PREFIX', '')
    beta_min_free_bytes: int = int(os.getenv('BETA_MIN_FREE_BYTES', str(30 * 1024**3)))

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
        if not 10 <= self.presentation_timeout <= 300:
            raise ValueError('PRESENTATION_CAPTURE_TIMEOUT_SECONDS must be between 10 and 300')
        if self.camera_rotation not in (0, 180):
            raise ValueError('CAMERA_ROTATION must be 0 or 180')
        if self.arm_url:
            parsed = urlsplit(self.arm_url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('ARM_URL must be an HTTP(S) base URL without credentials, query or fragment')
        if self.switch_policy not in ('review','auto') or self.ttl<10 or self.retention<1:
            raise ValueError('Invalid switch policy, capture TTL or evidence retention')
        if self.local_beta and (self.dry_run_only or self.fileflows_enabled):
            raise ValueError('Local beta requires DRY_RUN_ONLY=false and FILEFLOWS_ENABLED=false')
        if self.tray_cycle_enabled and not self.local_beta:
            raise ValueError('Tray cycling requires LOCAL_BETA_ENABLED=true')
        if self.tray_close_enabled and not self.local_beta:
            raise ValueError('Tray close requires LOCAL_BETA_ENABLED=true')
        if self.tray_monitor_enabled and not self.local_beta:
            raise ValueError('Tray monitoring requires LOCAL_BETA_ENABLED=true')
        if self.tray_monitor_enabled and not self.tray_monitor_device.startswith('/'):
            raise ValueError('TRAY_MONITOR_DEVICE must be an absolute path')
        if self.tray_monitor_enabled and self.tray_monitor_device != self.drive:
            raise ValueError('TRAY_MONITOR_DEVICE must match configured ARM_DRIVE')
        if (not 0.1 <= self.tray_state_poll_seconds <= 5
                or not 2 <= self.tray_state_debounce_samples <= 10):
            raise ValueError('Tray polling/debounce configuration is outside safe bounds')
        if (not 0.5 <= self.tray_calibration_settle_seconds <= 10
                or not 10 <= self.tray_calibration_timeout_seconds <= 300):
            raise ValueError('Tray calibration timing is outside safe bounds')
        if self.tray_close_enabled and not self.tray_monitor_enabled:
            raise ValueError('Automatic tray close requires TRAY_MONITOR_ENABLED=true')
        if self.auto_ordinal_mapping and not self.local_beta:
            raise ValueError('ARM_AUTO_ORDINAL_MAPPING requires LOCAL_BETA_ENABLED=true')
        if self.beta_min_free_bytes < 1:
            raise ValueError('BETA_MIN_FREE_BYTES must be positive')


REFERENCE_SHA = 'f6ec2e3fd47cf951e89094e0a9d6999ab94d781f'
