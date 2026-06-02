from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe_media(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(result.stdout)
    video_stream = next((stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"), {})
    format_info = payload.get("format", {})
    return {
        "duration_seconds": float(format_info["duration"]) if format_info.get("duration") else None,
        "size_bytes": int(format_info["size"]) if format_info.get("size") else None,
        "bit_rate": int(format_info["bit_rate"]) if format_info.get("bit_rate") else None,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec_name": video_stream.get("codec_name"),
        "avg_frame_rate": video_stream.get("avg_frame_rate"),
    }

