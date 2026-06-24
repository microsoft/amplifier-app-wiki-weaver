# wiki-weaver Phase 1 — Implementation Plan

> **Status:** Buildable. Grounded in the actual repo + the cached attractor bundle + the
> foundation activator source. The design is already validated (design conversation + 6-lens
> council, conditional-approve with the seam fix as a hard requirement); this plan does **not**
> re-litigate it — it turns it into ordered, file-level work and resolves the two gating unknowns.
>
> **Design of record:** `docs/designs/wiki-weaver-platform.md`
> **Date:** 2026-06-23

---

## 0. Resolved unknowns (these gate the plan — read first)

### Unknown 1 — Pin transitivity: **pinning the bundle commit is NOT enough.**

**Finding (evidence-backed).** The cached attractor bundle
(`~/.amplifier/cache/amplifier-bundle-attractor-*/bundles/attractor-pipeline.yaml`) and every
YAML it pulls in (`behaviors/attractor-core.yaml`, `agents/attractor-agent-*.yaml`) declare
**every module `source:` at `@main`** — provider-anthropic, context-simple, tool-filesystem,
tool-bash, tool-search, and even the same-repo `loop-pipeline` / `loop-agent`
(`amplifier-bundle-attractor@main#subdirectory=modules/...`). Pinning
`ATTRACTOR_PIPELINE_GIT@<commit>` in `engine_runner.py` pins only the **bundle YAML text** (and
the `attractor:` namespace refs that resolve within the same repo). The explicit `source:` git
URLs are absolute `@main` pointing at **separate repos / separate `@main` refs** and are NOT
pinned by the bundle commit. **Conclusion: pinning the bundle commit alone does not achieve
determinism.**

**Decision (no cross-repo work required for Phase 1).** Foundation's `Bundle.prepare()` accepts a
`source_resolver: (module_id, original_source) -> resolved_source` callback (see
`amplifier_foundation/bundle/_dataclass.py:303-345`) applied to every module source *before*
activation. wiki-weaver supplies a `source_resolver` that rewrites each known `@main` module
source to a **pinned commit** from a local pin map. This pins the entire transitive set **from
inside wiki-weaver** — no change to `amplifier-bundle-attractor` needed.

- **Cross-repo (cleaner, later, optional):** tag `amplifier-bundle-attractor` and pin its
  *internal* `source:` refs to commits, then pin wiki-weaver to that tag. Flagged in §6; **not**
  required for the Phase 1 gate.

### Unknown 2 — Seam fix: **`install_deps=False` does NOT stop the runtime fetch; true bake-into-venv is not achievable in wiki-weaver alone.**

**Finding (evidence-backed).** `ModuleActivator.activate()`
(`amplifier_foundation/modules/activator.py:68-114`) **always** calls
`self._resolver.resolve(source_uri)` first — which git-clones each module `source:` into
`~/.amplifier/cache` (`sources/git.py`, cache-keyed by `{url}@{ref}`) — and *then*, only if
`install_deps=True`, pip-installs that module's Python deps. So `install_deps=False` skips the
**pip-dependency** step but **not** the source clone. There is **no entry-point / installed-package
resolution path**: the engine loads module CODE from `source:` git refs into the cache and onto
`sys.path`, not from packages in the venv. Therefore declaring the engine modules as wiki-weaver
`pyproject` deps does **not** make the activator skip the clone (the "already importable → skip"
checks at `activator.py:215-238,358-410` apply only to the *pip dep* step and a bundle's *own*
package, never to the module-source resolve).

**Consequence:** the council's literal "bake the engine modules into the venv at install time so
the venv is complete before first run" is **not fully achievable in wiki-weaver alone** with the
current foundation API.

**Decision — minimal viable seam fix (wiki-weaver-only) that satisfies the *intent* (no runtime
mutation, deterministic, fail-loud):**

1. **Pin everything** via the Unknown-1 `source_resolver`. This converts the runtime resolve from
   non-deterministic `@main` drift (which also fires a `git ls-remote` staleness check + possible
   refetch every run) into a **deterministic, immutable-commit, fetch-once-then-offline** cache
   read. A pinned commit is content-addressed in the cache → same bytes every run, no network once
   warm.
