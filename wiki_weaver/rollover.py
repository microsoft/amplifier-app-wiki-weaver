"""Roll-over: move a hub page's oldest whole dates, verbatim, into immutable history volumes.

Hub pages (`resolve.md`, `weekly-commitments.md`, ...) gain dated entries with every source
and never lose them, so the text the writer is handed grows without limit. When a page's
record passes its budget, its oldest whole dates move, unchanged, into volumes
`history/<hub-slug>--<first>--<last>.md`, and the hub ends with a `## History` list of them.
Volumes sit below the corpus root, so they are not pages (`lib.page_files`).

- Record: the page body minus frontmatter, minus `## Current state ...`, minus `## History`.
- Entry: a top-level `- ` bullet and its continuation lines (every indented line after it
  among them); in a section without bullets, a paragraph. A label - a paragraph of
  unindented lines above a bullet, directly or across blank lines (`_label_start`) - heads a
  group: the label and every bullet below it, up to the next label, are one entry, dated by
  its newest bullet. A section's lines before its first entry (its lead) stay with its
  heading, unless the whole section leaves (below). The text before the first `## ` heading
  never moves. A superseded block that spans entries makes them one. Headings are `## ` and
  `# ` at column 0; indented and fenced lines are never headings, entries or labels; a page
  with a fence that never closes is not rolled, nor one whose entry sections hold a layout
  it cannot split: a `###` subsection, a numbered, `*` or `+` list, a table, or an HTML
  comment over several lines, at column 0 (`LAYOUT_RES`).
- A section the lens does not name whose entries all leave goes whole: its lead travels
  with its first entry and its heading leaves the hub. Volumes keep the hub's order.
- Entry date: the first YYYY-MM-DD in its first 60 characters, superseded markers stripped;
  else the first date in its section heading; else the date of the nearest dated entry above
  it in its section; else none, and it never moves.

Planning is pure text in, text out, and `plan_rollover` returns nothing that
`check_conservation` rejects. `roll_wiki` applies the plans to a wiki on disk.
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from itertools import accumulate, pairwise
from pathlib import Path

import yaml

from . import ledger as lg
from . import lens
from .checks import (
    CITE_RE,
    CLOSE_RE,
    CURRENT_STATE_RE,
    MARKER_RE,
    WIKILINK_RE,
    kept_size,
    superseded_spans,
)
from .lib import GIT_ID, atomic_temp_files, atomic_write_text, contained_file, page_files
from .sections import fenced as _fenced
from .sections import is_heading as _is_heading
from .sources import FRONTMATTER_RE, normalize_ws, read_text, split_frontmatter

DEFAULT_BUDGET = 48_000  # characters of record
TYPE_BUDGETS = {"weekly": 96_000}
HISTORY_DIR = "history"
HISTORY_HEADING = "## History"
DATE_WINDOW = 60  # an entry's own date is in its first 60 characters

DATE_RE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
LEADING_MARKERS_RE = re.compile(rf"^(?:(?:{MARKER_RE.pattern}|{CLOSE_RE.pattern})[ \t]*)+")
HISTORY_ITEM_RE = re.compile(
    r"^- \[(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2}) \u00b7 (\d+) entries\]\((history/[^)\s]+)\)$"
)
# A label glued under a bullet's lines: one line, wholly bold, then at most `:` or `— text`.
BOLD_LABEL_RE = re.compile(r"\*\*[^*]+\*\*(?::|[ \t]*\u2014.*)?")
# Layouts rollover cannot split safely. At column 0, outside fences, in a section whose
# entries move, any of them refuses the page (RolloverRefused) instead of a guess.
LAYOUT_RES = (
    (re.compile(r"#{3,} "), "a level-3 or deeper heading"),
    (re.compile(r"\d+[.)] "), "a numbered list item"),
    (re.compile(r"[*+] "), "a * or + bullet"),
    (re.compile(r"\|"), "a table row"),
)
CLOSED_COMMENT_RE = re.compile(r"<!--.*?-->")

# Section kinds. FIXED: the text before the first heading and `# ` sections (record; never
# moves). STATE: `## Current state ...`. HISTORY: `## History` (regenerated). ENTRIES: every
# other `## ` section (record; split into entries).
FIXED, STATE, HISTORY, ENTRIES = "fixed", "state", "history", "entries"
KEPT = "kept"  # a section the lens keeps (`Keep:` lines): never moves, outside the record


class ConservationError(Exception):
    """A roll-over would lose or invent text: a code defect. Nothing may be written."""


class RolloverRefused(ConservationError):
    """A page this module will not roll as it stands - its `## History` holds a line that is
    not a volume link, a code fence on it never closes, or a section whose entries move
    holds a layout it cannot split (`LAYOUT_RES`, or an HTML comment over several lines) -
    so nothing is written. Not a defect: a caller may skip the page and report it; one that
    stops on ConservationError still stops."""


@dataclass
class Entry:
    text: str  # as written, with the blank lines after it
    date: str | None = None  # None: it never moves


@dataclass
class Section:
    heading: str  # the heading line as written, line break included; "" before the first
    lead: str = ""  # the lines before its first entry; they move only with the whole section
    entries: list[Entry] = field(default_factory=list)
    kind: str = ENTRIES

    @property
    def text(self) -> str:
        return self.heading + self.lead + "".join(e.text for e in self.entries)


@dataclass
class PageParts:
    frontmatter: str  # as written, fences and the blank lines after them included; "" if none
    sections: list[Section]
    unclosed_fence: int | None = None  # the body line of a code fence that never closes
    layout: tuple[int, str, str] | None = None  # a layout refused: body line, what, its text


@dataclass(frozen=True)
class RolloverPlan:
    hub_text: str
    volumes: list[tuple[str, str]]  # (path under the corpus root, text), oldest first
    moved_entries: int
    record_before: int
    record_after: int
    over_budget: bool  # the record is still over budget after everything movable moved


# ---------------------------------------------------------------- parsing


def parse_page(
    text: str,
    keep: frozenset[str] | set[str] = frozenset(),
    kept: frozenset[str] | set[str] = frozenset(),
) -> PageParts:
    """Split a page into frontmatter and sections, and each `## ` section into its lead and
    dated entries. `render_page` puts it back byte for byte. A section starts at a line that
    starts `## ` or `# ` at column 0, as `checks.check_current_state` reads headings
    (`steps.strip_current_state` and `checks._headings` also take an indented one). Indented
    and fenced lines are never headings, entries or labels.

    ``keep``: the `_heading_key`s of the sections the lens names. A lens-named section's
    lead never moves, so there a paragraph a blank line above its first bullet is its lead,
    not that bullet's label. ``kept``: the `_heading_key`s of the sections the lens keeps
    (`Keep:` lines). Such a section is KEPT: never split into entries, never moved, and
    outside the record, however dated its lines look."""
    m = FRONTMATTER_RE.match(text)
    head = text[: m.end()] if m else ""
    body = text[len(head) :]
    lines = body.splitlines(keepends=True)
    offs = list(accumulate(map(len, lines), initial=0))
    spans = superseded_spans(body)
    fenced, unclosed = _fenced(lines)
    heads = [i for i, line in enumerate(lines) if not fenced[i] and _is_heading(line)]
    sections = [Section("", "".join(lines[: heads[0] if heads else len(lines)]), kind=FIXED)]
    placed: list[tuple[Entry, int, int]] = []
    layout: tuple[int, str, str] | None = None
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        kind = _kind(lines[h])
        if kind == ENTRIES and _heading_key(lines[h]) in kept:
            kind = KEPT
        if kind != ENTRIES:
            sections.append(Section(lines[h], "".join(lines[h + 1 : end]), kind=kind))
            continue
        layout = layout or _layout(lines, fenced, h, end)
        units = _entries(lines, offs, spans, fenced, h, end, _heading_key(lines[h]) not in keep)
        starts = [a for a, _ in units]
        bounds = list(zip(starts, [*starts[1:], end]))
        entries = [Entry("".join(lines[a:b])) for a, b in bounds]
        dated = []  # the texts that date each entry: its own, or each bullet of a labelled group
        for (_, group), (a, b) in zip(units, bounds):
            marks = [*group, b] if group else [a, b]
            dated.append(["".join(lines[x:y]) for x, y in pairwise(marks)])
        _date_entries(lines[h], entries, dated)
        placed += [(e, offs[a], offs[b]) for e, (a, b) in zip(entries, bounds)]
        lead = "".join(lines[h + 1 : starts[0] if starts else end])
        sections.append(Section(lines[h], lead, entries))
    # A superseded block that crosses a heading could only move split in two: what it
    # touches never moves.
    crossing = [(a, b) for a, b in spans if any(a < offs[h] < b for h in heads)]
    for e, s, t in placed:
        if any(s < b and a < t for a, b in crossing):
            e.date = None
    return PageParts(head, sections, unclosed, layout)


def render_page(parts: PageParts) -> str:
    return parts.frontmatter + "".join(s.text for s in parts.sections)


def has_history(page: str | Path) -> bool:
    """True if a page - its text, or the file at a path - has a `## History` section that
    lists at least one volume in `history/`: some of its entries now live there."""
    return bool(listed_volumes(read_text(page) if isinstance(page, Path) else page))


def listed_volumes(text: str) -> list[str]:
    """The volumes a page's `## History` lists (`history/<file>.md`), in its order."""
    if HISTORY_HEADING not in text:
        return []
    return [
        m.group(4)
        for s in parse_page(text).sections
        if s.kind == HISTORY
        for line in s.lead.splitlines()
        if (m := HISTORY_ITEM_RE.match(normalize_ws(line)))
    ]


