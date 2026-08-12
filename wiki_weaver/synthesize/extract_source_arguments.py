"""wiki_weaver.synthesize.extract_source_arguments -- deterministic thesis
extraction from source-level wiki pages, the iteration-4 fix to
``scan_arguments``' input.

THE FAILURE THIS REPLACES (see pipeline/synthesize.dot's header for the full
account): ``scan_arguments`` read ``wiki/index.md`` + ``wiki/overview.md``
only. An independent evaluation found this measurably self-referential --
``index.md`` is an ENTITY roster (organized per-entity, so an argument with
no entity and no shared title vocabulary is invisible to it by
construction) and ``overview.md`` is a per-SOURCE changelog (organized by
arrival order during ingest, so an argument repeated across many sources in
different words is buried rather than surfaced). Feeding either structure
back into a "find what's missing" scan finds only what those structures
already surface -- confirmed measurement: the entity-layer single-source
ratio did not move (68.8% -> 68.8%) across a full run.

THE UNUSED LAYER: every source-level wiki page (``type: source`` in its own
frontmatter -- the page ``pipeline/ingest.dot``'s own weave prompt is
instructed to write per source, "capturing what this source AS A WHOLE says
and argues -- its own thesis, scope, and conclusions") already states its
source's argument in prose, independent of which entities that source
happens to mention. Reading all of them costs the full page bodies (300+ KB
across a real corpus) which does not fit alongside everything else this
pass needs; this module extracts just the argument-bearing PART of each --
observed convention across every measured wiki: a "## Thesis" or "## Thesis
and Scope" heading, immediately after the title/byline block, ending at the
next heading. ~43 pages x ~500-1000 bytes each is a condensed, ARGUMENT-only
view of the corpus that fits easily where the full source pages would not.

EXTRACTION CHOICE (and why, not just what): the heading-bounded slice was
chosen over two alternatives after reading the actual synth2-* source
pages under .amplifier/evaluation/wiki-weaver/ (read-only lab fixtures):

  - "first paragraph of the body" was rejected -- several source pages open
    with a byline/metadata block (**Author:**/**Published:**/**Source:**)
    before any prose, so "first paragraph" often grabs metadata, not
    argument.
  - "first sentence under each ## heading" was rejected -- it captures a
    sentence from EVERY section (scope, benchmarks, conclusions, ...), not
    just the thesis, diluting the extract with the same per-section detail
    this task is trying to get away from.
  - The "## Thesis" / "## Thesis and Scope" heading is the section
    ``ingest.dot``'s own weave prompt is asking the writer to produce
    ("its own thesis, scope, and conclusions") and, empirically, every
    source page across the sampled corpus uses one of these two exact
    headings as its FIRST heading after the title. Extracting exactly that
    section is the closest available proxy for "what does this source
    argue" without reading the whole page.

This is an LLM-emergent convention (``ingest.dot``'s weave prompt asks for
the CONTENT, not this exact heading text), not a schema ``wiki_weaver.ingest.
validate`` enforces -- so the match is case-insensitive and tolerant of
either wording, and a source page that does not use it at all falls back to
its first non-metadata prose paragraph rather than silently contributing
nothing.

Usage:
    python3 -m wiki_weaver.synthesize.extract_source_arguments --wiki-root <path>
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    extract_source_citations,
    list_wiki_pages,
    parse_frontmatter,
)

# The frontmatter `type` value ingest.dot's weave prompt's source-level page
# convergently uses across every measured wiki (see module docstring).
SOURCE_PAGE_TYPE = "source"

# Matches "## Thesis" or "## Thesis and Scope" (any heading depth, any
# trailing words) case-insensitively -- an LLM-emergent convention, not a
# schema, so the match is deliberately loose on wording and tight only on
# the leading word.
THESIS_HEADING_RE = re.compile(r"^#{1,6}[ \t]*thesis\b.*$", re.IGNORECASE | re.MULTILINE)
ANY_HEADING_RE = re.compile(r"^#{1,6}[ \t]+.*$", re.MULTILINE)
_METADATA_LINE_RE = re.compile(r"^\*\*(author|published|source)\b", re.IGNORECASE)

# Defensive cap -- observed extracts run ~400-1200 bytes; this only guards
# against a runaway page whose "Thesis" section was never closed by a
# following heading.
MAX_EXTRACT_CHARS = 1500

# Handful of usable source-level pages required before this module's output
# is anything other than noise -- enforced by check_nav_pages, not here (see
# that module: refusing BEFORE scan_arguments runs against a near-empty set,
# never silently here after the fact).
MIN_EXTRACT_CHARS = 30


def is_source_page(meta: dict) -> bool:
    """True when this page's frontmatter marks it as a per-source
    "what does this source argue" page (see module docstring)."""
    return str(meta.get("type", "")).strip().lower() == SOURCE_PAGE_TYPE


def source_citation_id(meta: dict, extract: str, body: str, path: Path) -> str:
    """Best citeable source id for this page.

    Primary: an inline ``NNN-Name.md`` citation (``lib.extract_source_
    citations``) already present in the extract itself -- this is the
    SAME inline-citation convention ``overview.md`` used, which the
    original ``scan_arguments`` prompt already relied on ("that citation
    is what you report"), and empirically every sampled source page cites
    itself this way at least once inside its own Thesis section.

    Fallback 1: an inline citation anywhere else in the page body, in case
    the Thesis section itself didn't happen to include one.

    Fallback 2: the frontmatter ``sources``/``source_id`` field -- only
    reliable when written in inline-bracket form (``sources: [id]``, the
    form ``write_gap_page.py`` writes and ``lib.parse_frontmatter`` can
    actually parse into a list). The multi-line YAML dash-list form real
    ingest-written source pages commonly use (``sources:\\n  - id``) is
    NOT parsed into a list by this project's intentionally minimal
    frontmatter parser -- it silently yields an empty string -- so this is
    a secondary signal, never the primary one.

    Last resort: the page's own filename.
    """
    citations = extract_source_citations(extract)
    if citations:
        return citations[0]
    citations = extract_source_citations(body)
    if citations:
        return citations[0]
    sources = meta.get("sources")
    if isinstance(sources, list) and sources:
        return str(sources[0])
    source_id = meta.get("source_id")
    if isinstance(source_id, str) and source_id.strip():
        return source_id.strip()
    return path.name


def extract_thesis(body: str) -> str:
    """Return the argument-bearing extract for one source page's body.

    Primary: the content of its own "## Thesis" / "## Thesis and Scope"
    heading, up to the next heading (or end of body). Fallback: the first
    non-empty prose paragraph that is not a title line or a
    **Author:**/**Published:**/**Source:** metadata line -- used only when
    no Thesis-like heading exists at all, so a page never silently
    contributes nothing.
    """
    match = THESIS_HEADING_RE.search(body)
    if match:
        start = match.end()
        next_heading = ANY_HEADING_RE.search(body, start)
        end = next_heading.start() if next_heading else len(body)
        section = body[start:end].strip()
        if section:
            return section[:MAX_EXTRACT_CHARS]

    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para or para.startswith("#") or _METADATA_LINE_RE.match(para):
            continue
        return para[:MAX_EXTRACT_CHARS]
    return ""


def find_source_pages(wiki_dir: Path) -> list[tuple[Path, dict, str]]:
    """Every ``type: source`` wiki page, sorted by filename for
    deterministic output -- ``(path, frontmatter, body)`` tuples. Pages that
    fail to decode are skipped (never crash the extraction over one bad
    file), mirroring ``rank_candidates.named_concept_titles``' own
    tolerance."""
    pages: list[tuple[Path, dict, str]] = []
    for path in list_wiki_pages(wiki_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        meta, body = parse_frontmatter(text)
        if is_source_page(meta):
            pages.append((path, meta, body))
    pages.sort(key=lambda item: item[0].name)
    return pages


def build_source_argument_entries(wiki_dir: Path) -> list[tuple[str, str, str]]:
    """Return ``(title, source_id, extract)`` for every usable ``type:
    source`` wiki page, sorted by filename (via ``find_source_pages``) --
    the shared building block both ``build_source_arguments_doc`` (the
    full, single-file corpus view ``attribute_sources`` reads) and
    ``wiki_weaver.synthesize.chunk_arguments`` (fixed-size batches of the
    SAME view, for the per-chunk detection loop -- see that module's
    docstring for why detection now runs in chunks) draw from. A page
    contributes nothing (is simply absent from the returned list) only
    when its extract is empty (no Thesis heading AND no usable fallback
    paragraph) -- never silently dropped for any other reason."""
    entries: list[tuple[str, str, str]] = []
    for path, meta, body in find_source_pages(wiki_dir):
        extract = extract_thesis(body)
        if not extract:
            continue
        title = str(meta.get("title") or path.stem)
        citation = source_citation_id(meta, extract, body, path)
        entries.append((title, citation, extract))
    return entries


def format_argument_section(title: str, source_id: str, extract: str) -> str:
    """The one canonical ``## <title>\\n(source_id: ...)\\n\\n<extract>``
    block format -- used identically by the full-document builder below
    and by ``chunk_arguments.format_chunk_doc``, so a chunk's sections
    read exactly like the full document's sections (same labels, same
    shape)."""
    return f"## {title}\n(source_id: {source_id})\n\n{extract}\n"


def build_source_arguments_doc(wiki_dir: Path) -> tuple[str, int]:
    """Build the condensed ``.ai/source-arguments.md`` document. Returns
    ``(document_text, pages_included)`` -- a page is excluded only when its
    extract is empty (no Thesis heading AND no usable fallback paragraph),
    never silently dropped for any other reason."""
    entries = build_source_argument_entries(wiki_dir)
    sections = [format_argument_section(title, citation, extract) for title, citation, extract in entries]
    included = len(sections)

    header = (
        "# Source Arguments (condensed)\n\n"
        "One argument-bearing extract per source-level wiki page (type: "
        "source) -- the thesis each source's own page states, not the "
        "page's full body. Generated deterministically by "
        "wiki_weaver.synthesize.extract_source_arguments; see "
        "pipeline/synthesize.dot's header for why this replaces "
        "wiki/overview.md for this pass.\n\n"
    )
    if sections:
        doc = header + "\n".join(sections)
    else:
        doc = header + "(no source-level page produced a usable extract)\n"
    return doc, included


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.extract_source_arguments")
    parser.add_argument("--wiki-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    doc, included = build_source_arguments_doc(wr.wiki_dir)

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.source_arguments_file, doc)
    print(
        f"{included} source-level page(s) extracted into {wr.source_arguments_file}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
