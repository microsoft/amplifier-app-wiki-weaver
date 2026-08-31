"""wiki_weaver.ingest.retrieve_slice -- THE scale fix.

Deterministically assembles the candidate page slice for ``weave``: BM25-
ranked candidates over the source's title + first ~500 words, cut at a
BYTE budget (not a fixed count -- see ``lib.build_slice`` for the measured
defect this replaces: a fixed k means touches-per-source DECLINE as the
wiki grows, and starves small-page corpora while flooding large-page ones),
UNION one-hop link-graph neighbors of those hits, UNION always-include nav
pages. The LLM never chooses what it gets to read -- see docs/DESIGN.md §3
and the S7 BM25 result for why this must be BM25, not naive term counting.

``--k`` (DEPRECATED explicit override): pins the ORIGINAL fixed-top-k
behavior exactly -- no byte budget, no floor/ceiling. Kept only for
callers/tests that need to pin an exact count. Omit it (the default) to
get the byte-budgeted selection.

``--emit-strength`` (arm B / ingest-b.dot ONLY -- additive, default OFF):
also prints a routing signal so ingest-b.dot can have CODE decide whether
this source warrants a brand-new page, instead of relying on the LLM to
notice the slice is a poor match. See ``_classify_strength`` for the
threshold and its justification. When the flag is absent, stdout is
completely unchanged (arm A's contract): informational text on stderr
only, nothing on stdout.

Usage:
    python3 -m wiki_weaver.ingest.retrieve_slice --wiki-root <path> \
        --strategy index+link-graph+lexical [--budget-bytes 150000] \
        [--k 6] [--emit-strength]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import (
    DEFAULT_SLICE_BUDGET_BYTES,
    NAV_PAGES,
    WikiRoot,
    atomic_write_text,
    build_slice,
    ensure_dir,
    load_wiki_pages,
    read_current_source_id,
    to_wiki_relpath,
)

# DEPRECATED: the historical fixed-count default. Kept as a documented
# reference point only -- no longer used as --k's argparse default (--k now
# defaults to None, meaning "use the byte budget"). See
# lib.DEFAULT_SLICE_BUDGET_BYTES for THE current default path.
DEFAULT_K = 6

# THE threshold, and why it needs no calibration knob:
#
# bm25.BM25.score() (see wiki_weaver/bm25.py:58-70) has an early `continue`
# for every query term with zero term-frequency in a document -- a
# document's score is the sum of *only* the terms it shares with the
# query. That makes a score of exactly 0.0 a STRUCTURAL fact, not a
# magnitude judgment call: it is mathematically impossible for a BM25 top
# hit to score 0.0 while sharing even one token with the source. So
# "top_score <= 0.0" means "the single best-ranked page in the whole wiki
# has zero vocabulary overlap with this source" -- there is nothing here
# for an LLM to sensibly fold the source into, existing-page-wise.
#
# This threshold is deliberately NOT a small positive epsilon (e.g. 0.05).
# Any positive BM25 score already means genuine shared vocabulary (see the
# same early-continue), and picking a magnitude cutoff above zero would
# require calibrating against corpus size / avgdl in a way the measured
# S7 BM25 result (docs/DESIGN.md §3, §10) never characterized. Zero is the
# only cutoff the algorithm itself makes defensible without new
# measurement.
WEAK_SCORE_THRESHOLD = 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.retrieve_slice")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--strategy",
        default="index+link-graph+lexical",
        help="documentation only -- the strategy is fixed (BM25 + link-graph + nav pages)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=None,
        help=(
            "DEPRECATED explicit override: pin the ORIGINAL fixed-count behavior "
            "(no byte budget, no floor/ceiling). Omit to use the byte budget (the default)."
        ),
    )
    parser.add_argument(
        "--budget-bytes",
        type=int,
        default=DEFAULT_SLICE_BUDGET_BYTES,
        help=f"byte budget for the default (non--k) path (default: {DEFAULT_SLICE_BUDGET_BYTES})",
    )
    parser.add_argument(
        "--emit-strength",
        action="store_true",
        default=False,
        help="arm B only: also print {slice_strength, top_score, slice_k} as one JSON line on stdout",
    )
    return parser


def _classify_strength(wr: WikiRoot, slice_data: dict) -> dict:
    """Arm B's routing signal: is the deterministic slice a strong enough
    match that ``weave`` should fold the source into existing pages, or is
    it weak enough that a NEW page should be created instead?

    ``weak`` when EITHER:
      - the top BM25 hit scores at or below ``WEAK_SCORE_THRESHOLD`` (see
        the constant's docstring -- this also covers the empty-slice case,
        where ``bm25_hits`` is ``[]`` and ``top_score`` defaults to 0.0), OR
      - the wiki has no non-nav content pages at all yet (the exact bug
        this experiment targets: a near-empty wiki with only index.md /
        overview.md, where BM25 has nothing real to score against).
    """
    hits = slice_data["bm25_hits"]
    top_score = hits[0]["score"] if hits else 0.0

    pages = load_wiki_pages(wr.wiki_dir)
    has_content_pages = any(pid not in NAV_PAGES for pid in pages)

    strength = "weak" if (top_score <= WEAK_SCORE_THRESHOLD or not has_content_pages) else "strong"
    return {"slice_strength": strength, "top_score": top_score, "slice_k": slice_data["k"]}


def _render_for_disk(slice_data: dict) -> dict:
    """The on-disk form weave actually reads: every page id rendered as the
    unambiguous ``wiki/<filename>`` path (see ``lib.to_wiki_relpath`` -- the
    write-path bug this fixes). ``build_slice``'s in-memory return value
    keeps bare filenames internally (matched against ``NAV_PAGES``/BM25
    corpus keys elsewhere, and read by ``_classify_strength`` above); only
    this disk-facing copy needs the wiki/-relative form, and only
    ``pages``/``bm25_hits`` name an actual page path."""
    return {
        **slice_data,
        "pages": [to_wiki_relpath(pid) for pid in slice_data["pages"]],
        "bm25_hits": [{**hit, "page": to_wiki_relpath(hit["page"])} for hit in slice_data["bm25_hits"]],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    # DESIGN.md §5 segmentation: once segment_source --select has bounded a
    # (possibly huge) source down to one segment, query on THAT text, not
    # the raw source file. Absent (arm B/C evals never write this file, and
    # neither does a fresh checkout that hasn't run detect_kind/segment_source
    # yet) -- falls back to the original, pre-segmentation behavior exactly.
    content_path = wr.current_segment_content_file if wr.current_segment_content_file.is_file() else None
    slice_data = build_slice(wr, source_id, args.k, budget_bytes=args.budget_bytes, content_path=content_path)
    ensure_dir(wr.ai_dir)
    atomic_write_text(
        wr.current_slice_file, json.dumps(_render_for_disk(slice_data), indent=2, ensure_ascii=False) + "\n"
    )

    mode = f"k={args.k} (explicit override)" if args.k is not None else f"budget={args.budget_bytes} bytes"
    print(
        f"slice for {source_id}: {len(slice_data['pages'])} page(s) "
        f"({len(slice_data['bm25_hits'])} bm25 hit(s), {mode})",
        file=sys.stderr,
    )

    if args.emit_strength:
        signal = _classify_strength(wr, slice_data)
        print(f"slice_strength={signal['slice_strength']} (top_score={signal['top_score']:.4f})", file=sys.stderr)
        print(json.dumps(signal))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
