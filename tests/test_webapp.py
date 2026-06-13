from __future__ import annotations
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from ia_kissing_pipeline.config import load_settings
from ia_kissing_pipeline.db import get_connection, init_db
from ia_kissing_pipeline.ingest.fixture_ingest import ingest_fixture
from ia_kissing_pipeline.main import run_metadata_scoring
from ia_kissing_pipeline.webapp import (
    _find_first_workflow_image,
    _build_manual_clip_now,
    _canonicalize_ingestor_title,
    _call_codex_ingestor_title_canonicalizer,
    _cleanup_nonpending_local_artifacts,
    _run_kiss_detector_now,
    _start_get_more_vids,
    create_app,
)


def test_webapp_index_and_film_detail(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
    run_metadata_scoring(settings)

    app = create_app()
    client = app.test_client()

    index_response = client.get("/")
    films_response = client.get("/films")
    films_status_response = client.get("/films/status")
    detail_response = client.get("/films/1")
    clips_response = client.get("/clips")
    admin_response = client.get("/admin")

    assert index_response.status_code == 200
    assert b"No Ready Film Yet" in index_response.data
    assert films_response.status_code == 200
    assert b"Kiss in Spring" in films_response.data
    assert b"Review Data" in films_response.data
    assert films_status_response.status_code == 200
    assert b"films" in films_status_response.data
    assert detail_response.status_code == 200
    assert b"Available Metadata" in detail_response.data
    assert b"archive identifier" in detail_response.data
    assert b"Build / Refresh Skim Preview" in detail_response.data
    assert b"Skim Overview" in detail_response.data
    assert b"skim-overview-grid" in detail_response.data
    assert b"Kiss Detector" in detail_response.data
    assert b"skim-viewport" in detail_response.data
    assert b"No Kissing Scenes. Show Me New Video" not in detail_response.data
    assert clips_response.status_code == 200
    assert admin_response.status_code == 200
    assert b"Run Get More Films" in admin_response.data


def test_ingestor_page_runs_dry_metadata_probe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)

    def fake_fetch_search_page(self, query: str, page: int, rows: int) -> dict:
        if query.startswith('title:"Blind Alley"'):
            return {
                "response": {
                    "docs": [
                        {
                            "identifier": "blind-alley_202407",
                            "title": "Blind Alley",
                            "year": 1939,
                            "language": "eng",
                            "collection": "feature_films_unsorted",
                        },
                        {
                            "identifier": "blind-alley-colorized",
                            "title": "Blind Alley (restored, colorized)",
                            "year": 1939,
                            "language": "eng",
                            "collection": "feature_films_unsorted",
                        },
                        {
                            "identifier": "blind-alley-es-dub",
                            "title": "Blind Alley",
                            "year": 1939,
                            "language": "spa",
                            "collection": "feature_films_unsorted",
                        },
                        {
                            "identifier": "blind-alley-travelogue",
                            "title": "Blind Alley Travelogue",
                            "year": 1939,
                            "language": "eng",
                            "collection": "feature_films_unsorted",
                        },
                        {
                            "identifier": "blind-alley-audio",
                            "title": "Blind Alley OST",
                            "year": 1939,
                            "language": "eng",
                            "collection": "opensource_audio",
                        },
                    ]
                }
            }
        return {
            "response": {
                "docs": [
                    {
                        "identifier": "blind-alley_202407",
                        "title": "Blind Alley",
                        "year": 1939,
                        "description": "Crime drama.",
                        "subject": ["Crime", "Drama"],
                        "creator": None,
                        "collection": "feature_films_unsorted",
                        "language": "eng",
                        "runtime": None,
                        "licenseurl": None,
                    }
                ]
            }
        }

    def fake_fetch_metadata(self, identifier: str) -> dict:
        if identifier == "blind-alley_202407":
            return {
                "metadata": {
                    "title": "Blind Alley",
                    "description": "Gangster Hal Wilson takes psychiatrist Dr. Shelby hostage.",
                    "subject": ["Crime", "Drama"],
                    "collection": "feature_films_unsorted",
                    "language": "eng",
                    "year": "1939",
                },
                "files": [],
            }
        if identifier == "blind-alley-colorized":
            return {
                "metadata": {
                    "title": "Blind Alley (restored, colorized)",
                    "description": "Restored edition of Blind Alley.",
                    "subject": ["Crime", "Drama"],
                    "collection": "feature_films_unsorted",
                    "language": "eng",
                    "year": "1939",
                },
                "files": [],
            }
        if identifier == "blind-alley-es-dub":
            return {
                "metadata": {
                    "title": "Blind Alley",
                    "description": "Spanish dub release.",
                    "subject": ["Crime", "Drama"],
                    "collection": "feature_films_unsorted",
                    "language": "spa",
                    "year": "1939",
                },
                "files": [],
            }
        if identifier == "blind-alley-travelogue":
            return {
                "metadata": {
                    "title": "Blind Alley Travelogue",
                    "description": "A travelogue documentary through urban backstreets.",
                    "subject": ["travelogue", "documentary"],
                    "collection": "feature_films_unsorted",
                    "language": "eng",
                    "year": "1939",
                },
                "files": [],
            }
        if identifier == "blind-alley-audio":
            return {
                "metadata": {
                    "title": "Blind Alley OST",
                    "description": "Audio soundtrack release.",
                    "subject": ["soundtrack"],
                    "collection": "opensource_audio",
                    "language": "eng",
                    "year": "1939",
                },
                "files": [
                    {
                        "name": "blind-alley.flac",
                        "format": "FLAC",
                        "size": "12345",
                    }
                ],
            }
        raise AssertionError(identifier)

    monkeypatch.setattr("ia_kissing_pipeline.ingest.ia_client.IAClient.fetch_search_page", fake_fetch_search_page)
    monkeypatch.setattr("ia_kissing_pipeline.ingest.ia_client.IAClient.fetch_metadata", fake_fetch_metadata)

    app = create_app()
    client = app.test_client()
    response = client.get("/ingestor?run=1&limit=1&rows=1&duplicate_rows=5")

    assert response.status_code == 200
    assert b"Ingestor" in response.data
    assert b"1. Checkpoint" in response.data
    assert b"2. IA Search" in response.data
    assert b"3. Metadata Debug" in response.data
    assert b"4. Duplicate Probe" in response.data
    assert b"Blind Alley" in response.data
    assert b"title cleanup" in response.data
    assert b"variant flags on candidate" in response.data
    assert b"reject as obvious derivative variant" in response.data
    assert b"reject as non-film sibling" in response.data
    assert b"travelogue material" in response.data
    assert b"no video files on the Internet Archive item" in response.data
    assert b"restoration variant" in response.data
    assert b"blind-alley-es-dub" in response.data


