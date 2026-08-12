"""wiki_weaver.ingest.reweave_bound -- bounded retry for THIS segment.

Reached only on a deterministic ``validate`` failure. Counts attempts per
(source id, segment index) and is what makes the recovery loop terminate:
one bad segment can never wedge the whole drain. Identity-gated
(docs/DESIGN.md §12.3 rule 3): a stale attempts file left over from a
PREVIOUS (source, segment) pair is detected and reset to a fresh count,
never silently reused across sources or segments (and never a hard
pipeline failure either -- a new segment's first validate failure is
expected, ordinary state, not corruption).

SEGMENT-SCOPED, NOT JUST SOURCE-SCOPED (docs/KNOWN_ISSUES.md #3): this
counter predates segmentation (DESIGN.md §5) and was keyed by source id
alone. Since every segment of a multi-segment source shares one source id,
segment 1 exhausting its retries left segments 2+ inheriting an
already-spent counter -- confirmed against a real 73-source production
run, where segment 2+ of a multi-segment source could exhaust in as little
as ONE failed validate, having started already over ``--max``. Keying by
(source_id, segment_index) instead restores segment_source.py's own stated
design intent -- "each segment is a unit of work" -- at the accounting
level: every segment gets its own full retry budget, exactly like a
freshly-selected source does. See ``lib.read_current_segment_index``'s
docstring for the full mechanism and evidence.

Usage:
    python3 -m wiki_weaver.ingest.reweave_bound --wiki-root <path> --max <n>
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
    int_or_default,
    read_current_segment_index,
    read_current_source_id,
)

# THE single source of truth for this default. pipeline/ingest.dot passes
# --max via the bare $max_reweave substitution form (never
# ${max_reweave:-2} -- see lib.int_or_default's docstring for why); when the
# context key is absent the CLI receives an empty string, which
# int_or_default resolves to DEFAULT_MAX below.
DEFAULT_MAX = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.reweave_bound")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--max",
        required=True,
        type=int_or_default(DEFAULT_MAX),
        help="max reweave attempts for this source (default: 2)",
    )
    return parser


def _load_attempts(path: Path, source_id: str, segment_index: int) -> int:
    if not path.is_file():
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(state, dict) or state.get("source_id") != source_id:
        return 0  # stale state from a previous source -- reset, don't hard-fail
    try:
        stale_segment = int(state.get("segment_index", 1))
    except (TypeError, ValueError):
        stale_segment = 1
    if stale_segment != segment_index:
        return 0  # stale state from a previous SEGMENT of this source -- same reset discipline
    try:
        return int(state.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    segment_index = read_current_segment_index(wr)
    attempts_path = wr.reweave_attempts_file
    attempts = _load_attempts(attempts_path, source_id, segment_index) + 1
    ensure_dir(wr.ai_dir)
    atomic_write_text(
        attempts_path,
        json.dumps({"source_id": source_id, "segment_index": segment_index, "attempts": attempts}),
    )

    if attempts <= args.max:
        print(f"reweave attempt {attempts}/{args.max} for {source_id}", file=sys.stderr)
        print("retry")
    else:
        print(f"giving up on {source_id} after {attempts - 1} reweave attempt(s)", file=sys.stderr)
        print("give_up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
