# wiki-weaver v2 — Attractor Pipeline Rewrite Guide

**Audience:** the builder rewriting `pipeline/*.dot` from design sketches into runnable attractor pipelines.
**Status of the current files:** all 7 fail. 2 of 7 do not even parse. The other 5 fail validation with 8–11 ERRORs each. Nothing in `pipeline/` can run today.

Every claim below is cited to source in this workspace and was verified by executing the real engine against your files.

Path shorthand:
- `AB/` = `amplifier-bundle-attractor/`
- `LP/` = `AB/modules/loop-pipeline/amplifier_module_loop_pipeline/`
- `PR/` = `AB/modules/pipeline-runner/amplifier_module_pipeline_runner/`
- `RD/` = `amplifier-resolver-dot-graph/src/amplifier_resolver_dot_graph/`

---

## 0a. What "attractor pipeline" means here — and what it does not

These `.dot` files **run on** the attractor engine. They are not attractors in the
sense the team uses the word.

The distinction, as drawn on 2026-07-30:

> An attractor defines what the **desired world state** is — outcomes, gates,
> evidence requirements — and leaves enough room for agents to use their
> capabilities to get there. It does **not** prescribe the steps.

Measured against `ingest.dot`:

| Attractor property | What this repo actually does |
|---|---|
| Define outcomes, let the agent find the path | 32 nodes in a fixed sequence |
| Room for agent capability | 4 LLM `box` nodes; 28 deterministic |
| Convergence criteria | Hard-coded routing labels on edges |

The `weave` prompt alone is ~4,000 characters of "read this file, then check
that, then do X before Y." That is prescription, and it is deliberate — see §1.

**Where this repo *is* attractor-shaped:** `validate` states a desired end state
(no broken links, no orphans, no duplicate sections, no person-sensitivity
violations) and routes back to `reweave_bound` until the wiki converges on it.
That node says *what must be true*, not *how to get there*, and the retry loop
is a real convergence loop.

That is one node out of thirty-two. Calling the whole thing an attractor
overstates it.

**Why the prescription is deliberate.** Four measured attempts in this repo to
achieve an outcome by *asking* the LLM rather than *checking* it:

| Attempt | Outcome |
|---|---|
| A human stated a rule twice in the lens | *"It was still there at the end of the run"* |
| Prompt clarification for duplicate sections | Recorded as *"explicitly not the load-bearing fix"* |
| Prompt: "always name a page to fold into" | 26 sources committed with `pages_touched: 0` |
| LLM judge for content loss | Scored 0/3; a 50-line deterministic detector scored 2/3 |

A prompt is not a control. Every defect fixed in this pipeline was fixed by
adding a deterministic check to `validate`'s issue list, never by wording a
prompt more carefully. The node ratio — 4 judgment, 28 mechanical — is that
lesson expressed as structure.

Read the rest of this guide with that framing: it is a guide to building
**deterministic pipelines that call an LLM at a few well-chosen points**, hosted
on the attractor engine's runner.

---

## 0. Verification results (run this yourself first)

```
=== overview.dot === PARSE FAIL: invalid literal for int(): 'unbounded'
=== ingest.dot   === PARSE FAIL: invalid literal for int(): 'unbounded'
=== init.dot     === nodes=15  ERRORS=9
=== ask.dot      === nodes=18  ERRORS=11
=== lint.dot     === nodes=11  ERRORS=8
=== correct.dot  === nodes=15  ERRORS=9
=== canon.dot    === nodes=14  ERRORS=8
```

Reproduce:

```bash
cd amplifier-bundle-attractor
python3 -c "
import sys; sys.path.insert(0,'modules/loop-pipeline')
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import validate
src=open('../wiki-weaver-v2-design/pipeline/init.dot').read()
g=parse_dot(src)
for d in validate(g):
    if d.severity=='ERROR': print(d.rule, '|', d.message)
"
```

Make this your inner loop. Validation is free; an LLM node is not. `validate_or_raise` runs before any model call (`PR/runner.py:216-218`).

---

## 1. What makes a `.dot` a real pipeline vs a drawing

### 1.1 The hard checklist

Enforced by `LP/validation.py:65-102`. ERROR = refuses to run.

| Rule | Requirement | Source |
|---|---|---|
| `start_node` | **Exactly one** node with `shape=Mdiamond` / `type="start"` / `id="start"` | `validation.py:122-149` |
| `terminal_node` | **Exactly one** exit: `shape=Msquare` / `type="exit"` / `id="exit"`/`"end"` | `validation.py:152-182` |
| `edge_target_exists` | Every edge endpoint is a declared node | `validation.py:185-208` |
| `start_no_incoming` | Nothing points at start | `validation.py:211-226` |
| `exit_no_outgoing` | Nothing leaves exit | `validation.py:229-244` |
| `reachability` | **Every node BFS-reachable from start** | `validation.py:247-287` |
| `condition_syntax` | Conditions parse | `validation.py:91` |
| `fidelity_valid` | Valid fidelity mode | `validation.py:94` |

### 1.2 What must be ABSENT

**Legend clusters, shape-vocabulary subgraphs, and annotation nodes are fatal.** The parser flattens `subgraph` into the same node namespace (`dot_parser.py:196-197, 245-264`) — a cluster is *not* a separate scope. So `v_start` is a second start node, `v_exit` a second exit, and every `l_*`/`ci_*`/`v_*` an unreachable-node ERROR.

This single mistake accounts for **~90% of your current error count.**

> **Rule: an attractor `.dot` is an execution graph, not a diagram.** Move legends to a comment block, a separate `docs/legend.dot` never fed to the engine, or delete them. If you want a rendered legend, keep a *separate* file for graphviz and never run it.

