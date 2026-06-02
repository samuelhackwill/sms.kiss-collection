from __future__ import annotations

from dataclasses import dataclass


HIGH_SIGNALS = ("public domain", "public-domain", "creative commons", "cc-by", "cc0")
MEDIUM_SIGNALS = ("license", "licensed", "rights", "permission")
LOW_SIGNALS = ("all rights reserved", "copyright", "unknown", "restricted")


@dataclass(frozen=True)
class RightsScoreResult:
    category: str
    score: float
    reasons: dict


def score_rights(license_text: str | None, license_url: str | None, year: int | None, collection: str | None) -> RightsScoreResult:
    haystack = " ".join(part.lower() for part in (license_text or "", license_url or "", collection or ""))
    matched_high = [signal for signal in HIGH_SIGNALS if signal in haystack]
    matched_medium = [signal for signal in MEDIUM_SIGNALS if signal in haystack]
    matched_low = [signal for signal in LOW_SIGNALS if signal in haystack]

    inferred_old_film = bool(year and year <= 1930)

    if matched_low:
        category, score = "low_confidence", 0.2
    elif matched_high:
        category, score = "high_confidence", 0.9
    elif matched_medium or inferred_old_film:
        category, score = "medium_confidence", 0.6
    else:
        category, score = "low_confidence", 0.3

    return RightsScoreResult(
        category=category,
        score=score,
        reasons={
            "matched_high": matched_high,
            "matched_medium": matched_medium,
            "matched_low": matched_low,
            "inferred_old_film": inferred_old_film,
        },
    )

