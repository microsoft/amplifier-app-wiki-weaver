# Stage 2 — Running WikiWeaver V4 in Resolve

*The execution plan for PLAN.md §6, Stage 2. Written for three readers — the WikiWeaver side (us), the Resolve platform team (Ken, Marc), and the Team Pulse team (Samuel, Alex) — after the 2026-10-12 planning meeting, the code investigations that followed it, three rounds of review, and Samuel's description of how Team Pulse calls the weaver today. Stage 1 is closed.*

---

## The short version

**What this is.** WikiWeaver V4 replaces V1 as the engine that turns Team Pulse's sources into a wiki. It runs behind the same Resolve interface — same four commands, same zip in, same zip out — so nothing in Resolve or Team Pulse changes to run it. Stage 2 ends with V4 running in Resolve consistently: a V4 wiki of Team Pulse's full history, built inside Resolve, and V4 running alongside V1 on live jobs so the two can be compared. **Switching Team Pulse to V4 is a later, separate decision, and it is theirs.**

**Resolve team (Ken, Marc).** Nothing changes in the platform. Team Pulse runs on ACA (per Samuel; confirming with Ken). We need: a second catalog entry — `weaver-v4` — pinned to a V4 commit, so V4 jobs run beside `weaver` without touching it; one line added to the resolver's zip-exclude list (`.wiki/work/`); confirmation that ACA workers can reach `api.anthropic.com` and `github.com`; how long ACA retains job inputs; whether the resolver returns a zip when a job dies mid-run; access for us to submit jobs; and the model budget for a rebuild that is several hundred model calls over days of jobs. Budget two or three pin updates as V4 changes during Stage 2.

**Team Pulse team (Samuel, Alex).** Nothing changes in your code or your schedule. We need: read access to `data/corpus` on your server, to take the sources for the rebuild; your packaging code, to understand exactly what each job sends — V4 assumes the whole wiki is present, and you send a subset; your job parameters; and to know what on your side reads the wiki after Resolve returns it. The shadow runs are on our trigger, not yours.

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

**Resolve team:** nothing in the platform. The existing weaver resolver — the wrapper that unpacks the zip, runs four `wiki-weaver` commands, and repacks — stays as it is; V4 answers those four commands and writes the same result file V1 does, verified by running the code. On ACA, the resolver's CLI environment is built at job start from pinned requirements, so "which wiki-weaver" is a catalog entry, not an image. Three things on your side: a `weaver-v4` catalog entry pinned to a V4 commit (Phase 2); one line added to the resolver's list of folders left out of the zip (Phase 1); and two or three pin updates over Stage 2 as V4 changes — Phase 1 changes it, the splice may change it again. The contract is the boundary, so Resolve never notices; the pins just have to follow. Separately, we have shared a set of platform observations with Ken — watchdog behavior by lane, failure reporting, retention, the image fallback — tracked there, not here.

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

### What this is

Before V4 runs inside Resolve, a few things about how it behaves there need to be proven rather than assumed, what it reports needs to be useful to the people who will see it, one assumption about the model needs checking, and one known writing defect needs its cheap fix in place before the rebuild amplifies it. Phase 1 changes the pipeline graph — the writer's input assembly, the model rule, the hold vocabulary, the run id. Each change has a Resolve reason; each means Resolve's pin moves to a new commit.

### For the Resolve integration

**[V4 ↔ Resolve] Scratch and the zip round trip.** Resolve packs the corpus into a zip after each job and unpacks it for the next, and deliberately leaves out the corpus's `.git` history. V4 keeps a scratch folder, `.wiki/work/`, during a run and clears it on success; its safety rule is that scratch left behind means a run died, so the next run stops and asks a human to look rather than guess. Inside Resolve that rule has nothing to work with — `.git` is gone, so there is no history to recover against — and a stopped job would stop every job after it. The fix is at the contract level: `.wiki/work/` joins `.git`, `.wiki/runs/` and `.wiki/snapshots/` in the list of folders that do not round-trip (`contracts/corpus-package.v1.md`, clause 10, committed), and the resolver's pack list follows the contract. **For Marc:** one line in the resolver's exclude list. On V4's side, a narrow fallback only: if `init` has just created `.git` from nothing *and* scratch is present, clear it, because there is provably no history to recover against. The rule stays intact everywhere else.

