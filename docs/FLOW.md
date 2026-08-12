# FLOW — what actually happens, and what it costs

The operational picture, rebuilt from measurement. Every number here came from a real run
this session, not an estimate. Companion to `DESIGN.md` (the *why*); this is the *what and
how much*.

---

## 1. The one fact that determines everything

Measured across all 6 sources of a real seed run:

```
LLM time         3,174.0 s     99.97 %
deterministic        0.9 s      0.03 %
```

**Three orders of magnitude.** Every deterministic component — index build, index query,
lexical scan, structural validation, normalization, footnotes — together cost less than one
second across the entire run.

This collapses the design space. There is exactly one cost lever:

> **How many LLM passes happen, and how much the LLM reads on each one.**

Nothing else is worth optimizing. Every deterministic node can be the dumbest possible
implementation and it will not show up in the wall clock.

---

## 2. Measured per-source cost, v1 seed run (26-page wiki)

| run | ingest | assess | feedback | determ. | TOTAL |
|---|---|---|---|---|---|
| 054831 | 376.6 | 35.8 | — | 0.1 | 412.5 |
| 055535 | 386.9 | 40.5 | — | 0.1 | 427.6 |
| **060526** | **1310.2** | 40.2 | **27.2** | 0.3 | **1377.8** |
| 063217 | 270.1 | 54.2 | — | 0.1 | 324.4 |
| 063855 | 298.5 | 31.7 | — | 0.2 | 330.3 |
| 064704 | 250.5 | 51.2 | — | 0.1 | 301.9 |
| **total** | **2892.8** | **253.6** | **27.2** | **0.9** | **3174.5** |

Mean **8.8 min/source** at 26 pages. Run 060526 is the one that looped — its ingest is
two passes (895.6 s + 414.6 s).

**`assess` cost 253.6 s — 8.0% of the entire run — and triggered zero refines.**

---

## 3. The v2 ingest loop, and why each node is where it is

```
select_source ──┐  deterministic. reads ledger, computes pending = on-disk MINUS ingested.
                │  THIS is what makes a 16-hour run resumable: kill it, restart from
                │  start, it recomputes pending and picks up where it stopped.
                ▼
drain_bound ────┐  deterministic. per-run cap so one run can't spend forever.
                ▼
retrieve_slice ─┐  deterministic. picks the k candidate pages the LLM may read.
                │  MEASURED FREE: index N^0.58, query N^0.88, lexical N^1.01 @0.025ms/page.
                │  ~26 ms at 1,000 pages = 0.006% of one LLM pass.
                │  ← this node is the ONLY defense against the 87-min/source problem
                ▼
weave ──────────┐  ** THE ONE LLM PASS. ~99.9% of all cost. **
                │  sees ONLY the slice. under lens/. retention contract in prompt.
                ▼
validate ───────┐  deterministic, 0.0 s. Emits printf sentinel.
                │  MEASURED: this is what caught the 1-in-6 real structural failure.
                ├── structural_bad ──> reweave_bound ──> weave   (bounded retry)
                │                            └── give_up ──> quarantine, advance drain
                ▼ structural_ok
retention_check ┐  deterministic shrinkage/heading-loss + snapshot-on-suspicion.
                │  MEASURED: caught 2/3 real content losses. An LLM judge caught 0/3.
                ▼
budget ─────────┐  appends cost.jsonl. FAILS LOUD over the per-source ceiling.
                ▼
review_gate ────┐  human (console) or, when built, a proxy.
                │  accept / guide / skip. guide → re-weave with steering.
                ▼
commit ─────────┘  archive source, append ledger + log.md, one git commit.
                   AFTER the work, never before — an early ledger write marks
                   incomplete work as done, invisibly and permanently.
                   └──> loop to select_source
```

**No `assess` node. No LLM feedback node.** The retry loop survives, but its trigger is the
deterministic validator — which is what was actually triggering it in v1 all along.

---

## 4. What changed, in measured seconds

Per source, at the seed corpus size:

| | v1 | v2 | delta |
|---|---|---|---|
| typical source | 415 + 40 (assess) = **455 s** | **415 s** | **−40 s** |
| looping source (1 in 6) | 895 + 40 + 27 + 415 + 40 = **1,417 s** | 895 + 27 + 415 = **1,337 s** | **−80 s** |
| deterministic total | 0.9 s | ~0.9 s | — |

Deleting `assess` saves **~8% of wall clock** — real, but not the headline.

**The headline is what v2 does NOT lose.** The originally-drafted v2 deleted the retry loop
too. On this corpus that would have meant **1 source in 6 shipping structurally invalid or
hard-failing**. The cycle-diff caught it before it was built.

---

## 5. The 87-minute problem

