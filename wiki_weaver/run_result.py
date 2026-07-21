"""Run-result contract for headless callers (result.json + verdicts + exit codes).

WHY THIS EXISTS (the motivating incident): a run where 0 of 17 sources
converged (every source quarantined to ``.wiki/failed/`` or blocked by a
gate) printed ``ingest done -- lint: PASS -- inbox remaining: 0`` and exited
0 -- it looked healthy for a week. Separately, a gate block that *did* exit 1
surfaced as a generic error line instead of naming the gate. Headless callers
(cron drains, the Team Pulse app) could not tell a good run from a dead one.

This module is the single home for the machine-first contract that fixes
that, shared by all three ingest wiring sites (``engine_runner.run_ingest()``
tool/agent path; ``lib.ingest()`` single-file and drain paths):

1. **result.json** -- one JSON file per ingest run, written into the run's
   logs dir (the same ``.wiki/runs/ingest-<ts>/`` dir where ``events.jsonl``
   goes). Shape::

       {
         "run_id": "ingest-20260721-113000",
         "verdict": "failed",             // see VERDICTS below
         "counts": {"total": 17, "converged": 0, "failed": 17,
                    "blocked": 0, "errored": 0, "skipped": 0},
         "advisories": [...],             // gate advisories (fired, did NOT block)
         "blocked":   [{"gate": ..., "scope": "source|wiki|run",
                        "reason": ..., "offending_items": [...]}, ...],
         "errored":   [{"reason": ...}, ...],
         "sources":   [{"name": ..., "status": ...}, ...]   // when known
       }

2. **Verdict rules** -- THE load-bearing invariant: ``total > 0`` (sources
   were attempted) with ``converged == 0`` must NEVER produce verdict
   ``converged`` (it becomes ``failed``/``blocked``/``errored``). ``empty``
   means nothing was attempted (empty inbox, or only already-ingested
   duplicates). ``partial`` means some but not all converged. Advisories
   alone never downgrade the verdict (advisory == the run proceeded).

3. **Exit-code contract** (returned by ``lib.ingest()`` and propagated by
   ``wiki-weaver ingest`` / ``schedule run-now``)::

       0  -- >=1 source converged AND no blocked AND no errored
             (verdicts: converged, partial; advisories allowed)
       1  -- engine/infrastructure error (verdict: errored)
       3  -- nothing to do (verdict: empty)
       4  -- gate-blocked in enforce mode (verdict: blocked)
       5  -- attempted > 0, converged == 0, no gate/infra cause
             (verdict: failed)

   The invariant in code: ``converged == 0`` with ``total > 0`` can never
   exit 0 -- ``compute_verdict()`` structurally cannot return ``converged``
   or ``partial`` in that case, and only those two verdicts map to exit 0.

4. **Honest run-summary line** -- ``summary_line()`` produces the terminal
   headline (verdict + counts + dominant reason) so "lint: PASS" can never
   again be the headline of a run where nothing converged.

All writing here is FAIL-SOFT: a result.json write failure logs a warning
and never breaks the run (observability must not jeopardize the primary
flow).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Verdicts + exit codes (the contract -- see module docstring)
# ---------------------------------------------------------------------------

VERDICT_CONVERGED = "converged"
VERDICT_PARTIAL = "partial"
VERDICT_FAILED = "failed"
VERDICT_BLOCKED = "blocked"
VERDICT_ERRORED = "errored"
VERDICT_EMPTY = "empty"

VERDICTS = (
    VERDICT_CONVERGED,
    VERDICT_PARTIAL,
    VERDICT_FAILED,
    VERDICT_BLOCKED,
    VERDICT_ERRORED,
    VERDICT_EMPTY,
)

EXIT_OK = 0  # converged | partial (>=1 converged, no blocked, no errored)
EXIT_ERRORED = 1  # engine/infrastructure error
EXIT_EMPTY = 3  # nothing to do (empty inbox / only duplicates)
EXIT_BLOCKED = 4  # gate-blocked (WIKI_WEAVER_ENFORCE_GATES=1)
EXIT_FAILED = 5  # attempted > 0, converged == 0, no gate/infra cause

_EXIT_FOR_VERDICT: dict[str, int] = {
    VERDICT_CONVERGED: EXIT_OK,
    VERDICT_PARTIAL: EXIT_OK,
    VERDICT_ERRORED: EXIT_ERRORED,
    VERDICT_EMPTY: EXIT_EMPTY,
    VERDICT_BLOCKED: EXIT_BLOCKED,
    VERDICT_FAILED: EXIT_FAILED,
}

# Per-source summary status -> counts bucket. "skipped" (already-ingested
# duplicate) is tracked but NOT part of ``total`` -- nothing was attempted
# for it, so it must not be able to mask a 0-converged run as partial, nor
# turn a nothing-to-do run into a failure.
_STATUS_BUCKET: dict[str, str] = {
    "converged": "converged",
    "error": "errored",
    "retention-blocked": "blocked",
    "duplicate-blocked": "blocked",
    "binary": "failed",
    "tampered": "failed",
    "not-converged": "failed",
    "skipped": "skipped",
}


def counts_from_statuses(statuses: Iterable[str]) -> dict[str, int]:
    """Fold per-source summary statuses into the counts dict.

    ``total`` = converged + failed + blocked + errored (real attempts).
    Unknown statuses are counted as ``failed`` -- fail-safe: an unclassified
    outcome must never be able to read as success.
    """
    counts = {
        "total": 0,
        "converged": 0,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    for status in statuses:
        bucket = _STATUS_BUCKET.get(status, "failed")
        counts[bucket] += 1
        if bucket != "skipped":
            counts["total"] += 1
    return counts


def compute_verdict(
    counts: dict[str, int],
    blocked: list[dict[str, Any]],
    errored: list[dict[str, Any]],
) -> str:
    """Derive the run verdict from counts + structured block/error records.

    Precedence: errored > blocked > empty > failed > partial > converged.
    ``blocked``/``errored`` records cover RUN-level causes that have no
    per-source count (e.g. run_ingest's post-hoc whole-drain gate check, or
    an overview re-weave failure), so both the counts and the record lists
    are consulted.

    THE INVARIANT: ``counts["total"] > 0 and counts["converged"] == 0``
    can never return ``converged`` or ``partial`` -- every branch below
    that could reach them requires ``converged > 0``.
    """
    if errored or counts.get("errored", 0) > 0:
        return VERDICT_ERRORED
    if blocked or counts.get("blocked", 0) > 0:
        return VERDICT_BLOCKED
    total = counts.get("total", 0)
    converged = counts.get("converged", 0)
    if total == 0:
        return VERDICT_EMPTY
    if converged == 0:
        return VERDICT_FAILED
    if converged < total:
        return VERDICT_PARTIAL
    return VERDICT_CONVERGED


def exit_code_for(verdict: str) -> int:
    """Map a verdict to the documented CLI exit code (see module docstring).

    Unknown verdicts map to ``EXIT_ERRORED`` -- fail-safe, never 0.
    """
    return _EXIT_FOR_VERDICT.get(verdict, EXIT_ERRORED)


# ---------------------------------------------------------------------------
# Result building / writing (fail-soft)
# ---------------------------------------------------------------------------


def build_result(
    run_id: str,
    counts: dict[str, int],
    *,
    advisories: list[str] | None = None,
    blocked: list[dict[str, Any]] | None = None,
    errored: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the result.json payload (verdict derived, never passed in)."""
    blocked = blocked or []
    errored = errored or []
    result: dict[str, Any] = {
        "run_id": run_id,
        "verdict": compute_verdict(counts, blocked, errored),
        "counts": counts,
        "advisories": list(advisories or []),
        "blocked": blocked,
        "errored": errored,
    }
    if sources is not None:
        result["sources"] = sources
    return result


