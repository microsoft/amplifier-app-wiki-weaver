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
from enum import StrEnum
from pathlib import Path

from .lib import MAX_SOURCE_CHARS, atomic_append_line, wiki_inbox, wiki_ledger
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


class FailureKind(StrEnum):
    """failure_kind vocabulary (V4): where a source's path stopped and, for a model step,
    why. One closed set; make_row refuses anything else (contracts/cli-surface.v1.md, 12)."""

    OVERSIZED = "oversized"
    OVERSIZED_CONTEXT = "oversized_context"
    EMPTY = "empty_source"
    CHECKS = "checks_failed"
    MODEL_TIMEOUT = "model_timeout"
    MODEL_AUTH = "model_auth_error"
    MODEL_PROVIDER = "model_provider_error"  # 429, 529, other 5xx
    MODEL_CONNECTION = "model_connection_error"
    MODEL_STEP = "model_step_failed"  # the engine recorded no usable cause
    UNKNOWN = "unknown"


FAILURE_KINDS = frozenset(k.value for k in FailureKind)
KIND_OVERSIZED = FailureKind.OVERSIZED.value
KIND_OVERSIZED_CONTEXT = FailureKind.OVERSIZED_CONTEXT.value
KIND_EMPTY = FailureKind.EMPTY.value
KIND_CHECKS = FailureKind.CHECKS.value
KIND_MODEL_TIMEOUT = FailureKind.MODEL_TIMEOUT.value
KIND_MODEL_AUTH = FailureKind.MODEL_AUTH.value
KIND_MODEL_PROVIDER = FailureKind.MODEL_PROVIDER.value
KIND_MODEL_CONNECTION = FailureKind.MODEL_CONNECTION.value
KIND_MODEL_STEP = FailureKind.MODEL_STEP.value
KIND_UNKNOWN = FailureKind.UNKNOWN.value


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
    if failure_kind and failure_kind not in FAILURE_KINDS:
        raise ValueError(f"failure_kind {failure_kind!r} is not in FailureKind")
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
    atomic_append_line(wiki_ledger(wiki), json.dumps(row, ensure_ascii=False))


def eligible(wiki: Path) -> list[Path]:
    """Inbox sources this wiki has not yet dealt with, oldest content first.

    Excluded: content already converged, under any name (a duplicate is nothing to do).
    A held source dropped back into _inbox/ is retried, as in V1 (failed rows never count
    as processed); 0-byte and oversized sources leave _inbox/ when they are recorded.
    """
    inbox = wiki_inbox(wiki)
    if not inbox.is_dir():
        return []
    rows = read_rows(wiki)
    done = converged_hashes(rows)
    out = []
    for p in inbox.glob("*.md"):
        if not p.is_file():
            continue
        h = sha256_file(p)
        if h in done:
            continue
        out.append(p)
    out.sort(key=lambda p: (str(source_meta(p).get("date") or "9999"), p.name))
    return out


def is_oversized(path: Path) -> bool:
    return len(read_text(path)) > MAX_SOURCE_CHARS


def versions_dir(wiki: Path) -> Path:
    """Prior versions of edited sources, kept so `[s<id>: ...]` keeps resolving."""
    return Path(wiki) / ".wiki" / "source-versions"


def source_versions(wiki: Path) -> dict[int, tuple[str, str]]:
    """source_id -> (filename, text of that exact version), for every resolvable id.

    The current version lives in _sources/<name>; an earlier version of an edited source
    lives in .wiki/source-versions/s<id>.md.
    """
    wiki = Path(wiki)
    by_id: dict[int, tuple[str, str]] = {}
    for r in read_rows(wiki):
        sid = r.get("source_id")
        if isinstance(sid, int) and sid not in by_id:
            by_id[sid] = (_name(r), _hash(r))
    hashes: dict[str, str] = {}
    out: dict[int, tuple[str, str]] = {}
    for sid, (name, h) in by_id.items():
        cur = wiki / "_sources" / name
        if cur.is_file():
            if name not in hashes:
                hashes[name] = sha256_file(cur)
            if hashes[name] == h:
                out[sid] = (name, read_text(cur))
                continue
        old = versions_dir(wiki) / f"s{sid}.md"
        if old.is_file():
            out[sid] = (name, read_text(old))
    return out


def known_sources(wiki: Path) -> dict[int, str]:
    """source_id -> filename for every id the ledger has assigned, whether or not that
    version's text is on disk (``source_versions`` lists the ones that are). A job that
    carries the ledger but not `_sources/` still knows which ids are real."""
    out: dict[int, str] = {}
    for r in read_rows(wiki):
        sid = r.get("source_id")
        if isinstance(sid, int) and sid not in out:
            out[sid] = _name(r)
    return out


def ids_for_source(wiki: Path, name: str) -> set[int]:
    return {
        r["source_id"]
        for r in read_rows(wiki)
        if _name(r) == name and isinstance(r.get("source_id"), int)
    }
