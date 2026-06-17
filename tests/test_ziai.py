from __future__ import annotations

from ia_kissing_pipeline.ziai import _candidate_indices


def test_ziai_candidate_indices_keeps_short_dense_positive_sequences() -> None:
    predictions = [0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0]

    candidates = _candidate_indices(predictions, min_frames=3, threshold=0.5, max_gap_frames=2)

    assert candidates == [list(range(1, 10))]


def test_ziai_candidate_indices_rejects_sparse_positive_noise() -> None:
    predictions = [1, 0, 0, 1, 0, 0, 1]

    candidates = _candidate_indices(predictions, min_frames=3, threshold=0.5, max_gap_frames=2)

    assert candidates == []