def test_canonicalize_ingestor_title_strips_parenthetical_suffixes() -> None:
    result = _canonicalize_ingestor_title("Never Take Candy From A Stranger (restored, colorized)")
    assert result["canonical_title"] == "Never Take Candy From A Stranger"
    assert "canonicalized sibling-search title" in result["decisions"][0]["decision"]

    result = _canonicalize_ingestor_title("Film Title [Spanish Dub]")
    assert result["canonical_title"] == "Film Title"


def test_canonicalize_ingestor_title_uses_codex_refinement(monkeypatch) -> None:
    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._call_codex_ingestor_title_canonicalizer",
        lambda original, cleaned: {"status": "ok", "title": "Never Take Candy from a Stranger"},
    )
    result = _canonicalize_ingestor_title("Never Take Candy From A Stranger (restored, colorized)")
    assert result["canonical_title"] == "Never Take Candy from a Stranger"
    assert any(item["heuristic"] == "codex title canonicalizer" for item in result["decisions"])


def test_source_archive_excludes_env_and_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))

    archive_root = tmp_path / "fake-root"
    (archive_root / "src").mkdir(parents=True)
    (archive_root / "data").mkdir(parents=True)
    (archive_root / "src" / "app.py").write_text("print('ok')\n")
    (archive_root / ".env").write_text("SECRET=123\n")
    (archive_root / "data" / "pipeline.db").write_text("db")

    monkeypatch.setattr("ia_kissing_pipeline.webapp._code_archive_root", lambda: archive_root)

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)

    app = create_app()
    client = app.test_client()
    response = client.get("/source")

    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(archive.namelist())
    assert "src/app.py" in names
    assert ".env" not in names
    assert "data/pipeline.db" not in names


def test_call_codex_ingestor_title_canonicalizer_uses_stdin_prompt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TITLE_CANONICALIZER", "1")
    monkeypatch.setenv("IA_KISSING_CODEX_WORKDIR", str(tmp_path / "codex-workdir"))
    monkeypatch.setattr("ia_kissing_pipeline.webapp.shutil.which", lambda _: "/usr/bin/codex")

    def fake_run(args, **kwargs):
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text("Canonical Title")
        assert args[-1] == "-"
        assert "Original title: Noisy Title" in kwargs["input"]
        assert "Cleaned candidate: Clean Title" in kwargs["input"]
        class Completed:
            returncode = 0
            stderr = ""
        return Completed()

    monkeypatch.setattr("ia_kissing_pipeline.webapp.subprocess.run", fake_run)
    result = _call_codex_ingestor_title_canonicalizer("Noisy Title", "Clean Title")
    assert result["status"] == "ok"
    assert result["title"] == "Canonical Title"


def test_cleanup_nonpending_local_artifacts_keeps_db_rows(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute("UPDATE films SET status = 'excluded_metadata' WHERE id = 2")
        conn.execute(
            """
            INSERT INTO films (
                archive_identifier, title, year, description, subjects_json, creator, collection,
                language, runtime_seconds, item_url, license_text, license_url, rights_notes,
                rights_confidence, rights_confidence_score, metadata_score, metadata_reason_json,
                status, ingested_at, updated_at
            ) VALUES (
                'synthetic-keep', 'Synthetic Keep', 1950, '', '[]', NULL, 'feature_films',
                NULL, NULL, 'https://archive.org/details/synthetic-keep', NULL, NULL, '{}',
                NULL, 0, 0, '{}', 'metadata_scored', '2026-03-24T00:00:00Z', '2026-03-24T00:00:00Z'
            )
            """
        )
        conn.execute(
            "INSERT INTO film_reviews (film_id, review_status, reviewed_at, cleanup_completed) VALUES (2, 'no_kiss', '2026-03-24T00:00:00Z', 1)"
        )
        conn.execute(
            "INSERT INTO film_reviews (film_id, review_status, reviewed_at, cleanup_completed) VALUES (3, 'has_kiss', '2026-03-24T00:00:00Z', 1)"
        )
    for archive_identifier in ("kiss_in_spring_1932", "ants_of_industry_1948", "synthetic-keep"):
        for root in (settings.download_dir, settings.frame_dir, settings.preview_dir):
            target = root / archive_identifier
            target.mkdir(parents=True, exist_ok=True)
            (target / "placeholder.txt").write_text("x")
    _cleanup_nonpending_local_artifacts(settings)
    with get_connection(settings.db_path) as conn:
        remaining = [tuple(row) for row in conn.execute("SELECT id, status FROM films ORDER BY id").fetchall()]
    assert remaining == [(1, "metadata_scored"), (2, "excluded_metadata"), (3, "metadata_scored")]
    assert (settings.download_dir / "kiss_in_spring_1932" / "placeholder.txt").exists()
    assert not (settings.download_dir / "ants_of_industry_1948" / "placeholder.txt").exists()


def test_skim_overview_endpoint_builds_and_reuses_images(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    build_calls = {"count": 0}

    def fake_build_skim_overview_frames(skim_preview_path: Path, output_dir: Path, *, force: bool = False):
        build_calls["count"] += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(output_dir.glob("frame_*.jpg"))
        if existing and not force:
            return existing
        created = []
        for index in range(1, 4):
            frame_path = output_dir / f"frame_{index:06d}.jpg"
            frame_path.write_bytes(b"fake-jpeg")
            created.append(frame_path)
        return created

    monkeypatch.setattr(
        "ia_kissing_pipeline.video.skim.build_skim_overview_frames",
        fake_build_skim_overview_frames,
    )
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    app = create_app()
    client = app.test_client()
    response = client.get("/films/1/skim-overview")
    second_response = client.get("/films/1/skim-overview")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["frames"]
    assert payload["frames"][0]["media_url"].startswith("/media/preview/")
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    assert overview_dir.exists()
    assert list(overview_dir.glob("frame_*.jpg"))
    assert build_calls["count"] == 2
    assert second_response.status_code == 200
    assert second_response.get_json()["frames"] == payload["frames"]


def test_kiss_detector_endpoint_builds_and_reuses_images(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key")
    monkeypatch.setenv("ROBOFLOW_WORKSPACE_NAME", "test-workspace")
    monkeypatch.setenv("ROBOFLOW_WORKFLOW_ID", "test-workflow")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    build_calls = {"overview": 0, "detector": 0, "use_workflow_cache": []}

    def fake_build_skim_overview_frames(skim_preview_path: Path, output_dir: Path, *, force: bool = False):
        build_calls["overview"] += 1
        output_dir.mkdir(parents=True, exist_ok=True)
        existing = sorted(output_dir.glob("frame_*.jpg"))
        if existing and not force:
            return existing
        created = []
        for index in range(1, 3):
            frame_path = output_dir / f"frame_{index:06d}.jpg"
            frame_path.write_bytes(b"fake-jpeg")
            created.append(frame_path)
        return created

    def fake_run_roboflow_kiss_detector(settings, frame_path: Path, *, use_workflow_cache: bool = True) -> tuple[bytes | None, object]:
        build_calls["detector"] += 1
        build_calls["use_workflow_cache"].append(use_workflow_cache)
        if frame_path.name.endswith("000001.jpg"):
            return None, {"image": {"height": 18, "width": 32}, "predictions": []}
        image = Image.new("RGB", (32, 18), color=(255, 0, 0))
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), {
            "image": {"height": 18, "width": 32},
            "predictions": [{"class": "head", "confidence": 0.9}],
        }

    monkeypatch.setattr(
        "ia_kissing_pipeline.video.skim.build_skim_overview_frames",
        fake_build_skim_overview_frames,
    )
    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._run_roboflow_kiss_detector",
        fake_run_roboflow_kiss_detector,
    )
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    with get_connection(settings.db_path) as conn:
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'kiss_detector', 'queued', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            ('{"phase":"queued","progress":0.05}',),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    assert _run_kiss_detector_now(job_id, 1) == 0
    assert output_dir.exists()
    assert len(list(output_dir.glob("frame_*.png"))) == 1
    assert len(list(output_dir.glob("frame_*.json"))) == 2
    assert len(list(output_dir.glob("frame_*.skip"))) == 1
    assert build_calls["detector"] == 2
    assert build_calls["use_workflow_cache"] == [True, True]

    app = create_app()
    client = app.test_client()
    detail_response = client.get("/films/1")
    assert b"Analyze Frames" in detail_response.data
    assert b"Analyze Collisions" in detail_response.data
    assert b"Remove Suspicious Masks" in detail_response.data
    assert b"Make Kiss Candidates" in detail_response.data
    assert b"Min size px" in detail_response.data
    assert b"Clear Cache" in detail_response.data
    assert b"Collisions" in detail_response.data
    assert b"Kiss Candidates" in detail_response.data
    assert b"Remove Frames" in detail_response.data
    assert b"Download All Frames" in detail_response.data
    response = client.get("/films/1/kiss-detector")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["completed"] == 1
    assert payload["total"] == 2
    assert payload["done"] is True
    assert payload["status"] == "done"
    assert len(payload["frames"]) == 1
    assert payload["frames"][0]["index"] == 2
    assert payload["frames"][0]["media_url"].startswith("/media/preview/")
    assert payload["frames"][0]["predictions_url"].startswith("/media/preview/")

    download_response = client.get("/films/1/kiss-detector/download-all")
    assert download_response.status_code == 200
    assert download_response.mimetype == "application/zip"

    remove_response = client.post("/films/1/kiss-detector/remove")
    assert remove_response.status_code == 200
    remove_payload = remove_response.get_json()
    assert remove_payload["completed"] == 0
    assert len(list(output_dir.glob("frame_*.png"))) == 0
    assert len(list(output_dir.glob("frame_*.json"))) == 0
    assert len(list(output_dir.glob("frame_*.skip"))) == 0


def test_kiss_detector_collision_analysis_updates_json_and_payload(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "frame_000001.png").write_bytes(b"fake")
    (output_dir / "frame_000002.png").write_bytes(b"fake")
    (output_dir / "frame_000001.json").write_text(
        json.dumps(
            {
                "image": {"height": 18, "width": 32},
                "predictions": [
                    {
                        "class": "head",
                        "points": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 4}, {"x": 0, "y": 4}],
                    },
                    {
                        "class": "mouth",
                        "points": [{"x": 3, "y": 3}, {"x": 7, "y": 3}, {"x": 7, "y": 7}, {"x": 3, "y": 7}],
                    },
                ],
            }
        )
    )
    (output_dir / "frame_000002.json").write_text(
        json.dumps(
            {
                "image": {"height": 18, "width": 32},
                "predictions": [
                    {
                        "class": "head",
                        "points": [{"x": 0, "y": 0}, {"x": 2, "y": 0}, {"x": 2, "y": 2}, {"x": 0, "y": 2}],
                    },
                    {
                        "class": "mouth",
                        "points": [{"x": 4, "y": 4}, {"x": 6, "y": 4}, {"x": 6, "y": 6}, {"x": 4, "y": 6}],
                    },
                ],
            }
        )
    )
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 160), "black").save(overview_dir / "frame_000001.jpg", format="JPEG")
    (overview_dir / "frame_000002.jpg").write_bytes(b"fake-jpeg")

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/analyze-collisions")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["collision_analysis_count"] == 2
    assert [frame["collision"] for frame in payload["frames"]] == [True, False]

    first_payload = json.loads((output_dir / "frame_000001.json").read_text())
    second_payload = json.loads((output_dir / "frame_000002.json").read_text())
    assert first_payload["collision"] is True
    assert second_payload["collision"] is False


