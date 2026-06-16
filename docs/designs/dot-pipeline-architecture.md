# wiki-weaver — Target Architecture: `.dot` Pipelines over a Thin Lib

**Status:** Design locked (2026-06-15). Verified against `amplifier-bundle-attractor`
and `amplifier-resolver-dot-graph`.
**Supersedes/extends:** the forward-looking parts of `evolution-plan.md` and
`PIPELINE_DESIGN.md` — this is the agreed end-state and the migration order to reach it.

---

## TL;DR

The **`.dot` pipelines are the product.** Everything else is a surface around them:

- **lib** — a thin attractor shim (`parse_dot → PipelineEngine.run()`). Not an orchestrator.
- **CLI** — a thin `click` wrapper over the lib (`wiki-weaver init/ingest/ask/lint`).
- **skill** — teaches an AI agent to drive the CLI via bash.
- **bundle** (`amplifier-bundle-llm-wiki`) — the prompt-driven, in-session, zero-engine
  surface. Kept. Its `/wiki-ingest` mode optionally shells out to the CLI.

The four operations (`init`, `ingest`, `ask`, `lint`) become `.dot` pipelines built from
**typed nodes** — LLM box = judgment, tool (`parallelogram`) = exact/0-LLM, conditional
(`diamond`) = routing, subgraph (`folder`) = reuse — sharing reusable subgraphs
(`synthesize`, `retrieve`, `heal-page`). Migration is **incremental and eval-gated.**

See: `wiki-weaver-architecture.png` (surfaces + consumers) and
`wiki-weaver-pipelines.png` (pipeline internals in real node vocabulary).

---

## Why this shape

1. **Drop-in composability.** Anywhere the attractor engine already exists
   (`amplifier-app-cli` + `amplifier-bundle-attractor`, `amplifier-resolver-dot-graph`),
   you run the `.dot` files directly — *zero wiki-weaver code*. The lib only matters for
   codebases where attractor is **not** wired up.
2. **Mechanism, not policy.** The lib is pure mechanism (run a graph). All logic/policy
   lives in declarative `.dot` — editable, forkable, reusable without touching code.
3. **Two valid altitudes, both kept.** The prompt-driven bundle (no engine, human-in-loop)
   and the engineered pipelines (autonomous, eval-gated) are *comparable but different*
   expressions of the same pattern. Neither absorbs the other.

---

## Verified engine facts (the foundation this rests on)

Attractor `.dot` pipelines are a typed handler system; **half the node types make zero LLM
calls.** (Source: `amplifier-bundle-attractor/docs/DOT-SYNTAX.md`,
`PIPELINE_AUTHORING_GUIDE.md`; real pipelines in `amplifier-resolver-dot-graph`.)

| Shape | Handler | LLM? | Role |
|---|---|---|---|
| `box` | codergen | **yes** | LLM agent session w/ tools (the *only* LLM node) |
| `parallelogram` | tool | no | shell command, captures stdout, `parse_json`, `tool.last_line` routing |
| `diamond` | conditional | no | context-based routing |
| `component` / `tripleoctagon` | parallel / fan-in | no | concurrent fan-out / join (`join_policy`) |
| `folder` | pipeline | no | invoke a sub-pipeline `.dot` (`dot_file=`, `outputs=`) |
| `hexagon` | wait.human | no | human gate |
| `Mdiamond` / `Msquare` | start / exit | no | lifecycle |

- **Flow control:** edge `condition="context.tool.last_line=..."` / `outcome=...`;
  `report_outcome(preferred_label, context_updates)`; convergence via
  `retry_target` + `max_retries`; `loop_restart`.
- **Subgraphs:** `folder` node → `dot_file="subgraphs/x.dot"`, parent context cloned in,
  only `outputs=` keys merged back. A `subgraphs/` library is idiomatic.
- **Runtime shim is thin:** `parse_dot(src) → apply_transforms → validate_or_raise →
  PipelineEngine.run()` (≈6 lines for LLM-only; ≈50 lines of `register_spawn_capability`
  for tool-bearing sessions). `prepare()` once, `create_session()` per run.
- **Determinism in the graph is the documented discipline:** *exact computation → tool
  node, judgment → box node.* (`resolve_validated.dot` is 5/14 tool nodes.)

This is what corrected the original misconception that `.dot` nodes are LLM-only — they
are not, so all of wiki-weaver's deterministic orchestration can live *in the graph*.

---

## Architecture

### Surfaces & consumers (`wiki-weaver-architecture.png`)

| Consumer | Path |
|---|---|
| Attractor-native host (app-cli+attractor, resolver-dot-graph) | runs the `.dot` **directly** — no lib |
| Other codebase (no attractor) | `import` the **lib** → it loads + runs the `.dot` |
| You / an AI agent | `bash` → **CLI** → lib |
| In an Amplifier session | **bundle** `/wiki-*` modes; `/wiki-ingest` shells to the CLI if installed |

### Inside the pipelines (`wiki-weaver-pipelines.png`)

`ingest.dot` exemplar: `start → scan-inbox (tool: list · hash-dedup · text-sniff) →
route (diamond: text? binary? dup?) → synthesize (folder subgraph) → validate (tool) →
converged? (diamond)` → loop via `retry_target` or dispose to `_archive`/`_failed`
(tool moves) → `exit`. `init`/`ask`/`lint` share the skeleton, swapping subgraphs.

---

