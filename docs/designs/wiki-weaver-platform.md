# wiki-weaver: Standalone Usability + Multi-Provider Design

> **Status:** Validated via design conversation + 6-lens council review (conditional-approve; the engine self-install seam fix is incorporated as a hard requirement). Phase 0 and Phase 1 are the active scope; Phase 2 and Phase 3 are documented but parked.
>
> **Date:** 2026-06-23

---

## 1. Goal / Intent (pinned)

Make `wiki-weaver` usable by someone who has **not** installed or set up `amplifier-app-cli`.

**Target experience:** a clean machine + one API key + `uv tool install git+…` → it just works.

```console
$ uv tool install git+https://github.com/microsoft/amplifier-bundle-wiki-weaver
$ export ANTHROPIC_API_KEY=sk-ant-...
$ wiki-weaver ingest --wiki my-wiki ./docs   # self-bootstraps end-to-end
```

**AmplifierSession and the Amplifier ecosystem stay under the hood.** This is explicitly **not** about removing the Amplifier engine — the attractor engine, `AmplifierSession`, `load_bundle`, and the DOT pipelines remain the machinery. The only thing being removed is the dependency on a *separately-installed* `amplifier-app-cli`.

The council pinned this as **intent (i): "lower install friction."** A competing framing — **"no Amplifier lineage,"** i.e. forking the engine out into a standalone runner — was **rejected** as a means masquerading as the goal. The user wants the ecosystem under the hood; lineage stays.

---

## 2. Architecture: bundle (capability) vs app (product)

The system splits along the classic Amplifier seam: a **bundle is configuration**; daemons, HTTP servers, web UIs, auth, and process lifecycle are **application** concerns.

| | `amplifier-bundle-wiki-weaver` | `amplifier-app-wiki-weaver` (future) |
|---|---|---|
| **Role** | The reusable **capability** | The **product / delivery vehicle** |
| **Owns** | DOT pipelines, ingest/query/lint/ask logic, `AmplifierSession` wiring, the CLI (incl. `doctor`) | Watch-daemon, web UI, LAN service API, service install, config/state lifecycle |
| **Relationship** | Consumed by app-cli and others | Embeds `AmplifierSession`, **consumes** the bundle |
| **Status** | Home of Phase 0/1 work — **active** | **Parked (Phase 3)** — document the boundary, not the build |

- The bundle stays the home of all Phase 0/1 work, and **keeps a `doctor` command in its CLI** (useful independent of any app shell).
- The app is the right home for everything long-running and surface-bearing. We document its boundary now and build it later.

**Rationale:** A bundle should remain pure configuration + capability so any consumer (`amplifier-app-cli`, the future app, automation) can pull it. The moment we add a long-running daemon, an HTTP server, a web UI, or authentication, we are building an *application* — and that belongs in the app, not the bundle.

---

## 3. Phased plan

### Phase 0 — Prove the bootstrap (acceptance gate, NOT yet done)

Prove on a **genuinely bare DTU** — no host Amplifier venv, no `~/.amplifier` — that:

```
uv tool install git+…wiki-weaver  +  one API key  +  wiki ingest
```

self-bootstraps `AmplifierSession` **end-to-end**.

This is **unproven and load-bearing**: every prior DTU this session pre-seeded the runtime (host venv, warm cache, or hand-patched bundles), so the clean-machine path has never actually been exercised. Phase 0 is an *acceptance gate*, not a coding task — nothing downstream is trustworthy until it passes.

### Phase 1 — Harden the seams + provider registry (the active build)