def volume_entries(text: str, heading: str, keep_headings: Iterable[str] = ()) -> list[str]:
    """The entries volume ``text`` holds under its `## <heading>` section, as written and in
    order: what moved there from that section of the hub. They are split as `parse_page`
    splits the hub, ``keep_headings`` being the sections the lens names for its type."""
    key = _heading_key(heading)
    keep = {_heading_key(h) for h in keep_headings}
    return [
        e.text
        for s in parse_page(text, keep).sections
        if s.kind == ENTRIES and _heading_key(s.heading) == key
        for e in s.entries
    ]


def _kind(line: str) -> str:
    st = line.rstrip()
    if not st.startswith("## "):
        return FIXED
    if CURRENT_STATE_RE.match(st):
        return STATE
    return HISTORY if st == HISTORY_HEADING else ENTRIES


def _is_bullet(line: str) -> bool:
    return LEADING_MARKERS_RE.sub("", line, count=1).startswith("- ")


def _layout(lines: list[str], fenced: list[bool], h: int, end: int) -> tuple[int, str, str] | None:
    """The first line of the section headed by line ``h`` in a layout rollover cannot split
    safely - one of LAYOUT_RES, or an HTML comment still open at the end of its line - with
    what it is and its text; None if there is none. Fenced and indented lines are text
    inside an entry and never count."""
    for i in range(h, end):
        line = lines[i]
        if fenced[i] or line[:1] in (" ", "\t"):
            continue
        bare = LEADING_MARKERS_RE.sub("", line, count=1)
        what = next((w for rx, w in LAYOUT_RES if rx.match(bare)), None)
        if what is None and "<!--" in CLOSED_COMMENT_RE.sub("", line):
            what = "an HTML comment that spans lines"
        if what is not None:
            return i, what, line.rstrip("\r\n")
    return None


