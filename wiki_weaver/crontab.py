"""Multi-instance managed crontab-block text ops + crontab subprocess wrappers.

Two layers: pure text ops (fully unit-testable, no subprocess) and thin
subprocess wrappers around the real ``crontab`` binary. Adapted from
medium-tools' ``schedule.py`` marker-block convention, changed from a single
global block to **per-instance blocks** keyed by instance id -- N independent
wikis coexist in one user crontab, each block independently upsert/removable
without disturbing the others.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "marker_begin",
    "marker_end",
    "ManagedBlock",
    "scan",
    "scan_malformed",
    "upsert_block",
    "remove_block",
    "crontab_available",
    "read_current_crontab",
    "write_crontab",
    "resolve_binary",
    "build_cron_line",
]

_MARKER_BEGIN = "# >>> wiki-weaver:{id} >>>"
_MARKER_END = "# <<< wiki-weaver:{id} <<<"

_BEGIN_RE = re.compile(r"^# >>> wiki-weaver:(?P<id>[^\s>]+) >>>$", re.MULTILINE)
_END_RE = re.compile(r"^# <<< wiki-weaver:(?P<id>[^\s>]+) <<<$", re.MULTILINE)


def marker_begin(instance_id: str) -> str:
    return _MARKER_BEGIN.format(id=instance_id)


def marker_end(instance_id: str) -> str:
    return _MARKER_END.format(id=instance_id)


# ---------------------------------------------------------------------------
# Pure text ops (multi-instance)
# ---------------------------------------------------------------------------


@dataclass
class ManagedBlock:
    instance_id: str
    cron_line: str  # the single cron entry line inside the block (schedule + command)
    block_text: str  # full block incl. both markers


def _scan_markers(text: str) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Collect all begin/end marker line-start offsets, keyed by instance id."""
    begins: dict[str, list[int]] = {}
    for m in _BEGIN_RE.finditer(text):
        begins.setdefault(m.group("id"), []).append(m.start())
    ends: dict[str, list[int]] = {}
    for m in _END_RE.finditer(text):
        ends.setdefault(m.group("id"), []).append(m.start())
    return begins, ends


def scan(text: str) -> dict[str, ManagedBlock]:
    """Return every well-formed wiki-weaver managed block, keyed by instance_id.

    Discovers instance ids by regex over begin markers. For each discovered id,
    locates its matching begin/end pair. A block is well-formed iff exactly one
    begin and one matching end exist, begin precedes end. Malformed blocks
    (orphan/duplicate/misordered markers for a given id) are SKIPPED here (see
    ``scan_malformed`` for the companion diagnostic).
    """
    begins, ends = _scan_markers(text)
    result: dict[str, ManagedBlock] = {}
    for iid, begin_positions in begins.items():
        end_positions = ends.get(iid, [])
        if len(begin_positions) != 1 or len(end_positions) != 1:
            continue
        b_start = begin_positions[0]
        e_start = end_positions[0]
        if b_start >= e_start:
            continue
        b_line_end = (
            text.index("\n", b_start) + 1 if "\n" in text[b_start:] else len(text)
        )
        end_line = marker_end(iid)
        e_line_end = e_start + len(end_line)
        block_text = text[b_start : min(e_line_end + 1, len(text))]
        # Body between begin-line-end and the end marker's line start.
        body = text[b_line_end:e_start]
        cron_line = body.strip("\n")
        if not cron_line:
            continue
        result[iid] = ManagedBlock(
            instance_id=iid, cron_line=cron_line, block_text=block_text
        )
    return result


