---
title: Design and Promotion Workstream
type: concept
sources: [1, 2, 8, 9, 12, 16]
last_updated: 2026-06-29

---

# Design and Promotion Workstream

Design & Promotion is a Trailhead workstream owned by Renée Okafor, anchored at Renée's cross-pod Design championship [src: design-and-promotion]. Its activation is concurrent with Renée's return; the workstream was previously in a "slot held — pending return" state and is now active [src: design-and-promotion]. The workstream is described as new and expected to mature as Renée's scope sharpens [src: design-and-promotion].

## Scope

The workstream's core mission is **getting the team's packaged work in front of the right audiences** — accessibly, usefully, with the design care that makes adoption actually happen [src: design-and-promotion]. Three areas are in scope [src: design-and-promotion]:

- **Initial promotion of Halyard as Agent** to the focused external community (bring-your-own-agent users, framework adopters, local-model audiences).
- **Internal promotion** to partner engineering groups who could leverage the packaged versions of Halyard-powered work.
- **Design care** applied to how the packaged work is presented, documented, and onboarded.

This is described as the first concrete activation of Renée's Design championship [src: design-and-promotion]. The workstream intersects directly with [[halyard-as-agent-workstream|Halyard as Agent Workstream]] (Trailhead; Callum + Priya) — which is where the packaged work originates — and the Atrium App UX iteration (The Yard; Renée pairing with Yusuf when Yusuf frees up) [src: design-and-promotion].

## Why this work now

Three factors make Design & Promotion timely [src: design-and-promotion]:

- **Halyard as Agent is racing toward release** — the moment a packaged version exists, promotion to community and internal groups is the next-mile work that turns "released" into "actually used."
- **Renée's Design championship activates** with concrete deliverables across the work being shipped this quarter.
- **The team's outputs need design care at the edges** — not as decoration, but as the discipline that turns capability into adoption.

## Cross-workstream dependencies

Design & Promotion sits at an integration point between the team's production workstreams and its external audiences [src: design-and-promotion].

**Consumes from:**

- **[[halyard-as-agent-workstream|Halyard as Agent Workstream]]** — the packaged work itself that is being promoted.
- **[[cost-management|Cost Management]]** — pricing and cost framing for accessible packaging.
- **Evaluations** (sub-workstream of [[engineering-velocity-workstream|Engineering Velocity Workstream]]) — credibility signal for community audiences (eval scores, benchmark results).

**Provides to:**

- **[[halyard-as-agent-workstream|Halyard as Agent Workstream]]** — feedback from real adopter use back into the package shape.
- **The Yard (Atrium App UX)** — design care across the App surface; Renée pairs with Yusuf on this when both are available.
- **External community + internal partner groups** — accessible entry points into the team's Halyard-powered work.

## Atrium App UX role

Hollis explicitly named Renée as the person to step in and improve the [[atrium-platform-core|Atrium App]] UX when the stack is running [2026-05-11 Atrium-UX-Onboarding]. The current state is functional but not polished — the architecture (outer frame + per-resolver viewport repos) is solid, so improvements are additive rather than structural rewrites. Hollis's framing: *"If things go sideways this week and you don't even get to the point where we've got anything that anybody could use, not the end of the world, we have something. And when you come back again, you can pick up from there and continue on."* [2026-05-11 Atrium-UX-Onboarding]

The first feature request from Callum on this call: light mode support. Hollis's note: also verify dark mode, since a prior contributor shipped washed-out light-gray-on-lighter-gray contrast [2026-05-11 Atrium-UX-Onboarding]. Renée's response: both modes are table stakes and will be covered.

By May 13, Renée had the stack running and was actively executing [2026-05-13 Atrium-Jam]. Her approach: run a **design intelligence audit** first to surface a structured list of improvements, then prioritize the changes most likely to prevent early user drop-off ("bounce points") [2026-05-13 Atrium-Jam]. Light/dark mode was the first applied change — chosen partly as a visible confirmation that changes were taking effect. Renée is exploring trying multiple approaches in parallel (up to three different design directions in a single day) to identify which works best before tomorrow's check-in [2026-05-13 Atrium-Jam].

Hollis gave Renée explicit permission to redesign the entire `halyard-app-atrium` frontend, including rethinking the viewport concept if a better model emerges [2026-05-13 Atrium-Jam]. Hollis also suggested Renée consider using **Atrium itself (via standin) to do UX work on Atrium** — the "use the tool to build the tool" pattern [2026-05-13 Atrium-Jam]. Yusuf is Renée's primary pairing partner on the UX side; the two planned to sync at noon on May 13 to align on a prioritized list of improvements to target that day [2026-05-13 Atrium-Jam].

