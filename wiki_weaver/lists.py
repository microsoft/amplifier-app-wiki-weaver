"""The never-move lists of a page (design/plans/2026-11-10-short-lists-spec.md, sections
4-6): one AI step per history file, run once, after roll-over makes the file.

Code's part, all deterministic: which history files still need the step (their state in
``.wiki/lists-state.json``), the step's input, reading its output, putting the lists on the
page, and the checks that decide whether they go on it. The step itself is the `lists` box
node in pipeline/ingest.dot; `steps.step_lists_prep` and `steps.step_lists_apply` drive
this module. No list name is written here: they all come from the lens.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from . import checks as ck
from . import lens, rollover
from .lens import KeptSection, PageType
from .lib import atomic_write_text, contained_file, page_files
from .sections import pieces, unclosed_fence
from .sources import FRONTMATTER_RE, read_text, split_frontmatter

STATE_REL = ".wiki/lists-state.json"  # {history file: {status, attempts, reason, page}}
INPUT = "lists_input.md"  # in .wiki/work/: what the step reads
OUTPUT = "lists.md"  # in .wiki/work/: what it writes
MAX_ATTEMPTS = 3
DONE, FAILED, DEFERRED = "done", "failed", "deferred"
FIRST_DATE_RE = re.compile(r"--(\d{4}-\d{2}-\d{2})--")  # history/<page>--<first>--<last>.md


# ---------------------------------------------------------------- state


def read_state(wiki: Path) -> dict[str, dict]:
    """Each history file's list state: {status, attempts, reason, page}. Empty before the
    first. A file that is not JSON raises ValueError; one that holds no object, TypeError."""
    p = Path(wiki) / STATE_REL
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError(f"{STATE_REL} does not hold an object")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)}


def write_state(wiki: Path, state: dict[str, dict]) -> None:
    """Atomically: a reader, or a crash, never meets half a file."""
    text = json.dumps(state, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    atomic_write_text(Path(wiki) / STATE_REL, text)


def attempts(entry: dict | None) -> int:
    try:
        return int((entry or {}).get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def is_pending(entry: dict | None) -> bool:
    """A history file still waits for the step: it is not done and, if it failed, it has
    had fewer than MAX_ATTEMPTS attempts. A deferred file is pending."""
    status = (entry or {}).get("status")
    return status != DONE and not (status == FAILED and attempts(entry) >= MAX_ATTEMPTS)


def mark(state: dict[str, dict], vol: str, page: str, status: str, reason: str = "") -> None:
    """Record ``status`` for history file ``vol`` of ``page``. A step that ran - done or
    failed - is one more attempt; a deferral is none."""
    tries = attempts(state.get(vol)) + (status in (DONE, FAILED))
    state[vol] = {"status": status, "attempts": tries, "reason": reason, "page": page}


# ---------------------------------------------------------------- which files


def load(wiki: Path, name: str, types: dict[str, PageType]) -> tuple[str, PageType] | None:
    """Root page ``name``'s text and lens type, when the page exists, is no source summary,
    and its type keeps sections; else None."""
    path = contained_file(wiki, name)
    if path is None or path.parent != Path(wiki).resolve() or name.startswith("source-"):
        return None
    text = path.read_bytes().decode("utf-8", errors="replace")
    pt = lens.page_type_for(types, (split_frontmatter(text)[0] or {}).get("type"))
    return (text, pt) if pt is not None and pt.kept else None


def _first_date(vol: str) -> str:
    m = FIRST_DATE_RE.search(Path(vol).name)
    return m.group(1) if m else ""


def pending(
    wiki: Path, names: Iterable[str], types: dict[str, PageType], state: dict[str, dict]
) -> list[tuple[str, str]]:
    """(history file, page) for each pending file listed under `## History` by the pages
    ``names`` whose type keeps sections, oldest first by the first date in the file's name.
    A file listed but not in this job waits for one that carries it."""
    out = set()
    for name in names:
        got = load(wiki, name, types)
        for vol in rollover.listed_volumes(got[0]) if got else []:
            if is_pending(state.get(vol)) and contained_file(wiki, vol) is not None:
                out.add((vol, name))
    return sorted(out, key=lambda item: (_first_date(item[0]), item[0]))


def pending_count(wiki: Path) -> int:
    """History files the step has still to read, over the whole wiki: those listed by pages
    whose type keeps sections - no other page ever has them read - in this job or not."""
    types, state = rollover.page_types(wiki), read_state(wiki)
    n = 0
    for p in page_files(wiki):
        got = load(wiki, p.name, types)
        if got:
            n += sum(is_pending(state.get(v)) for v in rollover.listed_volumes(got[0]))
    return n


# ---------------------------------------------------------------- the step's input


def limit_for(pt: PageType) -> int:
    """Half the type's roll-over budget: the soft ceiling on its kept sections together."""
    return rollover.budget_for(lens.type_key(pt.name)) // 2


