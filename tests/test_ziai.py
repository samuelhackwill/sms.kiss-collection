from __future__ import annotations

import json

from ia_kissing_pipeline.ziai import _candidate_indices, _prepare_extraction_cache


def test_ziai_candidate_indices_uses_original_dense_window_rule() -> None:
    predictions = [0, 1, 1, 1, 1, 1, 1, 1, 0, 0, 1, 0]

    candidates = _candidate_indices(predictions, min_frames=10, threshold=0.7)

    assert candidates == [list(range(1, 11))]


def test_ziai_candidate_indices_rejects_short_positive_bursts() -> None:
    predictions = [1, 1, 1, 1, 1, 1, 1, 1, 1]

    candidates = _candidate_indices(predictions, min_frames=10, threshold=0.7)

    assert candidates == []


def test_ziai_candidate_indices_rejects_sparse_positive_noise() -> None:
    predictions = [1, 0, 0, 1, 0, 0, 1, 0, 0, 1]

    candidates = _candidate_indices(predictions, min_frames=10, threshold=0.7)

    assert candidates == []


def test_ziai_extraction_cache_invalidates_when_metadata_changes(tmp_path) -> None:
    cache_dir = tmp_path / "cache"
    first_metadata = {"version": 1, "source_path": "/tmp/source-a.mp4", "chunk_seconds": 300.0}
    second_metadata = {"version": 1, "source_path": "/tmp/source-b.mp4", "chunk_seconds": 300.0}

    _prepare_extraction_cache(cache_dir, first_metadata)
    stale_chunk = cache_dir / "chunk_0001.pt"
    stale_chunk.write_text("old tensor cache")
    _prepare_extraction_cache(cache_dir, first_metadata)

    assert stale_chunk.exists()

    _prepare_extraction_cache(cache_dir, second_metadata)

    assert not stale_chunk.exists()
    assert json.loads((cache_dir / "manifest.json").read_text()) == second_metadata
