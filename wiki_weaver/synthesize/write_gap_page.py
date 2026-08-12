"""wiki_weaver.synthesize.write_gap_page -- deterministic page assembly.

THE box/tool split (REWRITE-GUIDE.md \u00a71.6, dev_machine.dot's SelectWork/
ApplyState precedent, "distilled from the selectwork hill-climb experiment:
baseline 14/25 -> 25/25... the reliability failures were in the BOOKKEEPING
... NOT in the judgment"): ``answer_gap`` (box) writes ONLY prose to
``.ai/gap-answer.md`` -- it never touches frontmatter, filenames, or the
``sources:`` field. This module is the bookkeeping: it deterministically
computes the page's filename (slug of the term), writes CORRECT frontmatter
(``title``, ``type: theme``, ``sources:`` -- the exact fields
``lib.find_structural_issues`` requires and ``build_catalog``/``load_index``
already parse), and places the LLM's prose as the body. An LLM asked to
also format frontmatter is an LLM asked to get YAML-ish syntax exactly
right on every pass; code gets it right every time.

``type: theme`` is a NEW page type (distinct from the existing per-source
``type: source``/``type: concept`` pages seen in the corpus) -- see
module-level ``PAGE_TYPE`` -- so a page this pipeline wrote is trivially
distinguishable from ordinary ingest output, and a future lint pass could
report on synthesized-vs-ingested pages separately without re-deriving the
distinction from provenance heuristics.

ORPHAN FIX (the every-gap-declined incident): ``validate`` (downstream in
``pipeline/synthesize.dot``) reuses ``lib.find_structural_issues`` -- the
SAME orphan check ``ingest.dot``'s own validate node applies -- which flags
any non-nav page with zero incoming wikilinks. That check is correct: it
just never had a chance to pass here. ``ingest.dot``'s ``weave`` node links
a new page from an existing one in the SAME LLM pass that creates it;
this module wrote ONLY the new page and nothing ever linked to it, so the
orphan check failed on every single gap, every time, until ``retry_bound``
gave up -- a real 157-page run produced two well-supported synthesis pages
and threw both away. ``link_gap_page`` below closes that gap the same way
``write_gap_page`` closes the frontmatter gap: deterministically, in the
SAME step, so a brand-new page is never orphaned at validation time. This
does NOT weaken ``find_structural_issues`` -- a page nothing links to is
still, correctly, an orphan; see ``tests/test_validate.py`` for the
regression proof that a genuinely orphaned page still fails validation.

Usage:
    python3 -m wiki_weaver.synthesize.write_gap_page --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from wiki_weaver.lib import NAV_PAGES, WikiRoot, atomic_write_text, ensure_dir, split_header

PAGE_TYPE = "theme"

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")

# index.md (NAV_PAGES[0]) is the wiki's catalog page -- "a catalog of
# everything in the wiki -- each page listed with a link" (docs/
# llm-wiki-pattern.md L58) -- so it is the natural, existing place to hang
# the one inbound link a freshly synthesized page needs. NAV_PAGES itself
# is exempt from the orphan check (lib.find_structural_issues), so editing
# its body has no effect on ITS OWN validation status -- only on whatever
# it now links to.
_INDEX_PAGE_NAME = NAV_PAGES[0]

# Stable, idempotent home for every page this pipeline links from index.md
# -- created once, appended to on every subsequent gap so re-runs and later
# gaps never spawn a second heading.
_SYNTH_LINK_HEADING = "## Synthesized Themes"


def slugify(term: str) -> str:
    """``"token economics"`` -> ``"token-economics"``. Collapses any run of
    non-alphanumeric characters to a single hyphen and strips leading/
    trailing hyphens -- the same Windows-safe, filesystem-safe pattern used
    throughout this codebase for generated filenames."""
    slug = _SLUG_STRIP_RE.sub("-", term.lower()).strip("-")
    return slug or "gap"


def page_title(term: str) -> str:
    """The single place a gap term becomes a page title -- shared by
    ``render_page`` and ``link_gap_page`` so the linked display text always
    matches the page's own ``title:`` frontmatter exactly."""
    return term.title()


