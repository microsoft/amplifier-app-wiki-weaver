"""wiki_weaver.correct.capture: writes CORRECTION_TEXT or HUMAN.GATE.TEXT
verbatim (whichever is non-empty), and refuses the auto-approve stub and
empty input (see pipeline/CLI-CONTRACT.md's FAIL-LOUD requirement)."""

from __future__ import annotations

from wiki_weaver.correct import capture


def test_writes_correction_text_when_provided_externally(tmp_path, monkeypatch):
    monkeypatch.setenv("CORRECTION_TEXT", "Alice, not Bob, led the migration.")
    monkeypatch.delenv("HUMAN.GATE.TEXT", raising=False)
    out_path = tmp_path / ".ai" / "correction-text.md"

    code = capture.main(["--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "Alice, not Bob, led the migration."


def test_falls_back_to_freeform_gate_text_when_no_param(tmp_path, monkeypatch):
    monkeypatch.delenv("CORRECTION_TEXT", raising=False)
    monkeypatch.setenv("HUMAN.GATE.TEXT", "The onboarding doc misattributes the launch date.")
    out_path = tmp_path / "correction-text.md"

    code = capture.main(["--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "The onboarding doc misattributes the launch date."


def test_correction_text_wins_over_gate_text_when_both_set(tmp_path, monkeypatch):
    """correction_provided=true forks straight to capture_correction --
    CORRECTION_TEXT is the intended source in that path even if a stale
    HUMAN.GATE.TEXT happens to still be set in the environment."""
    monkeypatch.setenv("CORRECTION_TEXT", "external correction")
    monkeypatch.setenv("HUMAN.GATE.TEXT", "stale gate text")
    out_path = tmp_path / "correction-text.md"

    code = capture.main(["--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "external correction"


def test_rejects_auto_approved_stub(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORRECTION_TEXT", raising=False)
    monkeypatch.setenv("HUMAN.GATE.TEXT", "auto-approved")
    out_path = tmp_path / "correction-text.md"

    code = capture.main(["--out", str(out_path)])

    assert code == 1
    assert not out_path.exists()
    assert "auto-approve stub" in capsys.readouterr().err


def test_rejects_empty_text(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CORRECTION_TEXT", raising=False)
    monkeypatch.delenv("HUMAN.GATE.TEXT", raising=False)
    out_path = tmp_path / "correction-text.md"

    code = capture.main(["--out", str(out_path)])

    assert code == 1
    assert not out_path.exists()
    assert "no correction text" in capsys.readouterr().err
