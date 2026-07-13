---
title: Generative UI and Ephemeral Interfaces
type: concept
sources: [1, 2, 3]
last_updated: 2026-07-09
---

# Generative UI and Ephemeral Interfaces

Gurkaran Singh's exploration of **generative UI** — interfaces generated on demand from intent, dissolving when no longer needed — presented at the April 30, 2026 Show and Tell [2026-04-30 Show-and-Tell-MADE]. The work spans a thesis about why software looks the way it does, a set of demos, and an emerging design vocabulary for the team.

## The architectural inversion thesis

The core hypothesis: **the architecture underneath software is inverting** [2026-04-30 Show-and-Tell-MADE].

| Old model | New model |
|---|---|
| Apps are durable | **Data is durable** |
| Data lives inside apps | **Interfaces are ephemeral layers on top of data** |
| Interfaces authored once, for everyone | **Interfaces generated just-in-time, dissolvable when done** |

The historical shape of software was set by **economics**: the authoring cost of software was high relative to any specific need, so the only way to amortize the cost was the largest common denominator — producing "behemoth softwares" that users learned to adapt to, which Gurkaran characterized as a sign of human flexibility rather than actual fit [2026-04-30 Show-and-Tell-MADE].

The unique moment now: the cost of writing code is falling, and the cost of specifying what you want is falling. This is what's different from earlier experiments with malleable software (HyperCard at Apple, Zapier, Airtable, and many others over 50 years) that explored similar ideas but couldn't achieve them economically [2026-04-30 Show-and-Tell-MADE].

## Three kinds of software

Gurkaran's framework: ephemeral interfaces make sense across the board for **transactional software** (scheduling, retrieval, status checks) but not uniformly [2026-04-30 Show-and-Tell-MADE]:

1. **Transactional** — ephemeral interfaces make the most sense here; no shared state, no collaboration complexity.
2. **Shared / collaborative** — multiple users, shared state; more complex to generate ephemerally.
3. **Creative / expressive** — tools where the interface *is* the medium; ephemeral generation may be less appropriate.

## Apps as lenses, not containers

The framing that emerged: **apps aren't containers, they're lenses** [2026-04-30 Show-and-Tell-MADE]. A lens is a view over a pool of data — generated on demand, optimized for a specific purpose or moment, not a permanent structure that data lives inside. Team Pulse is cited as an example of this pattern already emerging: pulling context from multiple sources into one place and generating ephemeral views on top of it.

## Ambient and contextual interface inspiration

The conversation at the April 30 Show and Tell opened with a discussion of ambient interfaces as a long-standing design aspiration. One participant recalled the movie *Antitrust* (2001), in which the Bill Gates-equivalent character's house changed its artwork to match the style and preference of whoever moved through each room [2026-04-30 Show-and-Tell-MADE]. The appeal: *"having things in the background that are complementary to what's going on in the environment"* — an interface that personalizes to presence and context without requiring explicit input. This vision of ambient, presence-aware surfaces is a direct ancestor of the generative UI thesis: if the interface can sense who is present and what they need, it can surface complementary content without being asked.

Another participant described using a similar approach with their children: exploring aspirations and the kinds of jobs that could exist in areas they cared about — a task that would benefit from an interface that surfaces relevant examples dynamically, rather than requiring navigation of a static structure [2026-04-30 Show-and-Tell-MADE].

## Demos shown

**Personal app ecosystem** — a set of lightweight apps over a shared personal data pool (health data, workout tracker, location history, interview/knowledge-gathering app). The key observation: when all data is together, you can *"send AI on missions"* — ask it to find correlations across health signals, for example [2026-04-30 Show-and-Tell-MADE]. The apps use a GitHub Commit-style visual pattern for tracking things over time.

**Dynamic reader app** — a reader interface that evolves through voice: *"show me what I can read in 5 minutes"* filters the page dynamically; *"summarize this"* annotates in place; *"annotate"* adds context. The interface evolves on the fly without clicking. Breadcrumb history at the bottom allows reverting to prior interface states [2026-04-30 Show-and-Tell-MADE].

**Ambient listening desktop app** — a desktop app with ambient listening (no wake word required) that generates interfaces as a side effect of conversation [2026-04-30 Show-and-Tell-MADE]. Demonstrated: asking for weather in Seattle, asking to design a French cafe concept (logo, mockup). The framing: *"conversation is becoming a primary medium and an interface can be seen as a side effect."*

**flipbook.page** — Gurkaran shared an external project from South Park Commons: an interface where *"every pixel is generated"* [2026-04-30 Show-and-Tell-MADE]. Clicking on Notre Dame zooms in; the system interpolates using a vision model, showing people moving around in the scene. Users can take actions within the generated space. Gurkaran described it as *"visually really, really interesting"* and emblematic of the idea that *"basically everything becomes generated pixels."* Sam Schillace noted he had seen it before and called it *"pretty."* The project is still figuring out its full interaction model but illustrates the pixel-generation direction at a different scale than the demos above.

**Muse — a name spanning multiple, unrelated projects.** "Muse" refers to at least three distinct projects across different teams and companies, a naming collision that surfaced explicitly in a July 9, 2026 team discussion of provider routing [2026-07-09 Amp-Up]. In this generative-UI context, Diego Colombo mentioned Microsoft Research's *Muse*: a world generator engine in which the player has no pre-authored world — the system emits pixels in response to controller actions [2026-04-30 Show-and-Tell-MADE]. Diego described it as *"more along these lines"* than a traditional game engine, and suggested connecting with the MSR group working on it. Gurkaran noted he is not actively investing in this space but found it relevant to future directions. Diego framed Muse as an example of using a generative model as a world generator — the kind of behavior that would allow fully generated environments.

