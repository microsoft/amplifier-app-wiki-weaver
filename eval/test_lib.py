"""Frontmatter, wikilinks, ledger I/O -- the shared library primitives every
subcommand depends on."""

from __future__ import annotations

import pytest

from wiki_weaver.lib import (
    LedgerCorruptError,
    WikiRoot,
    already_ledgered,
    atomic_append_jsonl,
    atomic_write_text,
    count_pages_touched_detail,
    extract_wikilinks,
    find_duplicate_sections_in_page,
    git_changed_wiki_files_detail,
    ledgered_segment_indices,
    ledgered_source_ids,
    parse_frontmatter,
    read_explicit_kind,
    read_ledger,
    read_source_kind,
    summarize_ledger_touches,
)

FRONTMATTER_SAMPLE = """---
title: Retrieval-Augmented Generation (RAG)
type: concept
sources: [1]
last_updated: 2026-07-25
confidence: 0.6
---

# Retrieval-Augmented Generation (RAG)

Body text with a [[llm-wiki-pattern|LLM Wiki Pattern]] link and a
[[andrej-karpathy]] bare link.
"""


def test_parse_frontmatter_scalars_and_lists():
    meta, body = parse_frontmatter(FRONTMATTER_SAMPLE)
    assert meta["title"] == "Retrieval-Augmented Generation (RAG)"
    assert meta["type"] == "concept"
    assert meta["sources"] == [1]
    assert meta["last_updated"] == "2026-07-25"
    assert meta["confidence"] == 0.6
    assert "# Retrieval-Augmented Generation (RAG)" in body


def test_parse_frontmatter_absent_returns_whole_text_as_body():
    meta, body = parse_frontmatter("just plain text\nno frontmatter here\n")
    assert meta == {}
    assert "just plain text" in body


def test_extract_wikilinks_handles_piped_and_bare_links():
    _, body = parse_frontmatter(FRONTMATTER_SAMPLE)
    assert extract_wikilinks(body) == ["llm-wiki-pattern", "andrej-karpathy"]


