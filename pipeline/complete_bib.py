#!/usr/bin/env python3
"""Complete missing bibliography entries in-place in a wiki.

Shared bibliography-completer core for the wiki-weaver pipeline and standalone use.

Public API:
    complete_bibliography(wiki_dir) -> (pages_changed, entries_added, fallback_count)
        Backfills missing ## Sources entries IN-PLACE, no backup created. Idempotent.

Algorithm:
    1. Load valid source ids from <wiki>/.sources.json (id -> filename map).
    2. Harvest a global id -> best_bib_entry map from ALL pages (prefer URL-containing lines).
    3. For each page, detect inline-cited ids (not inside [[...]], not [N](, not [^N]).
    4. For each cited id missing from the page's ## Sources section:
       - Append the harvested entry; if not harvested, fall back to a title derived
         from the registry filename.
    5. Ensure the ## Sources section exists (creates at end if absent).
    6. Keep the section's - [N] lines sorted ascending by N, deduped. Idempotent.
    7. Never modifies existing entries — only ADDS missing ones.

CLI usage (in-pipeline mode — no backup, always exits 0):
    python pipeline/complete_bib.py <wiki_dir>

CLI usage (standalone / one-time mode — creates a dated backup first):
    python pipeline/complete_bib.py <wiki_dir> --backup
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Patterns (mirrors the verification snippet in the task spec for consistency)
# ---------------------------------------------------------------------------

# Matches a bibliography entry line: `- [N] text` (or `* [N] text`)
BIB_LINE = re.compile(r"^\s*[-*]\s+\[(\d+)\]\s")

# Matches an inline citation [N] that is:
#   - not preceded by ! (image alt) or ^ (footnote ref `[^N]`)
#   - not followed by ( (markdown link `[N](url)`)
INLINE_CITE = re.compile(r"(?<![!^])\[(\d+)\](?!\()")

# Strip [[wikilinks]] before scanning body for inline citations so that
# [[tag|label]] and [[page-name]] patterns don't feed false citation matches.
WIKILINK = re.compile(r"\[\[[^\]]+\]\]")

# Detect URLs to prefer URL-bearing entries when harvesting the best entry.
_URL_PAT = re.compile(r"https?://")

# Strip trailing (Part_One)-style parenthetical noise from fallback titles.
_TRAILING_PARENS = re.compile(r"\s*\([^)]+\)\s*$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_sources(sources_json: Path) -> tuple[set[str], dict[str, str]]:
    """Load valid source ids and id->filename map from .sources.json.

    Returns (valid_ids, id_to_filename).  Both empty if the file is absent or
    unparseable — caller should handle this gracefully.
    """
    if not sources_json.is_file():
        return set(), {}
    try:
        data = json.loads(sources_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), {}
    valid_ids: set[str] = set()
    id_to_filename: dict[str, str] = {}
    for src in data.get("sources", []):
        nid = str(src.get("id", ""))
        if nid:
            valid_ids.add(nid)
            fname = src.get("filename", "")
            if fname:
                id_to_filename[nid] = fname
    return valid_ids, id_to_filename


def _filename_to_title(filename: str) -> str:
    """Convert a registry filename to a human-readable fallback title.

    Steps (minimal, per spec):
      1. Strip .md extension.
      2. Replace _ and - with spaces.
      3. Strip a trailing (Part_One)-style parenthetical.
    """
    stem = Path(filename).stem  # strips .md
    title = stem.replace("_", " ").replace("-", " ")
    title = _TRAILING_PARENS.sub("", title).strip()
    return title


def _harvest_bib_entries(wiki_dir: Path, valid_ids: set[str]) -> dict[str, str]:
    """Scan every page and build a global id -> best bib entry map.

    'Best' = entry that contains a URL (https?://).  A URL-bearing entry from
    any page is preferred over a non-URL entry from any other page.  If no
    URL-bearing entry exists for an id, keep the first entry seen.

    Returns a dict mapping id (str) -> entry TEXT (everything after `- [N] `).
    """
    harvest: dict[str, str] = {}
    for page in sorted(wiki_dir.glob("*.md")):
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = BIB_LINE.match(line)
            if not m:
                continue
            nid = m.group(1)
            if nid not in valid_ids:
                continue
            # Entry text: everything after the matched prefix `- [N] `
            entry_text = line[m.end() :].rstrip()
            if nid not in harvest:
                harvest[nid] = entry_text
            elif _URL_PAT.search(entry_text) and not _URL_PAT.search(harvest[nid]):
                # Upgrade to the URL-bearing entry.
                harvest[nid] = entry_text
    return harvest


def _find_sources_section(lines: list[str]) -> tuple[int, int]:
    """Locate the ## Sources section in a page's lines.

    Returns (start_idx, end_idx) where start_idx is the index of the
    `## Sources` header line, and end_idx is exclusive (= next `## ` heading
    index, or len(lines) if none follows).

    Returns (-1, -1) if there is no ## Sources section.
    """
    start = -1
    for i, line in enumerate(lines):
        if re.match(r"^##\s+Sources\s*$", line):
            start = i
            break
    if start == -1:
        return -1, -1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^##\s+", lines[i]):
            end = i
            break
    return start, end


def _complete_page(
    page: Path,
    valid_ids: set[str],
    id_to_filename: dict[str, str],
    harvest: dict[str, str],
) -> tuple[bool, int, int]:
    """Backfill missing bibliography entries in a single page, in place.

    Returns (changed, entries_added, fallback_count).
    """
    text = page.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    trailing_newline = text.endswith("\n")

    # ---- Locate the ## Sources section ----------------------------------------
    sec_start, sec_end = _find_sources_section(lines)

    # ---- Collect existing local bib entries (id -> full line text) ------------
    sec_lines: list[str] = lines[sec_start + 1 : sec_end] if sec_start >= 0 else []
    existing_bib: dict[str, str] = {}
    for ln in sec_lines:
        m = BIB_LINE.match(ln)
        if m:
            existing_bib[m.group(1)] = ln

    # ---- Detect inline-cited ids in the non-bib body --------------------------
    # Strip wikilinks first, then scan for [N] patterns that are valid source ids.
    non_bib_lines = [ln for ln in lines if not BIB_LINE.match(ln)]
    body_text = WIKILINK.sub("", "\n".join(non_bib_lines))
    inline_ids = {
        m.group(1) for m in INLINE_CITE.finditer(body_text) if m.group(1) in valid_ids
    }

    # ---- Find missing: cited inline but absent from local Sources section -----
    missing = inline_ids - set(existing_bib.keys())
    if not missing:
        return False, 0, 0

    # ---- Build new entries for each missing id --------------------------------
    new_entries: dict[str, str] = {}
    fallback_count = 0
    for nid in sorted(missing, key=int):
        if nid in harvest:
            new_entries[nid] = f"- [{nid}] {harvest[nid]}"
        else:
            fname = id_to_filename.get(nid, "")
            title = _filename_to_title(fname) if fname else f"Source {nid}"
            new_entries[nid] = f"- [{nid}] {title}"
            fallback_count += 1

    # ---- Merge and sort: existing (unchanged) + new entries -------------------
    # new_entries only contains ids NOT in existing_bib, so no collision.
    all_bib: dict[str, str] = {**existing_bib, **new_entries}
    sorted_bib_lines = [all_bib[k] for k in sorted(all_bib.keys(), key=int)]

    # ---- Reconstruct the page -------------------------------------------------
    if sec_start >= 0:
        # Find the bib-line span within the section content.
        first_bib_idx: int | None = None
        last_bib_idx: int | None = None
        for i, ln in enumerate(sec_lines):
            if BIB_LINE.match(ln):
                if first_bib_idx is None:
                    first_bib_idx = i
                last_bib_idx = i

        if first_bib_idx is None:
            # Section exists but has no bib lines yet: append after existing content.
            before_bib: list[str] = list(sec_lines)
            after_bib: list[str] = []
        else:
            assert last_bib_idx is not None  # set whenever first_bib_idx is set
            before_bib = list(sec_lines[:first_bib_idx])
            after_bib = list(sec_lines[last_bib_idx + 1 :])

        new_sec_content = before_bib + sorted_bib_lines + after_bib
        new_lines = lines[: sec_start + 1] + new_sec_content + lines[sec_end:]
    else:
        # No Sources section: create one at the end of the page.
        stripped = list(lines)
        while stripped and not stripped[-1].strip():
            stripped.pop()
        new_lines = stripped + ["", "## Sources", ""] + sorted_bib_lines

    new_text = "\n".join(new_lines)
    if trailing_newline:
        new_text += "\n"

    if new_text == text:
        # Idempotency guard: content unchanged means nothing to write.
        return False, 0, 0

    page.write_text(new_text, encoding="utf-8")
    return True, len(missing), fallback_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def complete_bibliography(wiki_dir: Path | str) -> tuple[int, int, int]:
    """Backfill missing bibliography entries in all wiki pages, IN-PLACE.

    For every inline-cited valid source id that lacks a `- [N] …` entry in the
    page's ## Sources section, an entry is added: harvested from the corpus
    (preferring URL-bearing lines) or derived from the registry filename as a
    fallback.

    Returns (pages_changed, entries_added, fallback_count).
    Idempotent: a second run on an already-complete wiki returns (0, 0, 0).
    Skips index.md (navigation root; no citations expected).
    """
    wiki_dir = Path(wiki_dir)
    sources_json = wiki_dir / ".sources.json"

    valid_ids, id_to_filename = _load_sources(sources_json)
    if not valid_ids:
        # No registry = nothing to validate; exit clean.
        return 0, 0, 0

    harvest = _harvest_bib_entries(wiki_dir, valid_ids)

    pages_changed = 0
    total_added = 0
    total_fallbacks = 0

    for page in sorted(wiki_dir.glob("*.md")):
        if page.stem == "index":
            continue
        changed, added, fallbacks = _complete_page(
            page, valid_ids, id_to_filename, harvest
        )
        if changed:
            pages_changed += 1
            total_added += added
            total_fallbacks += fallbacks

    return pages_changed, total_added, total_fallbacks


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--backup"]
    backup = "--backup" in sys.argv
    if not args:
        print(__doc__)
        return 2

    wiki_dir = Path(args[0])
    if not wiki_dir.is_dir():
        print(f"FAIL: wiki dir not found: {wiki_dir}", file=sys.stderr)
        return 1

    if backup:
        bak = wiki_dir.parent / f"{wiki_dir.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(wiki_dir, bak)
        print(f"backup -> {bak}")

    pages_changed, entries_added, fallback_count = complete_bibliography(wiki_dir)
    print(
        f"complete_bibliography: pages_changed={pages_changed}"
        f" entries_added={entries_added}"
        f" fallback_count={fallback_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