**Concrete UX issues surfaced (May 13):**
- Input request UI covers the viewport entirely, creating a loss-of-control feeling — Yusuf's example of a blocking form that should not fully obscure the session [2026-05-13 Atrium-Jam].
- Sidebar clutter from accumulated instances (failed, running, completed) with no management affordance [2026-05-13 Atrium-Jam].
- Config screen ambiguity: required vs. optional fields are not clearly distinguished; onboarding flow is not clearly defined [2026-05-13 Atrium-Jam].
- Collapsed rail of limited use in current state [2026-05-13 Atrium-Jam].

## Renée's floating champion role

Hollis characterized Renée as a **"design champion" available across workstreams** rather than dedicated to a single one [2026-05-12 Team-Catchup-Atrium]. The role operates as a floating resource: some weeks Renée is fully embedded in Atrium UX work (as in the May 12–13 sprint), other weeks she may be doing a round of design intelligence work that produces tools the broader team can use at design time [2026-05-12 Team-Catchup-Atrium]. Hollis's framing: Renée brings a particular skill set and experience that didn't have its own dedicated workstream but is valuable across many workstreams — the champion slot exists to make that value available without over-committing Renée to one place [2026-05-12 Team-Catchup-Atrium].

A practical consequence: other team members and workstream leads will try to pull Renée into their work when they have design needs [2026-05-12 Team-Catchup-Atrium]. Hollis's guidance to Renée: guard time and stay focused on the primary assignment (Atrium UX for the May sprint), doing only brief consulting for other requests rather than context-switching fully. Direction on what to prioritize comes from Hollis; Renée should treat requests from other team members as advisory unless Hollis confirms them [2026-05-12 Team-Catchup-Atrium].

A future planned investment: revisiting Renée's **design intelligence** work with newer techniques developed in the preceding weeks, then packaging it so the rest of the team can use it at design time with zero cost on a "hello world" — a version that is less discoverable but can be included everywhere, with a more discoverable version available selectively [2026-05-12 Team-Catchup-Atrium].

## Renée's May 18 sprint framing

In the May 18 sprint planning meeting, Renée walked Hollis through her thinking on the Atrium frame and UX architecture [2026-05-18 Atrium-Sprint-Plan]. Key elements:

**Three UI states.** Renée was working from a framework of three distinct states for the Atrium surface [2026-05-18 Atrium-Sprint-Plan]:
1. **Base surface** — what's running, observable; the dominant mode when arriving at the surface
2. **Acting space** — going into a specific resolver (standin, dot-graph, etc.); base-surface content rolls up and out of the way; other instances remain as a lightweight indicator
3. **Peak** — a third, deeper state still being designed

**Design aesthetic: "quiet instrumentation."** Renée described trying to distill a design aesthetic that is *"reliable, structured without being sterile, but not bland"* — a quality she called "quiet instrumentation" [2026-05-18 Atrium-Sprint-Plan]. The intent: qualities that would be consistent regardless of which operator is at the surface — not tied to a specific font or color, but to expression patterns. Hollis's guidance: don't invest heavily in visual style right now ("throw a dart, pick one — slightly better than random"), but do think about the adaptive attention-management layer as the real design investment [2026-05-18 Atrium-Sprint-Plan].

**Scope for the sprint.** Renée had today (May 18), tomorrow, and Wednesday before going out for approximately two weeks [2026-05-18 Atrium-Sprint-Plan]. Hollis committed to making himself available for 30-minute check-ins as needed to keep Renée moving — grab a slot in the afternoon, first thing in the morning, or whenever needed [2026-05-18 Atrium-Sprint-Plan]. The sprint goal was to deliver a framework for hosting observability and wrapping the Atrium experience, with design perspective on the three-state model.

**Viewport vs. system boundary clarity.** Renée noted that working through the current system, she was observing that things she expected to change didn't change because they were controlled in different spots or the viewport was different — which helped her understand what is part of a particular viewport vs. part of the system itself [2026-05-18 Atrium-Sprint-Plan]. This boundary clarity was a key output of the sprint work.

## June 29 UX push — outcomes-first design approach

In the June 29, 2026 catch-up (Renée Okafor, Beatrix Sandoval, Ivo Petran, Yusuf Rahimi), Renée returned from an extended period of intermittent engagement and joined a focused four-day UX push [2026-06-29 Renee-Atrium-UX-Push]. Renée framed her design process explicitly: she goes wide first ("boiling the ocean") to understand the full space, then zooms in on the right things — Ivo had defended this as Renée's natural design process rather than a distraction [2026-06-29 Renee-Atrium-UX-Push].

Renée's key methodological stance: to design the right UX, she needs to understand the *outcomes* the team is trying to drive with Atrium and resolvers — not the internal resolver taxonomy or platform architecture [2026-06-29 Renee-Atrium-UX-Push]. Her framing: *"I want to know if I can understand what outcomes we're trying to drive with Atrium and resolvers, then I can reverse engineer that from a design perspective and say, okay, what are the things that need to be there?"* This outcomes-first approach produces a philosophy about how the UX lays out, which then drives execution decisions [2026-06-29 Renee-Atrium-UX-Push].

