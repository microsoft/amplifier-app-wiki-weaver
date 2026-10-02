"""Source and page parsing: frontmatter, header-line fallbacks, filename dates.

Deterministic text handling only.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_DATE_RE = re.compile(r"(20\d\d-\d\d-\d\d)")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_text(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter dict or None, body). Unparseable frontmatter -> None."""
    m = _FM_RE.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None, text[m.end() :]
    return (data if isinstance(data, dict) else None), text[m.end() :]


def source_meta(path: Path, text: str | None = None) -> dict:
    """Date, title and kind for a source: frontmatter, then header lines, then filename."""
    path = Path(path)
    text = read_text(path) if text is None else text
    fm, body = split_frontmatter(text)
    fm = fm or {}
    meta = {"filename": path.name, "title": None, "date": None, "kind": None}
    if fm.get("title"):
        meta["title"] = str(fm["title"])
    if fm.get("date"):
        meta["date"] = str(fm["date"])
    if fm.get("kind") or fm.get("type"):
        meta["kind"] = str(fm.get("kind") or fm.get("type"))
    for line in body.splitlines()[:40]:
        s = line.strip()
        if meta["title"] is None and s.startswith("# Transcript:"):
            meta["title"], meta["kind"] = s.split(":", 1)[1].strip(), meta["kind"] or "transcript"
        elif meta["title"] is None and s.startswith("# Chat:"):
            meta["title"], meta["kind"] = s.split(":", 1)[1].strip(), meta["kind"] or "chat"
        elif meta["date"] is None and s.lower().startswith("date:"):
            m = _DATE_RE.search(s)
            meta["date"] = m.group(1) if m else s.split(":", 1)[1].strip() or None
        elif s.lower().startswith("speakers:") and "speakers" not in meta:
            meta["speakers"] = s.split(":", 1)[1].strip()
    dates = _DATE_RE.findall(path.name)
    if meta["date"] is None and dates:
        # "pulled-" dates are export dates, not content dates; prefer the others.
        content = [d for d in dates if f"pulled-{d}" not in path.name] or dates
        meta["date"] = content[0]
        if len(content) > 1 and content[-1] != content[0]:
            meta["date_end"] = content[-1]
    if meta["title"] is None:
        stem = path.stem.removesuffix(".transcript")
        meta["title"] = re.split(r"__", stem)[0].strip() or stem
    meta["kind"] = meta["kind"] or "document"
    return meta


def source_summary_slug(filename: str) -> str:
    from .lib import slugify

    stem = Path(filename).stem.removesuffix(".transcript")
    return "source-" + slugify(stem)


def page_frontmatter(path: Path) -> dict | None:
    fm, _ = split_frontmatter(read_text(path))
    return fm


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
