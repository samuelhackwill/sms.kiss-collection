from __future__ import annotations

import json
from pathlib import Path

from ia_kissing_pipeline.ingest.store import upsert_film_item
from ia_kissing_pipeline.utils.time import utc_now_iso


def ingest_fixture(conn, fixture_path: Path) -> dict:
    payload = json.loads(fixture_path.read_text())
    film_count = 0
    file_count = 0

    for item in payload["items"]:
        item.setdefault("ingested_at", utc_now_iso())
        upsert_film_item(conn, item)
        film_count += 1
        file_count += len(item.get("files", []))

    return {"films_upserted": film_count, "files_upserted": file_count}