**[V4 ↔ Resolve] Proving V4 does what the interface documents say.** We wrote two short documents describing exactly what Resolve's weaver resolver calls and expects back: the commands and flags (`contracts/cli-surface.v1.md`) and the files and folders in the corpus zip (`contracts/corpus-package.v1.md`). Both were written by reading the resolver's code. Each says automated tests exist that check V4 against it; those tests were never written. Until they exist, "V4 matches what the resolver calls" is a reading of the code, not a command anyone can run. These tests are the Resolve team's assurance as much as ours.

**[V4 ↔ Resolve] Saying which version ran.** V4's result file does not name the wiki-weaver version that produced it. With V1 and V4 running side by side in Resolve, that is the first thing anyone will want to know. Fix: add it.

**[V4 ↔ Resolve] Saying why something failed.** When V4 cannot integrate a source it holds it and records a kind and a reason, and that record reaches Resolve's completion payload verbatim. Today a model timeout, a wrong API key, a provider outage and a connection error all get the same kind with identical text; the real cause sits in an engine log that never leaves the container. Ken asked to tell infrastructure trouble from "the work didn't go well." Fix: carry the engine's cause into the hold, give run-level errors a kind, and put the vocabulary under test so it stays accurate.

**[V4 ↔ Resolve] Verifying two claims about the zip.** The platform investigation reported that the resolver skips symbolic links when it packs the zip, and that the platform pinned Python 3.12 so that unpacking rejects links that escape the folder. We have not verified either claim ourselves. Doing so is a short read of two code paths, and it settles how much of the deferred data-safety list (below) the Resolve path already covers.

### For the Team Pulse integration

**[V4 ↔ Team Pulse] Reading the packaging code.** Team Pulse sends each job the pages it judges relevant, not the whole wiki. V4 assumes the whole wiki: its brief step selects pages from `index.md`, its link check requires every internal link to resolve to a file on disk, and its loss guard compares against what is there. If `index.md` names pages that are not in the zip, or pages link to pages that are absent, V4's checks hold the source where V1 might have shrugged. Before Phase 2 we read Team Pulse's packaging code and decide which of three things is true: V4 works on their job shape as-is; "relevant pages" should become "all pages," which is small for a wiki of a few hundred markdown files; or V4's checks need to tolerate a partial corpus. Phase 2's test job is then shaped like their real job, not like ours.

### The model — one assumption to check

**[V4] The writer's model and its context window.** In V4's pipeline graph, the style rule that assigns a model to the writing step used an underscore in its name; the engine's grammar accepts only letters, digits and hyphens, so the rule did not apply. Stage 1's logs show the writing step ran on Sonnet regardless — the worker's default happened to be the model we intended. That proves which model ran. **It does not prove which context window it had.** V4's size guard holds a source when the writer's input would exceed 3.5 million characters — roughly 875K tokens — which assumes the large window the broken rule was meant to request. If the writer actually has a standard window, E02's largest call at 805K characters was already near the edge, and the rebuild would hit a provider error long before the guard fires. That error would surface as a confusing "model step failed," not the clean "too big" hold the rest of this plan depends on, and Phase 3's trigger would never fire. Fix: repair the rule so the assignment is explicit; add a test that resolves each step's model through the engine; **confirm the writer's context window from the provider, and set the size guard from the confirmed number.** If the window is standard, the splice is needed far sooner than the extrapolation says.

### A known writing defect — the cheap fix

**[V4] Current state stacking.** In E02, on pages touched by many sources, the writer kept the previous roll-up under a heading like "Earlier roll-up, kept for history" and stacked new paragraphs led by the latest meeting on top. Median roll-up length went from 2,572 to 4,088 characters, maximum 10,618. The cause is a conflict inside one model call: on every other section the rule is *never delete*, and a check enforces it — a write that loses more than 15% of a page's lines is rejected, and that check fired eight times in E02; for Current state the rule is the opposite, *replace in full*, and it was only a sentence. The writer followed the rule that had a check behind it. It was complying, not disobeying. We tried three different sentences; none held, for the same reason the source-link rule needed a check.

