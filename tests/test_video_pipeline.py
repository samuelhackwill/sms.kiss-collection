from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ia_kissing_pipeline.db import connect, init_db
from ia_kissing_pipeline.ingest.store import upsert_film_item
from ia_kissing_pipeline.video.transcode import choose_preferred_file


def make_fixture_video(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x240:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x240:d=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[outv]",
            "-map",
            "[outv]",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )


def test_choose_preferred_file_prefers_marked_source(tmp_path: Path) -> None:
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)
    with connect(db_path) as conn:
        film_id = upsert_film_item(
            conn,
            {
                "archive_identifier": "test-film",
                "title": "Test Film",
                "item_url": "https://archive.org/details/test-film",
                "subjects": [],
                "files": [
                    {
                        "filename": "a.ogv",
                        "download_url": "/tmp/a.ogv",
                        "format": "Ogg Video",
                        "is_video": True,
                        "is_preferred_source": False,
                    },
                    {
                        "filename": "b.mp4",
                        "download_url": "/tmp/b.mp4",
                        "format": "h.264",
                        "is_video": True,
                        "is_preferred_source": True,
                    },
                ],
            },
        )
        row = choose_preferred_file(conn, film_id)
    assert row["filename"] == "b.mp4"


def test_video_prepare_and_shot_flow(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture_video = tmp_path / "fixture.mp4"
    make_fixture_video(fixture_video)
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)

    with connect(db_path) as conn:
        upsert_film_item(
            conn,
            {
                "archive_identifier": "fixture-film",
                "title": "Fixture Film",
                "item_url": "https://archive.org/details/fixture-film",
                "subjects": ["romance"],
                "files": [
                    {
                        "filename": fixture_video.name,
                        "download_url": str(fixture_video),
                        "format": "h.264",
                        "is_video": True,
                        "is_preferred_source": True,
                    }
                ],
            },
        )

    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(project_root / "src")
    env["DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    env["FRAME_DIR"] = str(tmp_path / "frames")
    env["CACHE_DIR"] = str(tmp_path / "cache")
    env["PREVIEW_DIR"] = str(tmp_path / "previews")
    env["LOG_DIR"] = str(tmp_path / "logs")

    subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", "prepare-video", "--film-id", "1"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", "detect-shots", "--film-id", "1", "--threshold", "0.10"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", "extract-shot-keyframes", "--film-id", "1"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    analysis_path = Path(env["DOWNLOAD_DIR"]) / "fixture-film" / "analysis.mp4"
    keyframe_dir = Path(env["FRAME_DIR"]) / "fixture-film"

    assert analysis_path.exists()
    assert keyframe_dir.exists()
    assert list(keyframe_dir.glob("shot_*.jpg"))
