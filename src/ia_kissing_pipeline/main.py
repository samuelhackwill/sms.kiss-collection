from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from ia_kissing_pipeline.config import load_settings
from ia_kissing_pipeline.db import get_connection, init_db
from ia_kissing_pipeline.ingest.ia_client import IAClient
from ia_kissing_pipeline.ingest.ia_ingest import ingest_from_ia
from ia_kissing_pipeline.ingest.fixture_ingest import ingest_fixture
from ia_kissing_pipeline.review.batch_formatter import format_review_batch
from ia_kissing_pipeline.review.commands import parse_review_command
from ia_kissing_pipeline.scoring.metadata_rules import score_metadata
from ia_kissing_pipeline.scoring.rights_score import score_rights
from ia_kissing_pipeline.scoring.text_gate import evaluate_text_gate
from ia_kissing_pipeline.utils.time import utc_now_iso
from ia_kissing_pipeline.video.probe import probe_media
from ia_kissing_pipeline.video.sampling import build_sample_windows
from ia_kissing_pipeline.video.scenedetect import build_shots, extract_keyframe
from ia_kissing_pipeline.video.extract_clips import extract_clip
from ia_kissing_pipeline.video.skim import build_skim_preview
from ia_kissing_pipeline.video.transcode import (
    build_analysis_path,
    choose_preferred_file,
    choose_source_files,
    create_analysis_video,
    ensure_source_video,
)


def cmd_init_db(_args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    print(f"Initialized database at {settings.db_path}")
    return 0


def cmd_ingest_fixture(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    with get_connection(settings.db_path) as conn:
        result = ingest_fixture(conn, Path(args.fixture_path))
    print(json.dumps(result, indent=2))
    return 0


def cmd_ingest_ia(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)
    client = IAClient(settings.cache_dir, settings.user_agent, throttle_seconds=args.throttle_seconds)
    with get_connection(settings.db_path) as conn:
        result = ingest_from_ia(
            conn,
            client,
            query=args.query,
            limit=args.limit,
            rows=args.rows,
            checkpoint_key=args.checkpoint_key,
        )
    print(json.dumps(result.__dict__, indent=2, sort_keys=True))
    return 0


def run_metadata_scoring(settings) -> int:
    updated = 0
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM films").fetchall()
        for row in rows:
            subjects = json.loads(row["subjects_json"])
            result = score_metadata(row["title"], row["description"], subjects, row["collection"])
            current_status = row["status"]
            if result.blocked:
                next_status = "excluded_metadata"
            elif current_status in ("text_gate_passed", "rights_screened"):
                next_status = current_status
            else:
                next_status = "metadata_scored"
            conn.execute(
                """
                UPDATE films
                SET metadata_score = ?, metadata_reason_json = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    result.score,
                    json.dumps(result.reasons, sort_keys=True),
                    next_status,
                    now,
                    row["id"],
                ),
            )
            updated += 1
    return updated


def cmd_score_metadata(_args: argparse.Namespace) -> int:
    settings = load_settings()
    updated = run_metadata_scoring(settings)
    print(f"Scored metadata for {updated} films")
    return 0


def run_text_gate_scoring(settings) -> int:
    updated = 0
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM films WHERE status = 'metadata_scored'").fetchall()
        for row in rows:
            subjects = json.loads(row["subjects_json"])
            result = evaluate_text_gate(
                title=row["title"],
                description=row["description"],
                subjects=subjects,
                collection=row["collection"],
                year=row["year"],
                creator=row["creator"],
            )
            existing_reasons = json.loads(row["metadata_reason_json"] or "{}")
            existing_reasons["text_gate"] = {
                "decision": result.decision,
                "passed": result.passed,
                "source": result.source,
                "confidence": result.confidence,
                "reasons": result.reasons,
            }
            next_status = "text_gate_passed" if result.passed else "excluded_text_gate"
            conn.execute(
                """
                UPDATE films
                SET metadata_reason_json = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_reasons, sort_keys=True),
                    next_status,
                    now,
                    row["id"],
                ),
            )
            updated += 1
    return updated


def cmd_score_text_gate(_args: argparse.Namespace) -> int:
    settings = load_settings()
    updated = run_text_gate_scoring(settings)
    print(f"Scored text gate for {updated} films")
    return 0


