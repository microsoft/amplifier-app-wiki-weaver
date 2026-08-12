"""wiki_weaver.ingest.build_catalog -- what weave KNOWS EXISTS.

THE FIX this module implements: see evals/compounding/PRECOMMIT-INDEX.md for
the full rationale. The source gist (docs/llm-wiki-pattern.md L58) is
index-FIRST: "index.md ... a catalog of everything in the wiki -- each page
listed with a link, a one-line summary ... the LLM reads the index first to
find relevant pages, then drills into them." The prior production pipeline
had CODE decide create-vs-fold from a BM25 top-score threshold in
retrieve_slice.py (still present, unchanged, for evals/arms/ingest-b.dot),
which measured N=10 -> 11 pages, N=110 -> 14 pages: a bootstrap rule (create
only when a source shares ZERO vocabulary with EVERY existing page) doing
duty as a growth rule.

This module restores the missing half. ``retrieve_slice`` still bounds what
``weave`` may READ (a real cost control at scale -- unchanged). This module
gives ``weave`` a compact whole-wiki catalog of what EXISTS -- filename,
title, source count, one-line summary, no bodies -- so the SAME writer that
already reads and writes the source can decide for itself, in the same pass,
whether this source belongs in an existing page or warrants a new one.
Exactly what a single interactive session would do.

No new node decides on weave's behalf (that was evals/arms/ingest-c.dot's
losing approach: a separate, cheaper planner with less context than the
writer). This is purely deterministic bookkeeping -- no model call.

PRE-WRITE STEERING (widened per the working-reference-implementation gap: a
real orchestrator hand-wrote a per-cycle brief naming which existing pages
the incoming source was PREDICTED to touch, with each page's current source
count, before the source was read -- closing the loop this pipeline's
post-write ``review_gate`` never actually exercised in 11 evaluation runs).
Two additions, both still purely deterministic bookkeeping -- no new node,
no model call:

1. Each catalog entry now also carries ``source_count`` -- the length of
   that page's frontmatter ``sources:`` list -- so a 9-source hub reads
   differently from a 1-source stub in the same listing.
2. ``build_predicted_targets`` reads the BM25-ranked slice
   ``retrieve_slice`` already wrote to ``.ai/current_slice.json`` (it runs
   immediately before this module in ``pipeline/ingest.dot``) and surfaces
   its top few hits as a distinct, explicitly-labeled "predicted merge
   targets" section -- a falsifiable PREDICTION for weave to confirm or
   reject while writing, never a directive that overrides its judgment.

Output: ``.ai/current_catalog.md`` (see ``WikiRoot.current_catalog_file``),
one line per page: filename, title, source count, one-line summary. Stable
order (the same alphabetical order ``list_wiki_pages``/``load_wiki_pages``
already guarantee) so an unchanged wiki produces byte-identical output
(modulo the predicted-targets section, which depends on the current
source's slice, not the wiki's contents). Truncated with an explicit,
visible marker -- never silently -- once the rendered catalog would exceed
``MAX_CATALOG_BYTES`` (measured: ~828 bytes at 14 real pages, ~49 KB at the
615-page split sibling of the same corpus -- comfortably under the ~60 KB
ceiling this module enforces). The predicted-targets section is reserved
budget FIRST -- see ``render_catalog`` -- so the full inventory is what
truncates under pressure, never the prediction.

Usage:
    python3 -m wiki_weaver.ingest.build_catalog --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, list_wiki_pages, parse_frontmatter, to_wiki_relpath

# ~60 KB ceiling (PRECOMMIT-INDEX.md): comfortably above the measured 615-page
# real-corpus catalog (~49 KB) while still small next to a single LLM pass's
# context budget. Truncation below this is deterministic and always visible.
MAX_CATALOG_BYTES = 60_000

# One-line summary cap -- a "one-line summary" (per the gist) that ran on for
# a full paragraph would defeat the point of a compact catalog.
_MAX_SUMMARY_CHARS = 160

# How many of retrieve_slice's BM25-ranked hits to surface as "predicted
# merge targets." Deliberately small -- per the task's own framing, this is
# a short, falsifiable hint, not a second inventory. retrieve_slice's own
# candidate slice can run to MAX_SLICE_CANDIDATES (60, see lib.py) once the
# byte budget allows; repeating that whole list here would just be the
# slice restated, not a prediction. 5 mirrors the real reference
# implementation's own per-cycle brief, which named a handful of pages, not
# the whole read-bounded slice.
MAX_PREDICTED_TARGETS = 5

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")


def _first_sentence(body: str) -> str:
    """The first non-heading, non-empty line's leading sentence, trimmed to
    ``_MAX_SUMMARY_CHARS``.

    This is the fallback every real page in the measured corpus actually
    uses: neither the N=110 wiki nor its 615-page split sibling has a
    ``description`` frontmatter field on any page (verified against both
    real snapshots) -- only ``title``, ``type``, ``sources``.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        sentence = _SENTENCE_END_RE.split(stripped, maxsplit=1)[0].strip()
        if len(sentence) > _MAX_SUMMARY_CHARS:
            sentence = sentence[: _MAX_SUMMARY_CHARS - 1].rstrip() + "\u2026"
        return sentence
    return ""


