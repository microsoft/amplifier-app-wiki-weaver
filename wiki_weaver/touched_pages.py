"""Derive the ingest touched-pages manifest from deterministic page content.

The ingest agent appends its own work-list opportunistically, but an agent
session can end before it reaches that instruction.  This module snapshots the
same root ``*.md`` pages that ``validate_wiki.py`` validates, then derives the
manifest from their SHA-256 delta after the agent exits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _page_hashes(wiki_dir: Path) -> dict[str, str]:
    """Return SHA-256 digests for validator-visible root markdown pages only."""
    return {
        page.name: hashlib.sha256(page.read_bytes()).hexdigest()
        for page in sorted(wiki_dir.glob("*.md"))
    }


def snapshot_pages(wiki_dir: Path, snapshot_path: Path) -> None:
    """Snapshot validator-visible wiki pages before one ingest agent runs."""
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(_page_hashes(wiki_dir), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_snapshot(snapshot_path: Path) -> dict[str, str]:
    """Load a snapshot written by :func:`snapshot_pages`."""
    data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(path, str) and isinstance(digest, str)
        for path, digest in data.items()
    ):
        raise ValueError(f"invalid page snapshot: {snapshot_path}")
    return data


def _agent_manifest_lines(manifest_path: Path) -> set[str]:
    """Read non-empty cooperative manifest lines when the agent wrote any."""
    if not manifest_path.is_file():
        return set()
    return {
        line.strip()
        for line in manifest_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    }


def derive_manifest(
    wiki_dir: Path, snapshot_path: Path, manifest_path: Path
) -> list[str]:
    """Write the sorted union of agent-reported and delta-detected page paths.

    New and content-modified validator-visible root pages are derived from the
    snapshot.  Deleted pages are intentionally not listed: assess can only
    review pages that still exist.  An empty union removes the manifest so the
    following goal gate continues to fail.
    """
    before = _read_snapshot(snapshot_path)
    after = _page_hashes(wiki_dir)
    changed = {path for path, digest in after.items() if before.get(path) != digest}
    pages = sorted(_agent_manifest_lines(manifest_path) | changed)

    if not pages:
        manifest_path.unlink(missing_ok=True)
        return []

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(pages) + "\n", encoding="utf-8")
    return pages


def main() -> int:
    """Provide the snapshot/derive commands used by ``synthesize.dot``."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("wiki_dir", type=Path)
    snapshot.add_argument("snapshot_path", type=Path)

    derive = subparsers.add_parser("derive")
    derive.add_argument("wiki_dir", type=Path)
    derive.add_argument("snapshot_path", type=Path)
    derive.add_argument("manifest_path", type=Path)

    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot_pages(args.wiki_dir, args.snapshot_path)
    else:
        derive_manifest(args.wiki_dir, args.snapshot_path, args.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())