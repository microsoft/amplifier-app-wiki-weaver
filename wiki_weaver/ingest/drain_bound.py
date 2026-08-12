"""wiki_weaver.ingest.drain_bound -- per-invocation source cap.

An in-context counter (parse_json round-trip), never a disk file: context
is fresh on every ``attractor run`` invocation, so there is no stale-
counter failure mode to reset. See pipeline/ingest.dot's header ("THE
DRAIN BOUND") for why this exists at all (the engine's generic
``max_steps`` safety net would otherwise FAIL a long drain opaquely).

Usage:
    python3 -m wiki_weaver.ingest.drain_bound --count <int> --max-sources <int>
"""

from __future__ import annotations

import argparse
import json

from wiki_weaver.lib import int_or_default

# THE single source of truth for these defaults. pipeline/ingest.dot passes
# both flags via the bare ``$var`` substitution form (never
# ``${var:-default}`` -- see lib.int_or_default's docstring for why); when
# the context key is absent the CLI receives an empty string, which
# int_or_default resolves to the default below.
DEFAULT_COUNT = 0
DEFAULT_MAX_SOURCES = 200


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.drain_bound")
    parser.add_argument(
        "--count", required=True, type=int_or_default(DEFAULT_COUNT), help="current in-context drain_count (default 0)"
    )
    parser.add_argument(
        "--max-sources",
        required=True,
        type=int_or_default(DEFAULT_MAX_SOURCES),
        help="per-run cap on sources processed (default 200)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    new_count = args.count + 1
    status = "continue" if new_count <= args.max_sources else "budget_exhausted"
    print(json.dumps({"drain_count": new_count, "drain_status": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
