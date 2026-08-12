"""wiki_weaver.init.read_verdict -- fail-closed verdict gate for the
interview loop.

Tier-2 verdict pattern (CLI-CONTRACT.md / init.dot's ``evaluate`` node): the
LLM writes a plain-word verdict to a file; this tool greps it and emits the
deterministic routing sentinel. Prose like "set context.enough to true"
sets nothing -- this is what actually works.

Fail-closed: missing, empty, or any content other than exactly ``enough``
-> ``not_enough``. Always ``rm -f``s the verdict file after reading so a
stale verdict can never leak into the next interview round.

Usage:
    python3 -m wiki_weaver.init.read_verdict --verdict-file .ai/init-verdict.txt
"""

from __future__ import annotations

import argparse
from pathlib import Path

ENOUGH = "enough"
NOT_ENOUGH = "not_enough"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.init.read_verdict")
    parser.add_argument("--verdict-file", required=True)
    return parser


def read_verdict(path: Path) -> str:
    """Fail-closed: only the literal ``enough`` content counts as enough."""
    if not path.is_file():
        return NOT_ENOUGH
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        return NOT_ENOUGH
    return ENOUGH if content == ENOUGH else NOT_ENOUGH


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.verdict_file)

    verdict = read_verdict(path)

    # A stale verdict must never leak into the next round, regardless of
    # what it said -- rm -f unconditionally.
    if path.is_file():
        path.unlink()

    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
