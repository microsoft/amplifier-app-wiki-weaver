# Stage 2 — Running WikiWeaver V4 in Resolve

*The execution plan for PLAN.md §6, Stage 2. Written for three readers — the WikiWeaver side (us), the Resolve platform team (Ken, Marc), and the Team Pulse team (Samuel, Alex) — after the 2026-10-12 planning meeting, the code investigations that followed it, and two rounds of review. Stage 1 is closed.*

---

## The short version

**What this is.** WikiWeaver V4 replaces V1 as the engine that turns Team Pulse's meeting notes into a wiki. It runs behind the same Resolve interface — same four commands, same zip in, same zip out — so nothing in Resolve or Team Pulse changes to run it. Stage 2 ends with V4 running in Resolve consistently: a V4 wiki of Team Pulse's full history, built inside Resolve, and V4 running alongside V1 on live batches so the two can be compared. **Switching Team Pulse to V4 is a later, separate decision, and it is theirs.**

**Resolve team (Ken, Marc).** Nothing changes in the platform. We need: which lane Team Pulse runs on; whether workers there can reach `api.anthropic.com` and `github.com` at run time; whether that lane retains job inputs; the status of PR #7; and one line added to the resolver's zip-exclude list (`.wiki/work/`). Marc owns the version pin if a switch ever happens.

**Team Pulse team (Samuel, Alex).** Nothing changes in your code or your schedule. We need: your current zip, once, to extract the sources for the rebuild; a handful of representative sources and your job parameters for the first test; and to know what on your side reads the wiki after Resolve returns it. The shadow runs are on our trigger, not yours.

---

Every item below is tagged with whose system it concerns:

- **[V4]** — a change inside WikiWeaver V4 itself.
- **[V4 ↔ Resolve]** — about how V4 behaves inside Resolve, or what Resolve sees from it.
- **[V4 ↔ Team Pulse]** — about what Team Pulse sends and gets back.

---

## What this is

**Today.** Team Pulse zips its wiki plus a batch of new meeting notes and hands the zip to Resolve. Resolve runs WikiWeaver V1 in a sealed container and hands back the updated wiki. Team Pulse stores that zip and sends it again with the next batch.

**What is different about V4.** The interface is the same; the wiki is not. V1 writes pages organized by theme with a history section per page, cites with numbered ids, decides when a page is finished by having a model judge its own work, and — a known defect — can silently drop content on rewrite. V4 opens every page with a maintained summary of where things stand *now*, organizes the record below by what matters — owners, commitments, blockers, decisions, open questions — rather than by meeting, cites every specific claim with a quoted passage an agent can verify in one search, and keeps history visible with dated markers instead of deleting it. A **lens** file the owner controls says what the wiki is for and what each page type tracks, so the same engine serves a team status wiki and a personal knowledge base without code changes. Deterministic checks replace the model judging the model: nothing is lost, nothing is cited that is not in the source, and when a source cannot be integrated it is held with a stated reason rather than forced in. Stage 1 measured V4 at: zero headings outside the intended set across every page; history markers correct 28 of 28 times; 31 of 31 sources ingested unattended; answers drawn from 110–156K characters of wiki rather than the whole corpus. Crash recovery, locking and containment went through three rounds of independent code review, with the remaining findings deferred (see "What we are not doing yet").

**How Team Pulse's wiki becomes a V4 wiki.** The zip Team Pulse holds today contains a V1-shaped wiki. V4 does not convert it (PLAN §9: "the rebuild is a rebuild"), and writing V4 pages into V1 pages would produce a mixture nobody designed. So a V4 wiki for Team Pulse is **built from scratch, from their sources** — and the V1 zip already carries every source Team Pulse has ever ingested, byte-for-byte, in its `_sources/` folder. That rebuild is Phase 3. There is no migration step, by design.

