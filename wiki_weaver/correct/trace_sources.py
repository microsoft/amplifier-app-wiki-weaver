"""wiki_weaver.correct.trace_sources -- given affected pages, find which raw
source ids fed them, using provenance the wiki already records.

Deterministic (no model call, no re-reading of ``sources/``): every wiki
page already carries two independent provenance mechanisms weave's own
prompt requires it to maintain (``pipeline/ingest.dot``'s weave node) --
frontmatter ``sources:`` (the page-level citation) and inline ``NNN-Name.md``
citations in the body (the per-claim citation, ``lib.extract_source_citations``).
This tool unions both across every affected page, so ``re_derive`` knows
exactly which real source files to re-read the corrected claim from.

An empty result (a page with no recorded provenance at all) is possible and
is not treated as an error here -- ``re_derive`` will simply have no traced
source to re-derive that page from and must rely on the correction text
alone; this tool's job is only to surface what provenance already exists,
never to fabricate it.

Usage:
    python3 -m wiki_weaver.correct.trace_sources --wiki-root <path> \\
        --pages-file .ai/affected-pages.json --out .ai/affected-sources.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    extract_source_citations,
    parse_frontmatter,
    resolve_path,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.correct.trace_sources")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--pages-file", required=True)
    parser.add_argument("--out", required=True)
    return parser


def _page_sources(text: str) -> list[str]:
    """Every source id a single page's provenance names: frontmatter
    ``sources:`` (normalized to a list, matching ``lib.refresh_page_index``'s
    own normalization of a bare scalar) UNION inline ``NNN-Name.md``
    citations in the body -- order-preserving, deduplicated."""
    meta, _ = parse_frontmatter(text)
    frontmatter_sources = meta.get("sources", [])
    if not isinstance(frontmatter_sources, list):
        frontmatter_sources = [frontmatter_sources]
    inline = extract_source_citations(text)

    seen: dict[str, None] = {}
    for raw in [*frontmatter_sources, *inline]:
        value = str(raw).strip()
        if value:
            seen.setdefault(value, None)
    return list(seen)


def trace_sources(wiki_dir: Path, page_ids: list[str]) -> list[str]:
    """Source ids fed into ANY of ``page_ids``, order-preserving,
    deduplicated across pages. A page id that does not exist on disk is
    silently skipped (never fabricated) -- it may name a page that was
    itself deleted or renamed since ``locate_pages`` ran."""
    seen: dict[str, None] = {}
    for page_id in page_ids:
        path = wiki_dir / page_id
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for src in _page_sources(text):
            seen.setdefault(src, None)
    return list(seen)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    pages_path = Path(args.pages_file)

    if not pages_path.is_file():
        print(f"refusing to trace sources: {pages_path} does not exist", file=sys.stderr)
        return 1
    try:
        data = json.loads(pages_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"refusing to trace sources: {pages_path} could not be read/parsed: {exc}", file=sys.stderr)
        return 1

    raw_pages = data.get("pages", []) if isinstance(data, dict) else []
    page_ids = [str(p) for p in raw_pages] if isinstance(raw_pages, list) else []

    sources = trace_sources(wr.wiki_dir, page_ids)

    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)
    atomic_write_text(out_path, json.dumps({"sources": sources}, indent=2) + "\n")
    print(
        f"traced {len(sources)} source id(s) from {len(page_ids)} page(s): "
        f"{', '.join(sources) if sources else '(none)'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
