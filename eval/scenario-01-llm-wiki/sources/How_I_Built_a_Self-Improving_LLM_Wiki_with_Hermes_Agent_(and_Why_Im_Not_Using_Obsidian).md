---
title: "How I Built a Self-Improving LLM Wiki with Hermes Agent (and Why I’m Not Using Obsidian)"
author: "Jsong"
source: "https://medium.com/@jsong_49820/how-i-built-a-self-improving-llm-wiki-with-hermes-agent-and-why-im-not-using-obsidian-1e9a7fa438c1"
---

# How I Built a Self-Improving LLM Wiki with Hermes Agent (and Why I’m Not Using Obsidian)

[![Jsong](https://miro.medium.com/v2/da:true/resize:fill:64:64/0*WyRpUJCmD298fZ3a)](/@jsong_49820?source=post_page---byline--1e9a7fa438c1---------------------------------------)

[Jsong](/@jsong_49820?source=post_page---byline--1e9a7fa438c1---------------------------------------)

8 min read

·

Apr 17, 2026

--

3

Listen

Share

More

## The Short Version

I wanted a personal knowledge base that:

* Lives on the open internet (not just inside a desktop app)
* Compounds over time instead of rotting
* Is maintained by an LLM, not by me

So I:

1. Provisioned a small Linux VPS (Hetzner, 2 vCPU / 4 GB RAM)
2. Installed Hermes Agent (NousResearch’s self‑improving AI agent)
3. Connected it to Telegram as a “second brain” bot
4. Fed it Andrej Karpathy’s LLM Wiki pattern as a spec
5. Let Hermes build and maintain a wiki, and exposed it via a web frontend at `https://wiki.ai-biz.app`

This is the story of how and why I did it.

## 1. Why I Wanted This (and Why Not Obsidian)

## 1.1 Obsidian is great — until it isn’t

I’ve used Obsidian for years. It’s beautiful for personal knowledge management (PKM). But over time, I hit three problems:

1. Maintenance debt  
   I kept abandoning wikis because updating cross‑references, summaries, and “index” pages felt like a second job. This is exactly what Karpathy points out: the tedious part of a knowledge base is the bookkeeping, not the reading.
2. Desktop lock‑in  
   My notes lived on my laptop. If I was on my phone or a different machine, my “second brain” was out of reach. I wanted something that feels like a website I can open from anywhere.
3. Static notes  
   Most of my notes were dead the moment I wrote them. Nothing changed unless I manually edited it. I wanted a knowledge base that improves itself as I use it.

## 1.2 The LLM Wiki pattern

Andrej Karpathy’s LLM Wiki gist describes a different approach: instead of RAG‑style retrieval from raw documents every time, the LLM incrementally builds and maintains a persistent wiki — a structured, interlinked set of markdown files that sits between you and your raw sources.

The architecture is simple:

* Raw sources — your curated articles, papers, notes (immutable)
* The wiki — LLM‑generated summaries, entity pages, concept pages, cross‑links
* The schema — a config (e.g. `AGENTS.md`) that tells the LLM how to behave

Operations are:

* Ingest — add a source → LLM updates multiple wiki pages
* Query — ask questions → answers get saved as new wiki pages
* Lint — periodic health checks (contradictions, orphans, stale claims)

I didn’t want to implement this from scratch. I wanted an agent that could own the wiki layer.

That’s where Hermes Agent comes in.

## 2. Why Hermes Agent?

[Hermes Agent](https://github.com/NousResearch/hermes-agent) is an open‑source, self‑improving AI agent built by Nous Research. It’s designed to:

* Run on a $5 VPS or a bigger server
* Talk to you via Telegram, Discord, Slack, WhatsApp, Signal, CLI — all from a single gateway
* Maintain layered memory, create reusable “skills,” and generally act like a long‑term collaborator instead of a forgetful chatbot

The key things for me:

* Persistent memory across sessions  
  It remembers what we’ve done, not just the current chat.
* Multi‑surface access  
  I can talk to it from Telegram while it runs on a Hetzner VPS.
* Tool use and terminal access  
  It can run commands, edit files, and manage a wiki repository if I teach it how.

That made it the perfect host for an LLM Wiki.

## 3. Architecture Overview

Here’s the high‑level setup:

Press enter or click to view image in full size

![](images/How_I_Built_a_Self-Improving_LLM_Wiki_with_Hermes_Agent_(and_Why_Im_Not_Using_Obsidian)_01.jpg)

* Hermes Agent runs on the Hetzner VPS.
* The Telegram gateway receives messages and forwards them to Hermes.
* Hermes uses a local git repo as the wiki, applying Karpathy’s LLM Wiki pattern.
* A static site generator (or web UI) exposes the wiki at `https://wiki.ai-biz.app`[.](https://wiki.ai-biz.app.)

## 4. Step‑by‑Step: Building the LLM Wiki

## 4.1 Step 1 — Provision a Linux VPS

I chose:

* Provider: Hetzner
* Specs: 2 vCPU, 4 GB RAM (enough for a small LLM agent and wiki)
* OS: Ubuntu (latest LTS)

Once the server was up, I:

1. SSH’d in as root.
2. Updated the system:

> bash
>
> apt update && apt upgrade -y

3. Created a normal user with sudo access and switched to it.

You don’t need a GPU for this; Hermes is model‑agnostic and can use remote APIs (OpenAI, OpenRouter, etc.).

## 4.2 Step 2 — Install Hermes Agent

Hermes provides a one‑line installer for Linux/macOS/WSL2:

> bash
>
> curl -fsSL <https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh> | bash

This script:

* Installs dependencies (Python, Node.js, ripgrep, ffmpeg, etc.)
* Clones the Hermes Agent repo
* Creates a virtual environment
* Sets up the global `hermes` command

After installation:

> bash
>
> source ~/.bashrc # or ~/.zshrc
>
> hermes # Start chatting in the terminal

You can also run `hermes setup` to configure providers and other options.

## 4.3 Step 3 — Configure the Telegram Gateway

I wanted to talk to my wiki from my phone, so I used Telegram.

### 4.3.1 Create a Telegram bot

1. Open Telegram and search for @BotFather.
2. Send `/newbot`.
3. Follow the prompts to name the bot and choose a username (e.g. `my_llm_wiki_bot`).
4. BotFather returns a bot token like:

> Text
>
> 7123456789:AAF…

Keep this secret.

### 4.3.2 Get your Telegram user ID

Hermes uses numeric user IDs to gate access.

1. Message @userinfobot on Telegram.
2. It replies with a number like `123456789`.
3. Save this — this is your user ID.

### 4.3.3 Run the Hermes gateway setup

On the VPS:

> bash
>
> hermes gateway setup

Select Telegram.

Paste:

* The bot token from BotFather
* Your numeric user ID

Hermes stores these in `~/.hermes/.env` (or equivalent) and starts the gateway with:

> bash
>
> hermes gateway start

Now the bot is live and tied to your Hermes instance.

## 4.4 Step 4 — Set Up a Telegram Profile for the LLM Wiki

I wanted the bot to feel like a dedicated “LLM Wiki” assistant, so I:

1. Set a profile picture and description in Telegram (via BotFather or a Telegram client).
2. Gave it a clear persona:

* “You are my LLM Wiki maintainer. You manage a markdown wiki in a git repo.”

3. Ensured only my user ID is allowed to talk to it (for security).

At this point, I had a working Telegram interface to Hermes, but no wiki yet.

## 4.5 Step 5 — Teach Hermes the LLM Wiki Pattern

### 4.5.1 Share Karpathy’s gist

I sent Hermes a message along these lines:

> *I want to implement Andrej Karpathy’s LLM Wiki pattern.  
> Here is the spec:*[*https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f*](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) *Please:*
>
> - Read and understand the architecture (sources, wiki, schema)
>
> - Prepare to implement the operations: Ingest, Query, Lint.
>
> - Use a local git repo as the wiki and schema.

The gist describes:

* Three layers: raw sources, wiki, schema
* Three operations: ingest, query, lint
* Special files like `index.md` (catalog) and `log.md` (append‑only timeline)

Hermes ingested this and we iterated on a concrete layout for my domain.

### 4.5.2 Initialize the wiki repo

On the VPS, I had Hermes create a structure like:

> wiki/
>
> raw/ # source documents (articles, notes, images)
>
> wiki/ # LLM-generated pages
>
> schema/
>
> AGENTS.md # instructions for how to maintain the wiki
>
> index.md # catalog of pages
>
> log.md # chronological log

This mirrors Karpathy’s architecture, but tailored to my needs.

### 4.5.3 Define the schema

We co‑authored `AGENTS.md` with rules such as:

* Where to find raw sources.
* What kind of pages to create (summaries, entities, comparisons).
* How to format frontmatter and sections.
* When to update `index.md` and `log.md`.
* How to lint for contradictions, orphans, and stale claims.

This schema is what turns Hermes from a generic chatbot into a disciplined wiki maintainer.

## 4.6 Step 6 — Build the Web Frontend

I wanted the wiki to be accessible from any browser, not just Telegram or the CLI.

### 4.6.1 Choose a web stack

There are several community web UIs for Hermes, like Hermes WebUI and Hermes Workspace, which provide browser dashboards and workspaces for Hermes Agent.

For a public wiki, I instead:

* Treated the `wiki/` repo as a static site (Markdown → HTML).
* Used a static generator (Hugo, MkDocs, or a custom script) to build the site.
* Served it via a simple web server (Caddy, Nginx, or a PaaS).

### 4.6.2 Connect the wiki to the web

1. Point a domain  
   I pointed `wiki.ai-biz.app` to the VPS via my DNS provider.
2. Serve the site  
   Example with Caddy:

> wiki.ai-biz.app {
>
> root \* /home/user/wiki-site
>
> file\_server
>
> }

Or use a static hosting platform if you don’t want to manage the server.

3. Automate builds

* Hermes can run a script after each update to rebuild the static site.
* Or you can use a simple cron job or git hook.

Now:

* Telegram is my input interface.
* The web is my reading interface.
* Hermes does the heavy lifting in between.

## 4.7 Step 7 — Ongoing Workflow: Ingest, Query, Lint

With everything in place, my workflow looks like this:

1. Ingest

* I send a URL, file, or text to the Telegram bot.
* Hermes reads it, summarizes it, and integrates it into the wiki.
* It updates entity pages, the index, and the log, as Karpathy describes.

2. Query

* I ask questions like “What are the main open problems in X?”
* Hermes searches the wiki, synthesizes an answer, and files the answer as a new page when appropriate.

3. Lint

* Periodically, I ask Hermes to “lint the wiki.”
* It checks for contradictions, orphan pages, missing cross‑links, and stale claims.

The key point: I don’t maintain the wiki. Hermes does.

## 5. Lessons Learned

## 5.1 The agent is the real product

Hermes Agent turned the LLM Wiki from a nice idea into a working system without me writing a lot of code. The self‑improving agent concept means it:

* Remembers how I like my wiki structured.
* Suggests improvements to the schema.
* Gradually becomes better at maintaining the knowledge base.

## 5.2 Telegram is a surprisingly good interface

Once I got used to it, chatting with my wiki from anywhere (phone, desktop) felt natural. Voice memos get transcribed, and I can forward links and files directly into the “second brain.”

## 5.3 Obsidian is no longer the center

I still use Obsidian for some things, but:

* The wiki is now Hermes‑maintained and on the web.
* Obsidian is just one of many possible frontends.
* I’m no longer tied to a specific desktop app.

## 6. How You Could Adapt This

If you want to build something similar:

1. Pick your agent  
   Hermes is one option; there are others, but Hermes already has multi‑platform gateways and tooling.
2. Implement the LLM Wiki pattern

* Use Karpathy’s gist as a spec.
* Define your schema and directory layout.
* Start with a small set of sources and iterate.

3. Choose your interfaces

* Telegram for quick, mobile interactions.
* Web for reading and sharing.
* Maybe Obsidian only as a local viewer.

4. Automate the boring stuff  
Let the agent handle:

* Index maintenance
* Cross‑link updates
* Linting and contradiction checks

## 7. Why This Matters

For me, this is about more than a neat tool. It’s about:

* Compounding knowledge instead of accumulating dead notes.
* Having a second brain that actually maintains itself.
* Decoupling my knowledge base from any single app and putting it on the open web.

If you’re tired of abandoned wikis and scattered notes, building an LLM Wiki with Hermes Agent is a concrete way to make your knowledge actually work for you.

If you want to spin up something similar, you can start with:

* [Hermes Agent repo](https://github.com/NousResearch/hermes-agent)
* [Karpathy’s LLM Wiki gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
* [Hermes Telegram setup guide](https://remoteopenclaw.com/blog/hermes-agent-telegram-setup)