**What Stage 2 is, and is not.** Stage 2 ends with **V4 running in Resolve, consistently, alongside V1**: a complete V4 wiki of Team Pulse's history exists, built inside Resolve; and every V1 job since that wiki was built has also run through V4, so the two can be compared like for like. Team Pulse keeps reading V1 throughout. **Switching Team Pulse to V4 is not part of Stage 2.** It is written up as an addendum at the end so it can be tracked — not executed.

**The method.** Brian's: get it working end to end first; fix what the run shows needs fixing, and only that. Every phase below is a run, and every deferred item below has a trigger.

## What changes for each team

**Resolve team:** nothing in the platform. The existing weaver resolver — the wrapper that unpacks the zip, runs four `wiki-weaver` commands, and repacks — stays as it is; V4 answers those four commands and writes the same result file V1 does, verified by running the code. Two things on your side: a branch of the resolver that points its version pin at V4 (Phase 2), and one line added to the resolver's list of folders left out of the zip (Phase 1). Separately, we have shared a set of platform observations with Ken — watchdog behavior by lane, failure reporting, retention, the image fallback — tracked there, not here.

**Team Pulse team:** nothing in your code and nothing in your schedule. You keep sending zips to the same place with the same parameters and reading V1's output. We will need your current zip once, to extract the sources for the rebuild. The shadow runs are ours to trigger — we take each V1 job's input after it runs and submit it to V4 — so your scheduler sends nothing twice. The one thing we need to understand before anyone talks about switching is what on your side reads the wiki after Resolve returns it.

---

## The phases

| | Outcome | Mostly whose work |
|---|---|---|
| **Phase 1** | V4 is ready to run where we don't control the environment | us |
| **Phase 2** | V4 runs a job inside Resolve | us, with answers from Resolve |
| **Phase 3** | Team Pulse's wiki exists in V4, built inside Resolve | us, with one zip from Team Pulse |
| **Phase 4** | V4 runs alongside V1 on Team Pulse's live inputs | us |

---

## Phase 1 — V4 is ready to run where we don't control the environment

### What this is

Before V4 runs inside Resolve, a few things about how it behaves there need to be proven rather than assumed, what it reports needs to be useful to the people who will see it, and one known writing defect needs its cheap fix in place before the rebuild amplifies it.

### For the Resolve integration

**[V4 ↔ Resolve] Scratch and the zip round trip.** Resolve packs the corpus into a zip after each job and unpacks it for the next, and deliberately leaves out the corpus's `.git` history. V4 keeps a scratch folder, `.wiki/work/`, during a run and clears it on success; its safety rule is that scratch left behind means a run died, so the next run stops and asks a human to look rather than guess. Inside Resolve that rule has nothing to work with — `.git` is gone, so there is no history to recover against — and a stopped job would stop every job after it. The fix is at the contract level: `.wiki/work/` joins `.git`, `.wiki/runs/` and `.wiki/snapshots/` in the list of folders that do not round-trip (`contracts/corpus-package.v1.md`, clause 10), and the resolver's pack list follows the contract. **For Marc:** one line in the resolver's exclude list. On V4's side, a narrow fallback only: if `init` has just created `.git` from nothing *and* scratch is present, clear it, because there is provably no history to recover against. The rule stays intact everywhere else.

**[V4 ↔ Resolve] Proving V4 does what the interface documents say.** We wrote two short documents describing exactly what Resolve's weaver resolver calls and expects back: the commands and flags (`contracts/cli-surface.v1.md`) and the files and folders in the corpus zip (`contracts/corpus-package.v1.md`). Both were written by reading the resolver's code. Each says automated tests exist that check V4 against it; those tests were never written. Until they exist, "V4 matches what the resolver calls" is a reading of the code, not a command anyone can run. These tests are the Resolve team's assurance as much as ours.

**[V4 ↔ Resolve] Saying which version ran.** V4's result file does not name the wiki-weaver version that produced it. With V1 and V4 running side by side in Resolve, that is the first thing anyone will want to know. Fix: add it.

