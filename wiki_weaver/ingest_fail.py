# pyright: reportMissingImports=false
"""Fail-route tool for the ingest.dot drain loop.

Called by the `fail_handler` tool node in ingest.dot when synthesize.dot
did NOT converge (outcome != success). Moves the source file from _inbox/
to _failed/ so the inbox keeps shrinking and the drain loop can continue.

Reuses _collision_safe_move from cli/lib.py -- no reimplementation.

Usage:
    python <this_file> <wiki_dir> <source_path>

    wiki_dir     -- the wiki root (contains _failed/, etc.)
    source_path  -- absolute path to the source file in _inbox/

Exits 0 on success (including the case where the source is already absent
-- idempotent so retries don't fail the whole pipeline).
Exits non-zero on hard errors (bad args, missing wiki_dir).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            f"usage: {sys.argv[0]} <wiki_dir> <source_path>",
            file=sys.stderr,
        )
        return 1

    wiki_dir = Path(sys.argv[1]).resolve()
    source_path = Path(sys.argv[2]).resolve()

    if not wiki_dir.is_dir():
        print(f"ERROR: wiki_dir not found: {wiki_dir}", file=sys.stderr)
        return 1

    from wiki_weaver.lib import _collision_safe_move, wiki_failed

    failed_dir = wiki_failed(wiki_dir)
    failed_dir.mkdir(parents=True, exist_ok=True)

    if not source_path.is_file():
        # Already moved or never existed -- idempotent, not an error.
        print(
            f"NOTE: source not found (already moved or never existed): {source_path}",
            file=sys.stderr,
        )
        return 0

    dest = _collision_safe_move(source_path, failed_dir)
    print(
        f"failed: {source_path.name} -> _failed/{dest.name}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
