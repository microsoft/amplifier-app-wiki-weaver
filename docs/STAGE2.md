# Stage 2 — Running WikiWeaver V4 in Resolve

*The execution plan for PLAN.md §6, Stage 2. Written for three readers — the WikiWeaver side (us), the Resolve platform team (Ken, Marc), and the Team Pulse team (Samuel, Alex, Devis) — after the 2026-10-12 planning meeting, the code investigations that followed it, three rounds of review, and Samuel's description of how Team Pulse calls the weaver today. Stage 1 is closed.*

---

## The short version

**What this is.** WikiWeaver V4 replaces V1 as the engine that turns Team Pulse's sources into a wiki. It runs behind the same Resolve interface — same four commands, same zip in, same zip out — so nothing in Resolve or Team Pulse changes to run it. Stage 2 ends with V4 running in Resolve consistently: a V4 wiki of Team Pulse's full history, built inside Resolve, and V4 running alongside V1 on live jobs so the two can be compared. **Switching Team Pulse to V4 is a later, separate decision, and it is theirs.**

**Resolve team (Ken, Marc).** Nothing changes in the platform. Team Pulse runs on ACA — confirmed by Ken; the VM lane is retired — and ACA workers reach `api.anthropic.com` and `github.com`. Ken has built the mechanism: V1 and V4 coexist in the worker image, V4 in its own environment behind a `wiki-weaver-v4` wrapper (`amplifier-resolve#458`, merged), and a job picks V4 with one parameter, `wiki_weaver_version: "v4"` (resolver PR #10 — reviewed by us, merged at `b453be3`; it also excludes `.wiki/work/` from the archive). Still needed: a re-pin so the image carries the Phase 1 tip rather than `c527cd9`; the image built and deployed somewhere we can submit to; how long ACA retains job inputs; access for us to submit jobs; and the model budget for a rebuild that is several hundred model calls over days of jobs.

**Team Pulse team (Samuel, Alex, Devis).** Nothing changes in your code or your schedule. We need: read access to `data/corpus` on your server, to take the sources for the rebuild; your packaging code, to understand exactly what each job sends — V4 assumes the whole wiki is present, and you send a subset; your job parameters; and to know what on your side reads the wiki after Resolve returns it. The shadow runs are on our trigger, not yours.

---

Every item below is tagged with whose system it concerns:

- **[V4]** — a change inside WikiWeaver V4 itself.
- **[V4 ↔ Resolve]** — about how V4 behaves inside Resolve, or what Resolve sees from it.
- **[V4 ↔ Team Pulse]** — about what Team Pulse sends and gets back.

---

## What this is

**Today.** Team Pulse's wiki and its full source history live on Team Pulse's server, in `amplifier-app-team-pulse-made/data/corpus`. For each new source, Team Pulse's code packages the wiki pages it judges relevant, the ledgers, and that one source in `_inbox/`, uploads the zip, and starts a Resolve instance on ACA. Resolve runs WikiWeaver V1 in a sealed container and returns the updated pages; Team Pulse merges them back. Each job sees that package, not the whole corpus. RepoWeaver runs upstream on the same server, turning repository changes into source documents that go through the same path. (This is Samuel's description; we read the packaging code in Phase 1 to confirm the details.)

**What is different about V4.** The interface is the same; the wiki is not. V1 writes pages organized by theme with a history section per page, cites with numbered ids, decides when a page is finished by having a model judge its own work, and — a known defect — can silently drop content on rewrite. V4 opens every page with a maintained summary of where things stand *now*, organizes the record below by what matters — owners, commitments, blockers, decisions, open questions — rather than by meeting, cites every specific claim with a quoted passage an agent can verify in one search, and keeps history visible with dated markers instead of deleting it. A **lens** file the owner controls says what the wiki is for and what each page type tracks, so the same engine serves a team status wiki and a personal knowledge base without code changes. Deterministic checks replace the model judging the model: nothing is lost, nothing is cited that is not in the source, and when a source cannot be integrated it is held with a stated reason rather than forced in. Stage 1 measured V4 over two epochs on a fresh corpus: 64 of 64 sources ingested unattended; zero headings outside the intended set at 65 sources; history markers correct 28 of 28 times on the sample; answers drawn from 99–160K characters of wiki rather than the whole corpus. Crash recovery, locking and containment went through three rounds of independent code review, with the remaining findings deferred (see "What we are not doing yet").

**How Team Pulse's wiki becomes a V4 wiki.** The wiki in `data/corpus` is V1-shaped. V4 does not convert it (PLAN §9: "the rebuild is a rebuild"), and writing V4 pages into V1 pages would produce a mixture nobody designed. So a V4 wiki for Team Pulse is **built from scratch, from their sources** — every source they have ever ingested, which their corpus keeps byte-for-byte. That rebuild is Phase 3. There is no migration step, by design.

**What Stage 2 is, and is not.** Stage 2 ends with **V4 running in Resolve, consistently, alongside V1**: a complete V4 wiki of Team Pulse's history exists, built inside Resolve; and every source Team Pulse has processed since that wiki was built has also gone through V4, so the two can be compared like for like. Team Pulse keeps reading V1 throughout. **Switching Team Pulse to V4 is not part of Stage 2.** It is written up as an addendum at the end so it can be tracked — not executed.

