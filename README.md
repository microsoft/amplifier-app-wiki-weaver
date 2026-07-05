# Wiki-Weaver

Compile a structured, interlinked **markdown wiki** from a pile of source material, feed it
more content over time, and **read answers from the compiled wiki instead of RAG**. This is
the Karpathy "LLM wiki" / second-brain *compounding memory* pattern, made real and repeatable.

Agents do the judgment work (schema design, synthesis, quality assessment, remediation); code
owns control flow, structural validation, archiving, and deduplication. Each source is folded
into the growing wiki one at a time, and is only archived once it **genuinely converges** —
structurally valid and quality-assessed — with a provenance entry recording where every claim
came from.

> ⚠️ **Experimental exploration.** This is experimental software shared openly. See
> [SUPPORT.md](SUPPORT.md) for the (lack of) support policy.

## The idea

Instead of retrieving raw chunks at query time (RAG), wiki-weaver does the synthesis *up front*,
once, and stores the result as a navigable wiki:

- **`init`** designs a domain-fit *schema* from a plain-English description of what the wiki is
  for — the page types, frontmatter, and conventions tailored to your outcomes.
- **`ingest`** folds source files into the wiki one at a time, weaving overlapping content
  together, accruing provenance, and surfacing contradictions rather than averaging them.
- **`ask`** answers questions by *reading the compiled wiki* — no embeddings, no chunk
  retrieval. It cites the pages it used and refuses loudly when the wiki doesn't cover a topic.
- **`lint`** structurally validates the wiki (links, orphans, frontmatter, provenance).

The compiled wiki is just a folder of interlinked markdown — portable, diffable, and readable
without any tooling.

## Quick start

