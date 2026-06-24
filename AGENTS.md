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

Runtime: requires the Amplifier runtime (`amplifier_foundation` + `unified_llm`). `pyproject`
declares these as `@main` git deps + `allow-direct-references`, so `uv tool install git+...`
resolves them into an isolated tool venv (it is NOT self-contained — it still uses an installed
Amplifier at runtime for provider keys + the cached engine bundle). `python -m wiki_weaver doctor`
(and every command's preflight) verifies the runtime is importable.

## Versioning & dependency strategy (decisions + gotchas)

wiki-weaver tracks the latest `@main` of the Amplifier engine + modules + `amplifier-foundation`
and **fixes forward** when an upstream change breaks something. It does **not** pin to commit SHAs,
and **no `uv.lock` is committed** (a lock would freeze clone/`uv sync` users against `@main`).

Why not pin (both learned the hard way):

- **SHA pins don't even clone.** foundation's git source resolves via `git clone --depth 1 --branch <ref>`,
  which accepts branch/tag *names* only — a 40-char commit SHA fails with `Remote branch <sha> not found`.
- **Refetching on the hot path is a non-starter.** Making foundation's `resolve()` check the remote for a
  moved `@main` would add a network round-trip per module on *every* startup (amplifier-app-cli resolves
  ~8–15 sources on the critical path), breaking offline use and adding a per-module timeout tail.

How freshness works instead (explicit, zero hot-path cost):

- foundation's `resolve()` returns the cached clone and **never refetches a moved branch ref** — so `@main`
  silently goes stale after first install. Freshness comes only from an **explicit** action:
- `wiki-weaver update` — Layer 1: `uv tool install --reinstall git+…wiki-weaver` (plain `--reinstall` *does*
  refetch a moved `@main` — DTU-proven; a `--no-cache` / `uv cache clean` fallback ladder + fail-loud guard
  back it up). Layer 2: foundation `GitSourceHandler.update()` per `@main` engine source (rmtree+reclone).
- `wiki-weaver update --check` — drift detector (ls-remote vs resolved local commit; destroys nothing).
- `wiki-weaver doctor` — reports the resolved `@main` commit per source (your only window into staleness).

Docs discipline: a design/plan doc that describes a *rejected* strategy is context poison — delete or
retcon it, don't leave it alongside the real one.

## Build / test

- Tests live in `eval/` (`test_*.py`) alongside the eval harnesses.
- Run with a venv that has `pytest` + `pyyaml`:

  ```bash
  pytest eval/ -q
  ```

- Run quality checks (format, lint, types) before committing.

## Data discipline (important)

- **NEVER commit source corpora** (articles/transcripts) or built wikis. `runs/`, `wiki/`,
  `.ai/`, and `.amplifier/evaluation/` are gitignored — keep it that way.
- Eval / run outputs belong in `~/.amplifier/evaluation/wiki-weaver/<datetime>/`, never the repo.
- Scenario fixtures under `eval/` are **synthetic by design** — keep them generic. No real
  names, internal product names, personal paths, or real source content. If a scenario needs a
  "team" example, keep it generic (e.g. "a team-decisions wiki"), not a real team/product.
