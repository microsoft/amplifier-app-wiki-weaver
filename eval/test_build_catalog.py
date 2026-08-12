"""build_catalog: the index-first catalog weave reads to know what EXISTS.

See evals/compounding/PRECOMMIT-INDEX.md for the full rationale. Covers the
compact per-page entries (title + one-line summary, no bodies), stable
ordering, the explicit (never-silent) truncation ceiling, the CLI's atomic
write + informational stdout line, and two REAL-wiki scale checks (14 pages,
615 pages) against the actual growth-eval snapshots on disk.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wiki_weaver.ingest import build_catalog
from wiki_weaver.lib import WikiRoot, list_wiki_pages

PAGE_WITH_DESCRIPTION = """---
title: Quokka Habitat
description: Where quokkas live and why Rottnest Island suits them.
type: concept
sources: [1, 2, 3]
---

# Quokka Habitat

Quokkas live on Rottnest Island. This body text must never leak into the \
catalog verbatim -- only the one-line summary above should appear.
"""

PAGE_WITHOUT_DESCRIPTION = """---
title: Quokka Diet
type: concept
sources: 7
---

# Quokka Diet

Quokkas eat native vegetation and are herbivorous browsers. A second \
sentence that should never appear in the one-line summary.
"""

PAGE_NO_FRONTMATTER = """# Untitled Notes

Some content with no frontmatter at all.
"""

INDEX_PAGE = """---
title: Index
type: index
---

