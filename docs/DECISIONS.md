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

## 2026-10-06 — Stage 1, CP4 (E03 into e01)

- E03 ran with no code change after 427d3d4: 33/33 converged, 0 held, 0 model-step
  timeouts, 1 first write failed (3 short quotes, fixed on rewrite). The 5-word minimum
  stays; next decision after E04 numbers.
- REVISIT: writer input is now a median 617 K and a max 1.0 M characters per source (the
  whole of each selected page is handed over). Wall time stayed flat (median 255s vs 243s
  in E02), so this is a cost and context-headroom question before it is a time one.
- OPEN: `## Current state` is not behaving as a rewritten summary — see LEARNINGS
  2026-10-06. No change made; the owner decides between a writer-prompt fix and a check.

## 2026-10-06 — Stage 1, CP4 (E04 into e01)

- `## Current state` is the page's roll-up, replaced in full every pass, first `##`
  section, heading exactly `## Current state (as of YYYY-MM-DD)`. Writer prompt states the
  shape; each lens says what it covers per page type; the checks node enforces the
  heading format and position on any page that has the section (fail → rewrite once). No
  length rule; no backfill (the pages without one are a post-E07 step).
- The 5-word quote minimum stays; the REVISIT is closed (3 findings in 33 sources at E03,
  6 in 31 at E04, all fixed on rewrite).
- Writer input unchanged. REVISIT: at E04 the median is 825 K characters, 19 of 31
  sources exceed 800 K, the max is 1.51 M, and wall time now tracks it (r = 0.64).
- OPEN: `(as of <date>)` is filled with the ingest date (today), not the latest source
  date the section reflects; the sections say "as of 2026-06-26" in their text instead.
- OPEN: the heading-prefix match catches a source summary headed
  `## Current state, as Manoj described it (2026-05-15)`; `lint` reports it. Harmless
  until that summary is rewritten.

## 2026-10-06 — Stage 1, Phase A (correctness; e01 stopped after E04)

- Superseded text is a block, `<!-- superseded: DATE -->` … `<!-- /superseded -->`,
  wrapping only what is no longer true; the replacement sits outside it. A check fails a
  block that cites the source being ingested. A lone legacy marker covers the rest of
  its line.
- Loss guard: no marker exemption for the 15% rule (superseded text stays, so it is not a
  deletion); a removed heading passes only if its text survives on or next to a marker
  line; deleting a tracked selected page fails.
- Source bookkeeping copies first, records and commits, and removes the inbox copy last.
  Recovery restores every uncommitted path from HEAD (not the index), keyed on
  `.wiki/work/current.json` or `.wiki/work/inflight`; it drops an inbox copy only when an
  identical copy is committed in `_sources/`. A failed recovery stops the run (exit 1).
- Graph parameters reach steps as environment variables (`tool_env`), never as shell
  text.
- The run lock is an `flock` on `.wiki/ingest.lock`, held by the open descriptor; the PID
  in the file is for messages only. Chosen over write-PID-atomically because PID-based
  stale reclaim still races (two reclaimers can each delete the other's fresh lock);
  the kernel releases a dead holder's lock. `init` takes the same lock.
- A failed index step routes to `index_restore`: index.md and log.md back to HEAD, page
  commits kept, run exits 1.
- `--source` on a held file commits the move back to `_inbox/` before the write, so the
  scope check sees only the writer's changes.
- Every `[s<id>]`, quoted or bare, must resolve through the ledger.
- current.json, ledger rows and batch JSONL are written via temp file + `os.replace`.

## 2026-10-07 — Stage 1, Phase A re-verification

- Every pipeline operation that changes tracked state first writes a journal
  (`.wiki/work/journal.json`: op, source, destination, owned paths). Recovery returns only
  owned paths to HEAD and reports every other uncommitted change untouched. Owned: the
  source's retained copy, `.wiki/failed/<name>`, the source-version copy, the ledger, the
  summary page and the selected pages; `index.md` and `log.md` for the index phase.
- Pipeline commits stage and commit only owned paths (`git commit --only`), never `-A`.
- Hold reverts owned paths plus changes that appeared while the writer ran (the scope
  check names them); changes that existed before the write are the owner's and stay.
  The scope check ignores those pre-existing changes too.
- `init` runs recovery under the run lock before scaffolding; a failed recovery exits 1.
- After a committed hold, skip or ingest, recovery drops the inbox copy only when it is
  byte-identical to the copy committed at the journal's destination. A held file
  re-dropped into `_inbox/` with no journal is left alone (RepoWeaver's retry path).
- ask reads READING.md, index.md and every page through one contained-read: resolved,
  a regular file, inside the corpus root.
- `ruff` is in the `dev` dependency group (`uv run ruff check .`).
