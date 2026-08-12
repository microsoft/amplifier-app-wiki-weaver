"""wiki_weaver.ask.prepare_file_back -- file the answer back into the wiki
as a new, immutable synthetic source.

Writes ``sources/synthetic-answer-<epoch>.txt`` with a ``kind: article``
header (parsed by ``wiki_weaver.lib.read_source_kind``'s leading-line
convention -- the same one ``ingest``'s subcommands already honor),
containing the answer content verbatim -- registered as ordinary raw
material, not a special case. Scopes ``restrict_to_sources`` to just this
new file via ``parse_json``; ask.dot's ``file_back`` child ``ingest.dot``
run sees this key through the engine's full parent-context clone.

Usage:
    python3 -m wiki_weaver.ask.prepare_file_back --wiki-root <path> --answer-file .ai/answer.md
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ask.prepare_file_back")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--answer-file", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    answer_path = Path(args.answer_file)
    answer_text = answer_path.read_text(encoding="utf-8") if answer_path.is_file() else ""

    filename = f"synthetic-answer-{int(time.time())}.txt"
    ensure_dir(wr.sources_dir)
    atomic_write_text(wr.sources_dir / filename, f"kind: article\n\n{answer_text}")

    print(json.dumps({"restrict_to_sources": filename}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
