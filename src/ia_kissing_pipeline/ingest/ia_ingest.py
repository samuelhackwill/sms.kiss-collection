from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from ia_kissing_pipeline.ingest.store import upsert_film_item
from ia_kissing_pipeline.utils.time import utc_now_iso


VIDEO_FORMAT_HINTS = ("mpeg4", "h.264", "ogg video", "512kb mpeg4", "divx", "mp4", "ogv")
SUBTITLE_HINTS = ("subrip", "vtt", "subtitle")


def make_checkpoint_key(query: str) -> str:
    digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:12]
    return f"ia:{digest}"


def parse_runtime_seconds(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    match = re.match(r"(?:(\d+):)?(\d{1,2}):(\d{2})$", text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        return hours * 3600 + minutes * 60 + seconds
    match = re.match(r"(\d+)\s+minutes?(?:\s+(\d+)\s+seconds?)?", text)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2) or 0)
        return minutes * 60 + seconds
    return None


def _first_string(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0]) if value else None
    return str(value)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        if ";" in value:
            return [part.strip() for part in value.split(";") if part.strip()]
        return [value]
    return [str(value)]


def normalize_item(search_doc: dict, metadata_payload: dict) -> dict:
    metadata = metadata_payload.get("metadata", {})
    identifier = search_doc["identifier"]
    files = []
    for file_entry in metadata_payload.get("files", []):
        name = file_entry.get("name")
        if not name:
            continue
        file_format = _first_string(file_entry.get("format"))
        lowered_format = (file_format or "").lower()
        lowered_name = name.lower()
        is_video = any(hint in lowered_format for hint in VIDEO_FORMAT_HINTS) or lowered_name.endswith(
            (".mp4", ".ogv", ".avi", ".mkv", ".mov")
        )
        is_subtitle = any(hint in lowered_format for hint in SUBTITLE_HINTS) or lowered_name.endswith(
            (".srt", ".vtt", ".sub")
        )
        files.append(
            {
                "filename": name,
                "format": file_format,
                "size_bytes": int(file_entry["size"]) if str(file_entry.get("size", "")).isdigit() else None,
                "download_url": f"https://archive.org/download/{identifier}/{name}",
                "is_video": is_video,
                "is_subtitle": is_subtitle,
                "is_preferred_source": False,
            }
        )

    def preference(file_info: dict) -> tuple[int, int]:
        format_value = (file_info.get("format") or "").lower()
        filename_value = file_info["filename"].lower()
        score = 0
        if "512kb" in format_value or "512kb" in filename_value:
            score += 4
        if "h.264" in format_value or filename_value.endswith(".mp4"):
            score += 3
        if "ogg video" in format_value or filename_value.endswith(".ogv"):
            score += 2
        if file_info["is_video"]:
            score += 1
        size = file_info.get("size_bytes") or 0
        return (score, -size)

    video_files = [file_info for file_info in files if file_info["is_video"]]
    if video_files:
        preferred = max(video_files, key=preference)
        preferred["is_preferred_source"] = True

    return {
        "archive_identifier": identifier,
        "title": _first_string(metadata.get("title")) or _first_string(search_doc.get("title")) or identifier,
        "year": int(str(_first_string(metadata.get("year")) or _first_string(search_doc.get("year")) or "0")) or None,
        "description": _first_string(metadata.get("description")) or _first_string(search_doc.get("description")) or "",
        "subjects": _as_list(metadata.get("subject") or search_doc.get("subject")),
        "creator": _first_string(metadata.get("creator") or search_doc.get("creator")),
        "collection": _first_string(metadata.get("collection") or search_doc.get("collection")),
        "language": _first_string(metadata.get("language") or search_doc.get("language")),
        "runtime_seconds": parse_runtime_seconds(metadata.get("runtime") or search_doc.get("runtime")),
        "item_url": f"https://archive.org/details/{identifier}",
        "license_text": _first_string(metadata.get("licenseurl")),
        "license_url": _first_string(metadata.get("licenseurl")),
        "rights_notes": json.dumps({"source": "internet_archive_metadata"}, sort_keys=True),
        "status": "ingested",
        "ingested_at": utc_now_iso(),
        "files": files,
    }


@dataclass(frozen=True)
class IngestResult:
    films_upserted: int
    files_upserted: int
    pages_fetched: int
    checkpoint_key: str
    next_page: int


def get_checkpoint(conn, checkpoint_key: str, query: str, max_items: int | None) -> dict:
    row = conn.execute(
        "SELECT * FROM ingest_checkpoints WHERE checkpoint_key = ?",
        (checkpoint_key,),
    ).fetchone()
    if row:
        return dict(row)

    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO ingest_checkpoints (
            checkpoint_key, query_text, next_page, fetched_count, max_items, status, updated_at
        ) VALUES (?, ?, 1, 0, ?, 'pending', ?)
        """,
        (checkpoint_key, query, max_items, now),
    )
    return {
        "checkpoint_key": checkpoint_key,
        "query_text": query,
        "next_page": 1,
        "fetched_count": 0,
        "max_items": max_items,
        "status": "pending",
        "last_error": None,
        "updated_at": now,
    }


def update_checkpoint(conn, checkpoint_key: str, *, next_page: int, fetched_count: int, max_items: int | None, status: str, last_error: str | None = None) -> None:
    conn.execute(
        """
        UPDATE ingest_checkpoints
        SET next_page = ?, fetched_count = ?, max_items = ?, status = ?, last_error = ?, updated_at = ?
        WHERE checkpoint_key = ?
        """,
        (next_page, fetched_count, max_items, status, last_error, utc_now_iso(), checkpoint_key),
    )


def ingest_from_ia(conn, client, *, query: str, limit: int, rows: int, checkpoint_key: str | None = None) -> IngestResult:
    checkpoint_key = checkpoint_key or make_checkpoint_key(query)
    checkpoint = get_checkpoint(conn, checkpoint_key, query, limit)
    page = int(checkpoint["next_page"])
    fetched_count = int(checkpoint["fetched_count"])
    target_fetched_count = fetched_count + limit
    films_upserted = 0
    files_upserted = 0
    pages_fetched = 0

    while fetched_count < target_fetched_count:
        remaining = target_fetched_count - fetched_count
        page_rows = min(rows, remaining)
        payload = client.fetch_search_page(query, page=page, rows=page_rows)
        docs = payload.get("response", {}).get("docs", [])
        pages_fetched += 1
        if not docs:
            update_checkpoint(
                conn,
                checkpoint_key,
                next_page=page,
                fetched_count=fetched_count,
                max_items=target_fetched_count,
                status="complete",
            )
            break

        for doc in docs:
            identifier = doc["identifier"]
            metadata_payload = client.fetch_metadata(identifier)
            item = normalize_item(doc, metadata_payload)
            upsert_film_item(conn, item)
            films_upserted += 1
            files_upserted += len(item["files"])
            fetched_count += 1
            if fetched_count >= target_fetched_count:
                break

        page += 1
        status = "complete" if fetched_count >= target_fetched_count else "running"
        update_checkpoint(
            conn,
            checkpoint_key,
            next_page=page,
            fetched_count=fetched_count,
            max_items=target_fetched_count,
            status=status,
        )

    return IngestResult(
        films_upserted=films_upserted,
        files_upserted=files_upserted,
        pages_fetched=pages_fetched,
        checkpoint_key=checkpoint_key,
        next_page=page,
    )
