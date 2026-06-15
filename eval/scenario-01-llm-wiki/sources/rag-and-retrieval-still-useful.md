---
title: "RAG Is Not Dead: Why Retrieval Still Has a Role in Knowledge Systems"
author: "Marcus Thompson"
source: "https://ml-practitioner.example.com/rag-still-useful-2024"
date: "2024-04-18"
---

# RAG Is Not Dead: Why Retrieval Still Has a Role in Knowledge Systems

A wave of commentary has declared retrieval-augmented generation obsolete, arguing that compiled knowledge wikis are strictly superior. This overstates the case. RAG and compiled wikis solve different problems. Understanding when to use each matters more than picking a winner.

## What RAG Does Well

RAG excels at precise lookup from large, relatively stable corpora. When you need to find the specific clause in a contract, the exact specification for an API endpoint, or the verbatim wording of a policy document, retrieval from the raw source is superior to a synthesized wiki. The wiki synthesizes and compresses — which is exactly what you do *not* want when you need the original text.

RAG is also faster to stand up. There is no ingest pipeline, no validation loop, no convergence criterion. You index the documents and query. For corpora that change rapidly — daily news, live documentation, real-time data feeds — the latency of wiki maintenance may be unacceptable.

## Where Compiled Wikis Win

The compiled wiki approach outperforms RAG on cross-source synthesis. When a user asks "what do five different papers say about attention mechanisms and how do they disagree?" — that question requires reading all five papers, synthesizing across them, and identifying contradictions. A RAG system returns the top-k chunks from the embedding search and asks the LLM to synthesize on the fly. A wiki that has already performed that synthesis at ingest time answers from a concept page that already records the tensions.

The performance gap is widest for contradiction-aware questions and for corpora where multiple sources address overlapping topics with different conclusions.

## A Practical Recommendation

For corpora under 200 documents with moderate overlap, RAG is sufficient and easier to maintain. For larger corpora with significant thematic overlap — research literature, a company's internal knowledge base, ongoing technical reading — the compiled wiki approach delivers meaningfully better cross-source answers.

The two approaches are also composable: a RAG system over raw sources and a compiled wiki can coexist, with the wiki handling synthesized queries and RAG handling precise lookup. Declaring RAG dead forecloses useful architecture options.

The claim that "RAG has fundamental limitations" is accurate but incomplete. Every architecture has limitations. The question is whether those limitations matter for your use case.
