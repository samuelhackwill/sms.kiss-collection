from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


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
    user_agent: str

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
    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        db_path=Path(os.getenv("DB_PATH", str(cwd / "data" / "pipeline.db"))),
        cache_dir=Path(os.getenv("CACHE_DIR", str(cwd / "data" / "cache"))),
        download_dir=Path(os.getenv("DOWNLOAD_DIR", str(cwd / "data" / "downloads"))),
        frame_dir=Path(os.getenv("FRAME_DIR", str(cwd / "data" / "frames"))),
        preview_dir=Path(os.getenv("PREVIEW_DIR", str(cwd / "data" / "previews"))),
        clips_dir=Path(os.getenv("CLIPS_DIR", str(cwd / "data" / "clips"))),
        log_dir=Path(os.getenv("LOG_DIR", str(cwd / "data" / "logs"))),
        user_agent=os.getenv(
            "USER_AGENT",
            "ia-kissing-pipeline/0.1 (contact: operator@example.com)",
        ),
    )
