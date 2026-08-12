"""wiki_weaver.ask.present -- format and present the answer.

Reads ``--answer-file`` directly with the filesystem -- the answer text
never transits a ``$substitution`` shell token (it may be arbitrarily long
freeform prose with citations, quotes, anything). stdout is informational
only, not routed on (ask.dot's edge from ``present`` is either
unconditional to ``exit`` or gated on ``context.enable_file_back``, never
on this tool's stdout).

Usage:
    python3 -m wiki_weaver.ask.present --answer-file .ai/answer.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ask.present")
    parser.add_argument("--answer-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.answer_file)

    if not path.is_file():
        print(f"answer file not found: {path}", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
