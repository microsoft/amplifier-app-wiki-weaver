# WikiWeaver V4 — Plan and Design

**Status:** Kicked off building (Stage 1) — feedback welcome · **Date:** 2026-09-24, revised 2026-10-02 · **Owner:** Gurkaran Singh
**For:** Brian (team lead), Marc + Ken (Resolve), Sam (Team Pulse)

---

## 1. The problem

Teams and individuals accumulate hundreds of meetings, chats and documents. What was
decided, what changed, who owns what, what's blocked — it's all in there, but nobody can
hold it all in their head. Search finds only what you already know to ask for.

Two common approaches fall short in specific ways:

- **Re-read the sources every time** (RAG, or an agent with grep). Accurate on facts.
  Blind to how things evolved — it can't search for a keyword nobody used. And it costs
  the same on the hundredth question as on the first.
- **Generated summaries.** Fast, and generic. They don't know what you care about, and
  they forget every correction you give them.

What's missing is a knowledge layer an agent maintains *for you*: organized around what
you care about, improving as you correct it, and cheaper for an agent to consult than the
sources themselves.

Karpathy's LLM-wiki pattern is the base — an LLM maintaining markdown pages over retained
raw sources, with a human reviewing as it goes. V1, V2 and V3 each built part of the rest
of it. V1 established the page structure and the index-to-summaries-to-sources shape, with
sources retained. V2 added the attractor pipeline and designed the lens and the reviewer in
the loop. V3 built the evaluation harness that lets us tell whether any of it is working,
and a pattern worth reusing: a model proposes, a cheap checker gates, failures fall back.
The observation that sets up V4 is Brian's, from the sync:

> *"The version I did before we started with the wiki — I was giving it feedback on the
> things it would come up with. **And that's where we saw the highest value.** While being
> able to fully automate it meant we could get more of it done, we were sacrificing that.
> It was a trade. So the next iteration was: how can we get back some of that value?"*

In a follow-up 1:1, Brian and Gurkaran aligned on three pillars: lenses known in advance,
lenses drawn out by interview, and the feedback loop. That is what V4 goes after. On top of that base, four
additions — carrying all three:

1. **A lens that actually reaches the writer.** V1 derived a policy file from an initial
   purpose; V2 designed lens directories. But V2's writer never sees them — its input is
   wiki pages only, and its prompt tells it not to read outside that slice. In V4 the lens
   is delivered to the writer by code on every pass, and it is the *only* place
   deployment-specific knowledge lives.
2. **A proxy for the human reviewer.** V2 designed this and it never ran. V4 puts an agent
   in that seat, primed by the lens, so the wiki runs unattended — and lets a person take
   the seat back whenever they want.
3. **A learning system.** A correction given once is applied, kept, and honored by every
   run after it. That closes the feedback loop described above, and no version has built it.
4. **Measurement, carried forward from V3.** V3's most durable output was a
   custody-protected evaluation harness that tests any version against grep over the raw
   sources — on answer quality and on cost per answer. It is what told us V3's own design
   wasn't paying, and it's how we'll know whether V4 earns its place.

**One general-purpose system.** Team Pulse, a personal knowledge base, a project's history,
a research corpus — the pipeline is identical for all of them. The only thing that changes
between deployments is the lens file.

---

## 2. What we're building

**The thesis, in one sentence:** the wiki's value is pre-computed synthesis an agent can
trust and reuse, which gets better every time someone corrects it.

Both halves come from evidence rather than conviction. On the first: in the V3 four-arm
blind evaluation this month, an agent with grep over the raw sources matched every wiki on
fact lookup — but on the higher-order questions (*what patterns are emerging, where is work
duplicating, who assumes someone else is handling this*), the wikis handed the agent named
framings it could pick up and use, while grep rebuilt the same synthesis from raw
transcripts every single time. That is the one axis where a wiki showed an edge, and it is
exactly where Brian said grep would struggle: *"the ideas that build up over time… where
grep isn't even going to know the right keywords to look for."*

On the second: the prior measurement program found that a system given real guidance — one
person's expertise and intent, written down — closed more than half the gap between the
automated pipeline and a supervised one. Corrections are how that guidance accumulates
without anyone having to sit down and author it.

**The shape.** The pipeline is a **dot graph** — a `.dot` file where each node is either a
model call or a shell command — and it runs on the **dot-runner engine**, the same engine
Resolve runs. In attractor terms it's attractor-like in concept — a bounded convergence loop,
not gate ceremony: per source, write → deterministic checks → rewrite once; across runs,
corrections converge the wiki toward the lens. About eleven nodes: three inference (box)
nodes, everything else deterministic glue.
Per source: a lens-primed **brief** decides what matters here; a **writer** produces the
source summary and integrates into the topic pages it touches; **deterministic checks**
either pass it, send it back once, or hold it. Sources are retained and cited so an agent
can follow a thread back. A person can answer the brief instead of the proxy, or read the
output later and correct it — either way the correction persists and steers the next run.
Everything lives in one directory that travels as a tarball.

**What changed from V3, and why.**

