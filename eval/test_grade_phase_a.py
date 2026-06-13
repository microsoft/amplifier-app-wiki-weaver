"""Calibration tests for Phase A graders against the FROZEN corpus wiki.

Pins grade_overview and grade_provenance against runs/corpus/wiki (144 converged
pages, frozen 2026-06-13) so that a future pipeline fix cannot silently produce a
passing score on the old output.

CALIBRATION FACTS measured 2026-06-13 on runs/corpus/wiki:

  grade_overview (overview.md — 236 KB, one giant paragraph per source):
    source_narration_openers  : 111  (threshold <= 2  → OV1 FAILS)
    wikilink_count            : 388  (threshold >= 5  → OV2 passes)
    section_header_count      : 0    (diagnostic only — no thematic ## sections)
    result.passed             : False  (OV1 violates the hard gate)

  grade_provenance (.sources.json — 145 entries, id/filename/hash only):
    pct_sources_with_author_url : 0.0%  (threshold >= 80% → PR1 FAILS)
    total_sources               : 145
    result.passed               : False  (PR1 violates the hard gate)

Both graders MUST continue to FAIL on this frozen corpus. They MUST pass once the
corresponding pipeline fixes land (synthesized overview / enriched registry).

Tests are skipped if the frozen corpus is not present (CI without fixture data).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "pipeline"))

from grade_wiki import grade_overview, grade_provenance  # noqa: E402

_WIKI = _REPO / "runs" / "corpus" / "wiki"
pytestmark = pytest.mark.skipif(
    not _WIKI.is_dir(),
    reason=f"frozen corpus wiki not found at {_WIKI}",
)


# ---------------------------------------------------------------------------
# grade_overview calibration — frozen corpus MUST FAIL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def overview_result():
    """grade_overview result — deterministic-only (no judge_fn)."""
    return grade_overview(_WIKI, judge_fn=None)


def test_overview_fails_frozen_corpus(overview_result):
    """grade_overview must FAIL on the frozen concatenation corpus.

    overview.md is a per-source log (one paragraph per source article with
    parenthetical source references like "(source 84)").  OV1 hard gate must
    fire.  If this test starts PASSING it means either the overview was fixed
    (great — delete this calibration test and add a passing one) or the
    grader regressed and no longer catches the concatenation pattern.
    """
    assert not overview_result.passed, (
        "grade_overview should FAIL on the frozen corpus (OV1 gate must fire); "
        "if it passes, _OVERVIEW_SOURCE_REF regex is no longer detecting the "
        "per-source concatenation pattern in overview.md"
    )


def test_overview_ov1_gate_present_in_failures(overview_result):
    """The OV1 hard gate must appear explicitly in the failures list."""
    ov1_fails = [f for f in overview_result.failures if "OV1" in f]
    assert ov1_fails, (
        "OV1 gate message not found in failures — either the gate was removed "
        f"or renamed. failures={overview_result.failures!r}"
    )


def test_overview_opener_count_high(overview_result):
    """source_narration_openers must be >= 50 on the frozen corpus (observed: 111).

    A drop below 50 means the _OVERVIEW_SOURCE_REF regex has been weakened.
    The frozen overview.md has 111 '(source N)' parenthetical openers — one
    per source-article description paragraph.
    """
    count = _extract_opener_count(overview_result)
    assert count != -1, (
        "Could not parse opener count from OV1 messages; "
        f"failures={overview_result.failures!r}, notes={overview_result.notes!r}"
    )
    assert count >= 50, (
        f"expected >= 50 source-narration openers on frozen corpus (observed: 111), "
        f"got {count}; _OVERVIEW_SOURCE_REF regex may have been weakened"
    )


def test_overview_opener_count_pinned(overview_result):
    """source_narration_openers must match the frozen-corpus count within ±15.

    Pins the observed value (111) so an unexpected change in overview.md or
    the regex is caught.  ±15 tolerance allows for minor corpus edits without
    false-positives.
    """
    count = _extract_opener_count(overview_result)
    assert count != -1, (
        "Could not parse opener count from OV1 messages; "
        f"failures={overview_result.failures!r}"
    )
    assert 96 <= count <= 126, (
        f"expected source_narration_openers in range [96, 126] on frozen corpus "
        f"(observed: 111), got {count}; "
        "either overview.md changed or _OVERVIEW_SOURCE_REF was modified"
    )


# ---------------------------------------------------------------------------
# grade_provenance calibration — frozen corpus MUST FAIL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def provenance_result():
    """grade_provenance result — deterministic (no LLM)."""
    return grade_provenance(_WIKI)


def test_provenance_fails_frozen_corpus(provenance_result):
    """grade_provenance must FAIL on the frozen corpus.

    .sources.json stores only id/filename/hash — no author or url fields.
    PR1 hard gate must fire (0% < 80% threshold).  If this test starts
    PASSING it means either the registry was enriched (great — the fix
    landed) or the grader stopped checking correctly.
    """
    assert not provenance_result.passed, (
        "grade_provenance should FAIL on the frozen corpus (PR1 gate must fire); "
        "if it passes, either .sources.json unexpectedly gained author/url fields "
        "or the PR1 gate was broken"
    )


def test_provenance_pr1_gate_present_in_failures(provenance_result):
    """The PR1 hard gate must appear explicitly in the failures list."""
    pr1_fails = [f for f in provenance_result.failures if "PR1" in f]
    assert pr1_fails, (
        "PR1 gate message not found in failures — either the gate was removed "
        f"or renamed. failures={provenance_result.failures!r}"
    )


def test_provenance_pct_is_zero(provenance_result):
    """pct_sources_with_author_url must be 0.0% on the frozen corpus.

    The registry only stores filename + hash.  Any non-zero value means
    either (a) the registry was enriched (the fix we want — update this
    test), or (b) the grader is miscounting fields.
    """
    pct = _extract_provenance_pct(provenance_result)
    assert pct != -1.0, (
        "Could not parse provenance pct from PR1 messages; "
        f"failures={provenance_result.failures!r}, notes={provenance_result.notes!r}"
    )
    assert pct == pytest.approx(0.0, abs=0.01), (
        f"expected 0.0% sources with author+url on frozen corpus, got {pct:.1%}; "
        "registry may have gained author/url fields (fix landed!) "
        "OR the grader is counting wrong fields"
    )


def test_provenance_total_sources(provenance_result):
    """Registry must have >= 100 entries on the frozen corpus (observed: 145).

    Pins the source count so a silent registry truncation is caught.
    """
    total = _extract_total_sources(provenance_result)
    assert total != -1, (
        f"Could not parse total_sources from notes; notes={provenance_result.notes!r}"
    )
    assert total >= 100, (
        f"expected >= 100 sources in registry on frozen corpus (observed: 145), "
        f"got {total}; registry may be empty or the path is wrong"
    )


def test_provenance_total_sources_pinned(provenance_result):
    """Total source count must be in range [135, 155] (observed: 145).

    Pins the frozen-corpus size.  A count outside this range means the
    corpus was extended or the registry file changed.
    """
    total = _extract_total_sources(provenance_result)
    assert total != -1, (
        f"Could not parse total_sources; notes={provenance_result.notes!r}"
    )
    assert 135 <= total <= 155, (
        f"expected total_sources in [135, 155] on frozen corpus (observed: 145), "
        f"got {total}"
    )


# ---------------------------------------------------------------------------
# Helpers — extract metric values from GradeResult notes/failures
# ---------------------------------------------------------------------------


def _extract_opener_count(result) -> int:
    """Parse source-narration opener count from OV1 gate messages.

    Handles both:
      FAIL state:  "OV1 FAIL: 111 source-narration openers ..."  (in failures)
      PASS state:  "OV1 source-narration openers: 111 ..."        (in notes)
    Returns -1 if not found.
    """
    # FAIL message format: "OV1 FAIL: <count> source-narration openers"
    _fail_pat = re.compile(r"\bOV1 FAIL:\s*(\d+)\s+source-narration", re.IGNORECASE)
    # PASS message format: "OV1 source-narration openers: <count>"
    _pass_pat = re.compile(r"\bOV1 source-narration openers:\s*(\d+)", re.IGNORECASE)

    for msg in result.failures:
        m = _fail_pat.search(msg)
        if m:
            return int(m.group(1))

    for msg in result.notes:
        m = _pass_pat.search(msg)
        if m:
            return int(m.group(1))

    return -1


def _extract_provenance_pct(result) -> float:
    """Parse pct_sources_with_author_url from PR1 gate messages.

    Handles both:
      FAIL state:  "PR1 FAIL: 0.0% sources have author+url ..."   (in failures)
      PASS state:  "PR1 pct_sources_with_author_url: 0.0% ..."    (in notes)
    Returns -1.0 if not found.
    """
    _fail_pat = re.compile(r"\bPR1 FAIL:\s*([\d.]+)%", re.IGNORECASE)
    _pass_pat = re.compile(
        r"\bPR1 pct_sources_with_author_url:\s*([\d.]+)%", re.IGNORECASE
    )

    for msg in result.failures:
        m = _fail_pat.search(msg)
        if m:
            return float(m.group(1)) / 100.0

    for msg in result.notes:
        m = _pass_pat.search(msg)
        if m:
            return float(m.group(1)) / 100.0

    return -1.0


def _extract_total_sources(result) -> int:
    """Parse total_sources count from notes.

    Format: "total_sources: <count>"
    Returns -1 if not found.
    """
    _pat = re.compile(r"\btotal_sources:\s*(\d+)")
    for msg in result.notes:
        m = _pat.search(msg)
        if m:
            return int(m.group(1))
    return -1
