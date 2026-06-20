from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

from ia_kissing_pipeline import media_storage
from ia_kissing_pipeline.media_storage import prune_cache_directory, upload_media_file, upload_media_tree


def _settings(tmp_path: Path):
    return SimpleNamespace(
        media_storage_backend="s3",
        media_s3_bucket="bucket",
        media_s3_endpoint_url="https://s3.example.test",
        media_s3_region="gra",
        media_s3_access_key_id="key",
        media_s3_secret_access_key="secret",
        media_s3_acl="",
        media_s3_cache_control="",
        media_s3_public_base_url="",
        preview_dir=tmp_path / "previews",
        clips_dir=tmp_path / "clips",
    )


class _FakeS3Client:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, str]] = []

    def upload_file(self, filename: str, bucket: str, key: str, **kwargs) -> None:
        assert Path(filename).exists()
        self.uploads.append((filename, bucket, key))


def test_upload_media_file_can_delete_local_copy_after_s3_upload(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    fake_client = _FakeS3Client()
    monkeypatch.setattr(media_storage, "_s3_client", lambda loaded_settings: fake_client)
    path = settings.preview_dir / "film" / "skim-preview.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"preview")

    key = upload_media_file(settings, "preview", path, delete_local_after_upload=True)

    assert key == "previews/film/skim-preview.mp4"
    assert fake_client.uploads == [(str(path), "bucket", "previews/film/skim-preview.mp4")]
    assert not path.exists()


def test_upload_media_tree_deletes_uploaded_files_and_empty_parents(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    fake_client = _FakeS3Client()
    monkeypatch.setattr(media_storage, "_s3_client", lambda loaded_settings: fake_client)
    root = settings.preview_dir / "film" / "ziai" / "candidates"
    (root / "nested").mkdir(parents=True)
    (root / "candidate_001.mp4").write_bytes(b"clip")
    (root / "nested" / "candidate_002.mp4").write_bytes(b"clip")

    keys = upload_media_tree(settings, "preview", root, delete_local_after_upload=True)

    assert keys == [
        "previews/film/ziai/candidates/candidate_001.mp4",
        "previews/film/ziai/candidates/nested/candidate_002.mp4",
    ]
    assert not root.exists()


def test_prune_cache_directory_removes_old_files_then_enforces_size(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    old_file = cache_dir / "old.bin"
    oldest_large_file = cache_dir / "oldest-large.bin"
    newer_large_file = cache_dir / "newer-large.bin"
    old_file.write_bytes(b"a" * 20)
    oldest_large_file.write_bytes(b"b" * 60)
    newer_large_file.write_bytes(b"c" * 60)
    now = time.time()
    os.utime(old_file, (now - 500, now - 500))
    os.utime(oldest_large_file, (now - 100, now - 100))
    os.utime(newer_large_file, (now - 50, now - 50))

    result = prune_cache_directory(cache_dir, max_bytes=60, max_age_seconds=300)

    assert result["files_removed"] == 2
    assert result["bytes_before"] == 140
    assert result["bytes_after"] == 60
    assert result["bytes_removed"] == 80
    assert not old_file.exists()
    assert not oldest_large_file.exists()
    assert newer_large_file.exists()