V3 asked whether a wiki could be made verifiable sentence by sentence, and answered it:
yes, mechanically — and it doesn't move what a reader or an agent actually gets. Two things
from that work carry straight into V4. The evaluation harness, which is the reason we know
any of this — it surfaced the synthesis signal above, and it told us the verification axis
wasn't paying. And a pattern worth reusing: a model proposes, a cheap checker gates, and
anything that fails falls back — which rejected half its drafts before they ever reached a
page.

The design itself was over-steered, and we're loosening it:

- **Judgment moves before the write.** In V3 the model graded finished pages. The prior A/B
  program measured both placements and found the pre-write gate paid substantially while
  the post-write grader landed within noise — so the lens does its work while the page is
  being written, not after. *Note this differs from the flow as described in the sync —
  summaries reviewed after the fact — and it's a deliberate call on measured grounds. The
  post-write human review isn't lost: it becomes the correct path, which is asynchronous
  and persistent rather than per-source.*
- **Checks become deterministic and routing.** A small structural check caught real content
  losses that a model judge missed entirely.
- **Citations become thread-following, not proof.** A specific claim — a date, a number, a
  name, a commitment, a position — names its source and quotes a few of its words, so an
  agent can jump to the exact spot; orientation and synthesis need no citation. No model grades a
  sentence. The only check on a citation is mechanical: the quoted words must appear in
  the named source. If they don't, the write fails and the writer gets one more attempt
  with the finding; if it fails again, the source is set aside with its reason in the
  ledger and the run continues. Nothing is lost and nothing waits on a person: the hold
  is a signal to the retry path, and in Stage 3 a standing correction can change the
  outcome.
- **Pages accumulate rather than being replaced.** V3's renderer overwrote each page with
  the most recent source's view, silently hiding nearly half of everything it had
  extracted — pages that looked completely fine. We found it by counting rows against
  rendered lines, and fixed it. V4 carries the fix forward as design rather than as a
  patch: integration with dated supersession, and a guard that fires when a page loses
  material without one. It also answers *"sometimes the history is important."*
- **The lens reaches the writer by code.**
- **Corrections persist.**

---

## 3. Design principles

Each traces to something measured or decided, not to taste.

1. **The lens is the steering wheel.** Nothing deployment-specific lives outside it — Team
   Pulse is one lens, a personal wiki is another, the pipeline is the same.
   *Evidence: rewriting one guidance document as expertise and intent rather than procedure
   was the largest policy-level gain in the prior A/B program and cut junk pages by more
   than half. It also inverted on a second corpus — so the lens is re-measured per
   deployment, never assumed to transfer.*
2. **Judgment goes before the write.** A lens-primed brief decides what matters in this
   source; no model grades a finished page.
   *Evidence: the earliest gate was the largest single quality gain in the program and was
   kept; the gate that graded finished writes was retired as within noise, even though its
   calls independently matched a competent human on 19–20 of 21 cases. Grading a finished
   write cannot change how the write happened.*
3. **The writer is the quality node.** It gets the lens, the standing corrections, the
   disagreement contract, the lens's page structure, and the citation convention.
   *Evidence: tracing where information was lost put the overwhelming majority of loss at
   ingest — the first read of a source — not at composition or retrieval.*
4. **Pages accumulate; history stays visible.** New material is integrated, not
   substituted. What changed carries a date; a superseded position remains readable.
   *Evidence: V3's renderer replaced each page with its latest source's view and hid 43% of
   what had been extracted — and the pages looked fine.*
5. **Checks are deterministic, and they route.** A guard that cannot fail is not a guard.
   *Evidence: a model retention judge caught none of three real content losses; a small
   structural check caught two of three. V2 shipped a retention threshold that returns
   success unconditionally — a page can lose most of its words and still commit.*
6. **Tell the answering agent how to read the wiki.** Summaries are pointers, not testimony:
   for a specific date, number, name or commitment, follow the citation to the source; for
   orientation and synthesis, the page is enough.
   *Evidence: this is the hint-versus-fact distinction raised in the sync. It also avoids
   the failure in the field's one controlled experiment, where pages stamped "verify against
   source" made the agent read the wiki* and *the sources — paying twice for one answer.*
7. **Corrections persist.** Given once, honored thereafter — Ken's "best improving system"
   rather than the best system.
   *Evidence: every field mitigation for compounding summary error is a human review gate,
   and full automation removes it. A persistent correction is how a person's judgment stays
   in the loop without the person being in it.*
8. **Let the model do the work.** Short prompts that state intent, wide latitude in the
   middle, deterministic guardrails at the edges. Procedure written into a prompt is the
   wheel we're loosening. The tripwire against re-tightening: policy grows in the lens, never
   in the prompt; corrections are scoped to the pages they concern; no new model gate is
   added without a measurement that says it pays.
   *Evidence: guidance written as expertise-and-intent outperformed procedure; and a
   read-scope rule written as a prompt sentence was ignored until per-source cost had grown
   from five minutes to eighty-seven.*
9. **Measure the artifact, never the intermediate reasoning.** Answers and cost decide;
   structural metrics are tripwires only.
   *Evidence: intrinsic and extrinsic evaluations disagreed in the prior program, and the
   metric behind earlier conclusions correlated near-zero with judged quality. A standing
   proxy-judge improved its own reasoning while shrinking the artifact by a quarter — both
   blind judges preferred the arm without it.*