def run_rights_scoring(settings) -> int:
    updated = 0
    now = utc_now_iso()
    with get_connection(settings.db_path) as conn:
        rows = conn.execute("SELECT * FROM films WHERE status IN ('text_gate_passed', 'rights_screened')").fetchall()
        for row in rows:
            result = score_rights(row["license_text"], row["license_url"], row["year"], row["collection"])
            conn.execute(
                """
                UPDATE films
                SET rights_confidence = ?, rights_confidence_score = ?, rights_notes = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    result.category,
                    result.score,
                    json.dumps(result.reasons, sort_keys=True),
                    "rights_screened",
                    now,
                    row["id"],
                ),
            )
            updated += 1
    return updated


def cmd_score_rights(_args: argparse.Namespace) -> int:
    settings = load_settings()
    updated = run_rights_scoring(settings)
    print(f"Scored rights for {updated} films")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    settings = load_settings()
    with get_connection(settings.db_path) as conn:
        film_total = conn.execute("SELECT COUNT(*) AS count FROM films").fetchone()["count"]
        metadata_scored = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM films
            WHERE status IN ('metadata_scored', 'text_gate_passed', 'rights_screened', 'queued_for_video_analysis',
                             'video_prepared', 'shots_extracted', 'visually_scored', 'refined',
                             'ready_for_review', 'announced_to_openclaw', 'reviewed',
                             'approved_for_clip', 'clipped')
            """
        ).fetchone()["count"]
        metadata_excluded = conn.execute(
            "SELECT COUNT(*) AS count FROM films WHERE status = 'excluded_metadata'"
        ).fetchone()["count"]
        text_gate_excluded = conn.execute(
            "SELECT COUNT(*) AS count FROM films WHERE status = 'excluded_text_gate'"
        ).fetchone()["count"]
        rights_counts = {
            row["rights_confidence"] or "unscored": row["count"]
            for row in conn.execute(
                "SELECT rights_confidence, COUNT(*) AS count FROM films GROUP BY rights_confidence"
            ).fetchall()
        }
        file_total = conn.execute("SELECT COUNT(*) AS count FROM film_files").fetchone()["count"]
        checkpoints = [
            dict(row)
            for row in conn.execute(
                """
                SELECT checkpoint_key, query_text, next_page, fetched_count, max_items, status, updated_at
                FROM ingest_checkpoints
                ORDER BY updated_at DESC
                LIMIT 3
                """
            ).fetchall()
        ]

    payload = {
        "db_path": str(settings.db_path),
        "films_total": film_total,
        "films_metadata_scored": metadata_scored,
        "films_metadata_excluded": metadata_excluded,
        "films_text_gate_excluded": text_gate_excluded,
        "film_files_total": file_total,
        "rights_buckets": rights_counts,
        "recent_checkpoints": checkpoints,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_list_films(args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, archive_identifier, title, year, collection, language,
                   runtime_seconds, metadata_score, rights_confidence, status
            FROM films
            ORDER BY id ASC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    payload = [dict(row) for row in rows]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_show_film(args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        if args.film_id is not None:
            film = conn.execute("SELECT * FROM films WHERE id = ?", (args.film_id,)).fetchone()
        else:
            film = conn.execute(
                "SELECT * FROM films WHERE archive_identifier = ?",
                (args.archive_identifier,),
            ).fetchone()
        if not film:
            raise SystemExit("Film not found")
        files = conn.execute(
            """
            SELECT filename, format, size_bytes, download_url, is_video, is_subtitle, is_preferred_source
            FROM film_files
            WHERE film_id = ?
            ORDER BY is_preferred_source DESC, is_video DESC, filename ASC
            LIMIT ?
            """,
            (film["id"], args.file_limit),
        ).fetchall()
        shots = conn.execute(
            """
            SELECT shot_index, start_seconds, end_seconds, duration_seconds, keyframe_path, visual_score, status
            FROM shots
            WHERE film_id = ?
            ORDER BY shot_index ASC
            LIMIT 20
            """,
            (film["id"],),
        ).fetchall()
        moments = conn.execute(
            """
            SELECT id, start_seconds, peak_seconds, end_seconds, confidence, preview_path, approved, rejected
            FROM moments
            WHERE film_id = ?
            ORDER BY confidence DESC, id ASC
            LIMIT 20
            """,
            (film["id"],),
        ).fetchall()

    payload = dict(film)
    payload["subjects"] = json.loads(payload["subjects_json"])
    payload["metadata_reason"] = json.loads(payload["metadata_reason_json"])
    payload["rights_notes_parsed"] = json.loads(payload["rights_notes"]) if payload["rights_notes"] and payload["rights_notes"].startswith("{") else payload["rights_notes"]
    payload["files"] = [dict(row) for row in files]
    payload["shots"] = [dict(row) for row in shots]
    payload["moments"] = [dict(row) for row in moments]
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_show_cached_search(args: argparse.Namespace) -> int:
    settings = load_settings()
    search_dir = settings.cache_dir / "ia" / "search"
    if args.path:
        cache_path = Path(args.path)
    else:
        matches = sorted(search_dir.glob("*.json"))
        if not matches:
            raise SystemExit("No cached search payloads found")
        cache_path = matches[-1]
    print(cache_path.read_text())
    return 0


def cmd_show_cached_metadata(args: argparse.Namespace) -> int:
    settings = load_settings()
    metadata_path = settings.cache_dir / "ia" / "metadata" / f"{args.archive_identifier}.json"
    if not metadata_path.exists():
        raise SystemExit(f"No cached metadata payload found for {args.archive_identifier}")
    print(metadata_path.read_text())
    return 0


def _source_filename(source_url: str, fallback_name: str) -> str:
    parsed = urlparse(source_url)
    if parsed.scheme in ("http", "https"):
        return Path(parsed.path).name or fallback_name
    return Path(source_url).name or fallback_name


def _resolve_source_video(conn, settings, film_id: int, prefer_largest: bool = False) -> tuple[dict, str, Path]:
    film = conn.execute("SELECT * FROM films WHERE id = ?", (film_id,)).fetchone()
    if not film:
        raise SystemExit(f"Film {film_id} not found")
    preferred_file = choose_preferred_file(conn, film_id, prefer_largest=prefer_largest)
    if not preferred_file or not preferred_file["download_url"]:
        raise SystemExit(f"Film {film_id} has no preferred source file")

    archive_identifier = film["archive_identifier"]
    source_url = None
    source_path = None
    last_error = None
    for candidate_file in choose_source_files(conn, film_id, prefer_largest=prefer_largest):
        if not candidate_file["download_url"]:
            continue
        candidate_url = candidate_file["download_url"]
        candidate_path = settings.download_dir / archive_identifier / _source_filename(candidate_url, candidate_file["filename"])
        try:
            ensure_source_video(candidate_url, candidate_path)
            source_url = candidate_url
            source_path = candidate_path
            break
        except Exception as exc:  # pragma: no cover - runtime fallback path
            last_error = exc
    if source_url is None or source_path is None:
        raise SystemExit(f"Film {film_id} has no usable source file: {last_error}")
    return film, source_url, source_path


def cmd_prepare_video(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    with get_connection(settings.db_path) as conn:
        film, source_url, source_path = _resolve_source_video(conn, settings, args.film_id)
        archive_identifier = film["archive_identifier"]
        analysis_path = build_analysis_path(settings.download_dir, archive_identifier)
        create_analysis_video(source_path, analysis_path, max_height=args.max_height)
        probe = probe_media(analysis_path)
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'prepare_video', 'done', ?, ?, ?, ?)
            """,
            (
                args.film_id,
                json.dumps({"source_url": source_url, "filename": source_path.name}, sort_keys=True),
                json.dumps({"analysis_path": str(analysis_path), "probe": probe}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        conn.execute(
            "UPDATE films SET status = 'video_prepared', updated_at = ? WHERE id = ?",
            (utc_now_iso(), args.film_id),
        )
    print(json.dumps({"film_id": args.film_id, "analysis_path": str(analysis_path), "probe": probe}, indent=2, sort_keys=True))
    return 0


def cmd_sample_frames(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    with get_connection(settings.db_path) as conn:
        film, source_url, source_path = _resolve_source_video(conn, settings, args.film_id)
        probe = probe_media(source_path)
        duration = float(probe["duration_seconds"] or 0.0)
        samples = build_sample_windows(
            duration,
            interval_seconds=args.interval_seconds,
            max_frames=args.max_frames,
            window_seconds=args.window_seconds,
        )
        conn.execute("DELETE FROM shots WHERE film_id = ?", (args.film_id,))
        for sample in samples:
            conn.execute(
                """
                INSERT INTO shots (
                    film_id, shot_index, start_seconds, end_seconds, duration_seconds, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'detected', ?)
                """,
                (
                    args.film_id,
                    sample["shot_index"],
                    sample["start_seconds"],
                    sample["end_seconds"],
                    sample["duration_seconds"],
                    utc_now_iso(),
                ),
            )
            shot_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            output_path = settings.frame_dir / film["archive_identifier"] / f"sample_{sample['shot_index']:04d}.jpg"
            extract_keyframe(source_path, sample["midpoint_seconds"], output_path)
            conn.execute(
                "UPDATE shots SET keyframe_path = ?, status = 'keyframed' WHERE id = ?",
                (str(output_path), shot_id),
            )
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'sample_frames', 'done', ?, ?, ?, ?)
            """,
            (
                args.film_id,
                json.dumps(
                    {
                        "source_url": source_url,
                        "source_path": str(source_path),
                        "interval_seconds": args.interval_seconds,
                        "max_frames": args.max_frames,
                        "window_seconds": args.window_seconds,
                    },
                    sort_keys=True,
                ),
                json.dumps({"sample_count": len(samples), "probe": probe}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        conn.execute(
            "UPDATE films SET status = 'shots_extracted', updated_at = ? WHERE id = ?",
            (utc_now_iso(), args.film_id),
        )
    print(json.dumps({"film_id": args.film_id, "sample_count": len(samples), "source_path": str(source_path)}, indent=2, sort_keys=True))
    return 0


def cmd_build_skim_preview(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    with get_connection(settings.db_path) as conn:
        film, _, source_path = _resolve_source_video(conn, settings, args.film_id)
        output_path = settings.preview_dir / film["archive_identifier"] / "skim-preview.mp4"
        build_skim_preview(
            source_path,
            output_path,
            sample_every_seconds=args.sample_every_seconds,
            output_fps=args.output_fps,
            max_height=args.max_height,
        )
    print(json.dumps({"film_id": args.film_id, "preview_path": str(output_path)}, indent=2, sort_keys=True))
    return 0


def _analysis_path_for_film(conn, film_id: int) -> Path:
    row = conn.execute(
        """
        SELECT result_json
        FROM analysis_jobs
        WHERE film_id = ? AND job_type = 'prepare_video' AND status = 'done'
        ORDER BY id DESC
        LIMIT 1
        """,
        (film_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"Film {film_id} has no prepared analysis video")
    payload = json.loads(row["result_json"])
    return Path(payload["analysis_path"])


def cmd_detect_shots(args: argparse.Namespace) -> int:
    settings = load_settings()
    with get_connection(settings.db_path) as conn:
        analysis_path = _analysis_path_for_film(conn, args.film_id)
        shots = build_shots(analysis_path, threshold=args.threshold)
        conn.execute("DELETE FROM shots WHERE film_id = ?", (args.film_id,))
        for shot in shots:
            conn.execute(
                """
                INSERT INTO shots (
                    film_id, shot_index, start_seconds, end_seconds, duration_seconds, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'detected', ?)
                """,
                (
                    args.film_id,
                    shot["shot_index"],
                    shot["start_seconds"],
                    shot["end_seconds"],
                    shot["duration_seconds"],
                    utc_now_iso(),
                ),
            )
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'detect_shots', 'done', ?, ?, ?, ?)
            """,
            (
                args.film_id,
                json.dumps({"threshold": args.threshold}, sort_keys=True),
                json.dumps({"shot_count": len(shots), "analysis_path": str(analysis_path)}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        conn.execute(
            "UPDATE films SET status = 'shots_extracted', updated_at = ? WHERE id = ?",
            (utc_now_iso(), args.film_id),
        )
    print(json.dumps({"film_id": args.film_id, "shot_count": len(shots), "analysis_path": str(analysis_path)}, indent=2, sort_keys=True))
    return 0


def cmd_extract_shot_keyframes(args: argparse.Namespace) -> int:
    settings = load_settings()
    with get_connection(settings.db_path) as conn:
        film = conn.execute("SELECT archive_identifier FROM films WHERE id = ?", (args.film_id,)).fetchone()
        if not film:
            raise SystemExit(f"Film {args.film_id} not found")
        analysis_path = _analysis_path_for_film(conn, args.film_id)
        rows = conn.execute(
            "SELECT id, shot_index, start_seconds, end_seconds FROM shots WHERE film_id = ? ORDER BY shot_index",
            (args.film_id,),
        ).fetchall()
        keyframes = []
        for row in rows:
            midpoint = float(row["start_seconds"]) + ((float(row["end_seconds"]) - float(row["start_seconds"])) / 2)
            output_path = settings.frame_dir / film["archive_identifier"] / f"shot_{row['shot_index']:04d}.jpg"
            extract_keyframe(analysis_path, midpoint, output_path)
            conn.execute(
                "UPDATE shots SET keyframe_path = ?, status = 'keyframed' WHERE id = ?",
                (str(output_path), row["id"]),
            )
            keyframes.append(str(output_path))
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (?, 'extract_shot_keyframes', 'done', '{}', ?, ?, ?)
            """,
            (
                args.film_id,
                json.dumps({"keyframe_count": len(keyframes)}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
    print(json.dumps({"film_id": args.film_id, "keyframe_count": len(keyframes)}, indent=2, sort_keys=True))
    return 0


def cmd_score_shots(args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        film = conn.execute("SELECT metadata_score, rights_confidence_score FROM films WHERE id = ?", (args.film_id,)).fetchone()
        if not film:
            raise SystemExit(f"Film {args.film_id} not found")
        rows = conn.execute(
            "SELECT id, duration_seconds FROM shots WHERE film_id = ? ORDER BY shot_index",
            (args.film_id,),
        ).fetchall()
        scored = 0
        for row in rows:
            duration = float(row["duration_seconds"])
            duration_prior = max(0.0, min(1.0, 1 - abs(duration - 3.0) / 6.0))
            visual_score = round(
                min(1.0, 0.25 + (0.35 * duration_prior) + (0.25 * float(film["metadata_score"])) + (0.15 * float(film["rights_confidence_score"]))),
                3,
            )
            conn.execute(
                "UPDATE shots SET visual_score = ?, status = 'scored' WHERE id = ?",
                (visual_score, row["id"]),
            )
            scored += 1
    print(f"Scored {scored} shots for film {args.film_id}")
    return 0


def cmd_refine_candidates(args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        film = conn.execute(
            "SELECT id, metadata_score, rights_confidence_score FROM films WHERE id = ?",
            (args.film_id,),
        ).fetchone()
        if not film:
            raise SystemExit(f"Film {args.film_id} not found")
        conn.execute("DELETE FROM moments WHERE film_id = ?", (args.film_id,))
        rows = conn.execute(
            """
            SELECT id, start_seconds, end_seconds, duration_seconds, visual_score
            FROM shots
            WHERE film_id = ?
            ORDER BY visual_score DESC, shot_index ASC
            LIMIT ?
            """,
            (args.film_id, args.limit),
        ).fetchall()
        created = 0
        for row in rows:
            peak = float(row["start_seconds"]) + (float(row["duration_seconds"]) / 2)
            refine_score = max(0.0, min(1.0, 1 - abs(float(row["duration_seconds"]) - 4.0) / 8.0))
            confidence = round(
                (0.30 * float(film["metadata_score"]))
                + (0.15 * float(film["rights_confidence_score"]))
                + (0.35 * float(row["visual_score"] or 0.0))
                + (0.20 * refine_score),
                3,
            )
            reason = {
                "metadata_score": float(film["metadata_score"]),
                "rights_confidence_score": float(film["rights_confidence_score"]),
                "shot_visual_score": float(row["visual_score"] or 0.0),
                "dense_refinement_score": refine_score,
                "summary": f"metadata {film['metadata_score']:.2f}, visual {float(row['visual_score'] or 0.0):.2f}, duration {float(row['duration_seconds']):.2f}s",
            }
            conn.execute(
                """
                INSERT INTO moments (
                    film_id, shot_id, start_seconds, peak_seconds, end_seconds, confidence, reason_json, preview_path, approved, rejected, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, 0, ?)
                """,
                (
                    args.film_id,
                    row["id"],
                    max(0.0, float(row["start_seconds"]) - args.pad_before),
                    peak,
                    float(row["end_seconds"]) + args.pad_after,
                    confidence,
                    json.dumps(reason, sort_keys=True),
                    utc_now_iso(),
                ),
            )
            created += 1
        conn.execute(
            "UPDATE films SET status = 'refined', updated_at = ? WHERE id = ?",
            (utc_now_iso(), args.film_id),
        )
    print(f"Created {created} candidate moments for film {args.film_id}")
    return 0


def _load_latest_pending_batch(conn):
    batch = conn.execute(
        "SELECT * FROM review_batches WHERE status = 'pending' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not batch:
        raise SystemExit("No pending review batch found")
    return batch


def _load_batch_rows(conn, batch_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            review_items.display_index,
            films.title,
            films.year,
            films.archive_identifier,
            films.rights_confidence,
            moments.peak_seconds,
            moments.confidence,
            moments.preview_path,
            moments.reason_json
        FROM review_items
        JOIN moments ON moments.id = review_items.moment_id
        JOIN films ON films.id = moments.film_id
        WHERE review_items.review_batch_id = ?
        ORDER BY review_items.display_index ASC
        """,
        (batch_id,),
    ).fetchall()
    formatted = []
    for row in rows:
        reason = json.loads(row["reason_json"])
        formatted.append(
            {
                **dict(row),
                "reason_summary": reason.get("summary", "no summary"),
            }
        )
    return formatted


def cmd_build_review_batch(args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        rows = conn.execute(
            """
            SELECT moments.id AS moment_id
            FROM moments
            LEFT JOIN review_items ON review_items.moment_id = moments.id
            WHERE moments.approved = 0 AND moments.rejected = 0
              AND review_items.id IS NULL
            ORDER BY moments.confidence DESC, moments.id ASC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
        if not rows:
            raise SystemExit("No moments available for review")
        batch_date = utc_now_iso().split("T", 1)[0]
        conn.execute(
            """
            INSERT INTO review_batches (batch_date, channel, account_id, peer_id, status, created_at)
            VALUES (?, 'local', 'local', 'local', 'pending', ?)
            """,
            (batch_date, utc_now_iso()),
        )
        batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        for display_index, row in enumerate(rows, start=1):
            conn.execute(
                """
                INSERT INTO review_items (review_batch_id, moment_id, display_index, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (batch_id, row["moment_id"], display_index, utc_now_iso()),
            )
    print(json.dumps({"batch_id": batch_id, "item_count": len(rows)}, indent=2, sort_keys=True))
    return 0


def cmd_review(_args: argparse.Namespace) -> int:
    with get_connection(load_settings().db_path) as conn:
        batch = _load_latest_pending_batch(conn)
        rows = _load_batch_rows(conn, batch["id"])
    print(format_review_batch(rows, batch["id"]))
    return 0


def _load_review_item(conn, display_index: int):
    batch = _load_latest_pending_batch(conn)
    row = conn.execute(
        """
        SELECT review_items.id AS review_item_id, review_items.review_batch_id, review_items.moment_id,
               moments.film_id, moments.start_seconds, moments.peak_seconds, moments.end_seconds,
               moments.approved, moments.rejected, films.archive_identifier
        FROM review_items
        JOIN moments ON moments.id = review_items.moment_id
        JOIN films ON films.id = moments.film_id
        WHERE review_items.review_batch_id = ? AND review_items.display_index = ?
        """,
        (batch["id"], display_index),
    ).fetchone()
    if not row:
        raise SystemExit(f"Display index {display_index} not found in pending batch {batch['id']}")
    return batch, row


def cmd_approve(args: argparse.Namespace) -> int:
    approved = []
    with get_connection(load_settings().db_path) as conn:
        for display_index in args.indices:
            batch, row = _load_review_item(conn, display_index)
            conn.execute("UPDATE moments SET approved = 1, rejected = 0 WHERE id = ?", (row["moment_id"],))
            conn.execute(
                "UPDATE review_items SET human_decision = 'approved', decision_source = 'local_cli' WHERE id = ?",
                (row["review_item_id"],),
            )
            conn.execute(
                "UPDATE films SET status = 'approved_for_clip', updated_at = ? WHERE id = ?",
                (utc_now_iso(), row["film_id"]),
            )
            approved.append({"batch_id": batch["id"], "display_index": display_index})
    print(json.dumps({"approved": approved}, indent=2, sort_keys=True))
    return 0


def cmd_reject(args: argparse.Namespace) -> int:
    rejected = []
    with get_connection(load_settings().db_path) as conn:
        for display_index in args.indices:
            batch, row = _load_review_item(conn, display_index)
            conn.execute("UPDATE moments SET approved = 0, rejected = 1 WHERE id = ?", (row["moment_id"],))
            conn.execute(
                "UPDATE review_items SET human_decision = 'rejected', decision_source = 'local_cli' WHERE id = ?",
                (row["review_item_id"],),
            )
            rejected.append({"batch_id": batch["id"], "display_index": display_index})
    print(json.dumps({"rejected": rejected}, indent=2, sort_keys=True))
    return 0


def _clip_output_path(settings, archive_identifier: str, display_index: int, suffix: str) -> Path:
    return settings.preview_dir / archive_identifier / f"item_{display_index:02d}_{suffix}.mp4"


def _send_openclaw_message(channel: str, target: str, message: str, media: str | None = None) -> None:
    staged_media = None
    if media:
        source = Path(media)
        if source.exists():
            outbound_root = Path.home() / ".openclaw" / "workspace" / "media" / "outbound"
            outbound_root.mkdir(parents=True, exist_ok=True)
            staged_media = outbound_root / source.name
            shutil.copyfile(source, staged_media)

    command = [
        "openclaw",
        "message",
        "send",
        "--channel",
        channel,
        "--target",
        target,
        "--message",
        message,
    ]
    if staged_media:
        command.extend(["--media", str(staged_media)])
    subprocess.run(command, text=True, capture_output=True, check=True)


def _build_review_item_message(row: dict) -> str:
    return "\n".join(
        [
            f"[{row['display_index']}] {row['title']} ({row['year'] or 'unknown'})",
            f"archive_id: {row['archive_identifier']}",
            f"timestamp: {int(row['peak_seconds'])}s",
            f"confidence: {row['confidence']:.2f}",
            f"rights: {row['rights_confidence'] or 'unscored'}",
            f"reason: {row['reason_summary']}",
        ]
    )


def _ensure_batch_previews(settings, conn, batch_id: int, preview_seconds: float = 4.0) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
            review_items.display_index,
            review_items.moment_id,
            films.archive_identifier,
            films.title,
            films.year,
            films.rights_confidence,
            moments.film_id,
            moments.peak_seconds,
            moments.confidence,
            moments.preview_path,
            moments.reason_json
        FROM review_items
        JOIN moments ON moments.id = review_items.moment_id
        JOIN films ON films.id = moments.film_id
        WHERE review_items.review_batch_id = ?
        ORDER BY review_items.display_index ASC
        """,
        (batch_id,),
    ).fetchall()
    enriched = []
    for row in rows:
        row_dict = dict(row)
        reason = json.loads(row["reason_json"])
        row_dict["reason_summary"] = reason.get("summary", "no summary")
        preview_path = row["preview_path"]
        if not preview_path:
            analysis_path = _analysis_path_for_film(conn, row["film_id"])
            output_path = _clip_output_path(settings, row["archive_identifier"], row["display_index"], "review")
            extract_clip(
                analysis_path,
                output_path,
                max(0.0, float(row["peak_seconds"]) - (preview_seconds / 2)),
                preview_seconds,
            )
            preview_path = str(output_path)
            conn.execute("UPDATE moments SET preview_path = ? WHERE id = ?", (preview_path, row["moment_id"]))
        row_dict["preview_path"] = preview_path
        enriched.append(row_dict)
    return enriched


def cmd_clip(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    generated = []
    with get_connection(settings.db_path) as conn:
        for display_index in args.indices:
            _, row = _load_review_item(conn, display_index)
            analysis_path = _analysis_path_for_film(conn, row["film_id"])
            output_path = _clip_output_path(settings, row["archive_identifier"], display_index, "clip")
            start_seconds = max(0.0, float(row["peak_seconds"]) - args.pre_seconds)
            duration_seconds = args.pre_seconds + args.post_seconds
            extract_clip(analysis_path, output_path, start_seconds, duration_seconds)
            conn.execute(
                "UPDATE moments SET preview_path = ? WHERE id = ?",
                (str(output_path), row["moment_id"]),
            )
            conn.execute(
                "UPDATE films SET status = 'clipped', updated_at = ? WHERE id = ?",
                (utc_now_iso(), row["film_id"]),
            )
            generated.append(str(output_path))
    print(json.dumps({"clips": generated}, indent=2, sort_keys=True))
    return 0


def cmd_more(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    generated = []
    with get_connection(settings.db_path) as conn:
        for display_index in args.indices:
            _, row = _load_review_item(conn, display_index)
            analysis_path = _analysis_path_for_film(conn, row["film_id"])
            output_path = _clip_output_path(settings, row["archive_identifier"], display_index, "more")
            start_seconds = max(0.0, float(row["peak_seconds"]) - args.pre_seconds)
            duration_seconds = args.pre_seconds + args.post_seconds
            extract_clip(analysis_path, output_path, start_seconds, duration_seconds)
            generated.append(str(output_path))
    print(json.dumps({"more_previews": generated}, indent=2, sort_keys=True))
    return 0


def cmd_parse_review_command(args: argparse.Namespace) -> int:
    print(json.dumps(parse_review_command(args.text), indent=2, sort_keys=True))
    return 0


def cmd_send_review_batch(args: argparse.Namespace) -> int:
    settings = load_settings()
    with get_connection(settings.db_path) as conn:
        if args.batch_id is None:
            batch = _load_latest_pending_batch(conn)
        else:
            batch = conn.execute("SELECT * FROM review_batches WHERE id = ?", (args.batch_id,)).fetchone()
            if not batch:
                raise SystemExit(f"Batch {args.batch_id} not found")
        rows = _ensure_batch_previews(settings, conn, batch["id"], preview_seconds=args.preview_seconds)

    for row in rows:
        _send_openclaw_message(
            args.channel,
            args.target,
            _build_review_item_message(row),
            media=row["preview_path"],
        )
    _send_openclaw_message(
        args.channel,
        args.target,
        "\n".join(
            [
                f"Review batch {batch['id']} sent.",
                "Reply with:",
                "1 2",
                "reject 3",
                "more 4",
                "clip 1",
            ]
        ),
    )
    print(json.dumps({"batch_id": batch["id"], "channel": args.channel, "target": args.target}, indent=2, sort_keys=True))
    return 0


def cmd_handle_review_command(args: argparse.Namespace) -> int:
    parsed = parse_review_command(args.text)
    action = parsed["action"]
    if action == "approve":
        result = cmd_approve(argparse.Namespace(indices=parsed["indices"]))
        if args.channel and args.target:
            _send_openclaw_message(args.channel, args.target, f"Approved items: {' '.join(map(str, parsed['indices']))}")
        return result
    if action == "reject":
        result = cmd_reject(argparse.Namespace(indices=parsed["indices"]))
        if args.channel and args.target:
            _send_openclaw_message(args.channel, args.target, f"Rejected items: {' '.join(map(str, parsed['indices']))}")
        return result
    if action == "clip":
        result = cmd_clip(argparse.Namespace(indices=parsed["indices"], pre_seconds=5.0, post_seconds=5.0))
        if args.channel and args.target:
            settings = load_settings()
            with get_connection(settings.db_path) as conn:
                _, row = _load_review_item(conn, parsed["indices"][0])
                preview_path = conn.execute(
                    "SELECT preview_path FROM moments WHERE id = ?",
                    (row["moment_id"],),
                ).fetchone()["preview_path"]
            _send_openclaw_message(args.channel, args.target, f"Clip ready for item {parsed['indices'][0]}", media=preview_path)
        return result
    if action == "more":
        result = cmd_more(argparse.Namespace(indices=parsed["indices"], pre_seconds=8.0, post_seconds=8.0))
        if args.channel and args.target:
            settings = load_settings()
            archive_identifier = None
            with get_connection(settings.db_path) as conn:
                _, row = _load_review_item(conn, parsed["indices"][0])
                archive_identifier = row["archive_identifier"]
            media = str(_clip_output_path(settings, archive_identifier, parsed["indices"][0], "more"))
            _send_openclaw_message(args.channel, args.target, f"Extended preview for item {parsed['indices'][0]}", media=media)
        return result
    if action == "review":
        if args.channel and args.target:
            return cmd_send_review_batch(argparse.Namespace(batch_id=None, channel=args.channel, target=args.target, preview_seconds=4.0))
        return cmd_review(argparse.Namespace())
    if action == "status":
        result = cmd_status(argparse.Namespace())
        if args.channel and args.target:
            with get_connection(load_settings().db_path) as conn:
                film_total = conn.execute("SELECT COUNT(*) AS count FROM films").fetchone()["count"]
                moment_total = conn.execute("SELECT COUNT(*) AS count FROM moments").fetchone()["count"]
            _send_openclaw_message(args.channel, args.target, f"Status: films={film_total}, moments={moment_total}")
        return result
    if action == "resend":
        if not args.channel or not args.target:
            raise SystemExit("resend requires --channel and --target")
        return cmd_send_review_batch(argparse.Namespace(batch_id=None, channel=args.channel, target=args.target, preview_seconds=4.0))
    raise SystemExit(f"Unhandled action: {action}")


def cmd_review_dispatch(args: argparse.Namespace) -> int:
    text = args.text.strip()
    normalized = " ".join(text.split())
    lowered = normalized.lower()
    if lowered.startswith("ia-kissing-scraper "):
        lowered = lowered[len("ia-kissing-scraper ") :].strip()
    if lowered == "scan":
        return cmd_run_batch(
            argparse.Namespace(
                query=args.query,
                ingest_limit=args.ingest_limit,
                rows=args.rows,
                analyze_limit=args.analyze_limit,
                review_limit=args.review_limit,
                top_shots_per_film=args.top_shots_per_film,
                refine_pad_before=args.refine_pad_before,
                refine_pad_after=args.refine_pad_after,
                max_height=args.max_height,
                shot_threshold=args.shot_threshold,
                analysis_mode="sample",
                sample_interval_seconds=45.0,
                sample_max_frames=40,
                sample_window_seconds=4.0,
                throttle_seconds=args.throttle_seconds,
                checkpoint_key=args.checkpoint_key,
                channel=args.channel,
                target=args.target,
                account_id=args.account_id,
            )
        )
    return cmd_handle_review_command(
        argparse.Namespace(
            text=text,
            channel=args.channel,
            target=args.target,
        )
    )


def cmd_run_batch(args: argparse.Namespace) -> int:
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)

    client = IAClient(settings.cache_dir, settings.user_agent, throttle_seconds=args.throttle_seconds)
    with get_connection(settings.db_path) as conn:
        ingest_result = ingest_from_ia(
            conn,
            client,
            query=args.query,
            limit=args.ingest_limit,
            rows=args.rows,
            checkpoint_key=args.checkpoint_key,
        )

    metadata_updated = run_metadata_scoring(settings)
    text_gate_updated = run_text_gate_scoring(settings)
    rights_updated = run_rights_scoring(settings)

    processed_films = []
    skipped_films = []
    with get_connection(settings.db_path) as conn:
        candidate_rows = conn.execute(
            """
            SELECT id
            FROM films
            WHERE status IN ('rights_screened', 'text_gate_passed')
            ORDER BY (metadata_score + rights_confidence_score) DESC, id ASC
            """,
        ).fetchall()

    for row in candidate_rows:
        if len(processed_films) >= args.analyze_limit:
            break
        film_id = row["id"]
        try:
            if args.analysis_mode == "sample":
                cmd_sample_frames(
                    argparse.Namespace(
                        film_id=film_id,
                        interval_seconds=args.sample_interval_seconds,
                        max_frames=args.sample_max_frames,
                        window_seconds=args.sample_window_seconds,
                    )
                )
            else:
                cmd_prepare_video(argparse.Namespace(film_id=film_id, max_height=args.max_height))
                cmd_detect_shots(argparse.Namespace(film_id=film_id, threshold=args.shot_threshold))
                cmd_extract_shot_keyframes(argparse.Namespace(film_id=film_id))
            cmd_score_shots(argparse.Namespace(film_id=film_id))
            cmd_refine_candidates(
                argparse.Namespace(
                    film_id=film_id,
                    limit=args.top_shots_per_film,
                    pad_before=args.refine_pad_before,
                    pad_after=args.refine_pad_after,
                )
            )
            processed_films.append(film_id)
        except (Exception, SystemExit) as exc:  # pragma: no cover - defensive runtime path
            skipped_films.append({"film_id": film_id, "error": str(exc)})

    batch_summary: dict | None = None
    try:
        with get_connection(settings.db_path) as conn:
            rows = conn.execute(
                """
                SELECT moments.id AS moment_id
                FROM moments
                LEFT JOIN review_items ON review_items.moment_id = moments.id
                WHERE moments.approved = 0 AND moments.rejected = 0
                  AND review_items.id IS NULL
                ORDER BY moments.confidence DESC, moments.id ASC
                LIMIT ?
                """,
                (args.review_limit,),
            ).fetchall()
            if rows:
                batch_date = utc_now_iso().split("T", 1)[0]
                conn.execute(
                    """
                    INSERT INTO review_batches (batch_date, channel, account_id, peer_id, status, created_at)
                    VALUES (?, ?, ?, ?, 'pending', ?)
                    """,
                    (batch_date, args.channel or "local", args.account_id or "local", args.target or "local", utc_now_iso()),
                )
                batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
                for display_index, moment_row in enumerate(rows, start=1):
                    conn.execute(
                        """
                        INSERT INTO review_items (review_batch_id, moment_id, display_index, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (batch_id, moment_row["moment_id"], display_index, utc_now_iso()),
                    )
                batch_summary = {"batch_id": batch_id, "item_count": len(rows)}
    except Exception as exc:  # pragma: no cover - defensive runtime path
        batch_summary = {"error": str(exc)}

    if batch_summary and batch_summary.get("batch_id") and args.channel and args.target:
        cmd_send_review_batch(
            argparse.Namespace(batch_id=batch_summary["batch_id"], channel=args.channel, target=args.target)
        )

    summary = {
        "ingest": ingest_result.__dict__,
        "metadata_updated": metadata_updated,
        "text_gate_updated": text_gate_updated,
        "rights_updated": rights_updated,
        "processed_films": processed_films,
        "skipped_films": skipped_films,
        "review_batch": batch_summary,
    }
    if args.channel and args.target:
        ingest = ingest_result.__dict__
        message = (
            f"Scan finished: fetched {ingest['pages_fetched']} page(s), "
            f"upserted {ingest['films_upserted']} films / {ingest['files_upserted']} files, "
            f"updated {metadata_updated} metadata + {rights_updated} rights, "
            f"processed {len(processed_films)} film(s)"
        )
        if skipped_films:
            message += f", skipped {len(skipped_films)}."
        else:
            message += "."
        if batch_summary and batch_summary.get("batch_id"):
            message += f" Review batch #{batch_summary['batch_id']} has {batch_summary['item_count']} item(s)."
        else:
            message += " No review batch generated."
        _send_openclaw_message(args.channel, args.target, message)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ia-kissing-pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db")
    init_db_parser.set_defaults(func=cmd_init_db)

    ingest_parser = subparsers.add_parser("ingest-fixture")
    ingest_parser.add_argument("fixture_path")
    ingest_parser.set_defaults(func=cmd_ingest_fixture)

    ia_ingest_parser = subparsers.add_parser("ingest-ia")
    ia_ingest_parser.add_argument("--query", required=True)
    ia_ingest_parser.add_argument("--limit", type=int, default=10)
    ia_ingest_parser.add_argument("--rows", type=int, default=5)
    ia_ingest_parser.add_argument("--checkpoint-key")
    ia_ingest_parser.add_argument("--throttle-seconds", type=float, default=0.5)
    ia_ingest_parser.set_defaults(func=cmd_ingest_ia)

    metadata_parser = subparsers.add_parser("score-metadata")
    metadata_parser.set_defaults(func=cmd_score_metadata)

    text_gate_parser = subparsers.add_parser("score-text-gate")
    text_gate_parser.set_defaults(func=cmd_score_text_gate)

    rights_parser = subparsers.add_parser("score-rights")
    rights_parser.set_defaults(func=cmd_score_rights)

    status_parser = subparsers.add_parser("status")
    status_parser.set_defaults(func=cmd_status)

    list_films_parser = subparsers.add_parser("list-films")
    list_films_parser.add_argument("--limit", type=int, default=20)
    list_films_parser.set_defaults(func=cmd_list_films)

    show_film_parser = subparsers.add_parser("show-film")
    show_film_group = show_film_parser.add_mutually_exclusive_group(required=True)
    show_film_group.add_argument("--film-id", type=int)
    show_film_group.add_argument("--archive-identifier")
    show_film_parser.add_argument("--file-limit", type=int, default=15)
    show_film_parser.set_defaults(func=cmd_show_film)

    show_search_parser = subparsers.add_parser("show-cached-search")
    show_search_parser.add_argument("--path")
    show_search_parser.set_defaults(func=cmd_show_cached_search)

    show_metadata_parser = subparsers.add_parser("show-cached-metadata")
    show_metadata_parser.add_argument("archive_identifier")
    show_metadata_parser.set_defaults(func=cmd_show_cached_metadata)

    prepare_parser = subparsers.add_parser("prepare-video")
    prepare_parser.add_argument("--film-id", type=int, required=True)
    prepare_parser.add_argument("--max-height", type=int, default=360)
    prepare_parser.set_defaults(func=cmd_prepare_video)

    sample_parser = subparsers.add_parser("sample-frames")
    sample_parser.add_argument("--film-id", type=int, required=True)
    sample_parser.add_argument("--interval-seconds", type=float, default=45.0)
    sample_parser.add_argument("--max-frames", type=int, default=40)
    sample_parser.add_argument("--window-seconds", type=float, default=4.0)
    sample_parser.set_defaults(func=cmd_sample_frames)

    skim_parser = subparsers.add_parser("build-skim-preview")
    skim_parser.add_argument("--film-id", type=int, required=True)
    skim_parser.add_argument("--sample-every-seconds", type=float, default=3.0)
    skim_parser.add_argument("--output-fps", type=int, default=24)
    skim_parser.add_argument("--max-height", type=int, default=360)
    skim_parser.set_defaults(func=cmd_build_skim_preview)

    detect_parser = subparsers.add_parser("detect-shots")
    detect_parser.add_argument("--film-id", type=int, required=True)
    detect_parser.add_argument("--threshold", type=float, default=0.30)
    detect_parser.set_defaults(func=cmd_detect_shots)

    keyframe_parser = subparsers.add_parser("extract-shot-keyframes")
    keyframe_parser.add_argument("--film-id", type=int, required=True)
    keyframe_parser.set_defaults(func=cmd_extract_shot_keyframes)

    score_shots_parser = subparsers.add_parser("score-shots")
    score_shots_parser.add_argument("--film-id", type=int, required=True)
    score_shots_parser.set_defaults(func=cmd_score_shots)

    refine_parser = subparsers.add_parser("refine-candidates")
    refine_parser.add_argument("--film-id", type=int, required=True)
    refine_parser.add_argument("--limit", type=int, default=3)
    refine_parser.add_argument("--pad-before", type=float, default=2.0)
    refine_parser.add_argument("--pad-after", type=float, default=2.0)
    refine_parser.set_defaults(func=cmd_refine_candidates)

    batch_parser = subparsers.add_parser("build-review-batch")
    batch_parser.add_argument("--limit", type=int, default=10)
    batch_parser.set_defaults(func=cmd_build_review_batch)

    review_parser = subparsers.add_parser("review")
    review_parser.set_defaults(func=cmd_review)

    approve_parser = subparsers.add_parser("approve")
    approve_parser.add_argument("indices", nargs="+", type=int)
    approve_parser.set_defaults(func=cmd_approve)

    reject_parser = subparsers.add_parser("reject")
    reject_parser.add_argument("indices", nargs="+", type=int)
    reject_parser.set_defaults(func=cmd_reject)

    clip_parser = subparsers.add_parser("clip")
    clip_parser.add_argument("indices", nargs="+", type=int)
    clip_parser.add_argument("--pre-seconds", type=float, default=5.0)
    clip_parser.add_argument("--post-seconds", type=float, default=5.0)
    clip_parser.set_defaults(func=cmd_clip)

    more_parser = subparsers.add_parser("more")
    more_parser.add_argument("indices", nargs="+", type=int)
    more_parser.add_argument("--pre-seconds", type=float, default=8.0)
    more_parser.add_argument("--post-seconds", type=float, default=8.0)
    more_parser.set_defaults(func=cmd_more)

    parse_review_parser = subparsers.add_parser("parse-review-command")
    parse_review_parser.add_argument("text")
    parse_review_parser.set_defaults(func=cmd_parse_review_command)

    send_batch_parser = subparsers.add_parser("send-review-batch")
    send_batch_parser.add_argument("--batch-id", type=int)
    send_batch_parser.add_argument("--channel", required=True)
    send_batch_parser.add_argument("--target", required=True)
    send_batch_parser.add_argument("--preview-seconds", type=float, default=4.0)
    send_batch_parser.set_defaults(func=cmd_send_review_batch)

    handle_review_parser = subparsers.add_parser("handle-review-command")
    handle_review_parser.add_argument("text")
    handle_review_parser.add_argument("--channel")
    handle_review_parser.add_argument("--target")
    handle_review_parser.set_defaults(func=cmd_handle_review_command)

    run_batch_parser = subparsers.add_parser("run-batch")
    run_batch_parser.add_argument("--query", default="collection:feature_films")
    run_batch_parser.add_argument("--ingest-limit", type=int, default=10)
    run_batch_parser.add_argument("--rows", type=int, default=5)
    run_batch_parser.add_argument("--analyze-limit", type=int, default=3)
    run_batch_parser.add_argument("--review-limit", type=int, default=10)
    run_batch_parser.add_argument("--top-shots-per-film", type=int, default=3)
    run_batch_parser.add_argument("--refine-pad-before", type=float, default=2.0)
    run_batch_parser.add_argument("--refine-pad-after", type=float, default=2.0)
    run_batch_parser.add_argument("--max-height", type=int, default=360)
    run_batch_parser.add_argument("--shot-threshold", type=float, default=0.30)
    run_batch_parser.add_argument("--analysis-mode", choices=("sample", "full"), default="sample")
    run_batch_parser.add_argument("--sample-interval-seconds", type=float, default=45.0)
    run_batch_parser.add_argument("--sample-max-frames", type=int, default=40)
    run_batch_parser.add_argument("--sample-window-seconds", type=float, default=4.0)
    run_batch_parser.add_argument("--throttle-seconds", type=float, default=0.5)
    run_batch_parser.add_argument("--checkpoint-key")
    run_batch_parser.add_argument("--channel")
    run_batch_parser.add_argument("--target")
    run_batch_parser.add_argument("--account-id")
    run_batch_parser.set_defaults(func=cmd_run_batch)

    dispatch_parser = subparsers.add_parser("review-dispatch")
    dispatch_parser.add_argument("text")
    dispatch_parser.add_argument("--channel", required=True)
    dispatch_parser.add_argument("--target", required=True)
    dispatch_parser.add_argument("--query", default="collection:feature_films")
    dispatch_parser.add_argument("--ingest-limit", type=int, default=10)
    dispatch_parser.add_argument("--rows", type=int, default=5)
    dispatch_parser.add_argument("--analyze-limit", type=int, default=3)
    dispatch_parser.add_argument("--review-limit", type=int, default=10)
    dispatch_parser.add_argument("--top-shots-per-film", type=int, default=3)
    dispatch_parser.add_argument("--refine-pad-before", type=float, default=2.0)
    dispatch_parser.add_argument("--refine-pad-after", type=float, default=2.0)
    dispatch_parser.add_argument("--max-height", type=int, default=360)
    dispatch_parser.add_argument("--shot-threshold", type=float, default=0.30)
    dispatch_parser.add_argument("--throttle-seconds", type=float, default=0.5)
    dispatch_parser.add_argument("--checkpoint-key")
    dispatch_parser.add_argument("--account-id")
    dispatch_parser.set_defaults(func=cmd_review_dispatch)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
