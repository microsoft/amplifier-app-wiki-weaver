"""wiki_weaver.init.check_bound -- per-run interview round cap.

Same in-context-counter shape as ``wiki_weaver.ingest.drain_bound``
(CLI-CONTRACT.md: "same in-context-counter shape as ingest.drain_bound"):
an in-context counter, never a disk file for the count itself -- context is
fresh on every ``attractor run`` invocation.

Unlike ``drain_bound``, on ``hit`` this ALSO touches ``.ai/bound-hit.flag``
(an empty file). There is no ``--wiki-root`` argument on this subcommand
(matching pipeline/init.dot's actual invocation), so the flag path is
relative to the process's current working directory -- which attractor
sets to ``--cwd`` (the wiki root) for this pipeline. ``write_schema``'s
prompt checks for the flag's existence and deletes it after noting the
draft caveat.

FLAGGED CONTRACT DISCREPANCY (see delivery report): CLI-CONTRACT.md's
2026-07-25 "Corrections" section proposes collapsing this subcommand (and
``ingest.drain_bound``) into a single ``wiki_weaver.bound --scope
init|ingest``. But pipeline/init.dot's actual ``tool_command`` still
invokes ``wiki_weaver.init.check_bound`` verbatim -- the collapse was never
applied to the .dot files. Followed init.dot (the graph that must actually
run) and kept the original per-pipeline name, matching
``wiki_weaver.ingest.drain_bound``'s own precedent (also never collapsed).

Usage:
    python3 -m wiki_weaver.init.check_bound --count <int> --max-rounds <int>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wiki_weaver.lib import atomic_write_text, ensure_dir, int_or_default

BOUND_HIT_FLAG = Path(".ai/bound-hit.flag")

# THE single source of truth for these defaults. pipeline/init.dot passes
# both flags via the bare $var substitution form (never
# ${interview_rounds:-0} / ${max_interview_rounds:-5} -- see
# lib.int_or_default's docstring for why); when the context key is absent
# the CLI receives an empty string, which int_or_default resolves to the
# defaults below.
DEFAULT_COUNT = 0
DEFAULT_MAX_ROUNDS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.init.check_bound")
    parser.add_argument(
        "--count",
        required=True,
        type=int_or_default(DEFAULT_COUNT),
        help="current in-context interview_rounds (default 0)",
    )
    parser.add_argument(
        "--max-rounds",
        required=True,
        type=int_or_default(DEFAULT_MAX_ROUNDS),
        help="per-run cap on interview rounds (default 5)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    new_count = args.count + 1
    status = "ok" if new_count <= args.max_rounds else "hit"

    if status == "hit":
        ensure_dir(BOUND_HIT_FLAG.parent)
        atomic_write_text(BOUND_HIT_FLAG, "")

    print(json.dumps({"interview_rounds": new_count, "bound_status": status}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
