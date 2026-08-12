"""retrieve_slice: byte-budgeted candidate selection (replaces the historical
fixed top-k default).

THE measured defect this replaces (see docs/DESIGN.md and
lib.build_slice's module-level comment): with a FIXED count, touches-per-
source DECLINE as the wiki grows, and a fixed COUNT starves small-page
corpora (articles, ~14KB/page) while flooding large-page ones (transcripts,
~138KB/page). These tests prove the adaptive behavior in BOTH directions,
using the exact page-size profiles measured in the issue this fixes:

  - "articles" profile:    45 pages, ~14KB mean  -> MANY candidates, still
                           within the byte budget.
  - "transcripts" profile: 16 pages, ~138KB mean -> FEW candidates (the
                           floor), deliberately OVER budget because a huge-
                           page corpus must still get merge targets.

Also proves: the floor holds even when a SINGLE page alone exceeds the
whole budget, the ceiling caps a many-tiny-pages corpus, and an explicit
``--k`` still pins the exact deprecated fixed-count behavior regardless of
page size.
"""

from __future__ import annotations

import json

from wiki_weaver.ingest import retrieve_slice
from wiki_weaver.lib import (
    DEFAULT_SLICE_BUDGET_BYTES,
    MAX_SLICE_CANDIDATES,
    MIN_SLICE_CANDIDATES,
    WikiRoot,
    ensure_dir,
)

# The historical fixed default this whole fix replaces -- used below only as
# a comparison point ("more than the old cap"), never as an expectation.
LEGACY_FIXED_K = 6

NAV_INDEX = "---\ntitle: Index\ntype: index\n---\n\n# Index\n"


