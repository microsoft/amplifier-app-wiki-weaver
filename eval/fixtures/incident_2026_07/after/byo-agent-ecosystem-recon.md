---
title: BYO-Agent Ecosystem Recon
type: concept
sources: [1, 2, 3, 4, 6, 7, 11, 12]
last_updated: 2026-07-06
---

# BYO-Agent Ecosystem Recon

The BYO-agent ecosystem recon is Workstream 3 within the [[halyard-as-agent-workstream|Halyard as Agent Workstream]] — a separately assignable research effort that surveys the landscape of agent harnesses Halyard could be embedded in [src: halyard-as-agent][src: byo-agent-ecosystem-recon]. It serves as Priya's reference catalogue: which harnesses exist, what each exposes for embedded agent behavior, and what is possible versus not in each. Being separately assignable allows a research-focused person to drive the recon while packaging design proceeds in parallel [src: byo-agent-ecosystem-recon].

## Purpose within the workstream

The recon is not a standalone research exercise — it directly constrains the other two sub-areas [src: halyard-as-agent][src: byo-agent-ecosystem-recon]. The findings from the ecosystem survey determine what shape the adapter must take (feeding [[halyard-packaging-and-shape|Halyard Packaging and Shape]]) and what install/distribution channel choices are viable (feeding [[cli-distribution|CLI Distribution]]): the distribution channel question (question 2 in the recon's eight structured questions) maps directly onto the CLI's install-channel open question — a Python tool installer vs. a JavaScript package registry vs. both — and the auth model question (question 8) maps onto how the CLI guides community users through provider key setup [src: byo-agent-ecosystem-recon][src: cli-distribution]. Without recon, the packaging and CLI distribution decisions are made in a vacuum [src: byo-agent-ecosystem-recon]. The recon also feeds the **modern-bundles workstream** by determining which host harnesses each bundle variant targets [src: byo-agent-ecosystem-recon].

## Harnesses in scope

The investigation is organized into three tiers [src: byo-agent-ecosystem-recon]:

### Host harnesses (primary integration targets)

These are the platforms where Halyard as Agent might be consumed [src: halyard-as-agent][src: byo-agent-ecosystem-recon]:

- **Ironwood** (Grayfield Research's CLI agent) — the canonical reference for "agent in a CLI."
- **Nimbus CLI** (Verado's CLI agent) — its `/goal` command pattern is noted as inspiration for evals.
- **Slate CLI** (Northbeam's CLI agent).
- **Bramble** — open-source Ironwood-shaped harness; community-built.
- **Bramble-mini** — small/local-model-targeting variant of Bramble. See [[bramble-mini-dual-variant-integration|Bramble-mini Dual-Variant Integration]] for the live integration finding.
- **Quarry** — internal-only partner project; according to [src: byo-agent-ecosystem-recon], no public references were found in web search; internal sources required.
- **Anvil** — agent platform.
- **Tessellate** (Grayfield Research) — Grayfield's general-purpose agent platform.

### SDK-level integration points

- **Ironwood SDK** (Grayfield Research) — programmatic agent integration.
- **Agent SDK** (general).
- Any other SDK that BYO-agent ecosystems hook into [src: byo-agent-ecosystem-recon].

### Adjacent ecosystem (context, not direct integration)

These tools are worth tracking for adoption signals and ecosystem shape even if they are not direct integration targets [src: byo-agent-ecosystem-recon]:

- **Stitchwork** — agent-in-IDE.
- **Pilotfish** — CLI-based dev agent (also in Priya's primary adapter ordering per [src: halyard-as-agent]). By June 2026, Hollis Marchetti was running Halyard Agent + Pilotfish as a TUI with queuing/steering, task-list visibility, and streaming content presentation — with Halyard driving the session under the hood and Pilotfish providing the terminal interface layer [2026-06-28 Loop-In]. See [[halyard-pilotfish-integration|Halyard Agent and Pilotfish Integration]] for the live integration details.
- **Corvid** — CLI agent for code editing.

Sablefish and Tinderbox are also flagged as hosted-model alternatives that affect provider coverage [src: byo-agent-ecosystem-recon].

## Recon questions

For each target harness, the recon surfaces eight structured questions [src: byo-agent-ecosystem-recon]:

1. **Packaging shape** — how does the host harness package itself? (Single CLI? CLI + plugin? Library + adapter? Server + client?)
2. **Distribution channel** — language package manager? OS package manager? Curl-pipe-bash? Forge release? App-store-like?
3. **Plugin/agent integration model** — how does the host accept third-party agents, bundles, skills, or tools?
4. **State of the ecosystem** — adoption, community size, momentum signals.
5. **Where Halyard as Agent could plug in** — concrete integration point (interface, file format, protocol).
6. **Required adaptations** — what would Halyard as Agent need to change or add to plug in cleanly?
7. **Cost model** — does the host carry cost? does the user? how is provider access handled?
8. **Auth model** — how does the host handle auth to providers and other services?

The auth model question (question 8) was initially theoretical but is now actively encountered in practice via the Bramble-mini integration work [src: byo-agent-ecosystem-recon].

## Live finding: Bramble-mini (May 28)

The Bramble-mini integration surfaced the first concrete live finding from the recon [src: byo-agent-ecosystem-recon]: Bramble-mini is tightly coupled with its native providers, and integrating Halyard Agent as a native provider exposes gaps in `halyard-agent`'s capability surface. The key discovery is that the upstream contribution path — third-party integration via `/add-halyard-agent` skill — requires `AUTH_TOKEN` authentication support in providers. This makes the auth model question a real constraint rather than a theoretical one. Two repos shipped from this work: `praghu/halyard-app-bramble` (own fork for evals and internal work, handed to Naomi) and `praghu/bramble` (upstream-contribution fork via skill, gated on `AUTH_TOKEN`). See [[bramble-mini-dual-variant-integration|Bramble-mini Dual-Variant Integration]] for the full two-variant pattern.

## Sandpiper: market perception and long-horizon benchmark gap

The July 2, 2026 conversation with the Tallgrass Sandpiper CLI team surfaced an external perspective on the competitive landscape that directly informs the recon's framing. Dmitri Falk, who described speaking to roughly a hundred and fifty thousand people in person over two years and interacting with executives and university leadership, reported the market perception he hears back: Ironwood is *"the absolute market leader without any doubt or question"* — a tuned coding agent producing reliable responses within a certain scope; Nimbus CLI is *"up and coming, constantly bragging about capabilities it doesn't have"* but relatively quick; and Sandpiper is perceived as *"basically slow and unhelpful"* [2026-07-02 Tallgrass-Halyard]. Tobias Renn (Tallgrass Sandpiper team) acknowledged this perception problem while noting it may not reflect the underlying technology, and that the fragmented "Sandpiper" brand — with many different products sharing the name — compounds the perception issue. Marisol Trent confirmed the Sandpiper web chat is being rebuilt [2026-07-02 Tallgrass-Halyard].

Dmitri's concrete recommendation to the Tallgrass team: *"wedge yourselves between Nimbus and Ironwood"* — achieve an eval that ensures speed and reliability close to Ironwood as the primary goal, which he described as *"very achievable"* [2026-07-02 Tallgrass-Halyard]. A specific failure example Dmitri cited: attempting to rename files and edit a README via the Sandpiper web surface took an hour and still failed — a task Ironwood, Nimbus, Bramble, and Halyard all handle with 100% reliability [2026-07-02 Tallgrass-Halyard].

Ingrid Vasquez (Tallgrass Agent Runtime) independently identified the **long-horizon task benchmark gap**: there are no existing benchmarks for tasks like migrating a multi-million line codebase from one language to another, replacing an entire layer of a multi-layered app, or converting a monolith to microservices [2026-07-02 Tallgrass-Halyard]. Without benchmarks, there is no notion of how good any harness is at these tasks, or how to grade success when — for example — 98% of a C compiler's tests pass after a systems-language rewrite. Vasquez saw the Halyard team's on-demand evaluation creation capability as the most relevant thing they could offer to address this gap [2026-07-02 Tallgrass-Halyard]. The conversation also surfaced the idea of connecting the Halyard team with Tallgrass's internal benchmarking group to onboard long-horizon problem types into their benchmarking infrastructure [2026-07-02 Tallgrass-Halyard].

## Sandpiper: infrastructure and roadmap signals

Beyond market perception, the July 2, 2026 meeting surfaced concrete infrastructure signals about Sandpiper's direction. Ingrid Vasquez shared the **`tallgrass/sandpiper-agent-runtime`** repo as the underlying runtime powering Sandpiper's agentic work [2026-07-02 Tallgrass-Halyard]. Delphine Ruiz confirmed that **the Tallgrass forge site's chat experience is being relaunched on the team's proper harness** within approximately one month — a signal that the Sandpiper team was actively consolidating its fragmented surfaces onto a unified foundation [2026-07-02 Tallgrass-Halyard]. This relaunch is directly relevant to the recon's harness-evaluation work: the Sandpiper harness the recon targets will be a different architecture than the current fragmented surfaces, and integration planning should account for the post-relaunch shape.

Marisol Trent also described a concrete large-scale agentic achievement within the Sandpiper team: landing an **85,000 LOC PR adding slide-deck, spreadsheet, and document support** to the Tallgrass app from scratch — describing it as "lots of bugs but shockingly good" [2026-07-02 Tallgrass-Halyard]. This demonstrates that the Sandpiper team has its own experience with large-scale agentic code generation, making them a peer in the long-horizon task space rather than simply a target for Halyard's techniques.

Dmitri Falk (July 6, 2026) praised the **Sandpiper desktop app** as *"a much better and very different experience from the other surfaces"* and expressed hope that the other interfaces would be folded into it [2026-07-06 Tallgrass-Halyard]. This desktop-app surface represents a qualitatively different integration target from the web chat or CLI surfaces — one where the harness integration model, UX expectations, and capability surface may differ significantly from what the recon has characterized to date.

## Open questions

Three open questions govern the recon's scope and output format [src: byo-agent-ecosystem-recon]:

- **Scope cap** — how many hosts to deeply investigate before diminishing returns? The working suggestion is 3–5 deep investigations plus a breadth scan of the rest.
- **Quantitative comparison** — whether to produce a comparison matrix (host × packaging × distribution × etc.) or a per-host writeup with cross-references.
- **Source access** — Quarry is internal to a partner org; external sources are unavailable without help locating them.

The recon also notes that this ecosystem is moving fast and recommends re-reading in one month [src: byo-agent-ecosystem-recon].

## Position in the Trailhead dependency map

Within the [[trailhead-workstream-index|Trailhead]] dependency map, the recon sits as the first stage of Halyard as Agent (recon → packaging → CLI distribution) [src: trailhead-INDEX]. It is upstream of the packaging and CLI distribution stages: the harness capabilities discovered here determine the adapter shape, which determines the packaging interface, which determines the install experience. Evaluations (Naomi's meta-capability) consumes the adapter work downstream for measurement, and Cost Management feeds cost telemetry into the CLI distribution stage [src: halyard-as-agent].

## Shared infrastructure with Halyard Scientist

The `halyard-app-benchmarks` harness extension work (Callum and Priya) that underpins this recon is also named as a dependency of the [[halyard-scientist|Halyard Scientist]] horizon project [src: halyard-scientist]. This means the eval harness infrastructure being built for the ecosystem recon and BYO-agent adapters is shared with the scientific evaluation framework Dmitri is developing.

## Sources

[^1]: halyard-as-agent.md