Also absent:
- `style=invis` edges (they are real edges to the engine).
- `shape=note` annotations (become `codergen` LLM nodes — see §1.3).
- `shape=plaintext` nodes (same).
- `compound=true`, `labelloc`, `fontsize` — harmless but noise.

### 1.3 Unknown shapes silently become LLM nodes

`SHAPE_TO_HANDLER` (`validation.py:24-35`) maps 10 shapes. **Anything unmapped defaults to `codergen`** (`validation.py:167`, `_check_prompt_on_llm_nodes`). Your `overview.dot` has a typo edge `canon_pipeline -> targeted_reprocess` where the declared node is `targeted_reprocess_note`. Verified:

```
IMPLICIT NODE targeted_reprocess: EXISTS
  shape='box'  type=''  prompt=''
  -> handler: codergen
```

An undeclared node referenced by an edge is auto-created as a **box/LLM node with an empty prompt** — it will spawn a real agent with no instruction. This is the most expensive class of typo in the system, and it is only a WARNING, not an ERROR.

### 1.4 Graph attributes that crash the parser

`max_pipeline_duration="unbounded"` → `ValueError: invalid literal for int() with base 10: 'unbounded'`.

Accepted forms (`dot_parser.py:684-702`, `_try_parse_duration`): `"30m"`, `"5m"`, `"90s"`, `"2h"`, `"500ms"`, or bare integer ms. There is **no "unbounded" sentinel — omit the attribute entirely** to mean unbounded.

### 1.5 How a pipeline gets its inputs

The real invocation (`PR/cli.py:32-50`, `params.py:14-70`):

```bash
attractor run pipeline/ingest.dot \
  --param wiki_root=/srv/wikis/acme \
  --param per_source_time_ceiling=900 \
  --cwd /srv/wikis/acme \
  --on-human-gate console
```

**The substitution rule — this is where your files are systematically wrong.**

`--param k=v` is seeded as a **flat context key named exactly `k`** (`runner.py:133-134`: `context.set(key, str(value))`). Substitution is a plain dict lookup on that key (`substitution.py:63-108`). There is no `context.` namespace prefix added.

So `${context.wiki_root}` looks up a key *literally named* `context.wiki_root`, which does not exist. Verified empirically:

```
AS AUTHORED  : wiki-weaver ingest select-source --wiki-root ${context.wiki_root}
CORRECT FORM : wiki-weaver ingest select-source --wiki-root /tmp/w
```

**Missing keys pass through as literal text — no error, no warning** (`substitution.py:92-94`). Under `set -eu` bash the literal `${context.wiki_root}` then dies as an unbound variable, or worse, silently becomes a garbage argument.

| Form | Behavior |
|---|---|
| `${wiki_root}` | ✅ correct — matches `--param wiki_root=...` |
| `${context.wiki_root}` | ❌ literal passthrough (unless a param is literally named `context.wiki_root`) |
| `${context.target_dir}` | ✅ the **one** real dotted key; reserved, auto-set to `--cwd` (`runner.py:67,136`) |
| `${tool.output}` / `${tool.last_line}` | ✅ engine-set by tool handler (`handlers/tool.py:179,220`) |
| `${optional:-default}` | ✅ shell default — **the required defense for optional params** (`substitution.py:29-36`) |

> **Naming asymmetry to internalize:** `condition=` uses `context.foo` (with prefix, `conditions.py:84-94` strips it and tries both), while `${...}` uses bare `foo`. They are *different resolvers*. This is the single most confusing part of the engine and the source of most of your defects.

Substitution applies **only** in `tool_command`, `prompt`, `description`, `tool_env`, and `dot_file`. Notably **not** in a folder node's `context.k="v"` attributes — verified:

```
folder attrs: {'context.purpose': '${context.purpose}'}
-> injected VERBATIM as child key `purpose` with the literal value "${context.purpose}"
```

(`handlers/pipeline.py:175-179`.) Your `init_pipeline` node in `overview.dot` does exactly this and would pass the literal string.

### 1.6 box (LLM) vs parallelogram (tool) — where the line actually goes

Your instinct here is already correct and is the strongest part of the design. The shipped `dev_machine.dot` is the reference implementation of the same discipline, and it goes further than you did:

> **An LLM node produces judgment as a file. A tool node makes every state transition and emits the routing sentinel.**

`RD/pipelines/dev_machine.dot:52-100` splits what was one node:
- `SelectWork` (box) — *judgment only*: writes `.ai/decision.json` = `{"pick": "<id>"}`. Prompt says: "You must NOT edit STATE.yaml."
- `ApplyState` (parallelogram) — *all bookkeeping*: reads that JSON, mutates `STATE.yaml`, emits `selected` / `halt` / `noselect`.

The rationale is recorded in `RD/devmachine/apply_state.py:1-30`:

> "Distilled from the selectwork hill-climb experiment (**baseline 14/25 → 25/25**). The reliability failures were in the BOOKKEEPING (close-previous, halt detection, and hand-serializing STATE.yaml — which the LLM corrupted into invalid YAML), NOT in the judgment of which feature to pick."

**Prescription:**

| Do this in a `box` | Do this in a `parallelogram` |
|---|---|
| Weave prose, synthesize, judge, draft | Any file the pipeline routes on |
| Write **one** artifact at a known path | Every git commit, ledger append, archive/move |
| Read many, write one | Counters, budgets, bounds |
| Never emit a routing sentinel | **Always** emit the routing sentinel |

