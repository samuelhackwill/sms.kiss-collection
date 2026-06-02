from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ia_kissing_pipeline.scoring.metadata_rules import HARD_BLOCK_TERMS, POSITIVE_WEIGHTS


STRUCTURAL_NEGATIVE_TERMS = {
    "afrika",
    "africa",
    "bericht",
    "colony",
    "colonie",
    "colonial",
    "expedition",
    "heimat",
    "land in",
    "news",
    "report",
    "review",
    "war",
}

ROMANCE_POSITIVE_TERMS = set(POSITIVE_WEIGHTS.keys()) | {
    "affair",
    "beloved",
    "dating",
    "embrace",
    "fall in love",
    "fiance",
    "fiancee",
    "flirt",
    "heart",
    "honeymoon",
    "jealousy",
    "romantic",
    "triangle",
    "wedding",
}


@dataclass(frozen=True)
class TextGateResult:
    decision: str
    passed: bool
    source: str
    confidence: float
    reasons: dict


def evaluate_text_gate(
    *,
    title: str,
    description: str | None,
    subjects: list[str],
    collection: str | None,
    year: int | None,
    creator: str | None,
) -> TextGateResult:
    haystack = " ".join(
        part.lower()
        for part in (
            title or "",
            description or "",
            " ".join(subjects or []),
            collection or "",
            creator or "",
            str(year or ""),
        )
    )
    positive_hits = sorted(token for token in ROMANCE_POSITIVE_TERMS if token in haystack)
    hard_negative_hits = sorted(token for token in HARD_BLOCK_TERMS if token in haystack)
    structural_negative_hits = sorted(token for token in STRUCTURAL_NEGATIVE_TERMS if token in haystack)

    heuristic_reasons = {
        "positive_hits": positive_hits,
        "hard_negative_hits": hard_negative_hits,
        "structural_negative_hits": structural_negative_hits,
    }

    if hard_negative_hits:
        return TextGateResult(
            decision="reject",
            passed=False,
            source="heuristic",
            confidence=0.99,
            reasons={**heuristic_reasons, "summary": "hard negative metadata terms present"},
        )

    if positive_hits and not structural_negative_hits:
        return TextGateResult(
            decision="allow",
            passed=True,
            source="heuristic",
            confidence=0.9,
            reasons={**heuristic_reasons, "summary": "romance-oriented positive terms present"},
        )

    codex_result = _call_codex_text_gate(
        title=title,
        description=description,
        subjects=subjects,
        collection=collection,
        year=year,
        creator=creator,
        heuristic_reasons=heuristic_reasons,
    )
    if codex_result is not None:
        if codex_result.passed:
            return codex_result
        if structural_negative_hits:
            return codex_result
        return TextGateResult(
            decision="allow",
            passed=True,
            source="codex_mini_soft_allow",
            confidence=0.35,
            reasons={
                **heuristic_reasons,
                "summary": "ambiguous title-only no was treated as inconclusive, so the film remains eligible",
                "codex_answer": "no",
                "model": codex_result.reasons.get("model"),
            },
        )

    summary = "ambiguous metadata with no clear romance signal; kept eligible"
    if structural_negative_hits:
        summary = "structural negative title or subject signals without romance evidence"
    return TextGateResult(
        decision="reject" if structural_negative_hits else "allow",
        passed=not structural_negative_hits,
        source="heuristic_fallback",
        confidence=0.7 if structural_negative_hits else 0.3,
        reasons={**heuristic_reasons, "summary": summary, "fallback_reason": "codex_text_gate_unavailable"},
    )


def _call_codex_text_gate(
    *,
    title: str,
    description: str | None,
    subjects: list[str],
    collection: str | None,
    year: int | None,
    creator: str | None,
    heuristic_reasons: dict,
) -> TextGateResult | None:
    if os.getenv("IA_KISSING_USE_CODEX_TEXT_GATE", "1") != "1":
        return None
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return None

    model = os.getenv("IA_KISSING_CODEX_MODEL", "gpt-5.1-codex-mini")
    timeout_seconds = float(os.getenv("IA_KISSING_CODEX_TIMEOUT_SECONDS", "45"))
    workdir = Path(os.getenv("IA_KISSING_CODEX_WORKDIR", "/tmp/codex-text-gate"))
    workdir.mkdir(parents=True, exist_ok=True)

    prompt = "\n".join(
        [
            "Classify whether this film is likely to contain a consensual romantic kissing scene.",
            "Use only the metadata below.",
            "Do not use web search, shell commands, files, MCP, or any external tools.",
            "If the evidence is weak or ambiguous, answer no.",
            "Answer with exactly one word: yes or no.",
            f"Title: {title or ''}",
            f"Description: {description or ''}",
            f"Subjects: {', '.join(subjects or [])}",
            f"Collection: {collection or ''}",
            f"Year: {year or ''}",
            f"Creator: {creator or ''}",
        ]
    )

    with tempfile.NamedTemporaryFile(prefix="codex-text-gate-", suffix=".txt", delete=False) as tmp_file:
        output_path = Path(tmp_file.name)

    try:
        subprocess.run(
            [
                codex_bin,
                "exec",
                "-C",
                str(workdir),
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--ephemeral",
                "-c",
                'web_search="disabled"',
                "--model",
                model,
                "--output-last-message",
                str(output_path),
                prompt,
            ],
            text=True,
            capture_output=True,
            check=True,
            timeout=timeout_seconds,
        )
        answer = output_path.read_text().strip().lower()
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        output_path.unlink(missing_ok=True)

    if answer not in {"yes", "no"}:
        return None

    return TextGateResult(
        decision="allow" if answer == "yes" else "reject",
        passed=answer == "yes",
        source="codex_mini",
        confidence=0.65,
        reasons={
            **heuristic_reasons,
            "summary": "ambiguous title/metadata resolved by codex mini yes/no gate",
            "codex_answer": answer,
            "model": model,
        },
    )