def _entries(
    lines: list[str],
    offs: list[int],
    spans: list[tuple[int, int]],
    fenced: list[bool],
    h: int,
    end: int,
    lead_labels: bool = True,
) -> list[tuple[int, list[int]]]:
    """The entries of the section headed by line ``h``: where each starts, and the bullets of
    a labelled group ([] for any other entry). Entries are its top-level bullets, or if it has
    none its paragraphs. A label (`_label_start`) heads a group - the label and every bullet
    below it up to the next label - and a group is one entry. Fenced lines start nothing. A
    start inside a superseded block moves back to the line the block opens, when the block
    opens that line; otherwise the block already began in the entry above (or in the lead, or
    above the heading) and the start joins it. So a label inside a block begun above belongs
    to that block and heads nothing: its bullets are read as if it were not there."""
    rows = [i for i in range(h + 1, end) if not fenced[i]]
    bullets = [i for i in rows if _is_bullet(lines[i])]
    starts: list[tuple[int, bool]] = []  # (start, whether a label starts a group there)

    def placed(i: int) -> int | None:
        opened = next((a for a, b in spans if a < offs[i] < b), None)
        if opened is None:
            return i
        j = bisect_right(offs, opened) - 1
        if lines[j][: opened - offs[j]].strip() or j <= (starts[-1][0] if starts else h):
            return None
        return j

    labelled = False
    for b in bullets:
        # in a lens-named section, a paragraph a blank line above its first bullet is its lead
        k = _label_start(lines, fenced, h, b, lead_labels or b != bullets[0])
        if k is not None and (at := placed(k)) is not None:
            starts.append((at, True))
            labelled = True
        elif not labelled and (at := placed(b)) is not None:
            starts.append((at, False))
    if not bullets:
        for i in rows:
            paragraph = (
                lines[i].strip()
                and lines[i][:1] not in (" ", "\t")
                and (i == h + 1 or not lines[i - 1].strip())
            )
            if paragraph and (at := placed(i)) is not None:
                starts.append((at, False))
    ends = [a for a, _ in starts[1:]] + [end]
    return [
        (a, [x for x in bullets if a <= x < b] if group else [])
        for (a, group), b in zip(starts, ends)
    ]


def _label_start(
    lines: list[str], fenced: list[bool], h: int, b: int, across_blanks: bool = True
) -> int | None:
    """Where the label of bullet line ``b`` starts, or None. A label is a paragraph of
    unindented lines - not bullets, not fenced, saying more than a superseded marker - above
    ``b``, directly or across blank lines. Across blank lines it must follow a blank line or
    the heading: glued to the lines above, it continues them. Directly above ``b`` and glued
    to a bullet's lines, it is a label only as one explicit bold line (`BOLD_LABEL_RE`); any
    other - a wrapped line, a quote, superseded text - continues the entry above."""
    j = b
    while across_blanks and j - 1 > h and not lines[j - 1].strip() and not fenced[j - 1]:
        j -= 1
    k = j
    while k - 1 > h:
        line = lines[k - 1]
        if not line.strip() or fenced[k - 1] or _is_bullet(line) or line[:1] in (" ", "\t"):
            break
        k -= 1
    if k == j or not any(_unmark(lines[i]).strip() for i in range(k, j)):
        return None
    glued = k - 1 > h and bool(lines[k - 1].strip())
    if glued and (j < b or k + 1 < j or not BOLD_LABEL_RE.fullmatch(lines[k].strip())):
        return None
    return k


def _date_entries(heading: str, entries: list[Entry], dated: list[list[str]]) -> None:
    """Each entry's date: the first date in the first DATE_WINDOW characters of its text, else
    its section heading's, else the nearest dated one's above it. A labelled group is dated
    by its newest bullet, each dated that way, and never moves if one of them has no date."""
    from_heading = _first_date(_unmark(heading))
    above = None
    for e, texts in zip(entries, dated):
        ds = []
        for t in texts:
            d = _first_date(_unmark(t).lstrip()[:DATE_WINDOW]) or from_heading or above
            above = d or above
            ds.append(d)
        found = [d for d in ds if d]
        e.date = max(found) if found and len(found) == len(ds) else None


def _first_date(text: str) -> str | None:
    for m in DATE_RE.finditer(text):
        try:
            date.fromisoformat(m.group(0))
        except ValueError:
            continue
        return m.group(0)
    return None


def _unmark(text: str) -> str:
    return CLOSE_RE.sub("", MARKER_RE.sub("", text))


# ---------------------------------------------------------------- the cut


def budget_for(type_key: str | None, overrides: dict[str, int] | None = None) -> int:
    """The record budget for a page type (`lens.type_key` form). Override keys may be any
    form `lens.type_key` reads (`weekly`, `initiatives`, ...)."""
    budgets = TYPE_BUDGETS | {lens.type_key(k): v for k, v in (overrides or {}).items()}
    return budgets.get(lens.type_key(type_key), DEFAULT_BUDGET) if type_key else DEFAULT_BUDGET


