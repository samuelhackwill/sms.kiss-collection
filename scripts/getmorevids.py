#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"

os.chdir(PROJECT_ROOT)
os.environ.setdefault("DB_PATH", str(PROJECT_ROOT / "data" / "pipeline.db"))
os.environ.setdefault("CACHE_DIR", str(PROJECT_ROOT / "data" / "cache"))
os.environ.setdefault("DOWNLOAD_DIR", str(PROJECT_ROOT / "data" / "downloads"))
os.environ.setdefault("FRAME_DIR", str(PROJECT_ROOT / "data" / "frames"))
os.environ.setdefault("PREVIEW_DIR", str(PROJECT_ROOT / "data" / "previews"))
os.environ.setdefault("CLIPS_DIR", str(PROJECT_ROOT / "data" / "clips"))
os.environ.setdefault("LOG_DIR", str(PROJECT_ROOT / "data" / "logs"))

if os.environ.get("IA_KISSING_IN_VENV") != "1":
    if not VENV_PYTHON.exists():
        raise SystemExit(f"Missing virtualenv interpreter: {VENV_PYTHON}")
    env = os.environ.copy()
    env["IA_KISSING_IN_VENV"] = "1"
    os.execve(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], env)

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ia_kissing_pipeline.config import load_settings
from ia_kissing_pipeline.db import get_connection, init_db
from ia_kissing_pipeline.utils.time import utc_now_iso
from ia_kissing_pipeline.webapp import (
    _cleanup_nonpending_local_artifacts,
    _count_active_pool_films,
    _count_download_candidates,
    _find_next_download_candidate,
    _finish_queue_runtime,
    _heartbeat_queue_runtime,
    _ingest_and_score_more,
    _load_queue_runtime,
    _reconcile_stale_skim_job,
    _start_queue_runtime,
    _transition_queue_runtime,
    _update_job,
)


SKIM_TIMEOUT_SECONDS = float(os.environ.get("IA_KISSING_SKIM_TIMEOUT_SECONDS", "180"))


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[{timestamp} UTC] {message}", flush=True)


def progress_bar(done: int, total: int, width: int = 24) -> str:
    total = max(1, total)
    done = max(0, min(done, total))
    filled = int(round(width * done / total))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {done}/{total}"


def run_skim_job_with_timeout(settings, batch_job_id: int, target_ready: int, skim_job_id: int, film_id: int, sample_every_seconds: float, output_fps: int, max_height: int, timeout_seconds: float) -> int:
    command = [
        sys.executable,
        "-m",
        "ia_kissing_pipeline.webapp",
        "build-skim-job",
        "--job-id",
        str(skim_job_id),
        "--film-id",
        str(film_id),
        "--sample-every-seconds",
        str(sample_every_seconds),
        "--output-fps",
        str(output_fps),
        "--max-height",
        str(max_height),
    ]
    process = subprocess.Popen(
        command,
        cwd=str(PROJECT_ROOT),
        start_new_session=True,
    )
    started_at = time.monotonic()
    last_log_at = 0.0
    while True:
        rc = process.poll()
        if rc is not None:
            return int(rc)
        elapsed = time.monotonic() - started_at
        _heartbeat_queue_runtime(settings, batch_job_id, target_ready)
        if elapsed - last_log_at >= 30:
            log(f"skim build still running for film_id={film_id} elapsed={int(elapsed)}s")
            last_log_at = elapsed
        if elapsed > timeout_seconds:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            with get_connection(settings.db_path) as conn:
                _update_job(
                    conn,
                    skim_job_id,
                    "error",
                    "error",
                    1.0,
                    f"skim timeout after {int(timeout_seconds)}s",
                )
            return 124
        time.sleep(2)


