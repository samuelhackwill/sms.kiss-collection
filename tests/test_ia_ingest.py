from __future__ import annotations

from pathlib import Path

from ia_kissing_pipeline.db import connect, init_db
from ia_kissing_pipeline.ingest.ia_ingest import (
    get_checkpoint,
    ingest_from_ia,
    make_checkpoint_key,
    parse_runtime_seconds,
)


class FakeIAClient:
    def fetch_search_page(self, query: str, page: int, rows: int) -> dict:
        assert query == "collection:feature_films"
        if page > 1:
            return {"response": {"docs": []}}
        return {
            "response": {
                "docs": [
                    {
                        "identifier": "fake-film-1",
                        "title": "Fake Film 1",
                        "year": 1931,
                        "description": "A romance melodrama",
                        "subject": ["romance"],
                        "collection": ["feature_films"],
                    }
                ]
            }
        }

    def fetch_metadata(self, identifier: str) -> dict:
        assert identifier == "fake-film-1"
        return {
            "metadata": {
                "title": "Fake Film 1",
                "year": "1931",
                "description": "A romance melodrama",
                "subject": "romance; lovers",
                "collection": ["feature_films"],
                "runtime": "1:10:00",
                "licenseurl": "https://creativecommons.org/publicdomain/mark/1.0/",
            },
            "files": [
                {
                    "name": "fake-film-1.mp4",
                    "format": "h.264",
                    "size": "123456",
                }
            ],
        }


def test_make_checkpoint_key_is_stable() -> None:
    assert make_checkpoint_key("collection:feature_films") == make_checkpoint_key("collection:feature_films")


def test_parse_runtime_seconds() -> None:
    assert parse_runtime_seconds("1:10:00") == 4200
    assert parse_runtime_seconds("88 minutes 12 seconds") == 5292


def test_ingest_from_ia_records_checkpoint_and_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "pipeline.db"
    init_db(db_path)
    client = FakeIAClient()

    with connect(db_path) as conn:
        result = ingest_from_ia(
            conn,
            client,
            query="collection:feature_films",
            limit=1,
            rows=1,
        )
        checkpoint = get_checkpoint(conn, result.checkpoint_key, "collection:feature_films", 1)
        film_count = conn.execute("SELECT COUNT(*) AS count FROM films").fetchone()["count"]
        file_count = conn.execute("SELECT COUNT(*) AS count FROM film_files").fetchone()["count"]

    assert result.films_upserted == 1
    assert result.files_upserted == 1
    assert checkpoint["fetched_count"] == 1
    assert checkpoint["status"] == "complete"
    assert film_count == 1
    assert file_count == 1
