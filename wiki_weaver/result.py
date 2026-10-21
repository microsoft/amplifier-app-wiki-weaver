"""result.json and the exit-code contract for `wiki-weaver ingest`.

Shape read by the Resolve weaver resolver (progress.py / resolver.py::_classify):
counts.{total, converged, failed, blocked, errored, skipped}; ``total`` excludes skipped.
``total`` and ``converged`` are mirrored at top level. Exit codes follow V1's table.
"""

from __future__ import annotations

import json
from pathlib import Path

from .lib import atomic_write_text

EXIT_OK = 0
EXIT_ERRORED = 1
EXIT_USAGE = 2
EXIT_EMPTY = 3
EXIT_BLOCKED = 4
EXIT_FAILED = 5
EXIT_LOCKED = 75


def counts_for(rows: list[dict], errored: list[dict], blocked: list[dict]) -> dict[str, int]:
    conv = sum(1 for r in rows if r.get("status") == "converged")
    failed = sum(1 for r in rows if r.get("status") == "failed")
    skipped = sum(1 for r in rows if r.get("status") == "skipped")
    c = {
        "converged": conv,
        "failed": failed,
        "blocked": len(blocked),
        "errored": len(errored),
        "skipped": skipped,
    }
    c["total"] = conv + failed + c["blocked"] + c["errored"]
    return c


def verdict_for(c: dict[str, int]) -> str:
    if c["errored"]:
        return "errored"
    if c["blocked"]:
        return "blocked"
    if c["total"] == 0:
        return "empty"
    if c["converged"] == 0:
        return "failed"
    return "converged" if c["converged"] == c["total"] else "partial"


EXIT_FOR_VERDICT = {
    "converged": EXIT_OK,
    "partial": EXIT_OK,
    "errored": EXIT_ERRORED,
    "blocked": EXIT_BLOCKED,
    "empty": EXIT_EMPTY,
    "failed": EXIT_FAILED,
}


# errored[] entries are run-level problems, not sources: each carries one of these kinds.
ERRORED_KINDS = frozenset({"preflight", "recovery", "usage", "engine", "interrupted"})

# failed[].errors keeps at most this many entries; `reason` stays the short summary.
ERRORS_CAP = 50


def _errors(row: dict) -> list[str]:
    """The full list behind a failed row's `reason`: every check finding for a
    `checks_failed` hold, otherwise the single cause. Never longer than ERRORS_CAP."""
    errs = row.get("errors") or ([row["reason"]] if row.get("reason") else [])
    return [str(e) for e in errs][:ERRORS_CAP]


def build(
    *,
    run_id: str,
    status: str,
    rows: list[dict],
    errored: list[dict],
    blocked: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    blocked = blocked or []
    c = counts_for(rows, errored, blocked)
    res = {
        "run_id": run_id,
        "status": status,
        "verdict": verdict_for(c),
        "counts": c,
        "total": c["total"],
        "converged": c["converged"],
        "advisories": [],
        "blocked": blocked,
        "errored": errored,
        "failed": [
            {
                "source": r["source"],
                "reason": r.get("reason", ""),
                "failure_kind": r.get("failure_kind", ""),
                "errors": _errors(r),
            }
            for r in rows
            if r.get("status") == "failed"
        ],
        "sources": [
            {
                "name": r["source"],
                "status": r.get("status", ""),
                "compactions": r.get("compactions") or {},
                "ingress_truncations": r.get("ingress_truncations") or {},
            }
            for r in rows
        ],
    }
    res.update(extra or {})
    return res


def write(path: Path, data: dict) -> None:
    """Atomic replace: readers polling mid-run never see a torn file."""
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# The keys build() derives from its rows and lists; everything else in a record is `extra`.
BUILD_KEYS = frozenset(
    {
        "run_id",
        "status",
        "verdict",
        "counts",
        "total",
        "converged",
        "advisories",
        "blocked",
        "errored",
        "failed",
        "sources",
    }
)


def refresh(path: Path, rows: list[dict]) -> dict | None:
    """Re-derive the row-driven fields of the in-progress record at ``path`` - counts,
    verdict, failed[], sources[] - from ``rows`` and rewrite it atomically, keeping the
    rest of the record (started, model_calls, lens_page_types, ...) as the CLI last wrote
    it. Called after every source, so a progress panel polling the file sees each source
    land, not only each batch. None, and nothing written, when there is no in-progress
    record there: a final record is never touched.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("status") != "in_progress":
        return None
    out = build(
        run_id=str(data.get("run_id", "")),
        status="in_progress",
        rows=rows,
        errored=list(data.get("errored") or []),
        blocked=list(data.get("blocked") or []),
        extra={k: v for k, v in data.items() if k not in BUILD_KEYS},
    )
    write(path, out)
    return out
