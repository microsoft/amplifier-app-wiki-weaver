---
title: "I Built Karpathy’s LLM Wiki Twice — Once as Code, Once as a .md . Here's What Each One Gives Up."
author: "Leandro Bernardo"
source: "https://pub.towardsai.net/i-built-karpathys-llm-wiki-twice-once-as-code-once-as-a-md-heres-what-each-one-gives-up-08b31170999a"
---

# I Built Karpathy’s LLM Wiki Twice — Once as Code, Once as a `.md`. Here's What Each One Gives Up.

## Karpathy shared an idea file. I shipped a Python package, then folded the same idea back into a single markdown file for agents. They reach the same destination. The road there is what differs.

[![Leandro Bernardo](https://miro.medium.com/v2/resize:fill:64:64/1*QMI9_72BLUT34Ygdxoo13Q.png)](https://medium.com/@bernardo.leandro?source=post_page---byline--08b31170999a---------------------------------------)

[Leandro Bernardo](https://medium.com/@bernardo.leandro?source=post_page---byline--08b31170999a---------------------------------------)

7 min read

·

5 days ago

--

4

Listen

Share

More

Press enter or click to view image in full size

![]()

Read this article for free: [here](https://medium.com/@bernardo.leandro/i-built-karpathys-llm-wiki-twice-once-as-code-once-as-a-md-heres-what-each-one-gives-up-08b31170999a?sk=03579bb6d5a6f297495025a8d311ea08)

In April 2026, Andrej Karpathy posted [a GitHub gist titled](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) `llm-wiki.md`. It was not code. It was not a product. It was what he called an *idea file* — a pattern, written in plain prose, designed to be pasted into an LLM agent so the agent could build a wiki tailored to its user.

The framing was elegant. Most of what we do with documents today looks like RAG: you upload files, the model retrieves chunks, generates an answer, forgets. Karpathy’s pivot: stop re-reading raw sources every query. Compile them, once, into a structured, interlinked wiki. Then query the wiki. Treat knowledge the way compilers treat source code — pre-process once, run fast forever.

I read the gist on a Saturday. By Sunday night I had a Python package. A month later, I rewrote the same idea as a single `AGENTS.md` file. Both work. Both are open source. They are not redundant — they are two answers to the same question, and the question is *how much of the implementation should be frozen in code versus negotiated with the agent at runtime?*

This is a walk through both, what each one optimizes for, and how to choose.

## Why I went programmatic first

My first instinct was to make it reproducible. I work with corpora of hundreds to thousands of documents — corporate manuals, regulatory texts, internal procedures — where the cost of running an LLM over every page on every change is not academic. Tokens add up. Hallucinations compound. When the same content needs to produce the same wiki page every time, “ask the agent nicely” is not a specification.

So I wrote [wiki-llm](https://github.com/LeoBR84p/wiki_llm) (available on Github).

The architecture is the eight stages Karpathy sketched, hardened into a pipeline:

Press enter or click to view image in full size

![]()

A few decisions worth naming, because they are the ones that turn the idea into something a team can actually run unattended:

* **Pydantic v2 contracts between stages.** Every stage takes a typed input and returns a typed output. When the Writer hands a draft to the Evaluator, the schema is enforced, not hoped for.
* **Deterministic content-addressable IDs.** Each page’s UUID is derived from the SHA-256 of its stripped body. Same content, same ID — regardless of filename, frontmatter, or formatting. Rename a source file and nothing breaks downstream.
* **Multi-backend LLM via** `instructor`**.** OpenRouter, OpenAI, Bedrock, Ollama — same code path, structured outputs with automatic retries.
* **A repair agent built with LangGraph.** Not the whole pipeline — just the corner where lint findings need to be resolved with fan-out and review. Use the agent where the agent earns its keep.
* **BM25 chat, no vector store.** For a wiki the size of a personal or team knowledge base, lexical retrieval is fast, transparent, and good enough. Save the embeddings infrastructure for when you actually need it.
* **A Markdown processing layer that takes structure seriously.** This is the bit I’ll come back to in a minute.

The pipeline can run on a Kubernetes CronJob in production. The chat UI is a separate Deployment. The whole thing fits in a Docker image and is configured through a single Python module that exposes a `WikiConfig` object. The user writes that config once and the pipeline does the rest.

This is the programmatic answer to the idea file: *every operational concern made explicit, every prompt versioned, every output deterministic where determinism is possible.*

## Then I rewrote it as a `.md`

A few weeks later, helping a friend set up a personal wiki on a couple hundred articles, I realised I was over-engineering the introduction. He didn’t need Pydantic, Kubernetes, or a repair agent. He needed Claude Code pointed at a folder, with enough structure to behave like a wiki maintainer instead of a chatbot.

So I distilled the same eight stages into a single file: `AGENTS.md`, with a thin `CLAUDE.md` shim that points to it. The whole thing is in the same repo. It runs on any agent that reads a project-root instruction file — Claude Code, Codex, Cursor, VS Code Agent Mode.

The shape is deliberately close to the Python version. Same stages, same names, same conventions:

Press enter or click to view image in full size

![]()

What disappeared, compared to the programmatic version:

* The Python imports. The Pydantic models. The Dockerfile. The CI workflow.
* The LangGraph repair agent — replaced by “for each broken link, find the closest match by fuzzy/semantic similarity; if confidence > 80%, replace; else flag with TODO.”
* The deterministic UUID. The agent uses slugified titles.
* The chat UI. If the user wants chat, the agent answers from the wiki directly.

What stayed:

* The eight stages, in the same order.
* The triple-pass Writer → Evaluator → Editor loop.
* The `[[wikilink]]` convention.
* The lint rules. The repair behaviour. The consolidation step asking before deleting.
* A CONFIG block at the top of the file that the agent fills during a first-run wizard and edits with its file-editing tool.

It is, in effect, the idea file with the operational shape of the programmatic version stamped onto it.

Press enter or click to view image in full size

![]()

The honest summary is this: **for large corpora and recurring pipelines, the programmatic version pays for itself in tokens, predictability, and review-ability. For smaller bases or projects where the shape of the wiki is still being discovered, the** `.md` **version is faster to start and easier to change.** Output quality is comparable in both — Karpathy's pattern is robust enough that the loop *Writer → Evaluator → Editor → Lint → Repair* produces good pages whether it runs as Python or as agent instructions. The difference is everything around the loop, not the loop itself.

## The Markdown problem, and why it’s not optional

There is one piece of the programmatic version that I want to call out specifically, because it is the bit that most LLM-generated wikis quietly fail at.

When an agent writes Markdown, it produces *plausible* Markdown. Headings that mostly nest correctly. Tables that usually parse. Links that look right but resolve nowhere. Frontmatter that is YAML-shaped but breaks when a colon appears inside an unquoted value. Two H1s in the same file because the agent forgot it already wrote one. Section orders that drift between regenerations of the same source.

In a pipeline that compounds — every page becomes input to topics, groups, the index, the chat retriever — that structural noise is not cosmetic. It corrupts every downstream stage. The chunker misroutes overlap across sections. The deduplicator merges the wrong pages. The lint pass reports problems the repair pass can’t safely fix because it can’t tell which heading is the “real” one.

I wrote [markdown-hero](https://medium.com/@bernardo.leandro/markdown-hero-6391c786176b) to solve this layer specifically — type-checked, section-aware, multilingual, with a chunker whose overlap window stays inside the same heading. In wiki-llm, every page that leaves the Generate stage passes through `lint()`. Every merge in Consolidate goes through `markdown_merge(dedupe_headings=True)`. Every chunk that feeds the chat index comes from `extract_chunks(purpose="rag")`. The pipeline does not produce structural garbage because the markdown-hero layer refuses to.

The agentic version cannot afford that guarantee. It asks the agent to follow conventions, then trusts the agent. For most use cases, that trust holds. For pipelines where the wiki is the input to *another* automated stage, it doesn’t.

This is one of the few places where the programmatic version is not a heavier alternative — it’s a different *capability*.

You can read all about markdown-hero [here](https://medium.com/@bernardo.leandro/markdown-hero-6391c786176b) and get it from pypi with:

```
pip install markdown-hero
```

## When to pick which

If you fit any of these, start with the `.md`:

* A personal or small-team wiki, under ~200 documents.
* You’re still figuring out what the wiki should look like.
* You’re already using Claude Code, Codex, or Cursor every day, and want the wiki to live where you already work.
* You want to iterate on prompts and structure without redeploying anything.

If you fit any of these, start with the package:

* A corpus large enough that paying tokens to regenerate unchanged pages is a real cost.
* A team that needs the same wiki to come out of the same inputs every time.
* A pipeline that feeds downstream automation (search, chat, exports, dashboards).
* Production runs on a schedule — overnight, weekly, on commit.
* An audit trail of every LLM call is a requirement.

Both implementations honour the original idea. Karpathy’s instinct — that the right thing to share in the agent era is the *pattern*, not the code — turned out to be load-bearing in a way I didn’t appreciate until I’d written both versions. The `.md` exists *because* the pattern was clear. The Python exists *because* the pattern translates well into engineering decisions.

Two ways to start today:

* **The original idea file:** [Karpathy’s](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) `llm-wiki.md` [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — paste it into your agent and let it shape a wiki around you.
* **My agentic implementation:** `AGENTS.md` [in wiki\_llm](https://github.com/LeoBR84p/wiki_llm) — drop it into any project, point your agent at a folder of documents, ask it to build the wiki.
* **My programmatic implementation:** `wiki-llm` [on GitHub](https://github.com/LeoBR84p/wiki_llm) — `pip install`, write a config, run the pipeline. Built on [markdown-hero](https://github.com/LeoBR84p/markdown_hero) for the structural floor.

Pick the one that fits the corpus. Switch later if it doesn’t.

*If you found this useful, leave a clap and follow for more on the engineering decisions behind LLM-era knowledge tooling.*