def test_kiss_detector_make_candidates_updates_json_and_payload(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, 5):
        (output_dir / f"frame_{index:06d}.png").write_bytes(b"fake")
    (output_dir / "frame_000001.json").write_text(
        json.dumps(
            {
                "collision": True,
                "image": {"height": 40, "width": 40},
                "predictions": [
                    {
                        "class": "head",
                        "width": 10,
                        "height": 10,
                        "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
                    },
                    {
                        "class": "mouth",
                        "width": 10,
                        "height": 10,
                        "points": [{"x": 8, "y": 2}, {"x": 18, "y": 2}, {"x": 18, "y": 12}, {"x": 8, "y": 12}],
                    },
                ],
            }
        )
    )
    (output_dir / "frame_000002.json").write_text(
        json.dumps(
            {
                "collision": True,
                "image": {"height": 40, "width": 40},
                "predictions": [
                    {
                        "class": "head",
                        "width": 10,
                        "height": 10,
                        "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}],
                    },
                    {
                        "class": "hat",
                        "width": 8,
                        "height": 8,
                        "points": [{"x": 1, "y": 1}, {"x": 9, "y": 1}, {"x": 9, "y": 9}, {"x": 1, "y": 9}],
                    },
                ],
            }
        )
    )
    (output_dir / "frame_000003.json").write_text(
        json.dumps(
            {
                "collision": False,
                "image": {"height": 40, "width": 40},
                "predictions": [
                    {
                        "class": "head",
                        "width": 6,
                        "height": 6,
                        "points": [{"x": 0, "y": 0}, {"x": 6, "y": 0}, {"x": 6, "y": 6}, {"x": 0, "y": 6}],
                    },
                    {
                        "class": "mouth",
                        "width": 6,
                        "height": 6,
                        "points": [{"x": 5, "y": 1}, {"x": 11, "y": 1}, {"x": 11, "y": 7}, {"x": 5, "y": 7}],
                    },
                ],
            }
        )
    )
    (output_dir / "frame_000004.json").write_text(
        json.dumps(
            {
                "collision": True,
                "image": {"height": 160, "width": 160},
                "predictions": [
                    {
                        "class": "head",
                        "detection_id": "left-large-a",
                        "confidence": 0.6,
                        "width": 40,
                        "height": 40,
                        "x": 40,
                        "y": 40,
                        "points": [{"x": 20, "y": 20}, {"x": 60, "y": 20}, {"x": 60, "y": 60}, {"x": 20, "y": 60}],
                    },
                    {
                        "class": "head",
                        "detection_id": "left-large-b",
                        "confidence": 0.9,
                        "width": 38,
                        "height": 38,
                        "x": 42,
                        "y": 42,
                        "points": [{"x": 24, "y": 24}, {"x": 62, "y": 24}, {"x": 62, "y": 62}, {"x": 24, "y": 62}],
                    },
                    {
                        "class": "head",
                        "detection_id": "right-head",
                        "confidence": 0.8,
                        "width": 40,
                        "height": 40,
                        "x": 76,
                        "y": 42,
                        "points": [{"x": 56, "y": 22}, {"x": 96, "y": 22}, {"x": 96, "y": 62}, {"x": 56, "y": 62}],
                    },
                    {
                        "class": "head",
                        "detection_id": "bad-strip",
                        "confidence": 0.5,
                        "width": 56,
                        "height": 8,
                        "x": 48,
                        "y": 24,
                        "points": [{"x": 18, "y": 18}, {"x": 78, "y": 18}, {"x": 78, "y": 26}, {"x": 18, "y": 26}],
                    },
                ],
            }
        )
    )
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, 5):
        (overview_dir / f"frame_{index:06d}.jpg").write_bytes(b"fake-jpeg")

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/make-candidates", json={"min_size_pixels": 8})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kiss_candidate_analysis_count"] == 4
    assert payload["kiss_candidate_min_size_pixels"] == 8
    assert [frame["kiss_candidate"] for frame in payload["frames"]] == [True, False, False, True]

    first_payload = json.loads((output_dir / "frame_000001.json").read_text())
    second_payload = json.loads((output_dir / "frame_000002.json").read_text())
    third_payload = json.loads((output_dir / "frame_000003.json").read_text())
    fourth_payload = json.loads((output_dir / "frame_000004.json").read_text())
    assert first_payload["kiss_candidate"] is True
    assert second_payload["kiss_candidate"] is False
    assert third_payload["kiss_candidate"] is False
    assert fourth_payload["kiss_candidate"] is True
    assert first_payload["kiss_candidate_min_size_pixels"] == 8
    assert third_payload["kiss_candidate_cluster_count"] == 0
    assert fourth_payload["kiss_candidate_cluster_count"] == 2
    assert fourth_payload["kiss_candidate_representative_ids"] == ["left-large-b", "right-head"]