**[V4 ↔ Resolve] Saying why something failed.** When V4 cannot integrate a source it holds it and records a kind and a reason, and that record reaches Resolve's completion payload verbatim. Today a model timeout, a wrong API key, a provider outage and a connection error all get the same kind with identical text; the real cause sits in an engine log that never leaves the container. Ken asked to tell infrastructure trouble from "the work didn't go well." Fix: carry the engine's cause into the hold, give run-level errors a kind, and put the vocabulary under test so it stays accurate.

**[V4 ↔ Resolve] Verifying two claims about the zip.** The platform investigation reported that the resolver skips symbolic links when it packs the zip, and that the platform pinned Python 3.12 so that unpacking rejects links that escape the folder. We have not verified either claim ourselves. Doing so is a short read of two code paths, and it settles how much of the deferred data-safety list (below) the Resolve path already covers.

### A known writing defect — the cheap fix

**[V4] Current state stacking.** In E02, on pages touched by many sources, the writer kept the previous roll-up under a heading like "Earlier roll-up, kept for history" and stacked new paragraphs led by the latest meeting on top. Median roll-up length went from 2,572 to 4,088 characters, maximum 10,618. The cause is a conflict inside one model call: on every other section the rule is *never delete*, and a check enforces it — a write that loses more than 15% of a page's lines is rejected, and that check fired eight times in E02; for Current state the rule is the opposite, *replace in full*, and it was only a sentence. The writer followed the rule that had a check behind it. It was complying, not disobeying. We tried three different sentences; none held, for the same reason the source-link rule needed a check.

The cheap fix: strip Current state from the page before the writer sees it and ask for a new one — it cannot keep what it never received — and exclude that section from the loss guard's comparison, so a shorter new roll-up does not read as lost lines. A few lines of code, no new model call. **Tested first on E02's busiest pages**: re-ingest a few changed sources that touch Brian's page with the strip in place, read the result. If the stacking is gone and the roll-up reads as a summary, this is the fix for now. If it still leads with the latest meeting, the structural fix goes in before Phase 3 (see Phase 3, "Before starting") — and that step's regenerate mode is the clean test for it: run it once over a copy of E02 and read every roll-up.

### In addition — V4 cleanup

**[V4] The writer's model assignment.** In V4's pipeline graph, the style rule that assigns a model to the writing step used an underscore in its name; the engine's grammar accepts only letters, digits and hyphens, so the rule did not apply. Stage 1's logs show the writing step ran on Sonnet regardless — the worker's default happened to be the model we intended, so the effect was nil. Phase 1 fixes the rule so the assignment is explicit rather than a default, adds a test that resolves each step's model through the engine, and confirms from the logs what ran.

**[V4] Small things.** Two runs starting in the same second collide on a directory name. `--limit 0` is treated as "no limit" where the interface document says reject it. CI still points at V2's deleted test suite. And — **[V4 ↔ RepoWeaver, not Team Pulse]** — placeholder `.gitkeep` files sit in a folder RepoWeaver counts as failures; trivial to remove.

### Exit criteria

- **[V4 ↔ Resolve]** `.wiki/work/` is in the contract's round-trip exclusions, and Marc has confirmed the resolver's exclude list matches.
- **[V4 ↔ Resolve]** The two interface tests exist and pass.
- **[V4 ↔ Resolve]** V4's result file names the wiki-weaver version.
- **[V4 ↔ Resolve]** A forced timeout, a wrong API key and a connection failure produce three *different* kinds in V4's result file.
- **[V4 ↔ Resolve]** The two zip claims are verified or refuted, with the evidence written down.
- **[V4]** The Current state cheap fix is in, and its test on E02's busiest pages is recorded — stacking gone, and a note on whether recency remains.
- **[V4]** A test proves the writer resolves to the intended model.
- **[V4]** V4's full test suite passes.

---

## Phase 2 — V4 runs a job inside Resolve

### What this is

V4 runs one small job **inside a Resolve worker container**, submitted the way Team Pulse submits, through the existing weaver resolver — a zip in, a wiki out. End to end first, on a fresh, small corpus, before anything at scale. It is the first time V4 runs anywhere we do not control, and the first time it calls a model from inside Resolve.