10. **Spend deterministic machinery freely.** Model calls are essentially all of wall time;
    counters, scans and guards are free.

---

## 4. Requirements

In priority order. *When* each arrives is in §6.

| | Requirement |
|---|---|
| R1 | A pure graph on the dot-runner engine — every inference is a model step, every other step is a shell command. Acceptance: no bespoke module makes a model call |
| R2 | A lens — human-authored markdown, seeded at init from a purpose or a short interview — injected into the writer by code on every pass |
| R3 | A proxy that answers the brief; a person can answer it instead, or correct the output afterwards |
| R4 | **A learning system**: corrections are applied to the affected pages, persisted, and read by every subsequent run — so a correction given once is honored thereafter |
| R5 | Runs behind the contract Team Pulse and RepoWeaver already call: the `wiki-weaver` verbs (`doctor · init · ingest · ask · build-dashboard · --version · update`), `ask --json` → `{answer, pages_used, refused}`, `--version` → `wiki-weaver YYYY.MM.DD-<sha>`, the `wiki_weaver.lib` path helpers (`wiki_inbox · wiki_sources · wiki_failed · wiki_ledger · wiki_dashboard`), `.wiki/runs/ingest-*/result.json`, tarball in / tarball out. `init` runs on every job, so running it against an existing wiki must leave that wiki intact |
| R6 | Index → source summaries → raw sources; the agent follows threads back. Sources retained, never modified. Pages accumulate with dated supersession |
| R7 | Incremental: a new or edited source updates the wiki. Keyed on content, so an edited file gets re-read; `_sources/<name>` is the wrapper-visible done signal and the ledger carries the hash |
| R8 | **Output is for agents to read. Human readability of the wiki is a non-goal** |
| R9 | General: the same pipeline runs a second corpus with a different lens and zero code changes |
| R10 | Measured against grep over raw sources and against V2 — on synthesis questions and on cost per answer |
| R11 | Wall time and model passes are **recorded** from the first run. **Optimizing wall time or performance is a non-goal** — the lever is built into the shape (fewer passes, less read per pass), but we do not tune for it until quality is established |

**Explicitly out of scope.** Per-sentence verification and any census · byte-offset receipts ·
human readability of the wiki — out of scope since the original brief; a readable layer would
be a new decision · an editorial or "journalist" voice · wall-time and performance tuning ·
migration of an existing V1 or V2 wiki (see §9).

---

## 5. Design

### Layout

The directory names are V1's, deliberately: they are the contract Team Pulse's resolver and
RepoWeaver already call, so both swap to V4 without noticing. Everything new sits beside them.

    _inbox/                      pending sources — markdown; YAML frontmatter optional
                                 (date, title and kind fall back to header lines and filename)
    _sources/                    retained raw sources, never modified. A file appearing here
                                 under its original name = successfully ingested
    .wiki/failed/<name>          held sources, with the reason in the ledger
    .wiki/.processed.jsonl       the ledger: content hash, filename, outcome, reason
    .wiki/runs/ingest-<ts>/result.json   machine-readable outcome with counts
    .wiki/policy/schema.md       legacy wrapper policy; read if present (also policy/schema.md),
                                 folded into the lens as a fragment
    lens.md                      NEW — purpose · what matters · people · question shapes ·
                                 page types · owner
    lens/corrections/            NEW — standing corrections, each with the reason it was given
    feedback/log.jsonl           NEW — raw feedback, each entry with a why; mined later
    *.md at the corpus root      pages, with frontmatter title/type/sources/last_updated;
                                 per-source summaries as source-*.md
    index.md                     one line per page: title, date range, one-sentence summary
    log.md                       what was ingested, when, what changed, what was skipped and why

All state lives in this directory. Transport is whatever moves it — a tarball under a
resolver, the filesystem locally. Everything that must round-trip (`_sources/`, the ledger,
`lens.md`, `lens/`, `feedback/`, pages) sits outside the resolver's tarball exclusions
(`.wiki/runs`, `.wiki/snapshots`).

`index.md` carries a date and a one-line summary per entry deliberately: it's the model of
how an agent orients — scan titles and summaries, pick the few that look relevant, then read
those in full.

### The graphs

Three `.dot` files, and every model call in the system lives in a box node in one of them:
`ingest.dot` below; `init.dot` (the interview and the lens draft); `ask.dot` (read the index,
pick pages, answer with citations). The CLI verbs are a dispatcher that launches the right
graph. `doctor`, `build-dashboard`, `--version`, `update` and `feedback` are plain Python with
no model calls. The purity test covers all of it: no model call anywhere outside a box node.

**`ingest.dot` — ~11 nodes, 3 of them model calls**

    start
      → select_source   shell   next source, keyed on content
      → brief           MODEL   lens + standing corrections + the source →
                                what is this about, what matters here, what to watch for
      → write           MODEL   brief + lens + corrections + index + only the sections it
                                touches → source summary, and dated entries into the aspect
                                sections of topic pages; disagreement contract; citation
                                convention; returns changed sections only
      → checks          shell   citations resolve · content-loss guard · duplicate headings ·
                                heading set · Current state format · superseded blocks ·
                                write scope · link integrity
                                → ROUTES: pass | rewrite-once | hold (→ .wiki/failed/<name>)
      → commit          shell   git commit; record the source as done
      → (loop)
      → index           MODEL   rewrite index.md and log.md from what changed
      → exit