def _append_synth_link(body: str, slug: str, title: str, summary: str) -> str:
    """Idempotently append a ``[[slug|Title]]`` entry to ``index.md``'s
    body under the stable ``_SYNTH_LINK_HEADING`` section (created on first
    use, appended to on every later gap). Never adds a second entry for the
    same slug -- a re-run (crash between write and validate, or a retry
    pass) must not duplicate the link."""
    if f"[[{slug}]]" in body or f"[[{slug}|" in body:
        return body  # already linked -- idempotent re-run/retry

    entry = f"- [[{slug}|{title}]] -- {summary}".rstrip()
    lines = body.splitlines()

    if _SYNTH_LINK_HEADING in lines:
        heading_idx = lines.index(_SYNTH_LINK_HEADING)
        # Insert at the end of this section: right before the next heading
        # line, or at the end of the body if this is the last section.
        end_idx = len(lines)
        for i in range(heading_idx + 1, len(lines)):
            if lines[i].startswith("#"):
                end_idx = i
                break
        while end_idx > heading_idx + 1 and not lines[end_idx - 1].strip():
            end_idx -= 1  # keep the section's own trailing blank line out of the insert point
        lines[end_idx:end_idx] = [entry]
        return "\n".join(lines).rstrip("\n") + "\n"

    # First synthesized page ever -- create the section fresh.
    prefix = body.rstrip("\n")
    if prefix:
        prefix += "\n\n"
    return f"{prefix}{_SYNTH_LINK_HEADING}\n\n{entry}\n"


def link_gap_page(wiki_dir: Path, slug: str, title: str, summary: str) -> bool:
    """Add the one inbound wikilink a freshly written theme page needs so
    ``validate``'s orphan check (``lib.find_structural_issues``) is a real
    check for it rather than a guaranteed first-pass failure -- see this
    module's ORPHAN FIX docstring section. Best-effort: if ``index.md`` is
    somehow absent (should never happen past ``check_nav_pages``, this
    pipeline's own fail-loud precondition), this is a silent no-op rather
    than a crash -- linking is a quality improvement to the orphan check,
    not itself a new required precondition. Returns whether index.md was
    written."""
    index_path = wiki_dir / _INDEX_PAGE_NAME
    if not index_path.is_file():
        return False
    text = index_path.read_text(encoding="utf-8")
    header, body = split_header(text)
    new_body = _append_synth_link(body, slug, title, summary)
    if new_body == body:
        return False  # already linked, nothing to write
    atomic_write_text(index_path, header + new_body)
    return True


def render_page(term: str, source_ids: list[str], body: str) -> str:
    """Frontmatter + body for the new theme page. ``sources:`` lists every
    source id ``find_gaps`` measured as discussing this term -- the
    citations that justify the page existing at all (task constraint:
    "Every page it writes must cite the multiple sources that justified
    it")."""
    # Inline bracket-list form -- lib.parse_frontmatter's hand-rolled parser
    # (no PyYAML, stdlib only) only understands "key: [a, b]" for lists, not
    # YAML block-dash syntax; this is the SAME format documented in its own
    # module docstring ("sources: [1]") and what build_catalog/load_index
    # already parse via meta.get("sources", []).
    sources_inline = "[" + ", ".join(source_ids) + "]"
    title = page_title(term)
    frontmatter = f'---\ntitle: "{title}"\ntype: {PAGE_TYPE}\nsources: {sources_inline}\nsynthesized: true\n---\n\n'
    return frontmatter + f"# {title}\n\n" + body.strip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.write_gap_page")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _read_json(path: Path, what: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- {what} must run first")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        gap = _read_json(wr.current_gap_file, "select_gap")
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    answer_path = wr.gap_answer_file
    if not answer_path.is_file():
        print(f"{answer_path} does not exist -- answer_gap must write it before this runs", file=sys.stderr)
        return 1
    body = answer_path.read_text(encoding="utf-8")

    slug = slugify(gap["term"])
    page_path = wr.wiki_dir / f"{slug}.md"

    # Idempotency: if a prior, interrupted pass already wrote this exact
    # page (crash between write and validate/commit), overwrite it with the
    # SAME deterministic content rather than erroring -- re-running this
    # node is always a no-op-or-refresh, never a duplicate (REWRITE-GUIDE.md
    # \u00a72.3: "Box (LLM): rerunning overwrites its one artifact, never
    # appends" -- this tool node inherits the same discipline one step
    # downstream of the box).
    ensure_dir(wr.wiki_dir)
    atomic_write_text(page_path, render_page(gap["term"], gap["source_ids"], body))

    # ORPHAN FIX: link the new page from index.md in this SAME step, before
    # `validate` ever runs -- see this module's docstring. Uses the gap's
    # own "claim" (scan_arguments' one-sentence contention, when present)
    # as the catalog entry's summary; falls back to the source count when
    # scan_arguments didn't report one (older cached candidate lists).
    title = page_title(gap["term"])
    summary = str(gap.get("claim") or "").strip() or f"synthesized from {len(gap['source_ids'])} source(s)"
    link_gap_page(wr.wiki_dir, slug, title, summary)

    print(f"wrote {page_path} ({int(time.time())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