2. **Replace the implicit first-run install with an explicit warm + verify-only runtime.** Add an
   explicit, idempotent **warm step** (`wiki-weaver doctor --bootstrap`, also run by
   `provider install` and `self update`) that resolves the full pinned module set into cache once.
   The **run path** (`_build_prepared`) calls `prepare(install_deps=False)` *after* a preflight that
   asserts every pinned module is present in cache. **If anything is missing → fail loud**
   (`run: wiki-weaver doctor --bootstrap`) — **never** silently fetch/install mid-ingest. This is
   the council's "verify-only, fail-loud no-op" for the hot path; the only mutation is the explicit,
   user-invoked warm step (mutation belongs there, not mid-run).
3. **Assert version coherence:** preflight + `doctor` compare the venv-installed provider version
   (entry-point discovery) against the pinned commit's declared version; **fail loud on skew.** One
   source of truth per provider version.

- **True bake-into-venv (parked / cross-repo):** either vendor the pinned module set into the wheel
  (design Alternative B), or a foundation change to resolve modules from installed packages /
  entry points. Both are larger and out of Phase 1 scope — flagged in §6.

---

## 1. Ordered tasks

Order rationale: **pin first** (keystone, lowest-risk, proven target — Phase 0 already proved the
bootstrap against a known-good set). Seam fix rides on the same `_build_prepared` plumbing, so it is
task 2. Provider lift and the provider registry build on a stable, pinned engine.