def plan_rollover(
    name: str,
    text: str,
    *,
    budget: int,
    keep_headings: set[str],
    kept_headings: set[str] | frozenset[str] = frozenset(),
    known: dict[int, str],
    taken: set[str],
    today: str,
) -> RolloverPlan | None:
    """Plan the roll-over of hub page ``name`` (``resolve.md``) whose text is ``text``.

    None if its record is within ``budget``. Otherwise whole dates move, oldest first, until
    the record is within half the budget; the newest date never moves. A volume closes each
    time what moved into it reaches half the budget. If the record is still over budget once
    everything movable has moved (undated text, one huge newest date) the plan says
    ``over_budget``; with nothing movable it has no volumes and the hub is unchanged.

    ``keep_headings``: the sections the lens names for the page's type. Such a section keeps
    its heading and lead on the hub when its entries all move; any other leaves whole, lead
    and heading too, unless its lead holds superseded text. ``kept_headings``: the sections
    the lens keeps (`Keep:` lines). They never move and are outside the record, so they
    never count toward ``budget``.
    ``known``: source_id -> filename (`ledger.known_sources`), for `sources:`. ``taken``:
    volume paths already in use (``history/<file>.md``). ``today`` dates the move.

    Raises RolloverRefused for a page it will not roll as it stands, and ConservationError
    if the plan would lose or invent text.
    """
    keep = {_heading_key(h) for h in keep_headings}
    kept = {_heading_key(h) for h in kept_headings}
    parts = parse_page(text, keep, kept)
    before = _record_size(parts)
    if before <= budget:
        return None
    listed = [
        normalize_ws(ln) for s in parts.sections if s.kind == HISTORY for ln in s.lead.splitlines()
    ]
    if bad := [ln for ln in listed if ln and not HISTORY_ITEM_RE.match(ln)]:
        raise RolloverRefused(
            f"{name}: its ## History holds a line that is not a volume link: {bad[0][:120]}"
        )
    if parts.unclosed_fence is not None:
        line = parts.frontmatter.count("\n") + parts.unclosed_fence + 1
        raise RolloverRefused(f"{name}: the code fence opened at line {line} never closes")
    if parts.layout is not None:
        i, what, shown = parts.layout
        line = parts.frontmatter.count("\n") + i + 1
        raise RolloverRefused(
            f"{name}: line {line} is {what} ({shown!r}); rollover does not split that layout"
        )
    half = budget // 2
    dated = [
        (si, ei, e.date)
        for si, s in enumerate(parts.sections)
        for ei, e in enumerate(s.entries)
        if e.date
    ]
    movable = sorted({d for _, _, d in dated})[:-1]  # the newest date never moves
    # Section sizes are updated as each date leaves; the blank line written before
    # `## History` is not counted, so the built hub is measured and, if it is still over
    # half, one more date goes.
    sizes = [len(s.text) for s in parts.sections]
    gone: dict[int, set[int]] = defaultdict(set)
    record, n = before, 0
    while n < len(movable) and record > half:
        touched = set()
        for si, ei, d in dated:
            if d == movable[n]:
                gone[si].add(ei)
                touched.add(si)
        n += 1
        for si in touched:
            size = len(_kept(parts.sections[si], gone[si], keep))
            record, sizes[si] = record + size - sizes[si], size
    if n == 0:  # one date, or none: nothing can move
        plan = RolloverPlan(text, [], 0, before, before, True)
    else:
        while True:
            hub, volumes, moved = _build(name, parts, movable[:n], half, keep, known, taken, today)
            after = _record_size(parse_page(hub, kept=kept))
            if after <= half or n == len(movable):
                break
            n += 1
        plan = RolloverPlan(hub, volumes, moved, before, after, after > budget)
    check_conservation(text, plan, keep, kept)
    return plan


def _record_size(parts: PageParts) -> int:
    return sum(len(s.text) for s in parts.sections if s.kind in (FIXED, ENTRIES))


def _heading_key(heading: str) -> str:
    return normalize_ws(_unmark(heading)).lstrip("#").strip().lower()


def _leaves_whole(s: Section, gone: set[int], keep: set[str]) -> bool:
    """All of section ``s``'s entries leave (``gone``), the lens does not name it, and its
    lead holds no superseded text (which could belong to a block that crosses its heading):
    then its lead goes with its first entry, and its heading leaves the hub."""
    return (
        bool(gone)
        and len(gone) == len(s.entries)
        and _heading_key(s.heading) not in keep
        and not (MARKER_RE.search(s.lead) or CLOSE_RE.search(s.lead))
    )


def _kept(s: Section, gone: set[int], keep: set[str]) -> str:
    """Section ``s`` once its entries ``gone`` have left: nothing if it leaves whole, else
    its heading, its lead and what is left, ending in a blank line."""
    if _leaves_whole(s, gone, keep):
        return ""
    rest = [e for i, e in enumerate(s.entries) if i not in gone]
    return _blank_line_after(s.heading + s.lead + "".join(e.text for e in rest))


def _blank_line_after(text: str) -> str:
    text = _ends_line(text)
    lines = text.splitlines()
    return text if lines and not lines[-1].strip() else text + "\n"


def _ends_line(text: str) -> str:
    return text if text.endswith(("\n", "\r")) else text + "\n"


