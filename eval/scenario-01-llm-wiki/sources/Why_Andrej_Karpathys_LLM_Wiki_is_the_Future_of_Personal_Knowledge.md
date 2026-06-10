---
title: "Why Andrej Karpathy’s “LLM Wiki” is the Future of Personal Knowledge"
author: "evoailabs"
source: "https://evoailabs.medium.com/why-andrej-karpathys-llm-wiki-is-the-future-of-personal-knowledge-7ac398383772"
---

# Why Andrej Karpathy’s “LLM Wiki” is the Future of Personal Knowledge

## How to stop rediscovering information from scratch and let AI automatically compile, maintain, and compound your second brain.

[![evoailabs](https://miro.medium.com/v2/resize:fill:64:64/1*BLgNlA06Pt8WZJtdpS5u4g.png)](/?source=post_page---byline--7ac398383772---------------------------------------)

[evoailabs](/?source=post_page---byline--7ac398383772---------------------------------------)

5 min read

·

Apr 6, 2026

--

3

Listen

Share

More

Press enter or click to view image in full size

![](images/Why_Andrej_Karpathys_LLM_Wiki_is_the_Future_of_Personal_Knowledge_01.png)

In April 2026, Andrej Karpathy (OpenAI co-founder and former Tesla AI Director) caused a stir in the AI community when he dropped a GitHub Gist titled simply “[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f#llm-wiki)”. It wasn’t a new app, a library, or a SaaS product. It was an “idea file” — a conceptual pattern designed to be copy-pasted into an LLM agent like Claude Code or OpenAI Codex.

Press enter or click to view image in full size

![](images/Why_Andrej_Karpathys_LLM_Wiki_is_the_Future_of_Personal_Knowledge_02.png)

<https://x.com/karpathy/status/2040470801506541998>

Here is a deep dive into the core ideas behind the LLM Wiki, the problems it solves, how it works, and how the broader ecosystem is already building upon it.

## The Oldest Problem: Knowledge Maintenance

Currently, the default way we interact with our documents is through **Retrieval-Augmented Generation (RAG)**. You upload a bunch of PDFs, ask a question, and the LLM searches for relevant text chunks to synthesize an answer. But RAG has a critical flaw: **there is no accumulation**. If you ask a complex question that requires synthesizing five different documents, the LLM has to find and piece together those fragments from scratch. If you ask a similar question tomorrow, it does the exact same work again. Nothing is built up.

On the flip side, humans have tried manually building linked knowledge bases for decades (e.g., the Zettelkasten method, Notion wikis, Obsidian vaults). But human-maintained wikis inevitably fail because the **bookkeeping burden** — updating cross-references, tagging, and noting contradictions — grows much faster than the value.

## What is the “LLM Wiki”?

The LLM Wiki is a design pattern that inserts an LLM-maintained, compounding layer of markdown files between you and your raw source materials.

Instead of searching raw documents on the fly, the **LLM pre-compiles them**. It reads your sources, extracts the concepts, and permanently weaves them into an interlinked markdown wiki.

As Karpathy perfectly frames it: **“Obsidian is the IDE, the LLM is the programmer, the wiki is the codebase.”** You rarely write the wiki yourself. You explore and ask questions, while the LLM does the tedious grunt work of maintaining the database.

## How It Works: Architecture & Operations

The LLM Wiki framework is built on a simple three-layer architecture:

1. **Raw Sources (Immutable):** Your curated PDFs, articles, meeting transcripts, and images. The LLM reads these but never modifies them.
2. **The Wiki (LLM-Maintained):** A directory of markdown files (summaries, concept pages, timelines) completely maintained by the LLM.
3. **The Schema (The Rules):** A configuration file (e.g., CLAUDE.md) that tells your specific agent how to structure the wiki, how to ingest new files, and how to format answers.

To keep this ecosystem alive, the agent executes three main operations:

* **Ingest:** You drop a new article into the raw folder. The agent reads it, writes a summary, updates 10–15 related concept pages with new insights, adds backlinks, and logs the action.
* **Query:** You ask a question. The agent reads the wiki’s central index.md, navigates to the right pages, and gives you an answer. If you discover a new connection during your chat, the LLM files that insight back into the wiki as a new page.
* **Lint:** Periodically, you ask the LLM to “health-check” the wiki. It hunts for broken links, stale claims, contradictions, and orphan pages.

## How It Can Be Used

Because it is just a set of instructions applied to standard Markdown files, the use cases are virtually limitless:

* **Personal Growth:** Feed it journal entries, health data, and podcast notes to build a structured, evolving picture of your psychology and goals.
* **Academic/Deep Research:** As you read papers over months, the LLM incrementally builds out an evolving thesis, linking methodologies and noting where researchers contradict each other.
* **Book & Hobby Tracking:** Drop in book chapters as you read, and watch the LLM build a Tolkien-style fan wiki mapping characters, locations, and plot lines.
* **Business & Teams:** Feed it Slack threads, customer calls, and PRs. New team members can instantly browse an up-to-date internal wiki that nobody had to manually write.

## The Ecosystem: Similar Projects Solving the Same Problem

Karpathy’s gist didn’t happen in a vacuum. It gave a name to a massive shift in how developers are treating AI and knowledge. Here is an analysis of similar projects and spin-offs solving the RAG bottleneck:

**1. Waykee Cortex (Hierarchical Team Knowledge)**  
While Karpathy’s wiki relies on flat indexing, [*Waykee*](https://waykee.com/) focuses on teams. It utilizes a strict hierarchical inheritance model (a specific UI screen inherits context from its module, which inherits from the system). It also uniquely combines the “Knowledge” layer (what exists) with the “Work” layer (tasks, bugs, milestones), allowing issues to inherit dual-context automatically.

**2. Sage-Wiki (The Pipeline Approach)**  
[Built by developer *xoai*,](https://github.com/xoai/sage-wiki) this implementation treats the LLM less like a conversational agent and more like a strict compiler (similar to make). It uses a 5-step incremental pass (diff -> summarize -> extract -> write -> images) and enforces a strict typed-entity system (e.g., specifying if a node “is-a” or “contradicts” another) to prevent the LLM from creating duplicate concepts.

Press enter or click to view image in full size

![](images/Why_Andrej_Karpathys_LLM_Wiki_is_the_Future_of_Personal_Knowledge_03.png)

<https://x.com/xoai/status/2040936964799795503>

**3. Thinking-MCP (Capturing Mental Models)**  
Rather than tracking factual data, [*Thinking-MCP*](https://github.com/multimail-dev/thinking-mcp) captures *how you think*. It scans conversation transcripts to map your heuristics, tensions, and decision-making rules. Instead of a permanent wiki, its graph relies on “node decay” — meaning core values persist, but fleeting ideas fade over time, mirroring the live state of a human brain.

**4. ELF (Eli’s Lab Framework)**  
Designed specifically for the rigorous demands of scientific research, [ELF](https://github.com/ProjectEli/ELF) mixes the PARA organization method with wiki architecture. It uses a “base-delta protocol” for incremental experiments, ensuring total data traceability while minimizing researcher documentation fatigue.

**5. qmd (Local Markdown Search)**  
As wikis scale beyond a few hundred files, a simple index.md file isn’t enough. Built by Shopify CEO Tobi Lütke, [*qmd*](https://github.com/tobi/qmd/tree/main) solves this by acting as a local search engine over markdown files, using a hybrid of BM25 and vector search. With an MCP server, the LLM can shell out to *qmd* natively to fetch information across massive personal wikis.

The LLM Wiki pattern marks a fundamental maturity in how we use AI. We are moving away from treating LLMs purely as search engines or text generators, and finally starting to use them **as tireless librarians and system maintainers.** By shifting the workload of documentation to the AI, we finally unlock the dream of a compounding “Second Brain” that actually takes care of itself.

The viral story continues…

Press enter or click to view image in full size

![](images/Why_Andrej_Karpathys_LLM_Wiki_is_the_Future_of_Personal_Knowledge_04.png)

<https://ai.gopubby.com/the-most-important-employee-at-a-japanese-firm-was-a-markdown-file-8f079d9c4d19>
