# Lineage — what V4 changed from V1 and V2

One row per behavior. V1 is `main` of this repo; V2 is the `v2-attractor-rewrite` branch
(verified against HEAD 3a62de7, 2026-09-23). A row is added when a gap is closed or a
behavior deliberately differs. "Not checked" means exactly that.

| Behavior | V1 | V2 | V4 (Stage 1) | Why |
|---|---|---|---|---|
| Model calls live in the graph | Python orchestrates; `.dot` is a façade | `weave` is a real box node, but the proxy runs outside the graph (`python -m wiki_weaver.aitl.run`) | Every model call is a box node in one of three graphs; CLI is a dispatcher; tested | Brian: build it as a pure graph on dot-runner |
| Lens reaches the writer | `--purpose` → a policy file the pipeline reads each cycle | Lens directories exist; the writer's input is wiki pages only — the lens never arrives | Injected by code into brief and write on every pass (`steps.py:248`) | V2's design, delivered |
| Reviewer / human proxy | LLM assess node *after* the write, rubric-scored | `aitl/` proxy, 2,236 lines, never wired into a graph; default gate mode halts | The brief: a box node *before* the write, primed by the lens | Prior A/B program: pre-write gate paid, post-write grader retired |
| Guards in the graph | assess node in the graph | All `aitl` references in `.dot` files are comments | Deterministic checks in the graph that route: pass / rewrite once / hold | Measured: a 50-line structural check caught losses an LLM judge missed |
| Content-loss guard | Prompt contract + an LLM retention judge | Thresholds exist; `retention_check.py` returns 0 unconditionally | Fires on >15% line loss or a lost heading without a superseded marker; routes | A guard that cannot fail is not a guard |
| Pages accumulate | Integrate-don't-overwrite, in the prompt | Inert retention guard | Dated superseded markers; verified by the CP3 edit test (44 → 50 lines, 1 → 4 markers) | V3 hid 43% of what it extracted by overwriting |
| Citations | `[N]` footnotes | Source filename | `[s<id>: "quote"]` — V1's id form plus a verbatim quote, checked by exact match; specific claims only, not every sentence; 6,609 citations, 0 misses at CP3 | Thread-following was the largest measured win; the quote lets grep land in a 150 KB file |
| Edited source, same filename | Re-read as a brand-new source (content hash); no awareness it is an edit | Never re-read — keyed on filename (`select_source.py:189`) | Re-read; the writer gets a diff and every page that cites the old version; stale quotes marked superseded | V4 keeps hashes as the identity and closes the stale-quote gap |
| Corrections persist | No | `lens/corrections/` read by the proxy only; gate answers deleted after use | Writer reads `lens/corrections/` from day one; the `correct` verb is Stage 3 | The feedback loop was where Brian saw the highest value |
| Installs | Yes | Wheel builds with zero `.py` files; `import wiki_weaver` fails | Yes; 37 tests, purity tests | — |
| Writer prompt | Not checked | One 8,188-character prompt carrying every rule | Short intent prompt + the lens + two rules (keep disagreements two-sided; cite what you say) | Policy lives in the lens, not the prompt |
| Graph size | Not checked | `ingest.dot`: ~29 nodes with segment and catalog machinery | `ingest.dot`: 13 nodes; 3 model calls per source | Simpler and cleaner, per the sync |
| Source cap per page | None | None | Tried at 10, retracted at CP2 — bound after one epoch; the citation check guards attribution instead | Measured in the run, not assumed |
| Engine step cap | Not checked | Not checked | CLI batches 40 sources per graph run; one `result.json` per invocation | dot-runner stops a run at nodes × 50 steps |
| Exit codes and lock | 0/1/2/3/4/5/75; `pidlock.py` | Not checked | V1's table; `.wiki/ingest.lock` | RepoWeaver branches on the codes |
| Lens seeding | `--purpose` only | Hand-written `lens/persona.md` | `--purpose`, `--plain`, or an interview (three questions) | Brian: "pass in how I'm going to use it, or an interview style approach" |
| Corpus layout and CLI | `_inbox/ _sources/ .wiki/`; the verbs | Different layout; no CLI | V1's names and verbs, kept as the wrapper contract | RepoWeaver and the resolver swap in unchanged |
