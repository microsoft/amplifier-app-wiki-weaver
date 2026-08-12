"""wiki_weaver.synthesize.retry_bound -- bounded retry for THIS gap term.

Reached only on a deterministic ``validate`` failure after ``write_gap_page``
(a page ``answer_gap``'s prose produced structural issues -- e.g. a
wikilink to a page that does not exist). Mirrors
``wiki_weaver.ingest.reweave_bound`` exactly (same identity-gated counter
pattern, REWRITE-GUIDE.md \u00a72.3 rule 3: "never read run state keyed only by
an ambient identity; mismatched identity must reset, never silently
reused") but keyed on the current gap TERM (from ``current_gap_file``)
instead of a source id, and stored in its own attempts file so this
pipeline never shares mutable state with ``ingest.dot``.

Usage:
    python3 -m wiki_weaver.synthesize.retry_bound --wiki-root <path> --max <n>
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, int_or_default

# THE single source of truth for this default. pipeline/synthesize.dot
# passes --max via the bare $max_synth_retry substitution form (never
# ${max_synth_retry:-2} -- see lib.int_or_default's docstring for why).
DEFAULT_MAX = 2


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="python3 -m wiki_weaver.synthesize.retry_bound")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--max", required=True, type=int_or_default(DEFAULT_MAX), help="max retry attempts for this gap (default: 2)"
    )
    return parser


def _read_current_term(wr: WikiRoot) -> str:
    path = wr.current_gap_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- select_gap must run first")
    data = json.loads(path.read_text(encoding="utf-8"))
    term = data.get("term")
    if not term:
        raise FileNotFoundError(f"{path} has no 'term' field -- select_gap must run first")
    return str(term)


def _load_attempts(path: Path, term: str) -> int:
    if not path.is_file():
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0
    if not isinstance(state, dict) or state.get("term") != term:
        return 0  # stale state from a previous gap -- reset, don't hard-fail
    try:
        return int(state.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        term = _read_current_term(wr)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    attempts_path = wr.synth_reweave_attempts_file
    attempts = _load_attempts(attempts_path, term) + 1
    ensure_dir(wr.ai_dir)
    atomic_write_text(attempts_path, json.dumps({"term": term, "attempts": attempts}))

    if attempts <= args.max:
        print(f"retry attempt {attempts}/{args.max} for {term!r}", file=sys.stderr)
        print("retry")
    else:
        print(f"giving up on {term!r} after {attempts - 1} retry attempt(s)", file=sys.stderr)
        print("give_up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
