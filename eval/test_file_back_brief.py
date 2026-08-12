"""file_back_brief: the self-contained brief for ask.dot's file_back_gate --
question + answer gist. FAIL LOUD (never fabricate) when the answer file is
missing -- same discipline as review_brief.py/takeaways_brief.py, though
this path should never be reached given ask.dot's own coverage_check
contract."""

from __future__ import annotations

import json

from wiki_weaver.ask import file_back_brief
from wiki_weaver.lib import WikiRoot


def test_missing_answer_file_fails_loud_no_fabricated_brief(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)

    code = file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", ".ai/answer.md"])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""
    assert "does not exist" in err


def test_brief_includes_question_and_answer_gist(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.ai_dir / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Jane Doe owns the on-call runbook, per [[oncall-runbook]].\n", encoding="utf-8")
    monkeypatch.setenv("QUESTION", "who owns the on-call runbook?")

    code = file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["file_back_brief"]
    assert "who owns the on-call runbook?" in brief
    assert "Jane Doe owns the on-call runbook" in brief
    assert "[A]" in brief
    assert "ingest" in brief.lower()


def test_missing_question_env_reports_honestly(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.ai_dir / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Some answer.\n", encoding="utf-8")
    monkeypatch.delenv("QUESTION", raising=False)

    code = file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "question text unavailable" in payload["file_back_brief"]


def test_long_answer_is_truncated_with_ellipsis(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.ai_dir / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("word " * 1000, encoding="utf-8")
    monkeypatch.setenv("QUESTION", "a long question")

    code = file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "\u2026" in payload["file_back_brief"]


def test_writes_file_back_brief_file(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.ai_dir / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Some answer.\n", encoding="utf-8")
    monkeypatch.setenv("QUESTION", "a question")

    code = file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    stdout_payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert wr.file_back_brief_file.is_file()
    file_payload = json.loads(wr.file_back_brief_file.read_text(encoding="utf-8"))
    assert file_payload["stage"] == "file_back_gate"
    assert file_payload["brief"] == stdout_payload["file_back_brief"]


def test_output_is_exactly_one_json_line_on_success(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.ai_dir / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Some answer.\n", encoding="utf-8")
    monkeypatch.setenv("QUESTION", "a question")

    file_back_brief.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
