# pyright: reportMissingImports=false
"""Pure-unit tests for wiki_weaver/run_result.py (no engine deps needed).

THE CONTRACT UNDER TEST (see wiki_weaver/run_result.py's module docstring):
verdict derivation, the exit-code mapping, and -- above all -- THE INVARIANT:
``total > 0`` with ``converged == 0`` must NEVER yield verdict ``converged``
(or ``partial``) and must NEVER map to exit 0. This is the load-bearing fix
for the incident where a run with 0/17 converged exited 0 and looked healthy
for a week.

Also covers the fail-soft write path: a result.json write failure logs a
warning and returns None -- it never raises, never changes the run outcome.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wiki_weaver.run_result import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_EMPTY,
    EXIT_ERRORED,
    EXIT_FAILED,
    EXIT_OK,
    VERDICT_BLOCKED,
    VERDICT_CONVERGED,
    VERDICT_EMPTY,
    VERDICT_ERRORED,
    VERDICT_FAILED,
    VERDICT_PARTIAL,
    build_result,
    compute_verdict,
    counts_from_statuses,
    exit_code_for,
    summary_line,
    write_result_json,
)


def _counts(total=0, converged=0, failed=0, blocked=0, errored=0, skipped=0):
    return {
        "total": total,
        "converged": converged,
        "failed": failed,
        "blocked": blocked,
        "errored": errored,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# Verdict matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "blocked", "errored", "expected"),
    [
        # all converged
        (_counts(total=4, converged=4), [], [], VERDICT_CONVERGED),
        # some converged, some failed
        (_counts(total=4, converged=2, failed=2), [], [], VERDICT_PARTIAL),
        # attempted, nothing converged
        (_counts(total=17, failed=17), [], [], VERDICT_FAILED),
        # nothing attempted at all
        (_counts(), [], [], VERDICT_EMPTY),
        # nothing attempted, only already-ingested duplicates
        (_counts(skipped=3), [], [], VERDICT_EMPTY),
        # per-source gate blocks (counts) dominate failed/partial
        (_counts(total=3, converged=2, blocked=1), [], [], VERDICT_BLOCKED),
        # run-level gate block RECORD dominates even with clean counts
        (
            _counts(total=2, converged=2),
            [{"gate": "duplicate-page", "scope": "wiki", "reason": "x"}],
            [],
            VERDICT_BLOCKED,
        ),
        # per-source engine errors dominate everything
        (_counts(total=3, converged=2, errored=1), [], [], VERDICT_ERRORED),
        # run-level error RECORD (e.g. re-weave failure) dominates everything
        (
            _counts(total=2, converged=2),
            [],
            [{"reason": "overview re-weave failed"}],
            VERDICT_ERRORED,
        ),
        # errored beats blocked (precedence)
        (
            _counts(total=2, blocked=1, errored=1),
            [{"gate": "claim-retention", "scope": "source", "reason": "x"}],
            [{"reason": "boom"}],
            VERDICT_ERRORED,
        ),
    ],
)
def test_verdict_matrix(counts, blocked, errored, expected) -> None:
    assert compute_verdict(counts, blocked, errored) == expected


def test_the_invariant_zero_converged_never_reads_as_success() -> None:
    """THE incident invariant: attempted > 0 && converged == 0 can never
    produce verdict converged/partial, and can never map to exit 0 --
    exhaustively, for every failure-shape mix."""
    for failed in range(0, 4):
        for blocked_n in range(0, 4):
            for errored_n in range(0, 4):
                total = failed + blocked_n + errored_n
                if total == 0:
                    continue
                counts = _counts(
                    total=total, failed=failed, blocked=blocked_n, errored=errored_n
                )
                verdict = compute_verdict(counts, [], [])
                assert verdict not in (VERDICT_CONVERGED, VERDICT_PARTIAL), (
                    f"0-converged run must never read as success: {counts}"
                )
                assert exit_code_for(verdict) != 0, (
                    f"0-converged run must never exit 0: {counts} -> {verdict}"
                )


# ---------------------------------------------------------------------------
# Exit-code contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verdict", "code"),
    [
        (VERDICT_CONVERGED, EXIT_OK),
        (VERDICT_PARTIAL, EXIT_OK),
        (VERDICT_ERRORED, EXIT_ERRORED),
        (VERDICT_EMPTY, EXIT_EMPTY),
        (VERDICT_BLOCKED, EXIT_BLOCKED),
        (VERDICT_FAILED, EXIT_FAILED),
    ],
)
def test_exit_code_contract(verdict: str, code: int) -> None:
    assert exit_code_for(verdict) == code


def test_exit_codes_are_the_documented_values() -> None:
    """The numeric values are the PUBLIC contract (cron wrappers key on
    them) -- pin them so a refactor can't silently renumber."""
    assert (EXIT_OK, EXIT_ERRORED, EXIT_EMPTY, EXIT_BLOCKED, EXIT_FAILED) == (
        0,
        1,
        3,
        4,
        5,
    )


