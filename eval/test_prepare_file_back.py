"""ask.prepare_file_back: writes the answer as a new immutable sources/
file with a kind: article header, and scopes restrict_to_sources to it."""

from __future__ import annotations

import json

from wiki_weaver.ask import prepare_file_back
from wiki_weaver.lib import WikiRoot, read_source_kind


def test_writes_new_source_file_with_kind_header(wiki_root, monkeypatch):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.root / ".ai" / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("The owner is Jane Doe, per [[oncall-runbook]].\n", encoding="utf-8")
    monkeypatch.setattr(prepare_file_back.time, "time", lambda: 1_700_000_000)

    code = prepare_file_back.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])

    assert code == 0
    written = wr.sources_dir / "synthetic-answer-1700000000.txt"
    assert written.is_file()
    content = written.read_text(encoding="utf-8")
    assert content.startswith("kind: article")
    assert "The owner is Jane Doe" in content
    assert read_source_kind(written) == "article"


def test_stdout_is_one_json_line_with_restrict_to_sources(wiki_root, monkeypatch, capsys):
    wr = WikiRoot(wiki_root.root)
    answer_path = wr.root / ".ai" / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Some answer text.\n", encoding="utf-8")
    monkeypatch.setattr(prepare_file_back.time, "time", lambda: 1_700_000_001)

    prepare_file_back.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])

    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    payload = json.loads(out)
    assert payload == {"restrict_to_sources": "synthetic-answer-1700000001.txt"}


def test_registered_as_ordinary_source_not_a_special_case(wiki_root, monkeypatch):
    """The filed-back answer must be indistinguishable, to select_source, from
    any other source file -- it's ordinary raw material, per contract."""
    from wiki_weaver.ingest import select_source

    wr = WikiRoot(wiki_root.root)
    answer_path = wr.root / ".ai" / "answer.md"
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    answer_path.write_text("Answer content.\n", encoding="utf-8")
    monkeypatch.setattr(prepare_file_back.time, "time", lambda: 1_700_000_002)

    prepare_file_back.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])

    pending = select_source.compute_pending(wr, "")
    assert "synthetic-answer-1700000002.txt" in pending
