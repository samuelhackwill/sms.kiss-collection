from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ia_kissing_pipeline.db import connect, init_db
from ia_kissing_pipeline.ingest.store import upsert_film_item
from ia_kissing_pipeline.review.commands import parse_review_command
from tests.test_video_pipeline import make_fixture_video


def test_parse_review_command() -> None:
    assert parse_review_command("1 2 6") == {"action": "approve", "indices": [1, 2, 6]}
    assert parse_review_command("reject 4") == {"action": "reject", "indices": [4]}
    assert parse_review_command("review") == {"action": "review"}


def test_local_review_flow(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    fixture_video = tmp_path / "fixture.mp4"
    make_fixture_video(fixture_video)
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)
    with connect(db_path) as conn:
        upsert_film_item(
            conn,
            {
                "archive_identifier": "review-fixture",
                "title": "Review Fixture",
                "year": 1932,
                "item_url": "https://archive.org/details/review-fixture",
                "subjects": ["romance", "lovers"],
                "metadata_score": 0.8,
                "rights_confidence": "high_confidence",
                "rights_confidence_score": 0.9,
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
        conn.commit()

    env = os.environ.copy()
    env["DB_PATH"] = str(db_path)
    env["PYTHONPATH"] = str(project_root / "src")
    env["DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    env["FRAME_DIR"] = str(tmp_path / "frames")
    env["CACHE_DIR"] = str(tmp_path / "cache")
    env["PREVIEW_DIR"] = str(tmp_path / "previews")
    env["LOG_DIR"] = str(tmp_path / "logs")

    commands = [
        ["prepare-video", "--film-id", "1"],
        ["detect-shots", "--film-id", "1", "--threshold", "0.10"],
        ["extract-shot-keyframes", "--film-id", "1"],
        ["score-shots", "--film-id", "1"],
        ["refine-candidates", "--film-id", "1", "--limit", "2"],
        ["build-review-batch", "--limit", "2"],
        ["approve", "1"],
        ["clip", "1", "--pre-seconds", "1", "--post-seconds", "1"],
    ]
    for command in commands:
        subprocess.run(
            [sys.executable, "-m", "ia_kissing_pipeline.main", *command],
            cwd=project_root,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

    review = subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", "review"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    batch = subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", "build-review-batch", "--limit", "2"],
        cwd=project_root,
        env=env,
        text=True,
        capture_output=True,
    )

    assert "Review batch" in review.stdout
    assert batch.returncode != 0
    generated_clips = list((tmp_path / "previews" / "review-fixture").glob("item_*_clip.mp4"))
    assert generated_clips