**The method.** Brian's: get it working end to end first; fix what the run shows needs fixing, and only that. Every phase below is a run, and every deferred item below has a trigger.

## What changes for each team

**Resolve team:** nothing in the platform's execution flow. The weaver resolver — the wrapper that unpacks the archive, runs four `wiki-weaver` commands, and repacks — now also reads `wiki_weaver_version` (PR #10, merged `b453be3`) and uses `wiki-weaver` or `wiki-weaver-v4` for every command in the job; V4 answers those commands and writes the same result file V1 does, verified by running the code. The worker image carries both: V1 at `/opt/resolve-weaver` as before; V4 at `/opt/resolve-weaver-v4` in its own Python 3.12 environment with its own `dot-runner`, behind a seven-line wrapper (#458, merged). Three things remain on your side: a re-pin of the image's V4 from `c527cd9` to the Phase 1 tip and of the resolver to `b453be3` (one PR, ours to send); building and deploying that image somewhere we can submit to; and two or three further pin updates as V4 changes during Stage 2 — the splice may change it again. The contract is the boundary, so Resolve never notices; the pins just have to follow. Separately, we have shared a set of platform observations with Ken — watchdog behavior by lane, failure reporting, retention, the image fallback — tracked there, not here.

**Team Pulse team:** nothing in your code and nothing in your schedule. You keep packaging and sending jobs exactly as now, and reading V1's output. We need read access to `data/corpus` once, to take the sources for the rebuild, and to read your packaging code — the one real compatibility question is that V4 expects the whole wiki in the zip and you send a subset. The shadow runs are ours to trigger — we take each new source you've processed and run it through V4 — so your scheduler sends nothing twice. Before anyone talks about switching, we need to know what on your side reads the wiki after Resolve returns it.

---

## The phases

| | Outcome | Mostly whose work |
|---|---|---|
| **Phase 1** | V4 is ready to run where we don't control the environment | us |
| **Phase 2** | V4 runs a job inside Resolve, shaped like Team Pulse's | us, with answers from Resolve and Team Pulse |
| **Phase 3** | Team Pulse's wiki exists in V4, built inside Resolve | us, with corpus access from Team Pulse |
| **Phase 4** | V4 runs alongside V1 on Team Pulse's live sources | us |

---

## Phase 1 — V4 is ready to run where we don't control the environment

**Closed 2026-10-19 at `cc7e06b`.** Seventeen commits; 38 of 50 model calls; suite green in all three configurations. Eight exit criteria met in full, one met for glue-written files with the rest deferred by decision, two external items open — both gate Phase 2, not this close. The findings below are what Phase 1 actually found; where they correct the original plan, they say so.

### What this is

Before V4 runs inside Resolve, a few things about how it behaves there need to be proven rather than assumed, what it reports needs to be useful to the people who will see it, one assumption about the model needs checking, and one known writing defect needs its cheap fix in place before the rebuild amplifies it. Phase 1 changes the pipeline graph — the writer's input assembly, the model rule, the hold vocabulary, the run id. Each change has a Resolve reason; each means Resolve's pin moves to a new commit.

### For the Resolve integration

**[V4 ↔ Resolve] Scratch and the zip round trip.** Resolve packs the corpus into an archive after each job and unpacks it for the next, and deliberately leaves out the corpus's `.git` history. V4 keeps a scratch folder, `.wiki/work/`, during a run. The fix is at the contract level: `.wiki/work/` joins `.git`, `.wiki/runs/` and `.wiki/snapshots/` in the list of folders that do not round-trip (`contracts/corpus-package.v1.md`, clause 10 — done), and the resolver's pack list follows the contract. **For Marc:** one line in the resolver's exclude list — verified at CP2 to be still missing. *Correction:* this plan originally said leftover scratch would stop every job after a dead one. That premise was wrong — without `.git`, V4's recovery step is skipped and the select step wipes scratch at the start of every source anyway. The `init` fallback that landed only moves that clearing earlier and logs one line. The real risk from a dead job is a half-written page, next.

**[V4 ↔ Resolve] Atomic page writes.** Ken's design direction is that the resolver returns the artifact regardless of outcome, even when a job died. A job that dies mid-write would then ship a half-written page, and the next job would commit it as its baseline with no way to know. *Done for the files V4's own code writes* — `READING.md`, `lens.md`, `log.md`, source versions, migration rewrites — by temp file and rename. *Not done for the pages the model steps write:* the writer and index steps write live pages through their own tools, and a kill test showed the writer running a Python script of its own to do it. Protecting that path needs a staging directory both model steps write into, with commit doing the rename after checks pass — recorded in LEARNINGS as the plan of record and **built once, with the splice**, by decision on 2026-10-16. Ken: Team Pulse's jobs have not died so far; their failures are non-convergence.

