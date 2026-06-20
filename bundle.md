---
bundle:
  name: wiki-weaver
  version: 0.1.0
  description: >
    The Karpathy LLM-wiki pattern as composable Amplifier tools. Exposes four
    wiki-weaver commands as mountable tools: wiki_weaver_init, wiki_weaver_ingest,
    wiki_weaver_ask, wiki_weaver_lint. Each wraps the wiki-weaver engine that
    compiles a structured, interlinked markdown wiki from sources and answers
    questions by READING the compiled wiki (no RAG). Compose this bundle onto any
    bundle to add wiki automation — no separate CLI install needed.

# Thin root: only includes. The real payload (the tool-module + thin awareness
# context) lives in the behavior, which the root composes here so it is reachable.
includes:
  - bundle: wiki-weaver:behaviors/wiki-weaver
---

# wiki-weaver

@wiki-weaver:context/wiki-weaver-awareness.md
