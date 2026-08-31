"""wiki_weaver.correct.locate_pages -- search the wiki for pages that assert
the claim a correction is about to fix.

Deterministic (no model call): reuses the exact BM25 ranking
``wiki_weaver.ingest.retrieve_slice``/``wiki_weaver.lib.build_slice`` already
use for the ingest read-bound (``docs/DESIGN.md`` \u00a73's measured S7 result)
for RANKING, but ranking alone is not enough to decide "found nothing":
BM25's IDF is corpus-relative, so on a small wiki even a single shared
grammatical word (\"a\", \"to\", \"the\"...) gets a nonzero score -- score > 0
is therefore not a meaningful presence/absence signal by itself (verified:
a claim sharing no real vocabulary with a page still scored > 0 against it
in a 1-2 page test corpus, purely from stopword overlap). A page only
counts as a real candidate here if it ALSO shares at least one CONTENT word
(a token outside ``_STOPWORDS`` below) with the claim -- BM25 still decides
the ranking among candidates that pass this filter. This is a narrow,
locate_pages-specific requirement, not a change to ``bm25.tokenize`` or any
other consumer: ``retrieve_slice`` tolerates weak matches because its own
count/budget ceiling bounds cost regardless of precision; this tool feeds
``re_derive``'s WRITE scope, where a false-positive page could get touched
by a targeted rewrite it has nothing to do with, so a real content-word
match is the deliberately stricter bar.

The judgment call of whether a candidate page actually ASSERTS the claim
(as opposed to merely sharing vocabulary with it) is left to ``re_derive``
(the box node immediately downstream in ``pipeline/correct.dot``) -- this
tool only narrows the search space it needs to read, the same division of
labor ``retrieve_slice`` already has with ``weave``.

An empty result (zero pages found) is a normal, legitimate outcome, not a
failure: it means the claim's vocabulary matches nothing this wiki
currently says, which is exactly the "stop doing this class of thing"
(general) correction shape -- see ``wiki_weaver.correct.persist_lens``'s
scope classification, which uses this tool's own output as its evidence.

Usage:
    python3 -m wiki_weaver.correct.locate_pages --wiki-root <path> \\
        --claim-file .ai/correction-text.md --out .ai/affected-pages.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver import bm25
from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, load_wiki_pages, resolve_path

# A correction is meant to be a TARGETED fix (pipeline/correct.dot's own
# header: "only re-run ingestion against the specific sources traced as
# having fed the corrected claims"), not a corpus-wide rewrite. If a claim's
# lexical signature genuinely matches more than this many pages, that excess
# is dropped (best BM25 matches kept) rather than ballooning re_derive's
# read/write scope -- mirrors lib.py's MAX_SLICE_CANDIDATES discipline for
# the same reason (bound cost, never silently).
MAX_AFFECTED_PAGES = 10

# Deliberately small, closed-vocabulary function-word list -- used ONLY to
# decide whether a page shares real CONTENT vocabulary with the claim (see
# module docstring). Not a general-purpose stopword list for ranking (BM25's
# own IDF weighting, unchanged, still does that job everywhere else in this
# codebase); this is a narrow presence/absence gate specific to this tool.
_STOPWORDS = frozenset(
    """
    a an the and or but if of to in on at for with by is are was were be
    been being this that these those it its as not no never always must
    should will would can could do does did has have had than then so
    such any all each other about into from over under up down out off
    again further here there when where why how what who which whom we
    you he she they i me my your his her our their
    """.split()
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.correct.locate_pages")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--claim-file", required=True)
    parser.add_argument("--out", required=True)
    return parser


def locate_pages(wiki_dir: Path, claim_text: str, ceiling: int = MAX_AFFECTED_PAGES) -> list[str]:
    """Page ids (BM25-ranked, best first) that share at least one CONTENT
    word with ``claim_text`` (see module docstring for why "BM25 score > 0"
    alone is not a reliable presence/absence signal). BM25 still decides the
    ranking among pages that pass this content-word gate."""
    pages = load_wiki_pages(wiki_dir)
    if not pages:
        return []
    query_tokens = bm25.tokenize(claim_text)
    if not query_tokens:
        return []

    content_query_tokens = {t for t in query_tokens if t not in _STOPWORDS}
    if not content_query_tokens:
        # A claim made entirely of function words (degenerate, but never
        # silently guessed at) -- fall back to the full token set rather
        # than gating on an empty requirement that would match nothing.
        content_query_tokens = set(query_tokens)

    candidates = {pid for pid, page in pages.items() if content_query_tokens & set(page.tokens)}
    if not candidates:
        return []

    ranker = bm25.BM25({pid: page.tokens for pid, page in pages.items()})
    ranked = ranker.top_k(query_tokens, len(pages))
    return [pid for pid, _score in ranked if pid in candidates][:ceiling]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    claim_path = Path(args.claim_file)

    if not claim_path.is_file():
        print(f"refusing to locate pages: {claim_path} does not exist", file=sys.stderr)
        return 1
    claim_text = claim_path.read_text(encoding="utf-8")
    if not claim_text.strip():
        print(f"refusing to locate pages: {claim_path} is empty", file=sys.stderr)
        return 1

    pages = locate_pages(wr.wiki_dir, claim_text)

    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)
    atomic_write_text(out_path, json.dumps({"pages": pages}, indent=2, ensure_ascii=False) + "\n")
    print(
        f"located {len(pages)} affected page(s): {', '.join(pages) if pages else '(none)'}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