**[V4 ↔ Resolve] Proving V4 does what the interface documents say.** We wrote two short documents describing exactly what Resolve's weaver resolver calls and expects back: the commands and flags (`contracts/cli-surface.v1.md`) and the files and folders in the corpus zip (`contracts/corpus-package.v1.md`). Both were written by reading the resolver's code. Each says automated tests exist that check V4 against it; those tests were never written. Until they exist, "V4 matches what the resolver calls" is a reading of the code, not a command anyone can run. These tests are the Resolve team's assurance as much as ours.

**[V4 ↔ Resolve] Saying which version ran.** V4's result file does not name the wiki-weaver version that produced it. With V1 and V4 running side by side in Resolve, that is the first thing anyone will want to know. Fix: add it.

**[V4 ↔ Resolve] Saying why something failed.** When V4 cannot integrate a source it holds it and records a kind and a reason, and that record reaches Resolve's completion payload verbatim. Today a model timeout, a wrong API key, a provider outage and a connection error all get the same kind with identical text; the real cause sits in an engine log that never leaves the container. Ken asked to tell infrastructure trouble from "the work didn't go well." Fix: carry the engine's cause into the hold, give run-level errors a kind, and put the vocabulary under test so it stays accurate.

**[V4 ↔ Resolve] Two claims about the archive — verified.** The platform investigation claimed the resolver skips symbolic links when packing, and that Python 3.12's `data_filter` rejects escaping links on unpack. Read at both `main` and PR #7 (same file): **pack skips symlinks — true.** **`data_filter` — false**; the unpack is a hand-written pre-scan that refuses every link member, which is stricter than `data_filter` on links, then a plain extract. The archive is tar.gz, not zip. And `.wiki/work` is **not** in the exclude list — Marc's line stands.

### For the Team Pulse integration

**[V4 ↔ Team Pulse] Reading the packaging code.** Team Pulse sends each job the pages it judges relevant, not the whole wiki. V4 assumes the whole wiki: its brief step selects pages from `index.md`, its link check requires every internal link to resolve to a file on disk, and its loss guard compares against what is there. If `index.md` names pages that are not in the zip, or pages link to pages that are absent, V4's checks hold the source where V1 might have shrugged. Before Phase 2 we read Team Pulse's packaging code and decide which of three things is true: V4 works on their job shape as-is; "relevant pages" should become "all pages," which is small for a wiki of a few hundred markdown files; or V4's checks need to tolerate a partial corpus. Phase 2's test job is then shaped like their real job, not like ours.

### The model — one assumption to check

**[V4] The writer's model, its window, and two silent cuts — what Phase 1 found.** The style rule that assigns a model to the writing step used an underscore; the engine's class grammar is `[a-z0-9-]+`, so it never matched and the writer ran on the worker's default — Sonnet, as intended. Fixed, with a test that resolves every model node through the engine's own parser. The window question turned out to be the wrong question. The model's API window is 1M tokens; the worker advertises 200K to the context layer; and the context layer *compacts* the writer's session at about 105K estimated tokens — 0.8 of a budget that has already had half the output allowance and a margin removed. Compaction keeps the opening message, the latest message and the last five tool results, and stubs everything older. **The pages are not in the opening message.** The node prompt names `.wiki/work/pages.md` and the writer fetches it by tool — so the pages are in the cut-first category, the same as V1 and V2. In Stage 1 the writer compacted mid-session in 44 of 88 writer sessions and re-read `pages.md` up to seven times. Nothing in V4's output recorded it; the event lands in a hosted-session log outside the corpus. **A second cut sits in front of the first:** the context layer admits at most 128 KiB of any single tool result and keeps only the head. In Stage 1, 61 of 89 reads of `pages.md` exceeded it — the writer saw the first pages in selection order and never saw `index.md`, which is appended last. The writes still passed every check; what degraded was what the writer could see and remember on the busiest pages — a plausible contributor to the Current state stacking, not a proven one. Both cuts are now **recorded per source** — `compactions` and `ingress_truncations` on every ledger row and in `result.json` — so the Phase 4 comparison can say which pages were written by a writer that did not see all its input.

**Decision: a quality ceiling, not a blocker. The guard is not lowered to the compaction trigger** — holding half the sources to avoid a silent quality dip is the wrong trade for end-to-end-first, and V1 runs in production with the same exposure, unmeasured. What happened instead: (1) `worker=llm-direct` on the write node — tried and reverted. It is the engine's text-only worker: tool calls come back as prose, nothing is written, the source is held. It cannot run a writer that uses tools; it is exactly the worker for a writer that does not. (2) Detection — done, both cuts, per source. (3) The guard stays at 3.5M characters; it holds what would fail, not what would merely compact. The structural fix is the splice, in Phase 3, and every finding here is why it is specified **tool-less**: sections in the prompt are not a tool result, so neither cut applies, and with no session there is nothing to compact.

### A known writing defect — the cheap fix

