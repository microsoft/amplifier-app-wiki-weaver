# Pre-Build Spec Stub — Wiki Dashboard + Indexes

**Companion to:** `wiki-dashboard-design.md` (the locked design of record). This stub pins the items §12 of that doc intentionally deferred, so coding can start without guessing. **Fill-in-the-spec, not redesign.** Scope: `microsoft/amplifier-app-wiki-weaver` (generic), consumed by `repo-weaver`.

---

## 1. Index files + schema versioning

Indexes live under `.wiki/index/` (hidden). Five files, each a JSON object with a fixed envelope:

```json
{ "schema_version": 1, "built": { "max_mtime": 1750000000.0, "content_hash": "sha256:…", "weave_commit": "abc1234|null" }, "data": { … } }
```

| File | `data` shape |
|---|---|
| `backlinks.json` | `{ "<slug>": ["<slug>", …] }` — pages that link **to** the key |
| `links.json` (graph) | `{ "<slug>": { "out": ["<slug>",…], "in": ["<slug>",…] } }` |
| `tags.json` | `{ "<tag>": ["<slug>", …] }` |
| `properties.json` | `{ "<slug>": { …frontmatter k/v… } }` |
| `aliases.json` | `{ "<alias>": "<slug>" }` (post-resolution, tie-broken) + `{ "_broken": [...], "_cycles": [[…]] }` |

**Versioning contract (minimal — no framework):** a module-level `EXPECTED_SCHEMA_VERSION = 1`. Every reader does `if idx["schema_version"] != EXPECTED_SCHEMA_VERSION: raise SchemaVersionError(index, found, expected)`. Bump the int on any breaking shape change. No registry, no migration.

**Staleness:** `built.max_mtime` is the fast path. A query computes `current = max(mtime of corpus/*.md)`; if `current > built.max_mtime` → `stale = true`, reason `"corpus edited since last weave"`. `content_hash`/`weave_commit` are the precise fallback when mtimes are unreliable. **The tool always returns data + the flag; it never silently trusts and never refuses.** Consumer obligation (documented): the dashboard renders a "⚠ index N min stale — re-weave to refresh" banner; an agent tool result carries `stale: true` and callers must surface it.

---

## 2. Agent tool signatures (the query surface)

All return a common envelope `{ …result…, "stale": bool, "built": "<iso8601>" }`. All raise named errors (never silently wrong).

```python
wiki_backlinks(wiki_dir, page)            -> { "backlinks": [{ "slug", "title" }], stale, built }
wiki_graph_neighbors(wiki_dir, page)      -> { "out": [slug], "in": [slug], stale, built }   # immediate only; no depth param
wiki_tags(wiki_dir, tag=None)             -> { "tag": str|None, "pages": [{slug,title}],     # tag=None => { "tags": {tag: count} }
                                              stale, built }
wiki_properties(wiki_dir, page)           -> { "slug", "properties": {…}, stale, built }
wiki_resolve_citation(wiki_dir, page, n)  -> { "source": { "id", "slug", "path", "title", "url"? }, stale, built }
```

**Errors:** `SchemaVersionError`, `PageNotFound(page)`, `CitationNotFound(page, n)`, `CycleDetectedError(chain: list[str])`. All subclass a `WikiIndexError` base.