The cheap fix: strip Current state from the page before the writer sees it and ask for a new one. It cannot keep what it never received. (The loss guard already excludes Current state from its comparison — `checks.py:30–56` — so nothing changes there.) A few lines of code, no new model call. **Tested on a copy of the Stage 1 wiki's busiest pages, so the validated artifact does not change**: re-ingest a few changed sources that touch Brian's page with the strip in place, read the result. If the stacking is gone and the roll-up reads as a summary, this is the fix for now. If it still leads with the latest meeting, the structural fix goes in before Phase 3 (see Phase 3, "Before starting") — and that step's regenerate mode is the clean test for it: run it once over the copy and read every roll-up.

### In addition — V4 cleanup

**[V4] Small things.** Two runs starting in the same second collide on a directory name. `--limit 0` is treated as "no limit" where the interface document says reject it. CI still points at V1's deleted `eval/` suite. And — **[V4 ↔ RepoWeaver, not Team Pulse]** — placeholder `.gitkeep` files sit in a folder RepoWeaver counts as failures; trivial to remove.

### Exit criteria

- **[V4 ↔ Resolve]** `.wiki/work/` is in the contract's round-trip exclusions (done), and Marc has confirmed the resolver's exclude list matches.
- **[V4 ↔ Resolve]** The two interface tests exist and pass.
- **[V4 ↔ Resolve]** V4's result file names the wiki-weaver version.
- **[V4 ↔ Resolve]** A forced timeout, a wrong API key and a connection failure produce three *different* kinds in V4's result file.
- **[V4 ↔ Resolve]** The two zip claims are verified or refuted, with the evidence written down.
- **[V4 ↔ Team Pulse]** Team Pulse's packaging code has been read, and we know which of the three answers above is true.
- **[V4]** The writer's model is explicit and tested; its context window is confirmed from the provider; the size guard is set from the confirmed number.
- **[V4]** The Current state cheap fix is in, and its test on the copy is recorded — stacking gone, and a note on whether recency remains.
- **[V4]** V4's full test suite passes.

---

## Phase 2 — V4 runs a job inside Resolve, shaped like Team Pulse's

### What this is

V4 runs **inside a Resolve worker container on ACA**, submitted the way Team Pulse submits, through the existing weaver resolver — a zip in, a wiki out. End to end first, small, before anything at scale. It is the first time V4 runs anywhere we do not control, and the first time it calls a model from inside Resolve.

### Before starting — answers from the Resolve team

1. **[V4 ↔ Resolve] Confirm ACA.** Samuel says Team Pulse calls the weaver through Resolve ACA. Confirming with Ken, because time limits, retention and failure reporting differ by lane. On ACA, from the platform read: the resolver's CLI environment is built at job start from pinned requirements; nothing kills a hung job by default; API mode separates infrastructure failures from resolver outcomes by `failure_code`; inputs are retained, with no expiry we could find.
2. **[V4 ↔ Resolve] Egress.** Can ACA workers reach `api.anthropic.com` and `github.com` at run time? V4's engine fetches its modules from GitHub on first use and resolves model names live against the provider.
3. **[V4 ↔ Resolve] Retention.** How long does ACA keep a job's input after completion? Phase 4's primary mechanism depends on it; the fallback does not.
4. **[V4 ↔ Resolve] A dead job's zip.** When a job dies mid-run, does the resolver return an output zip at all? If no, a half-written page on disk never travels and the next job starts from the previous zip. If yes, that page travels, and the next job's fresh `init` commits it as the baseline — with the scratch that would have flagged the dead run now excluded by design. Then pages need atomic writes, which the splice provides by construction.

### One check on our side first

