from __future__ import annotations

import argparse
import os
from pathlib import Path

import ia_kissing_pipeline.main as main_module
from ia_kissing_pipeline.db import connect, init_db
from ia_kissing_pipeline.ingest.store import upsert_film_item
from tests.test_video_pipeline import make_fixture_video


def test_handle_review_command_approves_item(tmp_path: Path) -> None:
    fixture_video = tmp_path / "fixture.mp4"
    make_fixture_video(fixture_video)
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)
    os.environ["DB_PATH"] = str(db_path)
    os.environ["DOWNLOAD_DIR"] = str(tmp_path / "downloads")
    os.environ["FRAME_DIR"] = str(tmp_path / "frames")
    os.environ["CACHE_DIR"] = str(tmp_path / "cache")
    os.environ["PREVIEW_DIR"] = str(tmp_path / "previews")
    os.environ["LOG_DIR"] = str(tmp_path / "logs")

    with connect(db_path) as conn:
        upsert_film_item(
            conn,
            {
                "archive_identifier": "openclaw-review",
                "title": "OpenClaw Review Fixture",
                "item_url": "https://archive.org/details/openclaw-review",
                "subjects": ["romance"],
                "metadata_score": 0.8,
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
        conn.execute(
            "INSERT INTO moments (film_id, shot_id, start_seconds, peak_seconds, end_seconds, confidence, reason_json, created_at) VALUES (1, NULL, 0, 1, 2, 0.8, '{\"summary\":\"test\"}', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO review_batches (batch_date, channel, account_id, peer_id, status, created_at) VALUES ('2026-03-22', 'local', 'local', 'local', 'pending', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO review_items (review_batch_id, moment_id, display_index, created_at) VALUES (1, 1, 1, datetime('now'))"
        )
        conn.commit()

    main_module.cmd_handle_review_command(argparse.Namespace(text="1", channel=None, target=None))

    with connect(db_path) as conn:
        approved = conn.execute("SELECT approved FROM moments WHERE id = 1").fetchone()["approved"]
    assert approved == 1
