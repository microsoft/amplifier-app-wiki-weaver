"""Calibration tests for Phase A graders — static known-bad and known-good fixtures.

Decoupled from runs/corpus/wiki (live, mutable) onto deterministic static fixtures
under eval/fixtures/ so calibration tests stay green regardless of corpus state.

KNOWN-BAD FIXTURE: eval/fixtures/known-bad-wiki/
  Represents the pre-Phase-A concatenation state (per-source parenthetical openers
  in overview.md, registry entries without author/url).

CALIBRATION FACTS (deterministic — fixed by fixture content):

  grade_overview (overview.md — 10 source-narration openers):
    source_narration_openers  : 10  (threshold <= 2  → OV1 FAILS)
    wikilink_count            : 6   (threshold >= 5  → OV2 passes)
    section_header_count      : 0   (diagnostic only — no thematic ## sections)
    result.passed             : False  (OV1 violates the hard gate)

  grade_provenance (.sources.json — 5 entries, id/filename/hash only):
    pct_sources_with_author_url : 0.0%  (threshold >= 80% → PR1 FAILS)
    total_sources               : 5
    result.passed               : False  (PR1 violates the hard gate)

KNOWN-GOOD FIXTURE: eval/fixtures/good-wiki/
  Represents the post-Phase-A synthesized state (thematic ## sections, wikilinks,
  registry entries with author+url — the targets the Phase A fixes achieve).

Both graders MUST FAIL on the known-bad fixture and PASS on the known-good fixture.
Tests are skipped if the fixture directory is not present (guards against accidental
deletion; fixtures should always be present since they are committed).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "pipeline"))

from grade_wiki import grade_overview, grade_provenance  # noqa: E402

_BAD_WIKI = _REPO / "eval" / "fixtures" / "known-bad-wiki"
_GOOD_WIKI = _REPO / "eval" / "fixtures" / "good-wiki"


# ---------------------------------------------------------------------------
# grade_overview calibration — known-bad fixture MUST FAIL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def overview_result():
    """grade_overview result on known-bad fixture — deterministic-only (no judge_fn)."""
    if not _BAD_WIKI.is_dir():
        pytest.skip(f"known-bad wiki fixture not found at {_BAD_WIKI}")
    return grade_overview(_BAD_WIKI, judge_fn=None)


def test_overview_fails_known_bad(overview_result):
    """grade_overview must FAIL on the known-bad fixture.

    overview.md contains 10 per-source parenthetical openers like '(source N)'.
    OV1 hard gate must fire (10 > threshold of 2).  If this test starts PASSING
    it means either the grader regressed and no longer catches the concatenation
    pattern, or the fixture was accidentally modified.
    """
    assert not overview_result.passed, (
        "grade_overview should FAIL on the known-bad fixture (OV1 gate must fire); "
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
    """source_narration_openers must be >= 3 on the known-bad fixture (fixture has 10).

    A drop below 3 means the _OVERVIEW_SOURCE_REF regex has been weakened or
    the fixture was changed.  The static fixture contains 10 explicit '(source N)'
    parenthetical openers — well above the <= 2 pass threshold.
    """
    count = _extract_opener_count(overview_result)
    assert count != -1, (
        "Could not parse opener count from OV1 messages; "
        f"failures={overview_result.failures!r}, notes={overview_result.notes!r}"
    )
    assert count >= 3, (
        f"expected >= 3 source-narration openers on known-bad fixture (fixture has 10), "
        f"got {count}; _OVERVIEW_SOURCE_REF regex may have been weakened or fixture modified"
    )


def test_overview_opener_count_pinned(overview_result):
    """source_narration_openers must match the fixture count within ±2 (fixture has 10).

    Pins the known-bad fixture value so an unexpected change in overview.md or
    the regex is caught.  Tight tolerance since the fixture is static and deterministic.
    """
    count = _extract_opener_count(overview_result)
    assert count != -1, (
        "Could not parse opener count from OV1 messages; "
        f"failures={overview_result.failures!r}"
    )
    assert 8 <= count <= 12, (
        f"expected source_narration_openers in range [8, 12] on known-bad fixture "
        f"(fixture has exactly 10 explicit openers), got {count}; "
        "either the fixture was modified or _OVERVIEW_SOURCE_REF was changed"
    )


# ---------------------------------------------------------------------------
# grade_provenance calibration — known-bad fixture MUST FAIL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def provenance_result():
    """grade_provenance result on known-bad fixture — deterministic (no LLM)."""
    if not _BAD_WIKI.is_dir():
        pytest.skip(f"known-bad wiki fixture not found at {_BAD_WIKI}")
    return grade_provenance(_BAD_WIKI)


def test_provenance_fails_known_bad(provenance_result):
    """grade_provenance must FAIL on the known-bad fixture.

    .sources.json stores only id/filename/hash — no author or url fields.
    PR1 hard gate must fire (0% < 80% threshold).  If this test starts PASSING
    it means either the fixture was modified (check .sources.json) or the grader
    stopped checking correctly.
    """
    assert not provenance_result.passed, (
        "grade_provenance should FAIL on the known-bad fixture (PR1 gate must fire); "
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
    """pct_sources_with_author_url must be 0.0% on the known-bad fixture.

    The known-bad registry stores only filename + hash (no author, no url).
    Any non-zero value means either (a) the fixture was modified or
    (b) the grader is miscounting fields.
    """
    pct = _extract_provenance_pct(provenance_result)
    assert pct != -1.0, (
        "Could not parse provenance pct from PR1 messages; "
        f"failures={provenance_result.failures!r}, notes={provenance_result.notes!r}"
    )
    assert pct == pytest.approx(0.0, abs=0.01), (
        f"expected 0.0% sources with author+url on known-bad fixture, got {pct:.1%}; "
        "fixture .sources.json may have been modified OR the grader is counting wrong fields"
    )


def test_provenance_total_sources(provenance_result):
    """Registry must have >= 3 entries on the known-bad fixture (fixture has 5).

    Guards against silent registry truncation or fixture deletion.
    """
    total = _extract_total_sources(provenance_result)
    assert total != -1, (
        f"Could not parse total_sources from notes; notes={provenance_result.notes!r}"
    )
    assert total >= 3, (
        f"expected >= 3 sources in registry on known-bad fixture (fixture has 5), "
        f"got {total}; fixture .sources.json may be empty or the path is wrong"
    )


def test_provenance_total_sources_pinned(provenance_result):
    """Total source count must be in range [3, 8] (fixture has exactly 5).

    Tight tolerance since the fixture is static and deterministic.  A count
    outside this range means the fixture file was modified.
    """
    total = _extract_total_sources(provenance_result)
    assert total != -1, (
        f"Could not parse total_sources; notes={provenance_result.notes!r}"
    )
    assert 3 <= total <= 8, (
        f"expected total_sources in [3, 8] on known-bad fixture (fixture has 5), "
        f"got {total}; fixture .sources.json was likely modified"
    )


# ---------------------------------------------------------------------------
# grade_overview + grade_provenance — known-good fixture MUST PASS
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def good_overview_result():
    """grade_overview result on known-good fixture — deterministic-only (no judge_fn)."""
    if not _GOOD_WIKI.is_dir():
        pytest.skip(f"known-good wiki fixture not found at {_GOOD_WIKI}")
    return grade_overview(_GOOD_WIKI, judge_fn=None)


@pytest.fixture(scope="module")
def good_provenance_result():
    """grade_provenance result on known-good fixture — deterministic (no LLM)."""
    if not _GOOD_WIKI.is_dir():
        pytest.skip(f"known-good wiki fixture not found at {_GOOD_WIKI}")
    return grade_provenance(_GOOD_WIKI)


def test_overview_passes_known_good(good_overview_result):
    """grade_overview must PASS on the known-good fixture.

    The known-good overview.md has thematic ## sections, >= 5 [[wikilinks]],
    and zero source-narration openers — the target state Phase A aimed for.
    If this test FAILS it means the grader is too strict or the fixture was
    corrupted.
    """
    assert good_overview_result.passed, (
        "grade_overview should PASS on the known-good fixture; "
        f"failures: {good_overview_result.failures}"
    )


def test_provenance_passes_known_good(good_provenance_result):
    """grade_provenance must PASS on the known-good fixture.

    The known-good .sources.json has all entries with author + url (100% >= 80%
    threshold) — the target state Phase A aimed for.  If this test FAILS it
    means the grader is too strict or the fixture was corrupted.
    """
    assert good_provenance_result.passed, (
        "grade_provenance should PASS on the known-good fixture; "
        f"failures: {good_provenance_result.failures}"
    )


# ---------------------------------------------------------------------------
# Helpers — extract metric values from GradeResult notes/failures
# ---------------------------------------------------------------------------


def _extract_opener_count(result) -> int:
    """Parse source-narration opener count from OV1 gate messages.

    Handles both:
      FAIL state:  "OV1 FAIL: 10 source-narration openers ..."  (in failures)
      PASS state:  "OV1 source-narration openers: 10 ..."        (in notes)
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
