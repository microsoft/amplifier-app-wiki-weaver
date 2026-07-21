# pyright: reportMissingImports=false
"""Advisory-mode tests for the two runtime gates (duplicate-page + claim-retention).

THE CHANGE THIS PROVES: both gates -- added as hard blockers in PR #37 -- are
now ADVISORY by default at every wiring site (wiki_weaver/lib.py's ingest()
single-file + drain paths; the run_ingest() tool path is covered in
eval/test_run_ingest_backstops.py). They still DETECT and surface LOUDLY at
run level ("GATE ADVISORY" line + a distinct end-of-run block + the
machine-readable DrainReport.advisories field), but they never block a source:
no _fail, no _failed/ quarantine, no non-zero exit. The env hatch
WIKI_WEAVER_ENFORCE_GATES=1 restores the OLD hard-blocking behavior for both
gates -- see wiki_weaver.grading.gates_enforced().

Incidents this converts from silent-green blocking to loud advisory:
  - a legitimately coexisting version-page pair (gpt-5.md + gpt-5-1.md =
    "GPT-5.1") blocked EVERY source in a run (wiki-wide false positive);
  - claim-retention grader errors at the escalation threshold failed CLOSED
    on infrastructure hiccups.

MOCKING STRATEGY (same conventions as eval/test_ingest_drain.py and
eval/test_run_ingest_backstops.py):
  - run_inner() is mocked -- no real engine/LLM calls.
  - no_duplicate_pages() is NEVER mocked -- the real deterministic scan runs
    over real files (including the real gpt-5.md/gpt-5-1.md false-positive).
  - For claim-retention, only check_retention() is faked; the REAL
    enforce_retention_gate() orchestration (persistent escalation counter,
    fail-open/fail-closed decision, snapshot cleanup) runs unmodified.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# lib.ingest() pulls in the attractor engine deps at call time. Skip cleanly
# in lightweight CI -- same convention as eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.lib as lib  # noqa: E402
import wiki_weaver.retention as retention  # noqa: E402
from wiki_weaver.grading import gates_enforced  # noqa: E402
from wiki_weaver.lib import INBOX, SOURCES, DrainReport  # noqa: E402


@pytest.fixture(autouse=True)
def _advisory_default_env(monkeypatch):
    """The mode under test is the DEFAULT (advisory): make sure the escape
    hatch is unset regardless of the developer's shell env. Tests that prove
    the hatch set WIKI_WEAVER_ENFORCE_GATES explicitly."""
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)


@pytest.fixture(autouse=True)
def _bypass_reweave_gate(monkeypatch):
    """Stub the overview re-weave gate (orthogonal concern; covered by
    eval/test_reweave.py). Without this, grade_overview() would legitimately
    fail on these bare fixtures and attempt a real re-weave LLM call."""
    from wiki_weaver.reweave import ReweaveGateResult

    monkeypatch.setattr(
        "wiki_weaver.reweave.reweave_overview_if_needed",
        lambda *_a, **_kw: ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="stub: bypassed for gate-advisory test",
            final_report="stub: bypassed for gate-advisory test",
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers (pattern copied from eval/test_ingest_drain.py)
# ---------------------------------------------------------------------------


def _make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    return wiki


def _make_wiki_with_version_pages(tmp_path: Path) -> Path:
    """A wiki containing a legitimately COEXISTING version-page pair that the
    duplicate-page heuristic false-positives on: gpt-5-1.md ("GPT-5.1")
    alongside gpt-5.md."""
    wiki = _make_wiki(tmp_path)
    (wiki / "gpt-5.md").write_text("# GPT-5\n\nModel page.\n", encoding="utf-8")
    (wiki / "gpt-5-1.md").write_text("# GPT-5.1\n\nModel page.\n", encoding="utf-8")
    return wiki


def _seed_inbox(inbox: Path, name: str) -> Path:
    p = inbox / name
    p.write_text(f"# {name}\n\nUnique body text for {name}.\n", encoding="utf-8")
    old = time.time() - 10  # past the 2-s drain debounce
    os.utime(p, (old, old))
    return p


def _fake_result(converged: bool = True, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        converged=converged,
        status=status,
        failure_reason=None if converged else "did not converge",
        logs_dir=Path("/tmp/fake_logs"),
    )


def _mock_run_inner(*_a, **_kw) -> SimpleNamespace:
    return _fake_result(True)


def _confirmed_loss_result() -> "retention.RetentionGateResult":
    return retention.RetentionGateResult(
        pages=[
            retention.PageRetentionOutcome(
                page="page.md",
                status="confirmed_loss",
                silently_lost=[{"claim_quote": "Original grounded claim."}],
            )
        ]
    )


# ---------------------------------------------------------------------------
# gates_enforced() -- env hatch parsing
# ---------------------------------------------------------------------------


def test_gates_enforced_env_parsing(monkeypatch) -> None:
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)
    assert gates_enforced() is False, "unset must mean advisory (not enforced)"
    for off in ("", "0", "false", "False", " 0 "):
        monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", off)
        assert gates_enforced() is False, f"{off!r} must mean advisory"
    for on in ("1", "true", "yes"):
        monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", on)
        assert gates_enforced() is True, f"{on!r} must re-enable blocking"


# ---------------------------------------------------------------------------
# (a) duplicate-page gate is advisory: a pre-existing version-page pair no
#     longer blocks an unrelated source (was: 100% ingest blocked, wiki-wide)
# ---------------------------------------------------------------------------


def test_version_page_pair_no_longer_blocks_unrelated_source(
    tmp_path: Path, capsys
) -> None:
    wiki = _make_wiki_with_version_pages(tmp_path)
    src = _seed_inbox(wiki / INBOX, "unrelated.md")
    report = DrainReport()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, source=src, report=report)

    out = capsys.readouterr().out
    assert rc == 0, "advisory mode must NOT fail the run on a duplicate-page hit"
    assert (wiki / SOURCES / "unrelated.md").exists(), (
        "the unrelated source must proceed to archive -- was: blocked by a "
        "wiki-wide false positive on the pre-existing gpt-5.md/gpt-5-1.md pair"
    )
    # Detection preserved + surfaced loudly at run level.
    assert "GATE ADVISORY" in out
    assert "did NOT block" in out
    # Wiki-STRUCTURAL framing, naming the offending pages -- NOT "this source
    # is a duplicate".
    assert "wiki contains" in out
    assert "gpt-5-1.md" in out
    assert any("duplicate-page" in a for a in report.advisories)


# ---------------------------------------------------------------------------
# (b) claim-retention gate is advisory: escalated grader errors and confirmed
#     loss both PROCEED with a loud advisory (real enforce_retention_gate,
#     real escalation-counter mechanics; only check_retention is faked)
# ---------------------------------------------------------------------------


def test_escalated_grader_errors_proceed_with_advisory(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nA grounded claim.\n", encoding="utf-8")
    src = _seed_inbox(wiki / INBOX, "unrelated.md")

    # Pre-seed the REAL persistent counter at threshold-1 (default threshold 3)
    # so this run's grader error crosses the escalation threshold.
    retention.record_grader_error(wiki)
    retention.record_grader_error(wiki)
    assert retention.load_failure_counter(wiki) == 2

    def boom(before_dir, after_wiki, judge_fn=None):
        raise RuntimeError("simulated judge outage (infrastructure hiccup)")

    monkeypatch.setattr(retention, "check_retention", boom)

    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, source=src, report=report)

    out = capsys.readouterr().out
    assert rc == 0, (
        "advisory mode must PROCEED on block_escalated_errors -- was: failed "
        "CLOSED on an infrastructure hiccup"
    )
    assert (wiki / SOURCES / "unrelated.md").exists(), "source must still archive"
    assert "GATE ADVISORY" in out and "claim-retention" in out
    assert any("claim-retention" in a for a in report.advisories)
    # Counter mechanics preserved as-is: it kept counting (3/3) and the
    # escalation is SURFACED in the advisory instead of blocking.
    assert retention.load_failure_counter(wiki) == 3
    assert any("3" in a for a in report.advisories)


def test_confirmed_loss_proceeds_with_advisory(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text(
        "# Page\n\nOriginal grounded claim.\n", encoding="utf-8"
    )
    src = _seed_inbox(wiki / INBOX, "unrelated.md")

    monkeypatch.setattr(
        retention,
        "check_retention",
        lambda before_dir, after_wiki, judge_fn=None: _confirmed_loss_result(),
    )

    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, source=src, report=report)

    out = capsys.readouterr().out
    assert rc == 0, "advisory mode must PROCEED on block_confirmed_loss"
    assert (wiki / SOURCES / "unrelated.md").exists(), "source must still archive"
    assert "GATE ADVISORY" in out
    # The affected claim/reason is surfaced in the advisory.
    assert any("SILENTLY_LOST" in a for a in report.advisories)
    assert any("unrelated.md" in a for a in report.advisories), (
        "the advisory must name the affected source"
    )
    # A successful grader run (even one that found loss) resets the counter --
    # unchanged mechanics.
    assert retention.load_failure_counter(wiki) == 0


# ---------------------------------------------------------------------------
# (c) an advisory-fired run is DISTINGUISHABLE from a clean run at RUN LEVEL
# ---------------------------------------------------------------------------


def test_advisory_run_distinguishable_from_clean_run(tmp_path: Path, capsys) -> None:
    # Clean run: no version pair, nothing fires.
    clean_wiki = _make_wiki(tmp_path / "clean")
    src = _seed_inbox(clean_wiki / INBOX, "s.md")
    clean_report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc_clean = lib.ingest(clean_wiki, source=src, report=clean_report)
    clean_out = capsys.readouterr().out

    # Advisory run: identical except the wiki carries the version-page pair.
    adv_wiki = _make_wiki_with_version_pages(tmp_path / "adv")
    src2 = _seed_inbox(adv_wiki / INBOX, "s.md")
    adv_report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc_adv = lib.ingest(adv_wiki, source=src2, report=adv_report)
    adv_out = capsys.readouterr().out

    # Same (non-blocking) exit code either way...
    assert rc_clean == 0 and rc_adv == 0
    # ...but the run-level signals differ: machine-readable field...
    assert clean_report.advisories == []
    assert adv_report.advisories, "advisory run must populate DrainReport.advisories"
    # ...and the run-level log markers (fire line + end-of-run block).
    assert "GATE ADVISORY" not in clean_out
    assert "GATE ADVISORY" in adv_out
    assert "gate advisory(ies) fired this run" in adv_out
    assert "gate advisory(ies) fired this run" not in clean_out


def test_drain_path_advisory_fires_once_and_run_proceeds(
    tmp_path: Path, capsys
) -> None:
    """DRAIN-path wiring: the wiki-structural duplicate-page advisory is
    surfaced at run level (deduped -- not repeated per source) and every
    source still archives."""
    wiki = _make_wiki_with_version_pages(tmp_path)
    _seed_inbox(wiki / INBOX, "s1.md")
    _seed_inbox(wiki / INBOX, "s2.md")
    report = DrainReport()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, report=report)

    out = capsys.readouterr().out
    assert rc == 0
    assert (wiki / SOURCES / "s1.md").exists()
    assert (wiki / SOURCES / "s2.md").exists()
    assert not list((wiki / INBOX).glob("*.md")), "inbox must fully drain"
    dup_advisories = [a for a in report.advisories if "duplicate-page" in a]
    assert len(dup_advisories) == 1, (
        "identical wiki-structural advisory must be deduped at run level, "
        f"got: {report.advisories}"
    )
    assert "GATE ADVISORY" in out
    assert "gate advisory(ies) fired this run" in out


# ---------------------------------------------------------------------------
# (d) escape hatch: WIKI_WEAVER_ENFORCE_GATES=1 restores the OLD blocking
# ---------------------------------------------------------------------------


def test_enforce_env_restores_duplicate_blocking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki = _make_wiki_with_version_pages(tmp_path)
    src = _seed_inbox(wiki / INBOX, "unrelated.md")

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, source=src)

    assert rc == 1, "enforce mode must restore the old hard block (exit 1)"
    assert not (wiki / SOURCES / "unrelated.md").exists(), (
        "enforce mode must NOT archive a duplicate-blocked source"
    )


def test_enforce_env_restores_retention_blocking(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text(
        "# Page\n\nOriginal grounded claim.\n", encoding="utf-8"
    )
    src = _seed_inbox(wiki / INBOX, "unrelated.md")

    monkeypatch.setattr(
        retention,
        "check_retention",
        lambda before_dir, after_wiki, judge_fn=None: _confirmed_loss_result(),
    )

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_mock_run_inner):
        rc = lib.ingest(wiki, source=src)

    assert rc == 1, "enforce mode must restore the old hard block (exit 1)"
    assert not (wiki / SOURCES / "unrelated.md").exists(), (
        "enforce mode must NOT archive a retention-blocked source"
    )