def _build(
    name: str,
    parts: PageParts,
    dates: list[str],
    half: int,
    keep: set[str],
    known: dict[int, str],
    taken: set[str],
    today: str,
) -> tuple[str, list[tuple[str, str]], int]:
    """The hub and the volumes once every entry dated one of ``dates`` (oldest first) moves."""
    slug = name.removesuffix(".md")
    title = str((split_frontmatter(parts.frontmatter)[0] or {}).get("title") or slug)
    amount: dict[str, int] = defaultdict(int)
    for s in parts.sections:
        for e in s.entries:
            if e.date:
                amount[e.date] += len(e.text)
    groups, cur, size = [], [], 0
    for d in dates:
        cur.append(d)
        size += amount[d]
        if size >= half:
            groups.append(cur)
            cur, size = [], 0
    if cur:
        groups.append(cur)
    old_items = [
        item
        for s in parts.sections
        if s.kind == HISTORY
        for item in map(normalize_ws, s.lead.splitlines())
        if HISTORY_ITEM_RE.match(item)
    ]
    used = set(taken) | {_item_match(item).group(4) for item in old_items}
    leaving = set(dates)
    gone = [{ei for ei, e in enumerate(s.entries) if e.date in leaving} for s in parts.sections]
    whole = [_leaves_whole(s, g, keep) for s, g in zip(parts.sections, gone)]
    volumes, items, moved = [], [], 0
    for group in groups:
        these = set(group)
        secs = []  # (heading, lead if it moves here, entries in the hub's order)
        for si, s in enumerate(parts.sections):
            es = [e for e in s.entries if e.date in these]
            if es:
                secs.append((s.heading, s.lead if whole[si] and es[0] is s.entries[0] else "", es))
        count = sum(len(es) for _, _, es in secs)
        first, last = group[0], group[-1]
        path = _free_path(f"{HISTORY_DIR}/{slug}--{first}--{last}", used)
        used.add(path)
        volumes.append((path, _volume(name, slug, title, first, last, today, secs, known)))
        items.append(f"- [{first} to {last} \u00b7 {count} entries]({path})")
        moved += count
    body = "".join(
        _kept(s, gone[si], keep) if gone[si] else s.text
        for si, s in enumerate(parts.sections)
        if s.kind != HISTORY
    )
    listing = sorted(old_items + items, key=_item_key, reverse=True)
    body = _blank_line_after(body) + HISTORY_HEADING + "\n" + "".join(f"{i}\n" for i in listing)
    return _with_sources(parts.frontmatter, body, known) + body, volumes, moved


def _item_match(item: str) -> re.Match[str]:
    m = HISTORY_ITEM_RE.match(item)
    assert m is not None  # only called on items that match
    return m


def _item_key(item: str) -> tuple[str, str]:
    m = _item_match(item)
    return m.group(2), m.group(1)


def _free_path(stem: str, used: set[str]) -> str:
    path, n = f"{stem}.md", 1
    while path in used:
        n += 1
        path = f"{stem}-{n}.md"
    return path


def _volume(
    name: str,
    slug: str,
    title: str,
    first: str,
    last: str,
    today: str,
    secs: list[tuple[str, str, list[Entry]]],
    known: dict[int, str],
) -> str:
    """A volume: the moved entries as written, under the hub's own headings, in the hub's
    order - of sections and of entries within each - and a section's lead when it came."""
    moved = "\n".join(
        _ends_line(h) + (lead or "\n") + "\n".join(_trimmed(e.text) for e in es)
        for h, lead, es in secs
    )
    ids = {int(m.group(1)) for m in CITE_RE.finditer(moved)}
    fm = {
        "title": f"{title} \u2014 history, {first} to {last}",
        "type": "history",
        "history_of": name,
        "sources": sorted({known[i] for i in ids if i in known}),
        "last_updated": date.fromisoformat(last),
    }
    head = yaml.safe_dump(
        fm, sort_keys=False, allow_unicode=True, default_flow_style=False, width=2**31 - 1
    )
    intro = (
        f"History of [[{slug}]]: entries dated {first} to {last}, moved here unchanged on {today}."
    )
    return f"---\n{head}---\n\n{intro}\n\n{moved}"


def _trimmed(text: str) -> str:
    lines = text.splitlines(keepends=True)
    while lines and not lines[-1].strip():
        lines.pop()
    return _ends_line("".join(lines))


def _with_sources(head: str, body: str, known: dict[int, str]) -> str:
    """The hub's frontmatter with `sources` set to the files of the ids ``body`` cites: those
    it listed, in its order, then any it did not, sorted. Every other line is kept as written.
    Frontmatter that is missing, does not parse, or cannot be edited verifiably is kept."""
    m = FRONTMATTER_RE.match(head)
    fm, _ = split_frontmatter(head)
    if m is None or fm is None:
        return head
    ids = {int(c.group(1)) for c in CITE_RE.finditer(body)}
    cited = {known[i] for i in ids if i in known}
    listed = fm.get("sources") if isinstance(fm.get("sources"), list) else []
    kept = [f for f in dict.fromkeys(map(str, listed or [])) if f in cited]
    files = kept + sorted(cited - set(kept))
    inner = _set_sources(m.group(1), files)
    try:
        ok = yaml.safe_load(inner) == {**fm, "sources": files}
    except yaml.YAMLError:
        ok = False
    return head[: m.start(1)] + inner + head[m.end(1) :] if ok else head


def add_sources(text: str, files: Iterable[str]) -> str:
    """Page ``text`` with those of ``files`` its frontmatter `sources:` does not list added
    at the end of that list, in the indentation it uses; every other line as written.
    Unchanged when it lists them all, or its frontmatter is missing, does not parse, holds
    a `sources` that is not a list, or cannot be edited verifiably."""
    m = FRONTMATTER_RE.match(text)
    fm, _ = split_frontmatter(text)
    if m is None or fm is None or not isinstance(fm.get("sources", []), list | None):
        return text
    listed = list(fm.get("sources") or [])
    new = [f for f in dict.fromkeys(files) if f not in listed]
    if not new:
        return text
    inner = _set_sources(m.group(1), [*listed, *new])
    try:
        ok = yaml.safe_load(inner) == {**fm, "sources": [*listed, *new]}
    except yaml.YAMLError:
        ok = False
    return text[: m.start(1)] + inner + text[m.end(1) :] if ok else text


