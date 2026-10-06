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
        s = normalize_ws(MARKER_RE.sub("", line))
        if s:
            out.append(s)
    return out


def _headings(text: str, level: int | None = None, skip_current: bool = False) -> list[str]:
    _, body = split_frontmatter(text)
    if skip_current:
        body = _without_current_state(body)
    hs = []
    for line in body.splitlines():
        m = HEADING_RE.match(line.strip())
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
    for m in BARE_CITE_RE.finditer(body):
        if int(m.group(1)) in sources:
            errs.append(f"{name}: citation without a quote: [s{m.group(1)}]")
    return errs


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
    `## Current state (as of YYYY-MM-DD)` and it is the first `##` section."""
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


def check_content_loss(name: str, before: str, after: str) -> list[str]:
    """A rewritten page that drops >15% of its lines, or any heading, needs a new
    superseded marker; otherwise it fails."""
    b, a = _body_lines(before), _body_lines(after)
    if not b:
        return []
    lost = sum((Counter(b) - Counter(a)).values())
    frac = lost / len(b)
    lost_heads = sorted(
        set(_headings(before, skip_current=True)) - set(_headings(after, skip_current=True))
    )
    new_markers = len(MARKER_RE.findall(after)) > len(MARKER_RE.findall(before))
    errs = []
    if (frac > LOSS_THRESHOLD or lost_heads) and not new_markers:
        if frac > LOSS_THRESHOLD:
            errs.append(
                f"{name}: lost {lost}/{len(b)} lines ({frac:.0%}) without a superseded marker"
            )
        for h in lost_heads:
            errs.append(f"{name}: heading '{h}' removed without a superseded marker")
    return errs


# Check names, for counting which check caused a failed write.
CHECK_KINDS = (
    ("cites unknown source", "citations"),
    ("quote under 5 words", "short_quote"),
    ("not found verbatim", "citations"),
    ("not in frontmatter sources", "citations"),
    ("citation without a quote", "citations"),
    ("broken link", "links"),
    ("broken wikilink", "links"),
    ("without a superseded marker", "content_loss"),
    ("duplicate ## heading", "duplicate_headings"),
    ("current state heading must read", "current_state"),
    ("current state is not the first", "current_state"),
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
) -> list[str]:
    errs: list[str] = []
    for name in pages:
        p = wiki / name
        if not p.exists():
            continue
        text = read_text(p)
        errs += check_frontmatter(name, text)
        errs += check_duplicate_headings(name, text)
        errs += check_citations(name, text, sources)
        errs += check_links(name, text, wiki)
        errs += check_current_state(name, text)
        prior = before_dir / name
        if prior.exists():
            errs += check_content_loss(name, read_text(prior), text)
    return errs