def test_unknown_verdict_fails_safe() -> None:
    assert exit_code_for("garbage") != 0


# ---------------------------------------------------------------------------
# counts_from_statuses
# ---------------------------------------------------------------------------


def test_counts_from_statuses_buckets_and_total() -> None:
    counts = counts_from_statuses(
        [
            "converged",
            "converged",
            "not-converged",
            "binary",
            "tampered",
            "error",
            "retention-blocked",
            "duplicate-blocked",
            "skipped",
        ]
    )
    assert counts == {
        "total": 8,  # skipped is NOT an attempt
        "converged": 2,
        "failed": 3,  # not-converged + binary + tampered
        "blocked": 2,
        "errored": 1,
        "skipped": 1,
    }


def test_counts_unknown_status_fails_safe_as_failed() -> None:
    counts = counts_from_statuses(["mystery-status"])
    assert counts["failed"] == 1 and counts["total"] == 1


# ---------------------------------------------------------------------------
# summary_line -- the honest headline
# ---------------------------------------------------------------------------


def test_summary_lines_state_verdict_and_counts() -> None:
    conv = build_result("r", _counts(total=4, converged=4))
    assert summary_line(conv).startswith("ingest CONVERGED: 4/4")

    part = build_result("r", _counts(total=4, converged=2, failed=2))
    assert "ingest PARTIAL: 2/4" in summary_line(part)
    assert "2 failed" in summary_line(part)

    fail = build_result("r", _counts(total=17, failed=17))
    line = summary_line(fail)
    assert line.startswith("ingest FAILED: 0/17")
    assert "result.json" in line

    empty = build_result("r", _counts())
    assert summary_line(empty).startswith("ingest EMPTY")


def test_summary_line_blocked_names_the_gate() -> None:
    """The mislabel fix: a gate block's headline names the GATE, never a
    generic error line."""
    result = build_result(
        "r",
        _counts(total=1, blocked=1),
        blocked=[
            {
                "gate": "duplicate-page",
                "scope": "wiki",
                "reason": "wiki contains duplicate pages: gpt-5-1.md",
                "offending_items": ["gpt-5-1.md"],
            }
        ],
    )
    line = summary_line(result)
    assert line.startswith("ingest BLOCKED (enforce):")
    assert "duplicate-page" in line
    assert "[wiki]" in line


def test_summary_line_errored_carries_reason() -> None:
    result = build_result(
        "r", _counts(total=1, errored=1), errored=[{"reason": "engine boom"}]
    )
    line = summary_line(result)
    assert line.startswith("ingest ERRORED")
    assert "engine boom" in line


# ---------------------------------------------------------------------------
# write_result_json -- fail-soft by contract
# ---------------------------------------------------------------------------


def test_write_result_json_roundtrip(tmp_path: Path) -> None:
    result = build_result("run-1", _counts(total=1, converged=1))
    path = write_result_json(tmp_path / "run-1", result)
    assert path is not None and path.name == "result.json"
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["run_id"] == "run-1"
    assert loaded["verdict"] == VERDICT_CONVERGED
    assert loaded["counts"]["converged"] == 1
    assert loaded["advisories"] == []
    assert loaded["blocked"] == []
    assert loaded["errored"] == []


def test_write_result_json_failure_is_fail_soft(tmp_path: Path, capsys) -> None:
    """A write failure (run_dir path occupied by a FILE -> mkdir raises)
    must warn loudly and return None -- never raise."""
    blocker = tmp_path / "occupied"
    blocker.write_text("not a directory", encoding="utf-8")
    result = build_result("run-1", _counts(total=1, converged=1))
    path = write_result_json(blocker, result)
    assert path is None
    out = capsys.readouterr().out
    assert "WARNING" in out and "result.json" in out