def _rule(k: KeptSection) -> str:
    rule = (
        "Keep current: a line may be replaced when its fact changes"
        if k.current
        else "Keep: a line is never dropped, except by condensing"
    )
    return rule + (f"; covers: {k.covers}" if k.covers else "")


def render_input(name: str, pt: PageType, text: str, vol: str, vol_text: str) -> str:
    """What the step reads: the page and its type, what one line of each kept section holds
    (the lens's own words), the kept sections as they stand, the whole history file, and
    what to write."""
    keys = {ck._name_key(k.name) for k in pt.kept}
    on_page = [s for s in pieces(split_frontmatter(text)[1])[1:] if ck.piece_key(s) in keys]
    have = {ck.piece_key(s) for s in on_page}
    missing = [k.name for k in pt.kept if ck._name_key(k.name) not in have]
    size = ck.kept_size(text, [k.name for k in pt.kept])
    shown = "".join(s if s.endswith("\n") else s + "\n" for s in on_page) or "(none)\n"
    out = [
        f"# Never-move lists of {name}\n\n",
        f"Page: {name} (type: {pt.name})\n",
        f"History file: {vol}\n\n",
        f"## What one line of each kept section holds (the lens, for {pt.name})\n\n",
        *(f"- {k.name} ({_rule(k)}): {k.description or '(no description)'}\n" for k in pt.kept),
        "\n## The page's kept sections now, verbatim\n\n",
        "----- KEPT SECTIONS BEGIN -----\n",
        shown,
        "----- KEPT SECTIONS END -----\n",
        f"Kept sections not on the page yet: {', '.join(missing)}.\n" if missing else "",
        f"\n## The history file, in full: {vol}\n\n",
        "----- HISTORY FILE BEGINS -----\n",
        vol_text if vol_text.endswith("\n") else vol_text + "\n",
        "----- HISTORY FILE ENDS -----\n",
        "\n## Output\n\n",
        (
            "Write .wiki/work/lists.md: the page's complete updated kept sections - these "
            f"{len(pt.kept)} sections, in this order, each even if it has no lines - and "
            "nothing else:\n\n"
        ),
        *(f"## {k.name}\n" for k in pt.kept),
        (
            f"\nTogether they are {size:,} characters now; keep them within "
            f"{limit_for(pt):,} (half the page's budget). If they would be over that, "
            f"condense older settled items into one line that names {vol}.\n"
        ),
        f"End each line whose full entry lives in this history file with \u2192 {vol}.\n",
        *(
            f"{k.name} covers the page's {k.covers} section: each entry this file holds "
            f"under ## {k.covers} needs a line in {k.name} that cites one of its quotes, "
            f"unless {k.name} names {vol}.\n"
            for k in pt.kept
            if k.covers
        ),
    ]
    return "".join(out)


# ---------------------------------------------------------------- the step's output


def parse_output(text: str, kept: list[KeptSection]) -> tuple[dict[str, list[str]], list[str]]:
    """lists.md as {kept section name: its non-blank lines, right-stripped}, and what is
    wrong with it: a heading that is not a `## ` kept section, a section twice or missing,
    or text outside the sections. Sections are cut as `sections.pieces` cuts them: a
    heading inside a code fence is a line of its section, which the shape check refuses."""
    names = {ck._name_key(k.name): k.name for k in kept}
    out: dict[str, list[str]] = {}
    intro, *headed = pieces(text)
    problems = [
        f"lists.md: text outside the kept sections: {ln.strip()[:80]!r}"
        for ln in intro.splitlines()
        if ln.strip()
    ]
    for piece in headed:
        key = ck.piece_key(piece)
        name = names.get(key) if key is not None else None
        if name is None or name in out:
            heading = piece.split("\n", 1)[0].strip()
            why = "is not a section the lens keeps" if name is None else "appears twice"
            problems.append(f"lists.md: {heading[:80]!r} {why}")
            continue
        out[name] = [ln.rstrip() for ln in piece.splitlines()[1:] if ln.strip()]
    problems += [f"lists.md: missing '## {k.name}'" for k in kept if k.name not in out]
    return out, problems


