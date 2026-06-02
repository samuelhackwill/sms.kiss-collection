from __future__ import annotations


def utc_now_iso() -> str:
    from datetime import datetime, UTC

    return datetime.now(UTC).replace(microsecond=0).isoformat()