Corollary for you: `weave` stays a box. But `commit`, `budget`, `retention_check`, `validate`, `select_source` are correctly tools — and you must additionally ensure **no box node ever writes the ledger or makes the commit.**

### 1.7 Tool nodes: inline shell or a real CLI?

`tool_command` is executed via `asyncio.create_subprocess_shell` (`handlers/tool.py:129-135`) with `cwd = context.target_dir` (i.e. `--cwd`) (`tool.py:125`).

Both are idiomatic; `dev_machine.dot` uses both and the split is by complexity:

**Inline shell — for one-liners** (`dev_machine.dot:230`):
```
tool_command="bash -c 'mkdir -p .ai; n=$(cat .ai/fix-attempts 2>/dev/null || echo 0); n=$((n+1)); echo \"$n\" > .ai/fix-attempts; if [ \"$n\" -lt 3 ]; then echo retry; else echo exhausted; fi'"
```

**Python module — for anything with real logic** (`dev_machine.dot:501`):
```
tool_command="bash -c 'PYTHON=$( [ -x /opt/uv-tools/amplifier/bin/python ] && echo /opt/uv-tools/amplifier/bin/python || echo python3 ); $PYTHON -m amplifier_resolver_dot_graph.devmachine.resume_gate'"
```

**Yes — you must write the `wiki-weaver` CLI.** It does not exist. Every one of your ~20 `tool_command`s invokes it. Recommendation: a Python package `wiki_weaver/` invoked as `python3 -m wiki_weaver.ingest.select_source`, mirroring `RD/devmachine/`. Avoid depending on a console-script entry point being on `PATH` inside the pipeline's environment.

**Note on `$` collision:** inline shell using `$(...)` or `$n` is *first* passed through attractor substitution. `$n` is not a known context key so it survives literally (`substitution.py:100-103`) — but this is a latent trap. Prefer `${var:-default}` for context values and keep complex shell in a Python module.

### 1.8 How an LLM node returns a routing decision AND data

You said v1 had six failures with verdicts traveling as unstructured text through a non-string-aware brace matcher. Here is the robust hierarchy.

The backend resolves an LLM outcome in strict priority (`LP/backend.py:747-779`):

1. **`report_outcome` tool call** — authoritative, used unconditionally, extracted from `result.steps` (immutable, race-free). Explicitly beats trailing JSON-shaped text. The comment at `backend.py:749-758` documents this as a deliberate fix: a model that calls `report_outcome` *and* also emits JSON prose must not have its real verdict discarded — "that would be fail-UNSAFE."
2. `result.text` parsed as JSON / fenced JSON / embedded JSON / prose (`backend.py:774`).
3. No text → SUCCESS.

**Tier 1 — the idiomatic form for a box node that must route:**

```
Weave [
  shape=box,
  prompt="... When done, call report_outcome exactly once with:
    status: 'success' | 'fail'
    preferred_label: 'woven' | 'nothing_to_do'
    context_updates: {\"pages_written\": \"<int>\"}
  Do not print the verdict as prose."
];
Weave -> Validate [condition="preferred_label=woven"];
Weave -> Skip     [condition="preferred_label=nothing_to_do"];
```

Schema: `status` (required, one of `success|fail|partial_success|retry`), `preferred_label`, `suggested_next_ids`, `context_updates`, `notes`, `failure_reason` (`AB/modules/tool-report-outcome/.../__init__.py:50-84`).

Route on `preferred_label=` (`conditions.py:81-82`) rather than `outcome=`. Note `outcome=` returns `preferred_label or status` (`conditions.py:75-79`) — overloading one key with two meanings. **Keep them separate: use `outcome=fail` for failure, `preferred_label=` for choice.**

**Tier 2 — the maximally robust form, and what I recommend for wiki-weaver.** This is what `dev_machine.dot` actually does for every consequential verdict, *despite* `report_outcome` being available:

```
Review [ shape=box, prompt="... As your FINAL action write exactly two files:
   1. `.ai/review-verdict.txt` containing exactly one word: pass or fail
   2. `.ai/review-notes.md` containing your notes ..." ];

ReadReviewVerdict [
  shape=parallelogram,
  tool_command="bash -c 'v=$(cat .ai/review-verdict.txt 2>/dev/null); rm -f .ai/review-verdict.txt; case \"$v\" in pass) echo pass;; *) echo fail;; esac'"
];

Review -> ReadReviewVerdict;
ReadReviewVerdict -> Commit  [condition="context.tool.last_line=pass"];
ReadReviewVerdict -> FixGate [condition="context.tool.last_line=fail"];
```

(`dev_machine.dot:154-168, 897-899`.)

Why this is strictly more robust, and why it answers your six-failure history:
- The verdict never transits the LLM's *text* channel — no brace matching, no JSON parsing of prose.
- `case ... in pass) ... ;; *) echo fail` **fails closed**: a missing, empty, or garbled file routes to `fail`.
- `rm -f` consumes the verdict, so a stale file from a prior loop iteration cannot be re-read — this is the idempotency fix for verdicts in loops.
- The routing value is a single bare token on the last stdout line, matched exactly.

Use Tier 2 for `review_gate` outcomes, retention verdicts, and anything gating a commit. Use Tier 1 for low-stakes data passing.

**`tool.last_line` mechanics:** last non-empty stdout line, stripped, always set (empty string if no stdout) (`tool.py:212-221`). **Send all diagnostics to stderr** or they pollute the sentinel — `resume_gate.py:34-35` defines `diag()` to print to stderr for exactly this reason.

---

## 2. Resume and idempotency

### 2.1 The engine's contract