Renée also raised the fundamental question of whether the UX artifacts being built for the Atrium surface are intended to be used as-is or to be lifted and embedded in Tessera — because the answer changes what to prioritize in design [2026-06-29 Renee-Atrium-UX-Push]. The team's response: both, with the Atrium UI serving as an operator/admin surface and embeddable UX pieces surfacing into Tessera and other end-user surfaces (see [[atrium-platform-core|Atrium Platform Core]] for the full architectural debate).

**Use of external tools.** Beatrix explicitly told Renée to feel free to use Stitchwork Designer or any other external tool if it helps the UX work — citing the lesson from the preceding Thursday, when a teammate had been churning on a Halyard bug and made progress by pointing an off-the-shelf coding agent at it directly [2026-06-29 Renee-Atrium-UX-Push]. The team's stance: use whatever tool is best for the task, even if it's not a Halyard-built tool. Renée acknowledged she had pulled back from external tools and needed to re-engage with them [2026-06-29 Renee-Atrium-UX-Push].

**Working approach for the sprint.** Renée committed to not going dark: checking in the next morning with what she had processed, where she was at, and potentially early design explorations [2026-06-29 Renee-Atrium-UX-Push]. Beatrix's ask: throw any mockups or directional work into the team chat immediately for rapid feedback — the creative process benefits from fast reaction loops even when work is still directional [2026-06-29 Renee-Atrium-UX-Push]. Renée had been using Atrium as a backend in her own work (running dot graphs before a workstation update broke things) and planned to reconnect to that hands-on experience as a design input [2026-06-29 Renee-Atrium-UX-Push].

## Design quality evaluation for dynamic surfaces (June 2026)

In late June 2026, the team converged on a cluster of related design quality ideas through the Loop-In team chat [2026-06-29 Loop-In]. Dmitri Falk built a personal tool called **Surface Score** — a rubric for evaluating page design quality — and shared it with the team as a potential Halyard integration candidate [2026-06-29 Loop-In]. Sofia Larkin noted that **Glassplate** (added as an editor/forge extension) was implementing static analysis as a hook — regex and DOM-based checks — similar to how a page-audit tool runs, and was useful but limited to static page analysis [2026-06-29 Loop-In].

Renée Okafor re-engaged with these ideas after returning from an extended period and articulated the key distinction her design intelligence work was targeting: *"what does design quality evaluation mean when the design surface is dynamic?"* [2026-06-29 Loop-In] The direction is away from static page analysis ("hide the slop") and toward a deeper level of integration and intelligence that makes the whole system stronger — anticipating that static analysis will soon be table stakes. Renée proposed renaming the next iteration of design intelligence enhanced to **Meticulous** and framed it as an opportunity to collaborate with Dmitri [2026-06-29 Loop-In]. Dmitri proposed a **design loop** as the mechanism, and Sofia expressed interest in joining the collaboration [2026-06-29 Loop-In].

This conversation connects to the **design intelligence repackaging** open question (item 8 below): the Meticulous concept is the candidate form for the next-generation design intelligence tool, oriented toward dynamic surface evaluation rather than static analysis.

## Dmitri's Halyard conformance bundle (June 2026)

Dmitri Falk created and published `halyard-bundle-conformance` — a compliance bundle for auditing whether work follows "the Halyard way" — to reduce the need for manual conformance checking in sessions [2026-06-29 Loop-In]. The bundle is available at `forge.example.net/dfalk/halyard-bundle-conformance` and is designed to be invoked in Halyard sessions to audit bundle recipes and practices for conformance with Halyard's design patterns [2026-06-29 Loop-In]. This is a concrete instantiation of the design championship role: packaging design expertise so the team can use it at design time with minimal friction.

## Open questions

The following questions remain unresolved as of the source [src: design-and-promotion]:

1. **Focused community definition** — which subset of the external community is the first audience?
2. **Internal-group identification** — which partner engineering groups are prioritized for first-wave promotion?
3. **Design championship operating shape** — anchored at Renée; how does it reach into the other pods' work without overhead-loading Renée? Hollis's May 12 answer: time-guard and treat other requests as advisory [2026-05-12 Team-Catchup-Atrium].
4. **Promotion-materials format** — docs, video, demo, blog, conference?
5. **Atrium App UX pairing timing** — Renée active as of May 13; pairing with Yusuf when available; Yusuf's orchestrator work is the competing priority [2026-05-13 Atrium-Jam].
6. **Viewport redesign scope** — Renée has permission to redesign the entire frontend including the viewport concept; how far does the redesign go before it becomes a structural rewrite vs. additive improvement? [2026-05-13 Atrium-Jam]
7. **Use-Atrium-to-build-Atrium** — how much of Renée's UX work can be done via standin vs. direct coding? [2026-05-13 Atrium-Jam]
8. **Design intelligence repackaging** — when does the team revisit Renée's design intelligence work with newer techniques to produce a zero-cost team-wide tool? [2026-05-12 Team-Catchup-Atrium]

## Sources

[^12]: design-and-promotion.md
