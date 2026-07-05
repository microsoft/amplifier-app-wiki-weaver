# Design: Wiki Dashboard + Obsidian-Value + `.dot` Leverage

**Status:** Draft for council review · **Date:** 2026-06-27
**Repos affected:** `microsoft/amplifier-app-wiki-weaver` (primary), `microsoft/amplifier-app-repo-weaver` (consumer)
**Explicitly out of scope:** `microsoft/amplifier-bundle-attractor` (see §9, Parked)

---

## 1. Goals (named, prioritized)

This effort serves **two product goals plus one architectural goal**. Naming them separately (per the council's intent-keeper) is load-bearing — each decision below traces to one of them.

- **G1 — Navigable wiki (PRIMARY).** Any wiki-weaver corpus gets a browsable, self-contained HTML dashboard and Obsidian-grade navigation value (backlinks, link-graph, tags, provenance) baked into the corpus **data** — so that **agents querying via tools** and **humans without Obsidian** both benefit. The agent-facing value is the priority half of G1.
- **G2 — Clean generic/domain boundary (PRIMARY, enabling).** `wiki-weaver` stays domain-blind and owns everything generic to *any* wiki; `repo-weaver` is a thin git-domain producer/enricher. Locking this boundary **before announcement** is the reason for doing the structural work now.
- **G3 — Attractor/Resolve composability (SECONDARY, architectural).** Each command exists as a per-command `.dot` pipeline runnable as CLI / Resolve resolver / lib / tool module. This is a *convention goal*, not a user goal; it is satisfied at the cheapest level that a real consumer needs (see §4, §9).

**Non-goals (now):** a theme gallery; an attractor-engine registry; a graph-visualization view in the dashboard; migrating domain content out of repo-weaver.

---

## 2. The wiki-weaver / repo-weaver boundary (confirmed by evidence)

Investigation (file:line) shows the generic corpus machinery **already lives in wiki-weaver** — there is almost nothing to "migrate down." This section records the confirmed boundary so it can be locked.

| Artifact | Owner (creates/writes) | Generic or Domain | Evidence |
|---|---|---|---|
| `_inbox/` (ingest staging) | **wiki-weaver** (repo-weaver *produces into* it) | **Generic** | `wiki_weaver/lib.py:42,98,693` · `repo_weaver/weave.py:714,918` |
| `_archive/` (source **content** after convergence) | **wiki-weaver** | **Generic** | `lib.py:43,816` · `ingest_archive.py:110-156` |
| `_failed/` (non-converged) | **wiki-weaver** (repo-weaver reads for retry) | **Generic** | `lib.py:703,760,841` · `repo_weaver/weave.py:395-466` |
| `.sources.json` (ledger: id/hash/ingested/url/author) | **wiki-weaver** | **Generic** (structure); values may be domain | `lib.py:161,377-405` (zero repo-weaver refs) |
| `.processed.jsonl` (run ledger) | **wiki-weaver** (repo-weaver greps for failure class) | **Generic** | `lib.py:41,145-147` · `weave.py:284-303` |
| `.runs/` (ingest run logs) | **wiki-weaver** | **Generic** | `ingest_archive.py:42-64` |
| `policy/schema.md` (frontmatter contract) | **wiki-weaver slot**; repo-weaver supplies content | **Generic slot, Domain content** | `policy.py:90-105` · `weave.py:1093,1112-1116` |
| `.repo-weaver.json` (registered repos) | **repo-weaver** | **Domain** | `weave.py:56-77,1119-1128` |
| `.replay-progress.json` | **repo-weaver** | **Domain** | `weave.py:165,193-206` |
| `*-changes.md` digest + `repos:`/url/author derivation | **repo-weaver** | **Domain** | `materialize.py:261-323,613-821` |

**`sources` ≠ `archive`** (answering the open question): `.sources.json` is the **ledger** (provenance + dedupe state, no content); `_archive/` holds the actual **source content files** moved from `_inbox/` on convergence. Both are wiki-weaver's, written at the same moment.

**Conclusion:** the boundary is already ~correct. `repo-weaver` correctly owns only git-domain artifacts; `wiki-weaver` owns all structural machinery. The only live lever is **schema content** (§7). No structural directory needs to move between repos — but the **relocation under a hidden `.wiki/` root (§6) is a wiki-weaver change** to its own path constants, which repo-weaver follows by referencing the same literals.

---

## 3. Decision — naming

`dashboard` as a bare noun is ambiguous about its action. **Rename to the verb form `build-dashboard`**, applied consistently:

| Surface | Name |
|---|---|
| CLI | `wiki-weaver build-dashboard` / `repo-weaver build-dashboard` |
| Pipeline | `build-dashboard.dot` |
| Resolver action label | "Build Dashboard" |

Rationale: wiki-weaver's commands are flat verbs (`ingest`, `lint`, `ask`); a flat verb fits the cadence. (Noun-group `dashboard build` is the alternative if more artifact subcommands appear; flat verb is cleaner today.)

---

## 4. Decision — dashboard generation & leverage (G1, G3)

Dashboard generation is **deterministic** (parse frontmatter → render HTML; no LLM). Per the leverage DRY rule:

- **Logic home = the wiki-weaver lib.** A clean public function (e.g. `build_dashboard(corpus, out, theme=...)`).
- **L4 CLI** — `wiki-weaver build-dashboard` (thin wrapper over the lib). Always built.
- **L1 `.dot`** — a single `parallelogram` node shelling the CLI + a `.resolver.yaml` sidecar. Built because G3 + repo-weaver compose it.
- **L2/L3** — only if a real consumer (an importing app / an agent tool) appears. Not built speculatively.

**Decomposition rule (council-affirmed, unanimous):** the graph encodes *decisions and gates, not statements*. We do **not** explode linear deterministic logic into many `.dot` nodes. Multi-node `.dot` is reserved for real routing/retry/goal-gate/human-gate/parallel/LLM-mix boundaries (e.g. the existing weave pipeline), not the dashboard.

**repo-weaver composition = CLI shell-out (no vendoring, no registry).** repo-weaver's `build-dashboard.dot` has a `parallelogram` node that shells `wiki-weaver build-dashboard`, passing repo-enrichment via the engine's existing `context.*`-in / `outputs=`-out contract. This needs neither a vendored copy nor an engine change.

---

## 5. Decision — generic/domain seam (G2)

`wiki-weaver`'s `build-dashboard` provides **mechanisms only** and never references `repo`/`owner`/`github`:

1. **Group-by-a-named-frontmatter-field** — default `type`; a consumer (repo-weaver) sets `repos`.
2. **Opaque `extensions` passthrough** — emitted as `--repo-*`-style CSS vars for the consumer's namespace.
3. **Enrichment slot** — inlines an optional consumer-supplied **CSS fragment** (see §10 hardening).

`repo-weaver` supplies the **policy**: the `repos` grouping config + a CSS enrichment fragment (owner→repo tree styling, GitHub link affordances) + `extensions["repo-weaver"]` data.

**Must-design (council gap — user-advocate):** the **default view for a non-repo corpus** (no enrichment) must be explicitly good — a chosen default grouping, a useful landing, per-page backlinks surfaced — not an empty slot. "Degrades gracefully" is a deliverable, not an assertion.

---

## 6. Decision — Obsidian-value in data + the agent tool contract (G1 priority half)

A **generic, domain-blind, weave-time INDEX step** (in wiki-weaver) materializes from the `[[slug]]` links + frontmatter it already emits:

- Indexes (v1): backlink · link-graph · tag · properties · **alias map**. (Headings/outline index dropped — no v1 consumer; add when one exists. — cranky)

These feed **both** the dashboard (humans w/o Obsidian) **and** an **agent query-tool surface** — a first-class, tested deliverable (the prior council's biggest gap):

- **Tools (v1):** `wiki_backlinks(page)`, `wiki_graph_neighbors(page)` (immediate neighbors only — no `depth` param until a consumer needs it), `wiki_tags(tag)`, `wiki_properties(page)`. `wiki_resolve_citation(page, n)` is **in v1** — §8 makes citations first-class (confirmed). *(Signatures + return shapes pinned in the pre-build spec stub, §12.)*
- **Minimal versioning (not a framework):** each index carries a single integer `schema_version`; readers `assert schema_version == EXPECTED` and raise a **named** error otherwise. No registry / migration / validator framework in v1. — cranky/crusty
- **Staleness = signal + obligation:** the index stamps corpus state at build (commit, or `max(mtime of *.md)` + content hash). A query whose corpus is newer returns results flagged `stale: true` + reason, **and** the documented consumer contract is *surface the staleness, do not silently trust* — the dashboard shows a banner; an agent tool's result carries the flag. Detection without obligation is only half a contract. — crusty F2
- **Alias + broken-link correctness (must-test):** unresolved `[[slug]]` → structured broken-link report (not silently dropped, not a build abort). Alias resolution detects cycles of **any length** (A→A **and** A→B→A) → a named `CycleDetectedError` listing the chain, in finite time, never a hang. — tester-breaker F1. Duplicate alias → deterministic tie-break by an **explicit rule (alphabetical by slug)**, applied identically by every reader (tool, dashboard, indexer), + warning. — tester-breaker F7
- **Round-trip test uses a committed fixture corpus** with *known* backlinks/tags/alias-resolutions/broken-links **plus** the cross-alias cycle fixture (`alpha`↔`beta`); asserts against ground truth, not merely "did not throw." — tester-breaker F6

**Obsidian honesty (user-advocate F4):** the *human-in-Obsidian* value is the `[[slug]]` links Obsidian follows natively (real, already true). The richer indexes live under `.wiki/` (hidden from Obsidian's panels). We document this plainly; we do not claim Obsidian's backlink/graph panels are powered by our indexes.

---

## 7. Decision — schema content ownership

The `policy/schema.md` **slot is wiki-weaver's** (generic; wiki-weaver even ships an LLM schema-*designer*). `repo-weaver` deliberately bypasses the designer (`wiki-weaver init --plain`) and ships its own git-fit schema. **Recommendation: keep repo-weaver's schema as domain policy** — it *is* policy (the `repos:`/module/capability/concept taxonomy is git-specific). No migration; the slot is already generic. (Promoting the git schema to a wiki-weaver-selectable preset is possible later but is not warranted now.)

---

## 8. Decision — directory hygiene + Obsidian readiness (G1, G2)

Goal: the human/Obsidian vault view is **just the wiki pages + the inbox**; all machinery is hidden.

- **Relocate machinery under a single hidden `.wiki/` root** (wiki-weaver-owned change to its path constants `lib.py:41-44,161`; repo-weaver follows the same literals): ledgers, `.runs`, `policy/schema`, `_archive`, `_failed`, the new indexes, dashboard config, and an `.obsidian/` template.
- **`inbox/` stays human-visible** (the drop zone — humans add content; tools pick it up).
- **Provenance is first-class (SETTLED).** In-page citations are **clickable**, resolving to their source via `wiki_resolve_citation` + dashboard click-through. Source-digests live in a **single visible `_sources/` folder** (one collapsed folder, not 380 loose files) so the link is **also navigable natively in Obsidian** — closing the human-in-Obsidian citation dead-end (user-advocate). Everything *else* machine-only (ledgers, `.runs`, `policy/schema`, `_failed`, indexes, config, Obsidian template) goes hidden under `.wiki/`. Do **not** double-hide `_sources/`.
- **Obsidian template + `.gitignore`:** ship a curated `.obsidian/` (sensible graph/appearance/enabled-plugins) for a clean out-of-box open; `.gitignore` the macOS `._*` junk and user-specific `workspace.json`; ensure `.wiki/` is in Obsidian's excluded folders so it never indexes machinery as phantom notes.

**This migration is its own safety-gated PR (§11), not bundled with the dashboard build.**

---

## 9. Parked — attractor `.dot` registry (G3, deferred)

Proposed-then-parked (council near-unanimous): a `attractor.pipelines` entry-point tier in the engine so `dot_file="@wiki-weaver:build-dashboard"` resolves an installed package's `.dot` by name. **Deferred** because: CLI shell-out already composes pipelines with no vendoring and no engine change; it's a shared-infra change for one consumer (violates the two-implementation rule); and its failure modes (version skew, name collision, not-installed) are silent.

**Revisit trigger:** a *second* consumer + demonstrated CLI-composition pain. If revisited, prefer a published package lib function (`wiki_weaver.pipeline_path("build-dashboard")`) over an engine resolver tier, and require the version-skew / collision / not-installed tests the council demanded.

---

## 10. Theming (MVP — trimmed per council)

- **Ship:** one `.wiki-dashboard/theme.json` (branding + ~18 design tokens + opaque `extensions`) **+** an optional `custom.css` appended verbatim (wins; cannot crash the build). Default aesthetic **"Almanac"** — warm paper light, serif body / sans chrome / mono code, locked ~68ch reading measure, the "ledger rail" accent spine; type-badges carry the only saturation.
- **Defer (no current consumer):** `theme.local.json`, the 4 named presets, and WCAG **auto-correct**.
- **Hardening (tester-breaker):** the token cascade uses explicit `None`-checks (not truthiness — `0`/`""`/`false` are valid values); **unknown keys warn** (don't silently swallow); contrast is **validated and reported loudly**, not silently mutated. "Locked" legibility guardrails are *warn-and-document*, with `custom.css` as the explicit override path (user-advocate F5).

---

## 11. Sequencing (value-first; council-mandated order)

1. **wiki-weaver v1 (real value first):** index step + versioned/tested agent tool surface (§6) + generic `build-dashboard` (lib/CLI/`.dot`/resolver) with Almanac default, CSS-only escaped enrichment slot (§10, §10-hardening), and the designed non-repo default view (§5).
2. **repo-weaver consumes** via CLI shell-out + enrichment fragment/`extensions` (§4–5). Proves the seam end-to-end.
3. **Dir-hygiene migration** — its own safety-gated PR (§8) with the protocol below.
4. **Attractor registry** — parked (§9).

**Migration safety protocol (crusty + tester-breaker, non-negotiable for step 3):**
- Enumerate **every reader** of the old paths across *both* repos (file:line), update them first (parallel-change / expand-migrate-contract).
- Migration script is **idempotent and resumable**; **copy → verify → delete** with a completion sentinel; refuses to run with a concurrent writer (or instructs the user to close Obsidian).
- Post-migration assertions: `.wiki/` not indexed by Obsidian; no file left at both old and new paths; tool suite green on the new layout.

---

## 12. Council-required gates (must pass before "done")

From the council verdict (tester-breaker FAIL; five CONCERN):
- [ ] **Enrichment slot:** defined escaping contract; injection test (`</style><script>…` cannot escape the slot); a DOM-contract test that fails loudly when the generic shell's class names drift.
- [ ] **Agent tool surface:** written versioned schema per index; weave→query→ground-truth round-trip test; schema-mismatch → named error.
- [ ] **Index staleness/broken-link/alias:** staleness signal in responses; structured broken-link report; deterministic alias tie-break; no infinite loop on self-alias.
- [ ] **Migration:** idempotency + concurrent-writer + Obsidian-exclusion tests (§11).
- [ ] **Theming:** falsy-value, unknown-key-warn, and loud-contrast tests (§10).
- [ ] **Non-repo default view** designed and shown (§5).

---

## 13. Later — migrate the two existing wikis

Only two in-use corpora exist (this workspace's `amplifier-wiki`, and `~/dev/medium-wiki-weaver/medium-wiki`), both the author's, pre-announcement. After the structure lands, migrate both with the §11 protocol. Doing the realignment now (cheap, two corpora) is the explicit reason for the pre-adoption timing.

---

## Appendix — decisions log

| # | Decision | Verdict |
|---|---|---|
| Naming | `dashboard` → `build-dashboard` | adopted |
| Decompose | only at decisions/gates; dashboard = 1-node `.dot` | council PASS (unanimous) |
| Composition | CLI shell-out (no vendor, no registry) | council-preferred |
| Boundary | machinery already wiki-weaver's; repo-weaver = git-domain | confirmed by evidence |
| Schema | slot generic (wiki-weaver); content domain (repo-weaver keeps) | recommended |
| Indexes + agent tools | first-class, versioned, tested | promoted from gap |
| Theming | Almanac + custom.css; defer presets/local/auto-correct | trimmed |
| Provenance | keep reachable; don't double-hide | council reversal |
| Attractor registry | parked until 2nd consumer | deferred |

---

## 14. Council round-2 dispositions (amendments to the sections above)

Round-2 review of this doc: **no FAIL** (all six lenses CONCERN); cranky-old-sam: *"materially cleaner than the pre-council version… second-order residuals."* Approved **in direction**; was a decision record, not yet a build spec. Amendments:

- **§6 trims (applied above):** dropped `graph_neighbors(depth)`; dropped headings/outline index; bounded "versioned schema" to a single integer + assert; `resolve_citation` gated on §8.
- **§5 — drop the `extensions`→CSS-vars channel.** It duplicates `custom.css`. Consumers style via the enrichment slot / `custom.css` only. (cranky F1)
- **§10 — `custom.css` contrast + "locked" wording.** `custom.css` is the **documented user-responsibility escape hatch** — **not** contrast-checked (can't validate arbitrary CSS); the loud-contrast check applies to `theme.json` tokens only, and this exemption is documented, not silent. The reading measure is a **default with `custom.css` as the sanctioned override**, not "locked." (tester-breaker F2, user-advocate F6)
- **§8 — path-constant ownership (DECISION).** `repo-weaver` **imports** wiki-weaver's path constants (single source of truth) rather than re-declaring literals kept in manual sync; if import isn't feasible, the migration PR adds a contract test asserting agreement. (crusty F1)
- **§11 — migration hardening.** (a) migrate **ledger keys too** (`.sources.json`/`.processed.jsonl` may key on old paths → dedup/idempotency corruption); (b) concurrent-writer detection is **enforced** (lock/PID) + a **required** completion sentinel, not advisory; (c) explicit Obsidian `.gitignore` list (`workspace.json`, `app.json`, `graph.json`, `hotkeys.json`, `graph-analysis.json`, `._*`). (crusty F3/F4, tester-breaker F5)
- **§12 — NEW GATE: a pre-build SPEC STUB.** Before coding, pin what this doc intentionally leaves open: agent-tool signatures + each index's `schema_version` shape; the ~18 Almanac tokens (names + default hex + ledger-rail/badge palette); the CSS escaping contract (algorithm, not just the injection test); the round-trip fixture corpus; the non-repo default-view layout. Fill-in-the-spec, not redesign. (restless F5/F6, user-advocate F1/F3, crusty F5)
- **§9 — sharper revisit trigger.** Replace "demonstrated pain" with: *3+ distinct pipelines shelling wiki-weaver with differing arg patterns, OR a cross-version CLI break causing a real failure.* (crusty F7)
- **§1 — goal-naming fix.** G1 renamed to foreground the agent-query surface (its priority half); G2 is plain PRIMARY (work = confirm+lock, not migrate); add G4 "pre-announcement readiness" as the named timing constraint. (intent-keeper)

### Open items — RESOLVED (user, 2026-06-27)
1. **`custom.css` contrast** → **accepted as the documented, unchecked escape hatch** (no computed-style checking). theme.json tokens still get the loud-contrast check.
2. **`wiki_resolve_citation`** → **kept in v1.** Consequence: citations become first-class/clickable, and source-digests live in a **visible `_sources/`** folder (§8) so the link resolves in both the dashboard and Obsidian. This also closes the human-in-Obsidian provenance dead-end.
3. **Path constants** → **confirmed: `repo-weaver` imports wiki-weaver's path constants** (single source of truth); a contract test guards agreement if import proves infeasible.

**Design of record is now locked.** Only remaining pre-code step: fill the §12 pre-build spec stub.