- The engine **always starts from the start node**: `current_node = self._find_start_node()` (`engine.py:241`).
- `checkpoint.json` is **explicitly not a resume marker**. `LP/checkpoint.py:9`: *"is an observability record, not a resume marker. Graph-level idempotency..."*

So resume is a **property you author into the graph and the filesystem**. This is the right frame — design with it.

### 2.2 The canonical resume-gate pattern

Reference: `RD/devmachine/resume_gate.py` + `RD/pipelines/dev_machine.dot:498-502, 817-820`.

**The tool** (`resume_gate.py`, full logic at lines 48-95):

```python
def main() -> None:
    state_path = Path("STATE.yaml")
    if not state_path.exists():
        diag("no STATE.yaml -> fresh"); print("fresh"); return
    try:
        state = yaml.safe_load(state_path.read_text())
    except yaml.YAMLError as e:
        diag(f"unparseable ({e!r}) -> corrupt"); print("corrupt"); return
    if not isinstance(state, dict) or not isinstance(state.get("features"), dict):
        diag("malformed -> corrupt"); print("corrupt"); return
    features = state["features"]
    # cross-check against .ai/feature-manifest.yaml; divergence -> corrupt
    ...
    if has_progress(features):
        diag("committed progress found -> resume"); print("resume"); return
    diag("pristine -> fresh"); print("fresh")
```

**The graph** (`dev_machine.dot:817-820`) — note the three-way route, including a **fail-loud** arm:

```
Start -> ResumeGate;
ResumeGate -> SelectWork [condition="context.tool.last_line=resume",  label="resume\n(committed progress)"];
ResumeGate -> Intake     [condition="context.tool.last_line=fresh",   label="fresh run"];
ResumeGate -> ExitFail   [condition="context.tool.last_line=corrupt", label="STATE.yaml corrupt\n(fail loud)"];
```

Four properties to copy exactly:
1. **First node after start.** Nothing precedes it.
2. **Three outcomes, not two.** `corrupt` routes to a failing exit. Ambiguous state must never be guessed at.
3. **"Progress" is defined against durable, committed evidence** — `status != ready` **or** `commit_sha` is set (`resume_gate.py:38-45`), not a "we started" flag.
4. **Cross-validation.** State is checked against the manifest; divergence is `corrupt` (`resume_gate.py:70-87`).

### 2.3 Idempotency rules by node type

| Node type | Idempotent means | How |
|---|---|---|
| **Tool (read/route)** | Pure. No writes. Same state → same sentinel. | `resume_gate.py` — reads only |
| **Tool (state write)** | Re-running yields identical state. | Read-modify-write with **`os.replace`** |
| **Tool (ledger append)** | Re-append of an existing id is a **no-op**. | Check membership before appending |
| **Tool (irreversible: archive/commit)** | Guarded by a durable marker written **after** the act. | `git diff --staged --quiet` before commit |
| **Box (LLM)** | Rerunning **overwrites** its one artifact; never appends. | One artifact, fixed path, write-not-append |
| **Gate (hexagon)** | Re-asks. Answer is not durable. | Persist the decision in the next tool node |

**Atomic writes are mandatory.** `RD/devmachine/io_utils.py:15-43`:

```python
def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path_str = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding) as fh:
            fh.write(content)
        os.replace(tmp_path_str, path)   # atomic on POSIX
    except Exception:
        try: os.unlink(tmp_path_str)
        except OSError: pass
        raise
```

Never `open(path,"w")` on state. A crash mid-write leaves truncated YAML, which your resume gate must then classify as `corrupt` — turning a resumable run into a dead one.

**The ordering law:**

> **Do the work → make it durable → *then* record it done.**

A state file written before the work completes is the worst failure mode in this design: resume skips work that never happened, and the gap is silent and permanent.

**Do not trust that only you wrote the state.** `apply_state.py:54-60` documents LLM nodes writing `"completed"` out-of-band, and defensively accepts `{done, completed, complete, finished}`, normalizing back to `done`. Write your reader to be liberal and your writer to be canonical.

### 2.4 The resumable drain loop — your `ingest.dot`

Your 16-hour run that died at source 10 is the motivating case. The shape:

> **The queue is not in context. The queue is `pending = on-disk sources − ledger`, recomputed from scratch on every iteration.**

Because `select_source` derives pending state from durable evidence each pass, restarting from `start` is *automatically* correct. There is no resume gate needed for the drain itself — the selector **is** the resume gate.

I verified this end-to-end with a 5-source simulation, crashing after 2:

```
--- RUN 1: drain 2 then crash ---
{"source_id": "s1.txt", ...}
{"source_id": "s2.txt", ...}
--- RUN 2: restart from 'start' ---
[select] done=2 pending=3   -> s3.txt
[select] done=3 pending=2   -> s4.txt
[select] done=4 pending=1   -> s5.txt
[select] done=5 pending=0   -> no_source (route to exit)
final: 5 entries, no duplicates
```

`select_source` (emits sentinel, writes the current-source breadcrumb):

```python
led, done = Path("ledger.jsonl"), set()
if led.exists():
    for line in led.read_text().splitlines():
        if line.strip(): done.add(json.loads(line)["source_id"])
pending = sorted(p.name for p in Path("sources").glob("*.txt") if p.name not in done)
print(f"[select] done={len(done)} pending={len(pending)}", file=sys.stderr)   # stderr!
if not pending:
    print("no_source"); sys.exit(0)
Path(".ai").mkdir(exist_ok=True)
atomic_write_text(Path(".ai/current_source.txt"), pending[0])
print("has_source")
```

`commit` (idempotent append + fsync):