**[V4] Current state stacking.** In E02, on pages touched by many sources, the writer kept the previous roll-up under a heading like "Earlier roll-up, kept for history" and stacked new paragraphs led by the latest meeting on top. Median roll-up length went from 2,572 to 4,088 characters, maximum 10,618. The cause is a conflict inside one model call: on every other section the rule is *never delete*, and a check enforces it — a write that loses more than 15% of a page's lines is rejected, and that check fired eight times in E02; for Current state the rule is the opposite, *replace in full*, and it was only a sentence. The writer followed the rule that had a check behind it. It was complying, not disobeying. We tried three different sentences; none held, for the same reason the source-link rule needed a check.

The cheap fix: strip Current state from the page before the writer sees it and ask for a new one. It cannot keep what it never received. (The loss guard already excludes Current state from its comparison — `checks.py:30–56`.) A few lines of code, no new model call. **Proven on a real rewrite** on a copy of the Stage 1 wiki: one substantive dated change appended to a source cited on `team-pulse.md` — an owner change with a due date. The roll-up went from 10,657 to 1,747 characters; the "Earlier roll-up" block is gone; the three stacked "As of…" paragraphs are gone; it opens with what the thing *is* and who owns what, and the change appears once, as current state, with its citation. Brian's, Chris's and three other touched pages did the same (9,193 → 1,163; 7,284 → 1,061). The writer did not reopen the live page before writing. **The structural roll-up step is not needed before Phase 3.**

### In addition — V4 cleanup

**[V4] Small things — done.** Run ids are unique to the microsecond, from one clock read. `--limit 0` and negatives are a usage error, exit 2. CI runs V4's suite (without the engine — see Phase 2, question 5). `init` writes no `.gitkeep` and removes the ones an older `init` left.

### Exit criteria — status at close

- **[V4 ↔ Resolve]** `.wiki/work/` in the contract's round-trip exclusions — **done**. Marc's exclude line — **open**, verified missing at CP2.
- **[V4 ↔ Resolve]** Page writes atomic — **done for glue-written files**; model-step pages deferred to the splice, by decision.
- **[V4 ↔ Resolve]** The two interface tests exist and pass — **done**: 18 CLI (clauses 1–12), 15 corpus (clauses 1–10).
- **[V4 ↔ Resolve]** `result.json` names the version — **done**.
- **[V4 ↔ Resolve]** Three forced failures → three different kinds — **done**: `model_auth_error`, `model_connection_error`, `model_timeout`.
- **[V4 ↔ Resolve]** Two archive claims verified — **done**: symlinks refused both ways; `data_filter` not used; `.wiki/work` not excluded.
- **[V4 ↔ Team Pulse]** Packaging code read — **open**, waits on Samuel. Gates Phase 2.
- **[V4]** Writer model explicit and tested; `llm-direct` tried; compaction detected; guard sized — **done**, all four, and the ingress cap detected too.
- **[V4]** Current state cheap fix in and proven on a real rewrite — **done**.
- **[V4]** Full suite passes — **done**: 177 with engine and key; 174 + 3 skipped without the key; 169 + 8 skipped without the engine.

---

## Phase 2 — V4 runs a job inside Resolve, shaped like Team Pulse's

### What this is

V4 runs **inside a Resolve worker container**, submitted the way Team Pulse submits, through the existing weaver resolver — an archive in, a wiki out. **Local first:** Ken's instruction is to stand Resolve up locally (via `amplifier-bundle-resolve`, the way he verifies his own changes), build the worker image there with the real V4, and run the job; then the same job on ACA once an image is deployed. The local run is what he means by "verify it works in a DTU"; the ACA run is Phase 2's second clause. End to end first, small, before anything at scale. It is the first time V4 runs anywhere we do not control, and the first time it calls a model from inside Resolve.

### Before starting — answers from the Resolve team

1. **[V4 ↔ Resolve] ACA — confirmed.** Ken: the VM lane is retired, barring a staging environment he is fixing. On ACA, from the platform read: the resolver's CLI environment is built at job start from pinned requirements; nothing kills a hung job by default; API mode separates infrastructure failures from resolver outcomes by `failure_code`; inputs are retained, with no expiry we could find.
2. **[V4 ↔ Resolve] Egress — confirmed.** Ken: ACA workers reach `api.anthropic.com` and `github.com`; the goal and dotgraph resolvers already depend on it. V4's engine fetches its modules from GitHub on first use and resolves model names live, so this mattered.
3. **[V4 ↔ Resolve] Retention — pending.** How long does ACA keep a job's input after completion? Ken is working on this and will answer. Phase 4's primary mechanism depends on it; the fallback does not.
4. **[V4 ↔ Resolve] A dead job's zip — answered.** Ken's design direction: the resolver should always return the artifact, regardless of outcome; he is building that into the goal resolver now. So a half-written page *can* travel, and with scratch excluded from the zip the next job cannot tell. V4 writes pages atomically — Phase 1. Ken also notes Team Pulse's jobs have not died so far; their failures are non-convergence, not infrastructure.
5. **[V4 ↔ Resolve] Does the engine install in the ACA worker today?** **Partly answered by #458:** the image installs dot-runner `fa01aa0` — its `main` tip — from a fully pinned closure; Ken's local DTU ran one source on it. That is not the `80d5511` we verified on, and `80d5511` no longer installs fresh. We verify V4 on `fa01aa0` before the re-pin. At Phase 1's close, a fresh install of dot-runner at our pinned rev failed: its `amplifier-agent@main` dependency no longer builds — Ken's library change, landing. CI runs V4's suite without the engine for now. Which dot-runner and amplifier-agent versions does the ACA worker install, and do they build cleanly? If the pinned closure is broken, this is the first thing Phase 2 hits.

