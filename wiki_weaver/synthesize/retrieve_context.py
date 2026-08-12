"""wiki_weaver.synthesize.retrieve_context -- corpus-scoped reading list for
the current gap.

Deterministic bookkeeping only (no model call), mirroring ``ask.load_index``'s
index-first shape but seeded from a KNOWN gap term + a KNOWN source set
(``find_gaps`` already determined which sources discuss this term -- no
guessing needed) rather than a free-text question needing BM25 discovery
from scratch. The gap's own ``source_ids`` (see ``find_gaps.GapCandidate``)
ARE the primary reading list; a BM25 pass over the WIKI (reusing the same
ranker ``retrieve_slice``/``load_index`` already use) surfaces any existing
pages that already touch this term, so ``answer_gap`` can check them for
related, non-duplicate content and cross-link instead of writing a second,
disconnected page.

Usage:
    python3 -m wiki_weaver.synthesize.retrieve_context --wiki-root <path> [--k 6]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver import bm25
from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, refresh_page_index

DEFAULT_K = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.retrieve_context")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="BM25 top-k related wiki pages (default: 6)")
    return parser


def _read_current_gap(wr: WikiRoot) -> dict:
    path = wr.current_gap_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- select_gap must run first")
    return json.loads(path.read_text(encoding="utf-8"))


def build_gap_context(wr: WikiRoot, gap: dict, k: int) -> dict:
    """``{term, source_count, source_ids, related_pages}`` -- the exact set
    ``answer_gap`` reads: source_ids are the corpus's OWN evidence for this
    term (from ``find_gaps``); related_pages are existing wiki pages worth
    checking before writing a new one (cross-link opportunities, not a
    read-access boundary the way ``retrieve_slice``'s ingest-time slice is --
    this pass already knows exactly which sources matter)."""
    index = refresh_page_index(wr)
    corpus_tokens = {pid: entry["tokens"] for pid, entry in index.items()}
    query_tokens = bm25.tokenize(gap["term"])
    ranker = bm25.BM25(corpus_tokens)
    hits = ranker.top_k(query_tokens, k) if corpus_tokens else []
    related_pages = [{"page": pid, "title": index[pid]["title"], "score": score} for pid, score in hits]

    return {
        "term": gap["term"],
        "source_count": gap["source_count"],
        "source_ids": gap["source_ids"],
        "related_pages": related_pages,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        gap = _read_current_gap(wr)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    context = build_gap_context(wr, gap, args.k)

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.gap_context_file, json.dumps(context, indent=2) + "\n")
    print(
        f"gap context for {gap['term']!r}: {len(gap['source_ids'])} source(s), "
        f"{len(context['related_pages'])} related wiki page(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