class _Block(str):
    """A kept section this module wrote: it gets its blank line after it at the end."""


def _block(heading: str, lines: list[str]) -> _Block:
    return _Block(heading + "\n" + ("\n" + "".join(f"{ln}\n" for ln in lines) if lines else ""))


def with_sections(text: str, sections: dict[str, list[str]], kept: list[KeptSection]) -> str:
    """Page ``text`` with its kept sections set to ``sections`` ({name: lines}), sections cut
    as `sections.pieces` cuts them (outside code fences). A section on the page is replaced
    where it stands, its heading as written; a second copy of it goes. One not on the page
    goes after the kept section before it in lens order, or else after `## Current state`,
    or else after the frontmatter and the intro - unless it has no lines. A name of ``kept``
    not in ``sections`` is left as it is. Nothing outside the kept sections changes, not
    even a blank line."""
    m = FRONTMATTER_RE.match(text)
    head = text[: m.end()] if m else ""
    segs: list[str] = pieces(text[len(head) :])
    state = next(
        (i for i, s in enumerate(segs) if i and ck.CURRENT_STATE_RE.match(s.split("\n", 1)[0])),
        None,
    )
    first = next((i for i, s in enumerate(segs) if s.startswith("## ")), len(segs))
    anchor = state + 1 if state is not None else first
    for k in kept:
        if k.name not in sections:
            continue
        at = [i for i, s in enumerate(segs) if ck.piece_key(s) == ck._name_key(k.name)]
        if at:
            for i in reversed(at[1:]):
                del segs[i]
                anchor -= i < anchor
            segs[at[0]] = _block(segs[at[0]].split("\n", 1)[0].rstrip("\r"), sections[k.name])
            anchor = max(anchor, at[0] + 1)
        elif sections[k.name]:
            block = _block(f"## {k.name}", sections[k.name])
            if anchor and segs[anchor - 1] and not segs[anchor - 1].endswith("\n"):
                block = _Block("\n" + block)  # the page's last line had no end: it gets one
            segs.insert(anchor, block)
            anchor += 1
    last = len(segs) - 1
    return head + "".join(
        str(s) + "\n" if isinstance(s, _Block) and i < last else str(s) for i, s in enumerate(segs)
    )


def _pointers(lines: Iterable[str]) -> set[str]:
    """The `history/<file>.md` paths ``lines`` name, bare or as markdown links."""
    return {f"history/{f}" for ln in lines for f in ck.HISTORY_FILE_RE.findall(ln)}


def _unknown_pointers(
    name: str, before: str, now: dict[str, list[str]], names: list[str], wiki: Path
) -> list[str]:
    """A history file the kept sections newly point at must be one of the page's own: listed
    under its `## History`, and present under `history/`."""
    had = _pointers(ln for lines in ck.kept_sections(before, names).values() for ln in lines)
    listed = set(rollover.listed_volumes(before))
    return [
        f"{name}: unknown history pointer: {p}"
        for p in sorted(_pointers(ln for lines in now.values() for ln in lines) - had)
        if p not in listed or contained_file(wiki, p) is None
    ]


def _cut(text: str, names: list[str]) -> tuple[str, str]:
    """Page ``text`` as (its frontmatter, its body without the kept sections ``names``)."""
    m = FRONTMATTER_RE.match(text)
    head = text[: m.end()] if m else ""
    return head, ck.without_kept(text[len(head) :], names)