**[V4 ↔ Resolve]** The resolver runs V4's setup command on *every* job, including jobs that arrive with an already-configured corpus. If V4 regenerated the lens file in that case, every job would destroy the wiki's configuration. One hour to test; if it fails, fix it before any job runs.

### What is in it

- **[V4 ↔ Resolve] The catalog entry.** On ACA, which wiki-weaver runs is decided by pinned requirements the resolver's setup installs at job start — the mechanism from Ken's PR #7, whose head commit the hosted catalog already pins even though the PR is still a draft. The switch point is therefore not a branch we own but a catalog change Resolve owns: a second entry, `weaver-v4`, with its pins pointing at a commit on wiki-weaver's `v4` branch, plus the API image rebuild that makes the catalog live. Team Pulse keeps calling `weaver`; we call `weaver-v4`. We supply the commit SHA. V4 stays on `v4`; nothing merges to `main` in Stage 2. **For Ken:** is PR #7's draft the intended steady state, and what happens to the catalog pin if the branch is rebased?
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

**From the Resolve team:** ACA confirmed; egress; retention; the dead-job answer; PR #7's steady state; the `weaver-v4` catalog entry; the exclude-list line; access for us to submit jobs.
**From the Team Pulse team:** a handful of representative sources; the packaging code; which parameters you pass today (`limit`, `max_cycles`, `keep_going`, a purpose text or `--plain`).

---

## Phase 3 — Team Pulse's wiki exists in V4, built inside Resolve

### What this is

**[V4 ↔ Team Pulse]** Team Pulse's full history — every source in `data/corpus` on their server — through V4, **inside Resolve**, into a fresh wiki initialized with their lens. It runs as a series of `weaver-v4` jobs in chronological source order, so that dated entries and supersession markers come out right. This is the biggest thing V4 will do in Resolve. It is also the run that tells us whether V4 at Team Pulse's scale needs the one change we have deliberately held back.

### What we expect, and the trigger

**[V4]** On the current page shape, writer input quadrupled from 31 to 65 sources: the median went from 126K to 518K characters, the maximum from 411K to 805K, and one call was already over 800K; wall time per source went from 218 to 321 seconds. Team Pulse's history is more than 215 sources and has been growing every day since August. Extrapolated, the largest writer calls reach the size guard before the rebuild finishes — sooner, if Phase 1 finds the writer's window is standard — and the rebuild is days of jobs, not hours. **We expect an oversized-context hold on the busiest pages somewhere in the back half of the rebuild. Phase 3 measures where.**

**The rebuild stops at the first oversized-context hold.** Not "holds it and continues": continuing would ingest newer sources ahead of the held one, so that when it comes back after the splice it lands out of order, its entries read as newest, and the history markers can wrap the wrong text; and once the busiest pages are full, every later source that touches them is bound to be held too, at full model cost. So: stop. **That first hold triggers the splice** — the change PLAN §6 describes, scope as written there: the writer receives the sections of a page it needs and returns the sections it changed, plain code does the writing, and writer input stops tracking wiki size. The splice is built on the measurement Phase 3 produced. Then the rebuild **resumes from the held source**, in order. **Stage 2 is not done until the rebuild has run to the end.** The splice stays conditional; the outcome — a complete wiki, in order — does not.

### Before starting

**[V4] The roll-up step, if the Phase 1 test says so.** If the cheap fix left Current state leading with the latest meeting on busy pages, the structural fix goes in here, before 215-plus passes over exactly the pages people will compare: a separate model step that writes Current state *after* the batch's sections are finished. It reads the finished sections, never sees the previous roll-up, and has one job, so nothing pulls it the other way. Designed per batch, per touched page — not per source — so the rebuild adds a handful of calls to Brian's page rather than a hundred. It composes with the splice if the splice is built: plain code assembles the sections the writer returned, the roll-up step reads them, plain code inserts the result. Because it reads only the finished sections, the same step can **regenerate every roll-up over an existing wiki without re-ingesting** — which makes it a maintenance tool as well as a fix: change the lens's "covers" line, regenerate; find the rebuild's roll-ups wanting, regenerate rather than redo 215 sources.

### What is in it

