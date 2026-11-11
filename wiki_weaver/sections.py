"""Where a page's sections start, read one way by roll-over, the kept-section checks and the
list step: at a `## ` or `# ` line at column 0 that is not inside a code fence. Pure text in,
text out; no other module of the package is imported here.
"""

from __future__ import annotations

import re

FENCE_RE = re.compile(r"[ \t]*(`{3,}|~{3,})(.*)")  # a code fence at any indent, and its info


def is_heading(line: str) -> bool:
    """A section heading: `## ` or `# ` at column 0. An indented one is text - code, or a
    bullet's continuation - never a section boundary."""
    return line.startswith(("## ", "# "))


def fenced(lines: list[str]) -> tuple[list[bool], int | None]:
    """Which lines are fenced code, fences included, and the index of a fence that never
    closes (None if every fence closes). A fence is three or more backticks or tildes at any
    indent; it closes at a run of the same character, at least as long, with nothing after."""
    out: list[bool] = []
    fence, opened = "", None
    for i, line in enumerate(lines):
        m = FENCE_RE.fullmatch(line.rstrip("\r\n"))
        if not fence:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence, opened = m.group(1), i
            out.append(bool(fence))
            continue
        out.append(True)
        run, rest = (m.group(1), m.group(2)) if m else ("", "")
        if run[:1] == fence[0] and len(run) >= len(fence) and not rest.strip():
            fence = ""
    return out, (opened if fence else None)


def pieces(body: str) -> list[str]:
    """``body`` - a page without its frontmatter - cut before each section heading outside
    code fences: the text before the first heading (maybe empty), then one piece per
    heading. Joined, the pieces are ``body`` byte for byte."""
    lines = body.splitlines(keepends=True)
    code, _ = fenced(lines)
    parts: list[list[str]] = [[]]
    for line, inside in zip(lines, code, strict=True):
        if is_heading(line) and not inside:
            parts.append([])
        parts[-1].append(line)
    return ["".join(p) for p in parts]


def unclosed_fence(body: str) -> int | None:
    """The line, counted from 0, where a code fence in ``body`` opens and never closes; None
    when every fence closes."""
    return fenced(body.splitlines(keepends=True))[1]