def _sources_added(head: str, new_head: str) -> bool:
    """Frontmatter ``new_head`` is ``head`` with only `sources` added at the end of its list."""
    old, new = split_frontmatter(head)[0], split_frontmatter(new_head)[0]
    if old is None or new is None:
        return False
    was, now = list(old.get("sources") or []), list(new.get("sources") or [])
    rest = {k: v for k, v in old.items() if k != "sources"}
    return rest == {k: v for k, v in new.items() if k != "sources"} and now[: len(was)] == was


def changed_outside(name: str, before: str, after: str, names: list[str]) -> list[str]:
    """The invariant every list write keeps, checked on its own: the page outside its kept
    sections ``names`` is byte for byte as it was - but for the one line end a list added at
    the end of a page needs when the page's last line had none - and its frontmatter only
    gains sources."""
    (head, body), (new_head, new_body) = _cut(before, names), _cut(after, names)
    same = body == new_body or (not body.endswith("\n") and new_body == body + "\n")
    if same and (head == new_head or _sources_added(head, new_head)):
        return []
    return [f"{name}: the lists would change the page outside its kept sections"]


def check(
    name: str,
    before: str,
    after: str,
    pt: PageType,
    vol: str,
    vol_text: str,
    wiki: Path,
    sources: dict[int, tuple[str, str]],
    known: dict[int, str],
) -> list[str]:
    """What keeps ``after`` - page ``name`` with the step's lists on it - off the page: the
    kept-section checks a write gets (line shape; citations, a quote already on the page or
    in the history file standing on its known id; no loss but by condensing), history
    pointers to files that are not the page's, and coverage of the entries that moved to
    ``vol`` from each covered section."""
    names = [k.name for k in pt.kept]
    now = ck.kept_sections(after, names)
    m = FRONTMATTER_RE.match(after)
    only = (after[: m.end()] if m else "") + "".join(
        f"## {n}\n" + "".join(f"{ln}\n" for ln in lines) for n, lines in now.items()
    )
    prior = before + "\n" + split_frontmatter(vol_text)[1]
    problems = ck.check_kept_shape(name, after, pt.kept)
    problems += _unknown_pointers(name, before, now, names, wiki)
    problems += ck.check_citations(name, only, sources, prior, known)
    problems += ck.check_kept_loss(name, before, after, pt.kept, wiki)
    for k in (k for k in pt.kept if k.covers):
        moved = rollover.volume_entries(vol_text, k.covers or "", pt.sections)
        listed = "\n".join(now.get(k.name, []))
        problems += [
            f"{name}: kept list '{k.name}' does not cover {p}"
            for p in ck.coverage_problems({vol: moved}, listed)
        ]
    return problems


def apply(
    wiki: Path,
    name: str,
    vol: str,
    output: str,
    types: dict[str, PageType],
    sources: dict[int, tuple[str, str]],
    known: dict[int, str],
) -> tuple[str, list[str]]:
    """Page ``name`` with the lists in ``output`` (lists.md) on it - the sources its new
    lines cite added to its frontmatter `sources:` - and what keeps it off the page (none:
    it may be written). The page is read as UTF-8 bytes, so it is written back byte for
    byte but for the lists; a page whose code fence never closes is refused, its sections
    being past telling apart, and so is any result that `changed_outside` the lists."""
    path = contained_file(wiki, name)
    vol_path = contained_file(wiki, vol)
    if path is None or vol_path is None:
        raise FileNotFoundError(f"{name if path is None else vol} is not in the corpus")
    before = path.read_bytes().decode("utf-8")
    pt = lens.page_type_for(types, (split_frontmatter(before)[0] or {}).get("type"))
    if pt is None or not pt.kept:
        return before, [f"{name}: its page type keeps no sections"]
    if (line := unclosed_fence(split_frontmatter(before)[1])) is not None:
        return before, [f"{name}: the code fence at body line {line + 1} never closes"]
    sections, problems = parse_output(output, pt.kept)
    if problems:
        return before, problems
    after = with_sections(before, sections, pt.kept)
    ids = {int(c) for lines in sections.values() for ln in lines for c, _ in ck.CITE_RE.findall(ln)}
    after = rollover.add_sources(after, [known[i] for i in sorted(ids) if i in known])
    if problems := changed_outside(name, before, after, [k.name for k in pt.kept]):
        return before, problems
    vol_text = read_text(vol_path)
    return after, check(name, before, after, pt, vol, vol_text, wiki, sources, known)
