---
title: BYO-Agent Ecosystem Recon
type: concept
sources: [1, 2, 3, 4, 6, 7, 11, 12]
last_updated: 2026-07-10
---

# BYO-Agent Ecosystem Recon

The BYO-agent ecosystem recon is Workstream 3 within the [[amplifier-as-agent-workstream|Amplifier as Agent Workstream]] — a separately assignable research effort that surveys the landscape of agent harnesses Amplifier could be embedded in [src: amplifier-as-agent][src: byo-agent-ecosystem-recon]. It serves as Manoj's reference catalogue: which harnesses exist, what each exposes for embedded agent behavior, and what is possible versus not in each. Being separately assignable allows a research-focused person to drive the recon while packaging design proceeds in parallel [src: byo-agent-ecosystem-recon].

## Purpose within the workstream

The recon is not a standalone research exercise — it directly constrains the other two sub-areas [src: amplifier-as-agent][src: byo-agent-ecosystem-recon]. The findings from the ecosystem survey determine what shape the adapter must take (feeding [[amplifier-packaging-and-shape|Amplifier Packaging and Shape]]) and what install/distribution channel choices are viable (feeding [[cli-distribution|CLI Distribution]]): the distribution channel question (question 2 in the recon's eight structured questions) maps directly onto the CLI's install-channel open question — `uv tool install` vs. `npm` vs. both — and the auth model question (question 8) maps onto how the CLI guides community users through provider key setup [src: byo-agent-ecosystem-recon][src: cli-distribution]. Without recon, the packaging and CLI distribution decisions are made in a vacuum [src: byo-agent-ecosystem-recon]. The recon also feeds the **modern-bundles workstream** by determining which host harnesses each bundle variant targets [src: byo-agent-ecosystem-recon].

## Harnesses in scope

The investigation is organized into three tiers [src: byo-agent-ecosystem-recon]:

### Host harnesses (primary integration targets)

These are the platforms where Amplifier as Agent might be consumed [src: amplifier-as-agent][src: byo-agent-ecosystem-recon]:

- **Claude Code** (Anthropic CLI agent) — the canonical reference for "agent in a CLI."
- **Codex CLI** (OpenAI's CLI agent) — the `/goal` command pattern is noted as inspiration for evals.
- **Gemini CLI** (Google's CLI agent).
- **OpenClaw** — open-source Claude-Code-shaped harness; community-built.
- **NanoClaw** — small/local-model-targeting variant of OpenClaw. See [[nanoclaw-dual-variant-integration|NanoClaw Dual-Variant Integration]] for the live integration finding.
- **Paperclip** — internal Microsoft project; according to [src: byo-agent-ecosystem-recon], no public references were found in web search; internal sources required.
- **Manus** — agent platform.
- **pi (anthropic)** — Anthropic's general-purpose agent platform.

### SDK-level integration points

- **Claude SDK** (Anthropic) — programmatic agent integration.
- **Agent SDK** (general).
- Any other SDK that BYO-agent ecosystems hook into [src: byo-agent-ecosystem-recon].

### Adjacent ecosystem (context, not direct integration)

These tools are worth tracking for adoption signals and ecosystem shape even if they are not direct integration targets [src: byo-agent-ecosystem-recon]:

- **Cursor** — agent-in-IDE.
- **OpenCode** — CLI-based dev agent (also in Manoj's primary adapter ordering per [src: amplifier-as-agent]). By June 2026, Brian Krabach was running Amplifier Agent + OpenCode as a TUI with queuing/steering, task-list visibility, and streaming content presentation — with Amplifier driving the session under the hood and OpenCode providing the terminal interface layer [2026-06-28 Amp-Up]. See [[amplifier-opencode-integration|Amplifier Agent and OpenCode Integration]] for the live integration details.
- **Aider** — CLI agent for code editing.

Mini Max and Kimi are also flagged as hosted-model alternatives that affect provider coverage [src: byo-agent-ecosystem-recon]. Marc Goodner surfaced a further adjacent-ecosystem signal in the July 7–9, 2026 team sync: Databricks' **Omnigent**, described in its own announcement as a meta-harness for combining, controlling, and sharing agents across multiple underlying frameworks — a different architectural bet than Amplifier's single-bundle-per-host adapter model, since Omnigent's premise is orchestrating across harnesses rather than embedding one agent inside each host [6]. Marc shared it alongside a companion Databricks blog post benchmarking coding agents against Databricks' own multi-million-line codebase [6] — a concrete, named instance of the long-horizon multi-million-line benchmarking work that Matthew Rayermann separately flagged as a gap in the industry (see [[evaluations-workstream|Evaluations Workstream]]).

> TODO-VERIFY: The specific claims and methodology in Databricks' Omnigent announcement and its multi-million-line codebase benchmark post were shared as links in team chat, not independently reviewed or verified by the team as of the source date.

## Recon questions

For each target harness, the recon surfaces eight structured questions [src: byo-agent-ecosystem-recon]:

1. **Packaging shape** — how does the host harness package itself? (Single CLI? CLI + plugin? Library + adapter? Server + client?)
2. **Distribution channel** — `uv tool install`? `pip`? `npm`? Curl-pipe-bash? GitHub release? App-store-like?
3. **Plugin/agent integration model** — how does the host accept third-party agents, bundles, skills, or tools?
4. **State of the ecosystem** — adoption, community size, momentum signals.
5. **Where Amplifier as Agent could plug in** — concrete integration point (interface, file format, protocol).
6. **Required adaptations** — what would Amplifier as Agent need to change or add to plug in cleanly?
7. **Cost model** — does the host carry cost? does the user? how is provider access handled?
8. **Auth model** — how does the host handle auth to providers and other services?

The auth model question (question 8) was initially theoretical but is now actively encountered in practice via the NanoClaw integration work [src: byo-agent-ecosystem-recon].

## Evals within the harnesses themselves

David Koleczek raised a differentiator question in the July 9, 2026 team sync: he was not aware of any open-source agent harness — OpenCode included — that ships with its own evals, and floated this as a potential Amplifier differentiator [6]. Salil Das's read was that each harness likely has internal evals but they are rarely shared externally [6]. A follow-up survey shared in the same chat (sourced from a ChatGPT query Samuel Lee relayed) found the opposite is closer to true for several of the harnesses already named in this recon's host-harness tier: OpenCode ships `opencode-bench` under the OpenCode/Anomaly org; OpenHands' official benchmark repo describes itself as containing evaluation infrastructure for OpenHands agents; SWE-agent and mini-swe-agent are evaluated through the SWE-bench leaderboard maintained under the SWE-agent org; Aider documents the harness and tools used for its own benchmarks in its benchmark folder; Cline shipped `cline-bench`, an announced open benchmark initiative; and Gemini CLI's repo includes behavioral eval docs for validating agent behavior [6]. The survey's one-line finding: these are mostly official, project-maintained evals, while cross-agent rankings such as SWE-bench, Next.js evals, and Artificial Analysis are external comparison layers rather than each agent's own eval suite [6]. This refines rather than contradicts Salil's read: the harnesses do have evals, they are simply project-specific and not designed for cross-harness comparison — exactly the gap the team's cross-harness Amplifier-agent benchmark goal and Matthew Rayermann's long-horizon benchmark gap (below) both target; see [[evaluations-workstream|Evaluations Workstream]] for the fuller benchmark-gap context.

> TODO-VERIFY: The specific repo names and claims in this survey (e.g. `opencode-bench` under an "OpenCode/Anomaly org", the OpenHands benchmarks repo, Aider's benchmark folder) were generated by a third-party AI query relayed in chat, not independently confirmed by the team as of the source date.

## Live finding: NanoClaw (May 28)

The NanoClaw integration surfaced the first concrete live finding from the recon [src: byo-agent-ecosystem-recon]: NanoClaw is tightly coupled with its native providers, and integrating Amplifier Agent as a native provider exposes gaps in `amplifier-agent`'s capability surface. The key discovery is that the upstream contribution path — third-party integration via `/add-amplifier-agent` skill — requires `AUTH_TOKEN` authentication support in providers. This makes the auth model question a real constraint rather than a theoretical one. Two repos shipped from this work: `manojp99/amplifier-app-nanoclaw` (own fork for evals and internal work, handed to David) and `manojp99/nanoclaw` (upstream-contribution fork via skill, gated on `AUTH_TOKEN`). See [[nanoclaw-dual-variant-integration|NanoClaw Dual-Variant Integration]] for the full two-variant pattern.

## GitHub Copilot: market perception and long-horizon benchmark gap

The July 2, 2026 conversation with the GitHub Copilot CLI team surfaced an external perspective on the competitive landscape that directly informs the recon's framing. MJ Jabbour, who described speaking to approximately 150,000 people in person over two years and interacting with CEOs and university presidents, reported the market perception he hears back: Claude Code is *"the absolute market leader without any doubt or question"* — a tuned coding agent producing reliable responses within a certain scope; Codex is *"up and coming, constantly bragging about capabilities it doesn't have"* but relatively quick; and GitHub Copilot is perceived as *"basically slow and unhelpful"* [2026-07-02 GH-Copilot-Amplifier]. Stephen Toub (GitHub Copilot team) acknowledged this perception problem while noting it may not reflect the underlying technology, and that the fragmented "Copilot" brand — with many different products sharing the name — compounds the perception issue. Evan Boyle confirmed the Copilot web chat is being rebuilt [2026-07-02 GH-Copilot-Amplifier].

MJ's concrete recommendation to the GitHub Copilot team: *"wedge yourselves between Codex and Claude Code"* — achieve an eval that ensures speed and reliability close to Claude Code as the primary goal, which he described as *"very achievable"* [2026-07-02 GH-Copilot-Amplifier]. A specific failure example MJ cited: attempting to rename files and edit a README via Copilot Web took an hour and still failed — a task Claude Code, Claude Desktop, Codex, and Amplifier all handle with 100% reliability [2026-07-02 GH-Copilot-Amplifier].

Matthew Rayermann (GitHub Copilot Agent Runtime) independently identified the **long-horizon task benchmark gap**: there are no existing benchmarks for tasks like migrating a multi-million line codebase from one language to another, replacing an entire layer of a multi-layered app, or converting a monolith to microservices [2026-07-02 GH-Copilot-Amplifier]. Without benchmarks, there is no notion of how good any harness is at these tasks, or how to grade success when — for example — 98% of a C compiler's tests pass after a Rust rewrite. Rayermann saw the Amplifier team's on-demand evaluation creation capability as the most relevant thing they could offer to address this gap [2026-07-02 GH-Copilot-Amplifier]. The conversation also surfaced the idea of connecting the Amplifier team with GitHub's MS-bench team to onboard long-horizon problem types into their benchmarking infrastructure [2026-07-02 GH-Copilot-Amplifier].

## GitHub Copilot: infrastructure and roadmap signals

Beyond market perception, the July 2, 2026 meeting surfaced concrete infrastructure signals about GitHub Copilot's direction. Matthew Rayermann shared the **`github/copilot-agent-runtime`** repo as the underlying runtime powering Copilot's agentic work [2026-07-02 GH-Copilot-Amplifier]. Steve Sanderson confirmed that **GitHub.com's chat experience is being relaunched on the team's proper harness** within approximately one month — a signal that the Copilot team was actively consolidating its fragmented surfaces onto a unified foundation [2026-07-02 GH-Copilot-Amplifier]. This relaunch is directly relevant to the recon's harness-evaluation work: the Copilot harness the recon targets will be a different architecture than the current fragmented surfaces, and integration planning should account for the post-relaunch shape.

Evan Boyle also described a concrete large-scale agentic achievement within the GitHub Copilot team: landing an **85,000 LOC PR adding PowerPoint, Excel, and Word support** to the GitHub App from scratch — describing it as "lots of bugs but shockingly good" [2026-07-02 GH-Copilot-Amplifier]. This demonstrates that the Copilot team has its own experience with large-scale agentic code generation, making them a peer in the long-horizon task space rather than simply a target for Amplifier's techniques.

MJ Jabbour (July 6, 2026) praised the **GitHub Copilot desktop app** as *"a much better and very different experience from the other surfaces"* and expressed hope that the other interfaces would be folded into it [2026-07-06 GH-Copilot-Amplifier]. This desktop-app surface represents a qualitatively different integration target from the web chat or CLI surfaces — one where the harness integration model, UX expectations, and capability surface may differ significantly from what the recon has characterized to date.

## Open questions

Three open questions govern the recon's scope and output format [src: byo-agent-ecosystem-recon]:

- **Scope cap** — how many hosts to deeply investigate before diminishing returns? The working suggestion is 3–5 deep investigations plus a breadth scan of the rest.
- **Quantitative comparison** — whether to produce a comparison matrix (host × packaging × distribution × etc.) or a per-host writeup with cross-references.
- **Source access** — Paperclip is internal to Microsoft; external sources are unavailable without help locating them.

The recon also notes that this ecosystem is moving fast and recommends re-reading in one month [src: byo-agent-ecosystem-recon].

## Position in the Base Camp dependency map

Within the [[base-camp-workstream-index|Base Camp]] dependency map, the recon sits as the first stage of Amplifier as Agent (recon → packaging → CLI distribution) [src: base-camp-INDEX]. It is upstream of the packaging and CLI distribution stages: the harness capabilities discovered here determine the adapter shape, which determines the packaging interface, which determines the install experience. Evaluations (David's meta-capability) consumes the adapter work downstream for measurement, and Cost Management feeds cost telemetry into the CLI distribution stage [src: amplifier-as-agent].

## Shared infrastructure with Amplifier Scientist

The `amplifier-app-benchmarks` harness extension work (Salil and Manoj) that underpins this recon is also named as a dependency of the [[amplifier-scientist|Amplifier Scientist]] horizon project [src: amplifier-scientist]. This means the eval harness infrastructure being built for the ecosystem recon and BYO-agent adapters is shared with the scientific evaluation framework MJ is developing.

## Sources

[^1]: amplifier-as-agent.md
[^6]: Brian K - Team Sync Teams chat (2026-07-07 – 2026-07-09) — David Koleczek's harness-evals differentiator question, the ChatGPT-sourced survey of which open-source agent harnesses ship their own evals, and Marc Goodner's Databricks Omnigent / multi-million-line codebase benchmark links
