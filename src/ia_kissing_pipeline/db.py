from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS films (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_identifier TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    year INTEGER,
    description TEXT,
    subjects_json TEXT NOT NULL DEFAULT '[]',
    creator TEXT,
    collection TEXT,
    language TEXT,
    runtime_seconds INTEGER,
    item_url TEXT NOT NULL,
    license_text TEXT,
    license_url TEXT,
    rights_notes TEXT,
    rights_confidence TEXT,
    rights_confidence_score REAL NOT NULL DEFAULT 0,
    metadata_score REAL NOT NULL DEFAULT 0,
    metadata_reason_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'ingested',
    ingested_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS film_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    format TEXT,
    size_bytes INTEGER,
    download_url TEXT,
    is_video INTEGER NOT NULL DEFAULT 0,
    is_subtitle INTEGER NOT NULL DEFAULT 0,
    is_preferred_source INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE (film_id, filename)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id INTEGER REFERENCES films(id) ON DELETE CASCADE,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    attempts INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    error_text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_id_id ON job_events(job_id, id);

CREATE TABLE IF NOT EXISTS shots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    shot_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    duration_seconds REAL NOT NULL,
    keyframe_path TEXT,
    visual_score REAL,
    person_count INTEGER,
    face_count INTEGER,
    face_proximity_score REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS moments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    shot_id INTEGER REFERENCES shots(id) ON DELETE SET NULL,
    start_seconds REAL NOT NULL,
    peak_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    confidence REAL NOT NULL DEFAULT 0,
    reason_json TEXT NOT NULL DEFAULT '{}',
    preview_path TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_date TEXT NOT NULL,
    channel TEXT,
    account_id TEXT,
    peer_id TEXT,
    openclaw_agent_id TEXT,
    openclaw_session_key TEXT,
    delivery_message_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_batch_id INTEGER NOT NULL REFERENCES review_batches(id) ON DELETE CASCADE,
    moment_id INTEGER NOT NULL REFERENCES moments(id) ON DELETE CASCADE,
    display_index INTEGER NOT NULL,
    delivery_group_id TEXT,
    human_decision TEXT,
    decision_source TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_checkpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    checkpoint_key TEXT NOT NULL UNIQUE,
    query_text TEXT NOT NULL,
    next_page INTEGER NOT NULL DEFAULT 1,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    max_items INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    skim_path TEXT,
    skim_sample_every_seconds REAL NOT NULL DEFAULT 4.0,
    skim_output_fps INTEGER NOT NULL DEFAULT 12,
    preview_seconds REAL NOT NULL DEFAULT 0,
    sample_index INTEGER NOT NULL DEFAULT 1,
    source_seconds REAL NOT NULL DEFAULT 0,
    selected_tag TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS manual_clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    manual_mark_id INTEGER NOT NULL REFERENCES manual_marks(id) ON DELETE CASCADE,
    film_id INTEGER NOT NULL REFERENCES films(id) ON DELETE CASCADE,
    clip_path TEXT NOT NULL,
    clip_tag TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS film_reviews (
    film_id INTEGER PRIMARY KEY REFERENCES films(id) ON DELETE CASCADE,
    review_status TEXT NOT NULL DEFAULT 'pending',
    review_notes TEXT,
    reviewed_at TEXT,
    cleanup_completed INTEGER NOT NULL DEFAULT 0,
    cleanup_at TEXT
);

CREATE TABLE IF NOT EXISTS queue_runtime (
    queue_name TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'idle',
    owner_job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE SET NULL,
    owner_pid INTEGER,
    heartbeat_at TEXT,
    target_ready INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(db_path: Path) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_column(conn, "manual_clips", "cropped_clip_path", "TEXT")
        _ensure_column(conn, "manual_clips", "crop_x", "REAL")
        _ensure_column(conn, "manual_clips", "crop_y", "REAL")
        _ensure_column(conn, "manual_clips", "crop_width", "REAL")
        _ensure_column(conn, "manual_clips", "crop_height", "REAL")
        _ensure_column(conn, "manual_marks", "selected_tag", "TEXT")
        _ensure_column(conn, "manual_clips", "clip_tag", "TEXT")
        _ensure_column(conn, "manual_clips", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "manual_clips", "ignored", "INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            INSERT INTO queue_runtime (queue_name, state, updated_at)
            VALUES ('review_queue', 'idle', CURRENT_TIMESTAMP)
            ON CONFLICT(queue_name) DO NOTHING
            """
        )
        conn.execute(
            """
            INSERT INTO queue_runtime (queue_name, state, updated_at)
            VALUES ('download_batch', 'idle', CURRENT_TIMESTAMP)
            ON CONFLICT(queue_name) DO NOTHING
            """
        )
        conn.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES ('clip_order_mode', 'random')
            ON CONFLICT(key) DO NOTHING
            """
        )


@contextmanager
def get_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    if column_name in existing:
        return
    conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
