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
