"""result.json and the exit-code contract for `wiki-weaver ingest`.

Shape read by the Resolve weaver resolver (progress.py / resolver.py::_classify):
counts.{total, converged, failed, blocked, errored, skipped}; ``total`` excludes skipped.
``total`` and ``converged`` are mirrored at top level. Exit codes follow V1's table.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

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
            }
            for r in rows
            if r.get("status") == "failed"
        ],
        "sources": [{"name": r["source"], "status": r.get("status", "")} for r in rows],
    }
    res.update(extra or {})
    return res


def write(path: Path, data: dict) -> None:
    """Atomic replace: readers polling mid-run never see a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
