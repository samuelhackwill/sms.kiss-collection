from __future__ import annotations

import subprocess
from pathlib import Path


def extract_clip(video_path: Path, output_path: Path, start_seconds: float, duration_seconds: float) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            str(max(0.0, start_seconds)),
            "-i",
            str(video_path),
            "-t",
            str(max(0.5, duration_seconds)),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-an",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return output_path


def crop_clip(
    video_path: Path,
    output_path: Path,
    crop_x: float,
    crop_y: float,
    crop_width: float,
    crop_height: float,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    safe_x = min(max(crop_x, 0.0), 0.95)
    safe_y = min(max(crop_y, 0.0), 0.95)
    safe_w = min(max(crop_width, 0.05), 1.0 - safe_x)
    safe_h = min(max(crop_height, 0.05), 1.0 - safe_y)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"crop=iw*{safe_w}:ih*{safe_h}:iw*{safe_x}:ih*{safe_y}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "24",
            "-an",
            str(output_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    return output_path
