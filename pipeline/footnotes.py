#!/usr/bin/env python3
"""Convert citations to Obsidian-native footnotes in a wiki (in-place).

Shared footnote-citation core for the wiki-weaver pipeline and standalone use.

Public API:
    footnote_citations(wiki_dir) -> dict[str, int]
        Converts inline [N] → [^N] and bibliography bullets → footnote defs
        IN-PLACE. Idempotent. No backup created.

Algorithm:
    1. Load valid source ids from <wiki>/.sources.json (id -> filename map).
    2. Pass 1 – convert each page:
       a. Convert inline [N] (valid id, not in code fences/inline code, not
          already [^N], not a markdown link [N](...), not inside [[...]]) → [^N].
       b. Convert bibliography bullets under ## Sources:
          `- [N] <text>` → `[^N]: <text>`.
    3. Harvest a global id -> best footnote-def text from ALL pages
       (prefer URL-bearing defs).
    4. Pass 2 – backfill each page:
       - Every inline [^N] (valid id) lacking a [^N]: def on its page gets one
         from the harvest (registry-filename-derived title fallback if absent
         everywhere).
       - Sort defs ascending by N; dedupe (one def per id per page).
    5. Idempotent: already-[^N] inline refs and existing [^N]: defs are left
       as-is on both passes.

CLI usage (in-pipeline mode — no backup, always exits 0):
    python pipeline/footnotes.py <wiki_dir>

CLI usage (standalone / one-time mode — creates a dated backup first):
    python pipeline/footnotes.py <wiki_dir> --backup
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Matches a footnote definition line anchored at start: `[^N]: text`
_FOOTNOTE_DEF = re.compile(r"^\[\^(\d+)\]:\s+(.*)")

# Matches an inline footnote ref [^N] NOT followed by : (to exclude def lines).
_FOOTNOTE_REF = re.compile(r"\[\^(\d+)\](?!:)")

# Matches a plain inline citation [N] suitable for conversion to [^N].
# Excludes: image alts ![ , already-[^N] refs (^ lookbehind), markdown links [N](...).
_PLAIN_CITE = re.compile(r"(?<![!^])\[(\d+)\](?!\()")

# Matches a bibliography bullet: `- [N] text` or `* [N] text`.
_BIB_BULLET = re.compile(r"^\s*[-*]\s+\[(\d+)\]\s+(.*)")

# Wikilinks — masked before citation scanning to avoid false matches.
_WIKILINK = re.compile(r"\[\[[^\]]+\]\]")

# Inline code spans — masked before citation scanning.
_INLINE_CODE = re.compile(r"`[^`\n]*`")

# Fenced code block delimiters (``` or ~~~, 3+ chars).
_FENCE = re.compile(r"^(`{3,}|~{3,})")

# URL detector — prefer URL-bearing defs when harvesting.
_URL_PAT = re.compile(r"https?://")

# Strip trailing (Part_One)-style parenthetical noise from fallback titles.
_TRAILING_PARENS = re.compile(r"\s*\([^)]+\)\s*$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_sources(sources_json: Path) -> tuple[set[str], dict[str, str]]:
    """Load valid source ids and id->filename map from .sources.json.

    Returns (valid_ids, id_to_filename).  Both empty if the file is absent or
    unparseable — caller handles this gracefully.
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

    Steps: strip .md, replace _ and - with spaces, strip trailing parenthetical.
    """
    stem = Path(filename).stem
    title = stem.replace("_", " ").replace("-", " ")
    title = _TRAILING_PARENS.sub("", title).strip()
    return title


def _code_fence_line_set(lines: list[str]) -> set[int]:
    """Return the set of line indices that are inside (or are) fenced code blocks.

    Both the opening ``` / ~~~ delimiter line and the closing one are included,
    as is every line between them.  Safe on unterminated fences (all remaining
    lines are treated as code).
    """
    in_fence = False
    fence_char = ""
    code_lines: set[int] = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _FENCE.match(stripped)
        if not in_fence:
            if m:
                in_fence = True
                fence_char = m.group(1)[0]  # ` or ~
                code_lines.add(i)
        else:
            code_lines.add(i)
            # Close when we see a fence starting with the same char.
            if stripped.startswith(fence_char * 3):
                in_fence = False
    return code_lines


