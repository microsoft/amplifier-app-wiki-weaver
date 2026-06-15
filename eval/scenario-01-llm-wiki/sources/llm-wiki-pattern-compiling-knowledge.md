---
title: "The LLM Wiki Pattern: Compiling Knowledge Instead of Retrieving It"
author: "Jordan Patel"
source: "https://techblog.example.com/llm-wiki-compiling-knowledge"
date: "2024-03-15"
---

# The LLM Wiki Pattern: Compiling Knowledge Instead of Retrieving It

The dominant approach to giving language models access to a large body of knowledge has been retrieval-augmented generation: chunk the documents, embed them, retrieve the top-k chunks at query time, and paste them into the context window. RAG is widely deployed and genuinely useful for narrow lookups. But it has a fundamental limitation — it never actually *reads* the corpus. Every query starts from scratch. There is no accumulated understanding, no synthesis across sources, no contradiction surfaced.

The LLM Wiki pattern proposes a different model: **compile the knowledge base once, then query the compiled artifact**. Instead of retrieving raw chunks, you maintain a structured wiki where concept pages synthesize information from multiple sources, cross-link to related concepts, and explicitly record tensions between sources. Queries are then answered by navigating this structured representation — closer to how a human expert actually uses their notes.

## Why Compilation Matters

The key insight is that synthesis is expensive and should happen once, not at query time. When a new source arrives, you run an LLM-powered ingest loop that:

1. Identifies concepts in the new source
2. Finds existing concept pages that overlap
3. Merges new information into those pages — or creates new pages when genuinely new concepts appear
4. Records provenance for every claim

The result is a wiki where every page has been synthesized from *all* sources that touch its topic. When you later ask a question, you query a document that already knows what three different sources said about LLMs and memory — you don't have to hope the retrieval step surfaces all three.

## Contradictions as First-Class Information

One concrete advantage of the compiled wiki is that contradictions become visible and persistent. When source A says a method works well and source B says it fails in practice, the ingest loop creates an `## Open Tensions` section in the relevant concept page, recording both positions with attribution. A retrieval system would surface whichever source happened to score highest — potentially hiding the disagreement entirely.

## Current Limitations

The compilation approach is not without cost. The ingest loop is slower than indexing — a single article may take several LLM calls to integrate. And the wiki must be maintained: outdated pages need updating when new sources arrive that supersede them.

The reference implementation on GitHub (approximately 1,200 stars as of March 2024) demonstrates the core loop with structural validation and self-healing via a feedback cycle. Build costs are front-loaded; query costs are low.
