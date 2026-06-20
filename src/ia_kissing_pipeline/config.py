from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_env: str
    db_path: Path
    cache_dir: Path
    download_dir: Path
    frame_dir: Path
    preview_dir: Path
    clips_dir: Path
    log_dir: Path
    media_storage_backend: str
    media_s3_bucket: str
    media_s3_endpoint_url: str
    media_s3_region: str
    media_s3_access_key_id: str
    media_s3_secret_access_key: str
    media_s3_public_base_url: str
    media_s3_acl: str
    media_s3_cache_control: str
    media_delete_local_after_upload: bool
    cache_max_bytes: int
    cache_max_age_seconds: int
    user_agent: str
    roboflow_api_url: str
    roboflow_api_key: str
    roboflow_workspace_name: str
    roboflow_workflow_id: str
    roboflow_kiss_detector_classes: str

    def ensure_directories(self) -> None:
        for path in (
            self.db_path.parent,
            self.cache_dir,
            self.download_dir,
            self.frame_dir,
            self.preview_dir,
            self.clips_dir,
            self.log_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    cwd = Path.cwd()
    load_dotenv(cwd / ".env", override=False)
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        db_path=Path(os.getenv("DB_PATH", str(cwd / "data" / "pipeline.db"))),
        cache_dir=Path(os.getenv("CACHE_DIR", str(cwd / "data" / "cache"))),
        download_dir=Path(os.getenv("DOWNLOAD_DIR", str(cwd / "data" / "downloads"))),
        frame_dir=Path(os.getenv("FRAME_DIR", str(cwd / "data" / "frames"))),
        preview_dir=Path(os.getenv("PREVIEW_DIR", str(cwd / "data" / "previews"))),
        clips_dir=Path(os.getenv("CLIPS_DIR", str(cwd / "data" / "clips"))),
        log_dir=Path(os.getenv("LOG_DIR", str(cwd / "data" / "logs"))),
        media_storage_backend=os.getenv("MEDIA_STORAGE_BACKEND", "local"),
        media_s3_bucket=os.getenv("MEDIA_S3_BUCKET", ""),
        media_s3_endpoint_url=os.getenv("MEDIA_S3_ENDPOINT_URL", ""),
        media_s3_region=os.getenv("MEDIA_S3_REGION", ""),
        media_s3_access_key_id=os.getenv("MEDIA_S3_ACCESS_KEY_ID", ""),
        media_s3_secret_access_key=os.getenv("MEDIA_S3_SECRET_ACCESS_KEY", ""),
        media_s3_public_base_url=os.getenv("MEDIA_S3_PUBLIC_BASE_URL", ""),
        media_s3_acl=os.getenv("MEDIA_S3_ACL", "public-read"),
        media_s3_cache_control=os.getenv("MEDIA_S3_CACHE_CONTROL", "public, max-age=31536000, immutable"),
        media_delete_local_after_upload=_env_bool("MEDIA_DELETE_LOCAL_AFTER_UPLOAD", True),
        cache_max_bytes=_env_int("CACHE_MAX_BYTES", 10 * 1024 * 1024 * 1024),
        cache_max_age_seconds=_env_int("CACHE_MAX_AGE_SECONDS", 14 * 24 * 60 * 60),
        user_agent=os.getenv(
            "USER_AGENT",
            "ia-kissing-pipeline/0.1 (contact: operator@example.com)",
        ),
        roboflow_api_url=os.getenv("ROBOFLOW_API_URL", "https://serverless.roboflow.com"),
        roboflow_api_key=os.getenv("ROBOFLOW_API_KEY", ""),
        roboflow_workspace_name=os.getenv("ROBOFLOW_WORKSPACE_NAME", ""),
        roboflow_workflow_id=os.getenv("ROBOFLOW_WORKFLOW_ID", ""),
        roboflow_kiss_detector_classes=os.getenv("ROBOFLOW_KISS_DETECTOR_CLASSES", "head"),
    )
