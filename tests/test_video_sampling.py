from ia_kissing_pipeline.video.sampling import build_sample_windows


def test_build_sample_windows_caps_count_and_stays_in_bounds() -> None:
    samples = build_sample_windows(300.0, interval_seconds=60.0, max_frames=4, window_seconds=6.0)
    assert len(samples) == 4
    assert samples[0]["start_seconds"] >= 0.0
    assert samples[-1]["end_seconds"] <= 300.0
    assert all(sample["duration_seconds"] > 0 for sample in samples)
