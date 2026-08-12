"""curate_brief: the self-contained brief for curate_gate -- source gist +
what this wiki already covers. FAIL LOUD (never fabricate) when the
current-source breadcrumb is missing/unreadable -- same contract
review_brief.py/takeaways_brief.py already enforce."""

from __future__ import annotations

import json

from wiki_weaver.ingest import curate_brief
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt", content: str = "Sample source content.\n") -> WikiRoot:
    wiki_root.add_source(name, content)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def test_missing_current_source_fails_loud_no_fabricated_brief(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""
    assert "current_source" in err


def test_brief_names_source_and_states_default_toward_accept(wiki_root, capsys):
    wr = _seed_current_source(
        wiki_root,
        "042-onnx-runtime-notes.txt",
        "This article explains how the ONNX Runtime accelerates model inference across hardware backends.\n",
    )

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["curate_brief"]
    assert "042-onnx-runtime-notes.txt" in brief
    assert "ONNX Runtime" in brief
    assert "Default toward ACCEPTING" in brief
    assert "declined" in brief.lower()


def test_empty_wiki_reports_honestly_not_fabricated(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "currently empty" in payload["curate_brief"]


def test_wiki_about_text_lists_existing_pages(wiki_root, capsys):
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\nAn overview of the topic.\n")
    wiki_root.add_page("onnx-runtime.md", "---\ntitle: ONNX Runtime\ntype: concept\n---\n\nWhat it is.\n")
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["curate_brief"]
    assert "2 page(s)" in brief
    assert "Index" in brief
    assert "ONNX Runtime" in brief


def test_writes_curate_brief_file_stamped_with_source_id(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "042-onnx-runtime-notes.txt")

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    stdout_payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert wr.curate_brief_file.is_file()
    file_payload = json.loads(wr.curate_brief_file.read_text(encoding="utf-8"))
    assert file_payload["source_id"] == "042-onnx-runtime-notes.txt"
    assert file_payload["stage"] == "curate_gate"
    assert file_payload["brief"] == stdout_payload["curate_brief"]


def test_no_curate_brief_file_written_on_fail_loud_path(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    capsys.readouterr()

    assert code == 1
    assert not wr.curate_brief_file.is_file()


def test_output_is_exactly_one_json_line_on_success(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    curate_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1


def test_missing_source_file_gists_honestly_from_empty_content(wiki_root, capsys):
    """current_source.txt names a source id, but the underlying file under
    sources/ is gone -- must not crash, must still produce a brief (an
    empty-content gist), never fabricate content that isn't there."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("ghost.txt", encoding="utf-8")

    code = curate_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "ghost.txt" in payload["curate_brief"]
