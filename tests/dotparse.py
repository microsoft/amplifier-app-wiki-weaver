"""Minimal DOT node-attribute reader for tests (handles quoted strings containing brackets)."""

from __future__ import annotations

import re
from pathlib import Path


def _strip_comments(text: str) -> str:
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _parse_attrs(body: str) -> dict[str, str]:
    attrs, i, n = {}, 0, len(body)
    while i < n:
        m = re.compile(r"\s*,?\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*").match(body, i)
        if not m:
            break
        key, i = m.group(1), m.end()
        if i < n and body[i] == '"':
            j, buf = i + 1, []
            while j < n and body[j] != '"':
                if body[j] == "\\" and j + 1 < n:
                    buf.append(body[j + 1] if body[j + 1] == '"' else body[j : j + 2])
                    j += 2
                    continue
                buf.append(body[j])
                j += 1
            attrs[key], i = "".join(buf), j + 1
        else:
            m2 = re.compile(r"[^,\s\]]+").match(body, i)
            attrs[key], i = (m2.group(0), m2.end()) if m2 else ("", i + 1)
    return attrs


def nodes(path: Path) -> dict[str, dict[str, str]]:
    """Return {node_id: attrs} for node statements (edges and graph attrs skipped)."""
    text = _strip_comments(Path(path).read_text())
    found: dict[str, dict[str, str]] = {}
    n = len(text)
    stmt = re.compile(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\[")
    for m in stmt.finditer(text):
        if m.group(1) in ("graph", "node", "edge"):
            continue
        j, depth, in_str = m.end(), 1, False
        while j < n and depth:
            c = text[j]
            if in_str:
                if c == "\\":
                    j += 2
                    continue
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            j += 1
        found[m.group(1)] = _parse_attrs(text[m.end() : j - 1])
    return found
