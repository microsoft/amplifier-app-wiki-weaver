# wiki-weaver CLI contract

Every `tool_command` in `pipeline/*.dot` shells out to a `wiki-weaver` subcommand.
**None of these exist yet.** This document is the contract the CLI must satisfy for
the pipelines to run as written. Each entry lists: invocation, inputs (args / env /
files read), outputs (files written / context keys set via `parse_json`), and the
exact stdout contract (the printf sentinel other nodes route on).

Convention used throughout, mirroring `amplifier-resolver-dot-graph/src/amplifier_resolver_dot_graph/devmachine/`:

- Invoked as `python3 -m wiki_weaver.<command>.<subcommand>` (a real console-script
  entry point may be added later, but pipelines should not depend on one being on
  `PATH` inside the execution environment).
- **All diagnostics go to stderr.** Only the routing sentinel (and, for
  `parse_json="true"` nodes, exactly one JSON object) may appear on stdout.
- **All state writes are atomic** (`tempfile.mkstemp` + `os.replace`), per
  `RD/devmachine/io_utils.py:15-43` -- never `open(path, "w")` directly on anything
  that must survive a crash mid-write.
- **The ordering law:** do the work -> make it durable -> *then* print the "done"
  sentinel. A sentinel printed before its artifact is durable is the single worst
  failure mode in this design (resume would silently skip work that never happened).
- Every subcommand that reads a `--wiki-root` treats it as the filesystem root
  containing `sources/`, `lens/`, `wiki/`, `AGENTS.md`, `ledger.jsonl`, `cost.jsonl`,
  `log.md`, and the ephemeral `.ai/` directory.

---

## Shared internal library (not a CLI subcommand)

`wiki_weaver.common` -- houses `atomic_write_text()`, `atomic_append_jsonl()` (append
with an `fsync` before the sentinel is printed), and ledger membership helpers
(`already_ledgered(source_id) -> bool`) used by every subcommand below that touches
`ledger.jsonl` or `lens/corrections/`.

---

## `ingest.dot`