def _set_sources(inner: str, files: list[str]) -> str:
    """Frontmatter YAML with its top-level `sources:` block replaced (or added), in the
    indentation its list used."""
    lines = inner.split("\n")
    at = next((i for i, ln in enumerate(lines) if re.match(r"sources\s*:", ln)), len(lines))
    end = min(at + 1, len(lines))
    while end < len(lines) and (lines[end][:1] in (" ", "\t", "-") or not lines[end].strip()):
        end += 1
    while end > at + 1 and not lines[end - 1].strip():
        end -= 1
    item = next((ln for ln in lines[at + 1 : end] if ln.lstrip().startswith("-")), "  -")
    indent = item[: len(item) - len(item.lstrip())]
    block = [f"{indent}- {_scalar(f)}" for f in files]
    return "\n".join([*lines[:at], "sources:" if block else "sources: []", *block, *lines[end:]])


def _scalar(value: str) -> str:
    try:
        if yaml.safe_load(f"- {value}") == [value]:
            return value
    except yaml.YAMLError:
        pass
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- a wiki on disk


def roll_wiki(
    wiki: Path, *, dry_run: bool, overrides: dict[str, int] | None = None, today: str | None = None
) -> dict:
    """Roll every page of ``wiki`` whose record is over its budget, and report it.

    Pages: the root pages (`lib.page_files`) but source summaries. A page's budget comes
    from its frontmatter `type` (`budget_for`, with ``overrides``); the headings it keeps,
    from the lens's Sections for that type. A page this module will not roll - not UTF-8,
    or refused by `plan_rollover` - is skipped and listed under `refused`. In a git repo
    each hub is committed alone with its new volumes. ``dry_run`` writes nothing.

    Raises ConservationError, naming the page, if a plan would lose text; pages rolled
    before it stay rolled.
    """
    wiki = Path(wiki)
    today = today or date.today().isoformat()
    known, types, taken = lg.known_sources(wiki), page_types(wiki), volumes_in(wiki)
    commit = not dry_run and (wiki / ".git").exists()
    report: dict = {
        "pages_checked": 0,
        "pages_rolled": 0,
        "volumes_created": 0,
        "over_budget": [],
        "refused": [],
        "pages": [],
    }
    for path in page_files(wiki):
        if path.name.startswith("source-"):
            continue
        report["pages_checked"] += 1
        try:
            plan = plan_page(
                wiki,
                path.name,
                types=types,
                known=known,
                taken=taken,
                overrides=overrides,
                today=today,
            )
        except RolloverRefused as e:
            report["refused"].append({"page": path.name, "reason": str(e)})
            continue
        if plan is None:
            continue
        if plan.over_budget:
            report["over_budget"].append(path.name)
        if not plan.volumes:
            continue
        paths = [p for p, _ in plan.volumes]
        taken.update(paths)
        report["pages_rolled"] += 1
        report["volumes_created"] += len(paths)
        report["pages"].append(
            {
                "page": path.name,
                "record_before": plan.record_before,
                "record_after": plan.record_after,
                "volumes": paths,
            }
        )
        if dry_run:
            continue
        written = write_plan(wiki, path.name, plan)
        if commit:
            _commit(wiki, written, commit_message(path.name, plan))
    return report


def page_types(wiki: Path) -> dict[str, lens.PageType]:
    """The lens's page types; none when lens.md is missing or resolves outside the corpus."""
    lens_file = contained_file(wiki, "lens.md")
    return lens.parse_page_types(lens_file.read_text("utf-8")) if lens_file else {}


def volumes_in(wiki: Path) -> set[str]:
    """The volume paths already in ``wiki``'s `history/`: no new volume takes one."""
    return {f"{HISTORY_DIR}/{p.name}" for p in (Path(wiki) / HISTORY_DIR).glob("*.md")}


def _page_type(
    path: Path, types: dict[str, lens.PageType]
) -> tuple[str | None, lens.PageType | None]:
    """The page's text, as UTF-8, and its lens type; (None, None) when it cannot be read."""
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None, None
    kind = (split_frontmatter(text)[0] or {}).get("type")
    return text, lens.page_type_for(types, kind if isinstance(kind, str) else None)


def kept_over_half(wiki: Path, name: str, types: dict[str, lens.PageType]) -> dict | None:
    """Page ``name``'s kept sections (its type's `Keep:` lines), when together they exceed
    half its budget: {page, chars, limit}. Reported, never a finding or a refusal; None when
    its type keeps nothing, or they fit."""
    path = contained_file(wiki, name)
    text, pt = _page_type(path, types) if path else (None, None)
    if text is None or pt is None or not pt.kept:
        return None
    limit = budget_for(lens.type_key(pt.name)) // 2
    size = kept_size(text, [k.name for k in pt.kept])
    return {"page": name, "chars": size, "limit": limit} if size > limit else None


def history_without_keep(wiki: Path, types: dict[str, lens.PageType]) -> list[str]:
    """The pages that have history volumes but whose type keeps no sections, so nothing on
    them keeps a line for what moved: `lint` and `doctor` warn about them."""
    out: set[str] = set()
    for vol in (Path(wiki) / HISTORY_DIR).glob("*.md"):
        name = vol.name.rsplit("--", 2)[0] + ".md"  # history/<page>--<first>--<last>.md
        path = None if name in out else contained_file(wiki, name)
        if path is None:
            continue
        text, pt = _page_type(path, types)
        if text is not None and (pt is None or not pt.kept):
            out.add(name)
    return sorted(out)


