# Scenario 01 — Rubric & Grading

This rubric is used **twice** — deliberately the same artifact (DRY, to avoid
the prompt/validator drift anti-pattern):

1. As the wiki-weaver pipeline's **`assess` gate** — decides `converged` vs
   `refine` on each ingest cycle.
2. As the eval's **grader** — scores the final wiki and the A/B answers.

If you change the bar here, you change both the loop's exit condition and the
eval. That is the point.

---

## Tier 1 — Structural graders (code, cheap, objective, run first)

Deterministic checks over the produced `wiki/` directory. These ARE the
pipeline's `validate` node. A wiki failing Tier 1 never reaches Tier 2.

| id | check | pass condition |
|---|---|---|
| S1 link-integrity | every `[[wikilink]]` resolves to a page or an explicit stub | 100% resolve |
| S2 no-orphans | every page has ≥1 inbound link (except index/overview) | 0 unexpected orphans |
| S3 frontmatter | every page has valid frontmatter (title, type, sources[], last_updated) | 100% valid |
| S4 entity-coverage | the 9 canonical entities in `ground-truth.md §A` each have exactly one page | 9/9, no duplicate-concept pages |
| S5 source-provenance | every claim page cites ≥1 source article id | 100% cite a source |

Reported as raw counts + percentages (no judgment). Cheap to run; gives an
objective floor before spending LLM judgment.

---

## Tier 2 — Quality rubric (LLM judge, only on Tier-1 survivors)

Judge scores each dimension 1–5 against `ground-truth.md`. **Judge must be a
different model/prompt than the one that built the wiki** (grader independence).
Each score must cite evidence (page + quote).

| dim | what it measures | 5 = | 1 = |
|---|---|---|---|
| Q1 synthesis | knowledge is compiled & cross-linked, not dumped per-source | spanning concepts merged into single coherent pages | N near-duplicate per-article pages |
| Q2 contradiction-handling | the §C tensions + §N2 fork discrepancy are **surfaced**, not averaged | all 4 tensions + fork discrepancy explicitly flagged with both sides | tensions silently resolved or missed |
| Q3 merge-correctness | §A entities merged correctly; distinct gaps (C1) kept distinct | clean merges, C1 distinction preserved | over-merged (C1 conflated) or under-merged |
| Q4 no-confabulation | no claims absent from sources; the "RAG is dead" strawman NOT asserted | zero confabulations | asserts RAG is dead, or invents facts |
| Q5 provenance-fidelity | claims trace to the right source | accurate attributions | mis-attributed claims |

**Convergence gate (pipeline `assess`):** `converged` iff Tier-1 all pass AND
every Tier-2 dim ≥ 4. Otherwise `refine` (write feedback, loop). Hard cap:
`max_cycles = 4` (LLM-judged loop MUST have a hard bound — attractor principle).

---

## Tier 3 — The headline A/B (the hypothesis test)

The reason this project exists: **does a compounding wiki beat plain RAG on
cross-source synthesis?**

- **Variant A:** answer `questions.yaml` from the woven `wiki/`.
- **Variant B (baseline):** answer the same questions via naive RAG over the 6
  raw `sources/` (chunk + embed + retrieve-top-k + answer). No wiki.
- **Comparison:** a blind judge sees both answers (order randomized, variant
  labels hidden) + the ground-truth `answer`, and picks better / tie, with a
  one-line reason. Run **N=3 trials**, optionally with 2 different judge prompts,
  to denoise (fuzzy-comparison discipline).

**Scoring & expected signal:**

| question class | metric | hypothesis |
|---|---|---|
| `cross_source` | wiki win-rate vs RAG | **wiki >> RAG** (this is the whole bet) |
| `contradiction_probe` | flag-rate (both figures / both sides given) | **wiki flags; RAG confabulates or picks one** |
| `single_source` | wiki win-rate vs RAG | **≈ tie** (if wiki LOSES here → weaving lost detail = regression) |

**Decision:** the concept is validated if wiki clearly wins `cross_source` and
`contradiction_probe` while not regressing on `single_source`. If not — *that's
the finding*, and it's cheap to have learned it before building more.

---

## Also captured (free, per run)

Pipeline-health, not quality: cycles-to-converge per source, terminated within
`max_cycles`?, total cost/tokens. Tells us if the attractor loop itself behaves.

## Traps to hold the line on

- **Grader independence** — never judge the wiki with the model/prompt that wrote it.
- **Overfitting** — `holdout: true` questions (D6, D9) are excluded from any pipeline tuning.
- **No fabricated "pass"** — if a grader can't run, report the gap; never synthesize a score.