**Integration, not replacement.** The write step adds to a topic page rather than replacing
it: what's new is woven in, what changed carries a date, and a superseded position stays
readable rather than disappearing. The content-loss guard enforces this mechanically — a
page that loses material without a supersession marker doesn't commit. The disagreement
contract keeps a two-sided exchange from collapsing into a settled conclusion, and keeps
*decided* distinct from *proposed* and *still open*.

**Page shape — general, set by the lens.** Every accumulating page, in any corpus, opens
with a `## Current state (as of <latest source date>)` roll-up: a synthesis of the page as it
stands, replaced in full on every pass, written for a reader who reads nothing else. Below
it, the record is organized into sections the lens names for that page type. The *mechanism*
is the same for every deployment; only the *section names* come from the lens. A team
status wiki might name Owners · Commitments · Blockers · Decisions · Open questions; a
personal journal might name Decisions · Learnings · Next; a repository wiki whatever its
wrapper's schema says. New material becomes dated entries inside those sections, newest
first. A source is never a section — its narrative lives on its summary page. A
deterministic check holds whatever heading set the lens defines.

**Why sections are the unit of work.** The writer used to receive every selected page
whole. Pages grow with every source that touches them, so the writer's input grew with the
wiki — a median of 825K characters per source at 129 sources, 1.5M at the maximum, rising
55% an epoch, on course to exceed the model's window before the full corpus. But one source
only ever changes a few sections of a page: the roll-up, and the aspects it has something to
add to. So the writer now receives exactly those — Current state, the sections the brief
named, and a heading-only outline of the rest — and returns the changed sections in full; a
deterministic step splices them back. Input is bounded by section size, which is roughly
constant, instead of page size, which is not. The same shape serves the reader: an
answering agent reads Current state first and opens a section only when it needs one, so
cost to answer stops scaling with page size too. This is R11's lever — less read per pass —
built into the structure rather than tuned. Stage 1 runs with whole-page writes;
sections as the unit is the first change after Stage 1, built on a branch against the full
fresh corpus.

**Supersession is a block.** Text that is no longer true is wrapped in
`<!-- superseded: YYYY-MM-DD -->` … `<!-- /superseded -->`, covering exactly the old words;
the replacement goes outside the block as its own entry. A block may not cite the source
being ingested — you cannot supersede what you just wrote. The date is the source's when a
position changed in a source, and the ingest date when the source file itself was edited.

*Added 2026-10-06 after E04. None of the three was in the original design; each answers a
measured failure — 51% of topic-page sections named for a meeting, writer input at 1.51M
characters and rising 55% per epoch, 20–35% of superseded markers wrapping the new text.
See docs/LEARNINGS.md.*

**Oversized sources — the call, made here rather than discovered later.** Long transcripts
don't fit one pass with a small model. We use a large-context model for the write step and
do **not** chunk. Chunking means segments, and segments mean the bookkeeping machinery V2
grew around them. Each source is read once, so cost stays bounded and the pipeline stays
simple. If a source exceeds even a large context window, it is held and reported rather than
silently partially read — and if that turns out to happen often enough to matter, we take it
on as its own piece of work rather than pre-building machinery for it now.

### The two ways a person gets in

Neither is per-source. The field interview with a daily user was unambiguous: supervising
every ingest was *actively harmful* — the agent takes shortcuts and touches things it
shouldn't — and full automation was the stable state.

- **Answer the brief.** By default an agent answers it: the proxy standing in for the human
  reviewer. Run interactively and a person answers instead — occasionally, to teach the
  system.
- **Review and correct.** Read the output whenever you like, say what's wrong, and the
  correct path applies it to the affected pages and writes it to `lens/corrections/` with
  the reason it was given.

Both feed the same place, and every subsequent run reads it. A third, lighter channel — the
`feedback` verb — just records what someone noticed, with a why, into `feedback/log.jsonl`;
it is mined into lens edits later rather than acted on one entry at a time.

### The query side

The wiki is only half the system; how an agent reads it is the other half. V4 ships a short
context file that travels with the wiki and tells the answering agent three things: the shape
(index → summaries → sources), that summaries are pointers rather than testimony, and when to
follow a citation — for a specific date, number, name or commitment, go to the source; for
orientation and synthesis, the page is enough.

Not a blanket "verify everything," which is what made the field's one controlled experiment
pay twice for every answer. Not "trust everything" either.

### How this runs in Resolve

Resolve can run it two ways: by calling a command-line tool — which is how Team Pulse's
current version runs — or by running the graph directly. We start with the first so Team
Pulse sees no change when it switches over, and move to the second because the graph is
built for it.

### RepoWeaver and other wrappers

RepoWeaver is the first wrapper and the pattern for the rest: it gathers a repository's story
— PRs, commit logs, module snapshots — shapes it into markdown without any model call, drops
it into `_inbox/`, and runs `ingest`. Brian's rule is that source-specific preparation lives
in wrappers so WikiWeaver stays general; a conversation weaver for meetings and chats would
follow the same shape.

