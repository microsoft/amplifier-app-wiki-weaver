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
         "status": "final",               // see STATUS below
         "verdict": "failed",             // see VERDICTS below
         "counts": {"total": 17, "converged": 0, "failed": 17,
                    "blocked": 0, "errored": 0, "skipped": 0},
         "advisories": [...],             // gate advisories (fired, did NOT block)
         "blocked":   [{"gate": ..., "scope": "source|wiki|run",
                        "reason": ..., "offending_items": [...]}, ...],
         "errored":   [{"reason": ...}, ...],
         "sources":   [{"name": ..., "status": ...}, ...]   // when known
       }

   **status** (additive; existing consumers keep reading verdict/counts):

   - ``"in_progress"`` -- the DRAIN path rewrites result.json (atomically:
     tmp file + os.replace in the same dir) after EVERY source completes,
     so a crash mid-drain leaves a machine-readable snapshot of everything
     finished so far instead of stranding hours of work invisibly. The
     mid-run ``verdict`` is the normal verdict rules applied to
     counts-so-far.
   - ``"final"`` -- the end-of-run write (all three wiring sites).
   - ``"superseded"`` -- a crashed run's snapshot that a later drain on the
     same wiki has adopted (see RESUME below); it also carries
     ``"resumed_by": "<run_id>"``. Never counts as final.

   **RESUME (crash-safe drain):** re-running the drain on the same wiki dir
   is the resume path -- no flag needed. On start, the drain adopts any
   ``status: "in_progress"`` result.json left under ``.wiki/runs/`` (only a
   crashed run can leave one: the per-wiki ingest pidlock excludes live
   concurrent ingests), folds its per-source records into the new run's
   accumulation, and marks the old snapshot ``superseded`` so it can never
   be double-adopted. The new run's result.json therefore reflects the
   COMBINED drain state across the crash.

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
import os
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


# result.json lifecycle states (additive "status" field -- existing consumers
# keep reading verdict/counts; see module docstring for the full semantics).
STATUS_IN_PROGRESS = "in_progress"
STATUS_FINAL = "final"
STATUS_SUPERSEDED = "superseded"


def build_result(
    run_id: str,
    counts: dict[str, int],
    *,
    advisories: list[str] | None = None,
    blocked: list[dict[str, Any]] | None = None,
    errored: list[dict[str, Any]] | None = None,
    failed: list[dict[str, Any]] | None = None,
    sources: list[dict[str, Any]] | None = None,
    status: str = STATUS_FINAL,
) -> dict[str, Any]:
    """Assemble the result.json payload (verdict derived, never passed in).

    ``status`` defaults to ``"final"`` so every existing end-of-run wiring
    site gains the field without changes; the drain's mid-run checkpoints
    pass ``"in_progress"``. The verdict is always the normal verdict rules
    applied to whatever counts are given (counts-so-far mid-run).

    ``failed`` (additive, may be empty) carries per-source detail for sources
    quarantined to ``.wiki/failed/``: ``{"source": ..., "reason": ...,
    "failure_kind": "no_verdict" | "judged_non_converged" | "unknown"}``.
    ``failure_kind`` answers ONE question -- was a convergence verdict ever
    rendered for the final cycle? ``no_verdict`` = the assess step never
    rendered one (e.g. it exhausted its child-session tool budget
    mid-verification -- the 2026-07 incident where 4/25 sources were
    quarantined invisibly); ``judged_non_converged`` = assess DID render
    refine verdicts and the cycle budget ran out; ``unknown`` = the question
    was not (or could not be) evaluated for this record. It never affects
    the verdict or counts -- observability only.
    """
    blocked = blocked or []
    errored = errored or []
    result: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "verdict": compute_verdict(counts, blocked, errored),
        "counts": counts,
        "advisories": list(advisories or []),
        "blocked": blocked,
        "errored": errored,
        "failed": list(failed or []),
    }
    if sources is not None:
        result["sources"] = sources
    return result


def write_result_json(run_dir: Path, result: dict[str, Any]) -> Path | None:
    """Write ``<run_dir>/result.json`` ATOMICALLY. FAIL-SOFT: never raises.

    Atomic by tmp-file-in-same-dir + ``os.replace``: the drain rewrites this
    file after every source, so a reader (or a SIGKILL) mid-write must never
    be able to observe a torn/partial JSON document -- it sees either the
    previous complete snapshot or the new one.

    An observability write failure logs a loud warning and returns ``None``;
    it must never break (or change the outcome of) the run itself.
    """
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "result.json"
        tmp = run_dir / ".result.json.tmp"
        tmp.write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
        )
        os.replace(tmp, path)
        return path
    except Exception as e:  # noqa: BLE001 -- fail-soft by contract (see docstring)
        print(
            f"! WARNING: could not write result.json to {run_dir} "
            f"({type(e).__name__}: {e}) -- run outcome is unaffected",
            flush=True,
        )
        return None


# ---------------------------------------------------------------------------
# Crash-safe drain resume (see RESUME in the module docstring)
# ---------------------------------------------------------------------------


def find_interrupted_runs(
    runs_dir: Path, *, exclude_run_id: str | None = None
) -> list[tuple[Path, dict[str, Any]]]:
    """Find crashed-run snapshots: ``ingest-*/result.json`` with status in_progress.

    Only a crashed run can leave one behind -- every live ingest holds the
    per-wiki pidlock, and every non-crash exit path finalizes its result.json
    (status ``final``). Returned oldest-first (run dirs are timestamp-named).
    Unreadable/unparseable files are skipped (fail-soft: resume is an
    observability nicety, never a reason to refuse a drain).
    """
    found: list[tuple[Path, dict[str, Any]]] = []
    try:
        candidates = sorted(runs_dir.glob("ingest-*/result.json"))
    except OSError:
        return found
    for path in candidates:
        if exclude_run_id is not None and path.parent.name == exclude_run_id:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("status") == STATUS_IN_PROGRESS:
            found.append((path.parent, data))
    return found


def mark_superseded(run_dir: Path, result: dict[str, Any], resumed_by: str) -> None:
    """Flip an adopted crashed-run snapshot to status superseded (fail-soft).

    Prevents double-adoption: the NEW run's own (already-written) snapshot now
    carries these records, so the old snapshot must never be adopted again.
    ``resumed_by`` records the adopting run's id for the audit trail.
    """
    updated = dict(result)
    updated["status"] = STATUS_SUPERSEDED
    updated["resumed_by"] = resumed_by
    write_result_json(run_dir, updated)


def merge_source_records(
    base: list[dict[str, Any]], updates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge per-source records by name -- the resume's combined-state rule.

    ``base`` is the adopted pre-crash records; ``updates`` this run's own.
    Later records for the same source name replace earlier ones (a real
    re-attempt's disposition wins), with ONE exception: a ``skipped`` record
    never overwrites an existing one. ``skipped`` means "the ledger already
    has this source" -- the ledger-wins rule -- so the pre-crash record that
    put it in the ledger (e.g. ``converged``) is the informative one.
    Insertion order is preserved.
    """
    merged: dict[str, dict[str, Any]] = {}
    for rec in [*base, *updates]:
        name = str(rec.get("name", ""))
        if name in merged and rec.get("status") == "skipped":
            continue
        merged[name] = rec
    return list(merged.values())


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