> **Two independent version axes — keep them separate while reading this plan.** This plan is
> mostly about axis (b); only the *model-id* part of Task 3 is axis (a), and it has shipped.
> - **(a) Model-id selection** — *which LLM we call within a family* — is **live family resolution,
>   SHIPPED** (attractor PR #68 `150de03` + wiki-weaver PR #6 `5acd7de`). Family tokens
>   (`sonnet`/`opus`/`haiku`) resolve at runtime to the newest stable served model via the upstream
>   `unified_llm` resolver; explicit ids pass through. **No pinning** of model ids. Pre-1.0 the
>   wiki-weaver layer is anthropic-only-guarded; the upstream resolver supports anthropic/openai/gemini.
> - **(b) Engine / dependency versions** — *which engine code we run* — stay **pinned + atomically
>   upgraded** (Tasks 1, 2, 4 below). This is **PLANNED** Phase 1 work and is the bulk of this plan.
>
> Do not conflate "pin the model id" (gone — now live resolution) with "pin the engine commit"
> (the load-bearing strategy that remains).

| # | Task | Touches | Status |
|---|------|---------|--------|
| 1 | Pin the engine (pin map + `source_resolver`) | `engine_runner.py`, new `pins.py` | **PLANNED** |
| 2 | Seam fix: explicit warm + verify-only runtime + coherence assert | `engine_runner.py`, `lib.py` (doctor) | **PLANNED** |
| 3 | Lift provider/model into ALL DOTs (incl. `synthesize.dot` + ingest drain path) | `engine_runner.py`, `pipeline/synthesize.dot` | **DONE** — model selection via live family resolution (#68/#6); `synthesize.dot` model+provider substitution now applies on BOTH the CLI (`build_dot`) and the `run_ingest` drain path (materializes a resolved copy via the shared `_substitute_models` helper); verified live (opus/haiku per-stage, zero residual sonnet) |
| 4 | Provider registry + pinned extras + `provider` subcommands + global config/secrets | new `providers.py`, new `config.py`, `pyproject.toml`, `wiki_weaver.py`, `lib.py` | **PLANNED** |

---

### Task 1 — PIN the engine (keystone)

**Goal:** kill `@main` drift; make the transitive module set deterministic from inside wiki-weaver.

**1a. New file `wiki_weaver/pins.py` — the single source of truth for pins.**

```python
"""Pinned commits for the attractor engine bundle and every module it resolves.

ONE source of truth. doctor reports these; the source_resolver enforces them;
the coherence check asserts the installed provider package matches.
"""
# Engine bundle (the attractor-pipeline.yaml repo).
ATTRACTOR_BUNDLE_COMMIT = "5ae3118"          # Phase-0 proven good
CONTEXT_INTELLIGENCE_COMMIT = "<pin>"        # hook-context-intelligence repo

# module repo URL (without git+ / @ref)  ->  pinned commit SHA
# Covers every `source:` ref in attractor-pipeline.yaml + attractor-core.yaml
# + the attractor-agent-*.yaml child agents (all currently @main).
MODULE_PINS: dict[str, str] = {
    "https://github.com/microsoft/amplifier-bundle-attractor": "5ae3118",  # loop-pipeline, loop-agent, tool-report-outcome, hooks-*
    "https://github.com/microsoft/amplifier-module-provider-anthropic": "<pin>",
    "https://github.com/microsoft/amplifier-module-provider-openai": "<pin>",
    "https://github.com/microsoft/amplifier-module-context-simple": "<pin>",
    "https://github.com/microsoft/amplifier-module-tool-filesystem": "<pin>",
    "https://github.com/microsoft/amplifier-module-tool-bash": "<pin>",
    "https://github.com/microsoft/amplifier-module-tool-search": "<pin>",
    "https://github.com/microsoft/amplifier-bundle-context-intelligence": "<pin>",
    # add provider-gemini / tool-web / bundle-filesystem only if a used agent path needs them
}

def pin_source(source: str) -> str:
    """Rewrite a `git+https://REPO@ref[#subdirectory=...]` source to its pinned commit.

    Returns the source unchanged when the repo is unknown (fail-open here; the
    preflight in engine_runner is the fail-loud gate). Pure string surgery — no I/O.
    """
```

`pin_source` parses `git+https://<repo>@<ref>#subdirectory=<sub>`, looks up `<repo>` in
`MODULE_PINS`, and substitutes the pinned SHA for `<ref>` (preserving `#subdirectory=`).

**1b. `engine_runner.py` — pin the two module-level constants and thread a `source_resolver`.**

- Lines **83-86** (`ATTRACTOR_PIPELINE_GIT`): change `@main` → `@{ATTRACTOR_BUNDLE_COMMIT}`
  (import from `pins`).
- Lines **90-93** (`CI_HOOK_SOURCE`): change `@main` → `@{CONTEXT_INTELLIGENCE_COMMIT}`.
- Line **277** (the `_resolve_agent_bundle` fallback git URL, currently hardcoded `@main`): route
  through `pin_source(...)`.
- `_build_prepared` (**895-941**): pass the resolver to prepare:
  ```python
  from .pins import pin_source
  prepared = await composed.prepare(
      install_deps=install_deps,
      source_resolver=lambda module_id, source: pin_source(source),
  )
  ```

**Acceptance criteria (Task 1):**
- `grep -rn "@main" wiki_weaver/` returns **0** matches for engine/module/CI sources (the env-var
  local-override escape hatches may remain, but no `@main` default).
- A dry log line (debug) shows each module activated at its pinned SHA (no `@main`).
- **Gate (re-run Phase 0 bare-DTU bootstrap):** clean box + `uv tool install` + one key + `ingest`
  → engine resolves the **same pinned commit set every time** (diff two cold runs' resolved SHAs →
  identical) and still writes a real wiki (frontmatter + `[[wikilinks]]` + `[^citations]`,
  converged ≥1/1). This is the design's Phase 0 gate, now run against the *pinned* set.

---

### Task 2 — Seam fix: explicit warm + verify-only runtime + coherence assert

**Goal:** the hot path (`ingest`/`ask`) performs **zero** network/install mutation; all resolution
happens in an explicit warm step; skew fails loud.

**2a. `engine_runner.py` — preflight + verify-only run path.**

- New `def required_module_sources() -> list[str]`: the full pinned source list the run needs
  (the attractor bundle + every entry in `MODULE_PINS` actually on the active agent path + the CI
  hook). Derive from `pins.MODULE_PINS`.
- New `def preflight_cache(*, warm: bool = False) -> list[str]`:
  - For each required pinned source, compute its cache path the same way the git handler does
    (`hashlib.sha256(f"{git_url}@{ref}").hexdigest()[:16]`, mirroring
    `sources/git.py:_get_cache_path`) and check the clone exists + integrity (`.git/` present).
  - `warm=False` (run path): return the list of **missing** sources. Non-empty → caller fails loud.
  - `warm=True` (bootstrap path): `await activate`/resolve each missing source once (the ONLY place
    that fetches), then re-verify.
- `_build_prepared` (**933-939**): drop the implicit `install_deps = not _DEPS_INSTALLED` default.
  New rule:
  - Run path default = `install_deps=False`, **after** `missing = preflight_cache(); if missing:
    raise RuntimeError("engine modules not warmed: <list>\\n  run: wiki-weaver doctor --bootstrap")`.
  - `WIKI_WEAVER_INSTALL_DEPS=1` still forces install (dev/warm escape hatch), unchanged semantics
    otherwise.

**2b. `lib.py` `doctor()` — add `--bootstrap` + pin reporting + coherence.**

- `wiki-weaver doctor --bootstrap` calls `engine_runner.preflight_cache(warm=True)` (the explicit,
  idempotent warm). Prints each module + resolved pinned SHA.
- `doctor` (no flag) reports: pinned engine-bundle commit, each installed provider + version
  (entry-point discovery, Task 4), active provider/model, selected provider's required
  env/config presence, endpoint reachability, **and version coherence** (installed provider pkg
  version vs the pinned commit's declared version → ✓ / ✗ fail-loud).

**Acceptance criteria (Task 2):**
- After `doctor --bootstrap`, run `ingest` with the network blocked (offline) → succeeds (proves
  zero runtime fetch on the hot path).
- Delete one cached pinned module, run `ingest` → **fails loud** with the `doctor --bootstrap`
  remediation message; **no silent refetch**.
- Two concurrent `ingest` runs after warm → neither mutates the cache (verify-only); no
  half-written package class of failure.

---

### Task 3 — Lift provider/model into ALL DOTs (incl. `synthesize.dot`)

> **Status: model-selection portion DONE → evolved to live family resolution** (attractor PR #68
> `150de03` + wiki-weaver PR #6 `5acd7de`). The "lift the model out of the DOTs into config" intent
> shipped, but **not** as a static config value — it became **live family resolution**: per-stage
> model values are now **family tokens** (`sonnet`/`opus`/`haiku`) resolved at runtime to the newest
> stable served model via the upstream `unified_llm` resolver (`wiki_weaver/model_resolver.py` is a
> thin shim over `resolve_latest_for`; defaults in `wiki_weaver/policy.py`). Explicit ids still pass
> through. There is **no model id to pin**. **Still PLANNED / VERIFY:** *provider* substitution into
> `synthesize.dot` via the **ingest-drain path** (the `run_ingest` materialize-into-`logs_dir` fix
> below) is not confirmed shipped in this pass — the live-resolution engine is wired into the
> per-node injection in `build_dot`, but the drain-path bypass described next still needs verifying.
> Treat the prose below as the design for the **provider-lift + drain-path** work that remains.

**Goal:** one config knob (`policy.provider` + `policy.model_for(stage)`) drives every LLM node.
Today `init.dot` and `ask.dot` already substitute (see `build_init_dot_from_file:1442-1443`,
`build_ask_dot_from_file`), but `synthesize.dot` **hardcodes** `llm_provider="anthropic"` /
`llm_model="claude-sonnet-4-6"` on its three LLM nodes (`pipeline/synthesize.dot:42-43,86-87,96-97`)
and `build_dot` deliberately does **not** substitute (`engine_runner.py:241-243`).

**Critical subtlety (must handle):** the production path is `run_ingest`, which does **not** call
`build_dot`. It reads `INGEST_DOT` and injects `synthesize.dot`'s **absolute path**
(`synthesize_dot_abs = str(INNER_DOT)`, `engine_runner.py:1091,1104`) as a folder sub-pipeline — so
the engine loads `synthesize.dot` **straight from the package dir**, bypassing any Python
substitution. Substituting only in `build_dot` covers `run_inner` (single-source) but **not** the
real drain path.

**3a. `engine_runner.py` `build_dot` (176-244):** add, mirroring the init/ask pattern:
```python
dot = dot.replace('llm_provider="anthropic"', f'llm_provider="{policy.provider}"')
dot = dot.replace('llm_model="claude-sonnet-4-6"', f'llm_model="{policy.model_for("default")}"')
```
(covers `run_inner`). Update the now-stale comment at 241-243.

**3b. `engine_runner.py` `run_ingest` (1080-1108):** materialize a **substituted** copy of
`synthesize.dot` into `logs_dir` and point `$synthesize_dot` at the copy:
```python
policy = load_policy(wiki_dir)
synth = INNER_DOT.read_text(encoding="utf-8")
synth = synth.replace('llm_provider="anthropic"', f'llm_provider="{policy.provider}"')
synth = synth.replace('llm_model="claude-sonnet-4-6"', f'llm_model="{policy.model_for("default")}"')
synth_path = logs_dir / "synthesize.dot"
synth_path.write_text(synth, encoding="utf-8")
synthesize_dot_abs = str(synth_path)   # replaces str(INNER_DOT)
```
(Default policy → byte-identical `anthropic`/`claude-sonnet-4-6`, so no behavior change for the
default user.)

**Acceptance criteria (Task 3):**
- `grep -n 'llm_provider' pipeline/synthesize.dot` still shows the default `anthropic` (the file
  stays a valid standalone DOT); substitution happens at materialization, not in the tracked file.
- `WIKI_WEAVER_PROVIDER=openai WIKI_WEAVER_MODEL=gpt-4o wiki-weaver ingest …` → the run's
  `logs_dir/synthesize.dot` contains `llm_provider="openai"` / `llm_model="gpt-4o"` on all three
  LLM nodes, and the spawned child agents route to the openai profile.
- Default run (no env) → `logs_dir/synthesize.dot` is byte-identical to the tracked default.

---

### Task 4 — Provider registry + pinned extras + `provider` subcommands + global config

**Goal:** the UX in design §7 (`provider list/install/config`), read-only discovery, atomic pinned
extras, env-first secrets.

**4a. New `wiki_weaver/providers.py` — the thin static registry.**
```python
@dataclass(frozen=True)
class ProviderSpec:
    name: str               # "anthropic" | "openai" | "chat-completions" | "github-copilot"
    extra: str              # pip extra name, e.g. "openai"
    package: str            # distribution name for entry-point/version lookup
    profile: str            # attractor profile / llm_provider value
    default_model: str
    env_keys: tuple[str, ...]      # e.g. ("OPENAI_API_KEY",)
    config_keys: tuple[str, ...]   # e.g. ("model", "base_url")

REGISTRY: dict[str, ProviderSpec] = { ... }   # the 4 supported providers

def installed_providers() -> list[str]:
    """Read-only entry-point discovery of installed amplifier-module-provider-* pkgs."""
def available_providers() -> list[str]:
    """REGISTRY keys not currently installed."""
def coherence(name: str) -> tuple[bool, str]:
    """Installed package version vs pins.MODULE_PINS commit's declared version."""
```
Discovery is **read-only** (entry points + `importlib.metadata`); no registry writes.

**4b. `pyproject.toml` — base deps + pinned extras.**
- Base `dependencies`: keep `amplifier-foundation` + `amplifier-unified-llm-client`, **pin both to
  commits** (drop `@main`), and add **`provider-anthropic` pinned** (default provider present
  out-of-box, so `provider list` discovers it and the coherence check has a baseline).
- Add:
  ```toml
  [project.optional-dependencies]
  openai            = ["amplifier-module-provider-openai @ git+https://github.com/microsoft/amplifier-module-provider-openai@<pin>"]
  chat-completions  = ["amplifier-module-provider-chat-completions @ git+...@<pin>"]
  github-copilot    = ["amplifier-module-provider-github-copilot @ git+...@<pin>"]
  ```
- **Note (ties to Unknown 2):** extras put the provider *package* in the venv so `provider list`
  (entry points) and the coherence check work, and so the activator's pip-dep step is satisfied
  (skip-if-importable). The engine still resolves provider *code* via the pinned `source_resolver` —
  the extra version and the pinned commit MUST agree (the coherence assert enforces this). Keep the
  extra pin and `pins.MODULE_PINS` for the same repo **in lockstep** (one PR moves both).

**4c. New `wiki_weaver/config.py` — global 3-tier config + secrets.**
- Loads `~/.config/wiki-weaver/config.toml` (`[providers].default`, `[providers.<name>]` blocks)
  and `~/.config/wiki-weaver/secrets.toml` (mode-asserted `0600`; warn+ignore if looser).
- Resolution helpers: `resolve_provider(flag)` →
  `flag › WIKI_WEAVER_PROVIDER › config.toml [providers].default › "anthropic"`.
  Secrets: `env › secrets.toml › None`.
- `policy.load_policy` (`policy.py:104-115`) gains a fallback to `config.py` for `provider`/`models`
  **below** the per-wiki `wiki.config.yaml` layer (per-wiki still wins), so the global default and
  per-wiki override compose. Built-in default stays `anthropic` (`policy.py:42`).

**4d. `wiki_weaver.py` — `provider` subcommand group** (argparse, mirrors existing dispatch):
- `provider list` → `installed_providers()` (★active/default marked) + `available_providers()`.
- `provider install <name>` → run `uv tool install --upgrade "wiki-weaver[<extra>]"` (atomic
  extras, **not** `--with`), then `engine_runner.preflight_cache(warm=True)` for that provider's
  pinned source, then `coherence()` → print ✓ / fail loud.
- `provider config <name>` → inspect-and-edit of `config.toml [providers.<name>]` (design §7.3,
  chosen UX).
- `provider login <name>` → **github-copilot only**; device-OAuth (design §7.5). May ship as a
  stub that errors "not yet implemented" if it risks the gate — gate does not require copilot.

**Acceptance criteria (Task 4 = design's Phase 1 provider gate):**
- `wiki-weaver provider list` on a fresh install shows `anthropic` installed/active, the other
  three available.
- `wiki-weaver provider install openai` → atomic extras upgrade, warms the pinned openai source,
  `doctor` reports `openai <ver> ✓` **coherent** with the pinned engine commit.
- One **end-to-end** wiki build through the non-default provider
  (`wiki-weaver ingest --provider openai …` or `WIKI_WEAVER_PROVIDER=openai`) converges and writes
  a real wiki.
- `doctor` shows a **single coherent version per provider** + the pinned engine-bundle commit.
- Secrets: `OPENAI_API_KEY` (env) overrides `secrets.toml`; `secrets.toml` not `0600` → warned.

---

## 2. Verification gates (run in order; do not advance on a red gate)

1. **After Task 1 — Pinned bootstrap gate (re-run Phase 0 on a bare DTU).** Clean box + `uv tool
   install` + one key + `ingest` → resolves the **same pinned set deterministically** (two cold
   runs → identical resolved SHAs) and writes a real wiki. *This re-runs the design's Phase 0 gate
   against the pinned set — the keystone proof.*
2. **After Task 2 — Verify-only gate.** `doctor --bootstrap` then **offline** `ingest` succeeds;
   deleting a cached pinned module makes `ingest` **fail loud** (no silent refetch).
3. **After Task 3 — Lift gate.** `WIKI_WEAVER_PROVIDER`/`MODEL` override appears in the run's
   materialized `synthesize.dot` and routes the spawn; default run byte-identical.
4. **After Task 4 — Provider e2e gate (design §8).** Real `provider install <non-default>` → one
   build end-to-end through it → `doctor` shows coherent versions + pinned engine commit.

---

## 3. Cross-repo flags (NOT hidden — call out before building)

- **None required for Phase 1.** All pinning is achievable inside wiki-weaver via `source_resolver`
  + `pins.py`.
- **Optional / later (cleaner determinism):** tag `amplifier-bundle-attractor` and pin its
  *internal* `source:` refs (currently all `@main` in `attractor-pipeline.yaml`,
  `behaviors/attractor-core.yaml`, `agents/attractor-agent-*.yaml`). Then wiki-weaver pins to the
  tag and the `source_resolver` shrinks to just the bundle ref. Tracked as a follow-up; do not
  block Phase 1.
- **Parked (true bake-into-venv, design Alt B / foundation change):** vendor the pinned module set
  into the wheel, OR a foundation change to resolve modules from installed packages / entry points.
  Either removes the cache-resolve entirely. Out of Phase 1 scope.
- **Pin discovery:** the `<pin>` SHAs in `pins.py` / `pyproject.toml` extras must be filled from the
  Phase-0-proven set (`amplifier-bundle-attractor 5ae3118`, `amplifier-foundation 70a84d0`) plus a
  one-time `git ls-remote @main` capture of the other module repos at a coherent moment. Capture all
  in **one** PR so the set is internally consistent.

---

## 4. Risk & sequencing notes

- **Ship-incrementally-safe:** Task 1 (pin) and Task 3 (lift) are independently shippable and
  behavior-preserving for the default user (default pins = proven good; default lift = byte-identical
  `anthropic`/sonnet).
- **Coupled:** Task 2 (verify-only) depends on Task 1 (needs deterministic pinned sources to verify
  against). Task 4's coherence check depends on Task 1's `pins.py` and Task 2's preflight. **Keep
  the extra pin and `MODULE_PINS` for the same repo in lockstep** — they are the "two sources of
  truth for a version" the council warned about; one PR moves both, and the coherence assert is the
  fail-loud backstop.
- **Biggest sharp edge:** Task 3's ingest-drain path (folder sub-pipeline reads `synthesize.dot`
  from disk, bypassing `build_dot`). Materialize-into-logs_dir is the fix; verify the gate checks
  the **run's** `synthesize.dot`, not the tracked file.
- **Don't regress the local-dev escape hatches** (`WIKI_WEAVER_ATTRACTOR_PIPELINE`,
  `WIKI_WEAVER_INSTALL_DEPS`): keep them, but ensure the pinned `source_resolver` is the default
  path when they're unset.
