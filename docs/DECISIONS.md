# Decisions

One line per call, dated. REVISIT: a constraint-driven choice and what reopens it.
OPEN: owned by someone else, or not yet settled. Caller-facing promises live in
contracts/; direction and risks in PLAN.md §9. If this file passes ~60 entries, prune —
that's a signal.

## 2026-10-02 — Stage 1, CP1

- Corpus layout, verbs and lib helpers mirror V1 so RepoWeaver and the resolver swap in
  unchanged. Names only; the V4 design is unchanged. (PLAN §5, R5)
- Ledger rows use V1's keys; `converged` decides done-ness. V4-only keys ride alongside:
  pages_touched, model_calls, archived_to, failed_checks, wall_seconds. Reason: RepoWeaver
  reads the ledger. (corpus-package clause 5)
- result.json carries counts.{total, converged, failed, blocked, errored, skipped} and
  mirrors total/converged at top level. Reason: the resolver's `_classify` reads them from
  counts. (corpus-package clause 6)
- Exit codes follow V1's table (0/1/2/3/5/75; 4 reserved, never fires) because RepoWeaver
  branches on them. (cli-surface clause 10)
- Exit 3 = no eligible source at start: an empty inbox, or only content already converged
  under some name (V1's rule), decided once before batching. OPEN: Marc — intended for
  RepoWeaver sync, which tests zero vs non-zero? (cli-surface Reserved)
- Exit 75 with a lock file (.wiki/ingest.lock, O_EXCL + PID, stale locks reclaimed,
  released on every exit path). A correctness requirement, not just compatibility: two
  concurrent ingests would corrupt the corpus git.
- `ingest --source` for a file not in _inbox/ or .wiki/failed/ exits 1 with an errored
  result.json. A failed preflight (no lens, dot-runner, git or API key) exits 1 and writes
  an errored result.json; a missing wiki directory exits 1 with none (V1).
- Index-step failure keeps the committed pages, records an errored entry, exits 1.
  Overruled "exit 0, log only" — a run that failed a step does not report success.
- Ingest runs the graph in batches of 40 sources per engine run; one run directory and one
  result.json per invocation. Reason: the engine stops a run at 50 × node-count steps.
  REVISIT: if the engine's step bound changes.
- Skip and hold commit their bookkeeping at once. Reason: otherwise the next write's
  scope check fails on files the writer never touched. (Found by running CP2.)
- `--max-cycles` parses with no effect — it was V1's per-source convergence budget and
  RepoWeaver passes ~4; V4 rewrites at most once. `--limit` caps sources per run.
- Model roles via model_stylesheet: strong = opus (brief, index, init draft, ask pick and
  answer); large-context = sonnet (write). The engine has no role attribute.
  REVISIT: when ask is evaluated.
- `init` with neither --purpose nor --plain runs the interview if stdin is a TTY, else
  falls back to --plain. The resolver runs init in a container.
- Two checks beyond the spec: the writer may change only its selected pages; every cited
  source must appear in that page's `sources:`.
- New pages are flagged as new in the writer's input; no stub files are created.
- Diff privacy rule applies to added lines; deleting already-public V1 code is not a leak.
- .github/ workflows left as-is; they run only on main. OPEN: fix before any merge to main.

## 2026-10-03 — Stage 1, CP2–CP3

- Per-page source cap removed. It bound after one epoch (7 of 40 topic pages at 10
  sources) on exactly the pages that must accumulate. The citation check guards attribution
  mechanically instead: 6,609 citations, 0 misses, with pages citing up to 37 sources.
  (PLAN §5 cheap win 4, retracted)
- What the cap was bounding: the five largest pages roughly tripled in one epoch
  (team-pulse 224 → 694 sentences); per-source wall time rose ~24% E01 → E02 and climbs
  within an epoch, because the writer reads whole pages. REVISIT: page size and wall time
  decide splitting or consolidation after CP4 — candidates are moving superseded text to
  a sibling history file, splitting by period, or giving the writer sections not pages.
- Citation check kept. CP3 cost: 5 of 34 first writes failed, all 12 findings citations,
  zero from any other check. Its value accrues to an external agent with grep (a quote
  lands in a 150 KB transcript), not to ask.dot. OPEN: classify the 12 mismatches —
  whitespace, paraphrase, or fabrication — before deciding to soften.
- 0-byte sources move to .wiki/skipped/<name> so _inbox/ always drains; ledger row
  unchanged.
- An edited source gets a new source_id, because ids follow the content hash as in V1.
  Kept: an edited file is a new version; identity by filename breaks on rename.
- After an edit that removes text, pages the writer did not touch can keep quotes from
  the old text while _sources/ holds only the new version. CP3's edit was append-only, so
  it did not arise. Interim: `lint` catches it. REVISIT: on re-ingest of a changed source,
  auto-select every page that cites it.
- Re-ingest of a changed source (option C): the writer is told the source changed, gets a
  unified diff of old vs new in its context, and every page citing the source is added to
  its selection. Stale quotes are marked superseded, not deleted. All in tool-node glue —
  no new nodes, edges, model calls or checks. REVISIT: superseded-vs-delete when the
  source itself was corrected (a fixed typo shouldn't be preserved as history); decide
  with consolidation. The loss guard stays unaware of re-ingests on purpose.

## 2026-10-05 — Stage 1, CP4 step 1 (personal corpus)

- Prior versions of edited sources kept at `.wiki/source-versions/s<id>.md` so old
  citations still resolve. Travels with the corpus. (corpus-package layout, clause 10)
- Superseded-marker date: a position that changes in a source carries the source's date; a
  source file that is itself edited carries the ingest date — the closest honest proxy.
- `## Current state` is exempt from the heading-loss rule, matched on the prefix; its
  `(as of <date>)` suffix changes every pass.
- Citation 5-word minimum held through E03 so the density change is the only variable;
  short-quote failures counted separately. REVISIT after E03: lower to three, or drop.
- Model-step timeouts: brief 900 s · write 2400 s · index 1800 s · ask/init 900 s.
  Ingest undoes any half-written source before starting.
- Retry eligibility: anything not converged is eligible, as in V1 — RepoWeaver's retry
  path depends on it.
- The three ask questions run on the team corpus after E03, not on personal.


## 2026-10-06 — Stage 1, after E04 (two audits)

- Stop e01 after E04; no E05–E07 on it. Stage 1 completes on a fresh corpus built under
  the final design; e01 is kept as the before-artifact. Reason: a wiki built under three
  rule sets is not a clean rehearsal; a fresh start removes the consolidation pass and the
  backfill.
- Sections become the writer's and the reader's unit of work: Current state + the named
  aspect sections + a heading outline, never the whole page. Reason: E04 writer input
  median 825K chars, max 1.51M, +55%/epoch — overflows the window by E06–E07. R11's own
  lever: less read per pass.
- Aspect sections per page type, named in the lens (Team Pulse initiatives: Owners ·
  Commitments · Blockers · Decisions · Open questions; a heading pattern for chronological
  types); the writer adds dated entries inside, newest first, and never adds a `##`; a
  deterministic check holds the heading set; source pages exempt. Reason: 637 of 1,252
  topic-page headings (51%) were meeting-named. Not in PLAN.md as written — §5 updated.
- The brief names, per selected page, the aspect sections the source adds to. Reason:
  principle 2, and the extraction step needs to know what to pull.
- Superseded marker becomes a delimited block; a block may not cite the current source.
  Reason: 20–35% of markers wrapped the new text.
- Current state: universal by default; the lens opts decisions out; source pages exempt.
  `(as of)` = the latest source date on the page. Reason: 20 source pages and 12 of 43
  decision pages had one; headings said 2026-10-06 over June content.
- Citation density held at ~69% of body lines; measured for drift, not acted on. Reason:
  76% → 69% after the specific-claims rule; the content is mostly specific claims.
- Source-page links dropped from citations; index source entries carry the `s<id>`.
- Principle-8 exception, recorded: mechanics identical for every lens live in the writer
  prompt — how the roll-up is treated, how supersession is marked, returning changed
  sections. What matters and what pages look like live in the lens. Test: would the
  sentence differ per corpus?
- §9 person-page attribution: covered by the lens line ("only as the sources state them")
  and the citation check; no separate rule.
- Page growth is bounded by readability (Current state + aspects) and cost (sections);
  splitting is deferred until sections themselves grow. Reason: superseded share is 2–5% —
  these are live pages, not history bloat.
- §6 done-when "evaluation baseline recorded" → "measurements recorded," per the 9/30
  decision to dial evals down; resolves the §6/§7 contradiction.
- Grep comparison for R10: after Stage 1 closes, not in the critical path.
- Phase order: A correctness, test-first → independent verification → B shape and section
  design → fresh run (E01, stop and inspect, then E02–E07). Same builder for A; the audit
  session verifies.

## 2026-10-07 — Stage 1, Phase A and its verification

- The ingest lock is a kernel flock on `.wiki/ingest.lock`, held by the CLI process for the
  whole run; `init` takes the same lock. The builder's call over the prescribed atomic PID
  write, accepted: an atomic PID still races on stale reclaim; the kernel releases on holder
  death. Contention exits 75.
- Tool nodes never interpolate data into shell text: every one runs `"$PY" -m
  wiki_weaver.steps <step>` with parameters as environment variables, and the purity test
  enforces that form.
- Recovery rollback is scoped to the in-flight operation's journaled paths — the source, its
  selected pages, its bookkeeping. Unrelated uncommitted changes are reported, never touched.
  `init` runs recovery before it commits anything; the index phase is journaled. (Phase A.2)
- Every corpus-derived read in `ask` goes through one contained-read that rejects paths
  resolving outside the corpus root. (Phase A.2)
- Independent verification is a cross-provider code reviewer with clean context, run after
  every correctness phase; its verdict gates the next phase. Phase A → NOT SAFE → Phase A.2.

## 2026-10-07 — Stage 1, after the second review

- Second review of A.2 (same reviewer, same repros): the four original findings are fixed;
  four new ones, two sharing a root cause — model nodes can write files, so "whose change is
  this" was forensic. Verdict NOT SAFE. No Phase A.3.
- The small fixes fold into B-lean: hold with no writer baseline reverts nothing (hold reverts
  the journal's owned paths plus what the scope check named — known lists, no snapshot); the
  journal gets a committed marker, a missing journal with scratch present is an error, entries
  are validated; contained reads in ingest; a size guard that holds oversized writer input
  with an explicit reason before the model call.
- The splice — sections as the unit, brief names sections, `ask` reads sections, tools off
  model nodes — is deferred to the first post-Stage-1 milestone, on a branch (PLAN §6).
  Reason: the fresh run is "run it end to end"; the splice is "find the biggest friction,
  whack it" — that ordering is Brian's. An untested mechanism comes off the critical path.
  Brian has not seen the mechanism; §5's paragraph postdates his review.
- Stage 1 done-when rescoped, owner's call: two epochs on a fresh corpus under the final page
  design. The full 215 under that design moves to the splice milestone, which is what makes
  215 fit. e01's 129 unattended sources stand as the scale evidence under the predecessor
  shape.
- Section sets per page type approved. e01: initiatives — Owners · Commitments · Blockers ·
  Decisions · Open questions; people — Current work · Commitments · Blockers · Positions;
  topics — What it is · Decisions · Open questions · Related; weekly — `## Week of
  YYYY-MM-DD`; decisions — no set, opt out of Current state; source pages exempt from both.
  personal: projects — Status · Decisions · Learnings · Next; people — Working on · Open
  threads; topics — What I know · Open questions.
- The builder's hold-during-write rule (changes that appear during the write are the writer's;
  pre-existing changes are the owner's) accepted for B-lean; moot under glue-writes.
- Phase ledger within Stage 1: CP1 → CP2–CP3 → CP4 → A → A.2 → B-lean → fresh run (E01, stop
  and look; E02 incremental; fresh personal with the interview; one edit re-read, append and
  removal) → close.
## 2026-10-07 — Stage 1, B-lean (builder's calls)

- Hold reverts the journal's owned paths plus the paths the scope check named; the
  pre/post snapshot is gone. Consequence: an owner edit that is dirty while a writer runs
  is named and reverted on hold (the run's opening snapshot makes that a mid-run window).
- Finished operations clear .wiki/work/, so scratch with no journal reliably means a run
  died; that state exits 1 and touches nothing.
- Writer-input limit: env WIKI_WEAVER_MAX_WRITER_CHARS, default 3,500,000.
- The page-type parser matches lens names to frontmatter types by a normalized key
  (plural/singular, first word): initiatives/initiative, people/person,
  weekly commitments and blockers/weekly-commitments.
- Added `##` headings only are judged against the set; existing off-set headings are not
  re-judged (no backfill). `lint` does not run the set rule (it has no before-state).
  A type that opts out of Current state fails a newly added `## Current state`.
- Writer prompt: `(as of <today>)` became `(as of <date>)` with the per-page date given in
  pages.md; "Open with one or two lines on what this is" removed (it produced the
  "Initiative page. Source: ..." openers); citations "in no other form (no links to
  source pages)". OPEN: owner confirms these readings of item 3.
- Personal lens: decisions keep Current state (the opt-out was specified for e01 only).
  OPEN: owner confirms.
- full/ staging: E01 only. OPEN: 11 E02 chat slices share a name with an E01 slice once
  `__slice-E0x` is stripped, and their contents are disjoint windows (3–23% shared
  lines); both cannot sit in _inbox/ under one name.

## 2026-10-07 — Stage 1, B-lean.2 (third review: deferred findings)

- Stage 1 precondition: the corpus is one we construct, contains no symlinks, and is not
  edited while a run is in progress. READING.md and `ingest --help` say so.
- Every writer call is measured, rewrites included (`write_guard`); the largest call per
  source is `writer_input_chars`, the run's largest is `writer_chars_max` in result.json.
- Personal decisions opt out of Current state, as in e01. Both lenses' Current state
  covers "what this is and who owns it" first.
- E02 for full/ is staged in `full/_staged-E02/` (11 cumulative chat files - the E01
  window byte-identical, then the E02 window without its header - plus the 23 other E02
  files), to move into _inbox/ after the E01 inspection.

Deferred - fix only if it fires during the run (reviewer's file:line against 30c1b15):
- Headings inside fenced code blocks, and closing-ATX `## X ##`, are judged as newly added
  headings (checks.py:193-198). Out of scope: the writer emits neither in practice.
- A declared-but-empty `Sections:` value disables the rule silently (lens.py:33-34). Out
  of scope: our two lenses declare non-empty sets; the ingest-start printout shows it.
- Normalized page-type keys can collide (lens.py:55, 78). Out of scope: no collision in
  either lens; the printout would show one.
- A malformed lens bullet is slow to parse. Out of scope: the lenses are ours and small.

Stage 2 blockers - removed by the splice milestone, must be fixed before Resolve:
- The commit-to-marker crash window (steps.py:788-789): a crash after the commit and
  before `committed` is written leaves an uncommitted-looking journal for a landed commit.
- Summary-page containment: `_summary_page` reads frontmatter before validation
  (steps.py:272-276), and page_select includes the summary unconditionally (:528).
- index.md and log.md are read without containment (steps.py:829-833).
- Already-staged scratch can enter a snapshot commit (cli.py:152-157).
Why deferred: each needs a crash, a symlink or a human edit during a run, none of which
the Stage 1 precondition allows; the corpus runs in Resolve only after Stage 2.

## 2026-10-07 — Stage 1, before the fresh run

- Section sets are deployment policy, authored in the lens, not generated by `init` from
  purpose text. The default lens carries Current state only. A corpus created by `init`
  alone gets the roll-up and a journal-shaped record below it — intended, not a defect.
  The lens travels with the wiki (corpus contract clause 7), so a wrapper supplies its own
  sets. For smoke and full/: `init --purpose "<e01 text>"`, then e01's `## Page types`
  section replaces the generated one verbatim, Owner set to Gurkaran, committed in the
  corpus git.
- Staged E02 lives beside full/ (`ww-corpora/full_staged-E02/`), not inside it: the corpus
  contract covers _inbox/, _sources/, .wiki/ and root pages only.

## 2026-10-13 — Stage 2 scope

- Stage 2 is "V4 running in Resolve, consistently": a complete V4 wiki of Team Pulse's history
  built inside Resolve, and every V1 job since the snapshot also run through V4. It is not
  "Team Pulse has switched." The switch is an addendum — Team Pulse's decision, later.
  Execution plan: docs/STAGE2.md.
- Four phases, named by outcome: V4 is ready to run where we don't control the environment;
  V4 runs a job inside Resolve; Team Pulse's wiki exists in V4, built inside Resolve; V4 runs
  alongside V1 on Team Pulse's live inputs.
- Brian's method governs: end to end first; fix what the run shows; every deferred item has a
  trigger.
- The splice is triggered, not built first: any oversized-context hold in the Phase 3 rebuild
  starts it, inside Stage 2, and Stage 2 is not done until the held sources are re-ingested. We
  expect it to fire — on the current page shape, writer input quadrupled from 31 to 65 sources
  (LEARNINGS, "Writer input quadrupled in one epoch").
- The four data-safety items recorded as Stage 2 blockers move to the switch addendum: none is
  reachable on the Resolve path (a code-built zip has no symlinks; nobody edits between jobs;
  .git does not travel). Closed by the splice if built, or by direct fixes, before V4 is offered
  beyond Team Pulse. Phase 1 verifies the two symlink claims rather than assuming them.
- The shadow runs every V1 job since the rebuild snapshot, in order, on our trigger — a skipped
  batch or a gap makes the comparison meaningless. Five runs or a week.
- The scored evaluation stays deferred (9/30). The shadow's side-by-side is observation, not a
  gate.
- V4 stays on the v4 branch; Resolve's runtime pin points at a v4 commit; merging to main moves
  to the addendum, after the VM lane's worker image is pinned.
- Current state drift (LEARNINGS, 2026-10-13) is recorded in Stage 1 and fixed in Stage 2
  Phase 1: the cheap fix first, tested on E02's busiest pages; the separate roll-up step before
  Phase 3 if the test says so.
- `.wiki/work/` joins the corpus contract's round-trip exclusions (clause 10). The resolver's
  exclude list follows the contract. V4's fallback clears scratch only when init has just
  created .git from nothing.

## 2026-10-14 — Ken's answers, and one proposal

- ACA is the only lane; the VM is retired, barring a staging environment Ken is fixing. Every
  lane-conditional item in STAGE2 collapses to ACA. On ACA nothing kills a hung job by default,
  so V4's per-node timeouts (brief 900 s, write 2400 s, index 1800 s) are the only guard.
- ACA workers reach api.anthropic.com and github.com; the goal and dotgraph resolvers depend on
  it. V4's engine fetches modules from GitHub on first use and resolves model names live.
- PR #7 is not a foundation. amplifier-agent became a true library; Ken is re-assessing whether
  the isolated runtime is still how the core-2.0.1 problem gets fixed. Nothing in Stage 2 bases
  on PR #7. Our dot-runner check (V4's graphs run on dot-runner 80d5511 + core 2.0.1) is
  evidence that V4 may not need the quarantine V1 did; told Ken.
- The resolver should always return the artifact regardless of outcome — Ken's design direction,
  being built into goal now. A dead mid-write job can therefore ship a half-written page, and
  with .wiki/work/ excluded from the zip the next job cannot tell. V4 writes pages atomically:
  Phase 1, brief item 7b. Ken: Team Pulse's jobs have not died so far; their failures are
  non-convergence.
- Retention on ACA: pending Ken's answer. Access to submit ACA jobs and the model budget: asked
  twice, unanswered.
- PROPOSED by Ken, RECOMMENDED by us, PENDING Gurkaran: V4 as a second CLI in main —
  wiki_weaver_v4 / wiki-weaver-v4 beside V1 untouched; the resolver picks the binary. Replaces
  the weaver-v4 catalog entry. About a day: 59 Python references, 18 in the graphs, the
  contracts, pyproject and the build hook; V4's only dependency is pyyaml, so V1's install gains
  nothing. Would be the first Phase 2 item, done once, after Phase 1's fixes. Reverses "V4 stays
  on v4" (2026-10-13) — the reason for that, the VM's image floating on main, no longer exists.
- Devis is a Team Pulse teammate; on the thread with Samuel and Alex.

## 2026-10-15 — Compaction, the guard, and the splice's shape

- CP1 found the writer's session compacts at ~105K estimated tokens — 0.8 of a ~131K budget
  (200K advertised window minus half the 128K output cap minus a margin). Protected: system
  prompt, first and last human messages, last five tool results. Stubbed oldest-first: the
  writer's own tool results and turns. 43 of 88 Stage 1 writer sessions compacted mid-write,
  28 in E02. Not recorded in V4's output; the event lands in a hosted-session log.
- Not a blocker. E02's writes passed every check; the pages sit in the protected opening
  message. V1 runs in production with worse exposure (pages fetched by tool, cut first) and
  has never measured it; V2 byte-budgeted to an assumed 150–200K window and records
  tokens_in: 0. Neither detects compaction.
- The guard is NOT lowered to the compaction trigger. That would hold about half of E02's
  sources — the wiki stops growing on exactly the pages people compare. Wrong trade for
  end-to-end-first. Recorded because the number moved three times in two days (3.5M, 600K,
  "from the trigger") as each read revealed another layer; it is now set from measurement
  under whichever worker ships, to what would fail rather than what would compact.
- First experiment, CP2: worker=llm-direct on the write node. No context module, no
  compaction, full memory to the 1M API limit, hard failure beyond. Kept if it runs with the
  writer's tools. Trade: ignores user_instructions; higher cost per large write.
- Detection, CP2: V4 records when a write compacted, via the context:compaction record in the
  hosted-session events.jsonl or the input_tokens drop in the node's events.jsonl.
- The splice gains a clause: the writer has no tools. Glue assembles sections, source, lens
  rules and index entries; the writer returns text; glue writes. One request, one response —
  nothing to compact, nothing to reopen. Written into PLAN §6 and STAGE2 Phase 3.
- The 1M flag is provider config the default worker discards and a .dot graph cannot set;
  asked Ken. Persisting context:compaction into node output is a dot-runner change; asked Ken.
- Plausible, unproven: compaction erasing the writer's memory that it already replaced Current
  state is a mechanism for the stacking seen in E02. The llm-direct run under the Current
  state variant tests both at once.

## 2026-10-19 — Phase 1 closed

- Closed at cc7e06b: v4-stage2 merged into v4 at 4fb85a5, then the credential-skip fix on top.
  Seventeen commits; 177 tests with engine and key, 174 + 3 skipped without the key, 169 + 8
  skipped without the engine; 38 of 50 model calls. Eight exit criteria met in full; atomic
  writes met for glue-written files; two external items open — Marc's exclude line and Samuel's
  packaging code — both Phase 2 gates, not blockers to this close.
- Budget raised 40 → 50 on 2026-10-16, earmarked for the Current state real test and one
  compaction probe. 38 used in total.
- Staging for model-step page writes: with the splice, not now. Design recorded in LEARNINGS.
  Ken: jobs have not been dying; Phase 2 and early Phase 3 are our jobs on our corpus; change
  where the writer writes once, not twice.
- worker=llm-direct on the write node: tried, reverted. Text-only — tool calls come back as
  prose, nothing is written. Cannot run a tool-using writer; is the target worker for the
  tool-less splice.
- Two corrections to the 2026-10-15 entry: the pages are NOT in the writer's protected first
  message — the prompt names .wiki/work/pages.md and the writer fetches it by tool, so the pages
  are cut first; and the Stage 1 compaction count is 44 of 88 writer sessions. A second cut:
  the context layer admits 128 KiB per tool result at ingress, head kept; 61 of 89 Stage 1
  pages.md reads exceeded it and the writer never saw index.md. Both recorded per source as
  compactions and ingress_truncations.
- The ".wiki/work blocks every Resolve job" premise was wrong (LEARNINGS, 2026-10-16): without
  .git, recovery is skipped and select wipes scratch per source anyway. The init fallback is
  harmless. The real dead-job risk is a half-written page.
- Current state cheap fix proven on a real rewrite: team-pulse.md roll-up 10,657 → 1,747 chars,
  stacking gone, opens with state, change appears once with citation; the writer did not reopen
  the live page. The structural roll-up step is NOT needed before Phase 3.
- Archive claims verified at main and PR #7: pack skips symlinks; unpack refuses every link
  member (stricter than data_filter, which is not used); archive is tar.gz; .wiki/work is not in
  the exclude list.
- CI runs V4's suite but cannot install the engine: dot-runner at the pinned rev depends on
  amplifier-agent@main, which no longer builds. Engine-driven tests skip in CI. Whether the ACA
  worker's pins install cleanly is an open question to Ken and the first Phase 2 risk.
- Second CLI in main: still pending Gurkaran.
