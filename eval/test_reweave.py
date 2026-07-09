"""Unit tests for the bounded, gated overview re-weave (Item 2).

Covers ``reweave_overview_if_needed`` (eval/grade_wiki.grade_overview + a
bounded retry loop over an injectable re-weave call) with FAKE/mocked
grade_fn / reweave_fn -- no real wiki, no real LLM, no network access.

Also covers the pipeline wiring: ``wiki_weaver.lib.ingest()``'s drain path
calls the gate exactly ONCE after the entire inbox drain completes, never
once per source.

SAFETY: no real API calls; all LLM-shaped calls are mocked/faked.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# reweave.py imports wiki_weaver.engine_runner (attractor engine deps) at
# module load time. Skip cleanly in lightweight CI (no @main resolution)
# rather than erroring -- matches test_ingest_drain.py / test_claim_retention.py.
pytest.importorskip("wiki_weaver.engine_runner")

from wiki_weaver.reweave import ReweaveGateResult, reweave_overview_if_needed  # noqa: E402


def _fake_grade(passed: bool, report: str = "") -> SimpleNamespace:
    """Minimal stand-in for GradeResult -- only .passed / .report() are used."""
    return SimpleNamespace(passed=passed, report=lambda: report or f"passed={passed}")


# ---------------------------------------------------------------------------
# Case 1 -- initial PASS: zero re-weave calls, zero attempts.
# ---------------------------------------------------------------------------


def test_gate_noop_when_already_passing(tmp_path: Path) -> None:
    """A passing overview.md must short-circuit -- no re-weave call at all."""
    grade_calls: list[Path] = []
    reweave_calls: list[Path] = []

    def grade_fn(wiki_dir: Path) -> SimpleNamespace:
        grade_calls.append(wiki_dir)
        return _fake_grade(True, "PASS -- already a synthesized map")

    def reweave_fn(wiki_dir: Path) -> None:
        reweave_calls.append(wiki_dir)

    result = reweave_overview_if_needed(
        tmp_path, max_retries=2, grade_fn=grade_fn, reweave_fn=reweave_fn
    )

    assert isinstance(result, ReweaveGateResult)
    assert result.initial_passed is True
    assert result.final_passed is True
    assert result.attempts == 0
    assert not reweave_calls, (
        "reweave_fn must NOT be called when the gate already passes"
    )
    assert len(grade_calls) == 1, (
        "grade_fn should be called exactly once (the initial grade)"
    )


# ---------------------------------------------------------------------------
# Case 2 -- initial FAIL, re-weave call produces a PASSing result.
# ---------------------------------------------------------------------------


def test_gate_reweaves_once_then_passes(tmp_path: Path) -> None:
    """One failing grade, one re-weave call that fixes it -> 1 attempt, pass."""
    grade_call_count = [0]
    reweave_calls: list[Path] = []

    def grade_fn(wiki_dir: Path) -> SimpleNamespace:
        grade_call_count[0] += 1
        # First call (initial grade): FAIL. Second call (post-reweave): PASS.
        if grade_call_count[0] == 1:
            return _fake_grade(False, "FAIL -- 399 source-narration openers")
        return _fake_grade(True, "PASS -- synthesized map")

    def reweave_fn(wiki_dir: Path) -> None:
        reweave_calls.append(wiki_dir)

    result = reweave_overview_if_needed(
        tmp_path, max_retries=2, grade_fn=grade_fn, reweave_fn=reweave_fn
    )

    assert result.initial_passed is False
    assert result.attempts == 1, "must stop re-weaving as soon as the gate passes"
    assert result.final_passed is True
    assert len(reweave_calls) == 1
    assert grade_call_count[0] == 2, (
        "one initial grade + one re-grade after the reweave"
    )


# ---------------------------------------------------------------------------
# Case 3 -- initial FAIL, re-weave calls keep FAILing -> bounded, fail-loud.
# ---------------------------------------------------------------------------


def test_gate_exhausts_retries_and_fails_loud(tmp_path: Path) -> None:
    """A persistently failing gate stops at max_retries and reports failure clearly."""
    reweave_calls: list[Path] = []

    def grade_fn(wiki_dir: Path) -> SimpleNamespace:
        return _fake_grade(False, "FAIL -- still a per-source narration log")

    def reweave_fn(wiki_dir: Path) -> None:
        reweave_calls.append(wiki_dir)

    result = reweave_overview_if_needed(
        tmp_path, max_retries=2, grade_fn=grade_fn, reweave_fn=reweave_fn
    )

    assert result.initial_passed is False
    assert result.attempts == 2, (
        "must stop at exactly max_retries, never loop unbounded"
    )
    assert result.final_passed is False, "must NOT silently report success"
    assert len(reweave_calls) == 2
    assert "FAIL" in result.final_report, (
        "failure must be clearly reported, not swallowed"
    )


# ---------------------------------------------------------------------------
# Case 4 -- pipeline wiring: the gate runs ONCE per drain, not once per source.
# ---------------------------------------------------------------------------


def test_ingest_drain_calls_reweave_gate_exactly_once(tmp_path: Path) -> None:
    """wiki_weaver.lib.ingest()'s drain path invokes the gate exactly once,
    AFTER the whole _inbox/ has been drained -- never per source.
    """
    from wiki_weaver.cli import FAILED, INBOX, SOURCES, cmd_ingest  # noqa: E402

    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    del FAILED  # imported only for parity with test_ingest_drain.py's import shape

    # Seed two sources so the drain loop runs multiple times.
    import os
    import time

    for name in ("a.md", "b.md"):
        p = wiki / INBOX / name
        p.write_text(f"# {name}\n\nUnique body for {name}.\n", encoding="utf-8")
        old = time.time() - 10
        os.utime(p, (old, old))

    def mock_run(src, wiki_dir, max_cycles, source_id):
        return SimpleNamespace(
            converged=True,
            status="success",
            failure_reason=None,
            logs_dir=Path("/tmp/fake_logs"),
        )

    gate_calls: list[Path] = []

    def mock_gate(wiki_dir, max_retries=2, **_kw):
        gate_calls.append(Path(wiki_dir))
        return ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="PASS",
            final_report="PASS",
        )

    args = argparse.Namespace(
        wiki=str(wiki),
        source=None,
        max_cycles=None,
        keep_going=False,
        limit=None,
    )

    with (
        patch("wiki_weaver.cli.preflight", lambda **_kw: []),
        patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run),
        patch("wiki_weaver.reweave.reweave_overview_if_needed", side_effect=mock_gate),
    ):
        rc = cmd_ingest(args)

    assert rc == 0
    assert len(gate_calls) == 1, (
        f"reweave gate must run exactly once per full drain, not per source; "
        f"got {len(gate_calls)} calls"
    )
    assert gate_calls[0] == wiki.resolve()
