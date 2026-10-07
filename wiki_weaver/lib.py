"""Corpus layout: path helpers and naming rules.

These five helpers are the contract RepoWeaver imports at module load:
``wiki_inbox``, ``wiki_sources``, ``wiki_failed``, ``wiki_ledger``, ``wiki_dashboard``.
Everything else here is V4's own layout vocabulary. No model calls, no I/O beyond paths.
"""

from __future__ import annotations

import re
from pathlib import Path

WIKI_DIR = ".wiki"
INBOX = "_inbox"
SOURCES = "_sources"
LEDGER_NAME = ".processed.jsonl"

# Root-level markdown files that are not pages.
NON_PAGE_FILES = {"index.md", "log.md", "lens.md", "READING.md"}
# Slugs a page may not take (case-insensitive file systems make READING/reading collide).
RESERVED_SLUGS = {"_inbox", "_sources", "lens", "feedback", "index", "log", "reading"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
MAX_SOURCE_CHARS = 400_000


def wiki_ledger(wiki: Path) -> Path:
    """Return the ledger path: ``<wiki>/.wiki/.processed.jsonl``."""
    return Path(wiki) / WIKI_DIR / LEDGER_NAME


def wiki_failed(wiki: Path) -> Path:
    """Return the held-sources dir: ``<wiki>/.wiki/failed``."""
    return Path(wiki) / WIKI_DIR / "failed"


def wiki_sources(wiki: Path) -> Path:
    """Return the retained-sources dir: ``<wiki>/_sources`` (visible)."""
    return Path(wiki) / SOURCES


def wiki_inbox(wiki: Path) -> Path:
    """Return the inbox dir: ``<wiki>/_inbox`` (visible)."""
    return Path(wiki) / INBOX


def wiki_dashboard(wiki: Path) -> Path:
    """Return the dashboard assets dir: ``<wiki>/.wiki/dashboard``."""
    return Path(wiki) / WIKI_DIR / "dashboard"


def wiki_runs(wiki: Path) -> Path:
    return Path(wiki) / WIKI_DIR / "runs"


def wiki_work(wiki: Path) -> Path:
    """Transient per-source scratch space (gitignored, rebuilt every source)."""
    return Path(wiki) / WIKI_DIR / "work"


def lens_path(wiki: Path) -> Path:
    return Path(wiki) / "lens.md"


def corrections_dir(wiki: Path) -> Path:
    return Path(wiki) / "lens" / "corrections"


def feedback_log(wiki: Path) -> Path:
    return Path(wiki) / "feedback" / "log.jsonl"


def schema_fragments(wiki: Path) -> list[Path]:
    """Wrapper-provided policy, read as lens fragments if present (both paths)."""
    wiki = Path(wiki)
    cands = [wiki / WIKI_DIR / "policy" / "schema.md", wiki / "policy" / "schema.md"]
    return [p for p in cands if p.is_file()]


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:70].rstrip("-")) or "untitled"


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug)) and slug not in RESERVED_SLUGS


def page_files(wiki: Path) -> list[Path]:
    """Every page at the corpus root (``*.md`` minus the non-page files)."""
    return sorted(
        p for p in Path(wiki).glob("*.md") if p.is_file() and p.name not in NON_PAGE_FILES
    )


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file in the same directory and os.replace, so a reader or a crash
    never sees a half-written file."""
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def atomic_append_line(path: Path, line: str) -> None:
    """Append one line by rewriting the file atomically (ledger and batch JSONL are small)."""
    path = Path(path)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if old and not old.endswith("\n"):
        old += "\n"
    atomic_write_text(path, old + line.rstrip("\n") + "\n")


def contained_file(root: Path, rel: str | Path) -> Path | None:
    """The file ``rel`` names under ``root``, after resolving symlinks - or None if it is
    missing or resolves anywhere outside ``root``. Every corpus-derived input that ask
    reads goes through this."""
    root = Path(root).resolve()
    try:
        target = (root / rel).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not target.is_file() or not target.is_relative_to(root):
        return None
    return target
