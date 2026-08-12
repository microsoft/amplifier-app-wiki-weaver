"""wiki_weaver.synthesize.iteration_bound -- per-invocation gap-loop cap.

The task's own requirement: "Make it bounded -- a max-iterations cap
alongside the predicate, so a pathological corpus cannot loop forever."
This is that cap -- an in-context counter (parse_json round-trip), never a
disk file: context is fresh on every ``attractor run`` invocation, so there
is no stale-counter failure mode to reset. Mirrors
``wiki_weaver.ingest.drain_bound`` exactly (REWRITE-GUIDE.md \u00a73.2's
counter-file/in-context-counter pattern), applied to gap iterations instead
of ingested sources -- kept as a separate module rather than reused
directly so this pipeline never shares mutable state with ``ingest.dot``
(DESIGN.md \u00a712: do not modify or entangle the existing pipelines).

This is DELIBERATELY SEPARATE from ``select_gap``'s ledger-based exclusion:
the ledger stops the loop from proposing the SAME term twice; this stops
the loop from running past a fixed number of iterations at all, regardless
of how many distinct (still-undecided) candidates ``find_gaps`` keeps
producing.

Usage:
    python3 -m wiki_weaver.synthesize.iteration_bound --count <int> --max-iterations <int>
"""

from __future__ import annotations

import argparse
import json

from wiki_weaver.lib import int_or_default

# THE single source of truth for these defaults. pipeline/synthesize.dot
# passes both flags via the bare $var substitution form (never
# ${var:-default} -- see lib.int_or_default's docstring for why); when the
# context key is absent the CLI receives an empty string, which
# int_or_default resolves to the default below.
DEFAULT_COUNT = 0
DEFAULT_MAX_ITERATIONS = 50


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.iteration_bound")
    parser.add_argument(
        "--count",
        required=True,
        type=int_or_default(DEFAULT_COUNT),
        help="current in-context iteration_count (default 0)",
    )
    parser.add_argument(
        "--max-iterations",
        required=True,
        type=int_or_default(DEFAULT_MAX_ITERATIONS),
        help="per-run cap on gap iterations processed (default 50)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    new_count = args.count + 1
    status = "continue" if new_count <= args.max_iterations else "budget_exhausted"
    print(json.dumps({"iteration_count": new_count, "iteration_status": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
