"""Parse the lens's page types: the heading set or pattern each type uses, whether it
carries a `## Current state` section, and the sections it keeps. Deterministic; read by the
checks and by roll-over.

Format, under `## Page types`, one bullet per type with indented attribute lines:

    - **initiatives:** one page per initiative in flight ...
      Current state covers: where it stands, who owns what, ...
      Sections: Owners · Commitments · Blockers · Decisions · Open questions
      Keep: Decision list — one line per decision, with its status (covers: Decisions)
      Keep current: Current owners — one line per area and who owns it now
    - **weekly commitments and blockers:** ...
      Sections pattern: Week of YYYY-MM-DD
    - **decisions:** ...
      Current state: none

A type with no `Sections` or `Sections pattern` line gets no heading rule. `Keep:` and
`Keep current:` lines name sections that never move to history (`KeptSection`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .sections import fenced

BULLET_RE = re.compile(r"^[-*]\s+\**\s*([A-Za-z][A-Za-z \-]*?)\s*\**\s*(?::|\s-\s|\s—\s)")
# A Keep line's value: `<Name> <separator> <description>`, then an optional
# `(covers: <Section>)`. The name ends at the first separator: an em or en dash (spaces
# optional), a hyphen with a space on each side, or a colon.
KEEP_SEP_RE = re.compile(r"\s*[\u2014\u2013]\s*|\s+-\s+|\s*:\s*")
COVERS_RE = re.compile(r"\s*\(covers:\s*([^()]*?)\s*\)\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class KeptSection:
    """A section that pages of a type keep (`Keep:` / `Keep current:`): it never moves to
    history and stays out of the page's budget. ``description`` says what one line holds -
    the writer's instruction. A ``current`` list holds facts that change, so a line may be
    replaced. ``covers`` names the section whose entries the list must account for when
    they move to history."""

    name: str
    description: str = ""
    current: bool = False
    covers: str | None = None


@dataclass
class PageType:
    name: str
    sections: list[str] = field(default_factory=list)
    pattern: str | None = None
    current_state: bool = True
    covers: str | None = None
    kept: list[KeptSection] = field(default_factory=list)

    def heading_rule(self) -> bool:
        return bool(self.sections or self.pattern)

    def allows(self, heading: str) -> bool:
        """``heading`` is the text after `## `. A kept section's name is allowed too."""
        h = heading.strip()
        if any(h.lower() == s.lower() for s in [*self.sections, *(k.name for k in self.kept)]):
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
    """The lens's `## Page types` section as {type key: PageType}. A line inside a code fence
    (`sections.fenced`) is an example, never a heading or a type."""
    types: dict[str, PageType] = {}
    inside, cur = False, None
    lines = lens_text.splitlines()
    for line, code in zip(lines, fenced(lines)[0], strict=True):
        if code:
            continue
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
        key, val = " ".join(key.split()).lower(), val.strip()
        if key == "sections":
            cur.sections = [s.strip() for s in re.split(r"\s*[·•|,]\s*", val) if s.strip()]
        elif key == "sections pattern":
            cur.pattern = val.strip().strip("`").removeprefix("## ").strip('"')
        elif key == "current state":
            cur.current_state = val.lower() not in ("none", "no", "off")
        elif key == "current state covers":
            cur.covers = val
        elif key in ("keep", "keep current") and (k := _kept(val, key == "keep current")):
            cur.kept.append(k)
    return types


def _kept(val: str, current: bool) -> KeptSection | None:
    """A Keep line's value as a KeptSection (see KEEP_SEP_RE); None if it names nothing."""
    covers = None
    if m := COVERS_RE.search(val):
        covers, val = m.group(1) or None, val[: m.start()]
    parts = KEEP_SEP_RE.split(val, maxsplit=1)
    name = parts[0].strip().strip("*`\"'").removeprefix("## ").strip()
    desc = parts[1].strip().lstrip("*").strip() if len(parts) > 1 else ""
    return KeptSection(name, desc, current, covers) if name else None


def page_type_for(types: dict[str, PageType], fm_type) -> PageType | None:
    if not isinstance(fm_type, str) or not fm_type.strip():
        return None
    return types.get(type_key(fm_type))


def describe(types: dict[str, PageType]) -> list[dict]:
    """What the parser resolved, per page type: shown at ingest start and in result.json,
    so the heading rule and the kept sections are seen to be live rather than assumed."""
    out = []
    for key, pt in types.items():
        if pt.sections:
            rule = "sections: " + " · ".join(pt.sections)
        elif pt.pattern:
            rule = f"pattern: {pt.pattern}"
        else:
            rule = "no heading rule"
        out.append(
            {
                "type": pt.name,
                "key": key,
                "rule": rule,
                "current_state": "on" if pt.current_state else "opted out",
                "kept": [
                    k.name
                    + (" (current)" if k.current else "")
                    + (f" (covers: {k.covers})" if k.covers else "")
                    for k in pt.kept
                ],
            }
        )
    return out