### `wiki_weaver.ingest.select_source`
```
python3 -m wiki_weaver.ingest.select_source --wiki-root <path> --restrict <ids-or-empty>
```
- **Reads:** `sources/*` (all files), `ledger.jsonl` (completed ids).
- **`--restrict`:** comma-separated source ids; when non-empty, narrows the
  candidate pending set to exactly this list (used by `canon.dot`'s targeted
  reprocess and `ask.dot`'s file-back, via the shared parent-context clone a
  `folder` node performs -- see `ingest.dot`'s header).
- **Writes:** `.ai/current_source.txt` (the chosen id), `.ai/source-start` (epoch
  seconds, for `budget`) -- ONLY when a source is chosen.
- **stdout (exactly one line, after all writes are durable):**
  `has_source` | `no_source` | `ledger_corrupt`
  (`ledger_corrupt`: `ledger.jsonl` exists but fails to parse as JSONL --
  fail loud rather than silently treating a corrupt ledger as "nothing pending".)

### `wiki_weaver.ingest.drain_bound`
```
python3 -m wiki_weaver.ingest.drain_bound --count <int> --max-sources <int>
```
- **Reads:** nothing on disk -- `--count` is the current in-context counter value
  (substituted from the bare `$drain_count` form by the engine before this runs --
  NEVER `${drain_count:-0}`: the engine's substitution does not understand shell
  default-value syntax, so that form is left untouched and bash resolves its own
  default instead, silently discarding whatever the pipeline actually computed.
  When the context key is absent, the CLI itself applies the default -- see
  `wiki_weaver.lib.int_or_default`).
- **stdout (exactly one JSON line, consumed via `parse_json="true"`):**
  `{"drain_count": <count+1>, "drain_status": "continue" | "budget_exhausted"}`

### `wiki_weaver.ingest.retrieve_slice`
```
python3 -m wiki_weaver.ingest.retrieve_slice --wiki-root <path> --strategy index+link-graph+lexical
```
- **Reads:** `.ai/current_source.txt`, the wiki's index, link graph, and lexical
  search structures (whatever `wiki_weaver.index` maintains under `wiki/.index/` --
  out of scope for this contract, but must exist before this subcommand can run).
- **Writes:** `.ai/current_slice.json` (the deterministic candidate page list). This
  is the READ bound only -- see `wiki_weaver.ingest.build_catalog` below for what
  `weave` KNOWS EXISTS, a separate concept (evals/compounding/PRECOMMIT-INDEX.md).
- **`--emit-strength`:** additive/default-OFF, evals/arms/ingest-b.dot only. THE
  production pipeline (`ingest.dot`) does not pass this flag and does not route on
  its output -- see `_classify_strength` in the module docstring for what it was
  and why the production pipeline stopped using it.
- **stdout:** informational only, not routed on (unconditional downstream edge).
  Exception: with `--emit-strength`, one JSON line (`slice_strength`, `top_score`,
  `slice_k`) -- arm-b only.

### `wiki_weaver.ingest.build_catalog`
```
python3 -m wiki_weaver.ingest.build_catalog --wiki-root <path>
```
- **The index-first fix** (evals/compounding/PRECOMMIT-INDEX.md): gives `weave`
  a compact catalog of every wiki page -- filename, title, one-line summary, no
  bodies -- so it KNOWS WHAT EXISTS beyond its own read-bounded slice, and can
  decide for itself whether a source belongs in an existing page or warrants a
  new one. Purely deterministic bookkeeping; no model call.
- **Reads:** every page under `wiki/` (frontmatter `title`/`description` if
  present, else the first non-heading sentence of the body).
- **Writes:** `.ai/current_catalog.md`, stable-ordered (alphabetical by
  filename), truncated with an explicit `TRUNCATED` marker -- never silently --
  once the rendered text would exceed `build_catalog.MAX_CATALOG_BYTES` (60 KB).
- **stdout:** informational only, not routed on (unconditional downstream edge).

### `wiki_weaver.ingest.validate`
```
python3 -m wiki_weaver.ingest.validate --wiki-root <path> --out <report-path>
```
- **Reads:** the wiki tree (post-weave).
- **Writes:** `<report-path>` (e.g. `.ai/validation-report.md`) -- structural
  findings only (broken links, schema conformance, orphan pages). Never fails the
  pipeline on ordinary findings; only a genuine tool crash should exit non-zero.
- **stdout:** informational only, not routed on.

### `wiki_weaver.ingest.retention_check`
```
python3 -m wiki_weaver.ingest.retention_check --wiki-root <path> --snapshot-on-suspicion --out <report-path>
```
- **Deterministic** shrinkage / heading-loss detector -- NOT an LLM judge (measured:
  0/3 vs 2/3 on real losses; see `ingest.dot`'s header).
- **Writes:** `<report-path>`; on suspicion, a snapshot of the pre-weave page state
  for human inspection.
- **stdout (exactly one JSON line, consumed via `parse_json="true"`):**
  `{"retention_flag": "ok" | "suspicious: <short reason>"}`
  (folded into `prepare_quarantine_brief`'s brief on the quarantine-rescue path --
  `review_gate` is retired from the normal path, see that section below)

### `wiki_weaver.ingest.curate_brief`
```
python3 -m wiki_weaver.ingest.curate_brief --wiki-root <path>
```
- **THE FIX** for the source gist's fourth uncovered human job -- "curate sources"
  (the other three -- direct the analysis, ask good questions, think about what it
  all means -- each already have a gate). Every file under `sources/` was ingested
  unconditionally until now.
- Sits between `drain_bound` and `detect_kind` -- BEFORE any kind-detection/
  segmentation/retrieval work is spent on a source that might get declined.
- **Reads:** `.ai/current_source.txt`; the candidate source's raw content (reuses
  `takeaways_brief.gist_from_content`); every page under `wiki/` (reuses
  `build_catalog.build_catalog_entries`, rendered as a compact "what this wiki
  already covers" overview, not the full per-page inventory).
- **Writes:** `.ai/curate-brief.json` (`{"source_id", "stage": "curate_gate", "brief"}`
  -- same stamped shape as `review-brief.json`/`takeaways-brief.json`).
- **Default toward ACCEPT:** the brief's own text states this plainly -- a
  wrongly-declined source is permanently lost signal (this project has already
  destroyed good sources through an over-eager automatic path). Declining requires
  an affirmative, statable reason, never mere uncertainty.
- **FAIL LOUD, never fabricate:** exits 1 (no file written) if `current_source.txt`
  is missing/unreadable -- identical contract to `review_brief.py`/`takeaways_brief.py`.
- **stdout (exactly one JSON line via `parse_json="true"`):** `{"curate_brief": <text>}`

### `wiki_weaver.ingest.budget`
```
python3 -m wiki_weaver.ingest.budget --wiki-root <path> --append cost.jsonl
```
- **Env:** `PER_SOURCE_TIME_CEILING` (via `tool_env="per_source_time_ceiling"`; if
  absent, the subcommand itself applies a default of `900` seconds -- do not rely on
  shell `${:-default}` here, the env var may simply be unset).
- **Reads:** `.ai/source-start`.
- **Writes:** appends one line to `cost.jsonl`:
  `{"source_id", "kind", "bytes", "duration_s", "wiki_pages", "pages_touched", "tokens_in", "tokens_out"}`
  (append durable BEFORE the process exits either way).
- **Exit code IS the routing signal:** exit 0 (prints `within_budget`, unconditional
  edge) if `duration_s <= ceiling`; **exit 1** (FAIL, no `stdout` sentinel needed --
  the engine's fail-fast contract halts the pipeline with no explicit edge required)
  if `duration_s > ceiling`.

### `wiki_weaver.ingest.persist_guidance`
```
python3 -m wiki_weaver.ingest.persist_guidance --out .ai/review-guidance.md
```
- **Env:** `HUMAN.GATE.TEXT` (via `tool_env="human.gate.text"` -- read via
  `os.environ.get("HUMAN.GATE.TEXT")`; note dots survive in the env var name, so
  this must be read directly from `os.environ`, never via a bash `$VAR` reference).
- **Writes:** `<out>` verbatim (no shell interpretation of the content at any point).
- **stdout:** informational only.

### `wiki_weaver.ingest.commit`
```
python3 -m wiki_weaver.ingest.commit --wiki-root <path> --decision accept|skip|quarantine|declined \
    --append-ledger ledger.jsonl --append-log log.md [--ingested-count <int>]
```
- **`--decision accept`:** archives the source, appends `ledger.jsonl` (idempotent:
  no-op if this source id is already ledgered) and `log.md`, `git add -A && git commit`
  guarded by `git diff --staged --quiet` (nothing-to-commit -> no phantom commit),
  computes `ingested_count = --ingested-count + 1` and emits it via `parse_json`.
- **`--decision skip`:** the reviewer's OWN deliberate `[C] Skip`. FIRST reverts
  weave's working-tree edits (`git checkout -- wiki/` or equivalent), THEN ledgers
  the source (so it is never retried) and commits the ledger + log entry alone.
  Never merges skipped content.
- **`--decision quarantine`:** an AUTOMATIC quarantine -- a source that exhausted
  its `reweave_bound` attempts AND had already been offered its one
  post-exhaustion review at `review_gate` (see `wiki_weaver.ingest.quarantine_brief`
  below). Identical revert/ledger/commit mechanics to `skip`, but ledgered with
  `"decision": "quarantine"` -- distinct from `"decision": "skip"` so a reader of
  `ledger.jsonl` can tell a reviewer's own deliberate choice apart from an outcome
  the reviewer never got a second chance to weigh in on (previously both were
  recorded identically as `"skip"`, indistinguishable without reading commit
  contents).
- **`--decision declined`:** the answerer's OWN deliberate `[B] Decline` at
  `curate_gate` (see `wiki_weaver.ingest.curate_brief` below) -- BEFORE any
  `detect_kind`/`segment_source`/`weave` work ever ran for this source. Identical
  revert/ledger/commit mechanics to `skip` (the revert is a harmless no-op --
  nothing was ever written), but ledgered with `"decision": "declined"` -- distinct
  from `"skip"` so a reader of `ledger.jsonl` can tell a pre-write decline apart
  from a post-write skip.
- **stdout (accept only, exactly one JSON line via `parse_json="true"`):**
  `{"ingested_count": <n>}`
- All four decisions defensively `rm -f .ai/review-guidance.md` before returning
  (belt-and-suspenders against a stale guidance file leaking into the next source).

### `wiki_weaver.ingest.guidance_brief`
```
python3 -m wiki_weaver.ingest.guidance_brief --wiki-root <path>
```
- **THE FIX** for the defect where `collect_guidance` was reachable with NOTHING
  but its six-word node label -- the same vacuum `review_gate` had before
  `prepare_review_brief` (both live [B] Guide attempts in a real run hit this gate
  cold and correctly refused, crashing the pipeline with `no_matching_edge`).
- **Reads:** `.ai/review-brief.json` (the SAME weave brief `review_gate` just
  showed -- reused via `review_brief.build_brief`, verified fresh against
  `.ai/current_source.txt`), and, best-effort, the LAST line of
  `.ai/gate-decisions.jsonl` for the reviewer's own stated reason for choosing
  `[B] Guide` (never required -- a human console reviewer writes nothing there).
- **Writes:** `.ai/guidance-brief.json` (`{"source_id", "stage": "collect_guidance",
  "brief"}` -- same shape as `review-brief.json`).
- **FAIL LOUD, never fabricate:** exits 1 (no file written) if `review-brief.json`
  is missing, unreadable, or stale for the current source -- a gate this
  consequential must never invent steering context.
- **stdout (exactly one JSON line via `parse_json="true"`):** `{"guidance_brief": <text>}`

### `wiki_weaver.ingest.quarantine_brief`
```
python3 -m wiki_weaver.ingest.quarantine_brief --wiki-root <path>
```
- **THE FIX** for the defect where `reweave_bound`'s `give_up` routed straight to
  `commit_skip` -- a reviewer never saw a source that exhausted its re-weave
  attempts, even when the underlying weave was substantive (real losses in a live
  run included what an independent audit assessed as "arguably the single most
  central-to-thesis article in the whole corpus"). Ledgered identically to a
  deliberate skip, indistinguishable without reading commit contents.
- **Reads:** `.ai/current_source.txt`, `.ai/reweave-attempts.json` (attempt count,
  read-only -- never mutated), `.ai/validation-report.md` (the real, quoted
  structural-validation findings), and `.ai/quarantine-state.json` (the one-shot
  latch, keyed by source id).
- **Writes (first exhaustion only):** `.ai/review-brief.json` (reusing
  `review_brief.build_brief` plus a quarantine section: attempt count + quoted
  validation findings -- same file/shape `review_gate` already reads, so
  `review_gate` needs ZERO changes to handle this path) and
  `.ai/quarantine-state.json` (`{"source_id", "reviewed": true}`).
- **THE TERMINATION GUARANTEE:** the latch is a one-way flip, identity-gated
  exactly like `reweave_bound`'s own attempts file (a stale latch from a
  DIFFERENT source resets). `reweave_bound`'s own `--max` cap is completely
  UNTOUCHED by this module. Combined: a single source can reach `review_gate` via
  this path AT MOST ONCE, ever -- a SECOND `give_up` for the same source routes
  straight to `commit_quarantine`, never back to `review_gate`. See the module's
  own docstring for the full argument and `tests/test_quarantine_brief.py` for
  the proof (repeated exhaustions always yield `already_reviewed`, never a second
  `needs_review`).
- **stdout (exactly one JSON line via `parse_json="true"`):**
  `{"review_brief": <text>, "quarantine_status": "needs_review" | "already_reviewed"}`
  (`ingest.dot` routes on `context.quarantine_status`, not `tool.last_line`.)

---

## `init.dot`

### `wiki_weaver.init.capture_notes`
```
python3 -m wiki_weaver.init.capture_notes --out .ai/interview-notes.md
```
- **Env:** `HUMAN.GATE.TEXT`. **Appends** (not overwrites) this round's answer to
  `<out>`, since the interview may loop multiple rounds.

### `wiki_weaver.init.read_verdict`
```
python3 -m wiki_weaver.init.read_verdict --verdict-file .ai/init-verdict.txt
```
- **Reads:** `<verdict-file>`. **Fail-closed:** missing, empty, or any content other
  than exactly `enough` -> treated as `not_enough`.
- **Writes:** `rm -f <verdict-file>` after reading (a stale verdict must never leak
  into the next round).
- **stdout:** `enough` | `not_enough`

### `wiki_weaver.init.check_bound`
```
python3 -m wiki_weaver.init.check_bound --count <int> --max-rounds <int>
```
- Same in-context-counter shape as `ingest.drain_bound`.
- **stdout (one JSON line via `parse_json="true"`):**
  `{"interview_rounds": <count+1>, "bound_status": "ok" | "hit"}`
  When `hit`, also writes `.ai/bound-hit.flag` (empty file; `write_schema`'s prompt
  checks for its existence and deletes it after noting the draft caveat).

### `wiki_weaver.init.persist`
```
python3 -m wiki_weaver.init.persist --wiki-root <path>
```
- `git init` if `.git` absent (idempotent); `git add -A && git commit` guarded by
  `git diff --staged --quiet`; `rm -f .ai/interview-notes.md .ai/draft-schema.md
  .ai/draft-persona.md .ai/init-verdict.txt` (ephemeral cleanup -- AGENTS.md and
  lens/ are now the durable record).

---

## `ask.dot`

### `wiki_weaver.ask.load_index`
```
python3 -m wiki_weaver.ask.load_index --wiki-root <path> --out .ai/candidate-pages.json
```
- **Env:** `QUESTION` (via `tool_env="question"`).
- **Writes:** `<out>` -- candidate page list + a coverage estimate, index-first
  (must NOT open every page in the wiki).

### `wiki_weaver.ask.present`
```
python3 -m wiki_weaver.ask.present --answer-file .ai/answer.md
```
- **Reads:** `<answer-file>` directly (the answer text never transits a
  `$substitution` token). Formats for the caller's I/O channel.

### `wiki_weaver.ask.refuse`
```
python3 -m wiki_weaver.ask.refuse --loud --refusal-file .ai/refusal.md
```
- **Reads:** `<refusal-file>` directly; prints it loudly (what was searched, why it
  falls short) -- refusal is a first-class outcome, not an error swallowed silently.

### `wiki_weaver.ask.file_back_brief`
```
python3 -m wiki_weaver.ask.file_back_brief --wiki-root <path> --answer-file .ai/answer.md
```
- **THE FIX** for the defect where `file_back_gate` was reachable BY DEFAULT but had
  never once been exercised -- it carried NOTHING but a bare node label, the same
  vacuum `review_gate`/`takeaways_gate`/`collect_guidance` each had before they got
  a brief. An agent proxy answering cold has no grounds to judge whether THIS
  particular answer is worth filing back, and correctly refuses.
- **Reads:** `QUESTION` env var (`tool_env="question"`); `<answer-file>` directly
  (never `$substituted`).
- **Writes:** `.ai/file-back-brief.json` (`{"stage": "file_back_gate", "brief"}`).
  Unlike every ingest-side brief, this has NO source-id-style freshness stamp --
  `ask.dot` has no per-source resume loop of its own (see its own header);
  presence and a non-empty `brief` field are the whole contract.
- **FAIL LOUD, never fabricate:** exits 1 (no file written) if `<answer-file>` is
  missing -- should never happen given `ask.dot`'s own `coverage_check` contract,
  but never silently trusted.
- **stdout (exactly one JSON line via `parse_json="true"`):** `{"file_back_brief": <text>}`

### `wiki_weaver.ask.prepare_file_back`
```
python3 -m wiki_weaver.ask.prepare_file_back --wiki-root <path> --answer-file .ai/answer.md
```
- **Writes:** a new immutable file under `sources/` (e.g.
  `sources/synthetic-answer-<epoch>.txt`, `kind=article` header) containing the
  answer content -- registered as ordinary raw material, not a special case.
- **stdout (one JSON line via `parse_json="true"`):**
  `{"restrict_to_sources": "synthetic-answer-<epoch>.txt"}`
  (visible inside `file_back`'s child `ingest.dot` run via the full parent-context
  clone `handlers/pipeline.py` performs -- confirmed engine behavior, not assumed.)

---

## `lint.dot`

### `wiki_weaver.lint.structural_validate`
```
python3 -m wiki_weaver.lint.structural_validate --wiki-root <path> --out .ai/structural-report.md
```
- **Writes:** `<out>` -- broken links, schema conformance, orphan pages.
- **stdout:** `structural_ok` | `structural_bad`

### `wiki_weaver.lint.write_report`
```
python3 -m wiki_weaver.lint.write_report --wiki-root <path> --out <report-dir> \
    --structural .ai/structural-report.md --findings .ai/lint-findings.md
```
- **Reads:** `--structural` (always present), `--findings` (present only when the
  structural gate allowed `analyze` to run -- must handle its absence gracefully).
- **Writes:** a formatted report under `<report-dir>` (default `reports/`, relative
  to `--wiki-root`). Never edits the wiki itself.

---

## `correct.dot`

### `wiki_weaver.correct.capture`
```
python3 -m wiki_weaver.correct.capture --out .ai/correction-text.md
```
- **Env:** BOTH `CORRECTION_TEXT` and `HUMAN.GATE.TEXT` may be set (via
  `tool_env="correction_text,human.gate.text"`); exactly one is expected to be
  non-empty depending on which intake path fired. Writes whichever is non-empty to
  `<out>` verbatim.

### `wiki_weaver.correct.locate_pages`
```
python3 -m wiki_weaver.correct.locate_pages --wiki-root <path> --claim-file .ai/correction-text.md --out .ai/affected-pages.json
```
- **Reads:** `--claim-file` directly (never `${correction_text}` shell-interpolated
  -- this is the highest-risk injection site identified in review: multi-line free
  text into a shell command).
- **Writes:** `<out>` -- list of wiki pages that assert the corrected claim.

### `wiki_weaver.correct.trace_sources`
```
python3 -m wiki_weaver.correct.trace_sources --wiki-root <path> --pages-file .ai/affected-pages.json --out .ai/affected-sources.json
```
- **Reads:** `--pages-file`; existing provenance/citation records.
- **Writes:** `<out>` -- source ids that fed those pages.

### `wiki_weaver.correct.validate`
```
python3 -m wiki_weaver.correct.validate --wiki-root <path> --out .ai/validation-report.md
```
- Same structural-check contract as `ingest.validate`.

### `wiki_weaver.correct.persist_lens`
```
python3 -m wiki_weaver.correct.persist_lens --wiki-root <path> --claim-file .ai/correction-text.md --pages-file .ai/affected-pages.json
```
- **THE load-bearing step.** Computes a deterministic id for this correction (e.g.
  a content hash of the claim text) and checks `lens/corrections/<id>.md` for
  existence BEFORE writing -- idempotent, no duplicate correction on re-run.
- Commits wiki + lens together, guarded by `git diff --staged --quiet`
  (nothing-to-commit -> no phantom commit on a re-run that changed nothing).

---

## `canon.dot`

### `wiki_weaver.canon.capture`
```
python3 -m wiki_weaver.canon.capture --out .ai/canon-content.md
```
- **Env:** `HUMAN.GATE.TEXT`. Same whole-document-body injection concern as
  `correct.capture` -- never shell-interpolated.

### `wiki_weaver.canon.write`
```
python3 -m wiki_weaver.canon.write --wiki-root <path> --path <canon-doc-path> --content-file .ai/canon-content.md
```
- **Writes:** `lens/canon/<canon-doc-path>` from `--content-file` directly.

### `wiki_weaver.canon.impact_scan`
```
python3 -m wiki_weaver.canon.impact_scan --wiki-root <path> --canon-doc <canon-doc-path>
```
- **Reads:** the wiki's provenance records to find pages derived under the OLD
  version of this canon doc.
- **stdout (one JSON line via `parse_json="true"`):**
  `{"impacted_pages": "<comma-separated>", "restrict_to_sources": "<comma-separated>"}`
  (there is no `--set-context` CLI mechanism -- the ENTIRE stdout must be this one
  JSON object; a tool cannot set context any other way.)

### `wiki_weaver.canon.validate`
```
python3 -m wiki_weaver.canon.validate --wiki-root <path> --out .ai/validation-report.md
```
- Same structural-check contract as `ingest.validate`.

### `wiki_weaver.canon.commit`
```
python3 -m wiki_weaver.canon.commit --wiki-root <path> --canon-doc <canon-doc-path> --reprocessed <int>
```
- `git add -A && git commit` (canon doc + any reprocessing results), guarded by
  `git diff --staged --quiet`. `--reprocessed 0` on the no-reprocess path.

---

## Sentinel summary (every stdout contract in one table)

| Subcommand | stdout contract |
|---|---|
| `ingest.select_source` | `has_source` \| `no_source` \| `ledger_corrupt` |
| `ingest.drain_bound` | JSON: `drain_count`, `drain_status` (`continue`\|`budget_exhausted`) |
| `ingest.retention_check` | JSON: `retention_flag` |
| `ingest.budget` | `within_budget` (exit 0) or non-zero exit (FAIL, no sentinel needed) |
| `ingest.commit --decision accept` | JSON: `ingested_count` |
| `ingest.commit --decision skip\|quarantine\|declined` | no required stdout sentinel |
| `ingest.build_catalog` | informational, not routed on |
| `ingest.curate_brief` | JSON: `curate_brief` |
| `ask.load_index` \| `.present` \| `.refuse` | informational, not routed on |
| `ask.file_back_brief` | JSON: `file_back_brief` |
| `ask.prepare_file_back` | JSON: `restrict_to_sources` |
| ask.dot `coverage_check` (`test -f .ai/answer.md`) | `covered` \| `thin` |
| `init.read_verdict` | `enough` \| `not_enough` |
| `init.check_bound` | JSON: `interview_rounds`, `bound_status` (`ok`\|`hit`) |
| `lint.structural_validate` | `structural_ok` \| `structural_bad` |
| `canon.impact_scan` | JSON: `impacted_pages`, `restrict_to_sources` |
| all other tool nodes (`validate`, `locate_pages`, `trace_sources`, `write_*`,
  `persist_*`, `capture_*`) | informational only -- routed unconditionally |

**Rule enforced throughout:** a subcommand that emits a `parse_json="true"` JSON
object is never ALSO relied upon for `tool.last_line` routing on the same node --
the JSON is necessarily the last line, so mixing the two silently breaks last-line
routing (`json.loads` would also break if anything but the JSON is on stdout).
Every node above picks exactly one of the two mechanisms.

---

## Corrections — 2026-07-25 (post-council)

### COLLAPSED 2026-07-25 — 28 → 23 subcommands

Both councils flagged that this document **admits its own duplication** and then ships it
anyway. Collapsed, zero capability lost:

| Removed | Folded into | This document's own admission |
|---|---|---|
| `correct.validate`, `canon.validate` | **`wiki_weaver.validate`** | "the same structural-check contract" |
| `init.check_bound` | **`wiki_weaver.bound`** | "the same in-context-counter shape" |
| `ingest.drain_bound` | **`wiki_weaver.bound`** | same |
| (`ingest.validate` renamed) | **`wiki_weaver.validate`** | — |

One `validate`, one `bound`, parameterized by `--scope`. Callers updated in all `.dot` files.

### ADDED — `wiki_weaver.review` (was missing entirely)

`DESIGN.md §7` describes `wiki-weaver review` as the user's **only** recourse against a
proxy acting on their behalf — and it was absent from all 28 subcommands. The safety valve
was designed and never specified. Now specified:

```
wiki_weaver.review --wiki-root <path> [--since <iso8601|last-review>] [--json]
  reads   ledger.jsonl, log.md, .ai/gate-decisions.jsonl
  writes  nothing (read-only)
  prints  every gate decision made without a human present since <since>,
          with source id, the choice taken, and the wiki pages it touched
  exit    0 always (a report, not a gate)
```

### ADDED — `wiki_weaver.ingest.reweave_bound`

Required by the cycle-diff result (see `DESIGN.md §6`): a deterministic validate FAIL now
routes into a **bounded** re-weave. This is the bound.

```
wiki_weaver.ingest.reweave_bound --wiki-root <path> --max <n>
  reads   .ai/current_source.txt, .ai/reweave-attempts.json
  writes  .ai/reweave-attempts.json (increment for THIS source)
  prints  "retry"    -- attempts < max, re-weave the same source
          "give_up"  -- attempts >= max, quarantine and advance the drain
  exit    0
```

`give_up` routes to `wiki_weaver.ingest.quarantine_brief` (see its entry above under
`ingest.dot`), which offers the source to `review_gate` exactly ONCE before
automatically quarantining it via `commit_quarantine` on any subsequent exhaustion --
so one bad source can never wedge the whole drain, AND is never silently thrown away
without at least one chance for a human/proxy to weigh in.

### FAIL-LOUD requirement on every freeform capture

`init.capture_notes`, `canon.capture`, `correct.capture` each read the text of a
`mode="freeform"` hexagon gate. Attractor's `AutoApproveInterviewer.ask()` returns the
literal string `"auto-approved"` for freeform questions. Since this text flows into
`lens/canon/` and `lens/corrections/` — the **highest-precedence layers in §4** — each of
these tools MUST:

```
if captured_text.strip() == "auto-approved":
    exit non-zero   # hard pipeline failure
```

A gate that cannot be answered must stop the run, never invent an answer.

---

## ADDED 2026-08-01 — the AITL proxy (`wiki_weaver.aitl.*`)

`DESIGN.md §7` names the proxy as a design intent that was never built. Built now, as
`src/wiki_weaver/aitl/`. This is a NEW namespace, not a `wiki-weaver` subcommand mirroring
a `.dot` node's `tool_command` -- it operates one level up, at interviewer selection, so it
applies to ANY of the 6 pipelines above without touching a single one of their graphs.

### `lens/persona.md` — the character document

Location confirmed by `DESIGN.md §2`'s own layer list (`persona.md -- how the proxy
answers on our behalf`) and already referenced by `init.dot`'s `write_schema` prompt.
Format: the five-slot template from `DESIGN.md §7`'s guardrails, as `##`-or-deeper
markdown headings (any order, any depth), each with real body text:

```
## Identity            -- who the proxy stands in for; this wiki's purpose
## Capability floor     -- what it may decide vs. what it must refuse
## Hard constraints     -- things it must always/never do
## Refusal script       -- when and how it declines rather than guesses
## What "done" means    -- how it knows an answer is grounded enough to act on
```

Structural completeness (all five headings present, file non-empty) is checked
DETERMINISTICALLY by `wiki_weaver.aitl.persona.load_persona()` — no model call — before the
proxy will ever attempt to answer a gate. Missing/empty/incomplete → refused immediately.

### `wiki_weaver.aitl.backend.LLMProxyBackend`

The reasoning step. Deliberately NOT a pipeline `box` node — every `box` node spawns a full
coding agent with bash/filesystem/search tools (no per-node way to strip them back off), and
`DESIGN.md §7`'s guardrail requires the proxy hold no such tools as "a capability
restriction, not a request." Instead: one tool-free `unified_llm.generate()` chat completion
per gate — mechanically incapable of touching bash/filesystem/web because no tool-calling
loop is ever constructed. Requires a strict JSON verdict (`action: "answer"|"give_up"`,
`reason`, `grounding`, `answer_text`|`choice_key`); any parse failure, invalid choice, empty/
stub answer, or missing `grounding` citation is treated as `give_up`, never a guess.

### `wiki_weaver.aitl.proxy_interviewer.ProxyInterviewer`

Implements attractor's `Interviewer` protocol (drop-in alongside `AutoApproveInterviewer` /
`ConsoleInterviewer` at the engine's own `interviewer=` seam). Returns
`Answer(value=AnswerValue.SKIPPED)` — the SAME value `HumanGateHandler` already converts to
a FAIL `Outcome` for every other interviewer — whenever: the persona is missing/incomplete,
the backend gives up, the backend raises, or the backend's answer fails validation. Every
outcome (answered AND refused) is recorded via `wiki_weaver.aitl.audit` before returning.

### `wiki_weaver.aitl.audit` — `.ai/gate-decisions.jsonl`

One JSONL line per gate interaction: `{timestamp, stage, gate_type, question, choices,
decision, reason, grounding, answer_text, choice_key, choice_label, persona_digest}`.
`decision` is one of `answered | give_up | refused | error`. This is the exact path
`wiki_weaver.review` (below) already commits to reading — following the existing contract
rather than inventing a new one. Never cleaned up by any `persist` step (unlike other `.ai/`
breadcrumbs) — durable audit trail, not per-invocation scratch.

### `wiki_weaver.aitl.run` — the selectable launcher

```
python3 -m wiki_weaver.aitl.run <dot_file> \
    --param wiki_root=<path> [--param k=v ...] \
    --cwd <dir> \
    --gate-mode {fail,auto-approve,console,proxy}   # default: fail
    [--model <model>] [--provider anthropic] [--logs-root <dir>]
```

Wires the selected `Interviewer` into `amplifier_module_pipeline_runner.runner.run_pipeline`
via its public `interviewer=` parameter — the SAME seam the `attractor` CLI itself uses. **No
change of any kind to the attractor engine or the `attractor` CLI's own `--on-human-gate`
flag** — this is a purely additive, opt-in entry point at the wiki-weaver layer. Default
`--gate-mode` is `fail`, identical to the attractor CLI's own default, so this launcher can
never silently change behavior for a caller that doesn't explicitly ask for
`proxy`/`auto-approve`/`console`. No existing eval invokes `attractor run` through this
launcher, so nothing existing is affected either way.

### Deliberately NOT built (named so it doesn't sneak back in as "obviously needed")

- **`wiki_weaver.review`** itself (the human-readable report over `.ai/gate-decisions.jsonl`
  + `ledger.jsonl` + `log.md`) — the audit trail format above is what it will read whenever
  it is built; the JSONL file is independently `cat`/`jq`-inspectable in the meantime.
- **A training pipeline.** `DESIGN.md §7` explicitly rejects one: corrections persist as text
  in `lens/corrections/` and are re-read on every weave, not learned into weights.
- **Persona bootstrap-by-interview, persona import, and interactive-run recording** (3 of the
  4 named improvement approaches) — `init.dot`'s existing interview loop already covers
  bootstrap-by-interview (now with the five sections named explicitly in its prompt); import
  and interactive-recording are real but separate features, each deserving their own
  evidence before being built.

### Known pre-existing gap this work surfaced (NOT fixed here, out of scope)

`canon.capture` and `correct.capture` — named throughout this document and referenced by
`canon.dot`/`correct.dot` — have **no `wiki_weaver.canon`/`wiki_weaver.correct` Python package
in `src/` at all** as of this writing. Only `init.capture_notes` and `ingest.persist_guidance`
exist and carry the FAIL-LOUD stub-rejection guard above. This means `canon.dot` and
`correct.dot` cannot run end-to-end today **regardless of which interviewer answers their
gates** — a pre-existing CLI-implementation gap, unrelated to interviewer selection. Verify
before assuming otherwise: `find src -iname "*canon*" -o -iname "*correct*"` returns nothing
under `wiki_weaver/`.