def test_kiss_detector_cluster_duplicates_updates_json_and_overlay(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 160), "black").save(output_dir / "frame_000001.png")
    (output_dir / "frame_000001.json").write_text(
        json.dumps(
            {
                "collision": True,
                "image": {"height": 160, "width": 160},
                "predictions": [
                    {
                        "class": "head",
                        "detection_id": "left-large-a",
                        "confidence": 0.6,
                        "width": 40,
                        "height": 40,
                        "x": 40,
                        "y": 40,
                        "points": [{"x": 20, "y": 20}, {"x": 60, "y": 20}, {"x": 60, "y": 60}, {"x": 20, "y": 60}],
                    },
                    {
                        "class": "head",
                        "detection_id": "left-large-b",
                        "confidence": 0.9,
                        "width": 38,
                        "height": 38,
                        "x": 42,
                        "y": 42,
                        "points": [{"x": 24, "y": 24}, {"x": 62, "y": 24}, {"x": 62, "y": 62}, {"x": 24, "y": 62}],
                    },
                    {
                        "class": "head",
                        "detection_id": "right-head",
                        "confidence": 0.8,
                        "width": 40,
                        "height": 40,
                        "x": 76,
                        "y": 42,
                        "points": [{"x": 56, "y": 22}, {"x": 96, "y": 22}, {"x": 96, "y": 62}, {"x": 56, "y": 62}],
                    },
                    {
                        "class": "head",
                        "detection_id": "bad-strip",
                        "confidence": 0.5,
                        "width": 56,
                        "height": 8,
                        "x": 48,
                        "y": 24,
                        "points": [{"x": 18, "y": 18}, {"x": 78, "y": 18}, {"x": 78, "y": 26}, {"x": 18, "y": 26}],
                    },
                ],
            }
        )
    )
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    (overview_dir / "frame_000001.jpg").write_bytes(b"fake-jpeg")

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/cluster", json={"min_size_pixels": 8})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kiss_cluster_analysis_count"] == 1
    assert payload["kiss_cluster_min_size_pixels"] == 8
    assert len(payload["frames"]) == 1
    assert payload["frames"][0]["media_url"].startswith("/media/preview/")

    frame_payload = json.loads((output_dir / "frame_000001.json").read_text())
    assert frame_payload["kiss_cluster_count"] == 3
    assert frame_payload["kiss_cluster_representative_ids"] == ["left-large-a", "left-large-b", "right-head"]
    assert frame_payload["kiss_cluster_groups"] == [["left-large-a"], ["left-large-b"], ["right-head"]]
    assert frame_payload["kiss_cluster_irregular_ids"] == ["bad-strip"]
    overlay = Image.open(output_dir / "frame_000001.png").convert("RGBA")
    overlay_pixels = overlay.load()
    assert overlay_pixels[70, 24][:3] == (0, 0, 0)


def test_kiss_detector_cluster_handles_json_without_png(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "frame_000001.json").write_text(
        json.dumps(
            {
                "collision": True,
                "image": {"height": 160, "width": 160},
                "predictions": [
                    {
                        "class": "head",
                        "detection_id": "shape-a",
                        "confidence": 0.9,
                        "width": 40,
                        "height": 40,
                        "x": 40,
                        "y": 40,
                        "points": [{"x": 20, "y": 20}, {"x": 60, "y": 20}, {"x": 60, "y": 60}, {"x": 20, "y": 60}],
                    },
                    {
                        "class": "head",
                        "detection_id": "shape-b",
                        "confidence": 0.8,
                        "width": 40,
                        "height": 40,
                        "x": 76,
                        "y": 42,
                        "points": [{"x": 56, "y": 22}, {"x": 96, "y": 22}, {"x": 96, "y": 62}, {"x": 56, "y": 62}],
                    },
                ],
            }
        )
    )
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    (overview_dir / "frame_000001.jpg").write_bytes(b"fake-jpeg")

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/cluster", json={"min_size_pixels": 8})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["kiss_cluster_analysis_count"] == 1
    frame_payload = json.loads((output_dir / "frame_000001.json").read_text())
    assert frame_payload["kiss_cluster_count"] == 2


