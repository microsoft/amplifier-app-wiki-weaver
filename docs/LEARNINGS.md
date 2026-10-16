# Learnings — what went wrong, how it showed, what we did

One entry per problem found by building or running V4. Each has the problem, how it
showed up (with the number or example that made it visible), the fix, and status.
Decisions and their reasons are one line each in `docs/DECISIONS.md`; this file is the
longer record of what the running system taught us. Newest at the bottom.

### Contract shapes written from memory — 2026-10-02
**Problem.** The ledger row and `result.json` shapes in the builder prompt were specified
from memory rather than from the code that consumes them.
**How it showed.** CP1's `result.json` had no `counts` object and no `converged` field;
the resolver's `_classify` requires both, and for repo pipelines ignores the exit code —
so every V4 run would have reported *failed* in Resolve while working. The ledger used
`ts`/`filename`/`sha256`/`outcome` where RepoWeaver reads `timestamp`/`source`/`hash`/
`status`/`converged`.
**Fix.** Both shapes verified against V1's `lib.py` and the resolver's parser; the builder
told to read the parser, not a paraphrase; a deterministic test for each shape.
**Status.** Applied at CP2. (The Converge contract draft independently caught the ledger
keys — the exercise's first concrete return.)

### Held or skipped sources left the corpus dirty — 2026-10-02
**Problem.** A hold or skip wrote its ledger row and moved the file without committing.
**How it showed.** The next source's write-scope check would have seen those files as
writes outside the selected pages and failed. CP1 never hit it because nothing was held.
**Fix.** Skip and hold commit their bookkeeping immediately; a test asserts the corpus is
clean afterwards. Found by the builder while applying the CP1 corrections.
**Status.** Applied at CP2.

### Engine step cap — 2026-10-02
**Problem.** dot-runner stops any run at nodes × 50 steps: 650 for the 13-node graph,
about 85 sources.
**How it showed.** The full 215-source corpus could not run in one invocation.
**Fix.** The CLI runs the graph in 40-source batches until the inbox is empty; one run
directory and one aggregate `result.json` per invocation, because the resolver reads only
the newest file. The loop is deterministic glue; no inference left the graph.
**Status.** Applied at CP2. REVISIT if the engine's bound changes.

### Per-page source cap bound on the pages that must accumulate — 2026-10-02
**Problem.** The cap of ~10 sources per page (from the prior program's attribution
finding) collided with pages accumulating across the whole corpus.
**How it showed.** 7 of 40 topic pages were at the cap after one epoch; E02 would have
scattered Team Pulse and Resolve material across spillover pages or held sources.
**Fix.** Cap removed. The citation check guards attribution mechanically instead —
6,609 citations, 0 misses, with pages citing up to 37 sources.
**Status.** Applied at CP3. What the cap was incidentally bounding — page size — is the
next two entries.

### "Each sentence names its source" — 2026-10-05
**Problem.** PLAN.md §2 said each *sentence* cites. The vision copied it; the builder
prompt said "every claim." The design intent was always principle 6: specific claims
cite, orientation and synthesis need not.
**How it showed.** Dense cite-everything prose on every page; 513 citations on one
620-line page. A synthesized paragraph is hard to write when every sentence needs a
quote — this is likely upstream of the journal-not-synthesis pattern below. Caught by
the owner reviewing the vision, traced back to the plan.
**Fix.** PLAN.md §2 reworded to principle 6; vision text replaced; writer prompt aligned.
The check is unchanged — it never required a citation per sentence, only that present
citations resolve.
**Status.** Applied — plan, vision and writer prompt (d9a8b6d).

Where V4's citations land relative to the earlier versions:

| | V1 | V2 | V4 |
|---|---|---|---|
| Form | numbered footnote `[N]`; `sources:` as integer ids | the source filename | `[s42: "quote"]` — V1's id form plus a few quoted words |
| Points at | a whole file | a whole file | the exact spot — the quote is a grep target inside a 150 KB transcript |
| Verified | no (an LLM assess node scored pages; it did not resolve citations) | no | yes, mechanically — `grep` confirms the quote is in the file; no model |
| Density | no stated rule | no stated rule; the eval found V2's wrong answers "well-cited" | explicit — specific claims only; synthesis and orientation uncited |
| Measured value | — | +1.80 for following file-level citations back to sources, the prior program's largest win | keeps that; the quote makes "follow the thread" cheap |

The one property V4 shares with V3 that V1 and V2 lacked is verification. The difference
is `grep` versus a second model grading every sentence, and no rule that a sentence must
carry a citation at all.

### Citation strings are half the page — 2026-10-05
**Problem.** Citations carried the full ~100-character source filename.
**How it showed.** `team-pulse.md`: 149 KB, 513 citations averaging 154 characters —
79 KB, 53% of the page, is citation text.
**Fix.** Cite by the ledger's stable `source_id` (`[s42: "quote"]`); transform existing
pages mechanically with the ledger as the map; `lint` must report zero citation errors
afterwards or the transform is wrong.
**Status.** Applied before E03 (d9a8b6d). 6,609 citations transformed, 0 unmapped, lint 0.
Citation share on the five largest pages fell from 51–55% to 32–37%; Team Pulse went from
149 KB to 106 KB. The quotes are now the bulk; the density rule is the remaining lever.

### Initiative pages are journals, not syntheses — 2026-10-05
**Problem.** The writer appends a dated section per source instead of maintaining a
current view. "Integrate, never replace" plus a loss guard that fails on a lost heading
make consolidation expensive, so the writer never tries (zero loss-guard failures across
65 sources).
**How it showed.** `team-pulse.md`: 41 `##` sections, almost all dated, several named for
the *meeting* rather than what happened; only 7 superseded markers in 620 lines, so it is
not history bloat — it is 41 live entries. V2's own Team Pulse page was 13 sections by
aspect with a "Current State" section.
**Fix.** Two-layer hub pages: a `## Current state` section rewritten each pass on top, the
dated record below unchanged. Policy in the lens's page-type definitions, one writer line,
one loss-guard exemption keyed on the heading. Principle 3 holds: the record accumulates;
the current view is derived from it.
**Status.** Applied before E03 (d9a8b6d). First contact, personal corpus: 25 of 30 topic
pages gained a `## Current state` section (median 6 lines) on first touch; 37% of headings
carry a date, against nearly all of them on the team corpus before. E03 showed the section becoming a second journal; E04 fixed it — see the
2026-10-06 entries below.

### Stale quotes after an edit that removes text — 2026-10-03
**Problem.** Re-ingesting a changed source touched only the pages the brief selected;
pages elsewhere could keep quotes the source no longer contains.
**How it showed.** Not yet — CP3's edit was append-only. Identified by the builder as an
open item. Personal notes are where it would bite; transcripts never change.
**Fix.** On re-ingest: tell the writer the source changed, give it a diff of old vs new,
add every citing page to its selection, mark stale quotes superseded rather than deleting.
All in tool-node glue.
**Status.** Applied (d9a8b6d) and proven on the personal run: a bullet removed from one
note, four pages quoted it, every one kept the quote inside a superseded marker with its
original citation; no page lost a line; first-write pass; lint 0.

### The citation check has never caught a misquote — 2026-10-05
**Problem.** We assumed the check was guarding against fabricated or paraphrased quotes,
and weighed its cost (about 15% of first writes failing) against that.
**How it showed.** All 12 CP3 misses, classified: every one was a verbatim quote of
fewer than five words — "worked ok", "not in yet", "how does it authenticate". Zero
whitespace, paraphrase, dropped-prefix or not-present cases. The personal run repeated it:
11 short-quote findings, 0 quotes not found. Across ~8,000 citations on two corpora the
verification half has never fired; the 5-word minimum is the only friction the citation
machinery has ever produced.
**Fix.** None yet — the 5-word rule is held through E03 so the density change is the only
variable. If short-quote failures persist, lower the minimum to three words or drop it
and accept any verbatim quote.
**Status.** Measuring. Short-quote failures are now counted separately in every report.

### Model steps had no timeout — 2026-10-05
**Problem.** A model step could wait forever on a dead connection.
**How it showed.** During a network outage the writer sat 63 minutes on a 5 KB note.
Killing it left a half-written page uncommitted, and the next run's opening snapshot
would have committed it as if it were the owner's edit.
**Fix.** Every model step has a timeout (brief 900 s, write 2400 s, index 1800 s, ask and
init 900 s); ingest begins by undoing any half-written source. In the restarted run the
outage became fast failures — five briefs held with "Connection error", index step exit 1.
**Status.** Applied (5f390bb). Timeouts fired per epoch are now reported.

### Held sources could not be retried — 2026-10-05
**Problem.** Only content that had never been tried was eligible for ingest.
**How it showed.** Five sources held during the outage; moving them back into `_inbox/`
did nothing. RepoWeaver's retry path works exactly that way — move the failed file back,
run ingest — so its retries would have silently done nothing, and every contract assert
would still have passed.
**Fix.** Anything not converged is eligible, as in V1. Re-dropped, all five converged. A
conformance assert for the retry path is added to the corpus contract.
**Status.** Applied (2f90b2c). The lesson: the contracts lock the surface; behavior
underneath it still needs the V1-parity test.

### Old citations need the old source text — 2026-10-05
**Problem.** Editing a source replaces `_sources/<name>`; pages still cite the previous
version by its id, and those citations can no longer be checked.
**How it showed.** The Ken note edit: s11 became s26; four pages cite s11.
**Fix.** Prior versions are kept at `.wiki/source-versions/s<id>.md`, under `.wiki/` but
outside the resolver's exclusions, so they travel. Old citations resolve against them.
**Status.** Applied. Added to the corpus contract's layout and round-trip set.


### Superseded markers mark the wrong text — 2026-10-06
**Problem.** The marker was a single-line comment placed before a line. It cannot
distinguish "this sentence is old" from "this sentence describes a change."
**How it showed.** 208 markers on topic pages; 30 sampled: 6 clearly right, at least 5
clearly wrong, 13 ambiguous. Every wrong one marks a transition sentence that contains the
NEW fact — "<!-- superseded: 2026-05-21 --> Overtaken on 05-21: Gurkaran reported a fresh
install working". Estimated 20–35% wrong. READING.md tells agents to skip marked text, so
agents drop current facts. Found by an audit sampling pages; every check passed.
**Fix.** A delimited block — `<!-- superseded: YYYY-MM-DD -->` … `<!-- /superseded -->` —
wrapping exactly the old words; one writer sentence (the replacement goes outside the
block, as its own entry); a deterministic check: a block may not contain the current
source's citation id — you cannot supersede what you just wrote.
**Status.** Applied in 580639a; verified by the independent pass. The E03 removal test must be re-read against this.

### Crash recovery could lose a source — 2026-10-06
**Problem.** The source left `_inbox/` (gitignored) before the ledger row and commit were
written; recovery then deleted the untracked `_sources/` copy. Recovery restored from the
git index, not HEAD, so a staged half-written page survived. The CLI proceeded even when
recovery itself failed.
**How it showed.** Reproduced in a throwaway corpus: after an interrupt between the move
and the commit, the file was in neither place.
**Fix.** Commit first; the move out of `_inbox/` is the last step. Recover from HEAD.
Recovery failure exits 1 and the run does not start.
**Status.** Applied in 580639a; verified by the independent pass.

### The loss guard had two holes — 2026-10-06
**Problem.** A deleted tracked page passed, because the check skipped files that no longer
exist. One superseded marker anywhere on a page switched off the 15% line-loss rule for
the whole page.
**How it showed.** Both reproduced. Zero loss-guard failures across 129 sources is partly
explained by the second hole — the guard was not fully armed.
**Fix.** A deleted tracked `.md` fails. The 15% rule has no marker exemption — marked lines
stay; they are not deletions. Heading-loss passes only when the marker is adjacent to the
lost heading.
**Status.** Applied in 580639a; verified by the independent pass.

### Shell injection through filenames — 2026-10-06
**Problem.** DOT tool commands interpolated parameters into shell text.
**How it showed.** A fixture whose filename contained `$(...)` executed it. Teams export
names carry spaces, parentheses and apostrophes.
**Fix.** Every parameter shell-quoted, or passed by file or environment.
**Status.** Applied in 580639a; verified by the independent pass.

### The ingest lock could be held twice — 2026-10-06
**Problem.** The lock file was created empty and the PID written a moment later. A second
run reading it in that gap found no PID, called the lock stale, deleted it, and proceeded.
`init` took no lock at all.
**How it showed.** Reproduced: two concurrent starts both acquired the lock. Exit 75 is a
contract promise; RepoWeaver sync and a scheduled ingest can start within milliseconds.
**Fix.** The PID is written atomically (temp file + rename, or O_EXCL with the content in
one write). `init` takes the lock.
**Status.** Applied in 580639a; verified by the independent pass.

### Current state worked; one residual habit — 2026-10-06
**What happened.** The roll-up instruction, with no length cap, produced a Current state on
22 of 22 hub pages (5 initiatives, 10 people, 7 topics) at E04. Source-subject bullets went
from a median of 5 (E03) to 0; 28 of 37 touched pages had none. Median 7 lines — the
writer sized it itself. Orientation lines are real: "Team Pulse is the team's status
memory and its main engagement surface … Brian directs it, Samuel Lee builds it."
**The residual.** One or two prose paragraphs per section framed by the meeting just read —
"As of the planning call of 2026-06-26 …" — the one-source-at-a-time bias in a new form.
The bullet metric missed it. Also: 20 source pages and 12 of 43 decision pages gained the
section (wrong — a source page is a summary), and the heading carried the ingest date over
June content.
**Fix.** One sentence: "Current state describes the page, not the source you are
integrating." Measure prose openers that name a source. Source pages exempt; decisions opt
out via the lens; `(as of)` is the latest source date on the page.
**Status.** B-lean.

### Citation density is the content's nature — 2026-10-06
**Problem.** We expected "specific claims cite; synthesis does not" to cut the share of
body lines carrying a citation substantially.
**How it showed.** Measured against E02's committed state, the five largest pages:

| page | E02 | E04 |
|---|---|---|
| Team Pulse | 79% | 71% |
| Resolve | 76% | 69% |
| Brian | 75% | 70% |
| Evaluation | 82% | 73% |
| Amplifier Agent | 65% | 62% |
| **all five** | **76%** | **69%** |

Seven points. Rewrite failures fell much further (12 findings → 3), but that is a
different metric. A status wiki is mostly specific claims — dates, names, commitments —
so ~69% is close to what the content is, not a defect. Related: 28% of lines with hard
numbers carry no citation; the wiki is 47% the size of its sources.
**Fix.** Stop pushing density; measure it for drift only. The byte lever is elsewhere: ids
instead of filenames (done) and dropping the redundant source-page links (Phase B).
**Status.** Decided.

### Redundant source-page links beside every citation — 2026-10-06
**Problem.** The writer emits `[source page](source-….md)` next to each `[s<id>: "quote"]`.
**How it showed.** ~100 characters per link; 627 mentions of "slice E0x" in the wiki, many
inside these links — the epoch label from the test corpus's filenames echoed into prose.
**Fix.** Cite by id only. Index source entries carry the id so the id → summary-page hop
stays one grep. Strip `__slice-E0x` from filenames for the fresh run.
**Status.** B-lean.

### Reports are not evidence — 2026-10-06
**Problem.** Checkpoints were approved from the builder's reports.
**How it showed.** Two audit sessions that read code paths and sampled pages found six
correctness bugs and a broken history mechanism. No builder report surfaced any of them;
every check passed throughout; the builder's own metrics pointed the wrong way twice
(bullets but not prose; the "mid-page" misread).
**Fix.** Every checkpoint samples the actual pages and reads the changed code — by the
orchestrator or an independent audit session — before the next phase is approved.
Correctness fixes are written test-first so each repro is proven fixed. An independent
pass confirms Phase A before Phase B starts.
**Status.** Adopted.

### Writer input grows with the wiki — 2026-10-06
**Problem.** The writer received every selected page whole, and pages grow with every source
that touches them, so the writer's input grew with the wiki rather than with the source.
**How it showed.** Median characters handed to the writer per source: 617K at E03 (max
1.01M; 5 of 33 sources over 800K), then 825K at E04 (max 1.51M; 19 of 31 over 800K). The
three largest pages grew ~55% in one epoch (161–178K → 256–273K bytes); brian.md reached 102
sections. Wall time per source began tracking input (r = 0.64; median 255 s → 307 s). At
that rate E06–E07 would hand the writer 3.6–5.6M characters — past the model's window, so a
215-source run would stall before finishing. The reader side showed the same thing: 383K–549K
characters read per ask answer, double E03's, because the pages it opened were 50% larger.
**Fix.** Sections become the unit of work. The writer receives Current state, the aspect
sections the brief named, and a heading-only outline — never the whole page — and returns
the changed sections; a deterministic step splices them back. Input is bounded by section
size, not page size. The answering agent reads the same way. PLAN.md §5 "Why sections are
the unit of work" and §9.
**Status.** The splice milestone, first after Stage 1 (PLAN §6). It measures writer characters per source and ask characters
per answer against these numbers.

### The independent pass found what the author's tests missed — 2026-10-07
**Problem.** Phase A shipped six fixes with eighteen tests, every test written before its
fix and every one passing. Three data-corruption paths were still open.
**How it showed.** A cross-provider reviewer (different model family, clean context) read
the code with file:line, ran the suite (63 pass), re-ran the audit's repros, and ran new ones
in a throwaway corpus. Eight fixes verified — no data reaches any shell string; the lock
excluded a second real process (exit 3 / exit 75). Four did not: recovery reset every
changed path and deleted untracked files (a hand-edited lens.md destroyed); init committed
an interrupted writer's half-page; an interruption during the index phase set no sentinel
and the next run committed the partial index.md and log.md; ask's index.md and READING.md
reads followed symlinks out of the root. One test kept the heading it claimed to remove.
**Fix.** Phase A.2, same discipline — failing test from each repro first — then the same
reviewer re-verifies before Phase B.
**Status.** A.2 applied in 1cb82cc; the second pass verified it and found four more — see the next two entries. No A.3: the small fixes are in B-lean; the root cause is the splice milestone's.

### The snapshot was detecting what should be prevented — 2026-10-07
**Problem.** The brief and write nodes run as model sessions with a shell and a filesystem
tool, and the writer writes pages itself. Everything downstream — scope check, hold, the
pre-writer snapshot — infers after the fact what the model changed.
**How it showed.** The second review drove the engine's shell tool outside the corpus with no
model call (success=True). A selected slug that resolved to an external symlink was read into
context and written through, invisible to git. The snapshot stored path names only, so a
writer overwrite of an already-dirty owner file passed as "pre-existing." A brief failure
routed to hold with no baseline and destroyed an uncommitted lens.md edit and an untracked
draft. V1 met the same exposure with a 4,000-word prompt (HARD PROHIBITION, two self-reported
manifests); V2 with validate, retention_check and a reviewer agent. Each added supervision;
none removed the pen.
**Fix.** The writer returns text; a deterministic step writes exactly the named sections —
the splice milestone. Interim in B-lean: hold reverts only journaled and scope-named paths;
reads in ingest are contained.
**Status.** Interim in B-lean; structural fix in the splice milestone.

### The second verification found the root cause under the first one's fixes — 2026-10-07
**Problem.** Phase A fixed six reproduced bugs; A.2 fixed the verifier's four; both verified.
**How it showed.** The second pass re-ran its own repros — all passed — then ran new ones:
hold with no baseline, the name-only snapshot, a stale or missing journal, an ingest symlink
escape. Two trace to the same cause the first round's fixes had worked around.
**Fix.** Verify every phase, not only the first. When a second round finds a shared cause,
change the design instead of patching again — which is why there is no Phase A.3.
**Status.** Adopted; the design change is the splice milestone.
**Status.** In progress.

### init's lens draft knows nothing of section sets — 2026-10-07
**Problem.** init's draft node asks only for page type names and one line each; it knows
nothing about `Sections:`, `Sections pattern:`, `Current state covers:` or
`Current state: none`.
**How it showed.** Found while staging the smoke run. A smoke run on an init-generated lens
would have reported heading counts with no rule in force — the measure that says the design
works would have meant nothing.
**Fix.** None in the draft node: section sets are deployment policy (DECISIONS 2026-10-07).
Smoke and full/ take e01's `## Page types` verbatim after init.
**Status.** Recorded. Post-Stage-1: the init interview path is the right place to ask a
human what each page type should track; the purpose path should not have a model invent it.

### Model-step timeouts bound awake time, not wall time — 2026-10-08
**Problem.** The engine's per-node timeout runs on a monotonic clock that stops while the
machine sleeps. On a sleeping laptop the 900s brief timeout never fires, and a run hangs
indefinitely while looking alive — a fail-loud guard that silently did not fire.
**How it showed.** Smoke run on smoke-b: a brief request sent 03:49:46 UTC returned
05:37:17 with no timeout; the power log shows a lid-closed sleep at 05:32. An earlier
attempt sat about 3 hours on one brief request the same way.
**Fix.** Operational for Stage 1: sleep disabled for the duration of a run. A wall-clock
deadline alongside the step timeout is a candidate for later, not now — Resolve containers
don't sleep, so this is laptop-only.
**Status.** Recorded; operational fix in force for the Stage 1 runs.

### A new page check needs a corpus migration before it is armed — 2026-10-09
**Problem.** The source-link check (a markdown link to a `source-*.md` page fails the
write) was armed on a corpus that already carried 8 such links on 4 pages.
**How it showed.** E02's first changed-source re-ingests selected `workstream-chats.md`,
whose 3 legacy links sat inline. Removing them changed 3 of its 17 lines (18%), which the
15% loss guard rejects; keeping them fails the new check. 3 of the first 4 E02 sources
were held, all on that page, about 8 minutes and two writer calls each.
**Fix.** A deterministic corpus migration removed the 8 links (no model; citations
unchanged; `lint` 0 errors), then the held sources were re-dropped. Rule: a new
deterministic check on page content needs a corpus migration before it is armed, or every
source touching legacy content is held. The citation-id change did this (transform, then
lint); the source-link check didn't.
**Status.** Applied to full/ (corpus commit e2273b0) before resuming E02.

### Current state slid back into a journal at 64 sources — 2026-10-13
**Problem.** The roll-up instruction and the one added sentence ("It says where things
stand, not how they got there; chronology belongs in the sections below") did not hold it.
**How it showed.** full/, E01 → E02: Current state median 2,572 → 4,088 characters, max
10,618 (team-pulse.md); lines per section 1 → 5. On busy pages it became a stack of "As of
<date> (<meeting>): …" paragraphs plus an "Earlier roll-up, kept for history:" block inside
the section (team-pulse, context-intelligence, resolve, chris). Likely cause: content loss
was E02's most common first-write failure (8 of the 10 first-write failures that hit
sources); the writer appears to keep old roll-ups to avoid it, not knowing Current state
lines are exempt from the 15% rule.
**Fix.** Structural, not another sentence: Current state is written by a separate step that
reads the record sections and writes the roll-up fresh — a different call with a different
instruction. Goes with the splice milestone.
**Status.** Deferred to the splice milestone; no rule change in Stage 1.

### Writer input quadrupled in one epoch — 2026-10-13
**Problem.** The writer reads whole pages, and pages grow with every source.
**How it showed.** full/, E01 → E02: writer input per call median 126K → 518K characters,
max 805K; one call over 800K. The 11 changed-source re-ingests also pull in every page that
cites them.
**Fix.** The splice milestone: the writer reads and returns sections, not whole pages.
**Status.** The urgency of the splice, in numbers. The 3.5M size guard holds nothing yet.

### The Stage 1 cross-check — 2026-10-13
**Problem.** Builder reports are not evidence; the generated wiki needed an independent read.
**How it showed.**
- Structure, counted independently against the lens sets: 28/28 accumulating pages open
  with Current state; 0 off-set headings; 9 decision and 54 source pages correctly without
  one; no duplicate headings, no broken links; 54/54 index source entries carry their id;
  no meeting-named sections.
- Evidence, verified against _sources/ by a reviewer starting cold: 8/8 sampled citations
  exact; 6/6 uncited specific claims in Current state supported; the disagreement contract
  held (resolve.md:281 keeps Gurkaran's and Brian's positions attributed, "open
  disagreement, not decided").
- Supersession: across ~100 block reads by three readers, 0 blocks wrapping the new fact —
  the information-hiding failure that ran 20–35% on the old corpus.
- Three placement defects, deferred: (a) misfiled entries — context-intelligence.md:75 has
  page-generation meta under Owners, :137 a description the writer itself labelled "not a
  new decision" under Decisions → the brief-names-sections item in the splice milestone;
  (b) entries inside a section sit in integration order, not date order —
  context-intelligence.md:72–74 and :148–151, team-pulse.md:74–77 → a deterministic sort in
  the lint pass; (c) paul.md:90 supersedes a whole dated entry when only the approach inside
  it changed → the content-based guard.
- A reviewer's blocking finding cited a line that, on inspection, was a correct
  supersession (the E01 coverage statement, superseded when the export was extended).
  Reviewer verdicts get the same verification as builder reports.
- Candidate lint rule, not armed: a block whose wrapped text reappears verbatim after the
  close is a refinement, not a supersession. 0 of 44 single-line blocks.
**Fix.** None in Stage 1; the three defects go to the next milestone as listed.
**Status.** Meets the bar; Stage 1 closes on this run.

### Current state drifts back into a log on busy pages — 2026-10-13
**Problem.** The writer is one model call with two opposite jobs on the same page. For every
other section: integrate, never delete — enforced by the loss guard, which rejects a write
that loses more than 15% of a page's lines. For Current state: replace in full — a sentence in
the prompt, with no check behind it and no exemption from the 15% rule.
**How it showed.** In E02, on pages touched by many sources, the writer kept the previous
roll-up under a heading like "Earlier roll-up, kept for history" and stacked new paragraphs led
by the latest meeting on top. Median roll-up length 2,572 → 4,088 characters; maximum 10,618.
The loss guard fired on 8 first writes in E02, so the writer watched "deleting gets rejected"
repeatedly and applied it to the one section where we wanted the opposite. It was complying,
not disobeying. A second pressure pushes the same way: the writer works one source at a time,
so each pass leads with what that meeting said. It looked fixed after E04 (meeting-named
bullets went from a median of 5 to 0) and in the smoke run and E01 (few sources per page, no
old roll-up to protect — one clean paragraph of ~2,500 characters). Three prompt sentences were
tried — "replace in full," "describes the page, not the source," "where things stand, not how
they got there" — and none held, for the same reason the source-link rule needed a check: a
prose rule loses to a rule with a check behind it. Third instance of that lesson here.
**Fix.** Remove the conflict instead of arguing with it. Cheap: strip Current state from the
writer's input and ask for a new one — it cannot keep what it never received — and exclude that
section from the loss guard's comparison so a shorter roll-up does not read as lost lines. No
new model call. Structural: a separate step writes Current state after the batch's sections are
finished — reads the sections, never sees the previous roll-up, one job; one call per touched
page per batch, not per source; composes with the splice; can regenerate every roll-up over an
existing wiki without re-ingesting.
**Status.** Recorded at Stage 1 close; fixed in Stage 2 Phase 1 (cheap, tested on E02's busiest
pages), with the structural step before Phase 3 if the test says so. See docs/STAGE2.md.

### The writer's API window is 1M; its working window was 200K — 2026-10-14
**Problem.** The `.large_context` class never matched (engine class grammar is
`[.][a-z0-9-]+`; the regex fell through to a bare-identifier "shape" selector named
`large_context`, which matches no node), so the write node ran on the worker's default model.
**How it showed.** Every writer request in Stage 1's three ingest runs (1,399 `provider:response`
events across 88 writer sessions in `full/.wiki/runs/ingest-*/batch-*/engine/write/sessions/`)
reports `model: claude-sonnet-5-5`. Anthropic's Models API (`GET /v1/models/claude-sonnet-5-5`,
2026-10-14) gives `max_input_tokens: 1000000`, `max_tokens: 128000`. But the dot-runner worker
mounts provider-anthropic with `enable_1m_context` unset (default False), so the provider
advertises `context_window: 200000` to context-simple, which compacts the writer's history
against that. The largest prompt any writer request sent was 161,935 tokens (input + cache
write); 43 of 88 writer sessions show the prompt dropping by over 30% mid-session (12/37 E01a,
3/8 E01b, 28/43 E02). So no request came near the 1M API limit, and none could: the writer
never holds more than ~160K tokens at once, while E02's largest writer input was 805K
characters (~230K tokens). Output per request peaked at 20,938 tokens (cap 128,000); the write
node sets no `max_tokens`.
**Fix.** Class renamed to `.large-context`; `tests/test_models.py` resolves every box node
through the engine's own parser. The size guard stays at 3.5M (the API window), but on a
200K working window the guard is not what bounds the writer — compaction is, silently.
**Status.** Open — reported at CP1: either the worker advertises 1M, or the guard drops to
~700K characters.

