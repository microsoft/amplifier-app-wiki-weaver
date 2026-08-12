"""wiki_weaver.ingest.budget -- per-source cost ledger + fail-loud ceiling.

The cost law's guardrail (docs/DESIGN.md §3): every ingest appends
cost.jsonl, and the run fails loud when a source's wall-clock time exceeds
its ceiling. Exit code IS the routing signal here (per CLI-CONTRACT.md):
exit 0 within budget, exit 1 over it -- and cost.jsonl is appended durably
BEFORE the process exits, either way.

Usage:
    python3 -m wiki_weaver.ingest.budget --wiki-root <path> --append cost.jsonl
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_append_jsonl,
    count_pages_touched,
    count_wiki_pages,
    read_current_kind,
    read_current_source_id,
    read_source_kind,
    resolve_path,
)


def _kind_for_cost(wr: WikiRoot, source_path: Path) -> str:
    """Prefer what detect_kind actually determined (disk state) over the
    best-effort, frontmatter-only ``read_source_kind`` guess -- see
    commit.py's identical helper; the same accuracy gap applies to
    cost.jsonl's ``kind`` label."""
    try:
        return read_current_kind(wr)
    except FileNotFoundError:
        return read_source_kind(source_path)


DEFAULT_CEILING_S = 900.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.budget")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--append", default="cost.jsonl")
    return parser


def _ceiling_seconds() -> float:
    # Never rely on shell ${:-default} -- the env var may simply be unset.
    raw = os.environ.get("PER_SOURCE_TIME_CEILING")
    if not raw:
        return DEFAULT_CEILING_S
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_CEILING_S


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    cost_path = resolve_path(wr, args.append)

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        start = int(wr.source_start_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        print(f"missing or invalid {wr.source_start_file} -- select_source must run first", file=sys.stderr)
        return 1

    duration_s = time.time() - start
    ceiling = _ceiling_seconds()

    source_path = wr.sources_dir / source_id
    record = {
        "source_id": source_id,
        "kind": _kind_for_cost(wr, source_path),
        "bytes": source_path.stat().st_size if source_path.is_file() else 0,
        "duration_s": round(duration_s, 3),
        "wiki_pages": count_wiki_pages(wr.wiki_dir),
        "pages_touched": count_pages_touched(wr.root, wr.wiki_dir),
        # tokens_in/tokens_out: no attractor observability hook wires these
        # into this CLI's argv or environment today (DESIGN.md §3 names this
        # as separate, not-yet-built work). Recorded as 0 rather than
        # fabricated -- flagged in the delivery report.
        "tokens_in": 0,
        "tokens_out": 0,
    }
    atomic_append_jsonl(cost_path, record)  # durable BEFORE exit, either branch

    if duration_s <= ceiling:
        print("within_budget")
        return 0

    print(f"over budget: {duration_s:.1f}s > {ceiling:.1f}s ceiling for {source_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