# Index
"""

REAL_N110_WIKI = Path(
    "/home/bkrabach/dev/wiki-weaver-improvements/.amplifier/evaluation/wiki-weaver/"
    "growth-20260726T164439Z/snapshots/N110/wiki"
)
REAL_N110_SPLIT_WIKI = Path(
    "/home/bkrabach/dev/wiki-weaver-improvements/.amplifier/evaluation/wiki-weaver/"
    "growth-20260726T164439Z/snapshots/N110-split/wiki"
)


def _seed(wiki_root) -> WikiRoot:
    wiki_root.add_page("quokka-habitat.md", PAGE_WITH_DESCRIPTION)
    wiki_root.add_page("quokka-diet.md", PAGE_WITHOUT_DESCRIPTION)
    wiki_root.add_page("untitled-notes.md", PAGE_NO_FRONTMATTER)
    wiki_root.add_page("index.md", INDEX_PAGE)
    return WikiRoot(wiki_root.root)


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------


def test_catalog_lists_every_page(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    ids = {e["id"] for e in entries}
    assert ids == {"quokka-habitat.md", "quokka-diet.md", "untitled-notes.md", "index.md"}


def test_catalog_prefers_frontmatter_description(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    assert entries["quokka-habitat.md"]["summary"] == "Where quokkas live and why Rottnest Island suits them."


def test_catalog_falls_back_to_first_sentence_when_no_description(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    # First sentence only -- the second sentence in the body must be excluded.
    assert entries["quokka-diet.md"]["summary"] == "Quokkas eat native vegetation and are herbivorous browsers."


def test_catalog_handles_page_with_no_frontmatter(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    entry = entries["untitled-notes.md"]
    assert entry["title"] == "untitled-notes"  # falls back to filename stem
    assert entry["summary"] == "Some content with no frontmatter at all."


def test_catalog_order_matches_list_wiki_pages(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    expected_order = [p.name for p in list_wiki_pages(wr.wiki_dir)]
    assert [e["id"] for e in entries] == expected_order


# ---------------------------------------------------------------------------
# Source counts -- the "how established is this page" half of the
# pre-write steering fix. quokka-habitat.md has a real list (`sources: [1, 2,
# 3]`), quokka-diet.md has a bare scalar (`sources: 7`, the same
# non-list-normalization refresh_page_index already applies), and the
# remaining two seeded pages have no `sources:` field at all.
# ---------------------------------------------------------------------------


def test_catalog_entry_counts_a_list_valued_sources_field(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    assert entries["quokka-habitat.md"]["source_count"] == 3


def test_catalog_entry_counts_a_scalar_sources_field_as_one(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    assert entries["quokka-diet.md"]["source_count"] == 1


def test_catalog_entry_defaults_source_count_to_zero_when_field_absent(wiki_root):
    wr = _seed(wiki_root)
    entries = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    assert entries["untitled-notes.md"]["source_count"] == 0  # no frontmatter at all
    assert entries["index.md"]["source_count"] == 0  # frontmatter present, no `sources:` field


# ---------------------------------------------------------------------------
# Rendering: no bodies leak, stable text, explicit truncation
# ---------------------------------------------------------------------------


def test_render_catalog_never_includes_body_text(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    text = build_catalog.render_catalog(entries)
    # The second sentence of quokka-diet.md's body must never appear.
    assert "second sentence" not in text
    assert "This body text must never leak" not in text


def test_render_catalog_includes_filename_title_and_summary(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    text = build_catalog.render_catalog(entries)
    assert "quokka-habitat.md" in text
    assert "Quokka Habitat" in text
    assert "Where quokkas live and why Rottnest Island suits them." in text


def test_render_catalog_is_deterministic(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    assert build_catalog.render_catalog(entries) == build_catalog.render_catalog(entries)


def test_render_catalog_truncates_explicitly_when_over_budget():
    """Never silent: a small max_bytes must produce a visible TRUNCATED
    marker naming exactly how many pages were omitted."""
    entries = [{"id": f"page-{i}.md", "title": f"Page {i}", "summary": "x" * 50} for i in range(200)]
    text = build_catalog.render_catalog(entries, max_bytes=2000)
    assert "TRUNCATED" in text
    assert "200 page(s) exist" in text  # header still reports the TRUE total
    # Every included entry must be a real prefix of the full ordered list --
    # never silently reordered or cherry-picked.
    full_text = build_catalog.render_catalog(entries, max_bytes=1_000_000)
    assert "TRUNCATED" not in full_text
    for i in range(200):
        assert f"page-{i}.md" in full_text


def test_render_catalog_no_truncation_marker_when_under_budget(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    text = build_catalog.render_catalog(entries)
    assert "TRUNCATED" not in text


# ---------------------------------------------------------------------------
# Predicted merge targets -- the pre-write steering fix. build_predicted_targets
# reads retrieve_slice's already-computed BM25 ranking (.ai/current_slice.json);
# render_catalog renders it as a distinct, bounded, falsifiable-prediction
# section that survives even when the full inventory above it must truncate.
# ---------------------------------------------------------------------------


def _write_slice(wr: WikiRoot, bm25_hits: list[dict]) -> None:
    wr.ai_dir.mkdir(parents=True, exist_ok=True)
    wr.current_slice_file.write_text(
        json.dumps({"source_id": "s1.txt", "k": len(bm25_hits), "pages": [], "bm25_hits": bm25_hits}),
        encoding="utf-8",
    )


def test_build_predicted_targets_returns_empty_when_no_slice_file(wiki_root):
    wr = _seed(wiki_root)
    entries_by_id = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    assert build_catalog.build_predicted_targets(wr, entries_by_id) == []


def test_build_predicted_targets_matches_slice_hits_to_catalog_entries(wiki_root):
    wr = _seed(wiki_root)
    entries_by_id = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    _write_slice(wr, [{"page": "wiki/quokka-habitat.md", "score": 4.2}, {"page": "wiki/quokka-diet.md", "score": 1.1}])

    targets = build_catalog.build_predicted_targets(wr, entries_by_id)

    assert [t["id"] for t in targets] == ["quokka-habitat.md", "quokka-diet.md"]  # BM25 order preserved
    assert targets[0]["source_count"] == 3  # the SAME entry this catalog already built -- not re-read from disk


def test_build_predicted_targets_is_bounded_by_max_predicted_targets(wiki_root):
    wr = _seed(wiki_root)
    entries_by_id = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    # Far more hits than MAX_PREDICTED_TARGETS -- only the real pages resolve
    # to an entry, but the CAP itself must hold regardless of hit count.
    hits = [{"page": "wiki/quokka-habitat.md", "score": 9.0}] * (build_catalog.MAX_PREDICTED_TARGETS + 20)
    _write_slice(wr, hits)

    targets = build_catalog.build_predicted_targets(wr, entries_by_id)

    assert len(targets) <= build_catalog.MAX_PREDICTED_TARGETS


def test_build_predicted_targets_skips_hits_with_no_matching_entry(wiki_root):
    wr = _seed(wiki_root)
    entries_by_id = {e["id"]: e for e in build_catalog.build_catalog_entries(wr.wiki_dir)}
    _write_slice(wr, [{"page": "wiki/does-not-exist.md", "score": 5.0}, {"page": "wiki/quokka-diet.md", "score": 1.0}])

    targets = build_catalog.build_predicted_targets(wr, entries_by_id)

    assert [t["id"] for t in targets] == ["quokka-diet.md"]


def test_render_catalog_includes_predicted_targets_section_when_given(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    entries_by_id = {e["id"]: e for e in entries}
    predicted = [entries_by_id["quokka-habitat.md"]]

    text = build_catalog.render_catalog(entries, predicted_targets=predicted)

    assert "Predicted merge targets" in text
    # Falsifiable-prediction framing, not a directive -- confirm/reject language present.
    assert "confirm" in text.lower() or "reject" in text.lower()
    assert "quokka-habitat.md" in text


def test_render_catalog_omits_predicted_section_when_no_targets_given(wiki_root):
    wr = _seed(wiki_root)
    entries = build_catalog.build_catalog_entries(wr.wiki_dir)
    text = build_catalog.render_catalog(entries)
    assert "Predicted merge targets" not in text


def test_render_catalog_reserves_predicted_targets_before_truncating_inventory():
    """THE priority rule: if the catalog would overflow, the FULL INVENTORY
    truncates first -- the predicted-targets section is reserved room ahead
    of the inventory's own truncation loop, so it survives even when the
    inventory itself does not reach that far."""
    entries = [{"id": f"page-{i}.md", "title": f"Page {i}", "summary": "x" * 50, "source_count": i} for i in range(200)]
    predicted = [entries[199]]  # the LAST page in inventory order -- the first thing a tight budget would cut
    max_bytes = 2000

    without_predictions = build_catalog.render_catalog(entries, max_bytes=max_bytes)
    assert "page-199.md" not in without_predictions  # sanity: confirms the inventory alone truncates before it

    with_predictions = build_catalog.render_catalog(entries, max_bytes=max_bytes, predicted_targets=predicted)
    assert "TRUNCATED" in with_predictions  # inventory is still bounded and still truncates
    assert "page-199.md" in with_predictions  # but the prediction survives via its own reserved section


# ---------------------------------------------------------------------------
# CLI: atomic write + informational stdout
# ---------------------------------------------------------------------------


def test_main_writes_catalog_file_atomically(wiki_root, capsys):
    wr = _seed(wiki_root)
    code = build_catalog.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert wr.current_catalog_file.is_file()
    text = wr.current_catalog_file.read_text(encoding="utf-8")
    assert "quokka-habitat.md" in text
    assert "index.md" in text


def test_main_stdout_reports_page_count_and_byte_size(wiki_root, capsys):
    wr = _seed(wiki_root)
    build_catalog.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert "4 page(s)" in out
    size = wr.current_catalog_file.stat().st_size
    assert str(size) in out


def test_main_on_empty_wiki_writes_header_only(wiki_root):
    wr = WikiRoot(wiki_root.root)
    code = build_catalog.main(["--wiki-root", str(wr.root)])
    assert code == 0
    text = wr.current_catalog_file.read_text(encoding="utf-8")
    assert "0 page(s)" in text
    assert "TRUNCATED" not in text


# ---------------------------------------------------------------------------
# REAL-wiki scale checks (per task instruction: read-only, never write under
# .amplifier/evaluation/ -- these call the pure library functions directly,
# never build_catalog.main(), which is the only thing that writes files).
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_N110_WIKI.is_dir(), reason="real N110 growth-eval snapshot not present")
def test_real_n110_wiki_catalog_is_small_and_complete():
    entries = build_catalog.build_catalog_entries(REAL_N110_WIKI)
    real_page_count = len(list_wiki_pages(REAL_N110_WIKI))
    assert len(entries) == real_page_count == 14

    text = build_catalog.render_catalog(entries)
    size = len(text.encode("utf-8"))

    # No truncation should be needed at this scale (measured ~3.2 KB).
    assert "TRUNCATED" not in text
    assert size < 10_000

    # No page bodies leaked -- every page's full content is far larger than
    # the whole rendered catalog, so the catalog can only contain summaries.
    for page_path in list_wiki_pages(REAL_N110_WIKI):
        assert page_path.name in text


@pytest.mark.skipif(not REAL_N110_SPLIT_WIKI.is_dir(), reason="real N110-split growth-eval snapshot not present")
def test_real_n110_split_wiki_catalog_stays_bounded_and_truncates_explicitly_if_needed():
    entries = build_catalog.build_catalog_entries(REAL_N110_SPLIT_WIKI)
    real_page_count = len(list_wiki_pages(REAL_N110_SPLIT_WIKI))
    assert len(entries) == real_page_count == 615

    text = build_catalog.render_catalog(entries)
    size = len(text.encode("utf-8"))

    # Sane at 600+ pages: bounded near the ceiling, with a VISIBLE marker if
    # (and only if) it was actually truncated -- never a silent drop.
    assert size <= build_catalog.MAX_CATALOG_BYTES + 500
    if size > build_catalog.MAX_CATALOG_BYTES:
        assert "TRUNCATED" in text
    assert f"{real_page_count} page(s) exist" in text  # true total always reported, even when truncated
