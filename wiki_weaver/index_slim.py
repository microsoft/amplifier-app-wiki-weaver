"""The index the brief and the writer read: index.md without the lines for source summary
pages and history volumes. Those lines grow with every source, and neither model picks one
of those pages - the brief's `source-` slugs are dropped in code, and volumes are never
written - so they are read for nothing.
"""

from __future__ import annotations

import re
from urllib.parse import unquote

ITEM_RE = re.compile(r"[-*+][ \t]")  # a top-level list item; indented lines continue one
HEADING_RE = re.compile(r"[ ]{0,3}#{1,6}(?:[ \t]|\r?$)")
GROUP_RE = re.compile(r"[ ]{0,3}#{1,2}(?:[ \t]|\r?$)")  # `#` and `##` headings bound a group
H2_RE = re.compile(r"[ ]{0,3}##(?:[ \t]|\r?$)")
VOLUME_RE = re.compile(r"[a-z0-9][a-z0-9-]*--\d{4}-\d{2}-\d{2}--\d{4}-\d{2}-\d{2}(?:-\d+)?\.md")


def slim_index(text: str) -> str:
    """``text`` without each list item whose first link targets a source summary page
    (`source-*.md`) or a history volume (`history/...`, or a volume's file name), with its
    continuation lines, and without a `##` heading left with nothing but blank lines under
    it. Every other byte stays, in order."""
    kept, dropping = [], False
    for line in text.splitlines(keepends=True):
        if ITEM_RE.match(line):
            dropping = _leaves(line)
        elif not line.strip() or HEADING_RE.match(line):
            dropping = False
        if not dropping:
            kept.append(line)
    groups: list[list[str]] = [[]]
    for line in kept:
        if GROUP_RE.match(line):
            groups.append([])
        groups[-1].append(line)
    return "".join(
        line
        for g in groups
        if not (g and H2_RE.match(g[0]) and not any(ln.strip() for ln in g[1:]))
        for line in g
    )


def _leaves(item: str) -> bool:
    """Whether a list item's first link targets a source summary page or a history volume."""
    target = _first_link_target(item)
    if target is None:
        return False
    target = target.strip()
    if target.startswith("<"):
        target = target[1 : target.find(">")] if ">" in target else target[1:]
    else:
        target = (target.split() or [""])[0]
    path = unquote(target.split("#", 1)[0]).removeprefix("./")
    name = path.rsplit("/", 1)[-1]
    return (
        ("/" not in path and name.startswith("source-") and name.endswith(".md"))
        or path.startswith("history/")
        or VOLUME_RE.fullmatch(name) is not None
    )


def _first_link_target(line: str) -> str | None:
    """What is inside the parentheses of the line's first `[label](target)` link. A label
    may hold brackets of its own: `[Garden walk [share] (2026-09-22)](source-...md)`."""
    i = line.find("[")
    while i != -1:
        j = _closing(line, i, "[", "]")
        if j is None:
            return None
        if line.startswith("(", j + 1):
            k = _closing(line, j + 1, "(", ")")
            return line[j + 2 : k] if k is not None else None
        i = line.find("[", j + 1)
    return None


def _closing(line: str, start: int, opening: str, closing: str) -> int | None:
    """The index of the bracket that closes the one at ``start``, or None."""
    depth = 0
    for k in range(start, len(line)):
        if line[k] == opening:
            depth += 1
        elif line[k] == closing:
            depth -= 1
            if depth == 0:
                return k
    return None
