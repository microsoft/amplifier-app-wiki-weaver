"""wiki_weaver.synthesize.write_gap_page -- deterministic frontmatter +
slug assembly. The box/tool split: the LLM only ever wrote prose."""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir, find_structural_issues, parse_frontmatter
from wiki_weaver.synthesize import write_gap_page
from wiki_weaver.synthesize.write_gap_page import link_gap_page, slugify

INDEX_PAGE = """---
title: Index
type: index
---

# Index

- [[overview|Overview]]
"""


def test_slugify_basic():
    assert slugify("token economics") == "token-economics"
    assert slugify("  Weird!! Spacing__Here ") == "weird-spacing-here"
    assert slugify("") == "gap"


def test_writes_page_with_correct_frontmatter_and_cites_sources(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(
        json.dumps({"term": "token economics", "source_count": 4, "source_ids": ["s1.txt", "s2.txt"]}),
        encoding="utf-8",
    )
    wr.gap_answer_file.write_text(
        "The sources agree that token costs dominate deployment decisions.\n", encoding="utf-8"
    )

    code = write_gap_page.main(["--wiki-root", str(wr.root)])
    assert code == 0

    page_path = wr.wiki_dir / "token-economics.md"
    assert page_path.is_file()
    text = page_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    assert meta["title"] == "Token Economics"
    assert meta["type"] == "theme"
    assert meta["sources"] == ["s1.txt", "s2.txt"]
    assert "token costs dominate" in body


def test_missing_answer_file_fails_loud(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_ids": []}), encoding="utf-8")

    code = write_gap_page.main(["--wiki-root", str(wr.root)])
    assert code == 1
    assert not (wr.wiki_dir / "token-economics.md").exists()


def test_rerun_overwrites_the_same_page_not_a_duplicate(wiki_root):
    """Idempotency: a re-run (e.g. after a crash between write and
    validate/commit) overwrites the SAME file, never creates a second one."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_ids": ["s1.txt"]}), encoding="utf-8")
    wr.gap_answer_file.write_text("First draft.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    wr.gap_answer_file.write_text("Second, revised draft.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    pages = list(wr.wiki_dir.glob("token-economics*.md"))
    assert len(pages) == 1
    assert "Second, revised draft" in pages[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# ORPHAN FIX: the every-gap-declined incident -- validate's orphan check
# (lib.find_structural_issues) flags any non-nav page with zero incoming
# wikilinks. A freshly written theme page has none until something links to
# it; write_gap_page must add that link in the SAME step, so the check is a
# real check rather than a guaranteed first-pass failure.
# ---------------------------------------------------------------------------


def test_write_gap_page_links_new_page_from_index_so_it_is_not_orphaned(wiki_root):
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_page("index.md", INDEX_PAGE)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(
        json.dumps({"term": "token economics", "source_count": 4, "source_ids": ["s1.txt", "s2.txt"]}),
        encoding="utf-8",
    )
    wr.gap_answer_file.write_text(
        "The sources agree that token costs dominate deployment decisions.\n", encoding="utf-8"
    )

    code = write_gap_page.main(["--wiki-root", str(wr.root)])
    assert code == 0

    index_text = (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert "[[token-economics|Token Economics]]" in index_text

    # The check this whole fix exists to satisfy: the new page must not be
    # reported as an orphan once it is linked from index.md.
    issues = find_structural_issues(wr.wiki_dir)
    assert not any("orphan" in issue and "token-economics" in issue for issue in issues)


def test_write_gap_page_relink_on_retry_does_not_duplicate_entry(wiki_root):
    """A structural-validation retry re-runs write_gap_page for the SAME
    term (see pipeline/synthesize.dot's retry_bound -> answer_gap ->
    write_gap_page loop) -- the index.md link must appear exactly once,
    never accumulate one entry per attempt."""
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_page("index.md", INDEX_PAGE)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_ids": ["s1.txt"]}), encoding="utf-8")
    wr.gap_answer_file.write_text("First draft.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    wr.gap_answer_file.write_text("Retried, revised draft.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    index_text = (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert index_text.count("[[token-economics|") == 1


def test_write_gap_page_appends_second_theme_under_same_heading(wiki_root):
    """A second gap term, later in the same or a subsequent run, gets its
    own entry under the SAME '## Synthesized Themes' heading -- no second
    heading spawned."""
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_page("index.md", INDEX_PAGE)
    ensure_dir(wr.ai_dir)

    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_ids": ["s1.txt"]}), encoding="utf-8")
    wr.gap_answer_file.write_text("First theme.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    wr.current_gap_file.write_text(json.dumps({"term": "local inference", "source_ids": ["s2.txt"]}), encoding="utf-8")
    wr.gap_answer_file.write_text("Second theme.\n", encoding="utf-8")
    write_gap_page.main(["--wiki-root", str(wr.root)])

    index_text = (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert index_text.count("## Synthesized Themes") == 1
    assert "[[token-economics|Token Economics]]" in index_text
    assert "[[local-inference|Local Inference]]" in index_text


def test_link_gap_page_is_a_noop_without_index_md(tmp_path):
    """Best-effort: if index.md is somehow absent, linking is a silent
    no-op, never a crash -- linking is a quality improvement to the orphan
    check, not itself a new required precondition."""
    wiki_dir = tmp_path / "wiki"
    ensure_dir(wiki_dir)
    written = link_gap_page(wiki_dir, "token-economics", "Token Economics", "a synthesized theme")
    assert written is False