def test_atomic_write_text_then_read(tmp_path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    # No leftover temp files
    assert list(target.parent.glob(".*")) == []


def test_atomic_append_jsonl_appends_multiple_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    atomic_append_jsonl(path, {"source_id": "a.txt"})
    atomic_append_jsonl(path, {"source_id": "b.txt"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '"source_id": "a.txt"' in lines[0]
    assert '"source_id": "b.txt"' in lines[1]


def test_read_ledger_missing_file_returns_empty(tmp_path):
    assert read_ledger(tmp_path / "nope.jsonl") == []


def test_read_ledger_corrupt_line_raises(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"source_id": "a.txt"}\nnot json at all\n', encoding="utf-8")
    with pytest.raises(LedgerCorruptError):
        read_ledger(path)


def test_ledgered_source_ids_and_already_ledgered(tmp_path):
    path = tmp_path / "ledger.jsonl"
    atomic_append_jsonl(path, {"source_id": "a.txt", "decision": "accept"})
    atomic_append_jsonl(path, {"source_id": "b.txt", "decision": "skip"})
    assert ledgered_source_ids(path) == {"a.txt", "b.txt"}
    assert already_ledgered(path, "a.txt") is True
    assert already_ledgered(path, "c.txt") is False


def test_wiki_root_paths(tmp_path):
    wr = WikiRoot(tmp_path)
    assert wr.sources_dir == tmp_path / "sources"
    assert wr.ledger_path == tmp_path / "ledger.jsonl"
    assert wr.current_source_file == tmp_path / ".ai" / "current_source.txt"
    assert wr.current_kind_file == tmp_path / ".ai" / "current_kind.json"
    assert wr.segments_manifest_file == tmp_path / ".ai" / "segments.json"
    assert wr.current_segment_content_file == tmp_path / ".ai" / "current-segment-content.md"


# --- DESIGN.md §5 segment-aware ledger semantics ---


def test_legacy_ledger_lines_without_segment_fields_behave_as_before(tmp_path):
    """A pre-segmentation ledger line (no segment_index/segment_total) must
    be treated as segment 1 of 1 -- old ledgers, and callers that never run
    segment_source (the eval arms), behave exactly as before."""
    path = tmp_path / "ledger.jsonl"
    atomic_append_jsonl(path, {"source_id": "a.txt", "decision": "accept"})
    assert ledgered_source_ids(path) == {"a.txt"}
    assert already_ledgered(path, "a.txt") is True
    assert ledgered_segment_indices(path, "a.txt") == {1}


def test_source_with_only_some_segments_ledgered_is_not_fully_done(tmp_path):
    path = tmp_path / "ledger.jsonl"
    atomic_append_jsonl(path, {"source_id": "big.md", "decision": "accept", "segment_index": 1, "segment_total": 3})
    atomic_append_jsonl(path, {"source_id": "big.md", "decision": "accept", "segment_index": 2, "segment_total": 3})

    # Not fully ledgered yet -- segment 3 is still pending.
    assert ledgered_source_ids(path) == set()
    assert ledgered_segment_indices(path, "big.md") == {1, 2}
    assert already_ledgered(path, "big.md", segment_index=1) is True
    assert already_ledgered(path, "big.md", segment_index=3) is False

    atomic_append_jsonl(path, {"source_id": "big.md", "decision": "skip", "segment_index": 3, "segment_total": 3})
    assert ledgered_source_ids(path) == {"big.md"}  # NOW fully done (mixed accept/skip is fine)


def test_read_explicit_kind_returns_none_when_absent(tmp_path):
    path = tmp_path / "s1.txt"
    path.write_text("just plain prose, no kind marker\n", encoding="utf-8")
    assert read_explicit_kind(path) is None
    assert read_source_kind(path) == "article"  # best-effort wrapper still defaults


def test_read_explicit_kind_reads_leading_line(tmp_path):
    path = tmp_path / "s1.txt"
    path.write_text("kind: meeting\n\nbody text\n", encoding="utf-8")
    assert read_explicit_kind(path) == "meeting"


# --- touches_per_source metric: created/updated split + run-level summary ---


def _git(root, *args: str) -> None:
    import subprocess

    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _init_repo_with_one_committed_page(tmp_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "index.md").write_text("---\ntitle: Index\ntype: index\n---\n\n# Index\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return wiki_dir


def test_git_changed_wiki_files_detail_splits_created_from_updated(tmp_path):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_dir = _init_repo_with_one_committed_page(tmp_path)

    # Modify the tracked page (updated) and add a brand-new one (created).
    (wiki_dir / "index.md").write_text("---\ntitle: Index\ntype: index\n---\n\n# Index\n\nmore.\n", encoding="utf-8")
    (wiki_dir / "new-page.md").write_text("# New Page\n", encoding="utf-8")

    created, updated = git_changed_wiki_files_detail(tmp_path)
    assert created == {"new-page.md"}
    assert updated == {"index.md"}


def test_git_changed_wiki_files_detail_staged_add_counts_as_created(tmp_path):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_dir = _init_repo_with_one_committed_page(tmp_path)
    (wiki_dir / "new-page.md").write_text("# New Page\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")  # stage it, don't commit

    created, updated = git_changed_wiki_files_detail(tmp_path)
    assert created == {"new-page.md"}
    assert updated == set()


def test_count_pages_touched_detail_totals_match_created_plus_updated(tmp_path):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_dir = _init_repo_with_one_committed_page(tmp_path)
    (wiki_dir / "index.md").write_text("---\ntitle: Index\n---\n\nchanged\n", encoding="utf-8")
    (wiki_dir / "new-page.md").write_text("# New Page\n", encoding="utf-8")

    detail = count_pages_touched_detail(tmp_path, wiki_dir)
    assert detail == {"created": 1, "updated": 1, "total": 2}


def test_summarize_ledger_touches_no_ledger_returns_zeroed_summary(tmp_path):
    summary = summarize_ledger_touches(tmp_path / "ledger.jsonl")
    assert summary == {"sources": 0, "mean_touched": 0.0, "total_created": 0, "total_updated": 0}


def test_summarize_ledger_touches_means_only_accept_rows(tmp_path):
    path = tmp_path / "ledger.jsonl"
    atomic_append_jsonl(
        path, {"source_id": "a.txt", "decision": "accept", "pages_touched": 3, "pages_created": 2, "pages_updated": 1}
    )
    atomic_append_jsonl(
        path, {"source_id": "b.txt", "decision": "accept", "pages_touched": 1, "pages_created": 0, "pages_updated": 1}
    )
    # A skip row must not count toward the mean or the totals.
    atomic_append_jsonl(path, {"source_id": "c.txt", "decision": "skip"})

    summary = summarize_ledger_touches(path)
    assert summary == {"sources": 2, "mean_touched": 2.0, "total_created": 2, "total_updated": 2}


def test_segment_total_takes_the_largest_claim_not_the_last_row(tmp_path):
    """REGRESSION: rows for one source can disagree about segment_total the
    moment a source is ever re-split. Taking the LAST row's value silently
    shrinks the completion bar -- rows (1,2,3 of 4) followed by a (1 of 1) row
    made the source report FULLY DONE on the strength of segment 1 alone,
    retiring segment 4 without it ever being woven.

    max() can only make a source look LESS complete, never more, which is the
    safe direction for a gate whose job is to keep work pending.
    """
    import json

    from wiki_weaver.lib import ledgered_source_ids

    path = tmp_path / "ledger.jsonl"

    def write(*specs):
        path.write_text(
            "".join(
                json.dumps({"source_id": s, "decision": "accept", "segment_index": i, "segment_total": t}) + "\n"
                for s, i, t in specs
            ),
            encoding="utf-8",
        )

    write(("x.md", 1, 4), ("x.md", 2, 4), ("x.md", 3, 4))
    assert ledgered_source_ids(path) == set()  # segment 4 still pending

    write(("x.md", 1, 4), ("x.md", 2, 4), ("x.md", 3, 4), ("x.md", 1, 1))
    assert ledgered_source_ids(path) == set()  # a (1 of 1) row must not retire segment 4

    write(("y.md", 1, 2), ("y.md", 2, 2))
    assert ledgered_source_ids(path) == {"y.md"}  # genuinely complete still reports done


# ---------------------------------------------------------------------------
# find_duplicate_sections_in_page -- the duplicate-section detector (see
# docs/KNOWN_ISSUES.md's duplicate-section incident and the function's own
# docstring for the mechanism). Proven in BOTH directions per the task's
# own standard: it must fire on a page with real duplicates and stay QUIET
# on a page with none -- a checker that always fires and one that never
# fires are the same defect.
# ---------------------------------------------------------------------------


def test_find_duplicate_sections_quiet_on_clean_page():
    """NEGATIVE proof: a normal page with unique headings, including ones
    that share words or are substrings of one another, must not fire."""
    text = (
        "---\ntitle: Clean Page\ntype: concept\n---\n\n"
        "# Clean Page\n\n"
        "## Roadmap Revised to 3 Outcomes (2026-08-10)\n\n"
        "Some text.\n\n"
        "## Roadmap Revised to 4 Outcomes (2026-08-17)\n\n"
        "Different text -- deliberately similar heading, must NOT be treated\n"
        "as a duplicate of the one above: it is a different, later revision.\n\n"
        "### Roadmap Revised to 3 Outcomes (2026-08-10)\n\n"
        "A level-3 heading sharing text with the level-2 heading above --\n"
        "must not be conflated with it (different heading level).\n\n"
        "## Related\n\n"
        "- [[other-page|Other Page]]\n"
    )
    assert find_duplicate_sections_in_page(text) == {}


def test_find_duplicate_sections_fires_on_exact_duplicate():
    """POSITIVE proof: the same ## heading appearing twice, byte-identical
    body both times -- the simplest real case (6 of the 10 fixture
    pairs were exactly this shape)."""
    heading = "## Orchard Design for Signal Deck \u2014 Inbox-Based Architecture (2026-08-11)"
    text = (
        "---\ntitle: Signal Deck\ntype: source\n---\n\n# Signal Deck\n\n"
        f"{heading}\n\nRenata Ossovski described a detailed workflow. (some-source.md)\n\n"
        "## Some Other Section\n\nUnrelated content.\n\n"
        f"{heading}\n\nRenata Ossovski described a detailed workflow. (some-source.md)\n"
    )
    dupes = find_duplicate_sections_in_page(text)
    assert dupes == {heading: 2}


def test_find_duplicate_sections_fires_on_reworded_duplicate():
    """POSITIVE proof: same heading, DIFFERENT body -- the person-page
    shape (two independent write-ups of the same source). Header-only
    matching must still catch this; body similarity is not the signal."""
    heading = "## Crew Weekly Planning \u2014 2026-06-19"
    text = (
        "---\ntitle: Renata Ossovski\ntype: person\n---\n\n# Renata Ossovski\n\n"
        f"{heading}\n\nIn a 29-minute weekly planning meeting, Renata committed to X.\n"
        "(some-meeting.transcript.md)\n\n"
        f"{heading}\n\nIn the weekly planning meeting, Renata made several commitments, "
        "including Y.\n(some-meeting.transcript.md)\n"
    )
    dupes = find_duplicate_sections_in_page(text)
    assert dupes == {heading: 2}


def test_find_duplicate_sections_handles_spaces_em_dash_and_unicode():
    """Headers and citation filenames in this corpus routinely contain
    spaces, em dashes, and non-ASCII text (curly quotes, accents). The
    detector must never rely on whitespace-splitting or ASCII-only
    matching -- both failure classes have shipped in this project before
    (see the function's docstring). Also covers a citation filename WITH
    SPACES, to guard against a checker that (mis)treats a citation's
    internal spaces as section boundaries."""
    heading = "## R\u00e9sum\u00e9 Review \u2014 D\u00e9j\u00e0 Vu \u2014 \u201cQuoted\u201d Title"
    text = (
        "---\ntitle: X\ntype: concept\n---\n\n# X\n\n"
        f"{heading}\n\nBody one. (Signal Deck Workstream chat pulled 2026-08-12.md)\n\n"
        f"{heading}\n\nBody two, reworded. (Signal Deck Workstream chat pulled 2026-08-12.md)\n"
    )
    dupes = find_duplicate_sections_in_page(text)
    assert dupes == {heading: 2}


def test_find_duplicate_sections_ignores_level3_and_trailing_whitespace_variants():
    """A level-3 heading must never be conflated with a level-2 heading of
    similar text (## vs ###), and trailing whitespace differences alone
    are stripped before comparison (a heading with a trailing space is
    still the same heading)."""
    text = (
        "## Same Heading   \n\nBody A\n\n"
        "## Same Heading\n\nBody B\n\n"
        "### Same Heading\n\nBody C (level 3, must not count)\n"
    )
    dupes = find_duplicate_sections_in_page(text)
    assert dupes == {"## Same Heading": 2}


def test_find_duplicate_sections_fixture_fires_ten_times():
    """Regression proof against the ACTUAL evidence: the fixture page
    excerpt (10 duplicated headers, verified by direct inspection of the
    eval wiki) must be caught in full, with no over-count and no
    under-count."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "duplicate-sections-excerpt.md"
    text = fixture.read_text(encoding="utf-8")
    dupes = find_duplicate_sections_in_page(text)
    assert len(dupes) == 10
    assert all(count == 2 for count in dupes.values())