def create_download_batch_job(settings, count: int) -> tuple[bool, int | None]:
    with get_connection(settings.db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        runtime = _load_queue_runtime(conn)
        if runtime and runtime["state"] in ("queued", "running"):
            return False, int(runtime["owner_job_id"] or 0) if runtime else None
        conn.execute(
            """
            INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
            VALUES (NULL, 'download_batch', 'queued', ?, ?, ?, ?)
            """,
            (
                json.dumps({"count": count}, sort_keys=True),
                json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        _transition_queue_runtime(conn, "queued", job_id, None, count, None)
    return True, job_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Add more films to the active pool, then download/build every ready skim sequentially.")
    parser.add_argument("count", type=int, nargs="?", default=1, help="Number of additional films to add to the pool before draining it")
    args = parser.parse_args()

    count = max(1, min(100, args.count))
    settings = load_settings()
    settings.ensure_directories()
    init_db(settings.db_path)

    started, job_id = create_download_batch_job(settings, count)
    if not started:
        print(json.dumps({"count": count, "job_id": job_id, "started": False, "status": "already_running"}, indent=2, sort_keys=True))
        return 0

    print(json.dumps({"count": count, "job_id": job_id, "started": True, "status": "queued"}, indent=2, sort_keys=True))
    log(f"download_batch {job_id} starting with requested add count={count}")

    completed = 0
    ingest_attempts = 0
    try:
        if not _start_queue_runtime(settings, job_id, count):
            log("runtime ownership was not granted; exiting")
            return 0

        with get_connection(settings.db_path) as conn:
            start_pool_count = _count_active_pool_films(conn)
            _update_job(conn, job_id, "running", "expanding_pool", 0.05)
        target_pool_count = start_pool_count + count
        log(f"initial active pool={start_pool_count}, target pool={target_pool_count} {progress_bar(0, count)}")

        while True:
            _heartbeat_queue_runtime(settings, job_id, count)
            _cleanup_nonpending_local_artifacts(settings)
            with get_connection(settings.db_path) as conn:
                active_pool_count = _count_active_pool_films(conn)
            if active_pool_count >= target_pool_count:
                added = max(0, active_pool_count - start_pool_count)
                log(f"pool target reached: active_pool={active_pool_count} {progress_bar(min(added, count), count)}")
                break
            if ingest_attempts >= 8:
                log("stopping pool expansion: ingest attempt limit reached")
                break
            added = max(0, active_pool_count - start_pool_count)
            log(f"expanding pool: active_pool={active_pool_count}, ingest_attempt={ingest_attempts + 1} {progress_bar(min(added, count), count)}")
            if not _ingest_and_score_more(settings):
                log("pool expansion stopped: ingest_and_score_more returned no new films")
                break
            ingest_attempts += 1
            with get_connection(settings.db_path) as conn:
                active_pool_count = _count_active_pool_films(conn)
                progress = 0.05 if target_pool_count <= start_pool_count else min(
                    0.35,
                    0.05 + 0.30 * max(0, active_pool_count - start_pool_count) / max(1, target_pool_count - start_pool_count),
                )
                _update_job(conn, job_id, "running", "expanding_pool", progress)
            added = max(0, active_pool_count - start_pool_count)
            log(f"pool expanded: active_pool={active_pool_count} {progress_bar(min(added, count), count)}")

        with get_connection(settings.db_path) as conn:
            total_candidates = _count_download_candidates(conn)
            _update_job(conn, job_id, "running", "downloading_ready", 0.35)
        log(f"starting drain: candidates_without_skim={total_candidates} {progress_bar(0, total_candidates)}")

        while True:
            _heartbeat_queue_runtime(settings, job_id, count)
            _cleanup_nonpending_local_artifacts(settings)
            with get_connection(settings.db_path) as conn:
                candidate = _find_next_download_candidate(conn)
            if not candidate:
                log("drain complete: no more download candidates")
                break
            film_id = int(candidate["id"])
            with get_connection(settings.db_path) as conn:
                film = conn.execute("SELECT title, archive_identifier FROM films WHERE id = ?", (film_id,)).fetchone()
                film_title = film["title"] if film else f"film {film_id}"
                conn.execute(
                    """
                    INSERT INTO analysis_jobs (film_id, job_type, status, payload_json, result_json, created_at, updated_at)
                    VALUES (?, 'build_skim_preview', 'queued', ?, ?, ?, ?)
                    """,
                    (
                        film_id,
                        json.dumps({"sample_every_seconds": 4, "output_fps": 12}, sort_keys=True),
                        json.dumps({"phase": "queued", "progress": 0.05}, sort_keys=True),
                        utc_now_iso(),
                        utc_now_iso(),
                    ),
                )
                skim_job_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            log(
                f"starting skim build for film_id={film_id} title={film_title!r} "
                f"timeout={int(SKIM_TIMEOUT_SECONDS)}s {progress_bar(completed, total_candidates)}"
            )
            rc = run_skim_job_with_timeout(
                settings,
                batch_job_id=job_id,
                target_ready=count,
                skim_job_id=skim_job_id,
                film_id=film_id,
                sample_every_seconds=4,
                output_fps=12,
                max_height=360,
                timeout_seconds=SKIM_TIMEOUT_SECONDS,
            )
            with get_connection(settings.db_path) as conn:
                _reconcile_stale_skim_job(conn, film_id)
                skim_job = conn.execute(
                    "SELECT status, error_text, result_json FROM analysis_jobs WHERE id = ?",
                    (skim_job_id,),
                ).fetchone()
            if rc == 0 and skim_job and skim_job["status"] == "done":
                completed += 1
                log(f"completed skim build for film_id={film_id} title={film_title!r} {progress_bar(completed, total_candidates)}")
            else:
                error_text = skim_job["error_text"] if skim_job else "unknown skim failure"
                log(f"skim build failed for film_id={film_id} title={film_title!r}: {error_text} {progress_bar(completed, total_candidates)}")
            with get_connection(settings.db_path) as conn:
                progress = min(0.95, 0.35 + 0.60 * completed / max(1, total_candidates))
                _update_job(conn, job_id, "running", "downloading_ready", progress)

        with get_connection(settings.db_path) as conn:
            conn.execute(
                """
                UPDATE analysis_jobs
                SET status = 'done', result_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(
                        {
                            "phase": "done",
                            "progress": 1.0,
                            "requested_add_count": count,
                            "start_pool_count": start_pool_count,
                            "target_pool_count": target_pool_count,
                            "completed_count": completed,
                            "ingest_attempts": ingest_attempts,
                        },
                        sort_keys=True,
                    ),
                    utc_now_iso(),
                    job_id,
                ),
            )
        _finish_queue_runtime(settings, job_id, count, "idle", None)
        log(f"download_batch {job_id} finished: completed={completed}, ingest_attempts={ingest_attempts}")
        return 0
    except BaseException as exc:
        with get_connection(settings.db_path) as conn:
            _update_job(conn, job_id, "error", "error", 1.0, str(exc))
        _finish_queue_runtime(settings, job_id, count, "error", str(exc))
        log(f"download_batch {job_id} failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
