# pyright: reportMissingImports=false
"""wiki-weaver library API.

Importable concept-level functions that back the CLI and can be called
directly by other Python code (tests, the future attractor shim, etc.).
The CLI (wiki_weaver.py) is a thin argparse wrapper around these.

Public API
----------
init(wiki_dir)                      scaffold a fresh wiki
ingest(wiki, *, source, ...)        integrate inbox sources via the engine
lint(wiki)                          run the structural validator
doctor(*, wiki)                     environment diagnostics
ask(wiki, question, *, json_out)    answer a question from the compiled wiki

All functions print their own output (unchanged from the original cmd_*
behaviour) and return an integer exit code (0 = success).
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import NamedTuple

from ._assets import pipeline_dir

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

WIKI_DIR = ".wiki"  # hidden machine-only subtree root
LEDGER_NAME = ".processed.jsonl"
INBOX = "_inbox"
SOURCES = "_sources"  # was ARCHIVE; stays visible at corpus root
FAILED = "_failed"  # logical name; actual path via wiki_failed()
# Claim-retention gate's consecutive-grader-failure counter (fail-open/fail-closed
# escalation state) -- see wiki_retention_state() below and wiki_weaver/retention.py.
RETENTION_STATE_NAME = ".retention_gate_state.json"

# Pipeline assets resolve to the wheel sibling (wiki_weaver_pipeline/) on a real
# install or the repo-root pipeline/ in a dev tree -- see wiki_weaver._assets.
REPO_ROOT = Path(__file__).resolve().parent.parent
VALIDATE_PY = pipeline_dir() / "validate_wiki.py"


# ---------------------------------------------------------------------------
# Path helpers — single place that spells every machine/user path
# ---------------------------------------------------------------------------


def wiki_hidden_dir(wiki: Path) -> Path:
    """Return the hidden machine-only root: ``<wiki>/.wiki``."""
    return wiki / WIKI_DIR


def wiki_ledger(wiki: Path) -> Path:
    """Return the ledger path: ``<wiki>/.wiki/.processed.jsonl``."""
    return wiki / WIKI_DIR / LEDGER_NAME


def wiki_registry(wiki: Path) -> Path:
    """Return the source registry path: ``<wiki>/.wiki/.sources.json``."""
    return wiki / WIKI_DIR / REGISTRY_NAME  # REGISTRY_NAME defined below


def wiki_failed(wiki: Path) -> Path:
    """Return the failed-sources dir: ``<wiki>/.wiki/failed``."""
    return wiki / WIKI_DIR / "failed"


def wiki_retention_state(wiki: Path) -> Path:
    """Return the claim-retention gate's consecutive-grader-failure counter path:
    ``<wiki>/.wiki/.retention_gate_state.json``.

    Sibling to ``wiki_registry()`` (``.sources.json``) and the ledger -- same
    ``.wiki/`` process-state subtree, same small-JSON-file convention. See
    ``wiki_weaver/retention.py`` for the reader/writer and the fail-open/
    fail-closed escalation policy built on top of this counter.
    """
    return wiki / WIKI_DIR / RETENTION_STATE_NAME


def wiki_runs(wiki: Path) -> Path:
    """Return the run-logs dir: ``<wiki>/.wiki/runs``."""
    return wiki / WIKI_DIR / "runs"


def wiki_sources(wiki: Path) -> Path:
    """Return the archived-sources dir: ``<wiki>/_sources`` (visible)."""
    return wiki / SOURCES


def wiki_inbox(wiki: Path) -> Path:
    """Return the inbox dir: ``<wiki>/_inbox`` (visible)."""
    return wiki / INBOX


def wiki_dashboard(wiki: Path) -> Path:
    """Return the dashboard assets dir: ``<wiki>/.wiki/dashboard``."""
    return wiki / WIKI_DIR / "dashboard"


def wiki_policy_dir(wiki: Path) -> Path:
    """Return the per-wiki policy override dir: ``<wiki>/.wiki/policy``."""
    return wiki / WIKI_DIR / "policy"


def _ok(msg: str) -> None:
    print(f"{GREEN}\u2713{RESET} {msg}")


def _fail(msg: str) -> None:
    print(f"{RED}\u2717{RESET} {msg}")


def _warn(msg: str) -> None:
    print(f"{YELLOW}!{RESET} {msg}")


def _gate_advisory(gate: str, msg: str) -> None:
    """Loud, distinct, run-level gate-advisory line (the gate did NOT block)."""
    print(f"{YELLOW}!! GATE ADVISORY [{gate}]{RESET} {msg}")


def _print_advisories(advisories: list[str]) -> None:
    """Distinct end-of-run advisory block so an advisory-fired run can never
    present as byte-identical to a clean one (run-level, not buried per-source)."""
    if not advisories:
        return
    print(
        f"\n{YELLOW}!! {len(advisories)} gate advisory(ies) fired this run "
        f"(ADVISORY -- did NOT block any source):{RESET}"
    )
    for a in advisories:
        print(f"{YELLOW}   - {a}{RESET}")


# ---------------------------------------------------------------------------
# Obsidian-readiness helpers
# ---------------------------------------------------------------------------

# Marker line that wiki-weaver writes as the first line of its .gitignore block.
# Its presence is used to detect idempotency — if already in the file, skip.
_OBSIDIAN_GITIGNORE_MARKER = "# --- wiki-weaver: Obsidian ---"

# §14 F5 entries: user-specific Obsidian files + macOS junk
_OBSIDIAN_GITIGNORE_LINES: list[str] = [
    "# Obsidian user-specific (not shared)",
    ".obsidian/workspace.json",
    ".obsidian/app.json",
    ".obsidian/graph.json",
    ".obsidian/hotkeys.json",
    ".obsidian/graph-analysis.json",
    "# macOS junk",
    "._*",
    ".DS_Store",
]

# Bundled Obsidian template directory (lives inside the installed package).
# Resolution: wiki_weaver/templates/obsidian/ — always co-located with this file.
_OBSIDIAN_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates" / "obsidian"


def ensure_obsidian_ready(corpus: Path) -> None:
    """Seed Obsidian vault config and update ``.gitignore`` at the corpus root.

    Called by both ``init()`` (fresh corpora) and ``migrate()`` (existing
    corpora). Both operations are idempotent:

    * **``.gitignore``** — if the wiki-weaver marker line is absent the F5
      block is appended once.  Existing user entries are never modified.
    * **``.obsidian/``** — seeded from the package template *only* if the
      directory does not already exist.  A user's existing vault is never
      touched.

    Note on ``.wiki/`` visibility: Obsidian excludes dot-prefixed folders
    (``.*``) from its vault graph by default, so ``.wiki/`` is already hidden
    without any extra configuration.  The seeded ``app.json`` adds
    ``userIgnoreFilters`` as a belt-and-suspenders measure.
    """
    _ensure_corpus_gitignore(corpus)
    _seed_obsidian_config(corpus)


def _ensure_corpus_gitignore(corpus: Path) -> None:
    """Append the Obsidian gitignore block if the marker is absent."""
    gitignore_path = corpus / ".gitignore"

    if gitignore_path.exists():
        existing = gitignore_path.read_text(encoding="utf-8")
        if _OBSIDIAN_GITIGNORE_MARKER in existing:
            return  # block already present — idempotent no-op
        # Separate from existing content with a blank line
        sep = "\n" if existing.endswith("\n") else "\n\n"
        block = (
            sep
            + _OBSIDIAN_GITIGNORE_MARKER
            + "\n"
            + "\n".join(_OBSIDIAN_GITIGNORE_LINES)
            + "\n"
        )
        with gitignore_path.open("a", encoding="utf-8") as fh:
            fh.write(block)
    else:
        # Fresh corpus — write the whole block
        block = (
            _OBSIDIAN_GITIGNORE_MARKER
            + "\n"
            + "\n".join(_OBSIDIAN_GITIGNORE_LINES)
            + "\n"
        )
        gitignore_path.write_text(block, encoding="utf-8")


def _seed_obsidian_config(corpus: Path) -> None:
    """Copy the package Obsidian template into the corpus if absent."""
    obsidian_dir = corpus / ".obsidian"
    if obsidian_dir.is_dir():
        return  # user's vault already exists — never overwrite
    if not _OBSIDIAN_TEMPLATE_DIR.is_dir():
        _warn(
            "Obsidian template directory not found; skipping .obsidian/ seed. "
            f"(Expected: {_OBSIDIAN_TEMPLATE_DIR})"
        )
        return
    shutil.copytree(str(_OBSIDIAN_TEMPLATE_DIR), str(obsidian_dir))


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

INDEX_TEMPLATE = """\
---
title: Index
type: index
sources: []
last_updated: {today}
---

# Index

Catalog of wiki pages, grouped by type. (Maintained by the ingest pipeline.)
"""

OVERVIEW_TEMPLATE = """\
---
title: Overview
type: overview
sources: []
last_updated: {today}
---

# Overview

