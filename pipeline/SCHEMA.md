# Wiki Schema (the contract every wiki page obeys)

This is the "schema layer" of the LLM-Wiki pattern — the rules the weaving agent
follows so the wiki stays coherent and machine-checkable. The structural
validator (`validate_wiki.py`) enforces the mechanical parts; the `assess` node
judges the rest against the eval rubric.

## Page = one markdown file per concept

`wiki/<slug>.md`. ONE page per canonical concept/entity — never one page per
source. When a new source discusses an existing concept, **update that page**,
don't create a near-duplicate.

## Frontmatter (required on every page)

```yaml
---
title: Human Readable Title
type: concept | entity | comparison | synthesis | source-summary | index | overview
sources: [1, 3, 4]        # source article numbers this page draws from
last_updated: 2026-06-10
confidence: 0.0-1.0        # optional; lower when sources disagree
stale: false               # optional
---
```

`validate_wiki.py` requires `title`, `type`, `sources` on every page; content
pages (non-index/overview) must cite ≥1 source.

## Linking

- Cross-reference related pages with `[[Page Title]]` wikilinks. Link by the
  target page's **`title`** (Obsidian convention). The validator resolves a link
  if it matches a page's `title` OR its filename slug — but link by title for
  consistency. (This convention is pinned so the generator and `validate_wiki.py`
  never drift; a mismatch here is what the first proof run caught.)
- Every `[[link]]` must resolve to an existing page. If you reference a concept
  that has no page yet, create at least a stub page for it (title + frontmatter
  + one line) so the link resolves. **No dangling links.**
- Every content page should be reachable — linked from `index.md` or another
  page. No orphans.

## Contradiction handling (the heart of it)

When two sources disagree, **do NOT average them away or silently pick one.**
On the relevant page, add a section:

```markdown
## Open tensions
- **<one-line tension>**: Source N says "<quote>"; Source M says "<quote>".
  (status: unresolved | conditional on <X>)
```

A factual discrepancy (e.g. two different numbers for the same event) must be
flagged with both values and their sources — never collapsed to one or averaged.

## Content retention during re-writes

Integration re-writes existing prose; it must never silently delete it. Every
claim already present on a page being re-written must end the edit in exactly
one of three states (the same taxonomy the claim-retention grader uses):

- **RETAINED** — still present, possibly reworded, meaning preserved.
- **SUPERSEDED** — replaced by newer information **with a visible trace**:
  the new text names what it replaces (e.g. "500, up from 100",
  "superseded by [N]"), or the old claim moves to a dated `## History` note.
- **MOVED** — relocated to another page with a `[[wikilink]]`.

Absence of mention in a new source is **NOT** evidence of staleness — content
is never removed merely because a new source does not repeat it. Dated /
operational history is content, not clutter: when it no longer fits the
narrative flow, compact it into a `## History` subsection on the same page —
never silently drop it.

## Required navigation pages

- `index.md` (type: index) — catalog of all pages, grouped by type.
- `overview.md` (type: overview) — **SYNTHESIZED NAVIGATIONAL MAP** of the whole wiki.
  Structure rules (enforced by the eval grader):
  - Organized by **THEME/subtopic** with `##` section headers — NOT by source, NOT by ingest
    order. Themes are the reader's entry points (e.g. "## Agent Orchestration", "## LLM APIs").
  - Each theme section links to its hub/concept pages with `[[wikilinks]]` and a one-line
    orientation of what's there.
  - **NEVER** write `(source N)` parentheticals, `(sources N, M, …)`, or per-source prose
    openers such as "A fifth thread (source 23)…". The overview describes the KNOWLEDGE THEMES,
    not the ingest history.
  - Short and navigational: a reader learns the TOPICS covered and how to navigate to them —
    not a summarised list of each article.
  - **Update it every ingest cycle**: add new theme sections or `[[wikilinks]]` for pages
    created this cycle; consolidate or remove stale entries. Never append a new per-source
    summary paragraph; fold new knowledge into existing or new theme sections.

## Provenance: tracing citations to author + URL

Each source article is assigned a stable integer id stored in `<wiki>/.sources.json`.
That registry now carries `author`, `url`, and `date` (read from the source's YAML
frontmatter), so citation `[N]` can resolve to a full bibliographic record.

Pages that draw from external sources SHOULD include a `## Sources` footer listing
the source ids they cite and, where known, the author/title/URL:

```markdown
## Sources

- [1] Jane Doe — "Article Title" — https://example.com/article
- [3] Bob Smith — "Another Article" — https://example.com/other
```

The registry is the authoritative store; the footer is a reader convenience.
When writing or updating a page, list every id that appears in the page's
`sources:` frontmatter field. You can read `.sources.json` to resolve id → author/url.

## Never confabulate

Only write claims supported by a cited source. If sources don't say it, it
doesn't go in the wiki. A rhetorical/strawman framing in a source is not a claim
the wiki should assert. Vendor/marketing claims — star counts, performance superlatives, self-reported adoption stats — must be framed as attributed claims ("X reports…", "according to [N]"), not stated as fact in the wiki's own voice.
