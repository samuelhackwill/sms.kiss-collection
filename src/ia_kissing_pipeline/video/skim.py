from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def build_skim_preview(
    source_path: Path,
    output_path: Path,
    *,
    sample_every_seconds: float = 3.0,
    output_fps: int = 24,
    max_height: int = 360,
    progress_callback=None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ia-kissing-skim-") as tmpdir:
        frames_dir = Path(tmpdir)
        frame_pattern = frames_dir / "frame_%06d.jpg"
        if progress_callback:
            progress_callback("extracting_frames", 0.2)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(source_path),
                "-vf",
                f"fps=1/{max(0.5, sample_every_seconds)},scale=-2:min({max_height}\\,ih)",
                str(frame_pattern),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        if progress_callback:
            progress_callback("numbering_frames", 0.6)
        _number_frames(frames_dir)
        if progress_callback:
            progress_callback("encoding_preview", 0.85)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                str(max(1, output_fps)),
                "-i",
                str(frame_pattern),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            text=True,
            capture_output=True,
            check=True,
        )
        if progress_callback:
            progress_callback("done", 1.0)
    return output_path


def _number_frames(frames_dir: Path) -> None:
    font = ImageFont.load_default()
    for index, frame_path in enumerate(sorted(frames_dir.glob("frame_*.jpg")), start=1):
        image = Image.open(frame_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        text = str(index)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width = right - left
        text_height = bottom - top
        x = image.width - text_width - 24
        y = 24
        draw.rectangle(
            (x - 8, y - 8, x + text_width + 8, y + text_height + 8),
            fill=(0, 0, 0),
        )
        draw.text((x, y), text, fill=(255, 255, 255), font=font)
        image.save(frame_path, quality=95)
