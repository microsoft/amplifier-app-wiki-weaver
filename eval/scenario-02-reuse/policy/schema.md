# Board Games Wiki Schema

This is the **policy layer** for a board-games wiki — the rules the weaving agent follows so
the wiki stays coherent and machine-checkable for the board games domain.  It overrides the
default `pipeline/SCHEMA.md` for this project wiki.

## Page Taxonomy

`type:` enum (one per page):

- `game` — a specific board game (e.g., Catan, Wingspan, Dominion)
- `mechanic` — a gameplay mechanic (e.g., engine-building, worker-placement, deck-building)
- `designer` — a specific game designer (e.g., Klaus Teuber, Elizabeth Hargrave)
- `comparison` — a page comparing multiple games across a dimension
- `catalog` — navigation root listing all games (slug: `catalog.md`)
- `landing` — overview/entry point for the wiki (slug: `landing.md`)

## Frontmatter (required on every page)

```yaml
---
title: Human Readable Title
type: game | mechanic | designer | comparison | catalog | landing
sources: [1, 2]          # source article numbers this page draws from
last_updated: 2026-06-13
confidence: 0.0-1.0      # optional; lower when sources disagree
---
```

The validator (`validator.yaml` in this policy/) requires `title`, `type`, and `sources`
on every page.

## Navigation Pages

- `catalog.md` — lists all games indexed in this wiki (type: catalog)
- `landing.md` — the entry point and scope overview (type: landing)

These two pages are navigation roots and are **exempt from the orphan check** (they need
not be linked from other pages — they are entry points, not content pages).

## Linking

Cross-reference related pages with `[[Page Title]]` wikilinks. Link by the target page's
`title` (Obsidian convention). Every `[[link]]` must resolve. Create stub pages for
referenced concepts that don't yet have a page.

## Contradiction Handling

When sources disagree on a factual claim (e.g., different player counts or play times for
the same game):

- Record the disagreement in an `## Open tensions` section
- Cite BOTH sides with their source IDs: "Source 1 says X; Source 2 says Y"
- Do NOT average or pick a side
- Do NOT omit one version to appear consistent

## Provenance Footer

End each content page with:

```markdown
## Sources
- Source N: Author Name, URL
```

Use the author and source URL from `.sources.json`.

## Confabulation Rule

**Do NOT add facts from training data.** Every claim on every page must trace to a
source article in `_inbox/` or `_archive/`. If a source article does not mention a
detail, the page must not mention it either. When uncertain, mark with `[unverified]`
or omit.