We checked how it actually calls WikiWeaver rather than assuming. It depends on three things
at once: the CLI verbs and flags (`init --plain`, `ingest --wiki … --max-cycles`,
`ingest --source` for retries, `ask --json`, `build-dashboard --group-by repos`, `--version`,
`update`); a Python import of five path helpers from `wiki_weaver.lib` at module load, so
without them every RepoWeaver command fails, `doctor` included; and the corpus layout as shared
state — it writes `_inbox/`, reads `_sources/<same name>` as the success signal, derives each
repo's last-sync date from `_sources/` filenames, and reads `.wiki/failed/` and the ledger to
classify failures. The resolver additionally reads `.wiki/runs/ingest-*/result.json`.

V4 keeps all of it (R5 and the layout above). Two additions on our side: wrapper-provided
policy — RepoWeaver's `schema.md`, with its page types and `repos:` field — is read as a lens
fragment, which is where that knowledge belongs; and we accept the file at both paths
RepoWeaver might write it to. Three defects found by reading RepoWeaver's code (not by running
it) go to Marc as a separate brief: it writes its schema to a path V1 never reads, its retry
path resolves filenames against the wrong directory, and the resolver passes `sync` two flags
it rejects. None are V4's to fix; all pre-date it.

### Five cheap wins, taken as-is

1. **Citation-following.** The largest measured quality win in the prior program —
   following a citation back to the source recovered a compressed-away fact on nine of ten
   questions. V1 and V2 already cite the source file; V4 adds a few quoted words so the
   agent lands on the spot, plus a check that the quote is really there.
2. **The disagreement contract.** The largest prompt-level win. One paragraph.
3. **Deterministic gates instead of judge models.** See principle 5.
4. ~~Cap attributed sources per page at roughly 8–10.~~ **Retracted at Stage 1 CP2.** The prior
   program found attribution degrading past ~10 sources; Stage 1 began with that cap and it
   bound after one epoch on exactly the pages that must accumulate. The citation check — a
   verbatim quote verified against the named source — guards attribution directly, so the
   cap is removed and page size is watched instead.
5. **Spend deterministic machinery freely.** Counters, coverage reconciliation, loss guards.

### Why this shape

- Three model passes per source, each reading as little as it can. That's the only lever on
  cost and wall time — deterministic work was a rounding error in the prior measurement.
- The writer never sees the whole wiki. Handing synthesis a full page catalog drove
  per-source cost up by three-quarters over four epochs in V1/V2.
- Every check is a small script, and every one can fail the step.
- Pure by the attractor's Golden Rules, so it runs anywhere the dot-runner engine runs.

---

## 6. Sequencing

**Stage 1 is a complete, runnable system — not a prototype.** It ingests a full corpus
unattended, produces a usable wiki, and is measured. Every stage after it adds a capability
to something that already works.

