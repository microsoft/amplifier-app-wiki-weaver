# pyright: reportMissingImports=false
"""OV2 scale-aware wikilink gate -- regression + boundary tests.

THE BUG THIS PINS: OV2 used to gate every wiki on a flat
``OVERVIEW_WIKILINK_MIN`` (5) regardless of wiki size. overview.md links to
the wiki's OTHER pages -- in a wiki of N pages, a natural overview links to
roughly N-2 of them (every page except itself and index.md). A small wiki
cannot reach 5 links without repeating the same link, which a genuine
thematic synthesis will not do. Measured on a 674-repo production corpus,
476/674 (71%) have fewer than 5 pages TOTAL -- OV2 was practically
unreachable for the large majority of real wikis (see
wiki_weaver.grading._required_wikilink_count's docstring for the full
writeup, including the honest "practically unreachable, not structurally
impossible" framing: len(findall(...)) counts total occurrences, not
distinct pages, so padding the same link could technically pass -- but a
genuine synthesis does not pad).

THE FIX: OV2's bar scales with the wiki's linkable-page count, capped at the
unchanged ``OVERVIEW_WIKILINK_MIN`` (5) so wikis of >= 7 linkable pages see
ZERO behaviour change.

MOCKING STRATEGY: none needed -- grade_overview(judge_fn=None) is fully
deterministic and offline. Wikis are built synthetically under tmp_path
(same convention as eval/test_consistency.py's ``_page`` helper) rather than
as new static fixtures, since these tests pin exact page/link COUNTS that
are easiest to see and vary inline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wiki_weaver.grading import (  # noqa: E402
    OVERVIEW_WIKILINK_MIN,
    _required_wikilink_count,
    grade_overview,
)


def _build_wiki(
    wiki: Path,
    *,
    total_pages: int,
    wikilinks: int,
    include_index: bool = True,
) -> Path:
    """Build a minimal synthetic wiki.

    ``total_pages`` is the TOTAL count of root ``*.md`` files (matching the
    production-corpus stat cited in the PR: it includes index.md and
    overview.md themselves). ``wikilinks`` is the number of raw ``[[...]]``
    occurrences placed in overview.md (repeating targets if there are fewer
    content pages than links requested -- OV2 counts occurrences, not
    distinct targets, which is the documented "practically unreachable, not
    structurally impossible" honesty in the gate).

    overview.md prose deliberately avoids the OV1 "(source N)" pattern so
    these tests isolate OV2 behaviour.
    """
    wiki.mkdir(parents=True, exist_ok=True)
    n_special = 1 + int(include_index)  # overview.md always present
    n_content = max(total_pages - n_special, 0)
    for i in range(n_content):
        (wiki / f"page{i}.md").write_text(f"# Page {i}\n\nContent.\n", encoding="utf-8")
    if include_index:
        (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    targets = [f"page{i}" for i in range(n_content)] or ["overview"]
    links = " ".join(f"[[{targets[i % len(targets)]}]]" for i in range(wikilinks))
    (wiki / "overview.md").write_text(
        f"# Overview\n\nA thematic synthesis of the corpus. {links}\n",
        encoding="utf-8",
    )
    return wiki


# ---------------------------------------------------------------------------
# _required_wikilink_count -- pins the worked table from the PR description
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "total_pages,expected_required,expected_linkable",
    [
        (3, 1, 1),  # 3 pages -> min(5, max(1, 1)) = 1
        (5, 3, 3),  # 5 pages -> min(5, max(1, 3)) = 3
        (7, 5, 5),  # 7 pages -> min(5, max(1, 5)) = 5  (full bar first reached)
        (30, 5, 28),  # 30 pages -> min(5, 28) = 5      (unchanged, capped)
    ],
)
def test_required_wikilink_count_formula(
    tmp_path: Path, total_pages: int, expected_required: int, expected_linkable: int
) -> None:
    wiki = _build_wiki(tmp_path / "w", total_pages=total_pages, wikilinks=0)
    required, linkable = _required_wikilink_count(wiki)
    assert (required, linkable) == (expected_required, expected_linkable), (
        f"total_pages={total_pages}: expected required={expected_required}, "
        f"linkable={expected_linkable}; got required={required}, linkable={linkable}"
    )


def test_required_wikilink_count_capped_at_constant_for_large_wikis(
    tmp_path: Path,
) -> None:
    """>= 7 linkable pages must hit the unchanged cap, never exceed it."""
    wiki = _build_wiki(tmp_path / "w", total_pages=50, wikilinks=0)
    required, _linkable = _required_wikilink_count(wiki)
    assert required == OVERVIEW_WIKILINK_MIN


# ---------------------------------------------------------------------------
# Regression: small wiki that could never pass the old flat bar now passes
# ---------------------------------------------------------------------------


def test_small_wiki_3_pages_2_links_passes(tmp_path: Path) -> None:
    """3-page wiki, 2 wikilinks -- THE BUG. Old flat bar (>= 5) always FAILed
    a wiki this size; a genuine overview linking to both of its other pages
    (the max a 3-page wiki can naturally offer) must now PASS.
    """
    wiki = _build_wiki(tmp_path / "wiki", total_pages=3, wikilinks=2)
    result = grade_overview(wiki, judge_fn=None)
    assert result.passed, (
        "3-page wiki with 2 wikilinks should PASS OV2 under the scale-aware "
        f"gate; failures={result.failures!r}"
    )


def test_cortex_pack_items_shape_passes(tmp_path: Path) -> None:
    """Reproduces the confirmed-failing production case: exactly 3 files
    (index.md, overview.md, one content page) -- bkrabach/cortex-pack-items.
    OV1 passed cleanly there; only OV2's flat bar fired. With 3 total pages,
    required == 1, so even a single wikilink to the sole content page passes.
    """
    wiki = _build_wiki(tmp_path / "wiki", total_pages=3, wikilinks=1)
    result = grade_overview(wiki, judge_fn=None)
    assert result.passed, f"failures={result.failures!r}"
    assert any("OV2" in n for n in result.notes)


# ---------------------------------------------------------------------------
# The gate must NOT be gutted: a large wiki still fails on too few links
# ---------------------------------------------------------------------------


def test_large_wiki_10_pages_3_links_still_fails(tmp_path: Path) -> None:
    """10-page wiki, only 3 wikilinks -- required is min(5, 8) = 5, so this
    must still FAIL. Proves the scale-aware gate is not a free pass for
    large wikis that pad only a handful of links.
    """
    wiki = _build_wiki(tmp_path / "wiki", total_pages=10, wikilinks=3)
    result = grade_overview(wiki, judge_fn=None)
    assert not result.passed, (
        "10-page wiki with only 3 wikilinks should still FAIL OV2 "
        "(required is capped at 5, not lowered for large wikis)"
    )
    assert any("OV2" in f for f in result.failures), (
        f"expected an OV2 failure message; failures={result.failures!r}"
    )


def test_large_wiki_10_pages_5_links_passes(tmp_path: Path) -> None:
    """10-page wiki, 5 wikilinks -- meets the unchanged cap, must PASS."""
    wiki = _build_wiki(tmp_path / "wiki", total_pages=10, wikilinks=5)
    result = grade_overview(wiki, judge_fn=None)
    assert result.passed, f"failures={result.failures!r}"


# ---------------------------------------------------------------------------
# Boundary at exactly 7 pages -- where the full bar of 5 is first reached
# ---------------------------------------------------------------------------


def test_boundary_six_pages_needs_only_four(tmp_path: Path) -> None:
    """6 pages -> required = min(5, 4) = 4. 4 links must PASS (below the old
    flat bar of 5, but exactly the new scaled requirement).
    """
    wiki = _build_wiki(tmp_path / "wiki", total_pages=6, wikilinks=4)
    result = grade_overview(wiki, judge_fn=None)
    assert result.passed, f"failures={result.failures!r}"


def test_boundary_seven_pages_needs_full_bar(tmp_path: Path) -> None:
    """7 pages -> required = min(5, 5) = 5 -- the first page count at which
    the full, unchanged bar applies. 4 links must FAIL; 5 must PASS.
    """
    wiki_fail = _build_wiki(tmp_path / "wiki_fail", total_pages=7, wikilinks=4)
    result_fail = grade_overview(wiki_fail, judge_fn=None)
    assert not result_fail.passed, (
        f"7-page wiki with 4 links should FAIL (full bar of 5 applies at "
        f"7 pages); failures={result_fail.failures!r}"
    )

    wiki_pass = _build_wiki(tmp_path / "wiki_pass", total_pages=7, wikilinks=5)
    result_pass = grade_overview(wiki_pass, judge_fn=None)
    assert result_pass.passed, f"failures={result_pass.failures!r}"


# ---------------------------------------------------------------------------
# 1-page wiki -- explicit exemption decision (not floored at 1)
# ---------------------------------------------------------------------------


def test_one_page_wiki_exempt_from_ov2(tmp_path: Path) -> None:
    """A wiki whose only page IS overview.md (no index.md, no content pages
    -- 204/674 corpus wikis are this shape) has nothing to link to at all.
    DECISION: exempt (required == 0) rather than floored at 1 -- demanding
    a link from a page with no valid target would gate against a page that
    structurally cannot pass, not a genuine quality signal.
    """
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "overview.md").write_text(
        "# Overview\n\nA single-source corpus with nothing else to link.\n",
        encoding="utf-8",
    )
    required, linkable = _required_wikilink_count(wiki)
    assert (required, linkable) == (0, 0)

    result = grade_overview(wiki, judge_fn=None)
    assert result.passed, (
        f"1-page wiki (overview.md only) should be exempt from OV2; "
        f"failures={result.failures!r}"
    )


def test_two_page_wiki_index_and_overview_only_is_exempt(tmp_path: Path) -> None:
    """index.md + overview.md, ZERO content pages -- still nothing real to
    link to, so this is exempt too, even though naive page_count - 2
    arithmetic would suggest max(1, 0) = 1. Name-based exclusion (not a bare
    subtraction) is what makes this the right call.
    """
    wiki = _build_wiki(tmp_path / "wiki", total_pages=2, wikilinks=0, include_index=True)
    required, linkable = _required_wikilink_count(wiki)
    assert (required, linkable) == (0, 0)
    assert grade_overview(wiki, judge_fn=None).passed


def test_two_page_wiki_one_content_page_no_index_requires_one_link(
    tmp_path: Path,
) -> None:
    """overview.md + ONE real content page, no index.md yet (the documented
    "fresh wiki, no front door" case in consistency.py) -- there IS one page
    to link to, so >= 1 link is required; zero links must FAIL.
    """
    wiki = _build_wiki(
        tmp_path / "wiki", total_pages=2, wikilinks=0, include_index=False
    )
    required, linkable = _required_wikilink_count(wiki)
    assert (required, linkable) == (1, 1)
    assert not grade_overview(wiki, judge_fn=None).passed

    wiki2 = _build_wiki(
        tmp_path / "wiki2", total_pages=2, wikilinks=1, include_index=False
    )
    assert grade_overview(wiki2, judge_fn=None).passed
