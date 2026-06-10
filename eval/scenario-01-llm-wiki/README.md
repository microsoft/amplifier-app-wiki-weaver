# Scenario 01 — LLM-Wiki cluster

**Hypothesis under test:** a compounding, LLM-maintained wiki beats plain RAG on
**cross-source synthesis** — and flags contradictions instead of confabulating.
This is Karpathy's actual LLM-Wiki claim, made measurable.

**Why this corpus:** 6 real Medium articles (in `sources/`) that genuinely
overlap *and disagree* — LLM Wiki implementations, second brain, RAG-vs-memory.
Synthesis and contradiction-handling are the whole point, so a fresh 1-source
wiki wouldn't test anything; this set forces real weaving.

## Files
| file | role |
|---|---|
| `sources/` | the 6 input articles (the pipeline ingests these) |
| `ground-truth.md` | answer key: entities to merge, contradictions to surface, the verified star/fork discrepancy, the "RAG is dead" strawman trap |
| `questions.yaml` | Q&A graded A/B (cross-source vs single-source vs contradiction-probe; 2 held out) |
| `rubric.md` | the shared rubric — used BOTH as the pipeline's converge/refine gate AND the eval grader |

## How it will run (once the pipeline exists)
- **Variant A:** wiki-weaver ingests `sources/` → woven `wiki/` → answer `questions.yaml`.
- **Variant B (baseline):** naive RAG over the same raw `sources/` → answer the same questions.
- **Compare:** blind judge picks better answer per question; N=3 trials. Plus
  Tier-1 structural graders + Tier-2 quality rubric on the wiki itself.
- Run output is NOT committed (see `.gitignore`) — keys/prompts/paths leak.

**Status:** spec only. No pipeline built, nothing run yet. This defines "done"
before we build it.