1. **PIN the engine.** Pin the attractor engine bundle, its modules, and the context-intelligence hook to known-good commits. This kills `@main` drift and the known `unified_llm` → `llm` import-name skew. **(PLANNED — Phase 1 work; see the two-axes note below.)**
2. **LIFT provider/model selection out of the DOTs** into wiki-weaver config. **Model-id selection is now SHIPPED as live family resolution** (attractor PR #68 + wiki-weaver PR #6): family tokens (`sonnet`/`opus`/`haiku`) resolve at runtime to the **newest stable model the provider actually serves** via the upstream `unified_llm` resolver — there is **no model-id to pin or maintain**, and the listing adapter is the generating adapter so a resolved id can't 404. *Provider* selection still substitutes into the DOTs. **Substitution into `synthesize.dot` is DONE on both paths.** `build_dot` substitutes per-node on the CLI path, and `run_ingest` (the tool-module drain path) now materializes a model-substituted `synthesize.dot` into `logs_dir` via the shared `_substitute_models` helper rather than reading the package original — so the drain path resolves per-stage too. Verified live: `WIKI_WEAVER_MODEL=opus` → ingest/assess nodes `claude-opus-4-8`, feedback node `claude-haiku-4-5-…` (its per-stage default), zero residual `claude-sonnet-4-6`.
3. **The provider system** — see [Section 4](#4-provider-system-phase-1-core--council-approved-with-the-required-seam-fix). **(PLANNED — Phase 1 work.)**

> **Two independent version concerns — do not conflate them.** wiki-weaver has two separate "what version?" axes:
> - **(a) Model-id selection** — *which LLM we call within a family* — is **live family resolution, SHIPPED** (attractor PR #68 `150de03` + wiki-weaver PR #6 `5acd7de`). Family tokens resolve at runtime to the newest stable served model; explicit ids pass through unchanged. No pinning, no static catalog. Pre-1.0 the wiki-weaver layer is anthropic-only-guarded; the upstream resolver already supports anthropic/openai/gemini.
> - **(b) Engine / dependency versions** — *which engine code we run* — stay **pinned and moved atomically** via `uv tool upgrade` (the "Resolution C" strategy below). This is **PLANNED (Phase 1)** and is the subject of §3 item 1, §4, and §4.6.
>
> One axis asks "which model do we call" (live, zero-pin); the other asks "which engine code do we run" (pinned, deliberate upgrades). Everything about engine/provider *pinning* in this doc is axis (b) and remains planned; only the *model-id* narrative has flipped to live resolution.

### Phase 2 — App shell + watch-dir daemon (parked)

A headless daemon that watches multiple input directories, each mapped to its own output wiki, and auto-ingests files dropped in (multi-dir → multi-output). Lives in `amplifier-app-wiki-weaver`.

### Phase 3 — Web UI + LAN service API (parked)

- Web UI: drag-and-drop ingest, browse the wiki, chat-over-wiki via `AmplifierSession`.
- An **authorized LAN service API** so `amplifier-app-cli` (or other tool/bundle-aware clients) can issue queries and actions from authorized clients on the LAN.

---

## 4. Provider system (Phase 1 core) — council-approved WITH the required seam fix

Supported providers (existing `amplifier-module-provider-*` packages):

- `anthropic`
- `openai`
- `chat-completions`
- `github-copilot`

### 4.1 Providers are pinned extras, not floating `--with`

Providers are **pinned packages in the uv-tool venv, installed as pinned extras** (`wiki-weaver[openai]`), **not** via `uv tool --with`.

- Extras **resolve together as one coherent atomic set** — the whole environment moves to a consistent state.
- `--with` invites **independent drift** between the CLI, the engine, and the provider — the exact failure class this design exists to prevent.

The UX command `wiki-weaver provider install openai` wraps the extras upgrade.

### 4.2 Discovery is read-only

Discovery uses **read-only Python entry points**. `wiki-weaver provider list` shows installed vs. available providers. No mutation, no runtime registry writes. *(plugin-discovery-patterns.)*

### 4.3 Default provider

- **Default shipped provider = `anthropic`** — works out-of-box with one key and strands no one.
- `chat-completions` is the **first recommended install** for local / OpenAI-compatible setups.

### 4.4 Runtime selection

Selected provider is resolved by precedence, then **substituted into all DOTs**:

```
--provider flag  ›  WIKI_WEAVER_PROVIDER env  ›  config.toml [providers].default  ›  built-in default (anthropic)
```

### 4.5 Per-provider config — a thin static registry

Per-provider config is a **thin static registry**, deliberately **not** runtime schema-introspection of Amplifier's `ConfigField`. Introspection is YAGNI here and would couple wiki-weaver to engine internals.

**3-tier resolution:**

```
env vars  ›  namespaced [providers.<name>] blocks in ~/.config/wiki-weaver/config.toml  ›  defaults
```

**Secrets** (API keys / tokens) are **env-first**, or in a separate `~/.config/wiki-weaver/secrets.toml` (`chmod 0600`) — **never** in tracked config/state.

`doctor` validates that the **selected** provider's required config is present and that its endpoint is reachable.

### 4.6 Updates

- `uv tool upgrade wiki-weaver` moves the **whole environment atomically** — CLI + provider extras + engine modules, all pinned, together. **Never piecemeal.**
- Providers are pinned to **compatible version ranges, never `@main`**.
- `doctor` reports a **single coherent version per provider** plus the **pinned engine-bundle commit**.
- Optional `wiki-weaver self update` wraps the upgrade + post-upgrade verification.

---

## 5. The critical fix — engine self-install seam (council FAIL → resolved)

The anti-disaster claim rests on *"everything is a pinned package in the venv."* But the attractor engine's `prepare(install_deps=True)` **currently pip-installs the engine's own modules into the venv at runtime on first run**. That is a **second source of truth** for provider versions — it breaks under concurrency and offline, and can silently override the extras pin. The council ruled this a **FAIL**: a "one-time" runtime mutation is still the runtime mutation the goal forbids.

**Required resolution (hard requirement, not optional):**

1. **Bake the engine's modules in at install time.** Resolve and pin `loop-pipeline`, the provider modules, and tools into the venv as part of install / extras, so the venv is **complete before first run**.
2. **Make `prepare(install_deps=True)` a verify-only, fail-loud no-op.** Assert presence of every required module; if anything is missing, **fail loud** (`run wiki-weaver self update`) — **never** silently pip at runtime.
3. **Assert version coherence** between the extras-installed provider and any engine-declared provider requirement. **Fail loud on skew.** One source of truth per provider version.

---

## 6. Anti-disaster principle (the explicit contract)

State it plainly, because it is the load-bearing invariant:

> Everything wiki-weaver runs on is a **pinned package** in an **isolated, per-install uv-tool venv**. Discovery is **read-only** (entry points). Updates are **explicit and atomic** via `uv tool upgrade` — the whole environment moves together to a known state.

Explicitly **NOT** allowed:

- ❌ No mutable `~/.amplifier/cache` for code
- ❌ No editable installs
- ❌ No `@main` floating refs
- ❌ No runtime module mutation

This is the **deliberate opposite** of the `amplifier-app-cli` approach (mutable shared cache + editable installs + `@main`) — the approach this team **observed corrupt a running process this session** (a mid-run cache re-clone that broke a live eval).

---

## 7. User experience

### 7.1 First install — works out of the box with one key

```console
$ uv tool install git+https://github.com/microsoft/amplifier-bundle-wiki-weaver
  Installed wiki-weaver 0.3.0
  (engine baked in at install — loop-pipeline, provider-anthropic, tools — all pinned)

$ export ANTHROPIC_API_KEY=sk-ant-...

$ wiki-weaver doctor
  wiki-weaver 0.3.0
  engine bundle  : amplifier-bundle-attractor @ ead099b (pinned)        ✓
  providers      : anthropic 1.4.2  (default, active)                   ✓
  active model   : claude-sonnet-4-6
  ANTHROPIC_API_KEY .................................................. set ✓
  network → api.anthropic.com:443 ................................... ok  ✓
  ✅ Ready.

$ wiki-weaver init my-wiki --purpose "Knowledge base on our payments stack"
$ wiki-weaver ingest --wiki my-wiki ./docs
$ wiki-weaver ask   --wiki my-wiki "How does refund settlement work?"
```

The default (`anthropic`) strands no one: `uv tool install` + a key → working wiki.

### 7.2 Adding a provider — pinned, atomic, no runtime surprises

```console
$ wiki-weaver provider list
  installed:
    * anthropic 1.4.2   (active, default)
  available:
    openai · chat-completions · github-copilot
  → wiki-weaver provider install <name>

$ wiki-weaver provider install openai
  Upgrading the wiki-weaver environment with [openai]…
    + amplifier-module-provider-openai 2.1.0  (pinned)
  Verifying environment… engine present ✓  version coherence ✓
  ✅ 'openai' installed.  Configure: wiki-weaver provider config openai
```

Under the hood this is a pinned-extras upgrade of the whole environment — not a `--with` side-load.

### 7.3 Per-provider config — env + config.toml + secrets.toml (0600)

3-tier resolution: **env vars › `[providers.<name>]` in `config.toml` › defaults**. Secrets are env-first or in `secrets.toml` (0600), never in tracked config.

```console
$ wiki-weaver provider config openai          # inspect-and-edit
  ~/.config/wiki-weaver/config.toml  →  [providers.openai]
    model    = "gpt-4o"
    base_url = "https://api.openai.com/v1"
  Secrets resolve from (in order):
    1. env  OPENAI_API_KEY
    2. ~/.config/wiki-weaver/secrets.toml  [providers.openai].api_key   (chmod 0600)
```

```toml
# ~/.config/wiki-weaver/config.toml
[providers]
default = "openai"

[providers.openai]
model    = "gpt-4o"
base_url = "https://api.openai.com/v1"
```

```toml
# ~/.config/wiki-weaver/secrets.toml   (chmod 0600 — never tracked)
[providers.openai]
api_key = "sk-..."
```

```console
$ export OPENAI_API_KEY=sk-...   # env wins over secrets.toml and config.toml
$ wiki-weaver ask --provider openai --wiki my-wiki "Summarize the payments domain"
```

### 7.4 Local / OpenAI-compatible via chat-completions

```console
$ wiki-weaver provider install chat-completions
$ export CHAT_COMPLETIONS_BASE_URL=http://localhost:11434/v1
$ export CHAT_COMPLETIONS_MODEL=qwen2.5-coder:32b
$ export CHAT_COMPLETIONS_API_KEY=local            # often a placeholder for local servers

$ wiki-weaver doctor --provider chat-completions
  active provider : chat-completions
  endpoint        : http://localhost:11434/v1
  network → localhost:11434 ......................................... ok  ✓
  ⚠ heads-up: local/OSS models can under-converge on agentic pipelines —
    multi-step ingest/synthesize may stall or produce shallow wikis.
  ✅ Reachable.
```

The under-convergence heads-up is a **real finding** from this session, surfaced by `doctor` so the user isn't surprised when a local model produces a thin wiki.

### 7.5 github-copilot — device-OAuth login flow

```console
$ wiki-weaver provider install github-copilot
$ wiki-weaver provider login github-copilot
  To authorize, open https://github.com/login/device and enter code:  WXYZ-1234
  Waiting for authorization… ✓
  Token stored in ~/.config/wiki-weaver/secrets.toml (chmod 0600)

$ wiki-weaver ask --provider github-copilot --wiki my-wiki "Where is settlement retried?"
```

### 7.6 Upgrading — the whole env moves atomically

```console
$ uv tool upgrade wiki-weaver
  Upgrading wiki-weaver 0.3.0 → 0.4.0
    + wiki-weaver 0.4.0
    + amplifier-module-provider-anthropic 1.5.0  (pinned)
    + amplifier-module-provider-openai     2.2.0  (pinned)
    + engine bundle amplifier-bundle-attractor @ 7c41f02 (pinned)
  Whole environment moved together.

$ wiki-weaver doctor
  engine bundle : amplifier-bundle-attractor @ 7c41f02 (pinned)        ✓
  providers     : anthropic 1.5.0 ✓   openai 2.2.0 ✓   (coherent)      ✓
  ✅ Ready.
```

No provider is upgraded piecemeal; `doctor` confirms a single coherent version per provider and the pinned engine-bundle commit.

---

## 8. Acceptance gates (proof gates — design is not "done" until proven)

These are Restless-Old-Brian's gates: the design is **not done until proven end-to-end as a user would experience it.**

- **Phase 0 gate — bare-DTU bootstrap proof.** On a genuinely bare DTU (no host Amplifier venv, no `~/.amplifier`), `uv tool install git+…wiki-weaver` + one API key + `wiki ingest` self-bootstraps `AmplifierSession` end-to-end. (See [Section 3](#phase-0--prove-the-bootstrap-acceptance-gate-not-yet-done).)
- **Phase 1 provider gate.** A real `wiki-weaver provider install <non-default>`, one wiki build run **end-to-end through that provider**, and `doctor` showing a **single coherent version per provider** plus the **pinned engine-bundle commit**.

**Standing evidence this is unproven today:** the session's `qwen_swap.py` hack had to hand-edit bundle agent YAML *and* patch DOT model ids to switch models — exactly the manual surgery the provider system exists to eliminate. Until the gates above pass, the provider system is a design, not a fact.

---

## 9. Parked alternatives (with reasons)

| # | Alternative | Disposition | Reason |
|---|---|---|---|
| **B** | Vendor the entire engine into the wheel | **Parked** — defensible later | The right move for *true offline*, but pinning already buys reproducibility without the wheel-bloat and sync tax. Revisit when offline is a hard requirement. |
| **C** | Replace the engine with a standalone DOT runner (drop `amplifier_foundation`) | **Rejected** | A vanity cost: reimplements engine machinery the seam already isolates, forfeits shared-engine gains, and the user explicitly wants `AmplifierSession` under the hood. |
| **D** | Publish the Amplifier deps to PyPI for `pip install` | **Parked** — right vehicle for wider distribution | This is intent (iii) and the correct path for broad distribution, but it's an **upstream ecosystem commitment** wiki-weaver can't make alone. Don't block clean-machine usability on it. |

---

## 10. Open questions (small; owner = the human)

These are deliberately small calls, already defaulted to a chosen answer; the human can flip any of them:

- **Secrets UX:** env-first + optional 0600 `secrets.toml` *(chosen)* vs. env-only.
- **`provider config` interaction:** inspect-and-edit *(chosen, shown above)* vs. interactive prompt (interactive optional).
- **Default provider re-confirm:** `anthropic` *(chosen)* vs. `chat-completions` — flip only if the real target user is local-LLM-first.
