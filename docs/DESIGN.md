# Wiki-Weaver v2 — Design

> Anchor: `docs/llm-wiki-pattern.md` (Karpathy's gist, vendored verbatim).
> Everything here either **is** the gist, or is a **named, justified extension** to it.
> Nothing is here because it seemed like a good idea.

---

## 0. The sentence

> **A person or team with a growing pile of their own material ends up genuinely
> understanding it — provable by answering real questions about it, with citations,
> without re-reading the sources.**

Failure condition: *the wiki stops being worth returning to* — because it is stale,
wrong, too slow to feed, or says things nobody can trace.

Every deletion and every addition below is judged against that sentence.
(The council's only FAIL was that v1 had no such sentence. This is the repair.)

---

## 1. What v1 taught us — the evidence base

Measured, from the archaeology and the evals. Not opinion.

| Finding | Evidence |
|---|---|
| **The LLM retention judge does not work.** | Caught **0/3** of the real July-2026 content losses. A ~50-line deterministic shrinkage detector caught **2/3**. The judge cost ~2× latency and PR #47's own conclusion was "no retention improvement demonstrated." |
| **Per-source cost grows without bound.** | 3–5 min → 26 min → 87 min/source. Live run: 18 sources, 16h+, source 11. **No per-source timing is recorded anywhere in v1.** |
| **The root cause was unbounded read scope.** | Assess re-verified the *entire wiki* per source for six weeks → 16% of assess runs hit the 50-call ceiling on a 48-page wiki → 4 of 25 sources silently quarantined → `supervisor.py` was built to watch for it. |
| **Filing query answers back buys nothing.** | S1, n=3, blind judge: identical on 3 of 4 follow-ups, **worse** on the fourth (contradiction-handling 3.33 vs 4.0). +3 pages, no gain. |
| **The semantic lint is real but noisy.** | S2: 55% precision on new findings; **7% stability** across runs. Concept-gap detection genuinely works (12 true / 5 false). |
| **The cost thesis was refuted.** | Wiki $0.55 vs raw RAG $0.38 — **1.43× more expensive.** The *quality* thesis held: the wiki correctly refused where RAG fabricated. |

> **⚠ CORRECTED 2026-07-25 — two numbers in this table were wrong, and the error favored my argument.**
>
> I repeatedly cited v1 as *"~37,000 LOC with a 15:1 eval-to-mechanism ratio."* Measured:
>
> | | actual |
> |---|---|
> | core (`wiki_weaver/` + `pipeline/` + `modules/`, .py) | **17,093** |
> | `eval/` (.py) | **22,538** |
> | total .py | **39,631** |
> | **eval : core ratio** | **1.3 : 1** |
>
> **1.3:1 is a normal, healthy test-to-code ratio.** My "15:1" came from dividing eval LOC
> by `pipeline/*.py` alone (1,373) — a denominator that excluded 92% of the actual
> implementation. I picked the comparison that made the accretion case look strongest.
>
> The *specific* eval findings still stand and are independently verified: `run_ask_eval.py`
> genuinely does not parse; scenario-01's answer key genuinely is orphaned from its corpus.
> But "v1 was drowning in eval code" was never true. **v1's real problem was that a large,
> normally-sized eval suite contained broken files nobody ran — not that it was oversized.**
> That is a different defect with a different fix, and I spent much of this session arguing
> the wrong one.
| **Prose cannot enforce scale.** | v1's own philosophy doc says *"Mechanisms > instructions. Structural moves cost zero marginal context, can't be disobeyed, and don't decay."* Then made the focused-slice bound a sentence in a prompt. |

**The pattern behind every v1 subsystem:** symptom → workaround → new subsystem.
Never: symptom → root cause → structural fix.

---

## 2. The layers

Karpathy's three, plus two the team case forces. The extensions are named as extensions.

```
1. sources/     immutable raw material              [GIST]
                each carries a `kind` — see §5      [EXTENSION: segmentation policy]

2. lens/        human-authored ground truth         [EXTENSION — see §4]
                canon/       what we have decided is true
                corrections/ what we have said was wrong
                persona.md   how the proxy answers on our behalf

3. wiki/        LLM-owned markdown                  [GIST]

4. AGENTS.md    the schema, at the wiki root        [GIST — layer 3, verbatim]

5. cost.jsonl   per-ingest cost ledger              [EXTENSION — see §3]
```

**Why the lens is a legitimate extension, not scope creep.** The gist says *"You're in
charge of sourcing"* — authority in Karpathy's model comes from what the human chooses
to put in the raw layer. A hand-crafted product press release *is* a source the human
authored. v1 simply had no way to mark that some sources are **frame** rather than
**evidence**. The lens is that marking, nothing more.

**Why v1 didn't have it, and what we're consciously reversing.** v1 chose *"surface
conflicts, don't adjudicate"* (`## Open tensions`), and that choice was validated by its
best evidence. That is right **when both sources are peers**. It is wrong when a curated
spec is contradicted by an offhand remark in a chat log. v2 keeps `## Open tensions` for
peers and adds lens precedence for non-peers. This is a deliberate reversal, recorded here.

---

## 3. The cost law — the primary guardrail

> **No change merges without reporting its Δ per-source wall-time at two corpus sizes.**

Mechanism, not policy:

- Every ingest appends to `cost.jsonl`: `{source_id, kind, bytes, duration_s, wiki_pages, pages_touched, tokens_in, tokens_out, cycles}`.
- A `budget` tool node **fails the run loud** when per-source time exceeds `ceiling_s`.
- `wiki-weaver cost` plots per-source seconds against wiki page count. **If that curve
  bends upward, that is a defect** — regardless of what feature caused it.

Note: attractor emits `duration_ms` per node and full `usage` on `provider:response`,
but its shipped observability hook reads a flat `tokens_in` key that **no emitter
produces** — token aggregation currently reads zero. We write our own hook. (~30 lines.)

> **⚠ CORRECTED 2026-07-25 by S4** (`.amplifier/evaluation/wiki-weaver/*/s4-index-result.md`).
> This section fused two claims. One is false; the other is the whole argument.
>
> | claim | verdict |
> |---|---|
> | "slice selection is O(k)" | **FALSE.** Measured **N^0.88** with k held constant at 6. `query_*` parses the whole index JSON per call: O(N) parse, O(1) lookup. |
> | "the weave pass reads only k pages, so per-source LLM cost does not grow with wiki size" | **TRUE, structural — and this is the entire scale argument.** |
>
> **The false half is also irrelevant.** Against the measured 415 s `weave` pass:
> index query @400 pages = **14.93 ms = 0.0036%**; index build = **17.5 ms = 0.0042%**.
> Extrapolating at N^0.88: 4,000 pages → ~113 ms; 40,000 pages → ~860 ms. Still under a
> second against a ~400-second LLM call. **The index is not the bottleneck at any corpus
> size this tool will plausibly see.**
>
> The cost law is right in substance, wrong in stated mechanism. The property comes from
> *bounding what the LLM reads*, not from *fast slice selection*.
>
> ---
>
> **⚠⚠ SUPERSEDED 2026-07-25 by S6.** The line above says the substantive claim "survives."
> **It does not.** S6 measured slice *quality* and found:
>
> | corpus | k needed for ≥90% recall | as % of corpus |
> |---|---|---|
> | 25 | 6 | 24% |
> | 100 | 15 | 15% |
> | 200 | 20 | 10% |
> | 400 | 40 | **10%** |
>
> **k must grow ~linearly with the wiki, converging to ~10% of the corpus.**
>
> ```
> IF   quality requires  k ≈ 0.10 × N
> THEN O(k) IS O(N).
> ```
>
> **§3's cost law is refuted in substance.** Not because slice selection is slow — it is free
> (S4/S5) — but because **k cannot be held constant without losing recall.** The LLM must read
> ~10% of the wiki, and 10% of a growing wiki grows.
>
> The deterministic slice bound does **not** solve the scaling problem. It makes the growth
> *explicit and controllable* instead of *implicit and unbounded* — which is a genuine
> improvement over v1's prose guidance, but it is a smaller claim than §3 made.
>
> **This measures the 87-min/source problem:** 26 pages → k≈6; ~400 pages → k≈40. ~6.7x more
> pages in context against a ~10x observed slowdown.
>
> ---
>
> **✅ RESTORED 2026-07-25 by S7 — the cost law holds, conditional on BM25.**
>
> S6 refuted the law using *naive term-count* retrieval. S7 re-ran the identical protocol
> with BM25 (k1=1.5, b=0.75, ~30 lines, no dependency), under **topically-adjacent**
> distractors — the harder regime S6 flagged as its own caveat:
>
> | corpus | naive recall @k=6 | **BM25 recall @k=6** |
> |---|---|---|
> | 100 | 67% | **100%** |
> | 200 | 58% | **100%** |
> | 365 | 17% | **100%** |
>
> **k required for ≥90% recall:**
>
> | corpus | naive | **BM25** |
> |---|---|---|
> | 25 | 3 | 3 |
> | 100 | 10 | **6** |
> | 200 | 15 | **3** |
> | 400 | 40 | **3** |
>
> ```
> naive:  k ≈ 0.10 × N   →  O(k) IS O(N)    cost grows with the wiki
> BM25:   k ≈ 3–6, FLAT  →  O(k) IS O(1)    cost independent of wiki size
> ```
>
> **Naive lexical was the problem. The architecture was not.** The deterministic-slice
> mechanism does deliver constant per-source cost — but only when the ranker can
> discriminate. BM25 is therefore a **precondition of §3, not an optimization**.
>
> **Honest limit:** distractors are out-of-domain by construction (both regimes exclude any
> article mentioning Karpathy or LLM-wiki), so the most discriminating query terms have
> near-infinite IDF. That is correct retrieval behaviour, not leakage — but it makes the
> result optimistic for **a wiki that grows within one topic**, where every page shares the
> domain vocabulary. Untested, and now the top open question.

### The structural fix for the curve

v1's slice was chosen *by the LLM, instructed in prose*. v2 makes it **deterministic**:

```
retrieve_slice (tool, no LLM) → index + link-graph neighbors + lexical hits → k pages
weave          (box, one LLM) → sees ONLY those k pages
```

Per-source cost becomes **O(k)**, not O(wiki). This is the single most important change
in v2 and it is a direct application of v1's own stated philosophy to the one place v1
didn't apply it.

---

## 4. The lens — how "how we see this" becomes durable

The team scenario: transcripts arrive continuously; the team's shared understanding of
goals, methodology, and what matters is converging; **incoming chatter must not be free
to redefine it.**

```
PRECEDENCE (highest first)
  1. lens/canon/       deliberately authored. Press releases, decisions, definitions.
  2. lens/corrections/ "this was wrong, here is the truth." Durable.
  3. sources/          observed evidence.
```

Rules, enforced in the weave prompt and checked structurally:

- A source that **agrees** with canon → normal weave.
- A source that **contradicts** canon → recorded as *"observed divergence from stated
  intent"* on the page, **not** as a supersession of canon. Canon does not move.
- Canon changes **only** via `wiki-weaver canon` — a human act, gated, which then
  triggers a *scoped* reprocess of pages derived under the old canon.
- Two *peer* sources disagreeing → `## Open tensions`, unchanged from v1. Still right.

This is what "the press release steers ingestion, not the other way around" means
mechanically. It also answers the drift question: **guidance that can be overwritten by
the thing it governs is not guidance.**

---

## 5. Source kinds — large, small, and streaming

v1 had one policy for everything and deduped by exact content hash. Consequence: a chat
transcript with five more turns is a **different hash** → a **new source** → a **full
re-ingest** of 95%-identical content. And v1's retention contract now *forbids*
condensing the overlap. Those two are in direct conflict and nothing in v1 noticed.

```
kind=article    one-shot, immutable.                    Ingest whole.
kind=meeting    one-shot, may be huge (3h transcript).  SEGMENT deterministically;
                                                        each segment is a unit of work.
kind=stream     append-only (chat, channel).            The SOURCE IS THE CHANNEL.
                                                        Track a watermark; ingest only
                                                        the delta + a small overlap
                                                        window for context.
kind=repo       mutable snapshot.                        Diff against last snapshot.
```

The `stream` watermark is the fix for the near-duplicate problem, and it is deterministic
code — no LLM judgment about "is this the same conversation."

**Overlap policy** (starting values, to be tuned by eval, not by argument):
`stream` = 10 turns or 2000 tokens of preceding context, not re-cited.
`meeting` = segment on speaker-turn boundaries near a target size, 1 segment overlap.
Overlap is **context for the writer**, never re-counted as a new citation.

---

## 6. What gets deleted, and the evidence for deleting it

> **CORRECTED 2026-07-25 by the cycle-diff experiment** (§11 open question 1, now closed).
> Evidence: `.amplifier/evaluation/wiki-weaver/cycle-diff-result.md`. Half of what this
> section originally claimed was wrong. The correction is kept visible rather than
> rewritten away, because the error is instructive.

```
DELETE  assess (LLM judge)        CONFIRMED by cycle-diff: ran 6/6 sources, triggered
                                  ZERO refines, ~40s each (~4 min pure cost). Also 0/3
                                  on real content losses where a deterministic
                                  shrinkage detector caught 2/3, free.

KEEP    feedback + re-ingest      ** REFUTED. I was wrong. ** The loop fired on 1 of 6
        (was: DELETE)             sources and fixed a genuine structural failure. It was
                                  triggered by `validate` (deterministic, 0.0s) — NOT by
                                  the LLM judge. v2-as-originally-drawn had NO recovery
                                  path for that 1-in-6 case.

KEEP    max_cycles                Still needed — the loop stays, so its bound stays.
        (was: DELETE)             Bounded retry, deterministic trigger.
DELETE  cron scheduling           cron exists. And it is the mode where a proxy runs
                                  with no human near an undo-less wiki.
DELETE  HTML dashboard + theming  Obsidian is the IDE, per the gist.
DELETE  self-updater, model resolver, multi-instance, migrate
                                  Tool lifecycle homework the user never asked for.
DELETE  supervisor                It watches for four failures. Three are designed out
                                  by §3 and §7. Watchdogs are what you build when you
                                  can't fix the cause.
DELETE  the 5 dead consumer surfaces
                                  0/11 runnable. One library, thin adapters.

KEEP    deterministic validate    The writer cannot grade itself. Proven turn one.
KEEP    the retention CONTRACT    One paragraph of prompt. "Absence of mention is NOT
                                  evidence of staleness." Cheap and it is the fix.
KEEP    tamper deny-list          The agent WAS observed fabricating converged:true.
                                  Filesystem deny-list is the mechanism; keep only that.
KEEP    provenance/citations      It is what makes correct→reprocess possible at all.
KEEP    honest refusal            The one measured win: RAG fabricated, the wiki refused.
```

---

## 7. AITL — the agent proxy

> **⚠ CORRECTED 2026-07-25. This section previously described a system that does not exist.**
> Both councils independently verified against `interviewer.py` and `cli.py`. The original
> text claimed `--gate human|proxy|fail`, a `ProxyInterviewer`, and "byte-identical in all
> three modes." **None of that is real.** The error is left visible below rather than
> quietly rewritten, because it is the most important lesson in this document: I had the
> correct flag names in context from my own research and wrote fiction on top of them.

### What actually exists today

```
--on-human-gate console        real person answers at a TTY        ← REAL
--on-human-gate auto-approve   picks the FIRST listed choice       ← REAL, and dangerous (below)
--on-human-gate fail           refuse to run unattended            ← REAL (the default)
```

There is **no `ProxyInterviewer`**. There is no `--gate` flag. `give_up` appears nowhere in
the engine and has no landing edge in any of the 7 graphs — so a proxy that correctly gives
up would crash the pipeline rather than stop safely.

### The live corruption path this exposed

`AutoApproveInterviewer.ask()` on a **freeform** question falls through and returns the
literal string `"auto-approved"`. Three of our gates are freeform — `init.ask`,
`canon.author_canon`, `correct.capture` — and their text flows into `lens/canon/` and
`lens/corrections/`: **the two highest-precedence layers in §4's authority model.**

So the closest-to-unattended mode that exists would write the string `"auto-approved"` into
canon, and §4 would then treat it as ground truth outranking every ingested source. That is
not a missing feature; it is my own authority model being poisoned by its own fallback.

**Mandatory fix, ahead of any proxy work:** every freeform gate must FAIL LOUD under
`auto-approve` rather than accept the stub. A gate that cannot be answered must stop the
run, not invent an answer.

### Honest status of AITL

v2 is, today, **a human-supervised system.** `init` and `canon` cannot run unattended under
any existing mode. The proxy is a *design intent*, not a capability, and everything below is
a specification for work not yet done:

- Build a real `Interviewer` implementation; wire it to `--on-human-gate` (the engine's
  actual flag), not to an invented one.
- Add a `give_up` landing edge to every hexagon gate first — the safety exit must exist
  before the thing that needs it.
- Add `wiki-weaver review` to the CLI contract. It is described below as the user's only
  recourse against a proxy acting on their behalf, and **it is absent from all 28
  subcommands.** I designed the safety valve and forgot to specify it.

### How the persona gets good

1. **`init` interviews you** (attractor's `conversational-gate` pattern: ask → evaluate →
   loop until sufficient). Inputs: stated purpose, sample sources, and optionally an
   imported persona from another wiki instance as prior influence.
2. **Every real-human gate answer is a training exchange**, recorded. Run interactively
   when you want to teach; run with the proxy when you don't.
3. **Corrections feed back** (§8). Retroactive steering.

### BUILT 2026-08-01 — the proxy exists, `wiki_weaver.aitl.*`

Everything above this line in §7 was, until now, a specification for work not yet done. What
actually exists as of this commit:

- `wiki_weaver.aitl.persona.load_persona()` — deterministic structural validation of
  `lens/persona.md` (the five-slot template named below, as `##` headings). Refuses
  (fail-loud) before any model call when the persona is missing, empty, or incomplete.
- `wiki_weaver.aitl.backend.LLMProxyBackend` — the reasoning step. Deliberately NOT a
  pipeline `box` node (every `box` spawns a full bash/filesystem/search-capable agent with no
  per-node way to strip tools back off) — a single tool-free chat completion instead, which
  is mechanically incapable of touching bash/filesystem/web rather than merely asked not to.
  Requires a `grounding` citation into the persona on every answer; malformed output, an
  invalid choice, or a missing citation is treated as `give_up`, never a guess.
- `wiki_weaver.aitl.proxy_interviewer.ProxyInterviewer` — implements attractor's real
  `Interviewer` protocol (the engine's actual extension seam — `interviewer=` on
  `amplifier_module_pipeline_runner.runner.run_pipeline`, the SAME low-level API the
  `attractor` CLI itself uses). `give_up`, backend errors, and a missing/incomplete persona
  all map to `AnswerValue.SKIPPED` — the exact value `HumanGateHandler` already converts to a
  FAIL `Outcome` for every other interviewer. No engine change of any kind.
- `wiki_weaver.aitl.audit` — every decision (answered AND refused) appended to
  `.ai/gate-decisions.jsonl`, the exact path this section's own `wiki_weaver.review` spec
  already names.
- `wiki_weaver.aitl.run` — the selectable launcher: `--gate-mode {fail,auto-approve,console,
  proxy}`, default `fail` (identical to the attractor CLI's own default, so this addition
  changes nothing for anyone who doesn't ask for it).

Covered by 41 tests (persona structure, decision parsing, audit format, interviewer
fail-loud/answer paths including the two distinct "cannot answer" cases — missing/incomplete
persona vs. backend give_up — and CLI wiring), all offline (no network, no real LLM call
exercised in CI). Full command and exact model-call behavior: see
`pipeline/CLI-CONTRACT.md`'s "AITL proxy" section.

**Still not done, named so it doesn't get assumed:** `wiki_weaver.review` itself (the format
it will read already exists); persona import from another wiki instance; recording
interactive (human) gate answers as training exchanges. And, found while building this and
NOT fixed here (out of scope, pre-existing, unrelated to interviewer selection):
`canon.capture`/`correct.capture` do not exist in `src/` at all, so `canon.dot`/`correct.dot`
cannot complete end-to-end yet regardless of which interviewer answers their gates.

### Guardrails, taken from aiuser's documented failures

- **Compose the proxy without tools it shouldn't have.** aiuser's persona says *"you do
  not write code"* to a session holding bash/filesystem/web/search. Ask nicely → it
  cheats. We give the proxy no such tools. A capability restriction, not a request.
- **Never let the proxy self-certify.** aiuser's authors demoted the proxy verdict to a
  non-gating annotation after finding *"a 'success' verdict often just means the
  conversation ended cleanly, not that the user's goal was met."* Proxy decisions are
  **provisional and logged**; `wiki-weaver review` shows you what was decided on your
  behalf since you last looked.
- **`give_up` is a first-class verdict.** Distinguishes "we're done" from "we're stuck."
- **Satisfaction predicate lives in the persona, not the harness** — the five-slot
  template: identity, capability floor, hard constraint, refusal script, *what "done"
  means*.

---

## 8. Correction → reprocess (retroactive steering)

The user's case: *work attributed to the wrong person.* v1 had no path for this at all.

```
correction (freeform, human or file)
  → locate_pages   (tool)  search the wiki for the claim
  → trace_sources  (tool)  provenance already records which source fed which claim
  → re_derive      (box)   rewrite ONLY those pages, from those sources, under the correction
  → validate       (tool)
  → persist_lens   (tool)  write it into lens/corrections/
```

**`persist_lens` is the load-bearing step.** A correction that doesn't persist gets
re-broken by the next ingest that touches the same claim. This is also how the proxy
learns without a training pipeline: corrections are durable, machine-readable, and read
on every subsequent weave.

Re-derivation follows v1's one genuinely good insight, which it applied in exactly one
place: *"the defect was never model capability; it was the per-cycle INCREMENTAL
accretion pattern. Replace 'patch the page' with 're-derive from the clean catalog.'"*

---

## 9. Anti-accretion guardrails

Five rules. Each one is a specific v1 failure, inverted.

1. **Earn-your-place.** Every addition must cite the **failing eval** that passes with it
   and fails without it. No eval, no merge.
2. **The cost law (§3).** Every addition reports Δ per-source wall-time at two corpus
   sizes. Cost is a review criterion equal to correctness.
3. **Mechanism over instruction.** If a constraint is load-bearing for scale or safety,
   it must be structural. Prose constraints are defects. *(v1 wrote this rule and then
   broke it on its single most scale-critical constraint.)*
4. **No LLM in a gate a deterministic check can do.** Retention: 0/3 vs 2/3, at 2× cost.
5. **Delete-first.** A fix that removes code outranks a fix that adds it. The lock fix is
   the model: `flock` replaces 135 lines of PID-liveness with 21 and removes a whole
   failure class.

**Trip-wire (restated 2026-07-25 — the original was unmeasurable and its baseline was wrong):**

Counted as **core**: `wiki_weaver/**/*.py` + `pipeline/**/*.py` + `pipeline/**/*.dot` + `modules/**/*.py`.
Counted as **eval**: `eval/**/*.py`. Everything else (docs, fixtures, configs) is uncounted.

| gate | threshold | v1 actual | v2 today |
|---|---|---|---|
| core LOC | stop and justify above **3,000** | 17,093 | 1,163 (.dot only, 0 .py) |
| eval : core | stop and justify above **2:1** | 1.3:1 — *within bounds* | n/a |

Note what this correction does to the argument: **v1 never tripped the eval-ratio gate.** The
gate that would actually have caught v1 is core LOC — 17,093 against a 3,000 ceiling, 5.7×
over. A guardrail is only worth having if it fires on the case that motivated it; the
eval-ratio gate would have stayed green through the entire accretion.

---

## 10. Deliberately NOT built yet

Named, so they don't sneak back in as "obviously we need this."

- **BM25/`qmd` search.** The gist names it as the escape hatch past ~100 sources.
  > **CORRECTED 2026-07-25 by S5.** This said we'd add it "when the cost curve says the index
  > is no longer enough." **The cost curve never says that.** Naive lexical scan measured
  > **0.025 ms/page, dead flat (N^1.01)** — 102 ms at 4,000 pages, and it does not reach 1% of
  > a single LLM pass until ~162,000 pages. If BM25 is ever added the justification must be
  > **retrieval quality**, not speed.
  >
  > **UPDATED 2026-07-25 by S6 — that argument has now been made, and it is decisive.**
  > Naive lexical recall at fixed k=6 degrades 100% → 83% → 75% → 67% → 50% as the corpus
  > grows 25 → 400 pages. Holding recall requires k ≈ 10% of the corpus, which makes
  > per-source LLM cost linear in wiki size (see §3).
  >
  > **BM25/`qmd` is therefore REQUIRED, not deferred** — it is the only candidate mechanism
  > for holding k sub-linear. Karpathy's gist says index-first navigation works to ~100
  > sources and past that you need a real search engine, naming `qmd`. **I dismissed that
  > guidance twice and measured my way back to the same ceiling and the same fix.**
  >
  > Status: **BUILT AND MEASURED (S7). BM25 beats naive decisively** — 100% vs 17% recall at
  > k=6 on a 365-page corpus with topically-adjacent distractors; k stays flat at 3–6 rather
  > than growing to 10% of the corpus.
  >
  > **Decisions locked:**
  > - `retrieve_slice` uses **BM25**, not term counting. ~30 lines, no dependency, no added
  >   cost (S5: the scan is free; BM25 is one pass over the same bytes).
  > - **`qmd` / hybrid vector search stays deferred** — plain BM25 with no vector component
  >   is sufficient to 365 pages. The gist's escape hatch is not needed yet.
  > - **k = 6 default.**
- **Cross-repo code ingestion (`kind=repo` at scale).** Violates layer-1 immutability,
  blows the scale ceiling in week one, and needs entity resolution across a live graph.
  `team-knowledge` may already solve this. Out of scope until someone proves it isn't.
- **AI-session memory as a corpus.** Machine reader, unbounded growth, and
  context-intelligence already captures it. Different problem.
- **Any durable/resumable pause.** Attractor's checkpoint is observability only; the
  engine always restarts from `start`. We accept in-process gates until a real user is
  blocked by that.

---

## 11. Open questions I could not settle from evidence

1. **Does deleting the convergence loop actually cost quality?** Cheap test: diff the wiki
   tree between cycles 1/2/3 on the existing 6-source corpus. Zero bytes changed on 6/6 →
   delete with evidence. Nonzero → the loop is doing something and we need to know what.
   *This should be the first thing run.*
2. **Does `AGENTS.md` really make a cold agent a competent maintainer?** Claimed, never
   measured. S3 was staged and not run.
3. **What overlap window is right per `kind`?** §5's numbers are guesses. They are eval
   parameters, not design decisions.
4. **Where exactly does the O(k) slice break down?** k is a knob; the eval must find the
   value where answer quality starts falling.

---

## 12. Attractor discipline — how these pipelines are built

Added after reading the attractor engine repo end to end (345 commits, all docs, all 24
example graphs). These are not style preferences; each is a verified engine behavior or a
documented failure this project already paid for.

### 12.1 Never route on LLM token output (AP-2)

`docs/PIPELINE_PATTERNS.md §6 AP-2` is explicit: LLMs are inconsistent at exact-token
output. The robust form is:

```dot
DoWork       [shape=box, prompt="Analyze X. Write your verdict to verdict.txt."]
CheckVerdict [shape=parallelogram,
              tool_command="grep -qi 'approve' verdict.txt && printf approved || printf rejected"]
CheckVerdict -> Continue [condition="context.tool.last_line=approved"]
CheckVerdict -> Retry    [condition="context.tool.last_line=rejected"]
```

**`printf` is deterministic. The LLM is never responsible for typing the sentinel.**

> **Root cause of v1's worst subsystem, found.** `wiki-weaver/pipeline/synthesize.dot:7`
> declares itself a *"Specialized clone of convergence-factory.dot."* That shipped attractor
> "reusable pattern" contains, at line 51:
> `check [shape=parallelogram, label="Converged?", tool_command="echo routing"]`
> — a **no-op** parallelogram whose edges route on `context.preferred_label`, set by the LLM
> upstream. That is AP-2 wearing the correct shape. v1 inherited it and then spent six
> changes, one full revert, a `doctor` check, a pinned upstream SHA, and a paragraph of
> prompt-level "FLATNESS RULES" fighting the consequences.
>
> Both `examples/patterns/conversational-gate.dot` and `convergence-factory.dot` ship this
> defect. **Do not compose them.** Worth reporting upstream.

Verified across v2: zero `preferred_label` routing, zero `echo routing` no-ops, all routing
via `context.tool.last_line`.

### 12.2 Resume is a GRAPH-LEVEL property, not an engine feature

From `examples/pipelines/12-graph-resume.dot`, which is the doctrine:

> *"The engine ALWAYS runs from Start on every invocation — no goto, no jump, no special
> 'resume mode'. Resume happens ENTIRELY at the graph level: each guard node tests for its
> stage's artifact and prints a routing token. To REWIND a stage: delete its artifact file
> and re-run."*

This is a **better** property than durable pause: crash recovery is free, rewind is `rm`,
and there is no resume state to corrupt.

```dot
check_X [shape=parallelogram, tool_command="test -f .ai/X && printf done || printf todo"]
check_X -> work_X      [condition="context.tool.last_line=todo"]
check_X -> next_check  [condition="context.tool.last_line=done"]
```

**Applied to the 16-hour problem.** `ingest.dot`'s queue lives **on disk**, not in pipeline
context: `select_source` computes `pending = sources-on-disk MINUS ledger ids`. So a run
that dies at source 11 of 18 and is restarted from `start` recomputes pending as {11..18}
and resumes — no re-cost, no duplicates, no engine support required.

### 12.3 Idempotency rules for side-effecting nodes

Three nodes in `ingest.dot` are irreversible: archive the source, append the ledger, git
commit. Rules:

1. **State is written AFTER the work completes, never before.** A ledger line written early
   marks incomplete work as done and it is invisible forever after.
2. **Every irreversible node is guarded by a `test`** that makes re-execution a no-op
   (`git diff --staged --quiet` before commit; ledger membership before archive).
3. **Identity-gate any resume state.** Attractor's own scar tissue (`AGENTS.md`, #39):
   *"never read run state keyed only by `logs_root`. Mismatched identity must hard-fail,
   never silently restart — side-effecting nodes would double-apply."*

### 12.4 Engine facts that constrain authoring

| Fact | Consequence |
|---|---|
| Substitutable attrs are ONLY `tool_command`, `prompt`, `description`, `tool_env` | `$tokens` anywhere else are inert literals |
| `--param k=v` seeds a **flat** key | `$wiki_root`, never `${context.wiki_root}` |
| `graph [params="..."]` is documentation-only | The CLI flag is the real mechanism |
| Absent key leaves the literal `$token` | Dies under `set -eu`. Defend with `${var:-default}` |
| `context.tool.output` is full stdout | Conditions must use `context.tool.last_line` |
| Tool CWD = `context.target_dir` → `graph.source_dir` → process | Different resolver than `dot_file=` |
| Hexagon choices come from **unconditional** out-edge labels | A `condition=` there breaks the gate |
| No edge match → **hard FAIL** at top level | Every branching node needs a catch-all |
| `max_pipeline_duration="unbounded"` | Parser crash — omit the attribute |

### 12.5 Validation gate

`dot -Tpng` is necessary but not sufficient — it proves the file is Graphviz, not that it is
a pipeline. Every `.dot` must pass the **real engine**:

```python
parse_dot(text) → apply_transforms(g, {}) → validate_or_raise(g)
```

All 7 v2 pipelines pass this today. Any that does not is a drawing, not a pipeline.


---

## 13. Positioning — why this over the alternatives

Added 2026-07-25 because `positioning-critic` (product council) found that 406 lines never
named a single alternative, *"a silence that is conspicuous precisely because the design's
own cited authority explicitly discusses that comparison."* Karpathy's gist opens by
comparing against RAG; this document did not.

### The honest comparison

| Alternative | Where it beats us | Where we beat it |
|---|---|---|
| **NotebookLM / ChatGPT file upload** | Zero setup. Free. Better UI. **Cheaper — measured: $0.38 vs our $0.55.** | The artifact is ours: plain markdown, on our disk, in git, readable without the tool. Theirs is in someone's cloud, unexportable as structure. |
| **Obsidian + a search plugin** | The reading experience is better. Zero LLM cost. Mature. | It does not *write*. The bookkeeping — cross-refs, merges, contradiction flags — is the thing humans abandon, and the thing the gist says LLMs make near-free. |
| **Paste the pile into a 1M-context model** | Simplest possible thing. No infrastructure. Genuinely strong under ~100 sources. | No accumulation — full cost re-paid on every query, and nothing compounds between them. Breaks entirely on continuous corpora (a team's transcripts grow forever). |
| **Do nothing** | Free. | Nothing. This is the honest baseline and it wins more often than we would like. |

### Where the answer is genuinely weak — stated, not hidden

1. **We are more expensive than RAG and it is measured.** 1.43×. The only counter is that we
   refused where RAG fabricated — n=1. That is a real but thin win.
2. **Under ~100 sources, "paste it into a big context window" is a strong competitor** and
   getting stronger every model generation. The gist's own ceiling (~100 sources) is roughly
   where context windows now sit. **Our advantage is thin exactly where most users live.**
3. **S1 measured no benefit from filing answers back** — so "your explorations compound" is,
   for the query path, currently unsupported by our own evidence.

### The one durable differentiator

Not cost, not quality, not compounding — all contested above. It is this:

> **A wiki-weaver wiki is a plain-markdown artifact you own, that a *generic* agent can
> maintain without this tool installed.**

S3 measured that directly: a cold agent with no wiki-weaver knowledge, handed only the folder
and its `AGENTS.md`, produced a structurally valid ingest and correctly respected the
machine-owned boundary. No alternative in the table can say that. NotebookLM's artifact dies
with your account; Obsidian's needs you to do the writing; the context-window approach leaves
no artifact at all.

**The product is the corpus, not the tool.** If that is not worth the 1.43× premium to a
given user, they should use NotebookLM — and we should say so.

### Which customer this actually serves

`intent-keeper` flagged that §0's *"a person or team"* silently conceals two products. Resolved:

- **Primary (the gist's anchor, and where our evidence is):** one person, a bounded corpus of
  articles/papers they curated, ~100 sources. Needs `init`/`ingest`/`ask`/`lint`. **Needs none
  of §4, §7, or §8.**
- **Secondary (unvalidated, and where all the new machinery went):** a team, continuous
  transcripts, wants to walk away. Everything expensive in v2 — lens/canon, the proxy,
  correction→reprocess, 10 of 23 subcommands — serves *only* this customer, and no evidence in
  this document establishes they exist or would pay.

**Consequence:** ship the primary. Treat the secondary as an explicit, separately-funded bet.

---

## 14. The index engine — RESOLVED, and the real gap it exposed

> **This section originally said the index engine was "out of scope, must exist before this
> subcommand can run," and both councils correctly called it the biggest hole in the design.
> Both of us were wrong: it already exists.**

v1 ships `wiki_weaver/index.py` — **655 LOC**, working, tested. `build_indexes()` materializes
backlinks / links / tags / properties / aliases; a `query_*` layer sits on top. I marked as
undesigned a component that was already written. That materially shrinks the v2 build.

### Measured (S4, zero LLM cost)

| pages | build_ms | query_ms (k=6) |
|---|---|---|
| 26 | 3.5 | 1.36 |
| 100 | 5.9 | 3.94 |
| 400 | 17.5 | 14.93 |

`build = N^0.58`, `query = N^0.88`. Both negligible against LLM cost (§3).

**Decision: reuse v1's `index.py` as-is.** Do not rebuild it. If the query's whole-file JSON
parse ever matters — it does not today by 4 orders of magnitude — the fix is a keyed store,
not a redesign.

### The real remaining unknown: lexical search does not exist

§3 specifies the slice as *"index + link-graph neighbors + **lexical hits**."* S4 measured
index + link-graph. **The lexical component has no implementation in v1 and was not
measured.**

Naive lexical search (scan every page) is O(N) with a large constant — file I/O, not JSON
parse. At the gist's stated ~100-source ceiling that is certainly fine. Past it, this is the
piece that needs a real inverted index — or `qmd`, which the gist names by name and which
§10 already defers.

### S5 — lexical measured. CLOSED.

Implemented the cheapest possible scan (pure Python, read every page, count terms, no index)
and measured it:

| pages | corpus MB | scan_ms | ms/page |
|---|---|---|---|
| 100 | 0.3 | 2.5 | 0.025 |
| 1,000 | 3.4 | 25.5 | 0.026 |
| 4,000 | 13.6 | 102.0 | 0.026 |

`N^1.01` — perfectly linear, **0.025 ms/page flat**. Against the 415 s weave pass: 0.0006% at
100 pages, 0.0246% at 4,000. It does not reach **1%** of one LLM pass until ~**162,000 pages**
— three orders of magnitude past the gist's ceiling. And this is the pessimistic case;
`ripgrep` is ~10x faster.

**Verdict: naive scan is free. No inverted index. No `qmd`. Ship the `grep`.**

---

## §14 CONCLUSION — the scale story is settled

All three slice components are now measured, and all three are 3–5 orders of magnitude below
the LLM cost:

| component | scaling | @1,000 pages | % of one 415 s LLM pass |
|---|---|---|---|
| index build | N^0.58 | ~9 ms | 0.002 % |
| index query | N^0.88 | ~40 ms | 0.010 % |
| lexical scan | N^1.01 | ~26 ms | 0.006 % |

**Per-source cost is dominated by the LLM weave pass, full stop. The only lever that matters
is how many pages the LLM reads.** Every deterministic component can be the dumbest possible
implementation and it will not matter.

This retires §3's cost-law complexity. `retrieve_slice` needs no clever engineering — it needs
to return a *small, well-chosen* k.

### The one question that replaces it — and it is a QUALITY question

S4 and S5 measured **cost**, not **quality**. Nothing here shows that a naive term-count scan
selects the *right* k pages. That is now the open question, and it has a different experiment:
**hold cost constant, vary k and the selection strategy, measure answer accuracy.** §11(4)
already asks where k breaks down; that is the same experiment.

**Cost is answered. Quality is not. Do not confuse them again.**