### One check on our side first

**[V4 ↔ Resolve]** The resolver runs V4's setup command on *every* job, including jobs that arrive with an already-configured corpus. If V4 regenerated the lens file in that case, every job would destroy the wiki's configuration. One hour to test; if it fails, fix it before any job runs.

### What is in it

- **[V4 ↔ Resolve] How V4 gets into the worker and how a job picks it — decided and built by Ken.** Not a catalog entry, not a package rename. `amplifier-resolve#458` (merged `8c3a948`) bakes V4 into the worker image in its own Python 3.12 environment at `/opt/resolve-weaver-v4`, from a fully pinned 116-line closure, behind a seven-line `wiki-weaver-v4` wrapper that puts V4's own `dot-runner` first on PATH; V1 at `/opt/resolve-weaver` is byte-for-byte unchanged. Resolver PR #10 (merged `b453be3`) adds `wiki_weaver_version: "main" | "v4"` — omitted, empty or null means `main`; invalid values fail before any CLI call; a missing binary fails loud with no fallback; one binary serves every command in the job; the selection and the CLI's actual `--version` are recorded in every completion payload — and adds `.wiki/work` to the archive's pack exclusions. We reviewed #10 against real V4 at tip and fixed four things before merge, one of which would have failed every form-launched V1 job (the stock form sends `""` for an untouched picker). V4 stays on `v4`; nothing merges to `main`. **Two things are not yet true:** the image's V4 pin is `c527cd9`, twenty commits behind the Phase 1 tip `274d310` — a re-pin PR moves it, and the resolver's pin to `b453be3`; and no image has been built or deployed from the merged state — neither repo has a build workflow, and staging was "still being solidified" on the 19th.

- **[V4 ↔ Team Pulse] The lens.** V4 is steered by a file called `lens.md` — what the wiki is for, who reads it, what each page type tracks. V1 has no equivalent. We have Team Pulse's lens, built from their purpose during Stage 1; the page-type section in it was written by hand and pasted in, not generated, as DECISIONS records. **For Samuel:** who owns this file after any switch.
- **[V4 ↔ Team Pulse] A job shaped like theirs.** First, a fresh corpus: the lens and one of their sources. Then a job shaped exactly like Team Pulse's — the pages their packaging code would select, the ledger, one new source in `_inbox/` — against the small wiki the first job produced. That second job is where the partial-corpus question gets its answer in practice. We check Resolve's completion payload, fetch the returned zip through Resolve's artifact endpoint, read V4's result file from the event stream, and read the pages.
- **[V4 ↔ Resolve] One long job.** Thirty-plus sources in one job. Team Pulse's own jobs are one source each, so this is not their shape; it is ours for Phase 3, and it measures what ACA actually does to a job that runs for hours. In the meeting we quoted a three-hour limit; the platform code says nothing on ACA kills a job on time. This job replaces the argument with a measurement, and the measured per-source time seeds the batch budget for Phase 3.

### Exit criteria

- **[V4 ↔ Resolve]** Resolve's completion payload for the Team Pulse-shaped job reports `success`, with source counts matching what was sent.
- **[V4 ↔ Resolve]** The zip fetched back through Resolve contains a wiki with the Stage 1 page shape, verified by reading the pages.
- **[V4 ↔ Resolve]** At least one V4 model call completed inside a Resolve container — proving egress and provider resolution work there.
- **[V4 ↔ Team Pulse]** We know whether V4 handles Team Pulse's partial-corpus job shape, and if not, which of the three remedies is in place.
- **[V4 ↔ Resolve]** We know what ACA does to a multi-hour job, from having run one.

### What we need

**From the Resolve team:** the re-pin reviewed, and the image built and deployed somewhere we can submit to; retention; access for us to submit jobs; the model budget; `fa01aa0` confirmed or explained.
**From the Team Pulse team:** a handful of representative sources; the packaging code; which parameters you pass today (`limit`, `max_cycles`, `keep_going`, a purpose text or `--plain`).

---

## Phase 3 — Team Pulse's wiki exists in V4, built inside Resolve

### What this is

**[V4 ↔ Team Pulse]** Team Pulse's full history — every source in `data/corpus` on their server — through V4, **inside Resolve**, into a fresh wiki initialized with their lens. It runs as a series of `weaver-v4` jobs in chronological source order, so that dated entries and supersession markers come out right. This is the biggest thing V4 will do in Resolve. It is also the run that tells us whether V4 at Team Pulse's scale needs the one change we have deliberately held back.

### What we expect, and the trigger

