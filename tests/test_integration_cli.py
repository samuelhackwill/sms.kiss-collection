from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "ia_items.json"


def run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DB_PATH"] = str(tmp_path / "pipeline.db")
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "ia_kissing_pipeline.main", *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_fixture_ingest_and_scoring_flow(tmp_path: Path) -> None:
    run_cli(tmp_path, "init-db")
    ingest = run_cli(tmp_path, "ingest-fixture", str(FIXTURE_PATH))
    metadata = run_cli(tmp_path, "score-metadata")
    text_gate = run_cli(tmp_path, "score-text-gate")
    rights = run_cli(tmp_path, "score-rights")
    status = run_cli(tmp_path, "status")

    ingest_payload = json.loads(ingest.stdout)
    status_payload = json.loads(status.stdout)

    assert ingest_payload["films_upserted"] == 2
    assert ingest_payload["files_upserted"] == 2
    assert "Scored metadata for 2 films" in metadata.stdout
    assert "Scored text gate for 1 films" in text_gate.stdout
    assert "Scored rights for 1 films" in rights.stdout
    assert status_payload["films_total"] == 2
    assert status_payload["films_metadata_scored"] == 1
    assert status_payload["films_metadata_excluded"] == 1
    assert status_payload["films_text_gate_excluded"] == 0
    assert status_payload["rights_buckets"]["high_confidence"] == 1


def test_list_and_show_film_commands(tmp_path: Path) -> None:
    run_cli(tmp_path, "init-db")
    run_cli(tmp_path, "ingest-fixture", str(FIXTURE_PATH))
    run_cli(tmp_path, "score-metadata")
    run_cli(tmp_path, "score-rights")

    listed = run_cli(tmp_path, "list-films", "--limit", "5")
    shown = run_cli(tmp_path, "show-film", "--archive-identifier", "kiss_in_spring_1932")

    listed_payload = json.loads(listed.stdout)
    shown_payload = json.loads(shown.stdout)

    assert len(listed_payload) == 2
    assert shown_payload["archive_identifier"] == "kiss_in_spring_1932"
    assert shown_payload["files"]