def plan_page(
    wiki: Path,
    name: str,
    *,
    types: dict[str, lens.PageType],
    known: dict[int, str],
    taken: set[str],
    overrides: dict[str, int] | None = None,
    today: str | None = None,
) -> RolloverPlan | None:
    """`plan_rollover` for page ``name`` of ``wiki`` as written on disk. Its budget comes
    from its frontmatter `type` (`budget_for`, with ``overrides``); the headings it keeps,
    from the Sections ``types`` (`page_types`) gives that type.

    Raises RolloverRefused for a page that is a link (writing would replace the link with a
    copy), not UTF-8, or one plan_rollover will not roll; ConservationError, naming the
    page, if the plan would lose text."""
    if (Path(wiki) / name).is_symlink():
        raise RolloverRefused(f"{name}: a link to another page; that page rolls on its own")
    try:
        text = (Path(wiki) / name).read_bytes().decode("utf-8")  # no newline translation
    except UnicodeDecodeError:
        raise RolloverRefused(f"{name}: not valid UTF-8") from None
    kind = (split_frontmatter(text)[0] or {}).get("type")
    kind = kind if isinstance(kind, str) else None
    pt = lens.page_type_for(types, kind)
    try:
        return plan_rollover(
            name,
            text,
            budget=budget_for(lens.type_key(kind) if kind else None, overrides),
            keep_headings=set(pt.sections) if pt else set(),
            kept_headings={k.name for k in pt.kept} if pt else set(),
            known=known,
            taken=taken,
            today=today or date.today().isoformat(),
        )
    except RolloverRefused:
        raise
    except ConservationError as e:
        raise ConservationError(f"{name}: {e}") from e


def write_plan(wiki: Path, name: str, plan: RolloverPlan) -> list[str]:
    """Write ``plan`` for page ``name`` of ``wiki``: the volumes, then the hub - a crash in
    between leaves a second copy, never a loss. A volume is never overwritten. Volumes are
    0644 and the hub keeps its mode. Temp files that a killed write of these same paths left
    beside them go first. Returns the paths written, the hub first."""
    wiki = Path(wiki)
    if clash := [p for p, _ in plan.volumes if (wiki / p).exists()]:
        raise FileExistsError(f"{clash[0]} exists; a volume is never overwritten")
    hub = wiki / name
    for target in [*(wiki / p for p, _ in plan.volumes), hub]:
        for tmp in atomic_temp_files(target):
            tmp.unlink(missing_ok=True)
    for p, text in plan.volumes:
        atomic_write_text(wiki / p, text, mode=0o644)
    atomic_write_text(hub, plan.hub_text, mode=stat.S_IMODE(hub.stat().st_mode))
    return [name, *(p for p, _ in plan.volumes)]


def commit_message(name: str, plan: RolloverPlan) -> str:
    return f"rollover: {name} \u2192 {', '.join(p for p, _ in plan.volumes)}"


def _commit(wiki: Path, paths: list[str], message: str) -> None:
    """Commit exactly ``paths``, as `steps.commit_paths` does: nothing else in the corpus is
    staged or committed."""
    for args in (
        ("add", "-A", "--", *paths),
        ("commit", "-q", "--only", "-m", message, "--", *paths),
    ):
        r = subprocess.run(
            ["git", *GIT_ID, *args], cwd=wiki, capture_output=True, text=True, check=False
        )
        if r.returncode != 0:
            raise RuntimeError(f"git {args[0]} in {wiki}: {r.stderr.strip()}")


# ---------------------------------------------------------------- the guard


def check_conservation(
    before_text: str,
    plan: RolloverPlan,
    keep: frozenset[str] | set[str] = frozenset(),
    kept: frozenset[str] | set[str] = frozenset(),
) -> None:
    """Raise ConservationError unless ``plan`` only moves text out of ``before_text``.

    - Every `[sN: "..."]` and `[[link]]` that left the hub is in a volume.
    - The non-blank lines that left the hub's body (whitespace-normalized, as a multiset) are
      exactly the lines of the volumes' sections, headings aside; a heading that left the
      hub is in a volume.
    - The hub gained no line but `## History` and the items of these volumes, and it lists
      every volume.
    - Its frontmatter changed in `sources` only.
    - Order: under each heading, the lines left on the hub, and those in each volume, are in
      the order they had on the hub before (`_in_order`).
    - Each volume holds only entries dated within the range the hub's History gives it; the
      plan's volumes come oldest first and their ranges do not overlap; History lists every
      volume newest first.

    ``keep`` and ``kept``: as `plan_rollover` parsed the page, so each entry's date here is
    the one the plan moved it by.
    """
    before, after = parse_page(before_text, keep, kept), parse_page(plan.hub_text)
    old = before_text[len(before.frontmatter) :]
    new = plan.hub_text[len(after.frontmatter) :]
    moved = "".join(s.text for _, t in plan.volumes for s in parse_page(t).sections if s.heading)
    for what, rx in (("citation", CITE_RE), ("link", WIKILINK_RE)):
        lost = _found(rx, old) - _found(rx, new) - _found(rx, moved)
        if lost:
            raise ConservationError(f"a {what} left the hub and is in no volume: {min(lost)}")
    paths = {path for path, _ in plan.volumes}
    for line in _lines(new) - _lines(old):
        m = HISTORY_ITEM_RE.match(line)
        if line != HISTORY_HEADING and not (m and m.group(4) in paths):
            raise ConservationError(f"the hub gained a line: {line[:120]}")
    left, vol = _lines(old) - _lines(new), _lines(moved)
    left_heads, vol_heads = _headings(left), _headings(vol)
    if lost := (left - left_heads) - (vol - vol_heads):
        raise ConservationError(f"a line left the hub and is in no volume: {min(lost)[:120]}")
    if extra := (vol - vol_heads) - (left - left_heads):
        raise ConservationError(f"a volume holds a line the hub still has: {min(extra)[:120]}")
    if lost := left_heads - vol_heads:
        raise ConservationError(f"a heading left the hub and is in no volume: {min(lost)}")
    listed = {m.group(4) for line in _lines(new) if (m := HISTORY_ITEM_RE.match(line))}
    if unlisted := paths - listed:
        raise ConservationError(f"a volume is not in the hub's History: {min(unlisted)}")
    fm_old, fm_new = split_frontmatter(before_text)[0], split_frontmatter(plan.hub_text)[0]
    if fm_old is None:
        same = before.frontmatter == after.frontmatter
    else:
        same = fm_new is not None and _but_sources(fm_old) == _but_sources(fm_new)
    if not same:
        raise ConservationError("the hub's frontmatter changed beyond `sources`")
    _check_order(before, after, plan)