### A comment-only changed source does not rewrite the pages that cite it — 2026-10-14
**Problem.** The Current state cheap-fix test assumed a same-name, changed-hash source makes
the writer rewrite every page citing it.
**How it showed.** /tmp/cs-test (copy of full/): s23, s52, s64 re-ingested with one appended
`<!-- re-ingest test -->` line each. All three converged (7 model calls, 170 s), and page_select
offered team-pulse.md each time, but the writer touched only the three source summary pages:
"differs from s64 only by an appended test comment ... other cited pages left untouched".
team-pulse.md is byte-identical, so the strip was never exercised on a real rewrite. The
writer ran on `claude-sonnet-5-5` with the repaired `.large-context` rule.
**Fix.** The strip is in (`steps.strip_current_state`, applied in page_select); the real test
needs a change the writer must integrate into the busy page.
**Status.** Open — reported at CP1.

### The resolver's archive: symlinks skipped on pack (true); 3.12 + data_filter on unpack (false) — 2026-10-16
**Problem.** The platform investigation claimed (a) the resolver skips symbolic links when
packing the output archive, and (b) the platform pinned Python 3.12 so unpacking uses
`tarfile.data_filter`, which rejects links escaping the directory. Neither had been read.
**How it showed.** amplifier-resolver-weaver at `main` 0f87a02 and PR #7 8a02493 (draft, Ken).
`src/amplifier_resolver_weaver/corpus.py` is the same blob in both, so every line below holds
for both. The archive is a tar.gz, not a zip.
- (a) TRUE. `corpus.py:135` `with tarfile.open(dest, "w:gz") as tar:`; `:136`
  `for dirpath, dirnames, filenames in os.walk(src):`; `:156`
  `if abs_f.is_symlink() or not abs_f.is_file():` / `:157` `continue`. Directory symlinks are
  skipped only because `os.walk` defaults to `followlinks=False`; hard links are packed as
  link members (and then refused by this module's own unpack). No test covers pack-side
  symlinks.
- (b) FALSE as stated. No `filter=`, `data_filter` or `extraction_filter` anywhere in either
  tree. Unpack (`corpus.py:209-218`) is a hand-written pre-scan: `if member.issym() or
  member.islnk(): raise UnsafeArchiveMember(...)`, non-regular members refused,
  `_safe_member_path(member.name, dest)`, then a plain `tar.extractall(dest)`. That rejects
  every link, stricter than `data_filter`, but leaves setuid bits that `data_filter` would
  clear. A 3.12 pin would not give `data_filter` by default anyway (the default became
  `'data'` in 3.14). main pins nothing (`pyproject.toml:6` `requires-python = ">=3.11"`;
  `docs/WORKER_IMAGE.md:281` lists Python 3.11.2). PR #7 adds
  `manifest.json:28` `uv venv --clear --python 3.12 /opt/resolve-weaver`, but that venv hosts
  only the wiki-weaver/repo-weaver CLIs; pack and unpack run in the resolver's own
  `/opt/uv-tools/amplifier` interpreter. Unpack runs only on the input corpus
  (`resolver.py:858`); the output is only packed (`resolver.py:1073`).
- Excludes, both revisions: `corpus.py:58-62` `DEFAULT_EXCLUDES = (".wiki/runs",
  ".wiki/snapshots", ".runs")`; `:65` `.git` at any depth. `.wiki/work` is not excluded.
**Fix.** None in V4. Links escaping the corpus cannot travel either way. Marc's exclude-list
line for `.wiki/work` is still needed.
**Status.** Verified by reading both revisions (and running pack/unpack on hostile fixtures,
CPython 3.11-3.14 on macOS, not the worker image).