| Stage | What ships | Done when | Target |
|---|---|---|---|
| **1 — the working pipeline** | The three graphs, installable and runnable end to end. Lens delivered to the writer. Checks route. Pages accumulate. The writer reads `lens/corrections/` from day one (empty at first); a `feedback` verb appends to `feedback/log.jsonl`. Run first on a 7-file smoke slice, then one epoch (E01, 31 sources), then a second epoch incrementally, then two epochs on a fresh corpus under the final page design (e01 — 129 sources under the predecessor shape — retained as the before-artifact); and on a second, personal corpus with a different lens and no code changes | Purity acceptance passes · smoke, epoch and incremental slices pass · two epochs ingest unattended on a fresh corpus under the final page design (the full corpus under that design is the first post-Stage-1 milestone) · measurements recorded per epoch — wall time, model calls, page sizes, writer input, ask cost (the scored eval against grep and V2 is deferred per the 9/30 decision; §7's "no stage waits on evals" holds) | **days, from 9/30** |
| **2 — running in Resolve** | The four verbs and the tarball package over the same pipeline, called the way Team Pulse's current version is called. The command-line path is the bridge that keeps Team Pulse unaffected; running the graph directly is the destination, unlocked by the graph being pure — timing with Marc and Ken | One job executes in Resolve; Team Pulse can call it without noticing what changed underneath | days after Stage 1 complete |
| **3 — the learning loop** | The correct path end to end, plus interactive mode for the brief | A correction given at source N is honored at source N+1, demonstrated | week 2 |
| **4 — corrections become lens** | Lens edits proposed from accumulated corrections; the owner accepts or declines with a reason; declines feed the next pass | An owner reviews and accepts a proposed lens edit end to end | week 2–3 (gated on Stage 3 having run long enough to accumulate corrections) |
| **5 — cross-check views** *(optional)* | A second extraction technique run in parallel with the summarizer; disagreements surface as findings | Measured against Stage 3 — adopted only if it wins | later |

Measurements run from Stage 1; the scored evaluation is deferred per the 9/30 decision.

**First after Stage 1 — sections as the unit.** One branch, one milestone. The writer receives
and returns sections, not pages; glue does every write. The brief names the sections. `ask`
reads Current state first and drills on demand. Tools come off model nodes where the engine
allows. Then the full 215 under the final design — the scale validation Stage 1 handed off —
with anything held for size re-ingested through the retry path. It closes:
- writer input growing with the wiki — 825K median, 1.51M max, +55% per epoch at 129 sources
  (LEARNINGS, "Writer input grows with the wiki");
- model nodes writing files, and the forensic snapshot that forced — the reviewer drove the
  engine's shell tool outside the corpus with no model call; a name-only snapshot let a writer
  overwrite of a dirty owner file pass (LEARNINGS, "The snapshot was detecting what should be
  prevented");
- `ask` reading whole pages — 383K–549K characters per answer at E04;
- aspect misfiling, by moving that judgment to the brief (a Stage 1 watch).
*Done when:* writer characters per source stop tracking wiki size; 215 converge; `ask`
characters per question measured before and after. *Watch:* whether the writer needs a
"request more sections" turn; index.md at ~250 lines.

**Owed for Stage 2** — conformance tests from both contracts, if not finished during the Stage 1
run; `--limit ≤ 0` → exit 2, and hold logging when a batch is all-held; the `update` verb (OPEN
for Marc); CI fixed before the `main` merge; the merge sequence `v4-build` → `v4` → `main`.

**Later, not scheduled** — picked up as the earlier stages close out:

- Parallel ingestion of sources that provably don't touch the same pages. Raised and deferred
  in the sync; the engine supports fan-out natively; wall time is the daily user's top
  complaint, so it's worth revisiting once quality is established.
- Dedicated handling for sources too large even for a large context window, if the hold-and-
  report path turns out to fire often.
- A periodic lint pass over the whole wiki — near-duplicate pages, orphans, contradictions.
  Karpathy's gist has one; Stage 1's checks are per-write only. Structural decay is the field's
  second most-reported failure, so this sits early on the list, not as an afterthought.

---
- Page splitting, only if sections themselves grow — superseded share is 2–5%; these are live
  pages, not history bloat.
- The grep comparison for R10 and the scored evaluation, when Brian re-prioritizes them.

**Housekeeping** — retire the `smoke` corpus; the pyright error; `git add -A` in the CLI
snapshot commit.

**Watches, no action** — citation density (hold at ~69% of body lines); uncited hard-number
lines (28%); wiki/source size ratio (47%); the builder's hold-during-write rule, which
glue-writes make moot.

## 7. How we'll know it's working

**The evaluation harness already exists**, and it's the most valuable thing V3 produced. A
custody-protected slate of questions with verified answers; keys that never touch a tracked
file; margins agreed before any run; parallel arms that each see only their own tree, with no
prior context. It's what told us V3 wasn't paying on the axis it optimized, and it's
shape-agnostic — it reads pages and judges answers, so it carries into V4 untouched.

What it needs for V4: a V4 arm, cost recorded per stage, and more synthesis questions.

Five claims, in the order they matter, and what would settle each:

| Claim | Stage | Settled by |
|---|---|---|
| **It knows what you care about** | 1 | The same source under two lenses produces materially different pages; the questions the lens names are answerable from the pages it produced |
| **It gets better as you correct it** | 3 | A correction given at source N is honored at source N+1; the proxy's rejection rate falls as corrections accumulate |
| **Lens and learning work end to end, unattended** | 1–3 | A full corpus ingests without a person present, under its lens; a person steps in occasionally and the system carries what they said forward |
| **Nothing is silently lost** | 1 | The loss guard fires on real losses; every page's history is reconstructible from git |
| **Pre-computed synthesis is worth the cost** | 1 | Synthesis questions against grep and V2, plus tool calls, files opened and tokens per answer against grep's baseline |

Measurement runs alongside the build rather than gating it: the numbers tell us where to spend
the next week, not whether to continue. Aligned with Brian (9/30): evals are dialed down as a
requirement, and performance is not a focus for V4. The bar is feedback that the quality is
*as good, if not better* than what the team has now — reached by getting the plumbing working
first, then fixing the biggest friction points. Evals are recorded from Stage 1 and inform
priorities; no stage waits on them.

---

## 8. The evidence base

Everything above traces to one of three sources: the V1/V2-era A/B measurement program, the
V3 four-arm blind evaluation we ran this month, or a verified read of the V2 branch. Figures
are quoted as recorded; where a metric's units aren't defined in the source, the direction and
the decision are what carry weight.

### 8.1 What each version established, and what it left

*A running row-per-behavior comparison of V1, V2 and V4 is kept in `docs/LINEAGE.md`.*

**V1** built the base — the page structure, the index-to-summaries-to-sources shape, sources
retained, citations. Steering is limited to an initial purpose, and there is no reviewer.

**V2 is where much of this design already lives.** Verified against a fresh clone this week:
the lens directories exist; the agent-in-the-loop proxy exists, with a structurally validated
persona and an append-only audit log; the gate modes exist, including a human console mode;
and the gate sits *before* the writer, per segment — exactly where the measurement says it
belongs. Its main synthesis node is a genuine model step. The design is right. What it can't
do today:

- **It doesn't install.** The wheel builds successfully and contains zero Python files; an
  editable install reports success and the import fails.
- **The gates aren't in the graphs.** Every reference to the proxy in the pipeline files is a
  comment or prompt text, and the default gate mode halts a plain run at the first source.
- **The lens never reaches the writer.** The writer's input is wiki pages only. Its prompt says
  *"do not read files outside this slice"* and, six kilobytes later in the same prompt, *"work
  under the lens."* Three separate docstrings assert a read that no code performs.
- **The loop never closes.** Gate answers are stamped per source and deleted after use; the
  design document lists recording them as training exchanges under "still not done."
- **The retention guard is inert** — thresholds exist, the check returns success
  unconditionally, and its graph edge is unconditional.
- **No content hashing** — an edited source under the same filename is never re-ingested.

V4 is, in large part, finishing this: installable, gates in the pipeline, lens delivered to
the writer, and the loop closed.

**V3** tested whether a wiki could be made verifiable sentence by sentence. It can, and the
evaluation showed it doesn't move what a reader or an agent actually gets — no accuracy gain
over V2 or grep, at meaningfully higher cost per answer, across 1,219 thin pages. Three things
from it carry forward: **the evaluation harness**, which is how we know any of this; **the
probe-gated writer pattern**, where a model proposes, a cheap checker gates, and failures fall
back — which rejected half its drafts before publication; and a lesson about our own tooling —
a graph file is not automatically a pure pipeline. All three generations hid inference inside
shell steps and the linter passed anyway, so purity has to be an acceptance test.

### 8.2 Where quality comes from

**Ingest is the leverage point.** Tracing 36 lost (fact × arm) points across the prior program
put loss at **ingest 86%, weave 11%, retrieval 3%**. Downstream tuning was measuring noise.

**Judgment early beats judgment late, decisively.** The earliest gate was kept (+7.7pp alone,
+19.7pp with guidance, 3/3 above control ceiling), along with standing guidance (+3.0pp). The
review-stage gate — grading finished pages — was **retired** (+1.3pp, overlapping spreads) even
though its decisions were independently judged equal to a competent human on 19–20 of 21 cases.
A curate gate was **removed** (−1.9pp).

**A standing proxy-judge shrank the artifact.** The cleanest A/B of the program: −26% pages,
−31% words, and both blind judges preferred the arm without it. Its "consolidation" was the
synthesis step not happening. The proxy must prime the write, not prune it.

**Half the human/automation gap was policy, not machinery.** One guidance document grown from
1.7KB to 3.7KB — written as expertise and intent, not procedure — closed 53% of the gap
(+15.8pp) and cut junk pages from 21.7 to 8.3. Caveat from the same finding: a later tuning of
that document **inverted on a second corpus**.

### 8.3 What trust actually requires

**Citations do not discriminate.** Every wrong or partial answer, without exception, was
well-cited — specific dates, named people, direct quotes. Confident wrongness and confident
correctness are visually indistinguishable. This is why citations are for thread-following,
not proof.

**A fabrication passed every mechanical check** — zero repeated sentences, clean attributions,
zero broken links, and a day-1 exchange presented as day-2 and propagated into the overview.
Cheap metrics catch breakage, never falsehood.

**Verification gates are necessary and not sufficient.** An entry-point obligation raised
source-walking from 0/12 to 10/12 — and one answer verified the *wrong* item.

**The wrong-answer mode is over-summarization, not invention.** Commitments generalized into a
vaguer bucket than what someone actually said. Pushing once or twice made the system fetch the
transcript and answer correctly — the truth was retrievable all along; the wiki layer
suppressed it.

**Prose constraints don't hold.** A read-scope bound written as a prompt sentence let
per-source cost grow from 3–5 minutes to 26, then 87. Mechanisms over prose.

**Aggregate claims about people are the highest-risk inference class.** Cross-source inference
once put performance-review content on a person's page that no source supports.

### 8.4 What the wiki is worth

**Cost grows with the artifact, not the input.** 12.8 → 22.6 minutes per source by epoch 4
(+76%), because synthesis was handed the full page catalog.

**Wall time is ~entirely model calls** — LLM 99.97%, all deterministic work 0.03%.

**Wiki value scales inversely with how searchable the sources already are.** Lift by corpus:
transcripts +1.70, titled articles +0.20, structured session renders −0.10, repo snapshots
−0.20. Meeting transcripts — Team Pulse's corpus — are the best case in the data.

**The field's one controlled experiment agrees, and explains why.** Across 1,046 comments on
Karpathy's gist, the top-reported problems are navigation breaking at scale and unenforced
structure — not citability. The only controlled experiment found the wiki bought nothing, for
two reasons: the pages didn't compress, and pages stamped "verify against source" made the
agent read both and pay twice. Collapsing into one dense trusted page took a four-lookup task
from 10 calls to 4.

**The V3 four-arm blind evaluation, September 2026.** Four arms on one harness — plain grep
over the raw sources, V2's wiki, V3 as published, V3 after a render fix — each answering with
an agent plus grep over its own tree.

- **Fact lookup was a tie**, with grep numerically highest (96.2%, against 91.8% for V2 and
  90.6% for V3-after-fix). Every gap fell below the pre-registered floor, and majority-of-three
  confirmed V3 against V2 at +0.0.
- **No arm fabricated** on the trap questions — anti-fabrication is a property of the harness,
  not of any wiki.
- **Cost per answer separated the arms** where accuracy didn't: V2 was the cheapest tree to
  answer over, grep next, and V3 the most expensive — a structural consequence of thin pages
  forcing four to five page-opens per question.
- **The judgment tier is where a wiki showed its edge.** On *what patterns are emerging, where
  is work duplicating, who assumes someone else is handling this*, all four arms answered
  substantively with no fabrication — but the wikis gave the agent named framings with less
  re-derivation, while grep rebuilt the synthesis from raw transcripts on every run. A rubric
  signal, not a pass-rate. **It is also the thesis of V4.**

### 8.5 What the daily user told us

From an interview with someone running both a team wiki and a personal one, every day:

- **Latency is his top complaint**, raised first and unprompted.
- **Supervised runs were actively harmful.** When he drove ingestion himself, the agent "takes
  some shortcut, touches things it shouldn't touch." Full automation was the stable state — the
  human seat wants to be occasional and asynchronous.
- **Error detectability inverts between deployments.** On team content he can grade an answer
  from memory; on personal content he cannot grade at all — and he never checks sources there.
  So corrections carry a team wiki; the lens and the defaults have to carry a personal one.
- **Steering today is zero.** "I've never had to come back through updating anything."
- **His lens, unprompted:** keep specifics over tidiness, commitments never generalized;
  topical firewalls, because some topics rot others; carry his view of people and sources;
  expand outward — "bring me more like this"; report what it did *not* ingest; make the source
  type visible in answers.

---

## 9. Risks and open questions

*Implementation decisions and the revisit list are in `docs/DECISIONS.md`; what went wrong
in the build and how it was fixed, in `docs/LEARNINGS.md`; caller-facing promises are in
`contracts/`.*

**Risks we're carrying knowingly**

- **A short prompt plus a lens may write worse pages than V2's long one.** V2's writer prompt
  is eight kilobytes and holds real judgment. We relocate the deployment-specific parts into
  the lens rather than discard them — but relocation can lose fidelity. The evaluation catches
  it; the risk is real.
- **Guidance gains have inverted across corpora before.** The lens is re-measured per
  deployment.
- **Improvements are not additive.** Two individually positive changes measured together once
  came out negative. Combinations get measured.
- **Aggregate claims about people are the highest-risk inference class.** Person pages are
  covered by the lens's instruction to record roles only as the sources state them, and by
  the citation check; no separate rule.
- **Writer input grows with page size.** At 129 sources the writer's median input was 825K
  characters, max 1.51M, rising ~55% per epoch — on course to exceed the model's window
  before 215. Mitigation: sections as the unit of work (§5). Watched per epoch.
- **No migration path.** V4 re-ingests from sources; there's no converter from an existing V1
  or V2 wiki. For anyone holding a wiki that took a week to build, that's a real cost and we're
  stating it rather than discovering it later. Sources are retained in every version, so
  nothing is lost — but the rebuild is a rebuild.
- **The engine caps a run at ~85 sources** (dot-runner stops any run at nodes × 50 steps).
  Stage 1 works around it by batching in the CLI; the graph stays pure. Removed when the
  engine allows longer runs.

**Plan, where a question was open**

- **Repository.** V4 is built on the canonical `amplifier-app-wiki-weaver` repo — on a branch,
  merged to `main` once Stage 2 passes. That repo is what the Resolve worker image builds from,
  so landing there *is* the transparent swap for Team Pulse. V1 and V2 remain as tags, and the
  version-numbered repo naming ends.
- **Who adjudicates lens edits.** The person responsible for that particular system, per the
  sync. For Team Pulse specifically, we should name who that is.
- **RepoWeaver.** Covered, not deferred: V4 keeps the layout, verbs and library helpers it
  depends on (§5, R5), so it swaps in with no change on its side. Its own defects go to Marc as
  a brief.

**Still open**

- **The synthesis questions** — the class grep can't answer. Needed from whoever knows the
  corpus well enough to write them.
- **Whether a readable layer is ever wanted.** Out of scope by decision; if wanted, it's a new
  decision recorded as one.

---

## 10. What we need from the team

**Marc and Ken**
- Confirmation of the contract Team Pulse will be calling, so V4 lands behind it unchanged.
- Whether running the graph directly is the preferred host once purity is demonstrated, and the
  timing.
- A look at the RepoWeaver defects brief (§5) — three pre-existing issues found by reading its
  code, none of them V4's.

**Sam**
- The question shapes Team Pulse actually asks the wiki, to seed the lens.

---

## Appendix — sources

2026-09-21 team sync (full transcript) · the V3 four-arm evaluation report and custody
artifacts · the V1/V2-era A/B measurement program · V2 branch verified against a fresh clone
(HEAD 3a62de7, 2026-09-23) · V2's output package on the same 215-source corpus · community
field evidence, 1,046 comments on the Karpathy gist, weighted for measurement over opinion ·
a daily-user interview (2026-08-18) · Karpathy's LLM-wiki gist.
