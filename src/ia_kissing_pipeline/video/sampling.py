from __future__ import annotations


def build_sample_windows(
    duration_seconds: float,
    *,
    interval_seconds: float = 45.0,
    max_frames: int = 40,
    window_seconds: float = 4.0,
) -> list[dict]:
    if duration_seconds <= 0 or max_frames <= 0:
        return []

    interval_seconds = max(1.0, interval_seconds)
    window_seconds = max(1.0, window_seconds)
    half_window = window_seconds / 2

    midpoints: list[float] = []
    current = min(max(half_window, 1.0), duration_seconds / 2 if duration_seconds > 2 else duration_seconds)
    while current < duration_seconds and len(midpoints) < max_frames:
        midpoints.append(current)
        current += interval_seconds

    if not midpoints:
        midpoints = [max(duration_seconds / 2, 0.0)]

    samples: list[dict] = []
    for index, midpoint in enumerate(midpoints, start=1):
        start = max(0.0, midpoint - half_window)
        end = min(duration_seconds, midpoint + half_window)
        samples.append(
            {
                "shot_index": index,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(end - start, 3),
                "midpoint_seconds": round(midpoint, 3),
            }
        )
    return samples