### Before starting — three answers from the Resolve team

1. **[V4 ↔ Resolve] Which lane does Team Pulse run on?** Resolve has three deployments — the legacy VM, local mode, and the ACA control plane — and time limits, image selection, retention and failure reporting differ between them. Everything from here on reads differently by lane.
2. **[V4 ↔ Resolve] Can workers on that lane reach `api.anthropic.com` and `github.com` at run time?** V4's engine fetches its modules from GitHub on first use and resolves model names live against the provider.
3. **[V4 ↔ Resolve] Does that lane retain job inputs after a job completes, and for how long?** Phase 4 depends on taking each V1 job's input and running it through V4. We know API mode retains inputs; we have not confirmed the other lanes.

### One check on our side first

**[V4 ↔ Resolve]** The resolver runs V4's setup command on *every* job, including jobs that arrive with an already-configured corpus. If V4 regenerated the lens file in that case, every job would destroy the wiki's configuration. One hour to test; if it fails, fix it before any job runs.

### What is in it

- **[V4 ↔ Resolve] The branch.** On the weaver resolver repository, a branch off Marc's pinned-runtime commit (PR #7, which the hosted ACA lane already uses). The only change: the line in `runtime-requirements.txt` that names which wiki-weaver version to install points at a commit on wiki-weaver's `v4` branch. V4 stays on `v4`; nothing merges to `main` in Stage 2. **For Marc:** is PR #7 merging? We base on it either way.
- **[V4 ↔ Resolve] A small fresh corpus.** Initialized by V4 with Team Pulse's lens — the file built from their purpose during Stage 1 — and five to ten of their sources. Through the branch on the confirmed lane. We check Resolve's completion payload, fetch the returned zip through Resolve's artifact endpoint, read V4's result file from the event stream, and read three pages.
- **[V4 ↔ Resolve] One long job.** Thirty-plus sources, two to three hours, on the same lane. We watch what stops it, if anything. In the meeting we quoted a three-hour limit; reading the platform code, that specific number is probably not what kills jobs, but a stall reaper on the legacy VM is real and Marc has seen it fire. This job replaces the argument with a measurement, and the measurement sets the batch size for Phase 3.

### Exit criteria

- **[V4 ↔ Resolve]** Resolve's completion payload for the small job reports `success`, with source counts matching what was sent.
- **[V4 ↔ Resolve]** The zip fetched back through Resolve contains a wiki with the Stage 1 page shape, verified by reading three pages.
- **[V4 ↔ Resolve]** At least one V4 model call completed inside a Resolve container — proving egress and provider resolution work there.
- **[V4 ↔ Resolve]** We know the lane's real time limit: either the long job finished, or we know exactly what stopped it and after how long.

### What we need

**From the Resolve team:** the lane; the egress answer; the retention answer; PR #7's status; the exclude-list line; access for us to submit jobs on that lane.
**From the Team Pulse team:** a handful of representative sources for the small corpus, and which parameters you pass today (`limit`, `max_cycles`, `keep_going`, a purpose text or `--plain`).

---

## Phase 3 — Team Pulse's wiki exists in V4, built inside Resolve

### What this is

**[V4 ↔ Team Pulse]** Team Pulse's full history — every source in the `_sources/` folder of the zip they hold today — through V4, **inside Resolve**, into a fresh wiki initialized with their lens. It runs as a series of Resolve jobs, each sized to the time limit Phase 2 measured, in chronological source order so that dated entries and supersession markers come out right. This is the biggest thing V4 will do in Resolve. It is also the run that tells us whether V4 at Team Pulse's scale needs the one change we have deliberately held back.

### What we expect, and the trigger