## The four pipelines

| Pipeline | Does | Dominant nodes |
|---|---|---|
| `init.dot` | design + scaffold project policy (schema, publish target, viewer) | box (design) + tool (scaffold files) |
| `ingest.dot` | drain `raw/`; per source route → synthesize → validate → converge/dispose | tool + diamond + `synthesize` subgraph |
| `ask.dot` | answer a query grounded in the wiki | `retrieve` subgraph + box (answer) |
| `lint.dot` | health check | tool (orphans, broken refs — structural) **+** box (contradictions, stale — semantic) |

`lint` deliberately **splits across the discipline line**: structural checks are tool
nodes; semantic checks (the Finding-2 cross-page-tension class) are box nodes.

## Shared subgraphs (reusable bricks)

| Subgraph | Purpose | Reused by |
|---|---|---|
| `synthesize.dot` | write/heal a page from a source (the reasoning core) | `ingest`; runnable standalone |
| `retrieve.dot` | select the pages relevant to a query/source | `ask`, `ingest`, `lint` |
| `heal-page.dot` | converge a single page to validity | anywhere a page is written |

Rule: a subgraph earns its seam only with ≥2 consumers. Don't over-shard.

---

## The lib (minimal)

**Public API** — concept-level, narrow, hides the engine:

```
init(wiki_dir, ...)              # run init.dot
ingest(raw_dir, wiki_dir, ...)   # run ingest.dot
ask(wiki_dir, query, ...)        # run ask.dot
lint(wiki_dir, ...)              # run lint.dot
```

**Internals:** the thin shim (`parse_dot → apply_transforms → validate → PipelineEngine.run()`)
plus registration of the handful of custom tools the graphs call that aren't plain shell.
**It is NOT an orchestrator** — the graphs orchestrate. The moment the lib starts exposing
engine knobs, it has stopped being "drop in the wiki-weaver concept."

## Deterministic tool-node inventory (exact work, 0 LLM)

| Op | Node | How |
|---|---|---|
| scan `raw/` inbox | `parallelogram` | list + emit candidate paths |
| content-hash dedup | `parallelogram` | helper: `wiki-weaver dedup` (non-trivial → small testable CLI subcommand, not inline bash) |
| text-sniff (UTF-8 / NUL) | `parallelogram` | helper |
| file disposition `_archive`/`_failed` | `parallelogram` | `mv` / helper |
| structural validate (orphans, broken refs, schema) | `parallelogram` | helper: `wiki-weaver validate` (the deterministic checker as a tool the graph calls) |
| routing (text/binary/dup, converged?) | `diamond` | `condition=` on `tool.last_line` |

**Key nuance:** non-trivial deterministic logic stays as **small, testable helper
scripts / CLI subcommands** that the tool node invokes — *not* inline bash, *not* an LLM.
This keeps the lib minimal (graph orchestrates; helpers are callable tools) while honoring
"don't move exact work to the LLM."

---

## Migration plan (incremental, eval-gated)

The existing **eval harness is the gate**: claim-retention, cross-page-tension probe, and
the synthesis graders. *No `.dot` replaces a code path until it scores ≥ the path it
replaces.* This turns an ambitious rewrite into a measured, reversible, one-step-at-a-time
transformation.

1. **First pass — lib/CLI split.** Extract engine logic out of `cli/wiki_weaver.py` into an
   importable package behind a thin `click` `cli.py`. Behavior identical, suite green. *No
   `.dot` change yet.* (The baseline everything peels from.)
2. **`synthesize.dot`.** Formalize the existing per-source synthesis as a standalone
   subgraph the lib calls. Gate: synthesis graders ≥ baseline.
3. **`ingest.dot`.** Re-express `cmd_ingest`'s drain / dedup / route / dispose as the ingest
   pipeline (tool + diamond nodes) wrapping `synthesize`. Gate: claim-retention +
   cross-page-tension ≥ baseline on a re-ingest.
4. **`ask.dot`, `lint.dot`, `init.dot`.** Peel each, eval-gated; `retrieve`/`heal-page`
   subgraphs emerge here.
5. **Retire** the superseded Python orchestration as each `.dot` proves out.

---

## Where this lives (open decision)

**Recommendation:** co-package DTU-style into one domain repo — `src/` lib + `cli.py` +
the `.dot` pipelines + the bundle, where `/wiki-ingest` shells to the CLI (precedent:
`amplifier-bundle-digital-twin-universe`, `amplifier-bundle-context-intelligence`). This
gives one source of truth for the pattern (kills drift) and serves CLI, lib, `.dot`, and
in-session users from one home.

**Condition that flips to standalone:** if wiki-weaver's identity is a *general*
content→wiki engine used well beyond the llm-wiki domain. Until decided, work continues in
the `wiki-weaver` repo and the design moves with the code.

---

## Risks / cautions

- **Don't graph-maximize.** Exact work → tool node, but keep non-trivial deterministic
  logic as testable helper scripts the node calls. Goal is composability, not graphs for
  their own sake.
- **Versioned `.dot`↔lib contract.** The lib loads *named* `.dot` files through a documented
  context-key I/O. Editing a `.dot` (which resolver-dot-graph / app-cli also run) must not
  silently break the lib.
- **Narrow lib API.** `init/ingest/ask/lint` only; hide engine internals.
- **Eval gate is non-negotiable.** No `.dot` replaces code until it scores ≥ baseline.
