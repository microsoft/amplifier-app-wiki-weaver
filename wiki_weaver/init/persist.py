"""wiki_weaver.init.persist -- durable close of the init pipeline.

Strategy SF (matching ingest.commit's precedent): ``write_schema`` already
made the actual file edits (AGENTS.md, lens/persona.md, lens/canon/) with
its own file tools. persist's only job is the deterministic, idempotent
part: ``git init`` if this wiki_root has no repo yet (idempotent),
``git add -A && git commit`` guarded by ``git diff --staged --quiet``
(nothing-to-commit -> no phantom commit), and cleanup of the ephemeral
``.ai/`` breadcrumbs now that AGENTS.md and lens/ are the durable record.

Usage:
    python3 -m wiki_weaver.init.persist --wiki-root <path>
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wiki_weaver.lib import WikiRoot, commit_if_staged, git_init_if_absent

# CLI-CONTRACT.md's init.persist entry, verbatim list.
EPHEMERAL_FILES = (
    "interview-notes.md",
    "draft-schema.md",
    "draft-persona.md",
    "init-verdict.txt",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.init.persist")
    parser.add_argument("--wiki-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    git_init_if_absent(wr.root)
    commit_if_staged(wr.root, "wiki-weaver: initialize wiki schema and lens seed")

    for name in EPHEMERAL_FILES:
        path = wr.ai_dir / name
        if path.is_file():
            path.unlink()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