```python
sid = Path(".ai/current_source.txt").read_text().strip()
if sid in already_ledgered():
    print("[commit] already ledgered - skip", file=sys.stderr)
    print("committed"); sys.exit(0)
with open("ledger.jsonl", "a") as f:
    f.write(json.dumps({"source_id": sid, "pages": n}) + "\n")
    f.flush(); os.fsync(f.fileno())     # durable BEFORE we claim done
print("committed")
```

Graph:

```
start -> select_source;
select_source -> retrieve_slice [condition="context.tool.last_line=has_source"];
select_source -> exit           [condition="context.tool.last_line=no_source"];
...
commit -> select_source [condition="context.tool.last_line=committed", loop_restart="true"];
```

Note this **replaces your `has_source` diamond**. The tool's sentinel routes directly; the diamond was a no-op node that could not produce the value it routed on (see §4).

**`loop_restart="true"`** allocates a fresh log dir per iteration (`engine.py:802-811`) — keep it on the back edge so 200 iterations produce 200 inspectable iteration dirs.

### 2.5 Where durable state lives

The engine knows about **nothing on disk except `--cwd`**. It sets `context.target_dir` and runs tools there (`tool.py:125`). All durable state is your convention.

Conventions worth adopting from `RD/`:

| Path | Role | Committed? |
|---|---|---|
| `STATE.yaml` | Single source of truth, repo root | ✅ yes — resume reads the *committed* copy |
| `.ai/` | Ephemeral inter-node handoff (verdicts, decisions, breadcrumbs) | ❌ no |
| `.ai/<verdict>.txt` | One-word LLM verdict, `rm -f` after read | ❌ no |
| `ledger.jsonl` | Append-only record of completed units | ✅ yes |

For wiki-weaver:
- `ledger.jsonl` (or your `cost.jsonl`) — **the ingest ledger is your resume state.** One line per ingested source id. Committed.
- `.ai/current_source.txt` — breadcrumb for the in-flight source.
- `.ai/weave-verdict.txt` — the box node's one-word verdict, consumed by a reader tool.
- `lens/`, `wiki/`, `sources/` — the durable artifacts, committed.

`dev_machine.dot:486-489` notes resume works *because* the orchestrator checks out the branch before launch — the committed state is what survives. Uncommitted working-tree state is not resume state.

### 2.6 Failure modes when you get this wrong

| Failure | Cause | Defense |
|---|---|---|
| **Skipped-but-never-done** | State recorded before work completed | Record last, after fsync |
| **Double-commit** | Commit not guarded by a durable marker | `git diff --staged --quiet` → `nothing_to_commit` (`dev_machine.dot:209`) |
| **Duplicate content** | Ledger appended without membership check | Check-then-append |
| **Corrupt state** | Non-atomic write during crash | `atomic_write_text` |
| **Stale verdict** | Verdict file survives into next iteration | `rm -f` at read (`dev_machine.dot:168`) |
| **Silent no-op** | `${context.x}` literal passthrough → empty arg | Bare `${x}`; `${x:?}` to fail loud |
| **Phantom re-ingest** | Ledger not committed; next run sees clean tree | Commit the ledger in the same commit as content |
| **Out-of-band writes** | LLM edits state it was told not to | Liberal reader, canonical writer (`apply_state.py:54-60`) |

---

## 3. Loop bounding and cost

### 3.1 The real ceilings

Three, and only one is yours to set:

1. **`max_retries`** — within-node only. Not a loop bound.
2. **`max_steps = len(graph.nodes) × 50`** — hard engine safety net (`engine.py:66` `_MAX_GOAL_GATE_RETRIES: int = 50`; `engine.py:251`). Exceeding it is a FAIL with `"Pipeline exceeded N steps (safety bound)"` (`engine.py:258-271`).
3. **`max_pipeline_duration`** — wall clock, checked every step (`engine.py:273-288`).

For your `ingest.dot` (~10 nodes) that is ~500 steps ≈ ~55 sources at ~9 nodes/source. **Your 16-hour, N-source run can hit the safety bound and fail with a generic message.** Do not rely on it.

### 3.2 Bound the loop explicitly

The safety bound is a crash, not a design. Use the counter-file pattern (`dev_machine.dot:230`):

```
DrainBound [
  shape=parallelogram,
  tool_command="bash -c 'mkdir -p .ai; n=$(cat .ai/drain-count 2>/dev/null || echo 0); n=$((n+1)); echo \"$n\" > .ai/drain-count; if [ \"$n\" -lt ${max_sources:-50} ]; then echo continue; else echo budget_exhausted; fi'"
];
DrainBound -> retrieve_slice [condition="context.tool.last_line=continue"];
DrainBound -> exit_budget    [condition="context.tool.last_line=budget_exhausted"];
```

Two notes: `${max_sources:-50}` is the shell-default idiom that survives an unset param (`substitution.py:29-36`); and the counter file must be **deleted on successful completion**, or the next run starts pre-exhausted — the mirror of `dev_machine.dot:209` (`rm -f .ai/fix-attempts` on successful commit).

This also fixes your `init.dot` bound, which currently invents `context.iteration_limit_reached` that nothing sets.

### 3.3 Per-iteration time/cost budget, failing loud

Your `budget` node's design intent — non-zero exit halts the pipeline — is correct and matches the engine: a non-zero exit is FAIL (`tool.py:158-176`), and a FAIL outcome does **not** traverse unconditional edges (`edge_selection.py:79-94`). It only follows edges to nodes with `runs_on=always|failure`. So a failing budget node genuinely stops the run.

Make the ceiling real by measuring against a durable start time:

```python
started = float(Path(".ai/source-start").read_text())
elapsed = time.time() - started
ceiling = float(os.environ.get("PER_SOURCE_TIME_CEILING", "900"))
with open("cost.jsonl", "a") as f:
    f.write(json.dumps({"source_id": sid, "elapsed_s": elapsed, "pages": pages}) + "\n")
    f.flush(); os.fsync(f.fileno())
if elapsed > ceiling:
    print(f"[budget] {sid}: {elapsed:.0f}s exceeds {ceiling:.0f}s", file=sys.stderr)
    sys.exit(1)          # FAIL — halts the pipeline
print("within_budget")
```

Pass the ceiling via `tool_env="per_source_time_ceiling"` (uppercased into the environment, `tool.py:109-116`) rather than string-interpolating it — avoids the unbound-variable trap entirely.

Also add `timeout` on long nodes: `timeout="30m"` on the node kills the subprocess and FAILs (`tool.py:100-105, 140-147`). And `spawn_timeout` exists for hanging box nodes (`runner.py:343-346`).

---

## 4. Per-file defect list

### Defects common to all 7 files

| # | Defect | Fix | Cite |
|---|---|---|---|
| C1 | **Legend cluster** → duplicate start (`v_start`), duplicate exit (`v_exit`), exit-with-outgoing, 5–7 unreachable nodes. 8–11 ERRORs each. | Delete every `cluster_legend`. Move to comments. | `validation.py:122,152,229,247` |
| C2 | **`${context.X}` never substitutes.** Every tool_command is affected. | Use `${X}`; `${X:-default}` for optional. | `substitution.py:63-108`; verified |
| C3 | Diamonds route on context keys nothing sets (`context.enough`, `context.source_available`, `context.covered`, `context.structural_ok`, `context.correction_provided`). All resolve `""` → **no edge matches → pipeline FAILs**. | Route on `context.tool.last_line=` from the preceding tool, or have the box node `report_outcome` with `preferred_label`. | `conditions.py:84-94`; `edge_selection.py:96-101` |
| C4 | No resume gate; no idempotency in any commit/ledger node. | §2. | — |

> **On C3 — why your `diamond` no-ops are the deepest structural problem.** A `shape=diamond` maps to the `conditional` handler: it is a pass-through that evaluates *outgoing edge conditions*. It cannot itself produce the value it routes on. So `evaluate -> enough_gate -> [condition="context.enough=true"]` only works if `evaluate` actually set `enough` in context — which requires `report_outcome(context_updates={"enough": "true"})`. Your prompts say "Set context.enough to 'true'" as prose, which sets nothing. **Prose instructions do not write context.** Either drop the diamond and route directly off the producing node, or make the producer emit the key structurally.

---

### `overview.dot` — 21 ERRORs + parse failure. Recommend: **not a pipeline.**

| Line | Defect | Fix |
|---|---|---|
| 44 | `max_pipeline_duration="unbounded"` → **ValueError, parser crash** | Omit the attribute |
| 138 | `canon_pipeline -> targeted_reprocess` — **node is `targeted_reprocess_note`**. Auto-creates a **box/LLM node with an empty prompt** that will spawn a real agent. Verified. | Delete the edge |
| 121-133 | `cluster_layers` (5 `l_*` notes), `cluster_core_insight` (5 `ci_*`), `cluster_legend` (7 `v_*`) → 17 unreachable-node ERRORs | Delete all |
| 63 | `"context.purpose"="${context.purpose}"` on a folder node — **not substituted**, injected verbatim as literal `${context.purpose}` | Pass `--param purpose=...` to the child, or set it in a tool node before the folder |
| 106-115 | `outputs="schema_written,lens_seeded"` etc. — only merged back if the child sets those **exact bare keys** in its context | Ensure children set them via `parse_json=true` or `report_outcome` |
| 139-143 | `ask/lint/correct -> next_action [loop_restart]` — an unbounded human-driven menu loop, 28 nodes × 50 = 1400 steps | Acceptable if human-gated, but see below |

**Recommendation:** keep `overview.dot` as a **documentation diagram only** — do not make it runnable. It is genuinely valuable as the "I get it" artifact and it is the one file whose legends/clusters earn their place. Rename to `docs/overview-diagram.dot`, exclude from any `attractor run`, and let the six command pipelines be the executable surface. A top-level menu loop adds no capability that invoking the six directly does not already give you, and it doubles the resume surface area.

---

### `init.dot` — 9 ERRORs

| Line | Defect | Fix |
|---|---|---|
| 43 | `max_pipeline_duration="30m"` ✅ valid | — |
| 62 | `description="${context.purpose}"` → literal | `${purpose}` |
| 68-70 | `evaluate` prompt says "Set context.enough to 'true'" — **prose sets nothing** | `report_outcome(preferred_label="enough"|"not_enough")` |
| 79 | `tool_command="wiki-weaver init check-bound --counter-var interview_rounds --max-rounds 5"` — CLI does not exist | Write it; emit `bound_ok`/`bound_hit` |
| 96 | `${context.wiki_root}` → literal | `${wiki_root}` |
| 107-108 | Routes on `context.enough`, never set → **no match → FAIL** | Route on `preferred_label=` from `evaluate` |
| 115-116 | Routes on `context.iteration_limit_reached`, never set | Route on `context.tool.last_line=` from `check_bound` |
| 122-136 | Legend cluster → 6 ERRORs | Delete |

Corrected core:

```
evaluate -> write_schema [condition="preferred_label=enough"];
evaluate -> check_bound  [condition="preferred_label=not_enough"];
check_bound -> write_schema [condition="context.tool.last_line=bound_hit"];
check_bound -> ask          [condition="context.tool.last_line=bound_ok", loop_restart="true"];
```