**[V4]** On the current page shape, writer input quadrupled from 31 to 65 sources: the median went from 126K to 518K characters, the maximum from 411K to 805K, and one call was already over 800K; wall time per source went from 218 to 321 seconds. Team Pulse's history is more than 215 sources and has been growing every day since August. Extrapolated, the largest writer calls reach the size guard before the rebuild finishes — the 1M API limit under `llm-direct`, or routine compaction long before that under the default worker — and the rebuild is days of jobs, not hours. **We expect an oversized-context hold on the busiest pages somewhere in the back half of the rebuild. Phase 3 measures where.**

**The rebuild stops at the first oversized-context hold.** Not "holds it and continues": continuing would ingest newer sources ahead of the held one, so that when it comes back after the splice it lands out of order, its entries read as newest, and the history markers can wrap the wrong text; and once the busiest pages are full, every later source that touches them is bound to be held too, at full model cost. So: stop. **That first hold triggers the splice** — the change PLAN §6 describes, scope as written there: the writer receives the sections of a page it needs and returns the sections it changed as text, plain code does the writing, **the writer has no tools** — so there is no session to compact and no live page to reopen — and writer input stops tracking wiki size. The splice is built on the measurement Phase 3 produced. Then the rebuild **resumes from the held source**, in order. **Stage 2 is not done until the rebuild has run to the end.** The splice stays conditional; the outcome — a complete wiki, in order — does not.

### Before starting

**[V4] The roll-up step — not needed before Phase 3.** Phase 1 proved the cheap fix holds on a real rewrite, so this step does not gate the rebuild. It stays described here as a maintenance tool for later: a separate model step that writes Current state *after* the batch's sections are finished. It reads the finished sections, never sees the previous roll-up, and has one job, so nothing pulls it the other way. Designed per batch, per touched page — not per source — so the rebuild adds a handful of calls to Brian's page rather than a hundred. It composes with the splice if the splice is built: plain code assembles the sections the writer returned, the roll-up step reads them, plain code inserts the result. Because it reads only the finished sections, the same step can **regenerate every roll-up over an existing wiki without re-ingesting** — which makes it a maintenance tool as well as a fix: change the lens's "covers" line, regenerate; find the rebuild's roll-ups wanting, regenerate rather than redo 215 sources.

### What is in it

- **[V4 ↔ Team Pulse]** With read access to `data/corpus`, we take every source, confirm the archive is complete rather than pruned, and sort by source date. The moment we take them is the **snapshot**; Phase 4 picks up from it.
- **[V4 ↔ Resolve]** A fresh V4 corpus with Team Pulse's lens, then jobs submitted with `wiki_weaver_version: "v4"`. **Each batch is sized by time, not by count:** per-source time grows as the wiki grows (218 to 321 seconds between E01 and E02), so a count that fit early would run over late. Each batch gets as many sources as the most recent per-source time says fit in the budget.
- **[V4]** We watch writer input per call, wall time per source, and holds for size, batch by batch. Whatever happens is recorded.
- **[V4]** At the first size hold: stop; the splice; resume from the held source.
- **[V4]** We read pages and run the three questions we asked after every Stage 1 epoch. This is measurement, not a scored gate.

### Exit criteria

- **[V4 ↔ Resolve]** The rebuild ran to the end as Resolve jobs: every job finished, or was stopped by something we can name.
- **[V4 ↔ Team Pulse]** Every source in Team Pulse's history is converged, in order. If the rebuild stopped at a size hold, the splice landed and the rebuild resumed from that source to completion. Any remaining hold has a kind and a reason, and none is "unknown."
- **[V4 ↔ Resolve]** The resulting zip is inside Resolve's size caps (50 MiB in, 64 MiB out).
- **[V4]** The three questions answer from the wiki, and we have read enough pages to say the shape held at full scale — including Current state on the busiest pages.

### What we need

**From the Team Pulse team:** read access to `data/corpus`, once.
**From the Resolve team:** the model budget — the rebuild is several hundred model calls over days of jobs, on the platform's API key.

---

## Phase 4 — V4 runs alongside V1 on Team Pulse's live sources

### What this is

**[V4 ↔ Team Pulse]** With a complete V4 wiki in hand, every source Team Pulse has processed since the snapshot also goes through V4 into that wiki — one source per job, mirroring their pattern — in order, with no gaps. On our trigger; Team Pulse's scheduler sends nothing twice. A skipped source or a gap after the snapshot makes the two wikis drift apart, and the comparison stops being like for like. Team Pulse keeps reading V1. This proves the incremental path holds on live sources inside Resolve, and it produces the side-by-side. What anyone does with the comparison is a later decision; it is not a gate here.

**Two mechanisms, one of which does not depend on Resolve.** Primary: take each V1 job's input from Resolve's retained inputs and extract the new source. Fallback, if retention is shorter than the gap: diff `data/corpus` against the snapshot and take the new sources directly, in date order. If the splice fires in Phase 3, the gap between snapshot and shadow could be weeks; the fallback is what makes that safe.

**The backlog is caught up first and does not count.** Everything processed between the snapshot and the start of Phase 4 is replayed to bring the V4 wiki level with V1's. Only then does the shadow window start.

### Exit criteria

