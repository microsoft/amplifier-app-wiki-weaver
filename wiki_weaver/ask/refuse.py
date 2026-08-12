"""wiki_weaver.ask.refuse -- loud, first-class refusal.

ask.dot's ``answer`` node writes EITHER ``.ai/answer.md`` OR
``.ai/refusal.md``, never both -- ``coverage_check`` routes on which file
actually exists. Refusal is a deliberate, first-class outcome (docs/DESIGN.md:
"refuse rather than guess"), not an error swallowed silently: this tool
prints the refusal loudly to stderr (what was searched, why it falls
short) as well as echoing the refusal content itself.

Usage:
    python3 -m wiki_weaver.ask.refuse --loud --refusal-file .ai/refusal.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ask.refuse")
    parser.add_argument("--loud", action="store_true", help="refusal is always loud; flag accepted for clarity")
    parser.add_argument("--refusal-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.refusal_file)

    if not path.is_file():
        print(f"no refusal file found at {path} -- nothing to report", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    print("REFUSED: coverage was too thin to answer with citations", file=sys.stderr)
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
