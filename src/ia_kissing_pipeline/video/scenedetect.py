from __future__ import annotations

import subprocess
from pathlib import Path

from ia_kissing_pipeline.video.probe import probe_media


def detect_scene_change_timestamps(video_path: Path, output_dir: Path, threshold: float = 0.30) -> list[float]:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "scene_changes.txt"
    if metadata_path.exists():
        metadata_path.unlink()

    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-filter:v",
            f"select='gt(scene,{threshold})',metadata=print:file={metadata_path}",
            "-vsync",
            "vfr",
            "-f",
            "null",
            "-",
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    if not metadata_path.exists():
        return []

    timestamps: list[float] = []
    for line in metadata_path.read_text().splitlines():
        if "pts_time:" in line:
            _, value = line.split("pts_time:", 1)
            timestamps.append(float(value.strip()))
    return sorted(set(timestamps))


def build_shots(video_path: Path, threshold: float = 0.30) -> list[dict]:
    scene_dir = video_path.parent / "scene_cache"
    timestamps = detect_scene_change_timestamps(video_path, scene_dir, threshold=threshold)
    probe = probe_media(video_path)
    duration = float(probe["duration_seconds"] or 0)
    boundaries = [0.0, *timestamps]
    if duration > 0:
        boundaries.append(duration)
    boundaries = sorted(set(boundaries))
    shots: list[dict] = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        if end <= start:
            continue
        shots.append(
            {
                "shot_index": index,
                "start_seconds": round(start, 3),
                "end_seconds": round(end, 3),
                "duration_seconds": round(end - start, 3),
                "midpoint_seconds": round(start + ((end - start) / 2), 3),
            }
        )
    if not shots and duration > 0:
        shots.append(
            {
                "shot_index": 1,
                "start_seconds": 0.0,
                "end_seconds": round(duration, 3),
                "duration_seconds": round(duration, 3),
                "midpoint_seconds": round(duration / 2, 3),
            }
        )
    return shots


def extract_keyframe(video_path: Path, timestamp: float, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(timestamp),
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return output_path