def test_kiss_detector_cluster_skips_non_collision_frames(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    output_dir = settings.preview_dir / "kiss_in_spring_1932" / "kiss-detector"
    output_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), "black").save(output_dir / "frame_000001.png")
    (output_dir / "frame_000001.json").write_text(
        json.dumps(
            {
                "collision": False,
                "image": {"height": 32, "width": 32},
                "predictions": [
                    {
                        "class": "head",
                        "detection_id": "shape-a",
                        "confidence": 0.9,
                        "width": 20,
                        "height": 20,
                        "x": 16,
                        "y": 16,
                        "points": [{"x": 6, "y": 6}, {"x": 26, "y": 6}, {"x": 26, "y": 26}, {"x": 6, "y": 26}],
                    }
                ],
            }
        )
    )
    overview_dir = settings.preview_dir / "kiss_in_spring_1932" / "skim-overview"
    overview_dir.mkdir(parents=True, exist_ok=True)
    (overview_dir / "frame_000001.jpg").write_bytes(b"fake-jpeg")

    before = Image.open(output_dir / "frame_000001.png").tobytes()
    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/cluster", json={"min_size_pixels": 8})

    assert response.status_code == 200
    frame_payload = json.loads((output_dir / "frame_000001.json").read_text())
    assert frame_payload["kiss_cluster_count"] == 0
    assert frame_payload["kiss_cluster_representative_ids"] == []
    assert Image.open(output_dir / "frame_000001.png").tobytes() == before