def _source_count(meta: dict) -> int:
    """Number of frontmatter ``sources:`` citations on this page --
    established hub (many) vs. single-source stub (one) vs. not-yet-cited
    (zero). Reuses the same list-normalization ``lib.refresh_page_index``
    already applies to ``sources:`` frontmatter (a bare scalar becomes a
    one-item list) so a page's citation count is counted the same way
    everywhere it is read."""
    sources = meta.get("sources", [])
    if not isinstance(sources, list):
        sources = [sources]
    return len(sources)


def _catalog_entry(path: Path) -> dict:
    """One catalog entry for a single wiki page: id (filename), title,
    source count, one-line summary. Prefers frontmatter ``description`` when
    present (no real page in the measured corpus has one yet, but a future
    page might); otherwise falls back to ``_first_sentence`` of the body."""
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    title = str(meta.get("title") or path.stem)
    description = meta.get("description")
    summary = str(description).strip() if description else _first_sentence(body)
    return {"id": path.name, "title": title, "summary": summary, "source_count": _source_count(meta)}


def build_catalog_entries(wiki_dir: Path) -> list[dict]:
    """One entry per wiki page, in the SAME stable order ``list_wiki_pages``
    already guarantees (alphabetical by filename) -- deterministic,
    byte-for-byte-identical output for an unchanged wiki."""
    return [_catalog_entry(p) for p in list_wiki_pages(wiki_dir)]


