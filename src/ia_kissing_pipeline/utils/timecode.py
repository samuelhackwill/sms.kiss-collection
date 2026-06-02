from __future__ import annotations


def format_seconds(seconds: int | float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"