The live run that motivated this work: 18 sources, 16+ hours, still on source 11 —
roughly **87 min/source** against the seed run's 8.8 min/source at 26 pages.

Same code. The difference is corpus size, and the mechanism is:

```
v1: the ingest prompt SAYS "read a FOCUSED slice ... do NOT read every page."
    That is prose. Prose is advice. The LLM decides how much to read, and as the
    wiki grows there is more to decide to read.

v2: retrieve_slice is a TOOL NODE. It writes .ai/current_slice.json. The weave prompt
    says: do not reach outside this slice. The bound is structural.
```

This is attractor's own documented discipline — *"mechanisms > instructions; structural moves
cost zero marginal context and can't be disobeyed"* — applied to the one place v1 didn't
apply it, despite writing the rule down.

**MEASURED 2026-07-25 (S6) — and the news is worse than "unmeasured."**

The structural bound is real, but it cannot be held at a *constant* k. Measured recall of the
ground-truth pages at fixed k=6, as the corpus grows:

```
 25 pages  100%      k needed for >=90% recall:   6  (24% of corpus)
100 pages   75%                                  15  (15%)
200 pages   67%                                  20  (10%)
400 pages   50%                                  40  (10%)
```

**k must grow to ~10% of the corpus to preserve answer quality.** So per-source LLM cost is
linear in wiki size after all — `O(k)` where `k = 0.10N` is `O(N)`.

26 pages → k≈6. ~400 pages → k≈40. **~6.7x more pages in context against the ~10x observed
slowdown.** The 87-minute problem now has a measurement behind it, not an inference.

What v2 actually buys over v1 here is smaller than claimed but still real: the growth becomes
**explicit and controllable** (a number in a config, visible in `cost.jsonl`) instead of
**implicit and unbounded** (an LLM deciding how much to read, guided by prose).

**RESOLVED (S7): better retrieval fixes the growth outright.** Swapping naive term-counting
for BM25 (~30 lines, no dependency, no added cost) holds **100% recall at k=6 on a 365-page
corpus with topically-adjacent distractors**, where naive collapses to 17%. The k needed for
≥90% recall stays **flat at 3–6** instead of climbing to ~10% of the corpus:

```
        corpus    naive k    BM25 k
            25          3         3
           100         10         6
           200         15         3
           400         40         3
```

So `O(k)` is genuinely `O(1)` — **but only with BM25.** It is a precondition of the cost law,
not an optimization. See DESIGN.md §3/§10.

---

## 6. The full lifecycle

```
  init ─────── interview loop → schema (AGENTS.md) + lens/ seed
    │          MEASURED (S3): the seeded AGENTS.md is what made a cold agent respect
    │          the machine-owned .wiki/ boundary. A convention transfer, not a
    │          competence transfer — both arms already knew good markdown practice.
    ▼
  ingest ───── §3 above. drain until inbox empty. resumable by construction.
    │  ▲
    │  └─────────────────────────────────────────────┐
    ▼                                                │
  ask ──────── read-only. index-first. cites, or refuses loudly.
    │          filing answers back is OFF by default — S1 measured no benefit
    │          (identical on 3 of 4 follow-ups, worse on the 4th).
    ▼
  lint ─────── periodic. read-only, reports only, never edits.
    │          MEASURED (S2): 55% precision on new findings, 0.07 run-to-run
    │          stability. Concept-gap detection genuinely works (12 true / 5 false).
    │          Treat as a brainstorm, not a health check, until improved.
    ▼
  correct ──── natural language in → locate pages → trace sources → re-derive
    │          → persist to lens/corrections/  ← the load-bearing step. A correction
    │          that doesn't persist gets re-broken by the next ingest.
    ▼
  canon ────── human updates ground truth → impact scan → gated reprocess ──┘
               of only the sources affected.
```

Everything after `ingest` is cheap: `ask` and `lint` are one bounded LLM pass each;
`correct` and `canon` touch only the affected subset, never the whole corpus.

---

## 7. What is still unmeasured — and it is a quality question

Four experiments settled **cost**. None of them touched **quality**.

| settled | open |
|---|---|
| index/query/lexical are free at any plausible size | does a naive term-count scan pick the **right** k pages? |
| assess triggers nothing; delete it | does a smaller k degrade answer accuracy? |
| the retry loop is needed 1-in-6 | how does weave time actually scale with k? |
| AGENTS.md transfers conventions | — |

All three open questions are the *same experiment*: **hold cost constant, vary k and the
selection strategy, measure answer accuracy.** That experiment has ground truth available
(`eval/scenario-01`'s question set) and it is the last thing standing between this design and
an implementation worth writing.

**Do not confuse it with the scaling question again.** Cost is answered. Quality is not.