def _find_sources_section(lines: list[str]) -> tuple[int, int]:
    """Locate the ## Sources section.

    Returns (start_idx, end_idx) where start_idx is the index of the
    `## Sources` header line and end_idx is exclusive (next ## heading or
    len(lines)).  Returns (-1, -1) if no ## Sources section exists.
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


def _convert_inline_line(line: str, valid_ids: set[str]) -> str:
    """Convert [N] → [^N] in one line, masking code spans and wikilinks first.

    Already-converted [^N] refs are unaffected (lookbehind on ^).
    Markdown image alts ![ and inline links [N](...) are also skipped.
    """
    # Mask wikilinks so [N] inside [[...]] is never rewritten.
    wiki_saved: list[str] = []

    def _save_wiki(m: re.Match) -> str:
        wiki_saved.append(m.group(0))
        return f"\x00W{len(wiki_saved) - 1}\x00"

    masked = _WIKILINK.sub(_save_wiki, line)

    # Mask inline code spans.
    code_saved: list[str] = []

    def _save_code(m: re.Match) -> str:
        code_saved.append(m.group(0))
        return f"\x00C{len(code_saved) - 1}\x00"

    masked = _INLINE_CODE.sub(_save_code, masked)

    # Convert [N] → [^N] for valid ids.
    def _repl_cite(m: re.Match) -> str:
        nid = m.group(1)
        return f"[^{nid}]" if nid in valid_ids else m.group(0)

    masked = _PLAIN_CITE.sub(_repl_cite, masked)

    # Restore saved code spans then wikilinks.
    for i, cs in enumerate(code_saved):
        masked = masked.replace(f"\x00C{i}\x00", cs)
    for i, wl in enumerate(wiki_saved):
        masked = masked.replace(f"\x00W{i}\x00", wl)

    return masked


def _harvest_footnote_defs(wiki_dir: Path, valid_ids: set[str]) -> dict[str, str]:
    """Scan every page and build a global id -> best footnote-def-text map.

    'Best' = entry that contains a URL.  A URL-bearing def from any page is
    preferred over a non-URL def from any other page.  If no URL-bearing def
    exists for an id, keep the first def seen.

    Returns dict mapping id (str) -> definition TEXT (everything after `[^N]: `).
    """
    harvest: dict[str, str] = {}
    for page in sorted(wiki_dir.glob("*.md")):
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _FOOTNOTE_DEF.match(line)
            if not m:
                continue
            nid = m.group(1)
            if nid not in valid_ids:
                continue
            def_text = m.group(2).rstrip()
            if nid not in harvest:
                harvest[nid] = def_text
            elif _URL_PAT.search(def_text) and not _URL_PAT.search(harvest[nid]):
                harvest[nid] = def_text
    return harvest


def _convert_page_format(
    page: Path,
    valid_ids: set[str],
) -> tuple[bool, int, int]:
    """Pass 1: convert [N] → [^N] and `- [N] text` → `[^N]: text` on one page.

    Lines inside fenced code blocks are left entirely untouched.
    Bib-bullet conversion is limited to the ## Sources section.

    Returns (changed, refs_converted, defs_converted).
    """
    text = page.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    trailing_newline = text.endswith("\n")

    code_lines = _code_fence_line_set(lines)
    sec_start, sec_end = _find_sources_section(lines)

    new_lines: list[str] = []
    refs_converted = 0
    defs_converted = 0

    for i, line in enumerate(lines):
        # Code fence lines are never touched.
        if i in code_lines:
            new_lines.append(line)
            continue

        # Within the Sources section: convert bib bullets → footnote defs.
        if sec_start >= 0 and sec_start < i < sec_end:
            m = _BIB_BULLET.match(line)
            if m and m.group(1) in valid_ids:
                new_lines.append(f"[^{m.group(1)}]: {m.group(2)}")
                defs_converted += 1
            else:
                new_lines.append(line)
            continue

        # Everything else (including the Sources header and lines after end):
        # convert inline [N] → [^N].
        new_line = _convert_inline_line(line, valid_ids)
        if new_line != line:
            # Count net new [^N] instances introduced.
            refs_converted += new_line.count("[^") - line.count("[^")
        new_lines.append(new_line)

    new_text = "\n".join(new_lines)
    if trailing_newline:
        new_text += "\n"

    if new_text == text:
        return False, 0, 0

    page.write_text(new_text, encoding="utf-8")
    return True, refs_converted, defs_converted


def _backfill_page_defs(
    page: Path,
    valid_ids: set[str],
    id_to_filename: dict[str, str],
    harvest: dict[str, str],
) -> tuple[bool, int]:
    """Pass 2: backfill missing [^N]: defs and sort/dedupe the Sources section.

    Collects [^N] refs from non-Sources, non-code lines, then ensures every
    ref has a matching def in the ## Sources section.  Sorts defs ascending
    by N.  Idempotent.

    Returns (changed, defs_added).
    """
    text = page.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    trailing_newline = text.endswith("\n")

    code_lines = _code_fence_line_set(lines)
    sec_start, sec_end = _find_sources_section(lines)

    # Collect existing defs from the Sources section (first def per id wins).
    def_map: dict[str, str] = {}
    if sec_start >= 0:
        for i in range(sec_start + 1, sec_end):
            m = _FOOTNOTE_DEF.match(lines[i])
            if m and m.group(1) in valid_ids:
                nid = m.group(1)
                if nid not in def_map:
                    def_map[nid] = m.group(2).rstrip()

    # Collect [^N] refs from non-Sources, non-code lines.
    ref_ids: set[str] = set()
    for i, line in enumerate(lines):
        if i in code_lines:
            continue
        if sec_start >= 0 and sec_start <= i < sec_end:
            continue
        # Strip wikilinks and inline code before scanning for refs.
        masked = _WIKILINK.sub("", line)
        masked = _INLINE_CODE.sub("", masked)
        for m in _FOOTNOTE_REF.finditer(masked):
            nid = m.group(1)
            if nid in valid_ids:
                ref_ids.add(nid)

    # Nothing to do if no defs and no refs.
    if not def_map and not ref_ids:
        return False, 0

    # Backfill defs for refs that have no matching def.
    missing = ref_ids - set(def_map.keys())
    defs_added = len(missing)
    for nid in sorted(missing, key=int):
        if nid in harvest:
            def_map[nid] = harvest[nid]
        else:
            fname = id_to_filename.get(nid, "")
            def_map[nid] = _filename_to_title(fname) if fname else f"Source {nid}"

    if not def_map:
        return False, 0

    # Build sorted, deduped def lines.
    sorted_def_lines = [
        f"[^{k}]: {v}" for k, v in sorted(def_map.items(), key=lambda x: int(x[0]))
    ]

    # Reconstruct the Sources section preserving non-def lines (blank lines,
    # unknown-id bullets, etc.) that appear before the first or after the last
    # footnote def in the section.
    if sec_start >= 0:
        sec_content = lines[sec_start + 1 : sec_end]
        first_def_rel: int | None = None
        last_def_rel: int | None = None
        for j, ln in enumerate(sec_content):
            if _FOOTNOTE_DEF.match(ln):
                if first_def_rel is None:
                    first_def_rel = j
                last_def_rel = j

        if first_def_rel is None:
            # No defs exist yet: append sorted defs after existing section content.
            new_sec_content = list(sec_content) + sorted_def_lines
        else:
            assert last_def_rel is not None
            before = sec_content[:first_def_rel]
            after = sec_content[last_def_rel + 1 :]
            new_sec_content = list(before) + sorted_def_lines + list(after)

        final_lines = lines[: sec_start + 1] + new_sec_content + lines[sec_end:]
    else:
        # No Sources section at all: create one at the end of the page.
        stripped = list(lines)
        while stripped and not stripped[-1].strip():
            stripped.pop()
        final_lines = stripped + ["", "## Sources", ""] + sorted_def_lines

    new_text = "\n".join(final_lines)
    if trailing_newline:
        new_text += "\n"

    if new_text == text:
        return False, 0

    page.write_text(new_text, encoding="utf-8")
    return True, defs_added


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def footnote_citations(wiki_dir: Path | str) -> dict[str, int]:
    """Convert all citations to Obsidian footnote format IN-PLACE.

    Two-pass idempotent algorithm:
      Pass 1: convert [N] → [^N] inline and `- [N] text` → `[^N]: text` in Sources.
      Pass 2: harvest global defs, then backfill + sort defs on every page.

    Returns a stats dict:
      pages_changed    – total pages written (may count a page once per pass)
      refs_converted   – inline [N] refs converted to [^N]
      defs_converted   – bibliography bullets converted to [^N]: defs
      defs_backfilled  – new [^N]: defs added from corpus harvest / registry

    Idempotent: a second run on a fully-converted wiki returns all zeros.
    Skips index.md (navigation root; no citations expected).
    """
    wiki_dir = Path(wiki_dir)
    sources_json = wiki_dir / ".sources.json"

    valid_ids, id_to_filename = _load_sources(sources_json)
    if not valid_ids:
        return {
            "pages_changed": 0,
            "refs_converted": 0,
            "defs_converted": 0,
            "defs_backfilled": 0,
        }

    non_index = [p for p in sorted(wiki_dir.glob("*.md")) if p.stem != "index"]

    # Pass 1: convert formats on every page.
    p1_changed: set[Path] = set()
    total_refs = 0
    total_defs = 0
    for page in non_index:
        changed, refs, defs = _convert_page_format(page, valid_ids)
        if changed:
            p1_changed.add(page)
            total_refs += refs
            total_defs += defs

    # Harvest global footnote defs from the now-converted pages.
    harvest = _harvest_footnote_defs(wiki_dir, valid_ids)

    # Pass 2: backfill missing defs and sort.
    p2_changed: set[Path] = set()
    total_backfilled = 0
    for page in non_index:
        changed, backfilled = _backfill_page_defs(
            page, valid_ids, id_to_filename, harvest
        )
        if changed:
            p2_changed.add(page)
            total_backfilled += backfilled

    all_changed = p1_changed | p2_changed
    return {
        "pages_changed": len(all_changed),
        "refs_converted": total_refs,
        "defs_converted": total_defs,
        "defs_backfilled": total_backfilled,
    }


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

    stats = footnote_citations(wiki_dir)
    print(
        f"footnote_citations:"
        f" pages_changed={stats['pages_changed']}"
        f" refs_converted={stats['refs_converted']}"
        f" defs_converted={stats['defs_converted']}"
        f" defs_backfilled={stats['defs_backfilled']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
