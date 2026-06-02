import subprocess
from pathlib import Path

from ia_kissing_pipeline.scoring.metadata_rules import score_metadata
from ia_kissing_pipeline.scoring.rights_score import score_rights
from ia_kissing_pipeline.scoring.text_gate import evaluate_text_gate
from ia_kissing_pipeline.utils.timecode import format_seconds


def test_metadata_scoring_prefers_romance() -> None:
    result = score_metadata(
        "Kiss in Spring",
        "A romance about lovers and marriage",
        ["romance", "lovers"],
        "feature_films",
    )
    assert result.score > 0.7
    assert result.reasons["positive_signals"]


def test_metadata_scoring_penalizes_documentary_subjects() -> None:
    result = score_metadata(
        "Ants of Industry",
        "An educational documentary about insects",
        ["documentary", "insects"],
        "ephemera",
    )
    assert result.score == 0.0
    assert result.blocked is True
    assert result.reasons["negative_signals"]
    assert result.reasons["hard_block_signals"]


def test_metadata_scoring_hard_blocks_propaganda_material() -> None:
    result = score_metadata(
        "Triumph of the Reich",
        "A propaganda newsreel with Nazi imagery",
        ["propaganda", "newsreel"],
        "feature_films",
    )
    assert result.score == 0.0
    assert result.blocked is True
    assert result.reasons["hard_block_signals"]


def test_metadata_scoring_does_not_hard_block_educational_use_boilerplate() -> None:
    result = score_metadata(
        "The Intimate Stranger",
        "For Academic / Educational Use Only. Film noir feature.",
        ["Film noir"],
        "Film_Noir",
    )
    assert result.blocked is False


def test_rights_scoring_prefers_explicit_public_domain() -> None:
    result = score_rights("Public Domain Mark 1.0", "https://creativecommons.org/publicdomain/mark/1.0/", 1932, "feature_films")
    assert result.category == "high_confidence"
    assert result.score == 0.9


def test_rights_scoring_penalizes_restricted_copyright() -> None:
    result = score_rights("All rights reserved", "https://example.com", 1948, "ephemera")
    assert result.category == "low_confidence"
    assert result.score == 0.2


def test_format_seconds() -> None:
    assert format_seconds(3723) == "01:02:03"


def test_text_gate_allows_romance_metadata_without_llm() -> None:
    result = evaluate_text_gate(
        title="Kiss in Spring",
        description="A romance melodrama about lovers and marriage.",
        subjects=["romance", "lovers"],
        collection="feature_films",
        year=1932,
        creator="Example Studio",
    )
    assert result.passed is True
    assert result.source == "heuristic"


def test_text_gate_rejects_ambiguous_non_romance_metadata_without_codex(monkeypatch) -> None:
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "0")
    result = evaluate_text_gate(
        title="1939 Deutsches Land In Afrika",
        description="Deutsche Kolonie in Afrika",
        subjects=["Deutsches Land in Afrika"],
        collection="mid-century-german-film",
        year=1939,
        creator="",
    )
    assert result.passed is False
    assert result.decision == "reject"


def test_text_gate_uses_codex_for_ambiguous_titles(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "1")
    monkeypatch.setenv("IA_KISSING_CODEX_WORKDIR", str(tmp_path))

    def fake_run(cmd, text, capture_output, check, timeout):
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text("no")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("ia_kissing_pipeline.scoring.text_gate.subprocess.run", fake_run)
    monkeypatch.setattr("ia_kissing_pipeline.scoring.text_gate.shutil.which", lambda _: "/usr/bin/codex")

    result = evaluate_text_gate(
        title="1939 Deutsches Land In Afrika",
        description="Deutsche Kolonie in Afrika",
        subjects=["Deutsches Land in Afrika"],
        collection="mid-century-german-film",
        year=1939,
        creator="",
    )
    assert result.passed is False
    assert result.source == "codex_mini"


def test_text_gate_soft_allows_inconclusive_codex_no_for_neutral_title(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("IA_KISSING_USE_CODEX_TEXT_GATE", "1")
    monkeypatch.setenv("IA_KISSING_CODEX_WORKDIR", str(tmp_path))

    def fake_run(cmd, text, capture_output, check, timeout):
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text("no")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("ia_kissing_pipeline.scoring.text_gate.subprocess.run", fake_run)
    monkeypatch.setattr("ia_kissing_pipeline.scoring.text_gate.shutil.which", lambda _: "/usr/bin/codex")

    result = evaluate_text_gate(
        title="Wiretapper",
        description="Crime drama film noir.",
        subjects=["Crime", "Drama", "Film-Noir"],
        collection="Film_Noir",
        year=1955,
        creator="",
    )
    assert result.passed is True
    assert result.source == "codex_mini_soft_allow"
