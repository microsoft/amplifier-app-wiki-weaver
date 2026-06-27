# AGENTS.md — wiki-weaver

Wiki-weaver compiles a structured, interlinked markdown wiki from source material and answers
questions by reading the compiled wiki instead of RAG (the Karpathy "LLM wiki" pattern).

## Commands (the five `.dot` pipelines)

Each user command is an attractor `.dot` pipeline in `pipeline/`, run via the CLI / lib:

- `init` (`init.dot`) — scaffold a wiki + LLM-design a domain-fit schema from `--purpose`.
- `ingest` (`ingest.dot` → invokes `synthesize.dot`) — drain `_inbox/` into the wiki.
- `ask` (`ask.dot`) — answer a question by reading the compiled wiki, with citations.
- `lint` (`lint.dot`) — deterministic structural validation (no LLM).

`doctor` (env diagnostics) and `query` (a naive substring-grep stub — not the query surface;
use `ask`) round out the CLI.

`build-dashboard <corpus> --out <file.html>` is a **deterministic** command (no LLM, no Amplifier
runtime): it builds the corpus indexes, then renders a self-contained HTML dashboard. It is the first
pipeline to ship a Resolve sidecar (`pipeline/build-dashboard.dot` + `build-dashboard.resolver.yaml`)
because it is pure CLI delegation — see Architecture. Flags: `--theme <file>`, `--group-by <field>`
(default `type`), `--skip-index`.

## Architecture

Commands are **thin lib wrappers over attractor `.dot` pipelines**:

- `cli/wiki_weaver.py` — argparse front end (dispatch only).
- `cli/lib.py` — importable concept-level functions; owns the outer corpus sweep and all
  process state (the `.processed.jsonl` ledger + `_archive/`), written by code *only* on real
  convergence (a deterministic tamper guard reverts agent-written process state and fails loud).
- `cli/engine_runner.py` — runs the inner pipelines on the attractor engine. The `.dot` files
  are `$token` templates; `build_*_from_file()` fills them with concrete paths/prompts before
  execution. The `.dot` files are **not** drop-in standalone.
- `cli/policy.py` — resolves per-wiki schema/rubric/model overrides (`<wiki>/policy/…`).

**Deterministic dashboard layer** (no engine, no LLM, no Amplifier runtime — pure stdlib + `tinycss2`
+ `markdown`):

- `wiki_weaver/index.py` — `build_indexes(corpus_dir)` scans `<corpus>/*.md` and materialises five JSON
  indexes under `<corpus>/.wiki/index/` (`backlinks`, `links`, `tags`, `properties`, `aliases`), each
  wrapped in the envelope `{schema_version, built, data}`. Also the importable `query_*` layer + named
  errors (`PageNotFound`, `CitationNotFound`, `CycleDetectedError`, `SchemaVersionError`). Staleness is
  derived by comparing corpus mtimes against `built.max_mtime`; reads never refuse on stale.
- `wiki_weaver/dashboard.py` — `build_dashboard(...)` consumes the indexes and renders the
  domain-blind, Almanac-themed self-contained HTML. Theming reads `<corpus>/.wiki-dashboard/theme.json`
  (`--wiki-*` token overrides + optional `title`) and appends `.wiki-dashboard/custom.css` verbatim;
  enrichment CSS from a consumer is sanitized through tinycss2 (the security boundary — hard import,
  never a silent no-op).

**Agent-tool surface** (`modules/tool-wiki-weaver/`): mounts **9** tools — the 4 pipeline commands
(`wiki_weaver_init/ingest/ask/lint`) plus 5 read-only index query tools that wrap `index.query_*`:
`wiki_backlinks`, `wiki_graph_neighbors`, `wiki_tags`, `wiki_properties`, `wiki_resolve_citation`. The
five require `build_indexes()` to have run first.

Runtime: requires the Amplifier runtime (`amplifier_foundation` + `unified_llm`). `pyproject`
declares these as `@main` git deps + `allow-direct-references`, so `uv tool install git+...`
resolves them into an isolated tool venv (it is NOT self-contained — it still uses an installed
Amplifier at runtime for provider keys + the cached engine bundle). `python -m wiki_weaver doctor`
(and every command's preflight) verifies the runtime is importable.

## Build / test

- Tests live in `eval/` (`test_*.py`) alongside the eval harnesses.
- **Canonical test command (use this):**

  ```bash
  uv run pytest eval/ -q
  ```

  `uv run` syncs the project `.venv` first, so the runtime deps (`tinycss2`,
  `markdown`) AND the dev-group test tooling (`pytest`, `pytest-asyncio`) are
  all present in ONE interpreter. Do **not** invoke a global `pytest` — it runs
  under a different interpreter that lacks the runtime deps, which silently
  changes behaviour (e.g. the dashboard CSS sanitizer would be import-broken).
  The dev tooling is declared under `[dependency-groups].dev` in `pyproject.toml`
  and locked in `uv.lock`; run `uv sync` after changing deps.

- Deterministic tests run without the Amplifier runtime; tests needing it
  self-skip when `amplifier_foundation` is absent.
- Run quality checks (format, lint, types) before committing.

## Data discipline (important)

- **NEVER commit source corpora** (articles/transcripts) or built wikis. `runs/`, `wiki/`,
  `.ai/`, and `.amplifier/evaluation/` are gitignored — keep it that way.
- Eval / run outputs belong in `~/.amplifier/evaluation/wiki-weaver/<datetime>/`, never the repo.
- Scenario fixtures under `eval/` are **synthetic by design** — keep them generic. No real
  names, internal product names, personal paths, or real source content. If a scenario needs a
  "team" example, keep it generic (e.g. "a team-decisions wiki"), not a real team/product.
