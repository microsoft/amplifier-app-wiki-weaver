"""persist_guidance: writes HUMAN.GATE.TEXT verbatim, and refuses the
auto-approve stub (see ingest.dot's collect_guidance FAIL-LOUD guard)."""

from __future__ import annotations

from wiki_weaver.ingest import persist_guidance


def test_writes_guidance_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("HUMAN.GATE.TEXT", "Please cite [2] more precisely.")
    out_path = tmp_path / ".ai" / "review-guidance.md"

    code = persist_guidance.main(["--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "Please cite [2] more precisely."


def test_rejects_auto_approved_stub(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HUMAN.GATE.TEXT", "auto-approved")
    out_path = tmp_path / "review-guidance.md"

    code = persist_guidance.main(["--out", str(out_path)])

    assert code == 1
    assert not out_path.exists()
    assert "auto-approve stub" in capsys.readouterr().err


def test_missing_env_var_writes_empty_string(tmp_path, monkeypatch):
    monkeypatch.delenv("HUMAN.GATE.TEXT", raising=False)
    out_path = tmp_path / "review-guidance.md"
    code = persist_guidance.main(["--out", str(out_path)])
    assert code == 0
    assert out_path.read_text(encoding="utf-8") == ""