def scan_malformed(text: str) -> dict[str, str]:
    """id -> human reason, for any id whose markers are orphaned/duplicated/misordered.

    Callers refuse to mutate a malformed id's block and tell the user to inspect
    ``crontab -e``. (Same four malformed conditions medium-tools.parse_crontab
    detects, scoped per-id.)
    """
    begins, ends = _scan_markers(text)
    all_ids = set(begins) | set(ends)
    malformed: dict[str, str] = {}
    for iid in all_ids:
        b = begins.get(iid, [])
        e = ends.get(iid, [])
        if len(b) == 0:
            malformed[iid] = "end marker with no matching begin marker"
        elif len(e) == 0:
            malformed[iid] = "begin marker with no matching end marker"
        elif len(b) > 1 or len(e) > 1:
            malformed[iid] = "duplicate begin/end markers"
        elif b[0] >= e[0]:
            malformed[iid] = "end marker appears before begin marker"
    return malformed


def _render_block(instance_id: str, cron_line: str) -> str:
    return f"{marker_begin(instance_id)}\n{cron_line}\n{marker_end(instance_id)}\n"


def upsert_block(text: str, instance_id: str, cron_line: str) -> str:
    """Insert or replace ONLY this instance's block; leave all other text (incl.
    other wiki-weaver instances and unrelated cron entries) byte-for-byte intact.

    Rendered block: "{begin}\\n{cron_line}\\n{end}\\n". If absent, append (ensuring
    a single newline separator). Raise ValueError if this id's existing block is
    malformed.
    """
    malformed = scan_malformed(text)
    if instance_id in malformed:
        raise ValueError(
            f"crontab block for instance {instance_id!r} is malformed "
            f"({malformed[instance_id]}); inspect `crontab -e`"
        )

    blocks = scan(text)
    new_block = _render_block(instance_id, cron_line)

    if instance_id in blocks:
        old_block_text = blocks[instance_id].block_text
        return text.replace(old_block_text, new_block, 1)

    # Absent -- append, ensuring a single newline separator.
    if text and not text.endswith("\n"):
        text += "\n"
    return text + new_block


def remove_block(text: str, instance_id: str) -> str:
    """Remove ONLY this instance's block. Idempotent (no block -> unchanged).
    Raise ValueError if this id's block is malformed (tell the user to repair
    manually).
    """
    malformed = scan_malformed(text)
    if instance_id in malformed:
        raise ValueError(
            f"crontab block for instance {instance_id!r} is malformed "
            f"({malformed[instance_id]}); inspect `crontab -e`"
        )

    blocks = scan(text)
    if instance_id not in blocks:
        return text
    return text.replace(blocks[instance_id].block_text, "", 1)


# ---------------------------------------------------------------------------
# Subprocess wrappers & command construction
# ---------------------------------------------------------------------------


def crontab_available() -> bool:
    return shutil.which("crontab") is not None


def read_current_crontab() -> str:
    """`crontab -l`; rc!=0 -> "" (no crontab yet)."""
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def write_crontab(text: str) -> None:
    """`crontab -` via stdin; RuntimeError on rc!=0."""
    result = subprocess.run(
        ["crontab", "-"], input=text, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"crontab write failed (rc={result.returncode}): {result.stderr}"
        )


def resolve_binary() -> str:
    """`shutil.which("wiki-weaver")`; FileNotFoundError if absent."""
    binary = shutil.which("wiki-weaver")
    if binary is None:
        raise FileNotFoundError(
            "wiki-weaver binary not found on PATH; cannot construct a cron command"
        )
    return binary


def build_cron_line(
    *, cron_expr: str, binary_path: str, canonical_wiki: Path, env_file: Path | None
) -> str:
    """Build the full cron entry line.

    Base command (path-escaped -- must-fix #7):
        {binary_path} schedule run-now --wiki {shlex.quote(str(canonical_wiki))}
    If env_file is not None (D7), wrap so cron sources the key first:
        bash -lc {shlex.quote(f"set -a; source {shlex.quote(str(env_file))}; {base}")}
    Return: f"{cron_expr} {command}".
    """
    base = f"{binary_path} schedule run-now --wiki {shlex.quote(str(canonical_wiki))}"
    if env_file is not None:
        inner = f"set -a; source {shlex.quote(str(env_file))}; {base}"
        command = f"bash -lc {shlex.quote(inner)}"
    else:
        command = base
    return f"{cron_expr} {command}"