- **[V4 ↔ Team Pulse]** The backlog since the snapshot is fully replayed, in order, and the V4 wiki is level with V1's.
- **[V4 ↔ Resolve]** After that, at least five shadow runs on live sources, or a week, whichever comes first — every source in that window, in order — with no infrastructure failures. Any failures are V4's own holds, each with a kind.
- **[V4 ↔ Resolve]** Every shadow run's zip stayed inside Resolve's size caps.
- **[V4 ↔ Team Pulse]** We know what on Team Pulse's side reads the wiki after Resolve returns it, and have checked it against V4's shape — or the gap is named and sized.

### What we need

**From the Team Pulse team:** what reads the wiki; continued read access to `data/corpus` for the fallback.
**From the Resolve team:** nothing new.

---

## Stage 2 exit criteria

Stage 2 is done when all of the following are true:

1. **[V4 ↔ Resolve]** The rebuild of Team Pulse's history ran to the end as Resolve jobs — every job finished or was stopped by something we can name.
2. **[V4 ↔ Team Pulse]** Every source in that history is converged, in order. If the rebuild stopped at a size hold, the splice landed and the rebuild resumed to completion; any remaining hold has a kind.
3. **[V4 ↔ Team Pulse]** The backlog since the snapshot is replayed and the V4 wiki is level with V1's.
4. **[V4 ↔ Resolve]** After that, at least five shadow runs on live sources, or a week — every source in the window, in order — completed with no infrastructure failures.
5. **[V4 ↔ Resolve]** The V4 zip stayed inside Resolve's size caps throughout.

That is "V4 running in Resolve, consistently." It is not "Team Pulse has switched."

---

## Known issues the comparison will show

Named here so nobody reads them as a surprise when the V4 and V1 wikis sit side by side.

**Current state on the busiest pages.** Described in Phase 1. The cheap fix is in before the rebuild; the structural fix is in before Phase 3 if the cheap fix was not enough. If either is late, Brian's, Resolve's and Team Pulse's own pages — the ones anyone compares first — will show a roll-up that reads as a running log of meetings rather than a summary. That is a known defect with a known cause and a planned fix, not a property of the design.

**A pause at the size wall.** Described in Phase 3. If someone looks while the splice is being built, the rebuild will be stopped partway, with one source in `.wiki/failed/` marked `oversized-context` and nothing after it ingested yet. That is the expected shape of the wall, and the trigger for the fix.

---

## What we are not doing yet, and why

Brian's guidance: get the end-to-end working first; fix what the run shows needs fixing. Each item here has a trigger, and most will probably fire. They are deferred, not dropped.

**The splice — until Phase 3 shows the need.** Described above. With E02's numbers we expect it to fire. We run first anyway, so the splice is built on Team Pulse's real measurements rather than our extrapolation, and so the wall is a recorded fact rather than a projection.

**The four data-safety items.** An independent code review found four weaknesses in V4 — two involving a link inside the corpus pointing outside it, one a crash in the instant between committing and recording "done" followed by a human edit, one scratch left staged in git. PLAN records them as blockers for Stage 2 when Stage 2 was understood to include switching Team Pulse. **None of them is reachable on the Resolve path:** Team Pulse's zip is built by code and contains no such links (and the resolver refuses them on both pack and unpack — verified in Phase 1); nobody edits the corpus between jobs; and `.git` does not travel, so nothing stays staged. They are real for V4 run by hand on a corpus someone else built. They move to the switch addendum, to be closed — by the splice if it is built, or by direct fixes if not — before V4 is offered beyond Team Pulse.

**The scored evaluation.** Phase 4 produces a like-for-like side-by-side of V1 and V4 on the same sources. Judging which is better, formally, is the evaluation Brian deferred on 9/30. The comparison is there for whoever wants to look; it does not gate Stage 2.

**Merging to `main`.** V4 stays on the `v4` branch; the worker image installs it from a pinned `v4` commit. Ken's coexistence design removed the only reason to merge early. The merge happens when we decide, later.

---

## Addendum — switching Team Pulse to V4 (optional, later, not part of Stage 2)

Written down so it can be tracked; not scheduled.

**The decision** is Team Pulse's, in collaboration with us, once Stage 2 is done and they have looked at the V4 wiki.

**Before it:** the four data-safety items closed, by the splice or by direct fixes; whatever reads the wiki on Team Pulse's side confirmed against V4's shape; who owns the lens after the switch agreed.

**The switch itself is two things.** On Resolve's side, Team Pulse's jobs start passing `wiki_weaver_version: "v4"` — or the resolver's default flips from `main` to `v4` — one reviewed change, enforced by the `--version` preflight. On Team Pulse's side, the wiki pages in `data/corpus` are replaced by the V4-built wiki from Phases 3–4, which by then is level with V1's. Their next job packages from the V4 wiki and sends it to the V4 pin. Their code does not change.

**Reverting** is the same two steps in reverse: the pin back, the V1 pages restored. Checked, not assumed, before the switch.

**Merging V4 to `main`** belongs here too, once any lane whose worker image builds from `main` is pinned, so that landing on `main` cannot swap anything by accident.