**Citation model (settled — §8):** a citation in page prose is an inline marker the weaver emits as a resolvable reference (the existing `(source N)` becomes a link to the source's page in the visible `_sources/` folder). `wiki_resolve_citation(page, n)` maps page+ordinal → the `_sources/` entry via `.sources.json`. Clickable in the dashboard **and** (because `_sources/` is vault-visible) natively in Obsidian.

---

## 3. Alias resolution algorithm (correctness — must-test)

```
resolve(alias):
  seen = []
  cur  = alias
  while cur in alias_decls:                 # alias_decls: alias -> target slug as declared
      if cur in seen: raise CycleDetectedError(seen + [cur])   # any-length cycle (A→A, A→B→A)
      seen.append(cur)
      cur = alias_decls[cur]
  return cur                                 # terminal slug (may be a real page or itself unresolved->broken)
```

- **Duplicate alias tie-break:** if two pages declare the same alias, the winner is **the alphabetically-first slug**; emit a warning; apply identically in tool, dashboard, and indexer (one shared function).
- **Unresolved** (`[[slug]]` / alias → no page) → recorded in `aliases.json._broken` and the per-page broken-link report; **never** aborts the build.
- **Cycles** → recorded in `aliases.json._cycles`; resolution of any member raises `CycleDetectedError` (finite time, never hangs).

---

## 4. CSS escaping contract (enrichment slot)

The enrichment slot inlines a **consumer-supplied** CSS fragment (repo-weaver → generic dashboard, across the seam). Contract:

1. **Parse** the fragment as CSS (e.g. `tinycss2`). Parse failure → **reject**, omit the slot, emit a build **warning** (build still succeeds — fail-safe).
2. **Reject** `@import`, `@charset`, and any `url()` whose target is off-origin/non-`data:` (exfiltration / overlay defense).
3. **Re-serialize** the parsed stylesheet (canonical output) — never pass raw consumer bytes. Re-serialization is the primary defense: a parsed-and-reserialized stylesheet **cannot** contain a raw `</style>`, `</STYLE>`, `</style >`, unicode-escaped `\3c/style\3e`, or `<!--`.
4. **Wrap** the sanitized output in a dedicated `<style data-wiki-enrichment>` element (its own element — never string-interpolated into another `<style>`).

> Test inputs that MUST be neutralized: `</style><script>…`, `</STYLE>…`, `</style ><script>`, `\3c/style\3e<script>…`, `@import url(https://evil/x.css)`.

**`custom.css` is different:** it is the **wiki owner's own file** (in `.wiki-dashboard/`), not a cross-seam fragment. It is appended **verbatim, trusted, unchecked** — the documented user-responsibility escape hatch (incl. contrast). Only `theme.json` tokens get the loud-contrast check.

---

## 5. Almanac theme tokens (default, light) — the ~18 exposed

```css
:root {
  /* color — warm paper */
  --wiki-bg:#FBF9F4; --wiki-sidebar:#F3EEE3; --wiki-card:#FFFFFF; --wiki-subtle:#F1ECE0;
  --wiki-border:#E4DCCB; --wiki-border-strong:#D2C7B0;
  --wiki-text:#23211C; --wiki-text-secondary:#6B6655; --wiki-text-muted:#938C7A;
  --wiki-accent:#136F63; --wiki-accent-hover:#0E574E; --wiki-accent-tint:#E3EFEB;
  /* typography */
  --wiki-font-reading:Charter,"Bitstream Charter","Iowan Old Style","Source Serif 4",Georgia,Cambria,serif;
  --wiki-font-ui:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --wiki-font-mono:"JetBrains Mono","SF Mono","Cascadia Code",ui-monospace,Menlo,Consolas,monospace;
  --wiki-font-size:16px; --wiki-line-height:1.65; --wiki-content-width:68ch;
  /* space / shape / motion */
  --wiki-space-unit:8px; --wiki-radius:8px; --wiki-transition:150ms;
}
```
Reading body uses 17px serif; headings derive from `--wiki-font-size × 1.2^n`; spacing from `--wiki-space-unit` multiples; one shipped shadow. **Derived tokens are not exposed.**

**Dark default ("Almanac Night"):** bg `#1A1814` · sidebar `#211E18` · card `#232019` · subtle `#2A261E` · border `#342F26`/`#463F32` · text `#ECE6D8`/`#A39B88`/`#7C7565` · accent `#46B3A3`/`#5FC8B8`/tint `#1F2C28`. `defaultScheme: auto`.

**Type-badge palette (the only saturation; equal-luminance, text-on-tint):**

| type | text | tint |
|---|---|---|
| module | `#3A5BA0` | `#E7EDF7` |
| capability | `#2F6F4F` | `#E6F0E9` |
| concept | `#1F6A6A` | `#E3EFEB` |
| decision | `#7A4B9C` | `#F0E9F4` |
| source | `#9A5B2E` | `#F4EADD` |

**"Ledger rail" (the signature):** a 1px hairline gutter left of the reading column; `h2`/`h3` get a 2px accent tick on that rail (doubles as anchor target); the active sidebar item gets a 2px accent left-bar on the same vertical axis. Accent never fills a surface.

---

## 6. Round-trip fixture corpus (committed test data)

`tests/fixtures/wiki-min/` — 6 pages, frontmatter + body:

| file | frontmatter | links / cites |
|---|---|---|
| `alpha.md` | `type: concept`, `tags: [x]`, `aliases: [a-overview]` | `[[beta]]`, `[[gamma]]` |
| `beta.md` | `type: concept`, `aliases: [b-overview]` | `[[alpha]]`; cites `(source 1)` |
| `gamma.md` | `type: concept`, `tags: [x, y]` | — |
| `delta.md` | `type: note` | `[[nonexistent]]` (broken) |
| `loop-a.md` | `aliases: [loop-b]` | — |
| `loop-b.md` | `aliases: [loop-a]` | — |

**Ground truth (`expected.json`):**
- backlinks: `{alpha:[beta], beta:[alpha], gamma:[alpha]}`
- broken links: `[{from:"delta", target:"nonexistent"}]`
- tags: `{x:[alpha,gamma], y:[gamma]}`
- alias resolution: `a-overview→alpha`, `b-overview→beta`
- cycles: `wiki_resolve_citation`/alias resolve on `loop-a`/`loop-b` → `CycleDetectedError(["loop-a","loop-b","loop-a"])`
- citation: `wiki_resolve_citation("beta", 1) → source id 1`

Tests assert tool outputs **equal** ground truth (not merely "did not throw").

---

## 7. Non-repo default view (generic corpus, no `repos`)

For a pure knowledge wiki (no `repos` frontmatter, enrichment slot empty), the default must be genuinely useful:

- **Sidebar:** group by `type` (the default group-by-field) → collapsible sections with counts; if no `type` present, flat alphabetical.
- **Landing (home):** the corpus `overview`/`index` page if present; else a generated home — stat cards (pages · tags · links), a **Recently updated** list (by `last_updated`), a tag cloud, and a **pages-by-type** bar chart (the generic analogue of repo-weaver's pages-by-owner).
- **Page view:** metadata header (type badge · `last_updated` · tags · backlink count), rendered body, a **Linked from** (backlinks) panel, and an **On this page** outline computed from the page's headings on render (no stored outline index — §6 trim).

repo-weaver's view is this same shell with `group_by=repos`, the owner→repo tree, GitHub links, and pages-by-owner — supplied via the enrichment slot + `extensions`.

---

## Build-readiness checklist

- [ ] Index writers emit the §1 envelope; readers assert `schema_version`.
- [ ] Five agent tools (§2) with named errors + the round-trip test (§6) green.
- [ ] Alias algorithm (§3) incl. any-length cycle detection — fixture green.
- [ ] CSS escaping (§4): the five injection inputs neutralized; `custom.css` verbatim/trusted.
- [ ] Almanac tokens (§5) + ledger rail + badge palette; theme.json contrast check loud.
- [ ] Non-repo default view (§7) rendered against the fixture; screenshot attached to PR.
