"""Parse the lens's page types: the heading set or pattern each type uses, and whether it
carries a `## Current state` section. Deterministic; read by the checks.

Format, under `## Page types`, one bullet per type with indented attribute lines:

    - **initiatives:** one page per initiative in flight ...
      Current state covers: where it stands, who owns what, ...
      Sections: Owners · Commitments · Blockers · Decisions · Open questions
    - **weekly commitments and blockers:** ...
      Sections pattern: Week of YYYY-MM-DD
    - **decisions:** ...
      Current state: none

A type with no `Sections` or `Sections pattern` line gets no heading rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BULLET_RE = re.compile(r"^[-*]\s+\**\s*([A-Za-z][A-Za-z \-]*?)\s*\**\s*(?::|\s-\s|\s—\s)")


@dataclass
class PageType:
    name: str
    sections: list[str] = field(default_factory=list)
    pattern: str | None = None
    current_state: bool = True
    covers: str | None = None

    def heading_rule(self) -> bool:
        return bool(self.sections or self.pattern)

    def allows(self, heading: str) -> bool:
        """``heading`` is the text after `## `."""
        h = heading.strip()
        if any(h.lower() == s.lower() for s in self.sections):
            return True
        return bool(self.pattern and pattern_regex(self.pattern).fullmatch(h))


def pattern_regex(pattern: str) -> re.Pattern:
    rx = re.escape(pattern.strip())
    for tok, sub in (("YYYY", r"\d{4}"), ("MM", r"\d{2}"), ("DD", r"\d{2}")):
        rx = rx.replace(tok, sub)
    return re.compile(rx)


def type_key(name: str) -> str:
    """Normalize a lens type name or a frontmatter `type:` so 'initiatives' and
    'initiative', 'people' and 'person', 'weekly commitments and blockers' and
    'weekly-commitments' meet."""
    first = re.split(r"[\s\-_]+", name.strip().lower())[0] if name.strip() else ""
    if first in ("people", "person"):
        return "person"
    if first.endswith("ies"):
        return first[:-3] + "y"
    if first.endswith("s") and not first.endswith("ss"):
        return first[:-1]
    return first


def parse_page_types(lens_text: str) -> dict[str, PageType]:
    types: dict[str, PageType] = {}
    inside, cur = False, None
    for line in lens_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip().lower() == "page types"
            cur = None
            continue
        if not inside:
            continue
        m = BULLET_RE.match(line)
        if m:
            cur = PageType(name=m.group(1).strip())
            types[type_key(cur.name)] = cur
            continue
        if cur is None or not line.startswith((" ", "\t")):
            continue
        key, _, val = line.strip().partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "sections":
            cur.sections = [s.strip() for s in re.split(r"\s*[·•|,]\s*", val) if s.strip()]
        elif key == "sections pattern":
            cur.pattern = val.strip().strip("`").removeprefix("## ").strip('"')
        elif key == "current state":
            cur.current_state = val.lower() not in ("none", "no", "off")
        elif key == "current state covers":
            cur.covers = val
    return types


def page_type_for(types: dict[str, PageType], fm_type) -> PageType | None:
    if not isinstance(fm_type, str) or not fm_type.strip():
        return None
    return types.get(type_key(fm_type))
