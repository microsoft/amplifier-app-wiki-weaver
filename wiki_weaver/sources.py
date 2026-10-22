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
_MDY_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d\d)\b")
_SLICE_RE = re.compile(r"^_Slice:\s*(20\d\d-\d\d-\d\d)\s+to\s+(20\d\d-\d\d-\d\d)")
_RANGE_RE = re.compile(r"(20\d\d-\d\d-\d\d)_to_(20\d\d-\d\d-\d\d)")


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


_FM_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*)\s*:(?:\s+(.*?))?\s*$")
_FM_ITEM_RE = re.compile(r"^\s*-(?:\s+(.*?))?\s*$")
_NULLS = ("null", "Null", "NULL", "~")


def _tolerant_value(raw: str) -> object:
    """One frontmatter value as the writer meant it: YAML's reading when that is a plain
    scalar, a quoted string or a flow list; otherwise the raw text, unchanged."""
    if raw == "":
        return None
    try:
        v = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw
    if v is None:
        return None if raw in _NULLS else raw  # e.g. "# 3 notes" read as a comment
    if isinstance(v, dict):
        return raw  # "Ada: Bo" - a colon in a title, not a mapping
    if isinstance(v, list):
        return v if raw.startswith("[") else raw  # "- Planning" is a title, not a list
    if isinstance(v, str) and v != raw and raw[0] not in "'\"":
        return raw  # YAML dropped part of it (a trailing " #comment")
    return v


def normalize_frontmatter(text: str) -> str:
    """Make a page's frontmatter parse when a writer left a value unquoted.

    Frontmatter that already parses to a mapping (or no frontmatter at all) is returned
    unchanged, byte for byte. Otherwise the block is read line by line as top-level
    ``key: value`` pairs and ``- item`` lists under an empty key, and re-serialized with
    yaml.safe_dump, which quotes as needed. If a line cannot be read that way, or the
    result does not round-trip, the text is returned unchanged and the frontmatter check
    reports it as before. The body is never touched.
    """
    m = _FM_RE.match(text)
    if not m or split_frontmatter(text)[0] is not None:
        return text
    data: dict = {}
    key: str | None = None
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#") and not line.startswith(" "):
            continue
        km = None if line[:1].isspace() else _FM_KEY_RE.match(line)
        if km:
            key = km.group(1)
            data[key] = _tolerant_value(km.group(2) or "")
            continue
        im = _FM_ITEM_RE.match(line)
        if im and key is not None and (data[key] is None or isinstance(data[key], list)):
            if data[key] is None:
                data[key] = []
            data[key].append(_tolerant_value(im.group(1) or ""))
            continue
        return text  # not a shape we can read safely
    if not data:
        return text
    dumped = yaml.safe_dump(
        data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=2**31 - 1
    )
    try:
        if yaml.safe_load(dumped) != data:
            return text
    except yaml.YAMLError:
        return text
    return text[: m.start(1)] + dumped.rstrip("\n") + text[m.end(1) :]


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
            mdy = _MDY_RE.search(s)
            if m:
                meta["date"] = m.group(1)
            elif mdy:  # transcript headers: "Date: 5/15/2026, 2:20:15 PM"
                mo, dd, yy = mdy.groups()
                meta["date"] = f"{yy}-{int(mo):02d}-{int(dd):02d}"
            else:
                meta["date"] = s.split(":", 1)[1].strip() or None
        elif s.lower().startswith("speakers:") and "speakers" not in meta:
            meta["speakers"] = s.split(":", 1)[1].strip()
        elif meta["date"] is None and (m := _SLICE_RE.match(s)):
            # a windowed export is dated by the END of the window it covers
            meta["date_start"], meta["date"] = m.group(1), m.group(2)
    # No slice header: a filename range "A_to_B" is the window; date it by B.
    if meta["date"] is None and (m := _RANGE_RE.search(path.name)):
        meta["date_start"], meta["date"] = m.group(1), m.group(2)
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
