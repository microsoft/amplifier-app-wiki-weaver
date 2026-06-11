# Wiki-Weaver Pipeline — Systems Design

Status: living design (north star). Supersedes ad-hoc iteration.

## 1. Outcome (definition of done)

A **code-first, code-driven** pipeline (agents only where judgment beats code) that turns
raw, rich sources into the **most connected, organized, error-free knowledge corpus** —
a dependable, mineable **memory system that scales over time**. Concretely the corpus must be:

| Outcome word | Operational meaning | Measured by |
|---|---|---|
| error-free | validator clean every converge | 0 broken links / 0 orphans / 0 uncited |
| connected | concepts cross-link; no islands | cross-link density up, orphans = 0 as corpus grows |
| organized | one concept per page; no dup/fragment | merge-correctness; 0 `*-2.md`; dedup by content hash |
| mineable | pre-digested synthesis + provenance | synthesis quality; `sources:` accrual; cite-or-don't-claim |
| scalable | per-node context stays bounded as corpus grows | per-node request size flat, not linear in page count |
| dependable | never lies about its own state | real ledger only on real convergence; no confabulation |

## 2. Current state (verified, scale-test-3 + smokes)

- **Data integrity: SOLID.** No confabulated ledger (3-layer guard), real `logs_dir`, content-hash dedup, no false archive even through 2 hard kills.
- **Merge/compounding: WORKS.** `sources: [1] → [1,2]`, pages grow in place, zero duplicate concept pages, genuine synthesis, contradictions in `## Open tensions`.
- **Single-article convergence: WORKS** (per-source convergence rubric).
- **BLOCKER — multi-article convergence:** A2 never converges. Root cause (CI raw-request analysis): the `validate → feedback` edge carries the *label* "fail" but **drops the failure payload**. The feedback node reads only `.ai/assessment.md` (scored 5/5 "perfect" — assess deliberately ignores structure) so it free-writes generic guidance; the broken link survives every cycle. Amplified by ingest empty-output under context load ("read every page each cycle" → exhausts loop on reads → empty final → slow fallback).

## 3. Target architecture (convergent across CI analysis + resolver-dot-graph + foundation-expert)

All three sources independently prescribe the **same** shape — we are already ~90% aligned:

- **Code owns control + determinism; agents own judgment.** Loop engine = code (attractor engine ✓). Deterministic checks = code (`validate` tool_command ✓). Agents only for irreducible judgment: `ingest` (synthesis), `assess` (quality verdict), `feedback` (remediation synthesis).
- **Structured artifact hand-off via files; each downstream node is *given* the specific upstream output it must act on** — injected into the node's *instruction*, not buried in shared context, and **never via dotted context keys** (`context.x.y` is silently dropped in box-node prompts — confirmed in resolver-dot-graph). Nodes communicate via artifact files + targeted prompt injection, not session transcripts.
- **Thin per-node bundles:** each node gets only its role + I/O schema as static context; run-specific data (this source, the validator failures, prior feedback) is injected per-run. Never dump the whole corpus into any node.

| Node | Type | Why | Receives |
|---|---|---|---|
| ingest | agent | synthesis/judgment | source + (focused slice of touched/broken pages) + prior feedback |
| validate | **deterministic** | rules, must be auditable/cheap/reproducible | wiki dir → writes structured result |
| assess | agent | quality judgment (per-source); MUST NOT relitigate structure | touched pages + validate result (read) |
| feedback | agent | remediation synthesis | **the validator's exact failures** (the fix) |
| check | gate (code) | route converged vs refine | assess `preferred_label` |

## 4. The core fix (design-level, not a band-aid)

**Plumb the validator's structured failures into the remediation path.** This is the one
gap between us and the proven `convergence-factory.dot` pattern.

1. `validate` writes its structured result to a known file (it already emits `output.txt`/`failure_reason`).
2. The engine injects that file's content **verbatim into the `feedback` node's instruction**:
   *"The deterministic validator FAILED with: `<failures>`. Your #1 job is remediation guidance
   that fixes EACH listed item (e.g. the broken wikilink `…`)."*
3. The **refine-`ingest`** prompt likewise reads the latest validator failures and must resolve
   every broken/orphan **before** anything else — don't rely on feedback prose to relay it.
4. Do **not** feed assess's "perfect convergence" optimism into the fail branch.
5. `ingest`: add `goal_gate` (tool-only/empty final = retry with "emit your summary now"), and
   read a **focused slice** (pages touched by this source + broken/orphan targets), not "every page" —
   this fixes empty-output-under-load *and* the scalability requirement.

## 5. CI instrumentation discipline (standing practice, every run)

After each run, use `context-intelligence:graph-analyst` to review the **raw per-node requests**:
is each node receiving the right context — present, focused, not bloated? Diagnose
**plumbing vs behavior** before changing prompts. (This is how we found §4: the failure was a
missing input, not a bad agent.) Per-node request size is a first-class scale metric.

## 6. Eval harness evolution (what we measure)

**Keep:** structural validator (0 broken/orphan/uncited); data-integrity (real ledger only on real
convergence, dedup, no confabulation); merge-correctness (`sources:` accrual, no dup concept pages).

**Add:**
- **Multi-article convergence:** a cluster of N overlapping sources ALL converge + archive (the real value).
- **Recovery / loop-closes:** a *deliberately injected* broken link is repaired within the cycle budget (proves feedback→ingest actually closes).
- **Connectedness:** cross-link density rises and orphans stay 0 as the corpus grows.
- **Contradiction-surfacing:** where sources disagree, an `## Open tensions` section exists (not averaged).
- **Context-efficiency (scale):** per-node request size stays bounded as page count grows.

**Refine / remove (counter-productive):** assess convergence must be **per-source** and must **not**
relitigate structure (validator owns it) — the old "all 6 scenario sources present" criterion was the
bug; keep it retired. Reserve the scenario eval rubric for *final grading*, never per-cycle convergence.