---

### `ingest.dot` — parse failure + 9 ERRORs. **The critical file.**

| Line | Defect | Fix |
|---|---|---|
| 39 | `max_pipeline_duration="unbounded"` → **parser crash** | Omit |
| 55,69,81,91,102,114 | Six `${context.*}` → all literal | Bare `${...}` |
| 55 | `--restrict ${context.restrict_to_sources}` — optional param; even corrected, unset → literal → breaks CLI parsing | `--restrict "${restrict_to_sources:-}"` |
| 121-122 | `has_source` routes on `context.source_available`, never set → **FAIL on the first pass** | Delete the diamond; route on `select_source`'s `tool.last_line` |
| 132-134 | **`review_gate` has two edges to `commit`** (`[A] Accept`, `[C] Skip`). Verified: engine selects `[A] Accept` when the human picks `[C] Skip` — the human's choice is silently discarded. | Split into distinct targets: `commit_accept` and `commit_skip` |
| 100-104 | `budget` — right intent, but nothing measures elapsed time | §3.3 |
| 108 | `description="${context.validation_report} ${context.retention_flag}"` → literal | `${validation_report} ${retention_flag}` |
| 139 | `commit -> select_source [loop_restart]` ✅ correct shape | Keep |
| — | **No idempotency anywhere.** Re-running re-ingests everything. | §2.4 |
| 142-156 | Legend → 6 ERRORs | Delete |

**The `[A]/[C]` collision in detail.** `HumanGateHandler` builds `label_to_targets` and returns `suggested_next_ids=[targets]` (`human.py:307-313, 400-405`). Edge selection Step 3 then picks the **first unconditional edge whose `to_node` matches** (`edge_selection.py:59-63`). Both Accept and Skip resolve to `commit`, so Accept's edge always wins. Verified:

```
edges from g: [('commit','[A] Accept'), ('weave','[B] Guide'), ('commit','[C] Skip')]
selected for Skip: commit '[A] Accept'
```

"Skip (log only, do not merge)" and "Accept" have opposite semantics — this silently merges content the human rejected. **Give every gate choice a distinct target node.**

---

### `ask.dot` — 11 ERRORs

| Line | Defect | Fix |
|---|---|---|
| 47,64,70,95 | `${context.*}` → literal | Bare form |
| 47,70 | `--question ${context.question}` — **unquoted free text** → shell word-splitting/injection | `--question "${question}"`, or `tool_env="question"` |
| 110-111 | Routes on `context.covered`, never set → **FAIL** | `report_outcome(preferred_label="covered"\|"thin")` |
| 116-117 | Routes on `context.enable_file_back` — set only if `--param enable_file_back=...` passed; unset → `""` → `!=true` matches ✅ | Works, but document the param |
| 93-97 | **`prepare_injection` is unreachable** — declared, never given an incoming edge (real ERROR) | Wire `file_back_gate -> prepare_injection -> file_back` |
| 120-121 | Gate edges ✅ distinct targets | Keep |
| 99-104 | `file_back` folder → `ingest.dot`; with the ledger pattern a synthetic source must be registered in `sources/` first | Have `prepare_injection` write the synthetic source to disk |
| 126-141 | Legend → 8 ERRORs | Delete |

---

### `lint.dot` — 8 ERRORs (closest to correct)

| Line | Defect | Fix |
|---|---|---|
| 36 | `max_pipeline_duration="10m"` ✅ | — |
| 52,70 | `${context.wiki_root}`, `${context.report_dir}` → literal | Bare form |
| 77,81 | Routes on `context.structural_ok`, never set → **FAIL** | `structural_validate` emits `structural_ok`/`structural_bad`; route on `context.tool.last_line=` |
| 62-66 | `analyze` box, one bounded pass, no self-loop ✅ | Keep |
| 87-100 | Legend → 5 ERRORs | Delete |

Read-only with no gate and no write path — the cleanest design of the seven. After C1/C2/C3 it is nearly runnable.

---

### `correct.dot` — 9 ERRORs

| Line | Defect | Fix |
|---|---|---|
| 63,69,81,91 | `${context.*}` → literal | Bare form |
| 63,91 | `--claim ${context.correction_text}` — **multi-line free text unquoted into a shell command**. Highest-risk injection site in the set. | `tool_env="correction_text"`, read from env |
| 96-97 | Routes on `context.correction_provided` — works if passed as `--param`, `""` otherwise ✅ | Document; prefer a tool probe |
| 89-93 | `persist_lens` — correctly identified as load-bearing, but **not idempotent**: re-running appends a duplicate correction and re-commits | Check-then-append; `git diff --staged --quiet` guard |
| 73-77 | `re_derive` box writes pages **and** the flow commits — ensure the box writes only pages, never the lens or the commit | §1.6 |
| 107-121 | Legend → 6 ERRORs | Delete |

---

### `canon.dot` — 8 ERRORs

| Line | Defect | Fix |
|---|---|---|
| 55,70,94,100 | `${context.*}` → literal | Bare form |
| 55 | `--content ${context.canon_content}` — **whole document body through a shell arg**; also `canon_content` is never set by any node (`author_canon` is freeform → sets `human.gate.text`) | Read `${human.gate.text}` via `tool_env`, or have the gate write a file |
| 49,76 | `description="${context.canon_doc_path}"` / `"${context.impacted_pages}"` → literal | Bare form |
| 59-67 | Comment says "confirm this against the real engine" — **confirmed: the shared-context assumption is correct.** `impact_scan` setting `restrict_to_sources` does flow to the child, because the child clones the parent context (`pipeline.py:163`). | Keep the approach; use `parse_json=true` to set the key |
| 70 | `--set-context impacted_pages,restrict_to_sources` — a tool cannot set context by flag | Emit JSON on stdout + `parse_json="true"` (`tool.py:183-195`) |
| 110-111 | Gate edges ✅ distinct targets | Keep |
| 113-115 | `reprocess -> validate -> commit` — but `[B] Skip` also reaches `commit`; distinct paths converging is fine (unlike the hexagon case, these are *different labels to different first nodes*) ✅ | Keep |
| 121-133 | Legend → 5 ERRORs | Delete |