def write_result_json(run_dir: Path, result: dict[str, Any]) -> Path | None:
    """Write ``<run_dir>/result.json``. FAIL-SOFT: never raises.

    An observability write failure logs a loud warning and returns ``None``;
    it must never break (or change the outcome of) the run itself.
    """
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "result.json"
        path.write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
        )
        return path
    except Exception as e:  # noqa: BLE001 -- fail-soft by contract (see docstring)
        print(
            f"! WARNING: could not write result.json to {run_dir} "
            f"({type(e).__name__}: {e}) -- run outcome is unaffected",
            flush=True,
        )
        return None


def _first_reason(records: list[dict[str, Any]], limit: int = 200) -> str:
    if not records:
        return ""
    reason = str(records[0].get("reason", ""))
    return reason[:limit] + ("..." if len(reason) > limit else "")


def summary_line(result: dict[str, Any]) -> str:
    """The honest one-line run headline: verdict + counts + dominant reason.

    This line -- not lint status, not "inbox remaining: 0" -- is the headline
    of every ingest run.
    """
    verdict = result["verdict"]
    c = result["counts"]
    if verdict == VERDICT_EMPTY:
        return "ingest EMPTY: no sources to ingest (nothing attempted)"
    if verdict == VERDICT_CONVERGED:
        return f"ingest CONVERGED: {c['converged']}/{c['total']} converged"
    if verdict == VERDICT_PARTIAL:
        return (
            f"ingest PARTIAL: {c['converged']}/{c['total']} converged, "
            f"{c['failed']} failed"
        )
    if verdict == VERDICT_BLOCKED:
        blocked = result.get("blocked") or []
        if blocked:
            b = blocked[0]
            head = (
                f"{b.get('gate', 'gate')} [{b.get('scope', 'run')}]: "
                f"{str(b.get('reason', ''))[:200]}"
            )
        else:
            head = f"{c['blocked']} source(s) gate-blocked"
        return f"ingest BLOCKED (enforce): {head} -- see result.json"
    if verdict == VERDICT_FAILED:
        return (
            f"ingest FAILED: {c['converged']}/{c['total']} converged -- see result.json"
        )
    # errored
    reason = _first_reason(result.get("errored") or [])
    suffix = f": {reason}" if reason else ""
    return f"ingest ERRORED{suffix} -- see result.json"


def finish_run(run_dir: Path, result: dict[str, Any]) -> int:
    """End-of-run wrap-up shared by every wiring site.

    Writes result.json (fail-soft), prints the honest headline + a one-line
    pointer at the file, and returns the contract exit code for the verdict.
    """
    path = write_result_json(run_dir, result)
    print(summary_line(result), flush=True)
    if path is not None:
        print(f"run result: {path}", flush=True)
    return exit_code_for(result["verdict"])