---

## Alignment with PLAN.md §6 (for the WikiWeaver side)

| PLAN §6 says | This plan |
|---|---|
| Stage 2: the four verbs and the corpus zip over the same pipeline, called the way Team Pulse's current version is called; the CLI path is the bridge; running the graph directly is the destination | The same. The CLI path through the weaver resolver is every phase here. Running the graph directly, through Resolve's dot-graph resolver, remains the destination after the switch |
| Done when one job executes in Resolve and Team Pulse can call it without noticing; "days after Stage 1" | Phase 2 is the first clause. "Can call it" is read as capability — the interface is unchanged — which Stage 2 proves; actually switching is the addendum. Timing is weeks, not days: the rebuild alone is days of jobs |
| First after Stage 1: the splice, one branch, one milestone; then the full 215 under the final design | **Changed.** The full corpus is Phase 3 and is Team Pulse's actual history. The splice is triggered by the first size hold in that rebuild rather than built first; when triggered, it is inside Stage 2, and the rebuild resumes behind it. Its scope and done-when stay as written in §6 |
| §9: no migration — V4 re-ingests from sources and does not convert a V1 or V2 wiki | Phase 3 is exactly that |
| Owed: interface tests from both contracts | Phase 1 |
| Owed: `--limit ≤ 0` → exit 2; hold logging when a batch is all-held | Phase 1 |
| Owed: the `update` verb, open with Marc | Asked in Phase 2. The resolver never calls it; the stub likely stands |
| Owed: CI fixed before the `main` merge | Phase 1 |
| Owed: merge sequence `v4-build → v4 → main` | **Changed.** `v4-build → v4` for Stage 2; `→ main` moves to the addendum |
| §10: merged to `main` once Stage 2 passes; landing there *is* the transparent swap | **Changed.** The merge is at the switch, not at Stage 2; and the swap is not transparent — Team Pulse's wiki is rebuilt from sources, per §9 |
| The four data-safety items, recorded as Stage 2 blockers | **Re-scoped.** They were blockers for a Stage 2 that included the switch. Stage 2 no longer does; they move to the addendum, with the reason stated above |

Four recorded changes — the splice's trigger, the merge sequence, the §10 swap line, the data-safety scope — so PLAN.md and this document do not disagree silently.

---

## Owners

| | |
|---|---|
| **WikiWeaver (us)** | Phase 1; the lens; Phase 2's jobs; the Phase 3 rebuild, its measurements, and the splice if triggered; the Phase 4 backlog and shadow runs; the comparison |
| **Resolve — Ken** | Retention (pending); access and budget (unanswered); where the image is built and deployed; the `fa01aa0` question; the re-pin review; the two compaction asks; the watchdog and the other items in the platform notes already sent. ACA, egress, PR #7, the dead-job question, and the V4 mechanism: answered — the last by building it |
| **Resolve — Marc** | The exclude-list line; the `update` verb decision; emitting `summary` so Resolve's `result.summary` is not null for weaver jobs; the pin change if and when the switch happens |
| **Team Pulse — Samuel, Alex, Devis** | Sources and parameters for Phase 2; the packaging code; read access to `data/corpus`; what reads the wiki; and, later, the decision to switch |
| **Brian** | The Stage 1 report; the splice when Phase 3 triggers it; whether the deferred evaluation comes back |

## Open questions

Answered by Ken (DECISIONS 2026-10-14 and 2026-10-20): ACA is the only lane; ACA reaches Anthropic and GitHub; the resolver should always return the artifact; PR #7 is not a foundation; how V4 gets into the worker and how a job picks it (#458, #10); the `.wiki/work/` exclusion (#10). The second-CLI-in-`main` question is closed — no rename was needed.

1. **[Resolve]** How long does ACA retain a job's input after completion? (Ken: answer to follow.)
2. **[Resolve]** Access for us to submit jobs, and the model budget for the rebuild. (Asked three times; unanswered.)
3. **[Resolve]** Where is the worker image built and deployed from, and is staging real? Neither repo has a build workflow; the meeting said staging was "still being solidified."
4. **[Resolve]** Why dot-runner `fa01aa0` rather than `80d5511`? `80d5511` no longer installs fresh; we are re-verifying V4 on `fa01aa0`.
5. **[Resolve]** Can the worker pass `enable_1m_context` through, and can dot-runner persist the `context:compaction` event into node output?
6. **[Resolve, policy]** Should V4 recovery failure withhold the archive, or ship it flagged? Ken's "artifact regardless of outcome" says ship; #10 withholds. Marked PROPOSED in the review.
7. **[Team Pulse]** What on your side reads the wiki after Resolve returns it, and what does it expect to find?
8. **[Team Pulse]** How does the packaging code choose "relevant pages," and would sending all pages be a problem?
9. **[Team Pulse]** Is the source archive in `data/corpus` complete, or pruned? What parameters does each job pass? How do returned pages get merged back?
10. **[Team Pulse]** How do you want a rebuilt V4 wiki surfaced to you — Ken's suggestion is via your API, your design.