def test_kiss_detector_analyze_can_disable_workflow_cache(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_path = settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    spawned_commands: list[list[str]] = []

    def fake_spawn_pipeline_command(settings, command: list[str]) -> None:
        spawned_commands.append(command)

    monkeypatch.setattr("ia_kissing_pipeline.webapp._spawn_pipeline_command", fake_spawn_pipeline_command)

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/analyze", json={"use_workflow_cache": False})

    assert response.status_code == 200
    assert spawned_commands
    with get_connection(settings.db_path) as conn:
        job_row = conn.execute(
            "SELECT payload_json FROM analysis_jobs WHERE film_id = 1 AND job_type = 'kiss_detector' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert job_row is not None
    assert json.loads(job_row["payload_json"]) == {"use_workflow_cache": False}


def test_what_is_a_kiss_page_shows_only_kiss_clips_with_timing(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")
    monkeypatch.setenv("ROBOFLOW_API_KEY", "test-key")
    monkeypatch.setenv("ROBOFLOW_WORKSPACE_NAME", "test-workspace")
    monkeypatch.setenv("ROBOFLOW_WORKFLOW_ID", "test-workflow")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        clip_dir = settings.clips_dir / "kiss_in_spring_1932"
        clip_dir.mkdir(parents=True, exist_ok=True)
        kiss_clip_path = clip_dir / "kiss-001.mp4"
        missing_timing_path = clip_dir / "kiss-002.mp4"
        other_tag_path = clip_dir / "dance-001.mp4"
        kiss_clip_path.write_text("clip")
        missing_timing_path.write_text("clip")
        other_tag_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_marks (
                id, film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES
                (1, 1, '', 4, 12, 0, 1, 5, 'kiss', 'kiss', '2026-03-24T00:00:00Z'),
                (2, 1, '', 4, 12, 0, 2, 12, 'kiss', 'kiss', '2026-03-25T00:00:00Z'),
                (3, 1, '', 4, 12, 0, 3, 18, 'dance', 'dance', '2026-03-26T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO manual_clips (
                id, manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at, ignored
            ) VALUES
                (1, 1, 1, ?, 'kiss', '{"kiss_start_seconds": 4.25, "kiss_end_seconds": 5.5}', 5, 9, '2026-03-24T00:00:00Z', 0),
                (2, 1, 1, ?, 'kiss', '{}', 5, 9, '2026-03-25T00:00:00Z', 0),
                (3, 1, 1, ?, 'dance', '{"kiss_start_seconds": 4.25}', 5, 9, '2026-03-26T00:00:00Z', 0)
            """,
            (str(kiss_clip_path), str(missing_timing_path), str(other_tag_path)),
        )
    stale_lead_dir = settings.preview_dir / "what-is-a-kiss" / "clip-0001" / "lead-in"
    stale_lead_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, 21):
        Image.new("RGB", (16, 9), "blue").save(stale_lead_dir / f"frame_{index:02d}.jpg", format="JPEG")

    extracted_frames: list[float] = []

    def fake_ensure_video_frame(video_path: Path, output_path: Path, seconds: float) -> Path:
        extracted_frames.append(seconds)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 36), "black").save(output_path, format="JPEG")
        return output_path

    monkeypatch.setattr("ia_kissing_pipeline.webapp._ensure_video_frame", fake_ensure_video_frame)

    def fake_run_roboflow(settings, frame_path: Path, *, use_workflow_cache: bool = True) -> tuple[bytes | None, object]:
        image = Image.new("RGB", (64, 36), "red")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), {"predictions": [{"class": "head", "confidence": 0.9}]}

    monkeypatch.setattr("ia_kissing_pipeline.webapp._run_roboflow_kiss_detector", fake_run_roboflow)

    app = create_app()
    client = app.test_client()
    response = client.get("/what-is-a-kiss")

    assert response.status_code == 200
    assert b"What Is A Kiss" in response.data
    assert b"clip 1" in response.data
    assert b"kiss 4.25s" in response.data
    assert b"clip 2" not in response.data
    assert b"dance" not in response.data
    assert not extracted_frames

    films_response = client.get("/films")
    assert films_response.status_code == 200
    assert b"What Is A Kiss" in films_response.data

    load_response = client.post("/what-is-a-kiss/1/load-frames")
    assert load_response.status_code == 200
    load_payload = load_response.get_json()
    assert load_payload["kiss_frame_url"].startswith("/media/preview/")
    assert len(load_payload["lead_in_frames"]) == 15
    assert len(extracted_frames) == 16
    assert extracted_frames[0] == 4.25
    assert extracted_frames[1:] == pytest.approx(
        [
            2.45,
            2.65,
            2.85,
            3.05,
            3.25,
            3.45,
            3.65,
            3.85,
            4.05,
            4.25,
            4.45,
            4.65,
            4.85,
            5.05,
            5.25,
        ]
    )
    rebuilt_lead_paths = sorted(stale_lead_dir.glob("frame_*.jpg"))
    assert len(rebuilt_lead_paths) == 15
    assert rebuilt_lead_paths[-1].name == "frame_15.jpg"

    download_response = client.get("/what-is-a-kiss/1/download-frames")
    assert download_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download_response.data)) as zf:
        names = sorted(zf.namelist())
    assert names == [
        "kiss.jpg",
        "sequence/frame_01.jpg",
        "sequence/frame_02.jpg",
        "sequence/frame_03.jpg",
        "sequence/frame_04.jpg",
        "sequence/frame_05.jpg",
        "sequence/frame_06.jpg",
        "sequence/frame_07.jpg",
        "sequence/frame_08.jpg",
        "sequence/frame_09.jpg",
        "sequence/frame_10.jpg",
        "sequence/frame_11.jpg",
        "sequence/frame_12.jpg",
        "sequence/frame_13.jpg",
        "sequence/frame_14.jpg",
        "sequence/frame_15.jpg",
    ]

    analyze_response = client.post("/what-is-a-kiss/1/analyze-frames")
    assert analyze_response.status_code == 200
    analyze_payload = analyze_response.get_json()
    assert analyze_payload["annotated_count"] == 16
    assert analyze_payload["kiss_frame"]["annotated_url"].startswith("/media/preview/")
    assert len(analyze_payload["lead_in_frames"]) == 15
    assert all(frame["annotated_url"].startswith("/media/preview/") for frame in analyze_payload["lead_in_frames"])


def test_force_exclude_marks_review_and_cleans_artifacts(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO film_reviews (film_id, review_status, review_notes, reviewed_at, cleanup_completed, cleanup_at)
            VALUES (1, 'force_excluded', 'old state', '2026-04-01T00:00:00Z', 1, '2026-04-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, error_text, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'queued', '{}', '{}', NULL, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO manual_marks (
                film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, note, created_at
            ) VALUES (1, ?, 4, 12, 1.0, 1, 0, 'test', '2026-03-24T00:00:00Z')
            """,
            (str(settings.preview_dir / "kiss_in_spring_1932" / "skim-preview.mp4"),),
        )
        mark_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        clip_path = settings.clips_dir / "kiss_in_spring_1932" / "manual-mark-001.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_clips (manual_mark_id, film_id, clip_path, start_seconds, end_seconds, created_at)
            VALUES (?, 1, ?, 0, 10, '2026-03-24T00:00:00Z')
            """,
            (mark_id, str(clip_path)),
        )
    download_file = settings.download_dir / "kiss_in_spring_1932" / "source.mp4"
    download_file.parent.mkdir(parents=True, exist_ok=True)
    download_file.write_text("video")

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/force-exclude", follow_redirects=False)

    assert response.status_code == 302
    with get_connection(settings.db_path) as conn:
        review = conn.execute("SELECT review_status, cleanup_completed FROM film_reviews WHERE film_id = 1").fetchone()
        marks = conn.execute("SELECT count(*) AS count FROM manual_marks WHERE film_id = 1").fetchone()["count"]
        clips = conn.execute("SELECT count(*) AS count FROM manual_clips WHERE film_id = 1").fetchone()["count"]
    assert review["review_status"] == "force_excluded"
    assert int(review["cleanup_completed"]) == 1
    assert marks == 0
    assert clips == 0
    assert not download_file.exists()
    assert not clip_path.exists()


def test_kiss_detector_analyze_route_queues_background_job(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    spawned = {}
    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._spawn_pipeline_command",
        lambda settings, command: spawned.setdefault("command", command),
    )
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_dir = settings.preview_dir / "kiss_in_spring_1932"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / "skim-preview.mp4"
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/analyze")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "queued"
    assert "kiss-detector-job" in " ".join(spawned["command"])
    with get_connection(settings.db_path) as conn:
        job = conn.execute(
            "SELECT job_type, status FROM analysis_jobs WHERE film_id = 1 AND job_type = 'kiss_detector' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert job["job_type"] == "kiss_detector"
    assert job["status"] == "queued"


def test_kiss_detector_stop_route_interrupts_active_job(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    terminated = {}
    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._terminate_film_workers",
        lambda film_id: terminated.setdefault("film_id", film_id),
    )
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        preview_dir = settings.preview_dir / "kiss_in_spring_1932"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / "skim-preview.mp4"
        preview_path.write_bytes(b"fake-preview")
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'done', '{}', ?, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """,
            (
                '{"output_fps":12,"preview_path":"%s","sample_every_seconds":4}' % str(preview_path),
            ),
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'kiss_detector', 'running', '{}', ?, '2026-04-01T00:00:01Z', '2026-04-01T00:00:01Z')
            """,
            ('{"phase":"detecting_frames","progress":0.5}',),
        )

    app = create_app()
    client = app.test_client()
    response = client.post("/films/1/kiss-detector/stop")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "error"
    assert payload["error"] == "kiss detector interrupted by user"
    assert terminated["film_id"] == 1
    with get_connection(settings.db_path) as conn:
        job = conn.execute(
            "SELECT status, error_text FROM analysis_jobs WHERE film_id = 1 AND job_type = 'kiss_detector' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert job["status"] == "error"
    assert job["error_text"] == "kiss detector interrupted by user"


def test_find_first_workflow_image_accepts_label_visualization_output() -> None:
    payload = [
        {
            "label_visualization_output": "/9j/4AAQSkZJRgABAQAAAQABAAD",
            "predictions": {"image": {"height": 360, "width": 640}, "predictions": []},
        }
    ]

    image_payload = _find_first_workflow_image(payload)

    assert image_payload == {"type": "base64", "value": "/9j/4AAQSkZJRgABAQAAAQABAAD"}


def test_random_clips_api_returns_json_payload(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        clip_dir = settings.clips_dir / "kiss_in_spring_1932"
        clip_dir.mkdir(parents=True, exist_ok=True)
        kiss_clip = clip_dir / "manual-mark-001.mp4"
        phone_clip = clip_dir / "manual-mark-002.mp4"
        kiss_clip.write_text("kiss clip")
        phone_clip.write_text("phone clip")
        conn.execute(
            """
            INSERT INTO manual_marks (
                id, film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES
                (1, 1, '', 4, 12, 0, 1, 5, 'kiss', 'kiss', '2026-03-24T00:00:00Z'),
                (2, 1, '', 4, 12, 0, 2, 12, 'phone', 'phone', '2026-03-24T00:00:01Z')
            """
        )
        conn.execute(
            """
            INSERT INTO manual_clips (
                manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at
            ) VALUES
                (1, 1, ?, 'kiss', '{"kiss_start_seconds": 1.25, "kiss_end_seconds": 2.5}', 5, 9, '2026-03-24T00:00:00Z'),
                (2, 1, ?, 'phone', '{}', 12, 17, '2026-03-24T00:00:01Z')
            """,
            (str(kiss_clip), str(phone_clip)),
        )

    app = create_app()
    client = app.test_client()

    response = client.get("/api/random-clips?limit=2")
    tag_response = client.get("/api/random-clips?tag=kiss")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 2
    assert len(payload["clips"]) == 2
    assert all(item["media_url"].startswith("/media/clip/") for item in payload["clips"])

    assert tag_response.status_code == 200
    tag_payload = tag_response.get_json()
    assert tag_payload["count"] == 1
    assert tag_payload["clips"][0]["tag"] == "kiss"
    assert tag_payload["clips"][0]["title"] == "Kiss in Spring"
    assert tag_payload["clips"][0]["kiss_start_seconds"] == 1.25
    assert tag_payload["clips"][0]["kiss_end_seconds"] == 2.5


def test_random_clips_api_ordered_mode_uses_clip_id_order(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        clip_dir = settings.clips_dir / "kiss_in_spring_1932"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_a = clip_dir / "manual-mark-001.mp4"
        clip_b = clip_dir / "manual-mark-002.mp4"
        clip_a.write_text("a")
        clip_b.write_text("b")
        conn.execute(
            """
            INSERT INTO manual_marks (
                id, film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES
                (1, 1, '', 4, 12, 0, 1, 5, 'kiss', 'kiss', '2026-03-24T00:00:00Z'),
                (2, 1, '', 4, 12, 0, 2, 12, 'kiss', 'kiss', '2026-03-24T00:00:01Z')
            """
        )
        conn.execute(
            """
            INSERT INTO manual_clips (
                id, manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at, ignored
            ) VALUES
                (10, 1, 1, ?, 'kiss', '{}', 5, 9, '2026-03-24T00:00:00Z', 0),
                (20, 2, 1, ?, 'kiss', '{}', 12, 17, '2026-03-24T00:00:01Z', 0)
            """,
            (str(clip_a), str(clip_b)),
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('clip_order_mode', 'ordered')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )

    app = create_app()
    client = app.test_client()
    payload = client.get("/api/random-clips?tag=kiss&limit=1").get_json()
    payload_next = client.get("/api/random-clips?tag=kiss&limit=1").get_json()
    payload_wrap = client.get("/api/random-clips?tag=kiss&limit=1").get_json()

    assert payload["count"] == 1
    assert [clip["id"] for clip in payload["clips"]] == [10]
    assert [clip["id"] for clip in payload_next["clips"]] == [20]
    assert [clip["id"] for clip in payload_wrap["clips"]] == [10]


def test_review_data_allows_deleting_source_error_clip_file(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'source_error' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO manual_marks (
                id, film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 1, '', 4, 12, 0, 1, 5, 'kiss', 'kiss', '2026-03-24T00:00:00Z')
            """
        )
        clip_dir = settings.clips_dir / "kiss_in_spring_1932"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / "manual-mark-001.mp4"
        clip_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_clips (
                id, manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at
            ) VALUES
                (1, 1, 1, ?, 'kiss', '{}', 5, 9, '2026-03-24T00:00:00Z')
            """,
            (str(clip_path),),
        )

    app = create_app()
    client = app.test_client()

    review_data_response = client.get("/review_data")
    delete_response = client.post(
        "/review_data/delete",
        data={"kind": "clip", "relpath": "kiss_in_spring_1932/manual-mark-001.mp4"},
        follow_redirects=False,
    )

    assert review_data_response.status_code == 200
    assert b"Delete video file" in review_data_response.data
    assert delete_response.status_code == 302
    assert not clip_path.exists()
    with get_connection(settings.db_path) as conn:
        clip_count = conn.execute("SELECT COUNT(*) AS count FROM manual_clips WHERE id = 1").fetchone()["count"]
    assert clip_count == 0


def test_start_get_more_vids_starts_explicit_batch(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
    run_metadata_scoring(settings)

    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._spawn_pipeline_command",
        lambda settings, command: None,
    )
    started, job_id = _start_get_more_vids(settings, 3)
    assert started is True
    assert isinstance(job_id, int)
    with get_connection(settings.db_path) as conn:
        job = conn.execute(
            "SELECT job_type, status, payload_json FROM analysis_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert job["job_type"] == "download_batch"
    assert job["status"] == "queued"


def test_admin_post_starts_get_more_vids_job(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
    run_metadata_scoring(settings)

    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._spawn_pipeline_command",
        lambda settings, command: None,
    )

    app = create_app()
    client = app.test_client()
    response = client.post("/admin/get-more-films", data={"count": "4"}, follow_redirects=False)

    assert response.status_code == 302
    assert "/admin?" in response.headers["Location"]
    with get_connection(settings.db_path) as conn:
        job = conn.execute(
            "SELECT job_type, status, payload_json FROM analysis_jobs WHERE film_id IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert job["job_type"] == "download_batch"
    assert job["status"] == "queued"
    assert '"count": 4' in job["payload_json"]


def test_update_mark_tag_updates_mark_and_clip(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO manual_marks (
                film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 'x', 4, 12, 1.0, 1, 0.0, NULL, NULL, '2026-03-24T00:00:00Z')
            """
        )
        mark_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        clip_path = settings.clips_dir / "kiss_in_spring_1932" / "manual-mark-001.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_clips (manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at)
            VALUES (?, 1, ?, NULL, '{}', 0, 10, '2026-03-24T00:00:00Z')
            """,
            (mark_id, str(clip_path)),
        )

    app = create_app()
    client = app.test_client()
    response = client.post(f"/marks/{mark_id}/tag", data={"selected_tag": "dance", "return_film_id": "1"}, follow_redirects=False)

    assert response.status_code == 302
    with get_connection(settings.db_path) as conn:
        mark = conn.execute("SELECT selected_tag, note FROM manual_marks WHERE id = ?", (mark_id,)).fetchone()
        clip = conn.execute("SELECT clip_tag, metadata_json FROM manual_clips WHERE manual_mark_id = ?", (mark_id,)).fetchone()
    assert mark["selected_tag"] == "dance"
    assert mark["note"] == "dance"
    assert clip["clip_tag"] == "dance"
    assert '"tag":"dance"' in clip["metadata_json"].replace(" ", "")


def test_update_clip_kiss_timing_updates_metadata_json(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO manual_marks (
                film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 'x', 4, 12, 1.0, 1, 0.0, 'kiss', 'kiss', '2026-03-24T00:00:00Z')
            """
        )
        mark_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        clip_path = settings.clips_dir / "kiss_in_spring_1932" / "manual-mark-001.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_clips (manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at)
            VALUES (?, 1, ?, 'kiss', '{}', 0, 10, '2026-03-24T00:00:00Z')
            """,
            (mark_id, str(clip_path)),
        )
        clip_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    app = create_app()
    client = app.test_client()
    response = client.post(
        f"/clips/{clip_id}/kiss-timing",
        data={"kiss_start_seconds": "2.500", "kiss_end_seconds": "4.250", "return_film_id": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    with get_connection(settings.db_path) as conn:
        clip = conn.execute("SELECT metadata_json FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
    compact = clip["metadata_json"].replace(" ", "")
    assert '"kiss_start_seconds":2.5' in compact
    assert '"kiss_end_seconds":4.25' in compact


def test_finalize_review_persists_clip_kiss_timing(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO manual_marks (
                film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 'x', 4, 12, 1.0, 1, 0.0, 'kiss', 'kiss', '2026-03-24T00:00:00Z')
            """
        )
        mark_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        clip_path = settings.clips_dir / "kiss_in_spring_1932" / "manual-mark-001.mp4"
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        clip_path.write_text("clip")
        conn.execute(
            """
            INSERT INTO manual_clips (manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at)
            VALUES (?, 1, ?, 'kiss', '{}', 0, 10, '2026-03-24T00:00:00Z')
            """,
            (mark_id, str(clip_path)),
        )
        clip_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    app = create_app()
    client = app.test_client()
    response = client.post(
        "/films/1/finalize",
        data={
            "action": "has_kiss",
            "clip_timings_json": f'[{{"clip_id":{clip_id},"kiss_start_seconds":"1.5","kiss_end_seconds":"2.25"}}]',
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with get_connection(settings.db_path) as conn:
        clip = conn.execute("SELECT metadata_json FROM manual_clips WHERE id = ?", (clip_id,)).fetchone()
        review = conn.execute("SELECT review_status FROM film_reviews WHERE film_id = 1").fetchone()
    compact = clip["metadata_json"].replace(" ", "")
    assert '"kiss_start_seconds":1.5' in compact
    assert '"kiss_end_seconds":2.25' in compact
    assert review["review_status"] == "has_kiss"


def test_build_manual_clip_defaults_kiss_start_to_pre_seconds_for_kiss_tag(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    source_dir = settings.download_dir / "kiss_in_spring_1932"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_path = source_dir / "source.mp4"
    source_path.write_text("source")
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO manual_marks (
                film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 'x', 4, 12, 1.0, 3, 50.0, 'kiss', 'kiss', '2026-03-24T00:00:00Z')
            """
        )
        mark_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (1, 'build_manual_clip', 'queued', '{}', '{}', '2026-03-24T00:00:00Z', '2026-03-24T00:00:00Z')
            """
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._resolve_source_video",
        lambda conn, settings, film_id, prefer_largest=True: ({"archive_identifier": "kiss_in_spring_1932"}, "", source_path),
    )

    def fake_extract_clip(source_path_arg, clip_path_arg, start_seconds_arg, duration_arg):
        Path(clip_path_arg).parent.mkdir(parents=True, exist_ok=True)
        Path(clip_path_arg).write_text("clip")

    monkeypatch.setattr("ia_kissing_pipeline.video.extract_clips.extract_clip", fake_extract_clip)

    rc = _build_manual_clip_now(job_id, 1, mark_id, 20.0, 20.0)

    assert rc == 0
    with get_connection(settings.db_path) as conn:
        clip = conn.execute("SELECT metadata_json FROM manual_clips WHERE manual_mark_id = ?", (mark_id,)).fetchone()
    compact = clip["metadata_json"].replace(" ", "")
    assert '"kiss_start_seconds":20.0' in compact


def test_ignore_clip_hides_it_from_random_api_and_media(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        clip_dir = settings.clips_dir / "kiss_in_spring_1932"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / "manual-mark-001.mp4"
        clip_path.write_text("kiss clip")
        conn.execute(
            """
            INSERT INTO manual_marks (
                id, film_id, skim_path, skim_sample_every_seconds, skim_output_fps,
                preview_seconds, sample_index, source_seconds, selected_tag, note, created_at
            ) VALUES (1, 1, '', 4, 12, 0, 1, 5, 'kiss', 'kiss', '2026-03-24T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO manual_clips (
                id, manual_mark_id, film_id, clip_path, clip_tag, metadata_json, start_seconds, end_seconds, created_at, ignored
            ) VALUES
                (1, 1, 1, ?, 'kiss', '{}', 5, 9, '2026-03-24T00:00:00Z', 0)
            """,
            (str(clip_path),),
        )

    app = create_app()
    client = app.test_client()

    response = client.post("/clips/1/ignore", data={"return_film_id": "1"}, follow_redirects=False)
    assert response.status_code == 302

    with get_connection(settings.db_path) as conn:
        ignored = conn.execute("SELECT ignored FROM manual_clips WHERE id = 1").fetchone()["ignored"]
    assert int(ignored) == 1

    api_payload = client.get("/api/random-clips?tag=kiss").get_json()
    assert api_payload["count"] == 0

    media_response = client.get("/media/clip/kiss_in_spring_1932/manual-mark-001.mp4")
    assert media_response.status_code == 404


def test_review_data_lists_video_files_and_pending_status(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
    download_dir = settings.download_dir / "kiss_in_spring_1932"
    download_dir.mkdir(parents=True, exist_ok=True)
    (download_dir / "kiss_in_spring_1932.mp4").write_text("video")
    loose_video = settings.db_path.parent / "phase3_fixture.mp4"
    loose_video.write_text("fixture")

    app = create_app()
    client = app.test_client()
    response = client.get("/review_data")

    assert response.status_code == 200
    assert b"Review Data" in response.data
    assert b"Downloaded Sources" in response.data
    assert b"Loose Data Videos" in response.data
    assert b"pending review" in response.data
    assert b"phase3_fixture.mp4" in response.data
    assert b"Requeue movie" in response.data
    assert response.data.count(b"Delete video file") == 1


def test_review_data_delete_removes_video_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    target_dir = settings.download_dir / "sample_movie"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "sample.mp4"
    target_file.write_text("video")

    app = create_app()
    client = app.test_client()
    response = client.post(
        "/review_data/delete",
        data={"kind": "download", "relpath": "sample_movie/sample.mp4"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert not target_file.exists()


def test_review_data_requeue_creates_build_skim_job(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "ia_items.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pipeline.db"))
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DOWNLOAD_DIR", str(tmp_path / "downloads"))
    monkeypatch.setenv("FRAME_DIR", str(tmp_path / "frames"))
    monkeypatch.setenv("PREVIEW_DIR", str(tmp_path / "previews"))
    monkeypatch.setenv("CLIPS_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("IA_KISSING_DISABLE_QUEUE_FILL", "1")

    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        ingest_fixture(conn, fixture_path)
        conn.execute("UPDATE films SET status = 'metadata_scored' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO film_reviews (film_id, review_status, review_notes, reviewed_at, cleanup_completed, cleanup_at)
            VALUES (1, 'force_excluded', 'old state', '2026-04-01T00:00:00Z', 1, '2026-04-01T00:00:00Z')
            """
        )
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, error_text, created_at, updated_at)
            VALUES (1, 'build_skim_preview', 'queued', '{}', '{}', NULL, '2026-04-01T00:00:00Z', '2026-04-01T00:00:00Z')
            """
        )

    spawned = {}
    monkeypatch.setattr(
        "ia_kissing_pipeline.webapp._spawn_pipeline_command",
        lambda settings, command: spawned.setdefault("command", command),
    )

    app = create_app()
    client = app.test_client()
    response = client.post("/review_data/requeue", data={"film_id": "1"}, follow_redirects=False)

    assert response.status_code == 302
    with get_connection(settings.db_path) as conn:
        job = conn.execute(
            "SELECT job_type, status, film_id FROM analysis_jobs WHERE job_type = 'build_skim_preview' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        old_job = conn.execute(
            "SELECT status, error_text FROM analysis_jobs WHERE job_type = 'build_skim_preview' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        review = conn.execute("SELECT review_status, cleanup_completed FROM film_reviews WHERE film_id = 1").fetchone()
    assert job["job_type"] == "build_skim_preview"
    assert job["status"] == "queued"
    assert int(job["film_id"]) == 1
    assert old_job["status"] == "error"
    assert old_job["error_text"] == "superseded by manual requeue"
    assert review["review_status"] == "pending"
    assert int(review["cleanup_completed"]) == 0
    assert "build-skim-job" in " ".join(spawned["command"])
