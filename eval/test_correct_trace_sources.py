"""wiki_weaver.correct.trace_sources: union of frontmatter `sources:` and
inline NNN-Name.md citations across every affected page."""

from __future__ import annotations

import json

from wiki_weaver.correct import trace_sources


def test_traces_frontmatter_and_inline_citations(wiki_root):
    # extract_source_citations (lib.py) matches inline "NNN-Name.md" refs
    # specifically -- the frontmatter `sources:` field carries the raw
    # (possibly non-.md) source id separately, so this page exercises BOTH
    # provenance mechanisms with the extension each one actually expects.
    wiki_root.add_page(
        "migration-history.md",
        "---\ntitle: Migration History\ntype: article\nsources: [001-standup-notes.txt]\n---\n"
        "Bob led the migration. See also 002-followup-email.md for details.\n",
    )
    pages_path = wiki_root.root / "affected-pages.json"
    pages_path.write_text(json.dumps({"pages": ["migration-history.md"]}), encoding="utf-8")
    out_path = wiki_root.root / ".ai" / "affected-sources.json"

    code = trace_sources.main(
        ["--wiki-root", str(wiki_root.root), "--pages-file", str(pages_path), "--out", str(out_path)]
    )

    assert code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert set(data["sources"]) == {"001-standup-notes.txt", "002-followup-email.md"}


def test_page_with_no_provenance_yields_empty_sources(wiki_root):
    wiki_root.add_page("no-provenance.md", "---\ntitle: X\ntype: article\n---\nNo citations here at all.\n")
    pages_path = wiki_root.root / "affected-pages.json"
    pages_path.write_text(json.dumps({"pages": ["no-provenance.md"]}), encoding="utf-8")
    out_path = wiki_root.root / "affected-sources.json"

    code = trace_sources.main(
        ["--wiki-root", str(wiki_root.root), "--pages-file", str(pages_path), "--out", str(out_path)]
    )

    assert code == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["sources"] == []


def test_nonexistent_page_is_skipped_not_fabricated(wiki_root):
    pages_path = wiki_root.root / "affected-pages.json"
    pages_path.write_text(json.dumps({"pages": ["does-not-exist.md"]}), encoding="utf-8")
    out_path = wiki_root.root / "affected-sources.json"

    code = trace_sources.main(
        ["--wiki-root", str(wiki_root.root), "--pages-file", str(pages_path), "--out", str(out_path)]
    )

    assert code == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["sources"] == []


def test_missing_pages_file_refuses(wiki_root, capsys):
    out_path = wiki_root.root / "affected-sources.json"

    code = trace_sources.main(
        [
            "--wiki-root",
            str(wiki_root.root),
            "--pages-file",
            str(wiki_root.root / "nope.json"),
            "--out",
            str(out_path),
        ]
    )

    assert code == 1
    assert not out_path.exists()
    assert "does not exist" in capsys.readouterr().err


def test_malformed_pages_file_refuses(wiki_root, capsys):
    pages_path = wiki_root.root / "affected-pages.json"
    pages_path.write_text("not json {{{", encoding="utf-8")
    out_path = wiki_root.root / "affected-sources.json"

    code = trace_sources.main(
        ["--wiki-root", str(wiki_root.root), "--pages-file", str(pages_path), "--out", str(out_path)]
    )

    assert code == 1
    assert not out_path.exists()
    assert "could not be read/parsed" in capsys.readouterr().err
