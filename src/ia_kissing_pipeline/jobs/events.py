from __future__ import annotations

import json

from ia_kissing_pipeline.utils.time import utc_now_iso


def append_job_event(conn, job_id: int, event_type: str, payload: dict) -> int:
    conn.execute(
        """
        INSERT INTO job_events (job_id, event_type, payload_json, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (job_id, event_type, json.dumps(payload, sort_keys=True), utc_now_iso()),
    )
    return int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])


def list_job_events(conn, job_id: int, *, after_id: int = 0, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, job_id, event_type, payload_json, created_at
        FROM job_events
        WHERE job_id = ? AND id > ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (job_id, after_id, limit),
    ).fetchall()
    return [_hydrate_job_event(row) for row in rows]


def list_recent_job_events(conn, job_id: int, *, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, job_id, event_type, payload_json, created_at
        FROM job_events
        WHERE job_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()
    return [_hydrate_job_event(row) for row in reversed(rows)]


def _hydrate_job_event(row) -> dict:
    return {
        "id": int(row["id"]),
        "job_id": int(row["job_id"]),
        "event_type": row["event_type"],
        "payload": json.loads(row["payload_json"] or "{}"),
        "created_at": row["created_at"],
    }
