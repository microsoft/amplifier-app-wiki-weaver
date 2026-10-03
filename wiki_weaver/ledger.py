"""The ledger (``.wiki/.processed.jsonl``) and inbox eligibility.

Rows use V1's keys exactly -- RepoWeaver reads this file:
    source, source_id, hash, status, converged, reason, failure_kind, failed_to, timestamp
Done-ness is ``converged`` (bool), as in V1:
    {row["source"] for row in ledger if row.get("converged")}
V4-only keys ride alongside: pages_touched, model_calls, archived_to.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .lib import MAX_SOURCE_CHARS, wiki_inbox, wiki_ledger
from .sources import read_text, sha256_file, source_meta

V1_KEYS = (
    "source",
    "source_id",
    "hash",
    "status",
    "converged",
    "reason",
    "failure_kind",
    "failed_to",
    "timestamp",
)

STATUS_CONVERGED = "converged"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# failure_kind vocabulary (V4). V1's kinds described its convergence loop; these
# describe where V4's per-source path stopped.
KIND_OVERSIZED = "oversized"
KIND_CHECKS = "checks_failed"
KIND_MODEL_STEP = "model_step_failed"
KIND_EMPTY = "empty_source"
KIND_UNKNOWN = "unknown"


def read_rows(wiki: Path) -> list[dict]:
    p = wiki_ledger(wiki)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _hash(row: dict) -> str:
    return str(row.get("hash") or row.get("sha256") or "")


def _name(row: dict) -> str:
    return str(row.get("source") or row.get("filename") or "")


def processed_sources(wiki: Path) -> set[str]:
    """V1's done-ness rule, verbatim."""
    return {row.get("source", "") for row in read_rows(wiki) if row.get("converged")}


def converged_hashes(rows: list[dict]) -> set[str]:
    return {_hash(r) for r in rows if r.get("converged")}


def seen_keys(rows: list[dict]) -> set[tuple[str, str]]:
    return {(_name(r), _hash(r)) for r in rows}


def source_id_for(rows: list[dict], file_hash: str) -> int:
    """Stable integer id per content hash (V1 assigned ids by hash)."""
    ids = []
    for r in rows:
        sid = r.get("source_id")
        if isinstance(sid, int):
            if _hash(r) == file_hash:
                return sid
            ids.append(sid)
    return (max(ids) + 1) if ids else 1


def make_row(
    wiki: Path,
    *,
    source: str,
    file_hash: str,
    status: str,
    reason: str = "",
    failure_kind: str = "",
    failed_to: str = "",
    **extra,
) -> dict:
    row = {
        "source": source,
        "source_id": source_id_for(read_rows(wiki), file_hash),
        "hash": file_hash,
        "status": status,
        "converged": status == STATUS_CONVERGED,
        "reason": reason,
        "failure_kind": failure_kind,
        "failed_to": failed_to,
        "timestamp": datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005 (V1: local time)
    }
    row.update(extra)
    return row


def append_row(wiki: Path, row: dict) -> None:
    p = wiki_ledger(wiki)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def eligible(wiki: Path) -> list[Path]:
    """Inbox sources this wiki has not yet dealt with, oldest content first.

    Excluded: content already converged (under any name -- a duplicate is nothing to
    do) and a (name, hash) pair already ledgered.
    """
    inbox = wiki_inbox(wiki)
    if not inbox.is_dir():
        return []
    rows = read_rows(wiki)
    done, seen = converged_hashes(rows), seen_keys(rows)
    out = []
    for p in inbox.glob("*.md"):
        if not p.is_file():
            continue
        h = sha256_file(p)
        if h in done or (p.name, h) in seen:
            continue
        out.append(p)
    out.sort(key=lambda p: (str(source_meta(p).get("date") or "9999"), p.name))
    return out


def is_oversized(path: Path) -> bool:
    return len(read_text(path)) > MAX_SOURCE_CHARS
