# Using wiki-weaver — the LLM Wiki pattern, automated

This bundle is an instantiation of the **Karpathy "LLM wiki" pattern**: instead of
re-deriving answers from a raw source pile on every query (RAG), you compile sources
once into a structured, cross-linked markdown wiki and *keep it current*, then answer
questions by **reading the compiled wiki**. The *why* lives in Karpathy's idea
([gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f); vendored at
`docs/llm-wiki-pattern.md`). This file is the *how* for these tools — when to reach for
each — and does not restate the pattern.

The one-line value: the wiki is a **persistent, compounding artifact**. Each ingested
source is integrated into status-tracked, interlinked pages; every later question reads
the compiled wiki, not the raw pile. The maintenance cost that makes humans abandon
wikis is paid by the pipeline instead.

## The four tools

| Tool | What it does | Cost / shape |
|---|---|---|
| `wiki_weaver_init` | Scaffold a wiki and (unless `plain=true`) LLM-design a domain-fit schema from a stated `purpose` — page types, frontmatter contract, conventions → `<wiki>/policy/schema.md`. | One LLM call (cheap). `plain=true` is free (deterministic scaffold only). |
| `wiki_weaver_ingest` | Drain `<wiki>/_inbox/` into the wiki: mine each source, write/update cross-referenced pages, reconcile dups/orphans, verify — looping until each source converges. | **LONG-RUNNING & LLM-heavy** (minutes per source). Place sources in `_inbox/` first. |
| `wiki_weaver_ask` | Read-only, **index-first** Q&A with citations. Navigates `index.md` + `[[wikilinks]]`, synthesizes a cited answer, and **refuses loudly** when the topic is absent. Structurally barred from writing/shelling/web. | One LLM call. Never mutates the wiki. |
| `wiki_weaver_lint` | Deterministic structural validation (frontmatter, type taxonomy, link integrity, orphans) via the same validator the ingest pipeline uses. Returns the full report + PASS/FAIL. | No LLM. Fast. Read-only. |

## When to use which

- Starting a new knowledge base → `wiki_weaver_init` with a clear `purpose`, then drop
  sources into `_inbox/` and `wiki_weaver_ingest`.
- A wiki already exists and you have a question → `wiki_weaver_ask` (NOT a grep/RAG step;
  it reads the compiled wiki and cites pages).
- Checking a wiki's structural health before/after ingest → `wiki_weaver_lint`.

## The compounding habit (the point)

`wiki_weaver_ask` is read-only by design, so a good synthesis is, by default, a one-off.
When an answer is worth keeping, file it back: drop it into `_inbox/` and `wiki_weaver_ingest`
it — now it's a first-class wiki page, cross-referenced and maintained on every future
ingest. Your *questions* enrich the wiki, not just your sources. This is operator/agent
habit, not an automatic step — the "is this worth keeping?" judgment is yours.

## Runtime note

These tools wrap the wiki-weaver engine, which drives the attractor pipeline on the
Amplifier runtime. `ingest` and `ask` spawn provider-backed sub-sessions, so a provider
must be configured. The same operations are also available as the `wiki-weaver` CLI
(`python -m wiki_weaver --help`) for terminal-driven use — one engine, two front doors.