> TODO-VERIFY: The Muse / Xbox MSR project is described by Diego Colombo in the transcript as a Microsoft Research preview; independent verification of current project status was not available in the source.

### Muse variants and disambiguation

The July 9, 2026 discussion arose when MJ Jabbour proposed creating a "MUSE adapter" alongside OpenAI provider support, prompting Salil Das to note the name collision directly: *"when I hear Muse — I hear the Meta/FB thing"*, linking to Meta's Muse Spark model API announcement (`ai.meta.com/blog/introducing-muse-spark-meta-model-api`) [1]. Diego Colombo then enumerated the collision explicitly: *"Muse at the moment is at least: world generator model from MSR, the AI assistant in Unity3D, [and a] Meta model"* [1]. The three variants, as identified in the source:

1. **MSR world-generator model** (Microsoft Research / Xbox) — the game-world generation engine described above, first surfaced in the April 30 Show and Tell.
2. **AI assistant in Unity3D** — a separate Muse-named project functioning as an in-engine AI assistant for the Unity3D development environment [1].
3. **Meta model ("Muse Spark")** — Meta's model API, referenced by Salil Das via Meta's own blog announcement [1].

Salil also raised an open, unresolved procurement question in the same thread — whether the team currently has access to Muse APIs at all — which MJ Jabbour committed to testing personally and submitting a procurement request for the following week [1]. This practical uncertainty (which Muse, and whether any Muse API access exists) sits alongside the naming disambiguation as a second open question from the same conversation.

## Generation and oversight as parallel responses to attention scarcity

Gurkaran articulated a key insight at the close of the presentation: **generation and oversight are not separate problems — they are the same response to the same underlying constraint** [2026-04-30 Show-and-Tell-MADE].

The parallel:

| | Generation | Oversight |
|---|---|---|
| **Framing** | A lens that surfaces things relevant to your current intent | A view that surfaces things that require your judgment |
| **Root problem** | Attention scarcity — humans shouldn't navigate static interfaces | Attention scarcity — humans shouldn't review everything an agent does |
| **Response** | Bring the surface to where the human is; personalize it | Surface what matters; filter the rest |
| **Failure mode** | Every interaction is disorienting — no consistency | Indiscriminate logging — everything surfaces for attention |

The failure modes mirror each other: generation done badly produces disorientation through inconsistency; oversight done badly produces the equivalent of log noise — indiscriminate surfacing that burns attention without delivering judgment value [2026-04-30 Show-and-Tell-MADE].

Sam Schillace noted he is working on exactly this problem with his book generator: directing the model to *"optimize for my attention"* as an explicit instruction, and building an editing-pass layer that fine-tunes to reduce false positives and false negatives — so the system doesn't surface things that don't need human judgment, and doesn't miss things that do [2026-04-30 Show-and-Tell-MADE]. Another participant described an orchestrator running at the orchestration level that forces these kinds of attention loops, operating above the tool-call layer [2026-04-30 Show-and-Tell-MADE]. Sam characterized the calibration challenge as *"really hard to get it to feel right."*

This convergence connects directly to the [[attention-managed-command-center|Attention-Managed Command Center]]'s foundational principle that *"every surfacing is a transaction"* — the system borrows a quantum of attention and must return value in the same gesture. Generation and oversight are two implementations of the same discipline.

## Relationship to agents and non-human UIs

A key observation: current software interfaces are **not suited to agents** any more than they are to every human [2026-04-30 Show-and-Tell-MADE]. Agents using Playwright and browser automation are working around interfaces designed for humans. The big labs are improving browser use through RL, but semi-structured or unstructured data is naturally better for models. Ephemeral interfaces generated for agents (not just humans) is an open direction.

## Design discussion: voice vs. button

Devis Lucato raised the question of whether purely voice-driven interfaces create friction for repeated actions — would users want contextually generated shortcuts (buttons) for things they do often, rather than having to say the same phrase repeatedly [2026-04-30 Show-and-Tell-MADE]? Gurkaran noted he had been enjoying the no-click format, but the discussion surfaced a hybrid model: voice for intent, generated shortcuts for repeated actions. Devis's synthesis: *"as we generate these apps on the fly, it could generate shortcuts on the fly"* — contextual, readily available alongside voice.

## Relationship to team work

The generative UI direction connects to several concurrent threads [2026-04-30 Show-and-Tell-MADE]:

- **[[attention-managed-command-center|Attention-Managed Command Center]]** — the shared design vocabulary for Amplifier-powered surfaces already includes "generate UI after the data exists" as a foundational principle; Gurkaran's work is a concrete instantiation. The generation↔oversight parallelism Gurkaran articulated maps directly onto the command center's attention-first design.
- **[[integration-shell|Integration Shell]]** — Devis's 3-4 month roadmap includes "UI generation / dynamic apps — interfaces composed on demand from intent," directly convergent with this direction.
- **[[team-pulse|Team Pulse]]** — cited by Gurkaran as an existing example of the lenses pattern.
- **Understudy resolver** — Gurkaran mentioned an uber session running on Resolve as part of his ongoing work.

## Sources

[^2]: Show and Tell MADE (rec-2026-04-30) — Gurkaran Singh's generative UI presentation
- [1] Amp-Up team chat (2026-07-09) — MJ Jabbour, Salil Das, Diego Colombo — Muse naming disambiguation across MSR world-generator, Unity3D AI assistant, and Meta Muse Spark
