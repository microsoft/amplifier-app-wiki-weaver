"""wiki_weaver.synthesize.select_chunk -- THE select-half of the chunked
detection loop (see pipeline/synthesize.dot's header, "CHUNKED DETECTION"
section, and wiki_weaver.synthesize.chunk_arguments' module docstring for
the measured finding this loop is built to exploit).

WHY THIS NODE EXISTS: a live experiment found scan_arguments correctly
detects arguments when scoped to a SMALL set of source theses (a chunk of
8) but triages to only 2-3 dominant signals when run over the FULL
extract (46 theses) in one pass -- the exact same failure shape iteration
6's attribute_select/attribute_sources/attribute_record split already
fixed for ATTRIBUTION ("a single pass juggling several things at once
degrades" -- see attribute_select's own module docstring). This module is
the CODE-tier "select the next item" half of an analogous select/box/
record loop for DETECTION: wiki_weaver.synthesize.chunk_arguments splits
the corpus's source theses into fixed-size groups; this node picks the
next un-scanned chunk index and writes its content to
wr.current_chunk_file for scan_arguments (the LLM box) to read in
isolation -- one bounded model call per chunk, narrowing the model's
attention to a handful of source theses at a time instead of all of them
at once.

RESUMABLE WITHIN ONE INVOCATION, RESET ACROSS INVOCATIONS: same
discipline as attribute_select's own progress file (see that module's
docstring for the full accounting) -- progress lives in
wr.chunk_detection_progress_file, stamped with a content signature
(sha256) of wr.argument_chunks_file at the time it was written. Same
signature -> resume (already-scanned chunks and their accumulated
candidates survive a crash/restart). Different signature (or no progress
file yet) -> a NEW chunk_arguments run produced this file -- start fresh
rather than silently mixing candidates from two different chunkings.

WHEN EVERY CHUNK HAS BEEN SCANNED: this node flattens the accumulated
cross-chunk candidate list into wr.gap_candidates_raw_file -- the SAME
file wiki_weaver.synthesize.attribute_select already reads, unchanged.
The attribution loop downstream (attribute_select / attribute_sources /
attribute_record / rank_candidates) requires no changes at all: as far as
it is concerned, one "scan_arguments" pass proposed this final candidate
list -- it has no visibility into (and no need to know about) the
chunking that produced it.

Usage:
    python3 -m wiki_weaver.synthesize.select_chunk --wiki-root <path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.select_chunk")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _signature(raw_bytes: bytes) -> str:
    """Deterministic content fingerprint of argument_chunks_file -- what
    makes progress resumable within one chunk_arguments run and discarded
    across a fresh one (see module docstring)."""
    return hashlib.sha256(raw_bytes).hexdigest()


def _load_progress(path: Path, signature: str) -> dict:
    """Read chunk_detection_progress_file; return a fresh accumulator
    whenever the file is absent, unparsable, or stamped with a DIFFERENT
    signature (a new chunk_arguments run happened -- see module
    docstring)."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if (
            isinstance(data, dict)
            and data.get("chunks_signature") == signature
            and isinstance(data.get("scanned_indices"), list)
            and isinstance(data.get("accumulated"), list)
        ):
            return data
    return {"chunks_signature": signature, "scanned_indices": [], "accumulated": []}


def finalize_union(accumulated: list[dict]) -> list[dict]:
    """Flatten the accumulated cross-chunk candidate list into the shape
    gap_candidates_raw_file must have for attribute_select (unchanged
    downstream -- see module docstring): ``{"term", "claim", "source_ids",
    "chunk_count"}``. ``chunk_count`` (how many distinct chunks proposed
    this term) is an informational extra field -- attribute_select and
    rank_candidates read only "term"/"claim"/"source_ids" and ignore
    unknown keys; it is preserved here purely so a real theme's cross-
    chunk dispersion is visible to anyone inspecting this file, per the
    task's own point that a term appearing in several chunks is evidence
    of dispersion, not noise."""
    out: list[dict] = []
    for entry in accumulated:
        out.append(
            {
                "term": entry["term"],
                "claim": entry.get("claim", ""),
                "source_ids": list(entry["source_ids"]),
                "chunk_count": len(entry.get("chunk_indices", [])),
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    chunks_path = wr.argument_chunks_file
    if not chunks_path.is_file():
        print(f"{chunks_path} does not exist -- chunk_arguments must run first", file=sys.stderr)
        print("chunks_bad")
        return 0

    raw_bytes = chunks_path.read_bytes()
    try:
        chunk_docs = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{chunks_path} is not valid JSON: {exc}", file=sys.stderr)
        print("chunks_bad")
        return 0

    if not isinstance(chunk_docs, list) or not all(isinstance(c, str) for c in chunk_docs):
        print(f"{chunks_path} must be a JSON array of chunk text strings", file=sys.stderr)
        print("chunks_bad")
        return 0

    ensure_dir(wr.ai_dir)
    signature = _signature(raw_bytes)
    progress = _load_progress(wr.chunk_detection_progress_file, signature)

    remaining = [i for i in range(len(chunk_docs)) if i not in progress["scanned_indices"]]

    # Persist (possibly freshly-reset) progress immediately, even before a
    # chunk is chosen -- makes the reset itself durable, not just
    # in-memory, so a crash right after this node still resumes correctly
    # (same discipline as attribute_select).
    atomic_write_text(wr.chunk_detection_progress_file, json.dumps(progress, indent=2) + "\n")

    if not remaining:
        final = finalize_union(progress["accumulated"])
        atomic_write_text(wr.gap_candidates_raw_file, json.dumps(final, indent=2) + "\n")
        print(
            f"{len(chunk_docs)} chunk(s) scanned this invocation -- {len(final)} unique candidate(s) "
            f"unioned into {wr.gap_candidates_raw_file}",
            file=sys.stderr,
        )
        print("all_chunks_scanned")
        return 0

    chosen_index = remaining[0]
    progress["current_index"] = chosen_index
    atomic_write_text(wr.chunk_detection_progress_file, json.dumps(progress, indent=2) + "\n")
    atomic_write_text(wr.current_chunk_file, chunk_docs[chosen_index])

    print(
        f"selected chunk {chosen_index} of {len(chunk_docs)} for detection "
        f"({len(remaining) - 1} remaining after this one)",
        file=sys.stderr,
    )
    print("has_chunk")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