One-paragraph orientation to this wiki. (Maintained by the ingest pipeline.)
"""


def init(wiki_dir: str | Path) -> int:
    """Scaffold a fresh wiki directory."""
    wiki = Path(wiki_dir).resolve()
    # Hidden machine-only subtree
    wiki_hidden_dir(wiki).mkdir(parents=True, exist_ok=True)
    # Visible user-facing dirs
    wiki_inbox(wiki).mkdir(parents=True, exist_ok=True)
    wiki_sources(wiki).mkdir(parents=True, exist_ok=True)
    (wiki / ".ai" / "feedback").mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    index = wiki / "index.md"
    overview = wiki / "overview.md"
    if not index.exists():
        index.write_text(INDEX_TEMPLATE.format(today=today), encoding="utf-8")
    if not overview.exists():
        overview.write_text(OVERVIEW_TEMPLATE.format(today=today), encoding="utf-8")

    ledger = wiki_ledger(wiki)
    if not ledger.exists():
        ledger.touch()

    _ok(f"initialized wiki at {wiki}")
    print(f"  {INBOX}/  {SOURCES}/  .ai/feedback/  .wiki/  index.md  overview.md")
    ensure_obsidian_ready(wiki)
    return 0


# ---------------------------------------------------------------------------
# ledger helpers
# ---------------------------------------------------------------------------


def _read_ledger(wiki: Path) -> list[dict]:
    ledger = wiki_ledger(wiki)
    if not ledger.exists():
        return []
    rows: list[dict] = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _processed_sources(wiki: Path) -> set[str]:
    return {row.get("source", "") for row in _read_ledger(wiki)}


def _append_ledger(wiki: Path, entry: dict) -> None:
    with wiki_ledger(wiki).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Fix 3 -- persistent source registry (stable ids + content-hash dedupe)
# ---------------------------------------------------------------------------
#
# Source ids used to be guessed per-run by the ingest LLM ([1]/[2]/[3]), which
# collided across runs and produced duplicate summary pages on re-ingest. The
# registry at <wiki>/.sources.json is the single source of truth: the CLI
# assigns/looks up a stable id by CONTENT HASH *before* ingest and threads it
# into the inner pipeline as $source_id. An already-ingested source (same hash)
# is deduped and skipped.

REGISTRY_NAME = ".sources.json"


def _parse_transcript_header(text: str) -> dict:
    """Parse provenance from a meeting-transcript header block (no YAML frontmatter).

    Handles the format produced by Teams/Zoom/calendar export tools::

        # Transcript: Weekly Planning Sync

        Source: https://example.com/meetings/...
        Duration: 1:00:50
        Speakers: Chris Park, Alex Rivera, Samuel Lee
        Date: 5/29/2026, 11:07:43 AM
        Chat type: Meeting
        Attendees: Samuel Lee, Chris Park, Alex Rivera

        ---

        [0:00:04] Chris Park: ...

    Returns a dict with keys ``author``, ``url``, ``date``, ``title`` (all
    default to ``None``). Returns all-None for files that are not recognised
    as transcripts — graceful fallback, no crash, no fabrication.

    Detection: at least one of ``Speakers:`` or ``Attendees:`` must appear in
    the header block (lines before the first ``---`` separator, or the first
    50 lines when no separator is present). Labelled-field matching is
    case-insensitive. ``Source:`` is only accepted when the value starts with
    ``http://`` or ``https://`` to avoid false positives on prose fragments.
    """
    result: dict = {"author": None, "url": None, "date": None, "title": None}
    lines = text.splitlines()

    # Locate the header block: up to the first "---" separator (the thematic
    # break that ends the metadata preamble) or a 50-line cap.
    sep_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            sep_idx = i
            break
    header_lines = lines[: sep_idx if sep_idx is not None else min(50, len(lines))]

    # Labelled-field regex: "Label Name: value" (multi-word keys allowed)
    _labeled = re.compile(r"^([A-Za-z][A-Za-z0-9 ]*?):\s*(.+)$")

    has_speaker_marker = False  # True when Speakers: or Attendees: found
    attendees_val: str | None = None

    for line in header_lines:
        stripped = line.strip()

        # Extract title from the first markdown heading
        if stripped.startswith("#") and result["title"] is None:
            heading = re.sub(r"^#+\s*", "", stripped)
            heading = re.sub(r"(?i)^Transcript:\s*", "", heading).strip()
            if heading:
                result["title"] = heading
            continue

        m = _labeled.match(stripped)
        if not m:
            continue
        key = m.group(1).strip().lower()
        val = m.group(2).strip()
        if not val:
            continue

        if key == "source":
            # Accept only URLs to avoid false positives on prose like "Source: Smith 2024"
            if val.startswith(("http://", "https://")):
                result["url"] = val
        elif key == "speakers":
            result["author"] = val
            has_speaker_marker = True
        elif key == "date":
            result["date"] = val
        elif key == "attendees":
            attendees_val = val
            has_speaker_marker = True

    # Speakers: takes priority; Attendees: is the fallback author
    if result["author"] is None and attendees_val is not None:
        result["author"] = attendees_val

    # If no speaker/attendee marker found this is not a transcript — return all-None
    if not has_speaker_marker:
        return {"author": None, "url": None, "date": None, "title": None}

    return result


def _read_source_frontmatter(src: Path) -> dict:
    """Extract author, url, and date from YAML frontmatter of a source article.

    Reads the ``---`` … ``---`` frontmatter block and returns a dict with keys
    ``author``, ``url``, and ``date`` (all defaulting to ``None`` when absent).

    Handles simple single-line string fields only (quoted or unquoted). Fails
    silently on any parse error — provenance is best-effort; missing fields are
    stored as ``None`` in the registry, never as fabrications.

    Recognises both ``source:`` and ``url:`` as the URL field (acquisition
    tools typically write ``source:``, other producers may write ``url:``).

    Fallback: when no YAML frontmatter is found, attempts to parse a
    meeting-transcript header block via :func:`_parse_transcript_header`.
    Sources with neither frontmatter nor transcript markers are unchanged
    (returns all-None).
    """
    result: dict = {"author": None, "url": None, "date": None}
    try:
        text = src.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return result

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        # No YAML frontmatter — try transcript header as graceful fallback.
        fm = _parse_transcript_header(text)
        if fm.get("author"):
            result["author"] = fm["author"]
        if fm.get("url"):
            result["url"] = fm["url"]
        if fm.get("date"):
            result["date"] = fm["date"]
        return result

    end_idx = None
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        return result

    # Regex for `key: "quoted value"` or `key: 'quoted value'` or `key: plain value`
    _quoted = re.compile(r'^(\w+):\s*["\'](.*)["\']$')
    _plain = re.compile(r"^(\w+):\s*(.+)$")

    for line in lines[1:end_idx]:
        line = line.strip()
        m = _quoted.match(line) or _plain.match(line)
        if not m:
            continue
        key = m.group(1).lower()
        val = m.group(2).strip().strip("\"'")
        if not val:
            continue
        if key == "author":
            result["author"] = val
        elif key in ("source", "url"):
            result["url"] = val
        elif key == "date":
            result["date"] = val

    return result


def _source_hash(src: Path) -> str:
    """Stable content hash (sha256) of a source file."""
    h = hashlib.sha256()
    h.update(src.read_bytes())
    return h.hexdigest()


def _load_registry(wiki: Path) -> dict:
    reg_path = wiki_registry(wiki)
    if not reg_path.exists():
        return {"version": 1, "next_id": 1, "sources": []}
    try:
        data = json.loads(reg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "next_id": 1, "sources": []}
    if not isinstance(data, dict):
        return {"version": 1, "next_id": 1, "sources": []}
    data.setdefault("version", 1)
    data.setdefault("next_id", 1)
    data.setdefault("sources", [])
    return data


def _save_registry(wiki: Path, registry: dict) -> None:
    """Atomic write of the registry (tmp + replace)."""
    reg_path = wiki_registry(wiki)
    tmp = reg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    tmp.replace(reg_path)


def _registry_entry_for_hash(registry: dict, file_hash: str) -> dict | None:
    for entry in registry.get("sources", []):
        if entry.get("hash") == file_hash:
            return entry
    return None


def _assign_source_id(wiki: Path, src: Path) -> tuple[dict, bool]:
    """Look up or assign a stable id for ``src`` by content hash.

    Returns ``(entry, is_new)``. ``entry`` always has id/filename/hash/ingested.
    On a new source the registry is persisted immediately so the id is stable
    even if the ingest run later fails or is retried.

    Provenance fields (author, url, date) are read from the source file's YAML
    frontmatter and stored alongside id/filename/hash so citation ``[N]`` can
    resolve to a real author + URL.  Missing fields are stored as ``None``
    (or omitted) — never fabricated.
    """
    file_hash = _source_hash(src)
    registry = _load_registry(wiki)
    existing = _registry_entry_for_hash(registry, file_hash)
    if existing is not None:
        return existing, False

    fm = _read_source_frontmatter(src)
    entry: dict = {
        "id": int(registry["next_id"]),
        "filename": src.name,
        "hash": file_hash,
        "first_seen": datetime.now().isoformat(timespec="seconds"),
        "ingested": False,
    }
    # Provenance from frontmatter — store only fields that are present so the
    # registry stays clean (no null-value noise for articles without metadata).
    if fm.get("author"):
        entry["author"] = fm["author"]
    if fm.get("url"):
        entry["url"] = fm["url"]
    if fm.get("date"):
        entry["date"] = fm["date"]

    registry["sources"].append(entry)
    registry["next_id"] = int(registry["next_id"]) + 1
    _save_registry(wiki, registry)
    return entry, True


def _mark_source_ingested(wiki: Path, file_hash: str) -> None:
    registry = _load_registry(wiki)
    entry = _registry_entry_for_hash(registry, file_hash)
    if entry is not None and not entry.get("ingested"):
        entry["ingested"] = True
        entry["ingested_at"] = datetime.now().isoformat(timespec="seconds")
        _save_registry(wiki, registry)


# ---------------------------------------------------------------------------
# Fix 1b -- deterministic tamper guard (the safety net under fs sandboxing)
# ---------------------------------------------------------------------------
#
# Process state (the ledger + _sources/) is the CLI's EXCLUSIVE job and is
# written ONLY here, AFTER a real convergence. The spawned ingest node is
# additionally sandboxed at the filesystem-tool layer (engine_runner Fix 1),
# but tool-bash has no path sandbox, so we ALSO verify deterministically: snap
# the ledger + archive before the inner run; if EITHER changed during the run,
# the agent fabricated process state. We never trust it -- we restore the
# pre-run state (drop fabricated ledger lines, return falsely-archived files to
# the inbox) and FAIL LOUD.


def _snapshot_process_state(wiki: Path) -> tuple[int, set[str]]:
    ledger = wiki_ledger(wiki)
    ledger_lines = (
        len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.exists() else 0
    )
    sources = wiki_sources(wiki)
    sources_files = {p.name for p in sources.iterdir()} if sources.is_dir() else set()
    return ledger_lines, sources_files


def _detect_and_undo_tamper(wiki: Path, before: tuple[int, set[str]]) -> list[str]:
    """Compare process state to the pre-run snapshot; undo + report tamper.

    Returns a list of human-readable violation strings (empty == clean).
    """
    before_lines, before_sources = before
    violations: list[str] = []

    # (1) Ledger: any new line during the inner run is agent-fabricated, since
    # the lib appends only after this guard runs. Truncate back to before_lines.
    ledger = wiki_ledger(wiki)
    if ledger.exists():
        lines = ledger.read_text(encoding="utf-8").splitlines()
        if len(lines) > before_lines:
            fabricated = lines[before_lines:]
            violations.append(
                f"agent wrote {len(fabricated)} fabricated ledger line(s): "
                + "; ".join(s[:160] for s in fabricated)
            )
            kept = lines[:before_lines]
            ledger.write_text(
                ("\n".join(kept) + "\n") if kept else "", encoding="utf-8"
            )

    # (2) Sources: any file that appeared during the inner run is an
    # agent-performed move. Return it to the inbox so it is NOT falsely treated
    # as processed, and so the source can be re-ingested honestly.
    sources = wiki_sources(wiki)
    inbox = wiki_inbox(wiki)
    if sources.is_dir():
        now_sources = {p.name for p in sources.iterdir()}
        new_files = sorted(now_sources - before_sources)
        if new_files:
            violations.append(
                "agent moved source(s) into _sources/ (CLI-exclusive): "
                + ", ".join(new_files)
            )
            inbox.mkdir(exist_ok=True)
            for name in new_files:
                try:
                    (sources / name).replace(inbox / name)
                except OSError:
                    pass

    return violations


# ---------------------------------------------------------------------------
# ingest helpers
# ---------------------------------------------------------------------------


def _collision_safe_move(src: Path, dest_dir: Path) -> Path:
    """Move *src* into *dest_dir*, adding an integer suffix if the name is taken.

    Returns the final destination path.  Raises RuntimeError only on extreme
    collision counts (>= 10,000), which should never occur in practice.
    """
    dest = dest_dir / src.name
    if not dest.exists():
        src.replace(dest)
        return dest
    stem, suffix = src.stem, src.suffix
    for i in range(1, 10_000):
        candidate = dest_dir / f"{stem}.{i}{suffix}"
        if not candidate.exists():
            src.replace(candidate)
            return candidate
    raise RuntimeError(f"too many name collisions in {dest_dir} for {src.name}")


def _looks_like_text(path: Path) -> bool:
    """Return True unless *path* is binary, using git's heuristic: a file is
    binary iff it contains a NUL byte.

    Scans the whole file in 64 KB chunks (ingest sources are read in full by the
    engine downstream anyway, so this costs nothing next to the LLM pass) and
    stops at the first NUL.  Deliberately encoding-agnostic -- it does NOT decode
    the bytes.

    History: an earlier version decoded an 8 KB head *slice* as UTF-8 and treated
    a decode error as "binary".  But a multi-byte UTF-8 character straddling the
    8 KB cut raises UnicodeDecodeError, so valid UTF-8 text with long lines was
    silently misclassified as binary and dropped.  NUL-sniffing the whole file
    has no boundary hazard, accepts text in any encoding (UTF-8, latin-1, ...),
    and still rejects real binaries (images, archives, executables, UTF-16) since
    those carry NUL bytes.  Genuinely mis-encoded input is surfaced loudly
    downstream, not hidden here.
    """
    try:
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                if b"\x00" in chunk:
                    return False
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# ingest (headline command) -- the OUTER corpus sweep
# ---------------------------------------------------------------------------


def _print_summary(summary: list[tuple[str, str]]) -> None:
    print("\n--- ingest summary ---")
    for name, status in summary:
        mark = GREEN + "\u2713" if status == "converged" else YELLOW + "\u2022"
        print(f"  {mark}{RESET} {status:<14} {name}")
    if summary:
        failed_n = sum(
            1
            for _, s in summary
            if s in {"error", "not-converged", "tampered", "binary"}
        )
        converged_n = sum(1 for _, s in summary if s == "converged")
        print(f"  total={len(summary)}  converged={converged_n}  failed={failed_n}")


def _finish_ingest_run(
    run_dir: Path,
    run_id: str,
    summary: list[tuple[str, str]],
    advisories: list[str],
    blocked: list[dict],
    errored: list[dict],
) -> int:
    """End-of-run contract for BOTH ingest paths (single-file + drain).

    Builds the machine-readable run result from the per-source summary plus
    the structured gate-block / engine-error records collected along the way,
    writes ``<run_dir>/result.json`` (fail-soft), prints the honest headline,
    and returns the documented exit code -- see ``wiki_weaver/run_result.py``
    for the full contract (verdict rules, exit codes, the 0-converged
    invariant).
    """
    from wiki_weaver.run_result import build_result, counts_from_statuses, finish_run

    counts = counts_from_statuses(s for _, s in summary)
    result = build_result(
        run_id,
        counts,
        advisories=advisories,
        blocked=blocked,
        errored=errored,
        sources=[{"name": n, "status": s} for n, s in summary],
    )
    return finish_run(run_dir, result)


@dataclass
class DrainReport:
    """Opt-in out-channel for the drain's ``--limit`` cap-hit signal.

    ``ingest()``'s return type stays ``-> int`` (a contract existing callers and
    tests depend on -- see the rationale in ``ingest()``'s docstring below).
    Callers who need to know whether a drain stopped early because it ran out
    of budget (rather than because the inbox was empty) pass a ``DrainReport``
    in and read ``.hit_limit`` back out after the call.

    ``advisories`` is the machine-readable run-level gate-advisory signal:
    non-empty means a runtime gate (duplicate-page / claim-retention) FIRED
    but did NOT block (advisory mode -- the default; see
    ``wiki_weaver.grading.gates_enforced()``). Lets a scheduler tell an
    "advisory fired" run apart from a genuinely clean one. Populated on both
    the single-file and drain paths whenever a report is passed.
    """

    hit_limit: bool = False
    advisories: list[str] = field(default_factory=list)


def ingest(
    wiki: str | Path = ".",
    *,
    source: str | Path | None = None,
    max_cycles: int | None = None,
    keep_going: bool = False,
    limit: int | None = None,  # drain-path cap on real-ingest sources; None = unlimited
    report: DrainReport | None = None,  # opt-in out-channel for hit_limit
) -> int:
    """Integrate inbox sources via the engine.

    Parameters
    ----------
    wiki:
        Wiki directory (resolved from cwd if relative).
    source:
        Path to a single source file.  When omitted the full inbox is drained.
    max_cycles:
        Convergence budget passed to the inner pipeline.  ``None`` means use
        the wiki policy default (or 3 if no policy is configured).
    keep_going:
        In single-file mode: continue to the next source after a failure.
        In drain mode: this flag is a documented NO-OP — failures always route
        to ``_failed/`` and draining always continues regardless.
    limit:
        Caps the number of sources that reach real LLM synthesis (``run_inner``)
        in **drain mode only**.  ``None`` means unlimited.  ``0`` means process
        zero real-ingestion sources this call (cheap dispositions -- binary
        rejects, already-ingested duplicates -- still run for free).  Has no
        effect in single-file (``source=``) mode, which always processes
        exactly the one file given.
    report:
        Optional out-channel.  When provided, ``.hit_limit`` is set to ``True``
        if the drain stopped early because the ``--limit`` budget was spent
        (see ``DrainReport`` above).  The loud cap-hit signal (a ``WARN`` log
        line) does not depend on this -- it fires regardless of whether a
        report was passed.

    Returns -- THE EXIT-CODE CONTRACT (see wiki_weaver/run_result.py)
    -----------------------------------------------------------------
    Every run also writes ``<wiki>/.wiki/runs/ingest-<ts>/result.json``
    (machine-readable verdict + counts + gate blocks + errors; fail-soft)
    and prints an honest one-line headline. The returned int follows the
    documented contract, propagated verbatim by ``wiki-weaver ingest`` and
    ``schedule run-now``::

        0  -- >=1 source converged AND no gate-blocked AND no errored
              (verdicts: converged, partial; advisories allowed)
        1  -- engine/infrastructure error (verdict: errored) -- also
              returned for a missing wiki dir (pre-run validation)
        3  -- nothing to do: empty inbox, or only already-ingested
              duplicates (verdict: empty)
        4  -- gate-blocked under WIKI_WEAVER_ENFORCE_GATES=1
              (verdict: blocked)
        5  -- attempted > 0 but 0 converged, no gate/infra cause
              (verdict: failed)

    THE INVARIANT: converged == 0 with attempted > 0 can NEVER return 0
    (the incident this contract exists to prevent: a fully-blocked run
    that looked healthy for a week).
    """
    wiki = Path(wiki).resolve()
    if not wiki.is_dir():
        _fail(f"wiki dir not found: {wiki} (run `wiki-weaver init {wiki}` first)")
        return 1

    inbox = wiki_inbox(wiki)
    sources_dir = wiki_sources(wiki)
    inbox.mkdir(exist_ok=True)
    sources_dir.mkdir(exist_ok=True)

    # Run-result contract (see wiki_weaver/run_result.py): every ingest run --
    # single-file, drain, even a nothing-to-do tick -- ends with one
    # <run_dir>/result.json + an honest headline + a documented exit code.
    # Microsecond suffix so back-to-back runs never share a dir.
    run_id = f"ingest-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    run_dir = wiki_runs(wiki) / run_id

    if source:
        # ------------------------------------------------------------------ #
        # SINGLE-FILE PATH — behavior UNCHANGED from original.                #
        # keep_going and exit-on-first-failure semantics are preserved here.  #
        # NOTE [C9c]: --limit is a no-op in single-file mode (exactly one     #
        # source is ever processed here regardless of the cap).              #
        # ------------------------------------------------------------------ #
        sources: list[Path] = [Path(source).resolve()]

        # Import the engine runner lazily so `doctor`/`init`/`lint` never pay
        # the cost of pulling in the attractor engine.
        from wiki_weaver.engine_runner import run_inner
        from wiki_weaver.grading import gates_enforced, no_duplicate_pages
        from wiki_weaver.retention import enforce_retention_gate, snapshot_pages

        processed = _processed_sources(wiki)
        summary: list[tuple[str, str]] = []
        # Run-level gate advisories. By DEFAULT both runtime gates below are
        # ADVISORY (detect + surface loudly, never block); the env hatch
        # WIKI_WEAVER_ENFORCE_GATES=1 restores the old hard-blocking behavior
        # for both gates -- see wiki_weaver.grading.gates_enforced().
        advisories: list[str] = []
        # Structured run-result records (see wiki_weaver/run_result.py):
        # enforce-mode gate blocks name the GATE (never a generic error line);
        # errored carries engine/infra reasons.
        blocked_records: list[dict] = []
        errored_records: list[dict] = []

        for src in sources:
            name = src.name

            # Text sniff: fail loud on binary source; don't pollute the registry.
            if not _looks_like_text(src):
                _fail(f"{name}: unsupported binary source (no text handler)")
                summary.append((name, "binary"))
                _print_summary(summary)
                return _finish_ingest_run(
                    run_dir,
                    run_id,
                    summary,
                    advisories,
                    blocked_records,
                    errored_records,
                )

            # Fix 3: assign/look up a STABLE id by content hash BEFORE ingest
            # and dedupe an already-ingested source (same bytes) regardless of
            # filename.
            entry, is_new = _assign_source_id(wiki, src)
            source_id = entry["id"]
            file_hash = entry["hash"]
            already_done = entry.get("ingested") or name in processed
            if already_done:
                _warn(
                    f"skip (already ingested as source id [{source_id}], "
                    f"hash {file_hash[:12]}): {name}"
                )
                summary.append((name, "skipped"))
                continue
            if is_new:
                print(f"  assigned stable source id [{source_id}] for {name}")
            else:
                print(f"  reusing stable source id [{source_id}] for {name}")

            print(f"\n=== ingest: {name} (source id [{source_id}]) ===")

            # Fix 1b: snapshot process state so we can detect any agent-written
            # ledger line / archive move performed DURING the inner run (the lib
            # writes process state only AFTER this, on real convergence).
            before_state = _snapshot_process_state(wiki)

            # Claim-retention backstop: snapshot the current page BODIES before
            # the inner run so a post-convergence independent re-check can tell
            # whether the re-write silently dropped any prior content. See
            # wiki_weaver/retention.py for the full mechanism + honest framing
            # (this is an LLM-judge-backed re-check, NOT a deterministic gate).
            retention_snapshot_dir = (
                wiki_runs(wiki)
                / f".retention-snap-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            )
            snapshot_pages(wiki, retention_snapshot_dir)

            try:
                try:
                    result = run_inner(
                        src, wiki, max_cycles=max_cycles, source_id=source_id
                    )
                except Exception as e:  # noqa: BLE001 -- surface the real failure, loudly
                    _fail(f"engine error on {name}: {type(e).__name__}: {e}")
                    summary.append((name, "error"))
                    errored_records.append(
                        {"reason": f"engine error on {name}: {type(e).__name__}: {e}"}
                    )
                    if not keep_going:
                        _print_summary(summary)
                        return _finish_ingest_run(
                            run_dir,
                            run_id,
                            summary,
                            advisories,
                            blocked_records,
                            errored_records,
                        )
                    continue

                # Fix 1b: never trust agent-written process state. Undo + fail loud.
                violations = _detect_and_undo_tamper(wiki, before_state)
                if violations:
                    _fail(
                        f"{name}: TAMPER DETECTED -- the ingest agent wrote process "
                        f"state it does not own. Convergence is NOT trusted; "
                        f"fabricated records were reverted."
                    )
                    for v in violations:
                        _fail(f"    - {v}")
                    summary.append((name, "tampered"))
                    if not keep_going:
                        _print_summary(summary)
                        return _finish_ingest_run(
                            run_dir,
                            run_id,
                            summary,
                            advisories,
                            blocked_records,
                            errored_records,
                        )
                    continue

                if result.converged:
                    # Claim-retention backstop: an independent, LLM-judge-backed
                    # re-check of whether this re-write silently dropped prior
                    # content. ADVISORY by default (detect + surface, never
                    # block); WIKI_WEAVER_ENFORCE_GATES=1 restores the old
                    # blocking behavior (refuse archive/ledger-advance).
                    retention_decision = enforce_retention_gate(
                        wiki, retention_snapshot_dir
                    )
                    if retention_decision.action in (
                        "block_confirmed_loss",
                        "block_escalated_errors",
                    ):
                        if gates_enforced():
                            _fail(f"{name}: {retention_decision.message}")
                            summary.append((name, "retention-blocked"))
                            # Structured record: the GATE is named (mislabel fix
                            # -- never a generic error line).
                            blocked_records.append(
                                {
                                    "gate": "claim-retention",
                                    "scope": "source",
                                    "reason": retention_decision.message,
                                    "offending_items": [name],
                                }
                            )
                            if not keep_going:
                                _print_summary(summary)
                                return _finish_ingest_run(
                                    run_dir,
                                    run_id,
                                    summary,
                                    advisories,
                                    blocked_records,
                                    errored_records,
                                )
                            continue
                        advisory = (
                            "claim-retention gate (ADVISORY -- did NOT block) "
                            f"[source {name}]: {retention_decision.message}"
                        )
                        _gate_advisory("claim-retention", advisory)
                        advisories.append(advisory)
                    elif retention_decision.message:
                        # Either a plain PASS note or a fail-open WARN -- both
                        # non-blocking; the source proceeds to archive below.
                        print(f"  {retention_decision.message}")

                    # Duplicate-page backstop: a cheap, deterministic scan for
                    # merge-fragment duplicates (e.g. concept-2.md alongside
                    # concept.md -- the "appended instead of fused" failure
                    # signature). Free/no-LLM, so it always runs. ADVISORY by
                    # default; WIKI_WEAVER_ENFORCE_GATES=1 restores blocking.
                    dup_pages = no_duplicate_pages(wiki)
                    if dup_pages:
                        if gates_enforced():
                            _fail(
                                f"{name}: duplicate-page gate: merge-fragment "
                                f"duplicate(s) detected: {', '.join(dup_pages)}"
                            )
                            summary.append((name, "duplicate-blocked"))
                            # Structured record: the GATE is named (mislabel fix
                            # -- never a generic error line).
                            blocked_records.append(
                                {
                                    "gate": "duplicate-page",
                                    "scope": "wiki",
                                    "reason": (
                                        "duplicate-page gate: merge-fragment "
                                        f"duplicate(s) detected: {', '.join(dup_pages)}"
                                    ),
                                    "offending_items": list(dup_pages),
                                }
                            )
                            if not keep_going:
                                _print_summary(summary)
                                return _finish_ingest_run(
                                    run_dir,
                                    run_id,
                                    summary,
                                    advisories,
                                    blocked_records,
                                    errored_records,
                                )
                            continue
                        # Wiki-STRUCTURAL observation, not a verdict on this
                        # source: the pairs may pre-date this run entirely.
                        advisory = (
                            "duplicate-page gate (ADVISORY -- did NOT block): "
                            f"wiki contains {len(dup_pages)} version/merge-"
                            f"fragment page pair(s): {', '.join(dup_pages)}"
                        )
                        if advisory not in advisories:
                            _gate_advisory("duplicate-page", advisory)
                            advisories.append(advisory)

                    dest = sources_dir / name
                    if src.is_file() and src.parent == inbox:
                        src.replace(dest)
                    _append_ledger(
                        wiki,
                        {
                            "source": name,
                            "source_id": source_id,
                            "hash": file_hash,
                            "status": result.status,
                            "converged": result.converged,
                            "archived_to": str(dest),
                            "logs_dir": str(result.logs_dir),
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    _mark_source_ingested(wiki, file_hash)
                    _ok(f"{name}: converged (logs: {result.logs_dir})")
                    summary.append((name, "converged"))
                else:
                    _fail(
                        f"{name}: did not converge "
                        f"(status={result.status}, reason={result.failure_reason})"
                    )
                    summary.append((name, "not-converged"))
                    if not keep_going:
                        _print_summary(summary)
                        return _finish_ingest_run(
                            run_dir,
                            run_id,
                            summary,
                            advisories,
                            blocked_records,
                            errored_records,
                        )
            finally:
                # Belt-and-suspenders: enforce_retention_gate() already removes
                # retention_snapshot_dir on every path it runs; this covers the
                # error/tamper/not-converged paths above where it is never
                # invoked. ignore_errors=True tolerates an already-removed dir.
                shutil.rmtree(retention_snapshot_dir, ignore_errors=True)

        _print_summary(summary)
        _print_advisories(advisories)
        if report is not None:
            report.advisories.extend(advisories)
        return _finish_ingest_run(
            run_dir, run_id, summary, advisories, blocked_records, errored_records
        )

    # ---------------------------------------------------------------------- #
    # INBOX DRAIN PATH — re-globs _inbox on every pass so files added mid-run #
    # are picked up automatically.                                            #
    #                                                                         #
    # Load-bearing invariant: every file picked from _inbox MUST leave        #
    # _inbox this pass.  This keeps the inbox strictly shrinking and          #
    # guarantees termination — no infinite spin on bad files.                 #
    #                                                                         #
    # Terminal dispositions:                                                  #
    #   converged   → _sources/  (existing behaviour)                        #
    #   duplicate   → _sources/  (collision-safe; was: left in inbox → spin) #
    #   error/tamper/non-convergence → _failed/ (new; was: halted the run)   #
    #                                                                         #
    # --keep-going is accepted but is a NO-OP in drain mode: failures always  #
    # route to _failed/ and draining always continues regardless.  The flag   #
    # no longer controls early-exit here; exit code is set after the drain.   #
    # ---------------------------------------------------------------------- #

    # Warn + bail early if inbox is empty (preserves original UX; lazy import).
    if not any(
        p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")
    ):
        _warn(f"no sources to ingest (inbox empty: {inbox})")
        # Distinct nothing-to-do outcome (verdict "empty", exit 3): a headless
        # caller must be able to tell "no work" from "work succeeded".
        return _finish_ingest_run(run_dir, run_id, [], [], [], [])

    # Import the engine runner lazily so `doctor`/`init`/`lint` never pay the
    # cost of pulling in the attractor engine.
    from wiki_weaver.engine_runner import run_inner, shared_engine_loop
    from wiki_weaver.grading import gates_enforced, no_duplicate_pages
    from wiki_weaver.retention import enforce_retention_gate, snapshot_pages

    processed = _processed_sources(wiki)
    summary_drain: list[tuple[str, str]] = []
    # Run-level gate advisories. By DEFAULT both runtime gates below are
    # ADVISORY (detect + surface loudly, never block); the env hatch
    # WIKI_WEAVER_ENFORCE_GATES=1 restores the old hard-blocking behavior
    # for both gates -- see wiki_weaver.grading.gates_enforced().
    advisories_drain: list[str] = []
    # Structured run-result records (see wiki_weaver/run_result.py):
    # enforce-mode gate blocks name the GATE (never a generic error line);
    # errored carries engine/infra reasons.
    blocked_drain: list[dict] = []
    errored_drain: list[dict] = []

    failed_dir = wiki_failed(wiki)
    failed_dir.mkdir(parents=True, exist_ok=True)

    # Debounce: skip files written < 2 s ago (half-written by a concurrent
    # producer).  If all pending files are too-fresh, sleep briefly and retry
    # up to _FRESH_RETRIES_MAX times before declaring the drain complete.
    _DEBOUNCE_SECS = 2.0
    _FRESH_RETRIES_MAX = 5
    _fresh_retries = 0

    # --limit budget: counts only commitments to run_inner (real LLM work).
    # Cheap dispositions (binary reject, already-ingested duplicate) never
    # touch this counter -- see the gate below and
    # docs/designs/scheduled-ingestion-limit-addendum.md §2 [C3][C4].
    real_count = 0

    # Single event loop for the ENTIRE drain + final re-weave (Option A):
    # every per-source run_inner() and the overview re-weave share ONE loop,
    # so the load-once _BASE_BUNDLE provider client stays bound to a live loop
    # for the whole ingest (was: a fresh asyncio.run() per source that closed
    # its loop, wedging source N+1 on the closed-loop client).
    with shared_engine_loop():
        while True:
            # NOTE [C9a]: selection order is existing alphabetical-by-filename,
            # unrelated to --limit and unchanged by this addendum. Under
            # sustained new arrivals with early-sorting names, an older
            # backlog item with a late-sorting name can be deferred across
            # many ticks -- accepted, not a fairness bug introduced here (see
            # docs/designs/scheduled-ingestion-limit-addendum.md §6).
            pending = sorted(
                p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")
            )
            now = time.time()
            ready = [p for p in pending if (now - p.stat().st_mtime) >= _DEBOUNCE_SECS]

            if not ready:
                if pending and _fresh_retries < _FRESH_RETRIES_MAX:
                    # Files exist but all too-fresh; wait and retry.
                    _fresh_retries += 1
                    time.sleep(0.5)
                    continue
                # No files at all, or fresh-retry budget exhausted → drain complete.
                break

            _fresh_retries = 0  # reset whenever we find a ready file
            src = ready[0]
            name = src.name

            # Text sniff: route binary files to _failed/ without calling run_inner.
            if not _looks_like_text(src):
                _fail(
                    f"{src.name}: unsupported binary source (no text handler)"
                    " — routing to _failed/"
                )
                _collision_safe_move(src, failed_dir)
                summary_drain.append((src.name, "binary"))
                continue

            # Fix 3: assign/look up a STABLE id by content hash BEFORE ingest and
            # dedupe an already-ingested source (same bytes) regardless of filename.
            entry, is_new = _assign_source_id(wiki, src)
            source_id = entry["id"]
            file_hash = entry["hash"]
            already_done = entry.get("ingested") or name in processed
            if already_done:
                _warn(
                    f"skip (already ingested as source id [{source_id}], "
                    f"hash {file_hash[:12]}): {name}"
                )
                # Drain mode: move dup out of inbox to clear it (prevents spin).
                _collision_safe_move(src, sources_dir)
                summary_drain.append((name, "skipped"))
                continue
            if is_new:
                print(f"  assigned stable source id [{source_id}] for {name}")
            else:
                print(f"  reusing stable source id [{source_id}] for {name}")

            # --limit gate [C4]: eligibility is already determined at this
            # point (text file, not a duplicate, real source) -- check *then*
            # increment, so "capped" is decided precisely and cheaply, with no
            # extra end-of-loop inbox rescan. With limit == N, files 1..N each
            # pass here (real_count is 0..N-1 at check time) and the drain
            # reports complete; the (N+1)-th eligible file holds here with
            # real_count == N, sets hit_limit, and breaks -- leaving it in
            # _inbox/ for the next tick. _assign_source_id above may have
            # pre-registered this deferred source's stable id; that is
            # idempotent and harmless, the next tick just re-looks-it-up. This
            # break (not continue) preserves the drain's "every file picked
            # from _inbox/ must leave _inbox/ this pass" invariant: we haven't
            # picked this file for a disposition, we've stopped the whole
            # drain with it still sitting in _inbox/. See
            # docs/designs/scheduled-ingestion-limit-addendum.md §2 [C3][C4][C5].
            if limit is not None and real_count >= limit:
                if report is not None:
                    report.hit_limit = True
                _warn(
                    f"LIMIT REACHED: processed {real_count} real-ingest source(s) this "
                    f"pass (--limit {limit}); at least one more eligible source remains "
                    f"in _inbox/ and will be handled on the next tick. Raise the cap "
                    f"with `schedule install --limit N`, or process more now with "
                    f"`schedule run-now --wiki <dir> --limit N`."
                )
                break
            real_count += 1

            print(f"\n=== ingest: {name} (source id [{source_id}]) ===")

            # Fix 1b: snapshot process state so we can detect any agent-written
            # ledger line / archive move performed DURING the inner run (the lib
            # writes process state only AFTER this, on real convergence).
            before_state = _snapshot_process_state(wiki)

            # Claim-retention backstop: snapshot the current page BODIES before
            # the inner run so a post-convergence independent re-check can tell
            # whether the re-write silently dropped any prior content. See
            # wiki_weaver/retention.py for the full mechanism + honest framing
            # (this is an LLM-judge-backed re-check, NOT a deterministic gate).
            retention_snapshot_dir = (
                wiki_runs(wiki)
                / f".retention-snap-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
            )
            snapshot_pages(wiki, retention_snapshot_dir)

            try:
                try:
                    result = run_inner(
                        src, wiki, max_cycles=max_cycles, source_id=source_id
                    )
                except Exception as e:  # noqa: BLE001 -- surface the real failure, loudly
                    _fail(f"engine error on {name}: {type(e).__name__}: {e}")
                    if src.is_file() and src.parent == inbox:
                        _collision_safe_move(src, failed_dir)
                    summary_drain.append((name, "error"))
                    errored_drain.append(
                        {"reason": f"engine error on {name}: {type(e).__name__}: {e}"}
                    )
                    continue  # drain always continues; exit code is set after drain

                # Fix 1b: never trust agent-written process state. Undo + fail loud.
                violations = _detect_and_undo_tamper(wiki, before_state)
                if violations:
                    _fail(
                        f"{name}: TAMPER DETECTED -- the ingest agent wrote process "
                        f"state it does not own. Convergence is NOT trusted; "
                        f"fabricated records were reverted."
                    )
                    for v in violations:
                        _fail(f"    - {v}")
                    if src.is_file() and src.parent == inbox:
                        _collision_safe_move(src, failed_dir)
                    summary_drain.append((name, "tampered"))
                    continue

                if result.converged:
                    # Claim-retention backstop: an independent, LLM-judge-backed
                    # re-check of whether this re-write silently dropped prior
                    # content. ADVISORY by default (detect + surface, never
                    # block); WIKI_WEAVER_ENFORCE_GATES=1 restores the old
                    # blocking behavior (route to _failed/ like any other
                    # non-convergence disposition).
                    retention_decision = enforce_retention_gate(
                        wiki, retention_snapshot_dir
                    )
                    if retention_decision.action in (
                        "block_confirmed_loss",
                        "block_escalated_errors",
                    ):
                        if gates_enforced():
                            _fail(f"{name}: {retention_decision.message}")
                            if src.is_file() and src.parent == inbox:
                                _collision_safe_move(src, failed_dir)
                            summary_drain.append((name, "retention-blocked"))
                            # Structured record: the GATE is named (mislabel fix
                            # -- never a generic error line).
                            blocked_drain.append(
                                {
                                    "gate": "claim-retention",
                                    "scope": "source",
                                    "reason": retention_decision.message,
                                    "offending_items": [name],
                                }
                            )
                            continue
                        advisory = (
                            "claim-retention gate (ADVISORY -- did NOT block) "
                            f"[source {name}]: {retention_decision.message}"
                        )
                        _gate_advisory("claim-retention", advisory)
                        advisories_drain.append(advisory)
                    elif retention_decision.message:
                        print(f"  {retention_decision.message}")

                    # Duplicate-page backstop: cheap, deterministic, always-on
                    # (no fail-open/fail-closed escalation needed). ADVISORY by
                    # default; WIKI_WEAVER_ENFORCE_GATES=1 restores blocking.
                    dup_pages = no_duplicate_pages(wiki)
                    if dup_pages:
                        if gates_enforced():
                            _fail(
                                f"{name}: duplicate-page gate: merge-fragment "
                                f"duplicate(s) detected: {', '.join(dup_pages)}"
                            )
                            if src.is_file() and src.parent == inbox:
                                _collision_safe_move(src, failed_dir)
                            summary_drain.append((name, "duplicate-blocked"))
                            # Structured record: the GATE is named (mislabel fix
                            # -- never a generic error line).
                            blocked_drain.append(
                                {
                                    "gate": "duplicate-page",
                                    "scope": "wiki",
                                    "reason": (
                                        "duplicate-page gate: merge-fragment "
                                        f"duplicate(s) detected: {', '.join(dup_pages)}"
                                    ),
                                    "offending_items": list(dup_pages),
                                }
                            )
                            continue
                        # Wiki-STRUCTURAL observation, not a verdict on this
                        # source: the pairs may pre-date this run entirely.
                        advisory = (
                            "duplicate-page gate (ADVISORY -- did NOT block): "
                            f"wiki contains {len(dup_pages)} version/merge-"
                            f"fragment page pair(s): {', '.join(dup_pages)}"
                        )
                        if advisory not in advisories_drain:
                            _gate_advisory("duplicate-page", advisory)
                            advisories_drain.append(advisory)

                    dest = sources_dir / name
                    if src.is_file() and src.parent == inbox:
                        src.replace(dest)
                    _append_ledger(
                        wiki,
                        {
                            "source": name,
                            "source_id": source_id,
                            "hash": file_hash,
                            "status": result.status,
                            "converged": result.converged,
                            "archived_to": str(dest),
                            "logs_dir": str(result.logs_dir),
                            "timestamp": datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    _mark_source_ingested(wiki, file_hash)
                    _ok(f"{name}: converged (logs: {result.logs_dir})")
                    summary_drain.append((name, "converged"))
                else:
                    _fail(
                        f"{name}: did not converge "
                        f"(status={result.status}, reason={result.failure_reason})"
                    )
                    if src.is_file() and src.parent == inbox:
                        _collision_safe_move(src, failed_dir)
                    summary_drain.append((name, "not-converged"))
                    # Drain mode: always continue (never halt on non-convergence).
            finally:
                # Belt-and-suspenders: enforce_retention_gate() already removes
                # retention_snapshot_dir on every path it runs; this covers the
                # error/tamper/not-converged paths above where it is never
                # invoked. ignore_errors=True tolerates an already-removed dir.
                shutil.rmtree(retention_snapshot_dir, ignore_errors=True)

        _print_summary(summary_drain)
        _print_advisories(advisories_drain)
        if report is not None:
            report.advisories.extend(advisories_drain)
        # Fail-loud after the drain: surface anything routed to _failed/ as a
        # distinct, un-missable block (not merely a yellow bullet in the summary) so
        # silently-dropped sources cannot slip past the operator.
        failed_items = [
            (n, s)
            for n, s in summary_drain
            if s in {"error", "not-converged", "tampered", "binary"}
        ]
        if failed_items:
            print(
                f"\n{RED}!! {len(failed_items)} source(s) were NOT added to the wiki"
                f" -- moved to {failed_dir}{RESET}"
            )
            for n, s in failed_items:
                print(f"{RED}   - {n}  ({s}){RESET}")
            print(
                f"{RED}   review these, fix, and re-drop into _inbox/ to retry,"
                f" or remove them.{RESET}"
            )

        # ---------------------------------------------------------------------- #
        # Item 2 (overview re-weave): runs ONCE HERE, after the ENTIRE _inbox/    #
        # drain has completed -- never per source. grade_overview() is free and  #
        # deterministic; a re-weave LLM call only happens when overview.md has   #
        # actually degraded into a per-source narration log. Bounded retries;    #
        # fails loud (never silently reports success on a still-failing gate).   #
        # See wiki_weaver/reweave.py for the mechanism + cost-bounded design.    #
        # ---------------------------------------------------------------------- #
        from wiki_weaver.reweave import reweave_overview_if_needed

        reweave_result = reweave_overview_if_needed(wiki)
        if reweave_result.attempts:
            if reweave_result.final_passed:
                _ok(
                    f"overview.md re-woven into a synthesized map "
                    f"({reweave_result.attempts} attempt(s))"
                )
            else:
                _fail(
                    f"overview.md still fails grade_overview() after "
                    f"{reweave_result.attempts} re-weave attempt(s):\n"
                    f"{reweave_result.final_report}"
                )
                errored_drain.append(
                    {
                        "reason": (
                            f"overview re-weave failed after "
                            f"{reweave_result.attempts} attempt(s): "
                            f"{reweave_result.final_report}"
                        )
                    }
                )

        # Run-result contract: result.json + honest headline + documented exit
        # code (see wiki_weaver/run_result.py). Replaces the old binary
        # `1 if failed_items or reweave-failure else 0` -- which exited 0 even
        # when EVERY source was gate-blocked in enforce mode (the incident's
        # "looked healthy for a week" hole: retention-/duplicate-blocked
        # statuses were not in the failed_items set).
        return _finish_ingest_run(
            run_dir,
            run_id,
            summary_drain,
            advisories_drain,
            blocked_drain,
            errored_drain,
        )


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def lint(wiki: str | Path = ".") -> int:
    """Run the structural validator against a wiki directory."""
    wiki = Path(wiki).resolve()
    if not wiki.is_dir():
        _fail(f"wiki dir not found: {wiki}")
        return 1
    # Use the same validator config as the in-pipeline validate node so that
    # `wiki-weaver lint` and the pipeline `validate` step always agree.
    argv = [sys.executable, str(VALIDATE_PY), str(wiki)]
    validator_cfg = wiki_policy_dir(wiki) / "validator.yaml"
    if validator_cfg.is_file():
        argv += ["--config", str(validator_cfg)]
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
    )
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    return proc.returncode


# ---------------------------------------------------------------------------
# preflight: shared HARD-prerequisite checks (single source of truth)
# ---------------------------------------------------------------------------
#
# doctor() renders these verbosely (✓/✗ per check). The command wrappers in
# wiki_weaver.py call preflight() to fail CLEAN + UPFRONT before any engine or
# LLM work, so a broken environment never produces a mid-ingest traceback.
# Both paths run the SAME probes here, so doctor and the gate can never drift.


class _EnvCheck(NamedTuple):
    """One HARD prerequisite probe result (pure detection -- no printing)."""

    ok: bool
    ok_msg: str
    fail_msg: str
    remediation: tuple[str, ...] = ()


def _hard_env_checks(*, require_api_key: bool) -> list[_EnvCheck]:
    """Probe the HARD environment prerequisites in display order.

    ``require_api_key`` gates the ANTHROPIC_API_KEY check: engine/LLM-driven
    commands (init, ingest, ask) need a key; deterministic ones (lint) do not.
    All import probes are wrapped so a missing dependency is reported as a
    failed check, never raised -- the caller decides how loud to be.
    """
    checks: list[_EnvCheck] = []

    # API key -- only the engine/LLM-driven commands require it.
    if require_api_key:
        checks.append(
            _EnvCheck(
                ok=bool(os.environ.get("ANTHROPIC_API_KEY")),
                ok_msg="ANTHROPIC_API_KEY is set",
                fail_msg="ANTHROPIC_API_KEY is not set",
                remediation=(
                    "  export ANTHROPIC_API_KEY=... (or set it in ~/.amplifier settings)",
                ),
            )
        )

    # foundation is the engine entrypoint; prepare() resolves the loop-pipeline
    # orchestrator and hook modules from the bundle on demand.
    try:
        import amplifier_foundation  # noqa: F401

        checks.append(
            _EnvCheck(True, "amplifier_foundation importable", "amplifier_foundation")
        )
    except Exception as e:  # noqa: BLE001
        checks.append(
            _EnvCheck(
                ok=False,
                ok_msg="",
                fail_msg=f"amplifier_foundation not importable: {e}",
                remediation=(
                    "  run wiki-weaver under a python env that has amplifier-foundation",
                    "  (e.g. the interpreter behind ~/.local/bin/amplifier)",
                ),
            )
        )

    # unified_llm must be importable: the engine's DirectProviderBackend fallback
    # imports it, and a stale unified-llm-client (>=0.2 ships as `llm/`, not
    # `unified_llm/`) makes that fallback crash AFTER a multi-minute ingest with
    # ModuleNotFoundError. Catch the regression here in one second instead.
    #
    # Name-aware guardrail: if `unified_llm` is missing but `llm` IS present,
    # that is the v0.2 import-name regression -- emit a specific, actionable
    # message rather than a generic "not found".
    try:
        import unified_llm  # noqa: F401

        checks.append(
            _EnvCheck(True, "unified_llm importable (engine fallback path safe)", "")
        )
    except Exception as e:  # noqa: BLE001
        import importlib.util

        if importlib.util.find_spec("llm") is not None:
            # `llm` is present but `unified_llm` isn't -> v0.2 rename regression.
            checks.append(
                _EnvCheck(
                    ok=False,
                    ok_msg="",
                    fail_msg=(
                        "IMPORT REGRESSION: `unified_llm` NOT importable -- but `llm` "
                        "IS present. The installed amplifier-unified-llm-client uses the "
                        "new `llm` import layout (v0.2+); this wiki-weaver expects "
                        "`unified_llm` (v0.1.x). Incompatible -- reinstall wiki-weaver or "
                        "align the client version."
                    ),
                    remediation=(
                        "  fix: uv tool install --force"
                        " git+https://github.com/microsoft/amplifier-app-wiki-weaver",
                    ),
                )
            )
        else:
            # Neither unified_llm nor llm found -- client not installed at all.
            checks.append(
                _EnvCheck(
                    ok=False,
                    ok_msg="",
                    fail_msg=f"unified_llm NOT importable: {e}",
                    remediation=(
                        "  install the correct client:"
                        " uv pip install --python <amplifier py> \\",
                        "  --force-reinstall <attractor-cache>/modules/unified-llm-client"
                        " (v0.1.x, ships unified_llm/)",
                    ),
                )
            )

    # structural validator presence (deterministic lint + ingest verify nodes).
    checks.append(
        _EnvCheck(
            ok=VALIDATE_PY.is_file(),
            ok_msg=f"structural validator found: {VALIDATE_PY}",
            fail_msg=f"validate_wiki.py missing: {VALIDATE_PY}",
        )
    )

    return checks


def preflight(*, require_api_key: bool) -> list[str]:
    """Return HARD-prerequisite failure messages (empty list = environment OK).

    Command wrappers call this BEFORE any engine/LLM work so a broken
    environment fails CLEAN + UPFRONT (no mid-ingest traceback). doctor() runs
    the SAME probes (via ``_hard_env_checks``) for its verbose report, so the
    two can never drift.
    """
    return [
        c.fail_msg
        for c in _hard_env_checks(require_api_key=require_api_key)
        if not c.ok
    ]


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def doctor(*, wiki: str | Path | None = None) -> int:
    """Run environment diagnostics, optionally checking a specific wiki."""
    ok = True

    # HARD prerequisites -- the SAME probes the command wrappers gate on, so the
    # verbose report and the upfront gate can never disagree.
    for c in _hard_env_checks(require_api_key=True):
        if c.ok:
            _ok(c.ok_msg)
        else:
            _fail(c.fail_msg)
            for line in c.remediation:
                _warn(line)
            ok = False

    # Engine runner imports cleanly (local code; needed for the WARN probes).
    try:
        from wiki_weaver.engine_runner import (
            ATTRACTOR_PIPELINE_LOCAL,
            load_ci_config,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"could not load engine_runner: {e}")
        return 1

    # Amplifier runtime present: wiki-weaver is a companion tool. The engine's
    # load_bundle() fetches the attractor-pipeline bundle on first ingest and
    # reads API keys from ~/.amplifier/settings. A missing or empty cache means
    # first ingest will cold-fetch from git (needs network + Amplifier install).
    _amplifier_home = Path.home() / ".amplifier"
    _amplifier_cache = _amplifier_home / "cache"
    if not _amplifier_home.is_dir():
        _warn(
            "~/.amplifier/ not found — Amplifier (amplifier-app-cli) does not appear "
            "installed/initialized. wiki-weaver is a companion tool; first ingest will "
            "cold-fetch the engine bundle and requires network + an Amplifier install. "
            "See README."
        )
    elif not _amplifier_cache.is_dir() or not any(_amplifier_cache.iterdir()):
        _warn(
            "~/.amplifier/cache/ is missing or empty — Amplifier may not be fully "
            "initialized; first ingest will cold-fetch the engine bundle from git. "
            "Initialize Amplifier first, or ensure network access is available."
        )
    else:
        _ok("Amplifier runtime present (~/.amplifier/cache is non-empty)")

    # Network reachability: load_bundle() fetches from github.com when the cache
    # is cold. Fast TCP-only probe (no HTTP, no auth, no bundle load) — non-fatal
    # WARN so it never blocks a user with an already-warm cache.
    import socket as _socket

    try:
        with _socket.create_connection(("github.com", 443), timeout=3):
            _ok("network: github.com:443 reachable")
    except OSError as e:
        _warn(
            f"network: github.com:443 unreachable ({type(e).__name__}: {e}) — "
            "first ingest fetches the attractor engine bundle and will fail offline"
        )

    if ATTRACTOR_PIPELINE_LOCAL:
        pipeline_bundle = Path(ATTRACTOR_PIPELINE_LOCAL)
        if pipeline_bundle.is_file():
            _ok(f"attractor-pipeline bundle found: {pipeline_bundle}")
        else:
            _warn(
                f"WIKI_WEAVER_ATTRACTOR_PIPELINE set but path missing ({pipeline_bundle});"
                " will fall back to git URL"
            )
    else:
        _warn(
            "WIKI_WEAVER_ATTRACTOR_PIPELINE not set; will load attractor-pipeline from git URL"
        )

    # context-intelligence hook.
    # The hook's LoggingHandler is ALWAYS-ON: it writes per-session events.jsonl
    # locally regardless of config. Unconfigured = local-only = normal default.
    _ok("context-intelligence: logging locally (per-session events.jsonl) — normal")
    ci_cfg = load_ci_config()
    destinations = ci_cfg.get("destinations") or {}
    if destinations:
        for dest_name, dest in destinations.items():
            dest_url = dest.get("url", "") if isinstance(dest, dict) else ""
            if not dest_url:
                continue
            _ok(f"context-intelligence: remote destination '{dest_name}' → {dest_url}")
            # Probe the destination (non-fatal info -- local logging continues regardless).
            try:
                import urllib.request

                with urllib.request.urlopen(dest_url, timeout=3) as resp:  # noqa: S310
                    _ok(
                        f"context-intelligence: '{dest_name}' server UP (HTTP {resp.status})"
                    )
            except Exception as e:  # noqa: BLE001
                _ok(
                    f"context-intelligence: '{dest_name}' server DOWN/unreachable"
                    f" ({type(e).__name__}) — OK, local events.jsonl still written"
                )
    else:
        _ok(
            "context-intelligence: no remote destinations configured (local-only) — normal"
        )

    if wiki:
        wiki_path = Path(wiki).resolve()
        missing = [
            d for d in (INBOX, SOURCES, ".ai/feedback") if not (wiki_path / d).is_dir()
        ]
        if wiki_path.is_dir() and not missing:
            _ok(f"wiki structure OK: {wiki_path}")
        else:
            _fail(f"wiki structure incomplete at {wiki_path} (missing: {missing})")
            ok = False

        # Policy echo: show the resolved paths + model knobs for this wiki so the
        # user can verify that project overrides are being picked up correctly.
        if wiki_path.is_dir():
            try:
                from wiki_weaver.policy import load_policy

                policy = load_policy(wiki_path)
                _ok(f"  policy.schema:          {policy.schema_path}")
                _ok(f"  policy.rubric:          {policy.convergence_rubric_path}")
                _ok(f"  policy.inner_dot:       {policy.inner_dot_path}")
                _ok(
                    f"  policy.validator_cfg:   "
                    f"{policy.validator_config_path or '(built-in defaults)'}"
                )
                _ok(f"  policy.provider:        {policy.provider}")
                _ok(f"  policy.models:          {policy.models}")
                _ok(f"  policy.max_cycles:      {policy.max_cycles}")
                _warn(
                    f"  policy.parallelism:     {policy.parallelism}"
                    " (RESERVED \u2014 within-wiki ingest is sequential;"
                    " parallelism key accepted but always honored as 1)"
                )
            except Exception as e:  # noqa: BLE001
                _warn(f"  could not resolve policy for {wiki_path}: {e}")

    # Resolved @main commits — the "what am I running" record that replaces
    # the committed uv.lock (absent intentionally; see .gitignore).
    # Reads from local cache only (no ls-remote) so doctor stays fast offline.
    # Run `wiki-weaver update --check` to compare against remote.
    try:
        from wiki_weaver.updater import local_layer2_commits, wheel_dep_commits

        print()
        print(
            "Resolved @main commits (local — run 'wiki-weaver update --check' to compare remote):"
        )
        for rec in wheel_dep_commits():
            sha = rec.local_short
            _ok(f"  {rec.label:<44s} {sha}")
        for rec in local_layer2_commits():
            if rec.local_sha:
                _ok(f"  {rec.label:<44s} {rec.local_short}")
            else:
                _warn(f"  {rec.label:<44s} (not cached — will clone on first ingest)")
    except Exception as e:  # noqa: BLE001
        _warn(f"could not read resolved @main commits: {e}")

    # Attractor engine routing-contract floor: pipeline/synthesize.dot's assess
    # node reports its verdict as a flat bare-JSON final message (spawn path,
    # PR #41). Verdict routing is only fail-safe when the resolved
    # attractor-bundle commit is at or beyond ATTRACTOR_ROUTING_FLOOR_SHA
    # (attractor #89, transitively #88): older engines leave a stale
    # preferred_label in context across loop_restart, so a verdict from one
    # cycle/source can leak into the next and silently false-converge it.
    # Read-only diagnostic;
    # degrades to WARN (never blocks doctor) when inconclusive — e.g. offline,
    # not yet cloned, or the GitHub compare API is unreachable.
    try:
        import asyncio

        from wiki_weaver.updater import (
            ATTRACTOR_ROUTING_FLOOR_SHA,
            check_attractor_routing_floor,
        )

        floor_result = asyncio.run(check_attractor_routing_floor())
        floor_short = ATTRACTOR_ROUTING_FLOOR_SHA[:8]
        if floor_result.ok is True:
            _ok(
                f"attractor routing-contract floor: {floor_result.message}"
                f" (>= {floor_short})"
            )
        elif floor_result.ok is False:
            _fail(
                f"attractor routing-contract floor: {floor_result.message}"
                f" (>= {floor_short})"
            )
            _warn(
                "  upgrade the attractor engine (amplifier-module-loop-pipeline /"
                f" attractor bundle) to a commit at or past {floor_short}"
                " \u2014 run `wiki-weaver update`"
            )
            ok = False
        else:
            _warn(
                f"attractor routing-contract floor: {floor_result.message}"
                f" (>= {floor_short})"
            )
    except Exception as e:  # noqa: BLE001
        _warn(f"could not check attractor routing-contract floor: {e}")

    print()
    if ok:
        _ok("doctor: all required checks passed")
        return 0
    _fail("doctor: one or more checks failed")
    return 1


# ---------------------------------------------------------------------------
# update — refresh @main sources
# ---------------------------------------------------------------------------


def update(*, check_only: bool = False) -> int:
    """Refresh wiki-weaver's @main sources to latest.

    Tracks @main, fix-forward — no SHA pinning.

    Two layers:
      Layer 1 — ``uv tool install --reinstall`` to update wiki-weaver itself
                and its wheel deps (amplifier-foundation, amplifier-unified-llm-client).
                Uses verify+ladder+fail-loud: if stale uv-cached packages are
                detected, escalates to ``--no-cache`` then ``uv cache clean``.
      Layer 2 — Calls foundation's ``GitSourceHandler.update()`` on the
                attractor-bundle and context-intelligence engine bundles in
                ``~/.amplifier/cache/bundles`` (rmtree+reclone).

    ``check_only=True`` — detect and report without modifying anything.
    """
    try:
        from wiki_weaver.updater import (  # noqa: F401
            Layer1Result,
            SourceRecord,
            check_layer1,
            check_layer2,
            update_layer1,
            update_layer2,
        )
    except Exception as e:  # noqa: BLE001
        _fail(f"could not load updater module: {e}")
        return 1

    if check_only:
        return _update_check(check_layer1, check_layer2)
    return _update_real(update_layer1, update_layer2)


def _update_check(check_l1_fn, check_l2_fn) -> int:  # type: ignore[no-untyped-def]
    """--check mode: ls-remote all sources, report drift, no side effects."""
    print("Checking @main sources for drift (ls-remote only — no changes made)…")
    any_update = False
    any_error = False

    print()
    print("Layer 1 — wheel deps (amplifier-foundation, amplifier-unified-llm-client):")
    try:
        for rec in check_l1_fn():
            if rec.error:
                _warn(f"  {rec.label}: {rec.error}")
                any_error = True
            elif rec.needs_update:
                _warn(
                    f"  {rec.label}: UPDATE AVAILABLE  "
                    f"{rec.local_short} -> {rec.target_short}"
                )
                any_update = True
            elif rec.needs_update is False:
                _ok(f"  {rec.label}: up to date ({rec.local_short})")
            else:
                _warn(f"  {rec.label}: unknown (local={rec.local_short} remote=?)")
    except Exception as e:  # noqa: BLE001
        _fail(f"  layer-1 check failed: {e}")
        any_error = True

    print()
    print("Layer 2 — engine bundles (~/.amplifier/cache/bundles):")
    try:
        for rec in check_l2_fn():
            if rec.error:
                _warn(f"  {rec.label}: {rec.error}")
                any_error = True
            elif rec.needs_update:
                _warn(
                    f"  {rec.label}: UPDATE AVAILABLE  "
                    f"{rec.local_short} -> {rec.target_short}"
                )
                any_update = True
            elif rec.needs_update is False:
                _ok(f"  {rec.label}: up to date ({rec.local_short})")
            else:
                _warn(
                    f"  {rec.label}: not yet cached (will clone fresh on first ingest)"
                )
    except Exception as e:  # noqa: BLE001
        _fail(f"  layer-2 check failed: {e}")
        any_error = True

    print()
    if any_update:
        _warn("Updates are available.  Run `wiki-weaver update` to apply.")
    elif any_error:
        _warn("Some checks failed; could not determine update status for all sources.")
    else:
        _ok("All @main sources are up to date.")
    return 1 if any_error else 0


def _update_real(update_l1_fn, update_l2_fn) -> int:  # type: ignore[no-untyped-def]
    """Real update: Layer 1 reinstall + Layer 2 re-clone."""
    print("Updating wiki-weaver to latest @main…")
    overall_ok = True

    # --- Layer 1 ---
    print()
    print("Layer 1 — uv tool install --reinstall (wiki-weaver + wheel deps)…")
    res = None
    try:
        res = update_l1_fn(verbose=True)
    except Exception as e:  # noqa: BLE001
        _fail(f"Layer 1 update raised: {e}")
        overall_ok = False

    if res is not None:
        for name in res.before:
            b = (res.before.get(name) or "?")[:8]
            a = (res.after.get(name) or "?")[:8]
            if b != a:
                _ok(f"  {name}: {b} -> {a}")
            else:
                _ok(f"  {name}: {a} (already at latest)")
        for err in res.errors:
            _fail(f"  error: {err}")
        if res.stale:
            _fail(
                f"  FAIL: after {res.rung_reached} rung(s), {res.stale} still didn't update. "
                f"uv is serving a stale cache.  Manual fix:\n"
                f"    uv cache prune && "
                f"uv tool install --reinstall "
                f"git+https://github.com/microsoft/amplifier-app-wiki-weaver"
            )
            overall_ok = False
        elif not res.success and res.errors:
            overall_ok = False

    # --- Layer 2 ---
    print()
    print("Layer 2 — engine bundle re-clone (~/.amplifier/cache/bundles)…")
    try:
        for rec in update_l2_fn():
            if rec.skipped:
                _warn(f"  {rec.label}: skipped ({rec.error})")
            elif rec.error:
                _fail(f"  {rec.label}: ERROR — {rec.error}")
                overall_ok = False
            elif rec.needs_update:
                _ok(f"  {rec.label}: {rec.local_short} -> {rec.target_short}")
            else:
                _ok(f"  {rec.label}: {rec.target_short} (already at latest)")
    except Exception as e:  # noqa: BLE001
        _fail(f"Layer 2 update raised: {e}")
        overall_ok = False

    # --- Summary ---
    print()
    if overall_ok:
        _ok("Update complete.")
        print("  Run `wiki-weaver doctor` to confirm resolved commits.")
    else:
        _fail("Update completed with errors (see above).")
        print("  Run `wiki-weaver doctor` for diagnostics.")
    return 0 if overall_ok else 1


# ---------------------------------------------------------------------------
# migrate -- move corpus from OLD layout to NEW layout
# ---------------------------------------------------------------------------
#
# OLD layout (pre-0.5.0):
#   <corpus>/.processed.jsonl   ledger at corpus root
#   <corpus>/.sources.json      registry at corpus root
#   <corpus>/_archive/          archived source files (visible)
#   <corpus>/_failed/           failed-ingest files (visible)
#   <corpus>/.runs/             run logs (hidden but at root)
#   <corpus>/policy/            per-wiki schema overrides (visible)
#   <corpus>/.wiki-dashboard/   dashboard theme (hidden but at root)
#
# NEW layout (0.5.0+):
#   <corpus>/.wiki/.processed.jsonl   ledger under hidden subtree
#   <corpus>/.wiki/.sources.json      registry under hidden subtree
#   <corpus>/_sources/                archived sources (renamed, stays visible)
#   <corpus>/.wiki/failed/            failed-ingest files
#   <corpus>/.wiki/runs/              run logs
#   <corpus>/.wiki/policy/            per-wiki schema overrides
#   <corpus>/.wiki/dashboard/         dashboard theme
#
# Ledger field rewrites (absolute paths in the entries):
#   archived_to:  /<wiki>/_archive/…  →  /<wiki>/_sources/…
#   logs_dir:     /<wiki>/.runs/…     →  /<wiki>/.wiki/runs/…

MIGRATION_LOCK_NAME = ".wiki-migration.lock"
MIGRATION_SENTINEL = ".migration-complete"


def _migration_plan(wiki: Path) -> list[tuple[str, Path, Path, bool]]:
    """Return ``(description, old_path, new_path, is_dir)`` for OLD paths that exist."""
    candidates: list[tuple[str, Path, Path, bool]] = [
        ("ledger", wiki / ".processed.jsonl", wiki_ledger(wiki), False),
        ("registry", wiki / ".sources.json", wiki_registry(wiki), False),
        ("archive", wiki / "_archive", wiki_sources(wiki), True),
        ("failed", wiki / "_failed", wiki_failed(wiki), True),
        ("runs", wiki / ".runs", wiki_runs(wiki), True),
        ("policy", wiki / "policy", wiki_policy_dir(wiki), True),
        ("dashboard", wiki / ".wiki-dashboard", wiki_dashboard(wiki), True),
    ]
    return [item for item in candidates if item[1].exists()]


def _rewrite_ledger_keys(ledger_path: Path, wiki: Path) -> tuple[int, int]:
    """Rewrite path-valued fields in the NEW ledger after it has been copied.

    Rewrites:
    - ``archived_to``: replaces the ``<wiki>/_archive`` prefix with
      ``<wiki>/_sources`` (the new visible sources dir).
    - ``logs_dir``: replaces the ``<wiki>/.runs`` prefix with
      ``<wiki>/.wiki/runs``.

    Returns ``(changed, path_field_lines)``:
    - ``changed``: number of lines actually rewritten (prefix matched this corpus).
    - ``path_field_lines``: number of non-empty lines that carry a non-empty
      ``archived_to`` or ``logs_dir`` field at all (whether or not the prefix
      matched).  The caller uses this for the C1 mismatch guard: a ledger that
      HAS path fields but rewrote NONE means its absolute paths point at a
      different corpus location (moved/copied since processing).

    Writes atomically via a temp file + rename.  Raises ``RuntimeError`` if the
    line count changes.
    """
    if not ledger_path.exists():
        return 0, 0

    old_archive = str(wiki / "_archive")
    new_sources_str = str(wiki_sources(wiki))
    old_runs = str(wiki / ".runs")
    new_runs_str = str(wiki_runs(wiki))

    raw = ledger_path.read_text(encoding="utf-8")
    lines_in = raw.splitlines(keepends=True)
    lines_out: list[str] = []
    changed = 0
    path_field_lines = 0

    for line in lines_in:
        stripped = line.rstrip("\n").rstrip("\r")
        if not stripped:
            lines_out.append(line)
            continue
        try:
            entry = json.loads(stripped)
        except json.JSONDecodeError:
            lines_out.append(line)
            continue

        at = entry.get("archived_to")
        ld = entry.get("logs_dir")

        # Does this line carry ANY non-empty path field? (C1 denominator.)
        if (isinstance(at, str) and at) or (isinstance(ld, str) and ld):
            path_field_lines += 1

        modified = False
        if isinstance(at, str) and at.startswith(old_archive):
            entry["archived_to"] = new_sources_str + at[len(old_archive) :]
            modified = True

        if isinstance(ld, str) and ld.startswith(old_runs):
            entry["logs_dir"] = new_runs_str + ld[len(old_runs) :]
            modified = True

        if modified:
            changed += 1

        ending = "\n" if line.endswith("\n") else ""
        lines_out.append(json.dumps(entry) + ending)

    if len(lines_out) != len(lines_in):
        raise RuntimeError(
            f"ledger line count changed during rewrite: "
            f"{len(lines_in)} → {len(lines_out)}"
        )

    tmp = ledger_path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(lines_out), encoding="utf-8")
    tmp.replace(ledger_path)
    return changed, path_field_lines


def _write_migration_sentinel(sentinel: Path) -> None:
    """Write the completion sentinel with a timestamp."""
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(
        json.dumps(
            {
                "migrated_at": datetime.now().isoformat(timespec="seconds"),
                "from_layout": "pre-0.5.0",
                "to_layout": "0.5.0",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _safe_rel(path: Path, base: Path) -> str:
    """Return *path* relative to *base*, or the absolute string if not under it."""
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _files_differ(a: Path, b: Path) -> bool:
    """Return True when *a* and *b* differ in content (or comparison fails).

    Used by the no-clobber copy guard (M1): a differing existing target must be
    preserved, never silently overwritten with OLD content.  On any OS error we
    conservatively report "differ" so the target is preserved rather than lost.
    """
    try:
        return not filecmp.cmp(str(a), str(b), shallow=False)
    except OSError:
        return True


def _guarded_copytree(old: Path, new: Path, wiki: Path) -> list[str]:
    """Copy *old* → *new* file-by-file, preserving any newer/differing target (M1).

    Unlike ``shutil.copytree(dirs_exist_ok=True)``, this never overwrites an
    existing target file whose content differs from the OLD source — that target
    may be post-upgrade content (e.g. a ``build-dashboard``-written
    ``theme.json``) that the user does not want clobbered with OLD data.  Such
    files are skipped with a WARN and their relative paths returned.

    Identical existing targets are skipped silently (idempotent re-run).
    Missing targets are copied with ``copy2`` (preserves mtime).  Raises
    ``OSError`` on a real copy failure (e.g. ENOSPC) — handled by the caller.
    """
    new.mkdir(parents=True, exist_ok=True)
    preserved: list[str] = []
    for src in sorted(old.rglob("*")):
        rel = src.relative_to(old)
        dst = new / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and _files_differ(src, dst):
            preserved.append(_safe_rel(dst, wiki))
            _warn(
                f"    preserved existing {_safe_rel(dst, wiki)} "
                f"(differs from OLD; not overwritten)"
            )
            continue
        shutil.copy2(str(src), str(dst))
    return preserved


def migrate(wiki_dir: str | Path, *, dry_run: bool = False, force: bool = False) -> int:
    """Migrate a corpus from the OLD (pre-0.5.0) layout to the NEW layout.

    Safety invariants (all non-negotiable):

    * **PID lock** — ``<corpus>/.wiki-migration.lock`` prevents concurrent runs.
      A stale lock whose PID is gone is silently removed and migration proceeds.
    * **Idempotency sentinel** — ``<corpus>/.wiki/.migration-complete`` makes a
      second run a clean no-op.  ``--force`` bypasses the sentinel.
    * **Copy → rewrite → verify → delete** — old data is never removed before
      the new data has been copied and verified.  A failed verification aborts
      before any deletion occurs.
    * **``--dry-run``** — prints the full migration plan and exits without
      touching the filesystem.

    Ledger rewrites (in ``.wiki/.processed.jsonl`` after copying):

    * ``archived_to``: ``/_archive/…`` → ``/_sources/…``
    * ``logs_dir``:    ``/.runs/…``    → ``/.wiki/runs/…``

    ``sources.json`` filenames are bare identifiers — no path rewrite.
    """
    wiki = Path(wiki_dir).expanduser().resolve()

    if not wiki.is_dir():
        _fail(f"corpus directory not found: {wiki}")
        return 1

    lock_path = wiki / MIGRATION_LOCK_NAME
    sentinel = wiki_hidden_dir(wiki) / MIGRATION_SENTINEL

    # ── PID lock (atomic, TOCTOU-free) ──────────────────────────────────────
    # Create the lock with O_EXCL so the existence check and the write are a
    # single atomic syscall — two concurrent migrations cannot both win the
    # race. Only on FileExistsError do we inspect the holder's PID to decide
    # whether it is a live run (abort) or a stale lock to reclaim.
    if not _acquire_migration_lock(lock_path):
        return 1

    try:
        return _run_migration(wiki, sentinel, dry_run=dry_run, force=force)
    finally:
        lock_path.unlink(missing_ok=True)


def _acquire_migration_lock(lock_path: Path) -> bool:
    """Atomically acquire the migration PID lock; return True on success.

    Uses ``open(path, "x")`` (O_EXCL) so creation is atomic. If the lock already
    exists, inspects the recorded PID: a live process means a real concurrent
    migration (return False, do NOT touch its lock); a gone/invalid PID is a
    stale lock that is reclaimed, after which we re-attempt the atomic create
    once (losing that second race also returns False).
    """

    def _try_create() -> bool:
        try:
            with open(lock_path, "x", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            return True
        except FileExistsError:
            return False

    if _try_create():
        return True

    # Lock exists — inspect the holder.
    raw_pid = ""
    try:
        raw_pid = lock_path.read_text(encoding="utf-8").strip()
        existing_pid = int(raw_pid)
    except (ValueError, OSError):
        _warn(f"stale migration lock (invalid PID {raw_pid!r}); removing: {lock_path}")
        lock_path.unlink(missing_ok=True)
    else:
        alive = False
        try:
            os.kill(existing_pid, 0)
            alive = True
        except ProcessLookupError:
            pass  # process gone → stale lock
        except PermissionError:
            alive = True  # process exists, different user
        except OSError:
            pass  # other error → assume stale

        if alive:
            _fail(
                f"migration already in progress (PID {existing_pid}); "
                f"remove {lock_path} if the process is gone and retry."
            )
            return False
        _warn(f"stale migration lock (PID {existing_pid} gone); removing: {lock_path}")
        lock_path.unlink(missing_ok=True)

    # Stale lock cleared — re-attempt the atomic create once. Losing this race
    # means another process grabbed it in the gap, so we yield to it.
    if _try_create():
        return True
    _fail(
        f"migration lock contention at {lock_path}; another run won the race — retry."
    )
    return False


def _run_migration(
    wiki: Path,
    sentinel: Path,
    *,
    dry_run: bool,
    force: bool,
) -> int:
    """Inner migration logic (called inside the PID lock context)."""
    # ── Idempotency ────────────────────────────────────────────────────────
    if sentinel.exists() and not force:
        try:
            info = json.loads(sentinel.read_text(encoding="utf-8"))
            migrated_at = info.get("migrated_at", "unknown")
        except Exception:  # noqa: BLE001
            migrated_at = "unknown"
        print(f"corpus already migrated at {migrated_at}. Use --force to re-run.")
        return 0

    # ── Build plan ─────────────────────────────────────────────────────────
    plan = _migration_plan(wiki)

    if not plan:
        if dry_run:
            print("Nothing to migrate (no OLD-layout paths found).")
            return 0
        # Mark as clean even if already on the new layout
        wiki_hidden_dir(wiki).mkdir(parents=True, exist_ok=True)
        _write_migration_sentinel(sentinel)
        _ok("Nothing to migrate (no OLD-layout paths found); sentinel written.")
        return 0

    # ── Dry-run report ─────────────────────────────────────────────────────
    if dry_run:
        print(f"Migration plan for: {wiki}")
        print()
        for desc, old, new, is_dir in plan:
            kind = "dir " if is_dir else "file"
            print(f"  [{kind}] {_safe_rel(old, wiki)}  →  {_safe_rel(new, wiki)}")
        print()
        print("Ledger rewrites (after copy):")
        print("  archived_to : /_archive/…  →  /_sources/…")
        print("  logs_dir    : /.runs/…     →  /.wiki/runs/…")
        print()
        print("No changes made (--dry-run).")
        return 0

    # ── Create hidden subtree root ─────────────────────────────────────────
    wiki_hidden_dir(wiki).mkdir(parents=True, exist_ok=True)

    # ── Phase 1: Copy OLD → NEW ────────────────────────────────────────────
    # H4: a copy failure (e.g. ENOSPC) must leave OLD intact and exit non-zero;
    # re-running resumes because every copy is idempotent (guarded file-by-file
    # copy for dirs; existence check for the single files).
    print(f"Migrating {wiki} …")
    print()
    print("Phase 1/4  copy OLD → NEW")
    try:
        for desc, old, new, is_dir in plan:
            old_rel = _safe_rel(old, wiki)
            new_rel = _safe_rel(new, wiki)
            if is_dir:
                # M1: never clobber a newer/differing target inside the dir.
                _guarded_copytree(old, new, wiki)
            else:
                new.parent.mkdir(parents=True, exist_ok=True)
                # M1: preserve an existing target file that differs from OLD.
                if new.exists() and _files_differ(old, new):
                    _warn(
                        f"  preserved existing {new_rel} "
                        f"(differs from OLD {old_rel}; not overwritten)"
                    )
                    continue
                shutil.copy2(str(old), str(new))
            _ok(f"  {old_rel}  →  {new_rel}")
    except OSError as e:
        _fail(f"copy failed: {e}; OLD paths are intact, re-run to resume")
        return 1

    # ── Phase 2: Ledger key rewrite ────────────────────────────────────────
    print()
    print("Phase 2/4  rewrite ledger keys")
    new_ledger_path = wiki_ledger(wiki)
    if new_ledger_path.exists():
        changed, path_field_lines = _rewrite_ledger_keys(new_ledger_path, wiki)
        _ok(
            f"  {_safe_rel(new_ledger_path, wiki)}: {changed} line(s) rewritten"
            f" (archived_to + logs_dir)"
        )
        # C1: if the ledger HAS absolute-path fields but NONE matched this
        # corpus's location, the prefixes are stale (corpus moved/copied since
        # processing). Deleting _archive/ now would orphan the ledger forever.
        # Abort BEFORE Phase 3/4 — nothing has been deleted yet; the lock is
        # still released by migrate()'s finally.
        if path_field_lines > 0 and changed == 0:
            _fail(
                f"Ledger path fields do not match this corpus location "
                f"(0 of {path_field_lines} rewritten) — aborting to avoid "
                f"corrupting the ledger. Inspect with --dry-run."
            )
            return 1
    else:
        _warn("  no ledger found; skipping key rewrite")

    # ── Phase 3: Verify ────────────────────────────────────────────────────
    print()
    print("Phase 3/4  verify")
    failures: list[str] = []
    for desc, old, new, is_dir in plan:
        old_rel = _safe_rel(old, wiki)
        new_rel = _safe_rel(new, wiki)
        if is_dir:
            old_count = sum(1 for p in old.rglob("*") if p.is_file())
            new_count = sum(1 for p in new.rglob("*") if p.is_file())
            # C2: runs/ holds run-LOGS, not user data. A post-upgrade `weave`
            # may have already written NEW run-logs into the destination, so the
            # target legitimately holds MORE files than OLD .runs/. Accept >= for
            # runs/ only; keep STRICT equality for _sources/, policy, failed.
            ok = (
                (new_count >= old_count) if desc == "runs" else (new_count == old_count)
            )
            if not ok:
                failures.append(f"{old_rel}: file count {old_count} ≠ {new_count}")
            else:
                _ok(f"  {new_rel}: {new_count} file(s)")
        elif desc == "ledger":
            # The ledger is intentionally rewritten (path values change so byte
            # size differs).  Verify by non-empty entry count instead.
            old_entries = [
                ln for ln in old.read_text(encoding="utf-8").splitlines() if ln.strip()
            ]
            new_text = new.read_text(encoding="utf-8") if new.exists() else ""
            new_entries = [ln for ln in new_text.splitlines() if ln.strip()]
            if len(old_entries) != len(new_entries):
                failures.append(
                    f"{old_rel}: entry count {len(old_entries)} ≠ {len(new_entries)}"
                )
            else:
                _ok(f"  {new_rel}: {len(new_entries)} entries")
        else:
            old_size = old.stat().st_size
            new_size = new.stat().st_size if new.exists() else -1
            if old_size != new_size:
                failures.append(f"{old_rel}: size {old_size} B ≠ {new_size} B")
            else:
                _ok(f"  {new_rel}: {new_size} B")

    if failures:
        _fail("Verification failed — aborting before any deletion:")
        for msg in failures:
            _fail(f"  {msg}")
        return 1

    # ── Phase 4: Delete OLD ────────────────────────────────────────────────
    print()
    print("Phase 4/4  delete OLD paths")
    for desc, old, new, is_dir in plan:
        old_rel = _safe_rel(old, wiki)
        if is_dir:
            shutil.rmtree(str(old))
        else:
            old.unlink()
        _ok(f"  deleted {old_rel}")

    # ── Sentinel ───────────────────────────────────────────────────────────
    _write_migration_sentinel(sentinel)
    print()
    _ok(f"Migration complete. Sentinel: {_safe_rel(sentinel, wiki)}")
    ensure_obsidian_ready(wiki)
    return 0


# ---------------------------------------------------------------------------
# ask -- read the compiled wiki and answer a question (Phase B)
# ---------------------------------------------------------------------------
#
# MECHANISM (structural, not instructional): the spawned agent's tools are
# constrained in engine_runner.make_ask_spawn_fn so it structurally cannot
# write files or fetch from the web — only read within the wiki directory.
# This forces grounding in wiki content and makes fail-loud-on-absent the
# natural outcome (the agent can't pull from elsewhere).


def ask(
    wiki: str | Path = ".",
    question: str = "",
    *,
    json_out: bool = False,
) -> int:
    """Answer a question by reading the compiled wiki (no embeddings)."""
    import json as _json

    wiki_path = Path(wiki).resolve()
    if not wiki_path.is_dir():
        _fail(f"wiki dir not found: {wiki_path}")
        return 1

    from wiki_weaver.engine_runner import run_ask

    _warn(f"asking wiki at {wiki_path!r}: {question!r}")
    try:
        result = run_ask(wiki_path, question)
    except Exception as e:  # noqa: BLE001
        _fail(f"ask error: {type(e).__name__}: {e}")
        return 1

    if json_out:
        print(
            _json.dumps(
                {
                    "answer": result.answer,
                    "pages_used": result.pages_used,
                    "refused": result.refused,
                },
                indent=2,
            )
        )
    else:
        print(result.answer)
        if result.pages_used:
            print(f"\nPages consulted: {', '.join(result.pages_used)}")
    return 0