Install the `wiki-weaver` command with one line (requires an [Amplifier](https://github.com/microsoft/amplifier)
installation — see [Requirements](#requirements)):

```bash
uv tool install git+https://github.com/microsoft/amplifier-app-wiki-weaver
```

`wiki-weaver` is a **companion** to an installed Amplifier: it tracks `@main` of the Amplifier
runtime libraries so it stays in lockstep with your ecosystem, and it uses your Amplifier
install at runtime for provider keys and the engine bundle cache. (Equivalently, run it from a
clone as `python -m wiki_weaver <command>`.)

**Strategy: track `@main`, fix-forward — no version pinning.** wiki-weaver intentionally does
not commit a `uv.lock` (see `.gitignore`): the lock would silently freeze runtime deps at stale
commits for every `uv sync` user, which defeats the point of tracking `@main`.

### Keeping wiki-weaver current

Use the built-in `update` command — it's the canonical way to refresh both layers:

```bash
# Check for drift (ls-remote each @main source; no changes made):
wiki-weaver update --check

# Apply updates:
#   Layer 1 — reinstall wiki-weaver + wheel deps (amplifier-foundation, unified-llm-client)
#   Layer 2 — re-clone engine bundles in ~/.amplifier/cache/bundles
wiki-weaver update

# Confirm what you're running after update:
wiki-weaver doctor
```

`update` verifies that packages actually moved to the new remote commit after reinstall.
If uv served a stale cache, it escalates through a ladder (`--no-cache`, then
`uv cache clean`) and exits non-zero with a diagnostic if the package still didn't update —
so you're never silently left running stale code.

`doctor` now also prints the resolved `@main` commits for all sources (no network needed) so
you always have a "what am I actually running" record without a committed lock file.

Every command runs a fast preflight first and **fails loud and clean** — with a clear message,
no traceback — if the environment is missing a prerequisite (runtime, provider key, or the
import-name regression check). Run `doctor` any time for the full report:

```bash
# 0. Preflight — verifies the runtime, provider key, network, and validator are all present.
wiki-weaver doctor

# 1. Create a wiki and design a schema for YOUR purpose (one LLM call, ~30–60s).
#    The --purpose text should describe the intended use and the outcomes you want.
wiki-weaver init mywiki \
  --purpose "A personal research second-brain on distributed systems: answer 'which
  approach fits problem X', compare trade-offs between techniques, and track how my
  conclusions evolve as I read more."

# 2. Drop source material (markdown/plain text) into the wiki's inbox.
cp ~/notes/*.md mywiki/_inbox/

# 3. Weave the sources in. Re-run any time you add more to _inbox/ — it picks up where
#    it left off (already-ingested sources are deduped by content hash).
wiki-weaver ingest --wiki mywiki --max-cycles 5

# 4. Ask questions — answered by reading the compiled wiki, with citations.
wiki-weaver ask "what are the trade-offs between leader-based and leaderless replication?" \
  --wiki mywiki

# 5. Structurally validate the wiki (exit 0 = PASS).
wiki-weaver lint --wiki mywiki
```

Prefer a generic, no-LLM scaffold? `wiki-weaver init mywiki --plain` skips schema design and
uses the built-in generic schema (free, instant). You can always `ingest`/`ask`/`lint` the same
way afterward.

### Commands

| Command | What it does |
|---|---|
| `init <dir> --purpose "..."` | Scaffold a wiki and design a domain-fit schema (`<dir>/.wiki/policy/schema.md`) from the stated purpose (and a sample of any staged `_inbox/` sources). `--plain` = generic scaffold, no LLM. `--no-sample-inbox` = design from `--purpose` alone. |
| `ingest --wiki <dir>` | Drain `<dir>/_inbox/` into the wiki, synthesizing/updating pages and moving each source to `<dir>/_sources/` on convergence. Flags: `--source <file>` (one file), `--max-cycles N`, `--keep-going`. |
| `ask "<question>" --wiki <dir>` | Answer a question by reading the compiled wiki; cites pages used and refuses when the topic is absent. `--json` for structured output. |
| `lint --wiki <dir>` | Run the structural validator (links, orphans, frontmatter, provenance). Exit 0 = PASS. |
| `doctor [--wiki <dir>]` | Environment + (optional) wiki-structure diagnostics. Also prints resolved `@main` commits for all sources (your lock-file replacement). |
| `update [--check]` | Refresh wiki-weaver to latest `@main`: reinstall the tool (Layer 1) and re-clone engine bundles (Layer 2). `--check`/`--dry-run` = report drift only, no changes. |
| `build-dashboard <corpus> --out <file.html>` | Render a compiled wiki into one self-contained HTML dashboard (no LLM, no runtime). Flags: `--theme <file>`, `--group-by <field>` (default `type`), `--skip-index`. See [Dashboard](#dashboard). |
| `migrate <corpus>` | Relocate an existing corpus to the new hidden-`.wiki/` layout (machine state under `.wiki/`, `_archive/` renamed to `_sources/`). Deterministic, no LLM. Flags: `--dry-run` (print the plan, change nothing), `--force` (re-run past the completion sentinel). See [Migrating an existing corpus](#migrating-an-existing-corpus). |

`ask` is the single answer interface for a wiki — there is no separate `query` command.

## Corpus structure

A wiki corpus separates **human-facing content** (lives at the corpus root) from **machine-only
bookkeeping** (lives under a hidden `.wiki/` subtree). The compiled wiki pages and the source
material stay browsable; the operational state stays out of the way.

```
mywiki/
├── index.md, overview.md, *.md   the compiled wiki pages (human-visible)
├── _inbox/                       drop zone — stage new source material here (human-visible)
├── _sources/                     source digests, one per ingested source (human-visible; was _archive/)
├── wiki.config.yaml              optional per-wiki knobs (human-visible)
├── .obsidian/                    seeded Obsidian vault config (so the folder opens cleanly)
└── .wiki/                        machine-only subtree (hidden) — never hand-edit
    ├── .processed.jsonl          the ingest ledger
    ├── .sources.json             the source registry
    ├── policy/                   schema.md + any project policy overrides
    ├── failed/                   sources that did not converge
    ├── runs/                     per-run engine logs
    ├── index/                    backlinks/links/tags/properties/aliases JSON
    └── dashboard/                theme.json + custom.css for build-dashboard
```

Because `.wiki/` is a **dotfolder**, Obsidian (and most tooling) hides it automatically — it never
shows up as phantom notes in the vault graph. `init` and `migrate` also seed a corpus `.gitignore`
(so the machine state isn't committed as content) and a `.obsidian/` config, so the folder is
ready to open as a vault.

### Migrating an existing corpus

Corpora created before the `.wiki/` layout keep their bookkeeping at the corpus root
(`_archive/`, `.processed.jsonl`, `.runs/`, …). The `migrate` command relocates them in place:

```bash
wiki-weaver migrate mywiki --dry-run   # preview the plan, change nothing
wiki-weaver migrate mywiki             # perform the migration
```

It is **idempotent** and **copy-before-delete**: every file is copied and verified before
anything is removed, and the run records a completion sentinel so a second run is a no-op.

**Pre-run checklist** (from an adversarial safety review — do not skip these):

- **Back up `_sources/` first.** Migration is not a backup; the source digests are irreplaceable.
- **Stop everything touching the corpus** — close Obsidian and any running
  `wiki-weaver`/`repo-weaver` process. Migration locks against other migrations but **not**
  against a concurrent `ingest`/`weave`.
- **Do NOT run `weave`/`ingest` between upgrading and migrating.** Post-upgrade runs write to the
  new `.wiki/runs/` and can block the migration's verify step.
- **If the corpus is in a git repo,** expect a large rename/delete in `git status` — commit or
  stash first.
- Migration **safely aborts** (touching nothing) if the corpus's ledger paths don't match its
  location — a sign the corpus was moved. If you see that abort, **don't `--force`**; investigate.

## Dashboard

`build-dashboard` renders a compiled wiki into a **single self-contained HTML file** — a navigable
reading view of the whole corpus with no server and no build step. It is **deterministic**: no LLM
and no Amplifier runtime, so it runs anywhere Python does.

```bash
wiki-weaver build-dashboard mywiki --out mywiki.html
```

The output is one portable file with everything inlined — **open it directly in a browser, or drop it
(or the corpus folder) into Obsidian**; `[[wikilinks]]` resolve either way. You get a sidebar grouped
by frontmatter `type` (collapsed by default, pages A–Z by title), per-page type badges and dates, a
reading view with a heading "ledger rail", and a **"Linked from"** backlinks panel.

Under the hood it first builds the corpus indexes at `<corpus>/.wiki/index/` (backlinks, links, tags,
properties, aliases), then renders. Flags:

| Flag | Effect |
|---|---|
| `--out <file.html>` | Destination HTML file (required). |
| `--theme <file>` | Path to a `theme.json` (overrides the corpus's own `.wiki/dashboard/theme.json`). |
| `--group-by <field>` | Frontmatter field to group the sidebar nav by (default: `type`). |
| `--skip-index` | Reuse the existing `.wiki/index/` files instead of rebuilding them. |

### Theming

The default look is **Almanac**: a warm-paper light theme plus an "Almanac Night" dark variant that
switch automatically with the OS setting (`prefers-color-scheme` — there is no in-page toggle).

To restyle without touching code, drop a `theme.json` in the corpus at `.wiki/dashboard/theme.json`
(or point at one with `--theme`). It is a **flat JSON object of per-token overrides** keyed by the
dashboard's `--wiki-*` CSS custom properties — colors (`--wiki-bg`, `--wiki-accent`, …), typography
(`--wiki-font-reading`, `--wiki-font-size`, `--wiki-line-height`, …), and shape (`--wiki-radius`,
`--wiki-space-unit`, …). It also accepts an optional `title` string for the dashboard heading. Unknown
tokens warn and are ignored; overrides whose contrast falls below WCAG AA warn loudly (they are not
silently "corrected"). Keys prefixed with `_` are treated as private metadata.

```json
{
  "title": "My Knowledge Base",
  "--wiki-accent": "#3B5BDB",
  "--wiki-bg": "#F4F6FA",
  "--wiki-font-reading": "system-ui, sans-serif",
  "--wiki-radius": "4px"
}
```

For arbitrary CSS beyond the token set, add `.wiki/dashboard/custom.css` — it is appended **verbatim**
(trusted: it's the wiki owner's own file, so it is not contrast-checked).

> Theming is **tokens + an optional title only** today — there is no logo or richer branding support yet.

## Three ways to use it

**1. CLI (primary surface).** The commands above, via the installed `wiki-weaver <command>` console
script (or `python -m wiki_weaver <command>` from a clone). This is the supported, end-to-end path.

**2. As a library.** The commands are thin wrappers over importable functions in
`wiki_weaver.engine_runner`. They use the Amplifier runtime at execution time (see Requirements).

```python
from wiki_weaver.engine_runner import run_init, run_ingest, run_ask, run_lint

run_init("mywiki", purpose="A research second-brain on distributed systems …")
# ... drop sources into mywiki/_inbox/ ...
run_ingest("mywiki", max_cycles=5)             # returns an InnerResult
result = run_ask("mywiki", "leader vs leaderless replication?")  # returns an AskResult
print(result.answer, result.pages_used, result.refused)
run_lint("mywiki")                             # returns an int exit code (0 = PASS)
```

(The richer per-source / keep-going ingest options exposed on the CLI live on
`cli.lib.ingest`; `run_ingest` runs the full inbox drain.)

**3. As `.dot` pipelines.** Each command is an attractor `.dot` pipeline under
[`pipeline/`](pipeline/): `init.dot`, `ingest.dot` (which invokes `synthesize.dot`),
`ask.dot`, and `lint.dot`. **These are not yet drop-in standalone.** They are `$token`
templates that the Python wrapper (`engine_runner.build_*_from_file()`) fills with concrete
paths/prompts before handing them to the engine. Today you run them through the CLI or library
above — they document the pipeline shape, not a portable artifact you can run by hand.

## Sharing / publishing your wiki

There is no `publish` command, by design — the compiled wiki **is** the deliverable: a folder
of interlinked markdown with a navigable `index.md` and `overview.md`. To share it:

- **Git:** commit the wiki directory to its own repo and push it.
- **Obsidian / any markdown editor:** open the wiki folder directly — `[[wikilinks]]` resolve.
- **Static site:** point a static-site generator (MkDocs, Hugo, etc.) at the folder.

(`_inbox/` and `_sources/`, plus the hidden `.wiki/` subtree — ledger, registry, `failed/`,
`runs/`, `index/`, `policy/`, `dashboard/` — are operational state, not published content. See
[Corpus structure](#corpus-structure).)

## Architecture

Code-first, with agents where they earn their keep:

```
ingest (agent) → validate (code) → assess (agent) → check (code) → done
                                        │
                                        └── feedback (agent) → loop
```

The validator's exact failures are plumbed into the feedback/refine instructions, so
remediation targets the real problem. Each node runs as an isolated session with a thin,
focused context — `ingest` reads only a *slice* (the touched page plus any broken/orphan
pages), not the whole corpus, so the pipeline **scales** as the wiki grows. The pipeline is
driven by the [attractor engine](https://github.com/microsoft/amplifier-bundle-attractor)
(a DOT-graph pipeline orchestrator).

Full design: [`docs/`](docs/).

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `WIKI_WEAVER_MODEL` | Model (or family) for LLM pipeline/`init` nodes | `sonnet` |
| `WIKI_WEAVER_PROVIDER` | Provider the LLM nodes route to | `anthropic` |

`WIKI_WEAVER_MODEL` accepts a **bare family token** (`sonnet`, `haiku`, `opus`) or an explicit
model id.  Family tokens resolve at runtime to the newest model the provider actually serves in
that family — no pinned version to maintain.  An explicit id (e.g. `claude-opus-4-8`) passes
through unchanged.  Internally, `feedback` nodes use `haiku` (fast, cheap) while the heavier
synthesis nodes (`ingest`, `assess`, `init`, `ask`) use `sonnet`; both can be overridden per-wiki
in `wiki.config.yaml` under `models:`.  If the family can't be resolved (network error, no match)
wiki-weaver raises a loud error — it never silently falls back to a stale hardcoded id.

## Requirements

- Python 3.11+
- An [Amplifier](https://github.com/microsoft/amplifier) installation. `uv tool install` resolves
  the `amplifier_foundation` and `unified_llm` libraries (tracked at `@main`) into wiki-weaver's
  own tool venv, so the CLI imports cleanly — but the engine still uses your Amplifier install at
  **runtime** for provider keys and the cached engine bundle it loads on first `ingest`. wiki-weaver
  is a companion to that runtime, not a self-contained replacement for it.
- `ANTHROPIC_API_KEY` (or the relevant provider key) set in the environment.

`wiki-weaver doctor` verifies all of the above, and every command runs the same checks as a
preflight — so a missing prerequisite fails fast with a clear message, never a mid-run traceback.

## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
