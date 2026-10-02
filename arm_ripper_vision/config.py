from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ARV_", env_file=".env", extra="ignore")
    host: str = "0.0.0.0"
    port: int = 8099
    log_level: str = "INFO"
    data_dir: Path = Path("/data")
    masterlist_dir: Path = Path("/config/masterlists")
    database_url: str | None = None
    arm_base_url: str = "http://arm-rippers:8080"
    arm_poll_interval_seconds: float = Field(default=3.0, ge=1.0)
    arm_timeout_seconds: float = 15.0
    arm_auth_type: Literal["none","bearer","basic","header"] = "none"
    arm_auth_token: str | None = None
    arm_auth_username: str | None = None
    arm_auth_password: str | None = None
    arm_auth_header: str = "X-API-Key"
    arm_jobs_path: str = "/api/v1/jobs"
    arm_job_detail_path: str = "/api/v1/jobs/{job_id}/detail"
    arm_job_title_path: str = "/api/v1/jobs/{job_id}/title"
    arm_track_update_path: str = "/api/v1/jobs/{job_id}/tracks/{track_id}"
    arm_job_start_path: str = "/api/v1/jobs/{job_id}/start"
    arm_job_pause_path: str = "/api/v1/jobs/{job_id}/pause"
    arm_ripping_enabled_path: str = "/api/v1/system/ripping-enabled"
    require_allow_duplicates: bool = True
    require_manual_wait: bool = True
    max_safe_maxlength: int = 99998
    @property
    def resolved_database_url(self):
        return self.database_url or f"sqlite:///{self.data_dir / 'arm-ripper-vision.db'}"
    @model_validator(mode="after")
    def validate_auth(self):
        if self.arm_auth_type in {"bearer","header"} and not self.arm_auth_token:
            raise ValueError("ARV_ARM_AUTH_TOKEN is required")
        if self.arm_auth_type == "basic" and not self.arm_auth_username:
            raise ValueError("ARV_ARM_AUTH_USERNAME is required")
        return self
@lru_cache
def get_settings(): return Settings()
