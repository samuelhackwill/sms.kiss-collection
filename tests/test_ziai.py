from __future__ import annotations

from ia_kissing_pipeline.ziai import _candidate_indices


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