**[V4]** On the current page shape, writer input quadrupled from 31 to 65 sources: the median went from 126K to 518K characters, the maximum from 411K to 805K, and one call was already over 800K; wall time per source went from 218 to 321 seconds. Team Pulse's history is more than 215 sources and has been growing every day since August. Extrapolated, the largest writer calls reach the 3.5M size guard before the rebuild finishes, and the rebuild is days of jobs, not hours. **We expect oversized-context holds on the busiest pages near the tail of the rebuild. Phase 3 measures where.**

**Any oversized-context hold in Phase 3 triggers the splice** — the change PLAN §6 describes, scope as written there: the writer receives the sections of a page it needs and returns the sections it changed, plain code does the writing, and writer input stops tracking wiki size. The splice is built on the measurement Phase 3 produced, and the held sources are then re-ingested through V4's retry path. **Stage 2 is not done until they are.** The splice stays conditional; the outcome — a complete wiki — does not.

### Before starting

**[V4] The roll-up step, if the Phase 1 test says so.** If the cheap fix left Current state leading with the latest meeting on busy pages, the structural fix goes in here, before 215-plus passes over exactly the pages people will compare: a separate model step that writes Current state *after* the batch's sections are finished. It reads the finished sections, never sees the previous roll-up, and has one job, so nothing pulls it the other way. Designed per batch, per touched page — not per source — so the rebuild adds a handful of calls to Brian's page rather than a hundred. It composes with the splice if the splice is built: plain code assembles the sections the writer returned, the roll-up step reads them, plain code inserts the result. Because it reads only the finished sections, the same step can **regenerate every roll-up over an existing wiki without re-ingesting** — which makes it a maintenance tool as well as a fix: change the lens's "covers" line, regenerate; find the rebuild's roll-ups wanting, regenerate rather than redo 215 sources.

### What is in it

- **[V4 ↔ Team Pulse]** Team Pulse gives us their current zip once. We extract `_sources/`, confirm it is complete, and sort by source date.
- **[V4 ↔ Resolve]** A fresh V4 corpus with Team Pulse's lens, then batched jobs through the V4 branch on the real lane until every source is either converged or held with a stated reason.
- **[V4]** We watch writer input per call, wall time per source, and holds for size as the wiki grows, batch by batch. Whatever happens is recorded.
- **[V4]** If size holds appear: the splice, then the retry of the held sources.
- **[V4]** We read pages and run the three questions we asked after every Stage 1 epoch. This is measurement, not a scored gate.

### Exit criteria

- **[V4 ↔ Resolve]** The rebuild completes as Resolve jobs: every job finishes, or is stopped by something we can name.
- **[V4 ↔ Team Pulse]** Every source in Team Pulse's history is converged. If any were held for size, the splice has landed and they have been re-ingested. Any remaining hold has a kind and a reason, and none is "unknown."
- **[V4 ↔ Resolve]** The resulting zip is inside Resolve's size caps (50 MiB in, 64 MiB out).
- **[V4]** The three questions answer from the wiki, and we have read enough pages to say the shape held at full scale — including Current state on the busiest pages.

### What we need

**From the Team Pulse team:** the current zip, once.
**From the Resolve team:** the model budget — the rebuild is several hundred model calls over days of jobs, on the platform's API key.

---

## Phase 4 — V4 runs alongside V1 on Team Pulse's live inputs

### What this is

**[V4 ↔ Team Pulse]** With a complete V4 wiki in hand, we take the input of **every** V1 job Team Pulse has run since the moment we snapshotted their sources for the rebuild, in order, and submit the same sources to V4 into that wiki. On our trigger; Team Pulse's scheduler sends nothing twice. Every job, in order, with no gaps — a skipped batch or a gap after the snapshot makes the two wikis drift apart, and the comparison stops being like for like. Team Pulse keeps reading V1. This proves the incremental path holds on live inputs inside Resolve, and it produces the side-by-side. What anyone does with the comparison is a later decision; it is not a gate here.

### Exit criteria

