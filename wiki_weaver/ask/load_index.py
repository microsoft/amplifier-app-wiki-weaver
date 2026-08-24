"""wiki_weaver.ask.load_index -- index-first candidate page retrieval.

Reuses the BM25 ranker in ``wiki_weaver.bm25`` -- the SAME measured
load-bearing retrieval mechanism ``ingest.retrieve_slice`` uses (S7:
100% vs 17% recall at k=6 on 365 pages). Unlike ``retrieve_slice`` (which
rebuilds its in-memory corpus fresh every call -- cheap enough at ingest's
one-source-at-a-time cadence, per lib.py's comment), ``ask`` is answered
directly against the wiki with no per-question amortization elsewhere in
the pipeline, so this subcommand maintains a persisted, incrementally
refreshed index at ``wiki/.index/pages.json``
(``wiki_weaver.lib.refresh_page_index``): only pages that are new or
whose mtime changed since the last refresh are actually opened and
re-tokenized. CLI-CONTRACT.md's requirement -- "must NOT open every page
in the wiki" -- holds from the second call onward, once the index is warm.

Env:
    QUESTION (via tool_env="question") -- never shell-interpolated; read
    directly from os.environ.

Usage:
    python3 -m wiki_weaver.ask.load_index --wiki-root <path> --out .ai/candidate-pages.json
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from wiki_weaver import bm25
from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, refresh_page_index, resolve_path

DEFAULT_K = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ask.load_index")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="BM25 top-k (default: 6)")
    return parser


def _matched_query_terms(query_tokens: list[str], index: dict[str, dict], candidate_ids: list[str]) -> set[str]:
    """Distinct query terms that actually occur in at least one candidate
    page's tokens -- a cheap, honest coverage signal (not a claim of
    semantic relevance, just lexical overlap)."""
    candidate_tokens: set[str] = set()
    for pid in candidate_ids:
        candidate_tokens.update(index[pid]["tokens"])
    return {term for term in query_tokens if term in candidate_tokens}


def build_candidates(wr: WikiRoot, question: str, k: int) -> dict:
    """Index-first candidate set + a coverage estimate for ``question``.

    Surfaces the top-k BM25 hits regardless of score (even weak hits give
    `answer` something concrete to check and potentially refuse on) --
    the coverage estimate below is what actually reflects match strength.
    """
    index = refresh_page_index(wr)
    corpus_tokens = {pid: entry["tokens"] for pid, entry in index.items()}
    query_tokens = bm25.tokenize(question)

    ranker = bm25.BM25(corpus_tokens)
    hits = ranker.top_k(query_tokens, k) if corpus_tokens else []

    # cited_sources/frontmatter_sources come straight from the persisted
    # index (computed by refresh_page_index on whatever read already opened
    # the page) -- zero additional file opens here, so this stays free for
    # the hybrid query flow (DESIGN.md): it saves `answer` a parse step by
    # surfacing which source files each candidate page already cites.
    candidates = [
        {
            "page": pid,
            "title": index[pid]["title"],
            "score": score,
            "cited_sources": index[pid].get("cited_sources", []),
            "frontmatter_sources": index[pid].get("frontmatter_sources", []),
        }
        for pid, score in hits
    ]
    matched_terms = _matched_query_terms(query_tokens, index, [c["page"] for c in candidates])

    coverage = {
        "query_tokens": len(set(query_tokens)),
        "matched_tokens": len(matched_terms),
        "top_score": hits[0][1] if hits else 0.0,
        "wiki_pages": len(index),
    }

    return {"question": question, "k": k, "candidates": candidates, "coverage": coverage}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    question = os.environ.get("QUESTION", "")

    payload = build_candidates(wr, question, args.k)

    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)
    # ensure_ascii=False: "question" is the user's own free-text question and
    # each candidate's "title" is a real wiki page title -- both can contain
    # em-dashes, arrows, or curly quotes. This file is read RAW by ask.dot's
    # "answer" LLM box with its own file tools, never through json.loads --
    # ensure_ascii=True would leak a literal "\uXXXX" escape sequence into
    # what the answer node reads as plain text.
    atomic_write_text(out_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    print(
        f"{len(payload['candidates'])} candidate page(s) for question "
        f"({payload['coverage']['wiki_pages']} page(s) in index)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