def build_predicted_targets(
    wr: WikiRoot, entries_by_id: dict[str, dict], max_targets: int = MAX_PREDICTED_TARGETS
) -> list[dict]:
    """THE pre-write steering signal: pair retrieve_slice's already-computed
    BM25 ranking with this catalog's entries, so weave sees "these existing
    pages are PREDICTED to be where this source belongs" before it decides
    anything -- not a new signal, just surfacing one retrieve_slice already
    threw away once the slice was assembled (DESIGN.md \u00a73 / \u00a712.1: no new
    node, no new model call, purely deterministic bookkeeping).

    ``retrieve_slice`` runs immediately before ``build_catalog`` in
    ``pipeline/ingest.dot`` (select_source -> detect_kind -> watermark ->
    segment -> retrieve_slice -> build_catalog -> weave), so
    ``.ai/current_slice.json`` -- specifically its ``bm25_hits`` list,
    already ranked best-first by ``bm25.BM25.top_k`` -- is available by the
    time this runs. Absent (a fresh checkout, or a caller that invokes
    ``build_catalog`` in isolation without ever running ``retrieve_slice``,
    e.g. most of this module's own unit tests) -> empty list, exactly like
    ``retrieve_slice``'s own optional-upstream-artifact handling
    (``current_segment_content_file``).

    Entries are looked up in ``entries_by_id`` (this catalog's own, freshly
    built entries -- never re-read from disk) so a predicted target always
    carries the SAME title/summary/source_count this catalog already
    computed; a hit for a page this catalog has no entry for (should not
    happen -- both are built from the same ``wiki/`` directory in the same
    pass, but never trust that silently) is skipped rather than guessed at.
    """
    slice_path = wr.current_slice_file
    if not slice_path.is_file():
        return []
    try:
        slice_data = json.loads(slice_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    hits = slice_data.get("bm25_hits") or []

    targets: list[dict] = []
    for hit in hits[:max_targets]:
        page_path = str(hit.get("page", ""))
        page_id = page_path.removeprefix("wiki/")
        entry = entries_by_id.get(page_id)
        if entry is not None:
            targets.append(entry)
    return targets


def _source_label(source_count: int) -> str:
    """ "1 source" / "9 sources" -- the singular/plural form catalog lines
    and predicted-target lines both use."""
    return f"{source_count} source" if source_count == 1 else f"{source_count} sources"


def _entry_line(entry: dict) -> str:
    """One catalog/predicted-target line: wiki/-relative path, title, source
    count, one-line summary. Shared by the full inventory and the
    predicted-targets section so the two never drift into different
    formats for what is the same underlying entry.

    wiki/-relative path (see lib.to_wiki_relpath) -- never a bare filename.
    weave's file tools are rooted at wiki_root, so a bare "index.md" here is
    exactly the ambiguity that let content pages land at wiki_root instead
    of wiki/ (the write-path bug).
    """
    suffix = f": {entry['summary']}" if entry.get("summary") else ""
    source_count = entry.get("source_count", 0)
    return f"- {to_wiki_relpath(entry['id'])} \u2014 {entry['title']} ({_source_label(source_count)}){suffix}"


_PREDICTED_TARGETS_HEADER = (
    "\n## Predicted merge targets for this source (unconfirmed)\n\n"
    "Ranked by lexical overlap with this source, computed BEFORE it was read (the same "
    "BM25 ranking behind your read slice). This is a falsifiable PREDICTION to confirm "
    "or reject while you write, not an instruction: if an entity does not actually "
    "belong on a page listed here, place it wherever it actually belongs instead.\n\n"
)


def _render_predicted_targets(predicted_targets: list[dict]) -> str:
    """The bounded, explicitly-labeled prediction section -- distinct from
    the full inventory above it. Empty input -> empty string (no section at
    all), so a source with no prior slice, or a caller that never computed
    one, gets exactly today's catalog back, unchanged."""
    if not predicted_targets:
        return ""
    lines = [_entry_line(entry) for entry in predicted_targets]
    return _PREDICTED_TARGETS_HEADER + "\n".join(lines) + "\n"


def render_catalog(
    entries: list[dict],
    max_bytes: int = MAX_CATALOG_BYTES,
    predicted_targets: list[dict] | None = None,
) -> str:
    """Render the compact catalog markdown weave reads to know what EXISTS.

    Never silently drops pages: once the rendered text would exceed
    ``max_bytes``, remaining entries are cut and an explicit ``TRUNCATED``
    marker states exactly how many pages were omitted.

    ``predicted_targets`` (optional, see ``build_predicted_targets``): a
    short, bounded list of this catalog's own entries, already ranked by
    retrieve_slice's BM25 pass -- rendered as a clearly separate section
    AFTER the full inventory. Its budget is reserved FIRST, before the
    inventory's truncation loop runs, so a full wiki that must truncate
    truncates its OWN listing, never the prediction -- the prediction is
    small and bounded (``MAX_PREDICTED_TARGETS``) precisely so reserving
    room for it first never meaningfully starves the inventory.
    """
    header = (
        "# Wiki Catalog\n\n"
        f"{len(entries)} page(s) exist in this wiki. Filename, title, source count, "
        "one-line summary -- no bodies. This tells you what EXISTS (and how established "
        "each page already is); it does not\n"
        "grant permission to read any of them -- see .ai/current_slice.json "
        "for what you may READ.\n\n"
    )

    predicted_text = _render_predicted_targets(predicted_targets or [])

    budget = max_bytes - len(header.encode("utf-8")) - len(predicted_text.encode("utf-8"))
    body_lines: list[str] = []
    included = 0
    for entry in entries:
        line = _entry_line(entry)
        line_bytes = len(line.encode("utf-8")) + 1  # +1 for the trailing newline
        if line_bytes > budget:
            break
        body_lines.append(line)
        budget -= line_bytes
        included += 1

    text = header + "\n".join(body_lines) + ("\n" if body_lines else "")

    if included < len(entries):
        omitted = len(entries) - included
        text += (
            f"\n... TRUNCATED: {omitted} more page(s) omitted (catalog exceeded "
            f"{max_bytes} bytes) -- see wiki/index.md directly for the rest ...\n"
        )

    return text + predicted_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.build_catalog")
    parser.add_argument("--wiki-root", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    entries = build_catalog_entries(wr.wiki_dir)
    entries_by_id = {entry["id"]: entry for entry in entries}
    predicted_targets = build_predicted_targets(wr, entries_by_id)
    text = render_catalog(entries, predicted_targets=predicted_targets)

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.current_catalog_file, text)

    size = len(text.encode("utf-8"))
    predicted_note = f", {len(predicted_targets)} predicted target(s)" if predicted_targets else ""
    print(f"catalog: {len(entries)} page(s){predicted_note}, {size} byte(s) -> {wr.current_catalog_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
