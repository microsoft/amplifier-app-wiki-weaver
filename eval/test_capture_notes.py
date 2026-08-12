"""init.capture_notes: appends (never overwrites) each interview round, and
refuses the literal auto-approve stub -- see init.dot's FAIL-LOUD guard."""

from __future__ import annotations

from wiki_weaver.init import capture_notes


def test_first_round_writes_the_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMAN.GATE.TEXT", "This wiki tracks our team's runbooks.")
    out_path = tmp_path / ".ai" / "interview-notes.md"

    code = capture_notes.main(["--out", str(out_path)])

    assert code == 0
    content = out_path.read_text(encoding="utf-8")
    assert "This wiki tracks our team's runbooks." in content
    assert "Round 1" in content


def test_second_round_appends_rather_than_overwrites(tmp_path, monkeypatch):
    out_path = tmp_path / "interview-notes.md"

    monkeypatch.setenv("HUMAN.GATE.TEXT", "Purpose: runbooks and on-call knowledge.")
    capture_notes.main(["--out", str(out_path)])

    monkeypatch.setenv("HUMAN.GATE.TEXT", "Sample sources: incident postmortems, wiki exports.")
    capture_notes.main(["--out", str(out_path)])

    content = out_path.read_text(encoding="utf-8")
    assert "Purpose: runbooks and on-call knowledge." in content
    assert "Sample sources: incident postmortems, wiki exports." in content
    assert "Round 1" in content
    assert "Round 2" in content
    # First round's text must still precede the second's -- true accumulation.
    assert content.index("Purpose:") < content.index("Sample sources:")


def test_rejects_auto_approved_stub_and_does_not_write(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HUMAN.GATE.TEXT", "auto-approved")
    out_path = tmp_path / "interview-notes.md"

    code = capture_notes.main(["--out", str(out_path)])

    assert code == 1
    assert not out_path.exists()
    assert "auto-approve stub" in capsys.readouterr().err


def test_rejects_auto_approved_stub_even_mid_loop(tmp_path, monkeypatch):
    out_path = tmp_path / "interview-notes.md"
    monkeypatch.setenv("HUMAN.GATE.TEXT", "Round one real answer.")
    capture_notes.main(["--out", str(out_path)])

    monkeypatch.setenv("HUMAN.GATE.TEXT", "auto-approved")
    code = capture_notes.main(["--out", str(out_path)])

    assert code == 1
    # The prior round's real content must be untouched by the rejected round.
    content = out_path.read_text(encoding="utf-8")
    assert "Round one real answer." in content
    assert "auto-approved" not in content