- **[V4 ↔ Resolve]** At least five shadow runs on live V1 inputs, or a week, whichever comes first — every V1 job in that window, in order — with no infrastructure failures. Any failures are V4's own holds, each with a kind.
- **[V4 ↔ Resolve]** Every shadow run's zip stayed inside Resolve's size caps.
- **[V4 ↔ Team Pulse]** We know what on Team Pulse's side reads the wiki after Resolve returns it, and have checked it against V4's shape — or the gap is named and sized.

### What we need

**From the Team Pulse team:** what reads the wiki.
**From the Resolve team:** nothing new.

---

## Stage 2 exit criteria

Stage 2 is done when all of the following are true:

1. **[V4 ↔ Resolve]** The rebuild of Team Pulse's history completed as Resolve jobs — every job finished or was stopped by something we can name.
2. **[V4 ↔ Team Pulse]** Every source in that history is converged. Sources held for size triggered the splice and have been re-ingested; any remaining hold has a kind.
3. **[V4 ↔ Resolve]** At least five shadow runs on live V1 inputs, or a week — every V1 job in the window, in order — completed with no infrastructure failures.
4. **[V4 ↔ Resolve]** The V4 zip stayed inside Resolve's size caps throughout.

That is "V4 running in Resolve, consistently." It is not "Team Pulse has switched."

---

## Known issues the comparison will show

Named here so nobody reads them as a surprise when the V4 and V1 wikis sit side by side.

**Current state on the busiest pages.** Described in Phase 1. The cheap fix is in before the rebuild; the structural fix is in before Phase 3 if the cheap fix was not enough. If either is late, Brian's, Resolve's and Team Pulse's own pages — the ones anyone compares first — will show a roll-up that reads as a running log of meetings rather than a summary. That is a known defect with a known cause and a planned fix, not a property of the design.

**Holds for size at the tail.** Described in Phase 3. If the splice has not yet landed when someone looks, the newest sources on the busiest pages will be sitting in `.wiki/failed/` with the reason `oversized-context`. That is the expected shape of the wall, and the trigger for the fix.

---

## What we are not doing yet, and why

Brian's guidance: get the end-to-end working first; fix what the run shows needs fixing. Each item here has a trigger, and most will probably fire. They are deferred, not dropped.

**The splice — until Phase 3 shows the need.** Described above. With E02's numbers we expect it to fire. We run first anyway, so the splice is built on Team Pulse's real measurements rather than our extrapolation, and so the wall is a recorded fact rather than a projection.

**The four data-safety items.** An independent code review found four weaknesses in V4 — two involving a link inside the corpus pointing outside it, one a crash in the instant between committing and recording "done" followed by a human edit, one scratch left staged in git. PLAN records them as blockers for Stage 2 when Stage 2 was understood to include switching Team Pulse. **None of them is reachable on the Resolve path:** Team Pulse's zip is built by code and contains no such links (and the resolver very likely filters them — Phase 1 verifies); nobody edits the corpus between jobs, because it is a zip; and `.git` does not travel, so nothing stays staged. They are real for V4 run by hand on a corpus someone else built. They move to the switch addendum, to be closed — by the splice if it is built, or by direct fixes if not — before V4 is offered beyond Team Pulse.

**The scored evaluation.** Phase 4 produces a like-for-like side-by-side of V1 and V4 on the same inputs. Judging which is better, formally, is the evaluation Brian deferred on 9/30. The comparison is there for whoever wants to look; it does not gate Stage 2.

**Merging to `main`.** V4 stays on the `v4` branch. Resolve's pin points at a `v4` commit. The merge happens when we decide, later.

---

## Addendum — switching Team Pulse to V4 (optional, later, not part of Stage 2)

Written down so it can be tracked; not scheduled.

**The decision** is Team Pulse's, in collaboration with us, once Stage 2 is done and they have looked at the V4 wiki.

**Before it:** the four data-safety items closed, by the splice or by direct fixes; whatever reads the wiki on Team Pulse's side confirmed against V4's shape; who owns the lens after the switch agreed.

