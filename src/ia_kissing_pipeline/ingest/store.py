from __future__ import annotations

import json

from ia_kissing_pipeline.utils.time import utc_now_iso


def upsert_film_item(conn, item: dict) -> int:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO films (
            archive_identifier, title, year, description, subjects_json, creator,
            collection, language, runtime_seconds, item_url, license_text, license_url,
            rights_notes, rights_confidence, rights_confidence_score, metadata_score,
            metadata_reason_json, status, ingested_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(archive_identifier) DO UPDATE SET
            title = excluded.title,
            year = excluded.year,
            description = excluded.description,
            subjects_json = excluded.subjects_json,
            creator = excluded.creator,
            collection = excluded.collection,
            language = excluded.language,
            runtime_seconds = excluded.runtime_seconds,
            item_url = excluded.item_url,
            license_text = excluded.license_text,
            license_url = excluded.license_url,
            rights_notes = excluded.rights_notes,
            updated_at = excluded.updated_at
        """,
        (
            item["archive_identifier"],
            item["title"],
            item.get("year"),
            item.get("description", ""),
            json.dumps(item.get("subjects", [])),
            item.get("creator"),
            item.get("collection"),
            item.get("language"),
            item.get("runtime_seconds"),
            item["item_url"],
            item.get("license_text"),
            item.get("license_url"),
            item.get("rights_notes"),
            item.get("rights_confidence"),
            item.get("rights_confidence_score", 0.0),
            item.get("metadata_score", 0.0),
            item.get("metadata_reason_json", "{}"),
            item.get("status", "ingested"),
            item.get("ingested_at", now),
            now,
        ),
    )
    film_id = conn.execute(
        "SELECT id FROM films WHERE archive_identifier = ?",
        (item["archive_identifier"],),
    ).fetchone()["id"]

    for file_entry in item.get("files", []):
        conn.execute(
            """
            INSERT INTO film_files (
                film_id, filename, format, size_bytes, download_url,
                is_video, is_subtitle, is_preferred_source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(film_id, filename) DO UPDATE SET
                format = excluded.format,
                size_bytes = excluded.size_bytes,
                download_url = excluded.download_url,
                is_video = excluded.is_video,
                is_subtitle = excluded.is_subtitle,
                is_preferred_source = excluded.is_preferred_source
            """,
            (
                film_id,
                file_entry["filename"],
                file_entry.get("format"),
                file_entry.get("size_bytes"),
                file_entry.get("download_url"),
                int(file_entry.get("is_video", False)),
                int(file_entry.get("is_subtitle", False)),
                int(file_entry.get("is_preferred_source", False)),
                now,
            ),
        )

    return film_id