def _check_order(before: PageParts, after: PageParts, plan: RolloverPlan) -> None:
    """The order half of `check_conservation`, on whole entries: under each heading, what
    stays on the hub and what each volume holds are the hub's own entries - each whole, with
    its section, position and date - in the hub's order; and a volume holds only entries
    dated within the range its History item gives it."""
    hub = [s for s in before.sections if s.kind != HISTORY]
    blocks = [_blocks(s) for s in hub]
    _in_order("the hub", [s for s in after.sections if s.kind != HISTORY], hub, blocks)
    items = [
        m
        for s in after.sections
        if s.kind == HISTORY
        for line in s.lead.splitlines()
        if (m := HISTORY_ITEM_RE.match(normalize_ws(line)))
    ]
    keys = [(m.group(2), m.group(1)) for m in items]
    if keys != sorted(keys, reverse=True):
        raise ConservationError("the hub's History does not list its volumes newest first")
    span = {m.group(4): (m.group(1), m.group(2)) for m in items}
    prev = None
    for path, text in plan.volumes:
        first, last = span[path]
        if first > last or (prev is not None and prev[2] >= first):
            shown = prev[0] if prev is not None else path
            raise ConservationError(
                f"the volumes' date ranges overlap or are out of order: {shown}, {path}"
            )
        sections = [s for s in parse_page(text).sections if s.heading]
        _in_order(path, sections, hub, blocks, (first, last))
        prev = (path, first, last)


# A unit roll-over moves whole: (its index in its section, -1 for the section's lead; its
# date; its non-blank lines, whitespace collapsed).
Block = tuple[int, str | None, list[str]]


def _blocks(s: Section) -> list[Block]:
    """Section ``s`` as the units roll-over moves whole: its lead, then each entry."""
    lead = [normalize_ws(ln) for ln in s.lead.splitlines() if ln.strip()]
    out: list[Block] = [(-1, None, lead)] if lead else []
    for i, e in enumerate(s.entries):
        out.append((i, e.date, [normalize_ws(ln) for ln in e.text.splitlines() if ln.strip()]))
    return out


def _within(b: Block, span: tuple[str, str] | None) -> bool:
    """Whether block ``b`` may be in a volume covering ``span`` (any block may, without one):
    a lead may; an entry, when it is dated within it."""
    return span is None or b[0] < 0 or (b[1] is not None and span[0] <= b[1] <= span[1])


def _whole(
    lines: list[str], blocks: list[Block], span: tuple[str, str] | None = None
) -> list[Block] | None:
    """The blocks that make up ``lines`` exactly - each whole, in their order, each `_within`
    ``span`` - or None when no choice of them does. Blocks are compared whole, so two that
    share a line (one marker opening two entries) are never taken for each other, and where
    one block's lines begin another's, every reading of ``lines`` is tried."""
    via: dict[int, tuple[int, int] | None] = {0: None}  # position -> (position before, block)
    for b, block in enumerate(blocks):
        n = len(block[2])
        if not n or not _within(block, span):
            continue
        for p in sorted(via):  # reached by earlier blocks only: each block is used once
            q = p + n
            if q not in via and lines[p:q] == block[2]:
                via[q] = (p, b)
    if len(lines) not in via:
        return None
    out, p = [], len(lines)
    while (step := via[p]) is not None:
        p, b = step
        out.append(blocks[b])
    return out[::-1]


def _in_order(
    where: str,
    sections: list[Section],
    hub: list[Section],
    blocks: list[list[Block]],
    span: tuple[str, str] | None = None,
) -> None:
    """Match each of ``sections`` to the hub section, in the hub's order and under the same
    heading, whose whole blocks (``blocks``, `_whole`) make up its lines - given ``span``, a
    volume's range, only blocks `_within` it. Raises ConservationError, naming ``where``,
    for a section with no such match: for an entry outside the range when only one would
    make it up, else for the order."""
    j = 0
    for s in sections:
        key, mine = normalize_ws(s.heading), [ln for b in _blocks(s) for ln in b[2]]
        ks = [k for k in range(j, len(hub)) if normalize_ws(hub[k].heading) == key]
        k = next((k for k in ks if _whole(mine, blocks[k], span) is not None), None)
        if k is None:
            loose = next((got for k in ks if (got := _whole(mine, blocks[k])) is not None), [])
            out = next((b for b in loose if not _within(b, span)), None)
            if span is not None and out is not None:
                raise ConservationError(
                    f"{where}: an entry dated {out[1]} is outside its range, {span[0]} to {span[1]}"
                )
            shown = s.heading.strip()
            raise ConservationError(
                f"{where}: the lines under {shown!r} are out of the hub's order"
            )
        j = k + 1


def _found(rx: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(normalize_ws(m.group(0)) for m in rx.finditer(text))


def _lines(text: str) -> Counter[str]:
    return Counter(normalize_ws(line) for line in text.splitlines() if line.strip())


def _headings(lines: Counter[str]) -> Counter[str]:
    return Counter({ln: n for ln, n in lines.items() if ln.startswith(("## ", "# "))})


def _but_sources(fm: dict) -> dict:
    return {k: v for k, v in fm.items() if k != "sources"}
