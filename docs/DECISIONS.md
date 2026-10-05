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
    