The `parse_json` idiom that replaces `--set-context`:

```
impact_scan [
  shape=parallelogram, parse_json="true",
  tool_command="python3 -m wiki_weaver.canon.impact_scan --wiki-root ${wiki_root}"
];
```
stdout: `{"impacted_pages": "a.md,b.md", "restrict_to_sources": "s1,s2"}` → each key set into context (`tool.py:183-189`). Note this **conflicts with `tool.last_line` routing** (the JSON is the last line), so use `parse_json` for data and a separate node for routing.

---

## 5. Rewrite order

1. **Delete every legend cluster** in all 7 files. ~60 of ~76 ERRORs, mechanical.
2. **Remove `max_pipeline_duration="unbounded"`** from `overview.dot`, `ingest.dot`. Unblocks parsing.
3. **`${context.X}` → `${X}`** everywhere; `${X:-}` for optionals; `tool_env` for free text.
4. **Delete the routing diamonds**; route on `context.tool.last_line=` from the producing tool, or `preferred_label=` from a `report_outcome`.
5. **Fix `ingest.dot`'s `[A]/[C]` collision** — distinct target per gate choice.
6. **Fix `ask.dot`'s unreachable `prepare_injection`**; delete `overview.dot`'s `targeted_reprocess` typo edge.
7. **Build the `wiki-weaver` CLI** as `python3 -m wiki_weaver.*`, mirroring `RD/devmachine/`. Every tool: sentinel on stdout, diagnostics on stderr, `atomic_write_text` for state.
8. **Make `ingest.dot` resumable** — ledger-derived pending set (§2.4). This is the 16-hour fix.
9. **Add explicit drain bound + real budget enforcement** (§3).
10. **Validate after each step.**

Definition of done for the mechanical pass:

```bash
for f in pipeline/*.dot; do
  python3 -c "
import sys; sys.path.insert(0,'../amplifier-bundle-attractor/modules/loop-pipeline')
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import validate
g=parse_dot(open('$f').read())
e=[d for d in validate(g) if d.severity=='ERROR']
print('$f', 'OK' if not e else f'{len(e)} ERRORS')
"
done
```

Then a real smoke run with stub CLIs before wiring the LLM:

```bash
attractor run pipeline/ingest.dot --param wiki_root=/tmp/w --cwd /tmp/w \
  --on-human-gate auto-approve
```

`--on-human-gate auto-approve` selects the **first choice** at each gate (`cli.py:193-196`) — for `ingest.dot` that is `[A] Accept`, which is what you want for a smoke test. Use `console` for real runs (`cli.py:197-213`); the default `fail` mode deliberately halts on any gate.

---

## 6. Reference index

| Topic | File:line |
|---|---|
| Param parsing, `@file` convention | `PR/params.py:14-70` |
| CLI surface, `--on-human-gate` | `PR/cli.py:32-86, 192-213` |
| Param → context seeding; reserved keys | `PR/runner.py:100-136` |
| Substitution algorithm; shell-default guidance | `LP/substitution.py:63-108, 29-36` |
| Edge selection (5 steps); FAIL fail-fast | `LP/edge_selection.py:25-101` |
| Condition language; `context.` prefix stripping | `LP/conditions.py:15-100` |
| Validation rules | `LP/validation.py:65-102` |
| Shape → handler map | `LP/validation.py:24-35` |
| Reachability (ERROR) | `LP/validation.py:247-287` |
| Tool exec, cwd, `tool.last_line`, `parse_json` | `LP/handlers/tool.py:125,158-176,179-221` |
| Human gate: labels→choices, `suggested_next_ids` | `LP/handlers/human.py:304-313, 400-415` |
| Folder: context clone, `context.*` inject, `outputs` | `LP/handlers/pipeline.py:163-179, 306-315` |
| `report_outcome` priority (authoritative) | `LP/backend.py:747-779` |
| `report_outcome` schema | `AB/modules/tool-report-outcome/.../__init__.py:50-84` |
| Start-from-start; `max_steps = nodes × 50` | `LP/engine.py:241, 66, 251-271` |
| `loop_restart` fresh log dirs | `LP/engine.py:802-811` |
| Checkpoint is observability-only | `LP/checkpoint.py:9` |
| **Resume gate reference impl** | `RD/devmachine/resume_gate.py:48-95` |
| **Resume gate wiring (3-way, fail-loud)** | `RD/pipelines/dev_machine.dot:498-502, 817-820` |
| **LLM/tool split rationale (14/25 → 25/25)** | `RD/devmachine/apply_state.py:1-30` |
| **Verdict-file pattern (fail-closed)** | `RD/pipelines/dev_machine.dot:154-168, 897-899` |
| Atomic write | `RD/devmachine/io_utils.py:15-43` |
| Counter-file bound | `RD/pipelines/dev_machine.dot:230` |
| Commit guard (`nothing_to_commit`) | `RD/pipelines/dev_machine.dot:209` |
| Liberal-reader/canonical-writer | `RD/devmachine/apply_state.py:54-60` |
