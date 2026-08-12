"""wiki_weaver.synthesize.record_chunk_candidates -- THE record-half of the
chunked detection loop (see pipeline/synthesize.dot's header, "CHUNKED
DETECTION" section, and wiki_weaver.synthesize.select_chunk's module
docstring for the loop this completes).

scan_arguments (LLM box) now runs once per chunk (wiki_weaver.synthesize.
chunk_arguments' fixed-size groups of source theses, not the whole
~44 KB extract in one pass -- see chunk_arguments' module docstring for
the measured reason) and writes its per-CHUNK candidate list to
wr.current_chunk_candidates_raw_file. This module is the CODE half of the
box/tool split for that per-chunk output ("the model cannot certify its
own output's shape" -- the SAME reasoning rank_candidates and
attribute_record already apply elsewhere in this pipeline): validates the
shape (rank_candidates.find_candidate_issues, unchanged and reused
directly -- the schema is identical to the pre-chunking scan_arguments'
own single-pass output), and on a well-formed result, UNIONS it into the
accumulated cross-chunk candidate list in wr.chunk_detection_progress_file.

THE UNION, NOT A DISCARD, ON A REPEATED TERM: merging is by lowercased
term (same tie-break convention as rank_candidates.rank_and_filter and
attribute_select.dedupe_and_filter). A term proposed by a chunk that
ALREADY has an accumulated entry is never treated as a duplicate to throw
away -- its source_ids are UNIONED into the existing entry (never
discarded), and the contributing chunk's index is recorded. The SAME
argument proposed by several different chunks (necessarily using
different wording, since each chunk sees a different subset of the
corpus) is evidence the argument is genuinely dispersed across the whole
corpus, not noise -- exactly the case a full-corpus single pass was
measured to miss (see chunk_arguments' module docstring).

FAIL LOUD ON MALFORMED PER-CHUNK OUTPUT, NOT A GRACEFUL FALLBACK (unlike
attribute_record's per-candidate fallback to the seed): before iteration
6 split detection from attribution, a malformed scan_arguments output
failed the WHOLE detection pass loud (this was rank_candidates' job then,
now attribute_select's). scan_arguments now runs multiple times per
invocation (once per chunk); each run's output gets that SAME fail-loud
treatment, one chunk at a time -- a chunk whose detection output is
unparseable is not a small, safely-droppable amount of loss: scan_
arguments' own prompt states it plainly ("missing an argument ENTIRELY is
the one error downstream steps cannot correct"), and that is exactly what
silently skipping a malformed chunk would risk. The chunk is NOT marked
scanned on failure, so a re-run of this pipeline retries the SAME chunk
rather than skipping it or re-scanning already-completed chunks (prior
progress is untouched -- this node writes the merged progress file only
on success).

Usage:
    python3 -m wiki_weaver.synthesize.record_chunk_candidates --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text
from wiki_weaver.synthesize.rank_candidates import find_candidate_issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.record_chunk_candidates")
    parser.add_argument("--wiki-root", required=True)
    return parser


def merge_candidate(accumulated: list[dict], candidate: dict, chunk_index: int) -> None:
    """In-place upsert of ONE new candidate ``{"term", "claim",
    "source_ids"}`` into ``accumulated`` (list of ``{"term", "claim",
    "source_ids", "chunk_indices"}``), merging by lowercased term:
    ``source_ids`` becomes the UNION (never discarded or overwritten),
    ``claim`` is kept from whichever chunk proposed this term FIRST
    (attribution downstream may refine it further -- same "first
    occurrence wins" discipline used elsewhere in this pipeline), and
    ``chunk_indices`` records every chunk that proposed this term (sorted,
    deduped) -- informational provenance only, never read by anything
    downstream of ``wiki_weaver.synthesize.select_chunk``'s own
    ``finalize_union``."""
    term = str(candidate["term"]).strip()
    key = term.lower()
    source_ids = {str(s) for s in candidate["source_ids"]}

    for existing in accumulated:
        if existing["term"].strip().lower() == key:
            existing["source_ids"] = sorted(set(existing["source_ids"]) | source_ids)
            if chunk_index not in existing["chunk_indices"]:
                existing["chunk_indices"] = sorted([*existing["chunk_indices"], chunk_index])
            return

    accumulated.append(
        {
            "term": term,
            "claim": str(candidate.get("claim", "")).strip(),
            "source_ids": sorted(source_ids),
            "chunk_indices": [chunk_index],
        }
    )


def merge_chunk_candidates(accumulated: list[dict], new_candidates: list[dict], chunk_index: int) -> list[dict]:
    """Return a NEW accumulated list (does not mutate ``accumulated``) with
    every candidate in ``new_candidates`` merged in via ``merge_candidate``."""
    result = [
        {
            "term": entry["term"],
            "claim": entry.get("claim", ""),
            "source_ids": list(entry["source_ids"]),
            "chunk_indices": list(entry.get("chunk_indices", [])),
        }
        for entry in accumulated
    ]
    for candidate in new_candidates:
        merge_candidate(result, candidate, chunk_index)
    return result


def _read_json(path: Path) -> dict | None:
    """``None`` on any missing/invalid/non-object read -- callers treat that
    as "nothing trustworthy was written," never crash on it."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    raw_path = wr.current_chunk_candidates_raw_file
    if not raw_path.is_file():
        print(f"{raw_path} does not exist -- scan_arguments must run first for this chunk", file=sys.stderr)
        print("chunks_bad")
        return 0

    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{raw_path} is not valid JSON: {exc}", file=sys.stderr)
        print("chunks_bad")
        return 0

    issues = find_candidate_issues(raw)
    if issues:
        for issue in issues:
            print(f"current-chunk-candidates-raw.json: {issue}", file=sys.stderr)
        print("chunks_bad")
        return 0

    progress = _read_json(wr.chunk_detection_progress_file)
    if progress is None or "current_index" not in progress:
        print(
            f"{wr.chunk_detection_progress_file} missing or has no current_index -- select_chunk must run first",
            file=sys.stderr,
        )
        print("chunks_bad")
        return 0

    chunk_index = int(progress["current_index"])
    accumulated = merge_chunk_candidates(progress.get("accumulated", []), raw, chunk_index)
    scanned_indices = sorted({*progress.get("scanned_indices", []), chunk_index})

    new_progress = {
        "chunks_signature": progress.get("chunks_signature", ""),
        "scanned_indices": scanned_indices,
        "accumulated": accumulated,
    }
    atomic_write_text(wr.chunk_detection_progress_file, json.dumps(new_progress, indent=2) + "\n")

    print(
        f"chunk {chunk_index}: {len(raw)} raw candidate(s) merged -- accumulated total now "
        f"{len(accumulated)} unique term(s)",
        file=sys.stderr,
    )
    print("chunks_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
