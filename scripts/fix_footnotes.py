#!/usr/bin/env python3
"""One-time fixer: convert all citations to Obsidian-native footnotes so every
inline [^N] ref has a matching [^N]: def in the page's ## Sources section.

Why this is provably correct: it delegates to pipeline/footnotes.py which
harvests the best available footnote def for each id from the corpus itself
(preferring URL-bearing defs) and falls back to a title derived from the
registry filename.  No information is fabricated.

Usage:
    python scripts/fix_footnotes.py <wiki_dir> [<wiki_dir> ...]            # dry-run
    python scripts/fix_footnotes.py <wiki_dir> [<wiki_dir> ...] --apply   # writes (after backup)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Delegate to the shared footnotes core — NO duplicate logic here.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from pipeline.footnotes import (  # noqa: E402
    _BIB_BULLET,
    _INLINE_CODE,
    _PLAIN_CITE,
    _WIKILINK,
    _code_fence_line_set,
    _find_sources_section,
    _load_sources,
    footnote_citations,
)


def _count_pending_changes(wiki_dir: Path, valid_ids: set[str]) -> dict[str, int]:
    """Count changes footnote_citations would make — used for the dry-run report.

    Returns a dict with:
      refs_to_convert  – inline [N] refs (valid ids, outside code) → [^N]
      defs_to_convert  – `- [N] text` bullets under ## Sources → [^N]: defs
      pages_affected   – distinct pages that would be touched
    """
    refs = 0
    defs = 0
    pages_affected = 0

    for page in sorted(wiki_dir.glob("*.md")):
        if page.stem == "index":
            continue
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        lines = text.splitlines()
        code_lines = _code_fence_line_set(lines)
        sec_start, sec_end = _find_sources_section(lines)

        page_refs = 0
        page_defs = 0

        for i, line in enumerate(lines):
            if i in code_lines:
                continue
            if sec_start >= 0 and sec_start < i < sec_end:
                m = _BIB_BULLET.match(line)
                if m and m.group(1) in valid_ids:
                    page_defs += 1
            else:
                masked = _WIKILINK.sub("", _INLINE_CODE.sub("", line))
                page_refs += sum(
                    1 for m in _PLAIN_CITE.finditer(masked) if m.group(1) in valid_ids
                )

        if page_refs or page_defs:
            pages_affected += 1
            refs += page_refs
            defs += page_defs

    return {
        "refs_to_convert": refs,
        "defs_to_convert": defs,
        "pages_affected": pages_affected,
    }


def process_wiki(wiki: Path, apply: bool) -> None:
    sources_json = wiki / ".sources.json"
    valid_ids, _ = _load_sources(sources_json)
    if not valid_ids:
        print(f"  {wiki}: no .sources.json found — skipped")
        return

    pages = sorted(wiki.glob("*.md"))
    if not pages:
        print(f"  {wiki}: no .md pages")
        return

    counts = _count_pending_changes(wiki, valid_ids)
    print(f"  {wiki}")
    print(
        f"     pages={len(pages)}"
        f"  refs_to_convert={counts['refs_to_convert']}"
        f"  defs_to_convert={counts['defs_to_convert']}"
        f"  pages_affected={counts['pages_affected']}"
    )

    if not apply:
        print("     (dry-run — pass --apply to write changes)")
        return

    # Apply: take a backup, then run the converter.
    from datetime import datetime
    import shutil

    backup = wiki.parent / f"{wiki.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(wiki, backup)
    print(f"     backup -> {backup}")

    stats = footnote_citations(wiki)
    print(
        f"     APPLIED:"
        f" pages_changed={stats['pages_changed']}"
        f" refs_converted={stats['refs_converted']}"
        f" defs_converted={stats['defs_converted']}"
        f" defs_backfilled={stats['defs_backfilled']}"
    )


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2
    print(f"=== fix_footnotes ({'APPLY' if apply else 'DRY-RUN'}) ===")
    for d in args:
        process_wiki(Path(d), apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
