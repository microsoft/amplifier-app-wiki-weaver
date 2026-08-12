"""review_brief: the self-contained brief prepare_review_brief hands
review_gate -- source id, kind, segment position, WHICH pages were created
vs updated (by name), and the retention flag. FAIL LOUD (never fabricate)
when the current-source breadcrumb is missing/unreadable -- same contract
budget.py and commit.py already enforce via read_current_source_id."""

from __future__ import annotations

import json

import pytest

from wiki_weaver.ingest import review_brief
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt") -> WikiRoot:
    wiki_root.add_source(name)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def test_missing_current_source_fails_loud_no_fabricated_brief(wiki_root, capsys):
    """No current_source.txt at all (select_source never ran for this pass)
    -- must fail rather than emit an empty or invented brief."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)  # .ai/ exists, but current_source.txt does not

    code = review_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""  # never a fabricated JSON brief on failure
    assert "current_source" in err  # actionable, same message read_current_source_id raises


def test_empty_current_source_file_fails_loud(wiki_root, capsys):
    """An empty (torn/truncated) current_source.txt is exactly as unusable
    as a missing one -- read_current_source_id treats both identically, and
    so must this node."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("", encoding="utf-8")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out

    assert code == 1
    assert out.strip() == ""


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not available")
def test_brief_names_created_and_updated_pages_and_carries_kind_and_retention(wiki_root, monkeypatch, capsys):
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")

    wr = _seed_current_source(wiki_root, "042-onnx-runtime-notes.txt")
    wr.current_kind_file.write_text(json.dumps({"kind": "article"}), encoding="utf-8")

    # Simulate weave's edits: modify the tracked index page, add a new one
    # named after what it's actually about (per the task: a page's NAME is
    # the signal that lets a reader who wasn't watching judge the source-vs-
    # entity ratio -- e.g. a tool-named page tells a different story than an
    # update to an existing argument page).
    (wr.wiki_dir / "index.md").write_text("---\ntitle: Index\ntype: index\n---\n\nmore.\n", encoding="utf-8")
    (wr.wiki_dir / "onnx-runtime.md").write_text("# ONNX Runtime\n", encoding="utf-8")

    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()

    assert code == 0
    payload = json.loads(out)
    brief = payload["review_brief"]

    assert "042-onnx-runtime-notes.txt" in brief
    assert "kind: article" in brief
    assert "segment 1/1" in brief
    assert "Pages created (1): onnx-runtime.md" in brief
    assert "Pages updated (1): index.md" in brief
    assert "Retention check: ok" in brief


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not available")
def test_brief_reflects_segment_position_when_segmented(wiki_root, monkeypatch, capsys):
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")

    wr = _seed_current_source(wiki_root, "big-transcript.txt")
    wr.current_segment_file.write_text(json.dumps({"index": 2, "total": 5}), encoding="utf-8")
    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "segment 2/5" in payload["review_brief"]


def test_kind_falls_back_to_source_guess_when_current_kind_file_absent(wiki_root, monkeypatch, capsys):
    """detect_kind never ran (or its breadcrumb is missing) -- same
    best-effort fallback budget.py/commit.py already use, not a fail-loud
    condition (kind is a label, not the core judgment-call content)."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "kind: article" in payload["review_brief"]  # read_source_kind's documented default


def test_missing_retention_flag_reports_unknown_not_fabricated_ok(wiki_root, monkeypatch, capsys):
    """If RETENTION_FLAG never reached this node (e.g. called outside the
    full pipeline), report that honestly -- never silently claim 'ok'."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    monkeypatch.delenv("RETENTION_FLAG", raising=False)

    code = review_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "unknown" in payload["review_brief"]
    assert "Retention check: ok" not in payload["review_brief"]


def test_no_git_available_reports_none_pages_not_a_crash(wiki_root, monkeypatch, capsys):
    """No .git at all (mirrors count_pages_touched's existing, already-tested
    graceful degradation elsewhere in this pipeline) -- pages are honestly
    reported as none, never fabricated as a fake count."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "Pages created (0): none" in payload["review_brief"]
    assert "Pages updated (0): none" in payload["review_brief"]


@pytest.mark.skipif(__import__("shutil").which("git") is None, reason="git not available")
def test_many_created_pages_truncated_with_more_count(wiki_root, monkeypatch, capsys):
    """Compact by design (task: 'this goes into a prompt, not a log file') --
    long page-name lists are truncated with a '+N more' tail rather than
    dumped in full."""
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")

    wr = _seed_current_source(wiki_root, "s1.txt")
    for i in range(12):
        (wr.wiki_dir / f"page-{i:02d}.md").write_text(f"# Page {i}\n", encoding="utf-8")
    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["review_brief"]
    assert "Pages created (12):" in brief
    assert "+4 more" in brief


def test_output_is_exactly_one_json_line_on_success(wiki_root, monkeypatch, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    monkeypatch.setenv("RETENTION_FLAG", "ok")
    review_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1


def test_writes_review_brief_file_stamped_with_source_id(wiki_root, monkeypatch, capsys):
    """THE delivery fix: review_gate's actual consumer (ProxyInterviewer)
    never sees $review_brief / stdout -- it reads review_brief_file from
    disk instead. Stamped with source_id so a stale read (wrong source) is
    detectable, never silently trusted."""
    wr = _seed_current_source(wiki_root, "042-onnx-runtime-notes.txt")
    monkeypatch.setenv("RETENTION_FLAG", "ok")

    code = review_brief.main(["--wiki-root", str(wr.root)])
    stdout_payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert wr.review_brief_file.is_file()
    file_payload = json.loads(wr.review_brief_file.read_text(encoding="utf-8"))
    assert file_payload["source_id"] == "042-onnx-runtime-notes.txt"
    assert file_payload["stage"] == "review_gate"
    # Same brief text on disk as on stdout -- one computation, two delivery
    # channels (see review_brief.py's main()).
    assert file_payload["brief"] == stdout_payload["review_brief"]


def test_no_review_brief_file_written_on_fail_loud_path(wiki_root, capsys):
    """No current source -> no real brief to write -- must not leave a
    stale/empty review-brief.json behind for a proxy to (wrongly) trust."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = review_brief.main(["--wiki-root", str(wr.root)])
    capsys.readouterr()

    assert code == 1
    assert not wr.review_brief_file.is_file()