- **[V4 ↔ Team Pulse]** With read access to `data/corpus`, we take every source, confirm the archive is complete rather than pruned, and sort by source date. The moment we take them is the **snapshot**; Phase 4 picks up from it.
- **[V4 ↔ Resolve]** A fresh V4 corpus with Team Pulse's lens, then `weaver-v4` jobs on ACA. **Each batch is sized by time, not by count:** per-source time grows as the wiki grows (218 to 321 seconds between E01 and E02), so a count that fit early would run over late. Each batch gets as many sources as the most recent per-source time says fit in the budget.
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

**The four data-safety items.** An independent code review found four weaknesses in V4 — two involving a link inside the corpus pointing outside it, one a crash in the instant between committing and recording "done" followed by a human edit, one scratch left staged in git. PLAN records them as blockers for Stage 2 when Stage 2 was understood to include switching Team Pulse. **None of them is reachable on the Resolve path:** Team Pulse's zip is built by code and contains no such links (and the resolver very likely filters them — Phase 1 verifies); nobody edits the corpus between jobs; and `.git` does not travel, so nothing stays staged. They are real for V4 run by hand on a corpus someone else built. They move to the switch addendum, to be closed — by the splice if it is built, or by direct fixes if not — before V4 is offered beyond Team Pulse.

**The scored evaluation.** Phase 4 produces a like-for-like side-by-side of V1 and V4 on the same sources. Judging which is better, formally, is the evaluation Brian deferred on 9/30. The comparison is there for whoever wants to look; it does not gate Stage 2.

**Merging to `main`.** V4 stays on the `v4` branch. Resolve's catalog pins a `v4` commit. The merge happens when we decide, later.

---

## Addendum — switching Team Pulse to V4 (optional, later, not part of Stage 2)

Written down so it can be tracked; not scheduled.

**The decision** is Team Pulse's, in collaboration with us, once Stage 2 is done and they have looked at the V4 wiki.

**Before it:** the four data-safety items closed, by the splice or by direct fixes; whatever reads the wiki on Team Pulse's side confirmed against V4's shape; who owns the lens after the switch agreed.

**The switch itself is two things.** On Resolve's side, `weaver`'s catalog pins move to the V4 commit — or Team Pulse's calls move to `weaver-v4` — one change, reviewed, enforced by the runtime check. On Team Pulse's side, the wiki pages in `data/corpus` are replaced by the V4-built wiki from Phases 3–4, which by then is level with V1's. Their next job packages from the V4 wiki and sends it to the V4 pin. Their code does not change.

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
| **Resolve — Ken** | ACA confirmation; egress; retention; the dead-job answer; PR #7 and the `weaver-v4` catalog entry; access and budget; the watchdog and the other items in the platform notes already sent |
| **Resolve — Marc** | The exclude-list line; the `update` verb decision; emitting `summary` so Resolve's `result.summary` is not null for weaver jobs; the pin change if and when the switch happens |
| **Team Pulse — Samuel, Alex** | Sources and parameters for Phase 2; the packaging code; read access to `data/corpus`; what reads the wiki; and, later, the decision to switch |
| **Brian** | The Stage 1 report; the splice when Phase 3 triggers it; whether the deferred evaluation comes back |

## Open questions

1. **[Resolve]** Confirm Team Pulse runs only on ACA.
2. **[Resolve]** PR #7's head is pinned in the hosted catalog but the PR is a draft — is that the intended steady state, and what happens to the pin if the branch is rebased?
3. **[Resolve]** Can ACA workers reach `api.anthropic.com` and `github.com` at run time?
4. **[Resolve]** How long does ACA retain a job's input after completion?
5. **[Resolve]** When a job dies mid-run, does the resolver return an output zip?
6. **[Team Pulse]** What on your side reads the wiki after Resolve returns it, and what does it expect to find?
7. **[Team Pulse]** How does the packaging code choose "relevant pages," and would sending all pages be a problem?
8. **[Team Pulse]** Is the source archive in `data/corpus` complete, or pruned? What parameters does each job pass?
