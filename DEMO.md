# Wiki-Weaver — Demo Runbook & Proof

A **code-first** pipeline that turns a pile of raw source articles into a **connected,
organized, error-free, provenance-tracked** wiki — the Karpathy LLM-wiki / second-brain
"compounding memory" concept, made real and repeatable. Agents do the judgment work
(synthesis, quality, remediation); code owns control, validation, archiving, and dedup.

## Demo it (any pile of articles → a wiki)

```bash
PY=/home/bkrabach/.local/share/uv/tools/amplifier/bin/python3
cd wiki-weaver

$PY -m cli doctor                       # env preflight (all green)
$PY -m cli init   runs/demo/wiki        # new empty wiki
cp ~/some_articles/*.md runs/demo/wiki/_inbox/   # drop in source material
$PY -m cli ingest --wiki runs/demo/wiki --max-cycles 5   # weave it in (one source at a time)
$PY pipeline/validate_wiki.py runs/demo/wiki            # -> PASS (exit 0)
```

Each source is ingested, structurally validated, quality-assessed, and — only when it
genuinely converges — archived with a provenance entry. Re-running is idempotent
(already-ingested sources are deduped by content hash).

## What's proven (evidence, not claims)

| Property | Evidence |
|---|---|
| **Compounds correctly** | `runs/proof/` (Karpathy cluster): `sources:` accrue `[1]→[1,2]`, pages grow in place, **0 duplicate** concept pages |
| **Generalizes (not over-fit)** | `runs/holdout/` — a *different* topic (AI-agent long-term memory): 3 articles converge, **0 orphans / 0 broken / 0 dup**, `sources:[1,2,3]` accrual, real synthesis |
| **Surfaces contradictions** | `runs/contradiction/` — a pro-RAG source vs the anti-RAG page produced an `## Open Tensions` section with **both sides + provenance**, not averaged |
| **Error-free** | structural validator (links, orphans, frontmatter, provenance) exits 0 on every convergence |
| **Dependable (no lying)** | confabulation guard: the ledger/archive are written by code *only on real convergence*; an agent cannot fake "converged" (proven through 2 hard kills) |
| **Self-heals** | inject a broken `[[link]]` → the validate→feedback→ingest loop repairs it and re-converges |
| **Robust routing** | a flaky/empty assess verdict routes to *refine* (more work), never a dead-end or a false "converged" |
| **Reliable** | `runs/final/` — 4 articles, **4/4 converged**, validator PASS |

## Architecture (code-first, agents where they earn their keep)

`ingest`(agent) → `validate`(**code**) → `assess`(agent) → `check`(**code**) → done | `feedback`(agent) → loop.
The validator's exact failures are plumbed into the feedback/refine instructions (via file),
so remediation targets the real problem. Each node is an isolated session with a thin,
focused context — ingest reads a *slice* (touched + broken/orphan pages), not the whole
corpus, so it **scales** as the wiki grows. Full design: `docs/designs/PIPELINE_DESIGN.md`.

## Honest residuals (non-blocking)

- The `assess` LLM node occasionally returns its verdict as prose instead of strict JSON.
  This is now **non-fatal** — `check` routes any non-`converged` verdict (incl. unset/prose)
  to `refine`, so it costs at most an extra cycle and never falsely converges. A deeper
  engine-level fix (`_parse_outcome` should not launder a missing verdict into success) is
  noted for upstream.
- The pipeline currently runs on `claude-sonnet-4-20250514` (retires 2026-06-15); migrate
  to a current model — it should also improve final-message (verdict) compliance.