def _sized_page(idx: int, target_bytes: int, topic: str = "widget") -> str:
    """A wiki page whose on-disk UTF-8 size is close to ``target_bytes``,
    sharing ``topic`` with the query so it registers a positive BM25 score.
    Padding uses a filler word that shares no vocabulary with the query, so
    it inflates size without changing relevance.
    """
    header = f"---\ntitle: {topic.title()} Page {idx}\ntype: concept\n---\n\n# {topic.title()} Page {idx}\n\n{topic.title()} notes about {topic} usage.\n"
    header_bytes = len(header.encode("utf-8"))
    filler_needed = max(0, target_bytes - header_bytes)
    unit = "filler content "
    filler = (unit * (filler_needed // len(unit) + 1))[:filler_needed]
    return header + filler


def _seed_pages(wiki_root, count: int, target_bytes: int, topic: str = "widget") -> dict[str, str]:
    """Add ``count`` sized pages plus a nav index page; returns
    {filename: content} for the sized pages only (so tests can measure
    actual on-disk byte sizes directly from the strings they generated,
    independent of anything build_slice/WikiPage computes)."""
    pages: dict[str, str] = {}
    for i in range(count):
        name = f"{topic}-{i:03d}.md"
        content = _sized_page(i, target_bytes, topic)
        wiki_root.add_page(name, content)
        pages[name] = content
    wiki_root.add_page("index.md", NAV_INDEX)
    return pages


def _set_source(wiki_root, topic: str = "widget") -> WikiRoot:
    wiki_root.add_source("s1.txt", f"kind: article\n\n{topic.title()} usage across many contexts.\n")
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    return wr


def _run_default(wr: WikiRoot) -> dict:
    code = retrieve_slice.main(["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical"])
    assert code == 0
    return json.loads(wr.current_slice_file.read_text(encoding="utf-8"))


def _bare(page_path: str) -> str:
    """``bm25_hits[i]["page"]`` is rendered wiki/-relative (e.g.
    "wiki/widget-000.md") -- strip that prefix to look the page back up in
    the {filename: content} dict this test's own fixtures are keyed by."""
    return page_path.removeprefix("wiki/")


# ---------------------------------------------------------------------------
# Direction 1: small pages (the "articles" profile, ~14KB mean, 45 pages) ->
# MANY candidates, still within budget.
# ---------------------------------------------------------------------------


def test_small_pages_yield_many_candidates_within_budget(wiki_root):
    pages = _seed_pages(wiki_root, count=45, target_bytes=14_000)
    wr = _set_source(wiki_root)

    data = _run_default(wr)
    hits = data["bm25_hits"]

    # MANY: strictly more than the old fixed default -- this is the core of
    # the fix (small-page corpora were starved at a fixed k=6).
    assert len(hits) > LEGACY_FIXED_K
    # But NOT unbounded -- the budget still cuts before every page in this
    # 45-page corpus is included, proving the budget (not "return
    # everything because the wiki is small") is what's doing the work.
    assert len(hits) < len(pages)

    # WITHIN BUDGET: sum the ACTUAL on-disk bytes of the selected pages
    # (measured directly from the content this test generated, independent
    # of build_slice's own accounting) and confirm it fits.
    selected_names = {_bare(hit["page"]) for hit in hits}
    total_bytes = sum(len(pages[name].encode("utf-8")) for name in selected_names if name in pages)
    assert total_bytes <= DEFAULT_SLICE_BUDGET_BYTES


# ---------------------------------------------------------------------------
# Direction 2: large pages (the "transcripts" profile, ~138KB mean, 16
# pages) -> FEW candidates (the floor), deliberately OVER budget.
# ---------------------------------------------------------------------------


def test_large_pages_yield_few_candidates_floor_holds(wiki_root):
    pages = _seed_pages(wiki_root, count=16, target_bytes=138_000)
    wr = _set_source(wiki_root)

    data = _run_default(wr)
    hits = data["bm25_hits"]

    # FEW: the floor, not zero and not one -- create-vs-fold needs real
    # options to compare even when pages are huge.
    assert len(hits) == MIN_SLICE_CANDIDATES

    # The floor OVERRIDES the budget here: 3 pages at ~138KB each is well
    # over DEFAULT_SLICE_BUDGET_BYTES, and that is the whole point -- a
    # large-page corpus must still get merge targets.
    selected_names = {_bare(hit["page"]) for hit in hits}
    total_bytes = sum(len(pages[name].encode("utf-8")) for name in selected_names if name in pages)
    assert total_bytes > DEFAULT_SLICE_BUDGET_BYTES


# ---------------------------------------------------------------------------
# The floor holds even when a SINGLE page alone exceeds the whole budget.
# ---------------------------------------------------------------------------


def test_floor_holds_when_a_single_page_exceeds_the_whole_budget(wiki_root):
    # Each page, on its own, is already bigger than the entire budget.
    target = DEFAULT_SLICE_BUDGET_BYTES + 50_000
    pages = _seed_pages(wiki_root, count=5, target_bytes=target)
    wr = _set_source(wiki_root)

    data = _run_default(wr)
    hits = data["bm25_hits"]

    assert len(hits) == MIN_SLICE_CANDIDATES
    # Confirm the premise: the very first candidate alone already blows the
    # entire budget, yet the floor still returned MIN_SLICE_CANDIDATES.
    first_page = _bare(hits[0]["page"])
    assert len(pages[first_page].encode("utf-8")) > DEFAULT_SLICE_BUDGET_BYTES


# ---------------------------------------------------------------------------
# The ceiling caps a many-tiny-pages corpus even though the budget has
# bytes to spare.
# ---------------------------------------------------------------------------


def test_ceiling_caps_many_tiny_pages_even_under_budget(wiki_root):
    pages = _seed_pages(wiki_root, count=MAX_SLICE_CANDIDATES + 30, target_bytes=150)
    wr = _set_source(wiki_root)

    data = _run_default(wr)
    hits = data["bm25_hits"]

    assert len(hits) == MAX_SLICE_CANDIDATES

    # The budget was NOT the binding constraint here -- confirm the
    # selected pages use only a small fraction of it.
    selected_names = {_bare(hit["page"]) for hit in hits}
    total_bytes = sum(len(pages[name].encode("utf-8")) for name in selected_names if name in pages)
    assert total_bytes < DEFAULT_SLICE_BUDGET_BYTES / 2


# ---------------------------------------------------------------------------
# Explicit --k still pins the deprecated fixed-count behavior, ignoring the
# budget/floor/ceiling entirely -- even on a huge-page corpus where the
# requested count blows far past the byte budget.
# ---------------------------------------------------------------------------


def test_explicit_k_overrides_budget_entirely(wiki_root):
    _seed_pages(wiki_root, count=16, target_bytes=138_000)
    wr = _set_source(wiki_root)

    code = retrieve_slice.main(["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical", "--k", "6"])
    assert code == 0
    data = json.loads(wr.current_slice_file.read_text(encoding="utf-8"))

    # Exactly 6 -- NOT the floor (3), NOT budget-limited -- proving the
    # explicit override bypasses the new default path entirely.
    assert len(data["bm25_hits"]) == 6
    assert data["k"] == 6
