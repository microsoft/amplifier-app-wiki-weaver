# pyright: reportMissingImports=false
"""Fail-route tool for the ingest.dot drain loop.

Called by the `fail_handler` tool node in ingest.dot when synthesize.dot
did NOT converge (outcome != success) or tamper was detected. Moves the
source file from _inbox/ to .wiki/failed/ so the inbox keeps shrinking and
the drain loop can continue, and appends a FAILURE record to the ledger
(.wiki/.processed.jsonl) -- symmetrical with the success record
ingest_archive.py writes on convergence.

WHY the ledger record (2026-07 incident): a 25-source production run
quarantined 4 sources and the ledger recorded only the 21 successes -- the
failures were INVISIBLE in .wiki/.processed.jsonl. The record carries a
``failure_kind`` distinguishing "no verdict was ever rendered"
(``no_verdict`` -- e.g. the assess child session exhausted its tool budget
mid-verification) from "assess judged it non-converged and the cycle budget
ran out" (``judged_non_converged``), via the pragmatic assessment-file-mtime
heuristic in wiki_weaver.lib.classify_failure_kind.

Reuses _collision_safe_move / classify_failure_kind / _append_failure_ledger
from wiki_weaver/lib.py -- no reimplementation.

Usage:
    python <this_file> <wiki_dir> <source_path> [<source_id>] [<started_at>]

    wiki_dir     -- the wiki root (contains .wiki/failed/, the ledger, etc.)
    source_path  -- absolute path to the source file in _inbox/
    source_id    -- (optional) stable integer id assigned by ingest_setup.py;
                    recorded in the ledger failure record.
    started_at   -- (optional) unix-seconds float: when THIS source's
                    synthesis started (emitted by ingest_setup.py). Used to
                    classify failure_kind; absent => "unknown".

Both extra args are optional so any older 2-arg fail_cmd keeps working.

Exits 0 on success (including the case where the source is already absent
-- idempotent so retries don't fail the whole pipeline; NO duplicate ledger
record is written on such a retry).
Exits non-zero on hard errors (bad args, missing wiki_dir).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            f"usage: {sys.argv[0]} <wiki_dir> <source_path> "
            f"[<source_id>] [<started_at>]",
            file=sys.stderr,
        )
        return 1

    wiki_dir = Path(sys.argv[1]).resolve()
    source_path = Path(sys.argv[2]).resolve()
    source_id: int | str = sys.argv[3] if len(sys.argv) > 3 else ""
    try:
        source_id = int(source_id)
    except (TypeError, ValueError):
        pass  # keep the raw string ("" when absent)
    started_at: float | None = None
    if len(sys.argv) > 4:
        try:
            started_at = float(sys.argv[4])
        except ValueError:
            started_at = None

    if not wiki_dir.is_dir():
        print(f"ERROR: wiki_dir not found: {wiki_dir}", file=sys.stderr)
        return 1

    from wiki_weaver.lib import (
        _append_failure_ledger,
        _collision_safe_move,
        _source_hash,
        classify_failure_kind,
        wiki_failed,
    )

    failed_dir = wiki_failed(wiki_dir)
    failed_dir.mkdir(parents=True, exist_ok=True)

    if not source_path.is_file():
        # Already moved or never existed -- idempotent, not an error.
        # No ledger record either: the first (real) quarantine wrote it, and
        # a retry must not double-record the same source.
        print(
            f"NOTE: source not found (already moved or never existed): {source_path}",
            file=sys.stderr,
        )
        return 0

    # Hash BEFORE moving so the failure record can carry it (symmetry with
    # the success record's content-hash field).
    file_hash = _source_hash(source_path)

    dest = _collision_safe_move(source_path, failed_dir)

    # Classify the failure kind from fail-path artifacts (see module
    # docstring), then record the failure in the ledger. Fail-soft: the
    # quarantine move above is the load-bearing act.
    failure_kind = classify_failure_kind(wiki_dir, started_at)
    _append_failure_ledger(
        wiki_dir,
        source=source_path.name,
        source_id=source_id,
        file_hash=file_hash,
        failed_to=str(dest),
        reason=(
            "synthesize.dot did not converge (or tamper was detected) -- "
            "routed to .wiki/failed/ by the ingest.dot fail_handler"
        ),
        failure_kind=failure_kind,
        logs_dir=_run_logs_dir(wiki_dir),
    )

    print(
        f"failed: {source_path.name} -> _failed/{dest.name} "
        f"(failure_kind={failure_kind})",
        file=sys.stderr,
    )
    return 0


def _run_logs_dir(wiki_dir: Path) -> str:
    """Best-effort resolution of the active ingest run's logs dir.

    Reuses ingest_archive._find_ingest_logs_dir (the same resolution the
    success record uses). Fail-soft: an empty string when no ingest-* run
    dir exists -- the failure record is still written.
    """
    try:
        from wiki_weaver.ingest_archive import _find_ingest_logs_dir

        return str(_find_ingest_logs_dir(wiki_dir))
    except (RuntimeError, OSError):
        return ""


if __name__ == "__main__":
    sys.exit(main())