**The switch itself is two things.** Marc changes the version pin — one line in the hosted catalog for the ACA lane, or the resolver manifest for the VM lane; Resolve's runtime check enforces it. And Team Pulse replaces the corpus zip it stores with the V4-built one from Phases 3–4, which by then has every V1 batch in it. Their next scheduled job sends the V4 zip to the V4 pin. Their code does not change.

**Reverting** is the same two steps in reverse: the pin back, the V1 zip restored. Checked, not assumed, before the switch.

**Merging V4 to `main`** belongs here too, once the VM lane's worker image is pinned so that landing on `main` cannot swap anything by accident.

---

## Alignment with PLAN.md §6 (for the WikiWeaver side)

| PLAN §6 says | This plan |
|---|---|
| Stage 2: the four verbs and the corpus zip over the same pipeline, called the way Team Pulse's current version is called; the CLI path is the bridge; running the graph directly is the destination | The same. The CLI path through the weaver resolver is every phase here. Running the graph directly, through Resolve's dot-graph resolver, remains the destination after the switch |
| Done when one job executes in Resolve and Team Pulse can call it without noticing | Phase 2 is the first clause. "Can call it" is read as capability — the interface is unchanged — which Stage 2 proves; actually switching is the addendum |
| First after Stage 1: the splice, one branch, one milestone; then the full 215 under the final design | **Changed.** The full corpus is Phase 3 and is Team Pulse's actual history. The splice is triggered by what Phase 3 shows rather than built first; when triggered, it is inside Stage 2. Its scope and done-when stay as written in §6 |
| §9: no migration — V4 re-ingests from sources and does not convert a V1 or V2 wiki | Phase 3 is exactly that |
| Owed: interface tests from both contracts | Phase 1 |
| Owed: `--limit ≤ 0` → exit 2; hold logging when a batch is all-held | Phase 1 |
| Owed: the `update` verb, open with Marc | Asked in Phase 2. The resolver never calls it; the stub likely stands |
| Owed: CI fixed before the `main` merge | Phase 1 |
| Owed: merge sequence `v4-build → v4 → main` | **Changed.** `v4-build → v4` for Stage 2; `→ main` moves to the addendum |
| The four data-safety items, recorded as Stage 2 blockers | **Re-scoped.** They were blockers for a Stage 2 that included the switch. Stage 2 no longer does; they move to the addendum, with the reason stated above |

Three recorded changes — the splice's trigger, the merge sequence, the data-safety scope — so PLAN.md and this document do not disagree silently. §6 should point here for Stage 2; the contract's clause 10 gains `.wiki/work/`; LEARNINGS should carry the Current state diagnosis.

---

## Owners

| | |
|---|---|
| **WikiWeaver (us)** | Phase 1; the resolver branch; the lens; Phase 2's jobs; the Phase 3 rebuild, its measurements, and the splice if triggered; the Phase 4 shadow runs and comparison |
| **Resolve — Marc** | PR #7; the exclude-list line; the `update` verb decision; emitting `summary` so Resolve's `result.summary` is not null for weaver jobs; the pin change if and when the switch happens |
| **Resolve — Ken** | Which lane; run-time egress; input retention by lane; the watchdog and the other items in the platform notes already sent |
| **Team Pulse — Samuel, Alex** | A few sources and the parameters for Phase 2; the current zip for Phase 3; what reads the wiki; and, later, the decision to switch |
| **Brian** | The Stage 1 report; the splice when Phase 3 triggers it; whether the deferred evaluation comes back |

## Open questions

1. **[Resolve]** Which lane does Team Pulse run on, and is the uncapped patch applied there?
2. **[Resolve]** Is PR #7 merging?
3. **[Resolve]** Can workers on that lane reach `api.anthropic.com` and `github.com` at run time?
4. **[Resolve]** Does that lane retain job inputs after completion, and for how long?
5. **[Team Pulse]** What on your side reads the wiki after Resolve returns it, and what does it expect to find?
6. **[Team Pulse]** What parameters do you pass today, how many sources per batch, and is `_sources/` in your zip complete or pruned?
