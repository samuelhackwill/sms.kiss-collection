from __future__ import annotations

from ia_kissing_pipeline.utils.timecode import format_seconds


def format_review_batch(rows: list[dict], batch_id: int) -> str:
    lines = [f"Review batch {batch_id}"]
    for row in rows:
        reason = row["reason_summary"]
        lines.extend(
            [
                f"[{row['display_index']}] {row['title']} ({row['year'] or 'unknown'})",
                f"archive_id: {row['archive_identifier']}",
                f"timestamp: {format_seconds(row['peak_seconds'])}",
                f"confidence: {row['confidence']:.2f}",
                f"rights: {row['rights_confidence'] or 'unscored'}",
                f"reason: {reason}",
                f"preview: {row['preview_path'] or 'none'}",
                "",
            ]
        )
    lines.extend(
        [
            "Reply with:",
            "- numbers to approve: 1 3 5",
            "- reject 2",
            "- more 4",
            "- clip 1",
        ]
    )
    return "\n".join(lines)

