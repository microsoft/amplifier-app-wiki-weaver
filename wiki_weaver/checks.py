"""Deterministic per-write checks. Every check can fail the step; none calls a model.

Citation convention (what the writer is told, and what this enforces):
    [s<source_id>: "<five or more words copied verbatim from that source version>"]
The id is the ledger's hash-based ``source_id``: it names one exact version of a source,
so a quote from a since-edited version still resolves (see ledger.source_versions).
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from .sources import normalize_ws, read_text, split_frontmatter

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


def _without_current_state(body: str) -> str:
    """Drop the `## Current state` section (heading included): the one section the
    writer may rewrite, so it is outside the content-loss guard."""
    out, inside = [], False
    for line in body.splitlines():
        st = line.strip()
        if st.startswith(("## ", "# ")):
            inside = bool(CURRENT_STATE_RE.match(st))
        if not inside:
            out.append(line)
    return "\n".join(out)


def _body_lines(text: str) -> list[str]:
    _, body = split_frontmatter(text)
    out = []
    for line in _without_current_state(body).splitlines():
        s = normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", line)))
        if s:
            out.append(s)
    return out


def _headings(text: str, level: int | None = None, skip_current: bool = False) -> list[str]:
    _, body = split_frontmatter(text)
    if skip_current:
        body = _without_current_state(body)
    hs = []
    for line in body.splitlines():
        m = HEADING_RE.match(CLOSE_RE.sub("", MARKER_RE.sub("", line)).strip())
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
    dups = [h for h, n in Counter(_headings(text, level=2)).items() if n > 1]
    return [f"{name}: duplicate ## heading '{h}'" for h in dups]


def check_citations(name: str, text: str, sources: dict[int, tuple[str, str]]) -> list[str]:
    """Every quote is >=5 words and an exact (whitespace-normalized) substring of the source
    version its id names; every cited source is known and listed in the page's `sources:`.
    ``sources`` maps source_id -> (filename, text of that version)."""
    errs: list[str] = []
    fm, body = split_frontmatter(text)
    listed = {str(s) for s in ((fm or {}).get("sources") or []) if isinstance(s, str)}
    norm_cache: dict[int, str] = {}
    for m in CITE_RE.finditer(body):
        sid, quote = int(m.group(1)), m.group(2)
        if sid not in sources:
            errs.append(f"{name}: cites unknown source [s{sid}]")
            continue
        fname, stext = sources[sid]
        if len(quote.split()) < 5:
            errs.append(f'{name}: quote under 5 words: [s{sid}: "{quote}"]')
            continue
        hay = norm_cache.setdefault(sid, normalize_ws(stext))
        if normalize_ws(quote) not in hay:
            errs.append(f'{name}: quote not found verbatim in s{sid} ({fname}): "{quote[:80]}"')
        if listed and fname not in listed:
            errs.append(f"{name}: cites s{sid} ({fname}) but it is not in frontmatter sources")
    for m in MD_LINK_RE.finditer(body):
        target = m.group(1).strip("<>").split("#")[0]
        if re.fullmatch(r"(\./)?source-[^/]*\.md", target):
            errs.append(f'{name}: links to a source page ({target}); cite as [s<id>: "quote"] only')
    for m in BARE_CITE_RE.finditer(body):
        if int(m.group(1)) in sources:
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


def check_links(name: str, text: str, wiki: Path) -> list[str]:
    errs = []
    _, body = split_frontmatter(text)
    for m in MD_LINK_RE.finditer(body):
        target = m.group(1).strip("<>")
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("#"):
            continue
        path = unquote(target.split("#", 1)[0])
        if path and not (wiki / path).exists():
            errs.append(f"{name}: broken link -> {target}")
    for m in WIKILINK_RE.finditer(body):
        slug = m.group(1).strip()
        cand = slug if slug.endswith(".md") else f"{slug}.md"
        if not (wiki / cand).exists():
            errs.append(f"{name}: broken wikilink [[{slug}]]")
    return errs


CURRENT_STATE_FORMAT = re.compile(r"^## Current state \(as of \d{4}-\d{2}-\d{2}\)$")


def check_current_state(name: str, text: str) -> list[str]:
    """A page that has a `## Current state` section: the heading reads exactly
    `## Current state (as of YYYY-MM-DD)` and it is the first `##` section. Source
    summary pages are exempt."""
    if name.startswith("source-"):
        return []
    _, body = split_frontmatter(text)
    h2 = [ln.strip() for ln in body.splitlines() if ln.startswith("## ")]
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


def _h2(text: str) -> list[str]:
    _, body = split_frontmatter(text)
    out = []
    for ln in body.splitlines():
        if ln.startswith("## "):
            out.append(normalize_ws(CLOSE_RE.sub("", MARKER_RE.sub("", ln[3:]))).strip())
    return out


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
            allowed = " · ".join(pt.sections) if pt.sections else f"'{pt.pattern}'"
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


def check_content_loss(name: str, before: str, after: str) -> list[str]:
    """A rewritten page may not drop >15% of its lines (superseded text stays on the page,
    so marked lines are not deletions), nor a heading unless a superseded marker sits on or
    next to the line that keeps its text. `## Current state` is outside both rules."""
    b, a = _body_lines(before), _body_lines(after)
    if not b:
        return []
    lost = sum((Counter(b) - Counter(a)).values())
    frac = lost / len(b)
    lost_heads = sorted(
        set(_headings(before, skip_current=True)) - set(_headings(after, skip_current=True))
    )
    errs = []
    if frac > LOSS_THRESHOLD:
        errs.append(f"{name}: lost {lost}/{len(b)} lines ({frac:.0%}); superseded text must stay")
    for h in lost_heads:
        if not _heading_marked_nearby(h, after):
            errs.append(f"{name}: heading '{h}' removed without an adjacent superseded marker")
    return errs


# Check names, for counting which check caused a failed write.
CHECK_KINDS = (
    ("cites unknown source", "citations"),
    ("quote under 5 words", "short_quote"),
    ("not found verbatim", "citations"),
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
) -> list[str]:
    errs: list[str] = []
    for name in pages:
        p = wiki / name
        prior = before_dir / name
        if not p.exists():
            if prior.exists():
                errs.append(f"{name}: page deleted")
            continue
        text = read_text(p)
        errs += check_frontmatter(name, text)
        errs += check_duplicate_headings(name, text)
        errs += check_citations(name, text, sources)
        errs += check_links(name, text, wiki)
        errs += check_current_state(name, text)
        errs += check_superseded_blocks(name, text, current_id)
        if types:
            errs += check_section_headings(
                name, read_text(prior) if prior.exists() else None, text, types
            )
        if prior.exists():
            errs += check_content_loss(name, read_text(prior), text)
    return errs
