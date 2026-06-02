from __future__ import annotations

from dataclasses import dataclass


POSITIVE_WEIGHTS = {
    "romance": 3.0,
    "lovers": 2.5,
    "love story": 2.5,
    "courtship": 2.0,
    "marriage": 1.5,
    "melodrama": 2.0,
    "couple": 1.5,
    "seduction": 2.0,
    "passion": 2.0,
    "boyfriend": 1.0,
    "girlfriend": 1.0,
    "husband": 1.0,
    "wife": 1.0,
    "dramatic fiction": 1.2,
    "feature narrative": 1.2,
    "kiss": 3.0,
}

NEGATIVE_WEIGHTS = {
    "ants": -3.0,
    "insects": -3.0,
    "documentary": -2.5,
    "instructional": -2.5,
    "industrial": -2.0,
    "military training": -3.0,
    "newsreel": -2.5,
    "educational": -2.0,
    "sermon": -2.5,
    "lecture": -2.0,
    "tourism reel": -2.0,
    "landscape film": -2.5,
    "machinery": -2.0,
}

HARD_BLOCK_TERMS = {
    "documentary": "non-fiction documentary material",
    "newsreel": "newsreel material",
    "propaganda": "propaganda material",
    "nazi": "Nazi-related material",
    "hitler": "Hitler-related material",
    "swastika": "swastika-related material",
    "third reich": "Third Reich-related material",
    "military training": "military training material",
    "war footage": "war-footage material",
    "travelogue": "travelogue material",
    "travelogues": "travelogue material",
    "ethnographic": "ethnographic material",
    "expedition": "expedition material",
}

COLLECTION_PRIORS = {
    "feature_films": 1.5,
    "silent_films": 0.5,
}


@dataclass(frozen=True)
class MetadataScoreResult:
    score: float
    reasons: dict
    blocked: bool


def score_metadata(title: str, description: str, subjects: list[str], collection: str | None) -> MetadataScoreResult:
    haystack = " ".join(
        part.lower()
        for part in (
            title or "",
            description or "",
            " ".join(subjects or []),
            collection or "",
        )
    )
    positives: list[dict] = []
    negatives: list[dict] = []
    hard_blocks: list[dict] = []
    raw_score = 0.0

    for token, weight in POSITIVE_WEIGHTS.items():
        if token in haystack:
            raw_score += weight
            positives.append({"signal": token, "weight": weight})

    for token, weight in NEGATIVE_WEIGHTS.items():
        if token in haystack:
            raw_score += weight
            negatives.append({"signal": token, "weight": weight})

    for token, rationale in HARD_BLOCK_TERMS.items():
        if token in haystack:
            hard_blocks.append({"signal": token, "reason": rationale})

    collection_prior = 0.0
    if collection:
        collection_prior = COLLECTION_PRIORS.get(collection.lower(), 0.0)
        raw_score += collection_prior

    normalized = max(0.0, min(1.0, (raw_score + 5.0) / 15.0))
    blocked = bool(hard_blocks)
    if blocked:
        normalized = 0.0
    reasons = {
        "raw_score": round(raw_score, 3),
        "positive_signals": positives,
        "negative_signals": negatives,
        "hard_block_signals": hard_blocks,
        "collection_prior": collection_prior,
        "blocked": blocked,
    }
    return MetadataScoreResult(score=round(normalized, 3), reasons=reasons, blocked=blocked)
