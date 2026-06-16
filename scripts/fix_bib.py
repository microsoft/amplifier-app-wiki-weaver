#!/usr/bin/env python3
"""One-time fixer: backfill missing bibliography entries so every inline [N]
citation has a matching `- [N] …` entry in the page's ## Sources section.

Why this is provably correct: it delegates to pipeline/complete_bib.py which
harvests the best available entry for each id from the corpus itself (preferring
URL-bearing lines) and falls back to a title derived from the registry filename.
Because the backfill uses only data already present in the wiki and its
.sources.json registry, no information is fabricated.

Usage:
    python scripts/fix_bib.py <wiki_dir> [<wiki_dir> ...]            # dry-run
    python scripts/fix_bib.py <wiki_dir> [<wiki_dir> ...] --apply   # writes (after backup)
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

# Delegate to the shared bib-completion core — NO duplicate logic here.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from pipeline.complete_bib import (  # noqa: E402
    BIB_LINE,
    INLINE_CITE,
    WIKILINK,
    _load_sources,
    _harvest_bib_entries,
    _complete_page,
)


def _count_orphans(wiki_dir: Path, valid_ids: set[str]) -> int:
    """Count current orphan (page, id) pairs — for dry-run report."""
    count = 0
    for page in sorted(wiki_dir.glob("*.md")):
        if page.stem == "index":
            continue
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        local_bib = {m.group(1) for ln in lines if (m := BIB_LINE.match(ln))}
        body = WIKILINK.sub("", "\n".join(ln for ln in lines if not BIB_LINE.match(ln)))
        inline = {
            m.group(1) for m in INLINE_CITE.finditer(body) if m.group(1) in valid_ids
        }
        count += len(inline - local_bib)
    return count


def process_wiki(wiki: Path, apply: bool) -> None:
    sources_json = wiki / ".sources.json"
    valid_ids, id_to_filename = _load_sources(sources_json)
    if not valid_ids:
        print(f"  {wiki}: no .sources.json found — skipped")
        return

    pages = sorted(wiki.glob("*.md"))
    if not pages:
        print(f"  {wiki}: no .md pages")
        return

    before_orphans = _count_orphans(wiki, valid_ids)
    harvest = _harvest_bib_entries(wiki, valid_ids)

    print(f"  {wiki}")
    print(f"     pages={len(pages)}  orphan_(page,id)_pairs_before={before_orphans}")

    if not apply:
        # Dry-run: simulate without writing.
        simulated_added = 0
        for page in pages:
            if page.stem == "index":
                continue
            text = page.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            from pipeline.complete_bib import _find_sources_section as _fss

            sec_start, sec_end = _fss(lines)
            sec_lines = lines[sec_start + 1 : sec_end] if sec_start >= 0 else []
            existing = {m.group(1) for ln in sec_lines if (m := BIB_LINE.match(ln))}
            body = WIKILINK.sub(
                "", "\n".join(ln for ln in lines if not BIB_LINE.match(ln))
            )
            inline = {
                m.group(1)
                for m in INLINE_CITE.finditer(body)
                if m.group(1) in valid_ids
            }
            simulated_added += len(inline - existing)
        print(f"     entries_to_add (dry-run)={simulated_added}")
        return

    # Apply: take a backup, then run the completer.
    backup = wiki.parent / f"{wiki.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(wiki, backup)
    print(f"     backup -> {backup}")

    pages_changed = 0
    entries_added = 0
    fallback_count = 0
    for page in pages:
        if page.stem == "index":
            continue
        changed, added, fallbacks = _complete_page(
            page, valid_ids, id_to_filename, harvest
        )
        if changed:
            pages_changed += 1
            entries_added += added
            fallback_count += fallbacks

    after_orphans = _count_orphans(wiki, valid_ids)
    print(
        f"     APPLIED: pages_changed={pages_changed}"
        f"  entries_added={entries_added}"
        f"  fallback_count={fallback_count}"
        f"  orphans_after={after_orphans}"
    )


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2
    print(f"=== fix_bib ({'APPLY' if apply else 'DRY-RUN'}) ===")
    for d in args:
        process_wiki(Path(d), apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
