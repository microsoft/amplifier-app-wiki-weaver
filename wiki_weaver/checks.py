"""Deterministic per-write checks. Every check can fail the step; none calls a model.

Citation convention (what the writer is told, and what this enforces):
    [s<source_id>: "<five or more words copied verbatim from that source version>"]
The id is the ledger's hash-based ``source_id``: it names one exact version of a source,
so a quote from a since-edited version still resolves (see ledger.source_versions).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote

from .lib import contained_file
from .sections import fenced, pieces
from .sources import normalize_ws, read_text, split_frontmatter

if TYPE_CHECKING:
    from .lens import KeptSection

CITE_RE = re.compile(r'\[s(\d+): "([^"\n]+)"\]')
BARE_CITE_RE = re.compile(r"\[s(\d+)\](?!\()")
CURRENT_STATE_RE = re.compile(r"^##\s+current state\b", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[[^\]\n]*\]\((<[^>\n]+>|[^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK_RE = re.compile(r"\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]")
MARKER_RE = re.compile(r"<!--\s*superseded:\s*\d{4}-\d{2}-\d{2}\s*-->")
CLOSE_RE = re.compile(r"<!--\s*/superseded\s*-->")
HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*#*\s*$")
REQUIRED_FM = ("title", "type", "sources", "last_updated")
LOSS_THRESHOLD = 0.15


def _name_key(heading: str) -> str:
    """A heading, or a kept section's lens name, as the two are compared: superseded markers
    dropped, `#`s and whitespace stripped, case folded."""
    return normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", heading))).lstrip("#").strip().lower()


def _heading_line(piece: str) -> str:
    """The first line of a section piece (`sections.pieces`): its heading, line end dropped."""
    return piece.split("\n", 1)[0].rstrip("\r")


def is_current_state(piece: str) -> bool:
    """A section piece (`sections.pieces`) headed `## Current state`."""
    return bool(CURRENT_STATE_RE.match(_heading_line(piece)))


def _without_current_state(body: str, kept: Iterable[str] = ()) -> str:
    """Drop the `## Current state` section (heading included): the one section the
    writer may rewrite, so it is outside the content-loss guard. The sections named in
    ``kept`` (the lens's `Keep:` lines) go too: the kept-list checks guard them instead.
    Sections are cut as `sections.pieces` cuts them: a heading inside a code fence is text."""
    return "".join(p for p in pieces(without_kept(body, kept)) if not is_current_state(p))


def _body_lines(text: str, kept: Iterable[str] = ()) -> list[str]:
    _, body = split_frontmatter(text)
    out = []
    for line in _without_current_state(body, kept).splitlines():
        s = normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", line)))
        if s:
            out.append(s)
    return out


def _headings(
    text: str, level: int | None = None, skip_current: bool = False, kept: Iterable[str] = ()
) -> list[str]:
    """The headings of ``text`` - of ``level`` if given - lowercased; a line inside a code
    fence (`sections.fenced`) is text, never a heading."""
    _, body = split_frontmatter(text)
    if skip_current:
        body = _without_current_state(body, kept)
    lines = body.splitlines()
    hs = []
    for line, code in zip(lines, fenced(lines)[0], strict=True):
        m = None if code else HEADING_RE.match(CLOSE_RE.sub("", MARKER_RE.sub("", line)).strip())
        if m and (level is None or len(m.group(1)) == level):
            hs.append(normalize_ws(m.group(2)).lower())
    return hs


def check_frontmatter(name: str, text: str) -> list[str]:
    fm, _ = split_frontmatter(text)
    if fm is None:
        return [f"{name}: missing or unparseable frontmatter"]
    errs = [f"{name}: frontmatter missing '{k}'" for k in REQUIRED_FM if not fm.get(k)]
    srcs = fm.get("sources")
    if srcs is not None and not isinstance(srcs, list):
        errs.append(f"{name}: frontmatter 'sources' must be a list")
    return errs


def check_duplicate_headings(name: str, text: str) -> list[str]:
    """No `## ` heading twice. Headings are the sections `sections.pieces` cuts (column 0,
    outside code fences): a `## ` line inside a fence is text."""
    keys = [
        normalize_ws(m.group(2)).lower()
        for p in pieces(split_frontmatter(text)[1])
        if p.startswith("## ")
        and (m := HEADING_RE.match(CLOSE_RE.sub("", MARKER_RE.sub("", _heading_line(p))).strip()))
    ]
    dups = [h for h, n in Counter(keys).items() if n > 1]
    return [f"{name}: duplicate ## heading '{h}'" for h in dups]


def citations_in(text: str) -> set[tuple[int, str]]:
    """(source_id, whitespace-normalized quote) for every quoted citation in ``text``."""
    _, body = split_frontmatter(text)
    return {(int(m.group(1)), normalize_ws(m.group(2))) for m in CITE_RE.finditer(body)}


def check_citations(
    name: str,
    text: str,
    sources: dict[int, tuple[str, str]],
    prior: str | None = None,
    known: dict[int, str] | None = None,
) -> list[str]:
    """Every quote this write added or changed is >=5 words and an exact (whitespace-
    normalized) substring of the source version its id names; every cited source is known
    and listed in the page's `sources:`.

    ``sources`` maps source_id -> (filename, text of that version). ``prior`` is the page as
    committed before this write (None for a new page, or for `lint`): a quote already on it
    - same id, same words - was verified when it was first written, so it needs only a
    known id, one in ``sources`` or in ``known`` (source_id -> filename, from the ledger).
    A job that carries the pages it needs but not every source they quote (Team Pulse's
    shape) can then write to them; a quote from a source whose text is not here is an
    error only when this write added or changed it.
    """
    errs: list[str] = []
    fm, body = split_frontmatter(text)
    listed = {str(s) for s in ((fm or {}).get("sources") or []) if isinstance(s, str)}
    known = known or {}
    kept = citations_in(prior) if prior is not None else set()
    norm_cache: dict[int, str] = {}
    for m in CITE_RE.finditer(body):
        sid, quote = int(m.group(1)), m.group(2)
        if sid in sources:
            fname, stext = sources[sid]
        elif sid in known:
            fname, stext = known[sid], None
        else:
            errs.append(f"{name}: cites unknown source [s{sid}]")
            continue
        if (sid, normalize_ws(quote)) not in kept:  # added or changed by this write
            if len(quote.split()) < 5:
                errs.append(f'{name}: quote under 5 words: [s{sid}: "{quote}"]')
                continue
            if stext is None:
                errs.append(
                    f"{name}: quote from s{sid} ({fname}) not verifiable - that source's text "
                    f'is not in this job; cite only the current source for new claims: "{quote[:80]}"'
                )
            elif normalize_ws(quote) not in norm_cache.setdefault(sid, normalize_ws(stext)):
                errs.append(f'{name}: quote not found verbatim in s{sid} ({fname}): "{quote[:80]}"')
        if listed and fname not in listed:
            errs.append(f"{name}: cites s{sid} ({fname}) but it is not in frontmatter sources")
    for m in MD_LINK_RE.finditer(body):
        target = m.group(1).strip("<>").split("#")[0]
        if re.fullmatch(r"(\./)?source-[^/]*\.md", target):
            errs.append(f'{name}: links to a source page ({target}); cite as [s<id>: "quote"] only')
    for m in BARE_CITE_RE.finditer(body):
        if int(m.group(1)) in sources or int(m.group(1)) in known:
            errs.append(f"{name}: citation without a quote: [s{m.group(1)}]")
        else:
            errs.append(f"{name}: cites unknown source [s{m.group(1)}]")
    return errs


def superseded_spans(body: str) -> list[tuple[int, int]]:
    """Character spans of superseded text: `<!-- superseded: DATE -->` up to the next
    `<!-- /superseded -->`; a marker with no closing tag (the legacy line form) runs to the
    end of its line."""
    spans = []
    pos = 0
    while (m := MARKER_RE.search(body, pos)) is not None:
        close = CLOSE_RE.search(body, m.end())
        nxt = MARKER_RE.search(body, m.end())
        if close and (nxt is None or close.start() < nxt.start()):
            end = close.end()
        else:
            eol = body.find("\n", m.end())
            end = len(body) if eol < 0 else eol
        spans.append((m.start(), end))
        pos = end
    return spans


def check_superseded_blocks(name: str, text: str, current_id: int | None) -> list[str]:
    """A superseded block holds only text that is no longer true, so it may not cite the
    source being ingested now: the replacement goes outside the block."""
    if current_id is None:
        return []
    _, body = split_frontmatter(text)
    for a, b in superseded_spans(body):
        span = body[a:b]
        ids = {int(m.group(1)) for m in CITE_RE.finditer(span)}
        ids |= {int(m.group(1)) for m in BARE_CITE_RE.finditer(span)}
        if current_id in ids:
            snippet = normalize_ws(span)[:100]
            msg = (
                f"{name}: superseded block cites the current source s{current_id}; the marker "
                f"wraps only the text that is no longer true: {snippet}"
            )
            return [msg]
    return []


def links_in(text: str) -> set[str]:
    """Every link in ``text``'s body as written (label and target), whitespace-normalized:
    markdown links and wikilinks alike."""
    _, body = split_frontmatter(text)
    out = {normalize_ws(m.group(0)) for m in MD_LINK_RE.finditer(body)}
    return out | {normalize_ws(m.group(0)) for m in WIKILINK_RE.finditer(body)}


def check_links(name: str, text: str, wiki: Path, prior: str | None = None) -> list[str]:
    """Every link this write added or changed resolves to a file in the corpus - one the
    job carries or one this write created. A link already on the committed page
    (``prior``; same label, same target) stands even when its target is not in the job: it
    resolved when it was written, and a job that carries only the pages it needs cannot
    resolve every link they already hold. ``prior`` is None for a new page, or for `lint`,
    which checks every link. A changed label or target is a new link."""
    errs = []
    _, body = split_frontmatter(text)
    kept = links_in(prior) if prior is not None else set()
    for m in MD_LINK_RE.finditer(body):
        if normalize_ws(m.group(0)) in kept:
            continue
        target = m.group(1).strip("<>")
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
            continue
        path = unquote(target.split("#", 1)[0])
        if path and not (wiki / path).exists():
            errs.append(f"{name}: broken link -> {target}")
    for m in WIKILINK_RE.finditer(body):
        if normalize_ws(m.group(0)) in kept:
            continue
        slug = m.group(1).strip()
        cand = slug if slug.endswith(".md") else f"{slug}.md"
        if not (wiki / cand).exists():
            errs.append(f"{name}: broken wikilink [[{slug}]]")
    return errs


CURRENT_STATE_FORMAT = re.compile(r"^## Current state \(as of \d{4}-\d{2}-\d{2}\)$")


def check_current_state(name: str, text: str) -> list[str]:
    """A page that has a `## Current state` section: the heading reads exactly
    `## Current state (as of YYYY-MM-DD)` and it is the first `##` section, sections cut as
    `sections.pieces` cuts them. Source summary pages are exempt."""
    if name.startswith("source-"):
        return []
    _, body = split_frontmatter(text)
    h2 = [_heading_line(p).strip() for p in pieces(body) if p.startswith("## ")]
    cs = [h for h in h2 if CURRENT_STATE_RE.match(h)]
    if not cs:
        return []
    errs = []
    for h in cs:
        if not CURRENT_STATE_FORMAT.match(h):
            errs.append(
                f"{name}: current state heading must read '## Current state (as of YYYY-MM-DD)', got '{h}'"
            )
    if not CURRENT_STATE_RE.match(h2[0]):
        errs.append(f"{name}: current state is not the first ## section (first is '{h2[0]}')")
    return errs


def current_state_chars(text: str) -> int:
    """Characters of a page's Current state, heading aside. Reported for each page a writer
    writes (result.json `current_state_sizes`), never limited: a limit held sources on pages
    already over it, and the never-move lists hold the durable facts (docs/DECISIONS.md)."""
    from .rollover import STATE, parse_page

    return sum(len(s.lead.strip()) for s in parse_page(text).sections if s.kind == STATE)


def check_history_unchanged(name: str, before: str | None, after: str) -> list[str]:
    """`## History` is code's: a write leaves it as it was (blank lines and spacing aside),
    and adds none to a page that had none (``before`` None: a new page)."""
    from .rollover import HISTORY, parse_page

    def history(text: str | None) -> list[str]:
        sections = parse_page(text).sections if text is not None else []
        return [
            normalize_ws(ln)
            for s in sections
            if s.kind == HISTORY
            for ln in s.text.splitlines()
            if ln.strip()
        ]

    return (
        [f"{name}: History section is maintained by code"]
        if history(after) != history(before)
        else []
    )


def _h2(text: str) -> list[str]:
    """The `## ` section headings of ``text``, as `sections.pieces` cuts them (column 0,
    outside code fences): superseded markers dropped, whitespace collapsed."""
    return [
        normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", _heading_line(p)[3:]))).strip()
        for p in pieces(split_frontmatter(text)[1])
        if p.startswith("## ")
    ]


def check_section_headings(name: str, before: str | None, after: str, types: dict) -> list[str]:
    """A `##` the write added must be one the lens names for the page's type - one of its
    `Sections`, or matching its `Sections pattern`. `## Current state` is allowed unless
    the type opts out. Source pages are exempt; a type with no set or pattern has no rule."""
    if name.startswith("source-"):
        return []
    from .lens import page_type_for

    fm, _ = split_frontmatter(after)
    pt = page_type_for(types, (fm or {}).get("type"))
    if pt is None:
        return []
    old = {h.lower() for h in _h2(before)} if before is not None else set()
    errs = []
    for h in _h2(after):
        if h.lower() in old:
            continue
        if CURRENT_STATE_RE.match(f"## {h}"):
            if not pt.current_state:
                errs.append(f"{name}: page type '{pt.name}' has no Current state; added '## {h}'")
            continue
        if pt.heading_rule() and not pt.allows(h):
            rule = pt.sections if pt.sections else [f"'{pt.pattern}'"]
            allowed = " · ".join([*rule, *(k.name for k in pt.kept)])
            errs.append(
                f"{name}: added '## {h}', not a section the lens names for '{pt.name}' ({allowed})"
            )
    return errs


def _heading_marked_nearby(h: str, after: str) -> bool:
    """A removed heading passes only if its text survives on a line that carries a
    superseded marker or sits next to one."""
    _, body = split_frontmatter(after)
    lines = body.splitlines()
    marked = [bool(MARKER_RE.search(ln) or CLOSE_RE.search(ln)) for ln in lines]
    for i, ln in enumerate(lines):
        text = normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", ln))).lstrip("#").strip().lower()
        if h and h in text and any(marked[j] for j in range(max(0, i - 1), min(len(lines), i + 2))):
            return True
    return False


def check_content_loss(name: str, before: str, after: str, kept: Iterable[str] = ()) -> list[str]:
    """A rewritten page may not drop >15% of its lines (superseded text stays on the page,
    so marked lines are not deletions), nor a heading unless a superseded marker sits on or
    next to the line that keeps its text. `## Current state` is outside both rules, and so
    are the sections named in ``kept``: `check_kept_loss` replaces them there."""
    kept = list(kept)
    b, a = _body_lines(before, kept), _body_lines(after, kept)
    if not b:
        return []
    lost = sum((Counter(b) - Counter(a)).values())
    frac = lost / len(b)
    lost_heads = sorted(
        set(_headings(before, skip_current=True, kept=kept))
        - set(_headings(after, skip_current=True, kept=kept))
    )
    errs = []
    if frac > LOSS_THRESHOLD:
        errs.append(f"{name}: lost {lost}/{len(b)} lines ({frac:.0%}); superseded text must stay")
    for h in lost_heads:
        if not _heading_marked_nearby(h, after):
            errs.append(f"{name}: heading '{h}' removed without an adjacent superseded marker")
    return errs


# Kept sections: the `Keep:` / `Keep current:` lines of a page type (lens.KeptSection).
KEPT_LINE_MAX = 300  # characters in one line of a kept section
HISTORY_FILE_RE = re.compile(r"history/([^\s/()\[\]<>\"'`]+\.md)")
LEAD_DATE_RE = re.compile(r"(\d{4}-\d{2}(?:-\d{2})?)\s*(?:\u00b7\s*)?")


def piece_key(piece: str) -> str | None:
    """The name key of a section piece (`sections.pieces`) headed `## `; None for any other
    piece."""
    return _name_key(piece.split("\n", 1)[0]) if piece.startswith("## ") else None


def kept_sections(text: str, names: Iterable[str]) -> dict[str, list[str]]:
    """The body lines of each kept section on the page, by the name the lens gives it: from
    its `## ` heading to the next section, as `sections.pieces` cuts them (column 0, outside
    code fences). A name with no section on the page is absent."""
    by_key = {_name_key(n): n for n in names}
    out: dict[str, list[str]] = {}
    for piece in pieces(split_frontmatter(text)[1]):
        key = piece_key(piece)
        if key is not None and key in by_key:
            out.setdefault(by_key[key], []).extend(piece.splitlines()[1:])
    return out


def without_kept(body: str, names: Iterable[str]) -> str:
    """Page body ``body`` with the kept sections ``names`` cut out, as `kept_sections` finds
    them: the part of a page a list step may never change."""
    keys = {_name_key(n) for n in names}
    return "".join(p for p in pieces(body) if piece_key(p) not in keys)


def kept_size(text: str, kept_names: Iterable[str]) -> int:
    """Characters of the page's kept sections together, headings included. Soft: over half
    the page's budget is reported, never a finding."""
    keys = {_name_key(n) for n in kept_names}
    return sum(len(p) for p in pieces(split_frontmatter(text)[1]) if piece_key(p) in keys)


def check_kept_shape(name: str, text: str, kept: Iterable[KeptSection]) -> list[str]:
    """Every non-blank line of a kept section is a `- ` item of at most KEPT_LINE_MAX
    characters with at least one citation. Its quotes are verified like any other."""
    errs = []
    for sec, lines in kept_sections(text, [k.name for k in kept]).items():
        for line in (ln.rstrip() for ln in lines if ln.strip()):
            why = (
                "is not a '- ' item"
                if not line.startswith("- ")
                else f"is over {KEPT_LINE_MAX} characters"
                if len(line) > KEPT_LINE_MAX
                else "is without a citation"
                if not CITE_RE.search(line)
                else ""
            )
            if why:
                errs.append(f"{name}: kept list '{sec}': line {why}: {line[:80]!r}")
    return errs


# A history-file pointer on a kept line - `→ history/<file>.md` or a markdown link to
# one - with any parentheses around it: not part of the line's subject.
POINTER_RE = re.compile(
    r"\(?\s*(?:→\s*)?(?:\[[^\]\n]*\]\(history/[^)\s]+\.md\)|history/[^\s()\[\]]+\.md)\s*\)?"
)


def _line_key(line: str) -> tuple[str, str]:
    """What a status edit leaves alone: the leading date, if any, and the whole subject -
    the text before the first ` · `, citations and history pointers dropped, whitespace
    collapsed, case folded."""
    item = normalize_ws(line).removeprefix("- ")
    m = LEAD_DATE_RE.match(item)
    day, rest = (m.group(1), item[m.end() :]) if m else ("", item)
    rest = POINTER_RE.sub(" ", CITE_RE.sub(" ", rest))
    return day, normalize_ws(rest.split(" · ")[0]).casefold()


def _pair(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    """Pair a list's old and new lines one to one: an old line survives as the same line,
    else as a line with its `_line_key` - itself with its status edited. Returns the old
    lines left without a survivor and the new lines left unpaired: the ones this write
    added."""
    free = list(new)
    unpaired = []
    for ln in old:
        if ln in free:
            free.remove(ln)
        else:
            unpaired.append(ln)
    keys = [_line_key(ln) for ln in free]
    gone = []
    for ln in unpaired:
        key = _line_key(ln)
        if key in keys:
            i = keys.index(key)
            del keys[i], free[i]
        else:
            gone.append(ln)
    return gone, free


def _named_history(lines: Iterable[str], wiki: Path | None) -> list[str]:
    """The text, whitespace collapsed, of each history file ``lines`` name that exists under
    ``wiki``'s `history/`; none without a wiki."""
    if wiki is None:
        return []
    texts = []
    for f in sorted({f for ln in lines for f in HISTORY_FILE_RE.findall(ln)}):
        path = contained_file(wiki, f"history/{f}")
        if path is not None:
            texts.append(normalize_ws(read_text(path)))
    return texts


def _held(line: str, texts: list[str]) -> bool:
    """Each citation ``line`` makes - its id with its exact quote, whitespace collapsed - is
    in one of ``texts``. A line that cites nothing is never held."""
    cites = CITE_RE.findall(line)
    return bool(cites) and all(
        any(f'[s{n}: "{normalize_ws(q)}"]' in t for t in texts) for n, q in cites
    )


def check_kept_loss(
    name: str,
    before: str | None,
    after: str,
    kept: Iterable[KeptSection],
    wiki: Path | None = None,
) -> list[str]:
    """A `Keep:` list never loses a line. Old and new lines pair one to one (`_pair`): a line
    with the same date and whole subject is the old one with its status, reason or pointer
    edited. A line may leave only when lines this write adds to its list name history files
    that hold it - each of its citations, id and exact quote, is in one of those files under
    ``wiki``'s `history/` - as a condensing line does (`- 2026-05 to 2026-07 · 14 settled
    decisions on X → history/<file>.md`). A line that cites nothing, or a check without
    ``wiki``, has nothing to show it is held. `Keep current:` lists are exempt: their facts
    change."""
    if before is None:
        return []
    names = [k.name for k in kept if not k.current]
    old, new = kept_sections(before, names), kept_sections(after, names)
    errs = []
    for sec, lines in old.items():
        b = [normalize_ws(ln) for ln in lines if ln.strip()]
        a = [normalize_ws(ln) for ln in new.get(sec, []) if ln.strip()]
        gone, added = _pair(b, a)
        texts = _named_history(added, wiki) if gone else []
        lost = [ln for ln in gone if not _held(ln, texts)]
        if lost:
            errs.append(
                f"{name}: kept list '{sec}' lost {len(lost)} line(s) with no condensing line "
                f"naming a history/ file that holds them: {lost[0][:80]!r}"
            )
    return errs


def coverage_problems(moved_entries_by_file: dict[str, list[str]], list_text: str) -> list[str]:
    """Decision coverage (`covers: <Section>`): each entry that left the covered section for
    history file F must have one of its citations in the list, or the list must name F. One
    problem per entry with neither. ``moved_entries_by_file``: F (`history/<file>.md`, or
    its file name) -> the entries' texts."""
    have = {(int(n), normalize_ws(q)) for n, q in CITE_RE.findall(list_text)}
    out = []
    for f, entries in sorted(moved_entries_by_file.items()):
        if Path(f).name in list_text:
            continue
        for e in entries:
            if not {(int(n), normalize_ws(q)) for n, q in CITE_RE.findall(e)} & have:
                out.append(
                    f"{Path(f).name}: {normalize_ws(e)[:80]!r} has no citation in the list, "
                    "and the list does not name the file"
                )
    return out


def _kept_for(types: dict | None, text: str) -> list[KeptSection]:
    """The sections the page's type keeps, per the lens; none without a lens or a type."""
    if not types:
        return []
    from .lens import page_type_for

    pt = page_type_for(types, (split_frontmatter(text)[0] or {}).get("type"))
    return list(pt.kept) if pt else []


# Check names, for counting which check caused a failed write.
CHECK_KINDS = (
    ("with no condensing line", "kept_loss"),  # first: their messages quote page text
    ("kept list '", "kept_shape"),
    ("cites unknown source", "citations"),
    ("quote under 5 words", "short_quote"),
    ("not found verbatim", "citations"),
    ("not verifiable", "citations"),
    ("not in frontmatter sources", "citations"),
    ("links to a source page", "source_link"),
    ("citation without a quote", "citations"),
    ("broken link", "links"),
    ("broken wikilink", "links"),
    ("superseded text must stay", "content_loss"),
    ("without an adjacent superseded marker", "content_loss"),
    ("page deleted", "content_loss"),
    ("superseded block cites the current source", "superseded_current"),
    ("duplicate ## heading", "duplicate_headings"),
    ("current state heading must read", "current_state"),
    ("current state is not the first", "current_state"),
    ("History section is maintained by code", "history"),
    ("has no Current state; added", "sections"),
    ("not a section the lens names", "sections"),
    ("frontmatter", "frontmatter"),
    ("wrote outside the selected pages", "write_scope"),
    ("was not written", "summary_missing"),
)


def check_kind(finding: str) -> str:
    for needle, kind in CHECK_KINDS:
        if needle in finding:
            return kind
    return "other"


def run_page_checks(
    wiki: Path,
    pages: list[str],
    before_dir: Path,
    sources: dict[int, tuple[str, str]],
    current_id: int | None = None,
    types: dict | None = None,
    known: dict[int, str] | None = None,
) -> list[str]:
    """``before_dir`` holds each existing selected page as committed before this write
    (page_select copies it there; V4 commits per source, so that copy is HEAD's version).
    ``known`` is the ledger's source_id -> filename map; see check_citations."""
    errs: list[str] = []
    for name in pages:
        p = wiki / name
        prior = before_dir / name
        if not p.exists():
            if prior.exists():
                errs.append(f"{name}: page deleted")
            continue
        text = read_text(p)
        before = read_text(prior) if prior.exists() else None
        errs += check_frontmatter(name, text)
        errs += check_duplicate_headings(name, text)
        errs += check_citations(name, text, sources, before, known)
        errs += check_links(name, text, wiki, before)
        errs += check_current_state(name, text)
        errs += check_history_unchanged(name, before, text)
        errs += check_superseded_blocks(name, text, current_id)
        if types:
            errs += check_section_headings(
                name, read_text(prior) if prior.exists() else None, text, types
            )
        kept = _kept_for(types, text)
        if kept:
            errs += check_kept_shape(name, text, kept)
            errs += check_kept_loss(name, before, text, kept, wiki)
        if prior.exists():
            errs += check_content_loss(name, read_text(prior), text, [k.name for k in kept])
    return errs
