---
title: Design and Promotion Workstream
type: concept
sources: [1, 2, 8, 9, 12, 16]
last_updated: 2026-07-07

---

# Design and Promotion Workstream

Design & Promotion is a Base Camp workstream owned by Alex, anchored at Alex's cross-pod Design championship [src: design-and-promotion]. Its activation is concurrent with Alex's return; the workstream was previously in a "slot held — pending return" state and is now active [src: design-and-promotion]. The workstream is described as new and expected to mature as Alex's scope sharpens [src: design-and-promotion].

## Scope

The workstream's core mission is **getting the team's packaged work in front of the right audiences** — accessibly, usefully, with the design care that makes adoption actually happen [src: design-and-promotion]. Three areas are in scope [src: design-and-promotion]:

- **Initial promotion of Amplifier as Agent** to the focused external community (BYO agent users, framework adopters, local-model audiences).
- **Internal promotion** to Microsoft-internal groups who could leverage the packaged versions of Amplifier-powered work.
- **Design care** applied to how the packaged work is presented, documented, and onboarded.

This is described as the first concrete activation of Alex's Design championship [src: design-and-promotion]. The workstream intersects directly with [[amplifier-as-agent-workstream|Amplifier as Agent Workstream]] (Base Camp; Salil + Manoj) — which is where the packaged work originates — and the Resolve App UX iteration (The Rig; Alex pairing with Gurkaran when Gurkaran frees up) [src: design-and-promotion].

## Why this work now

Three factors make Design & Promotion timely [src: design-and-promotion]:

- **Amplifier as Agent is racing toward release** — the moment a packaged version exists, promotion to community and internal groups is the next-mile work that turns "released" into "actually used."
- **Alex's Design championship activates** with concrete deliverables across the work being shipped this quarter.
- **The team's outputs need design care at the edges** — not as decoration, but as the discipline that turns capability into adoption.

## Cross-workstream dependencies

Design & Promotion sits at an integration point between the team's production workstreams and its external audiences [src: design-and-promotion].

**Consumes from:**

- **[[amplifier-as-agent-workstream|Amplifier as Agent Workstream]]** — the packaged work itself that is being promoted.
- **[[cost-management|Cost Management]]** — pricing and cost framing for accessible packaging.
- **Evaluations** (sub-workstream of [[engineering-velocity-workstream|Engineering Velocity Workstream]]) — credibility signal for community audiences (eval scores, benchmark results).

**Provides to:**

- **[[amplifier-as-agent-workstream|Amplifier as Agent Workstream]]** — feedback from real adopter use back into the package shape.
- **The Rig (Resolve App UX)** — design care across the App surface; Alex pairs with Gurkaran on this when both are available.
- **External community + internal Microsoft groups** — accessible entry points into the team's Amplifier-powered work.

## Resolve App UX role

Brian explicitly named Alex as the person to step in and improve the [[resolve-platform-core|Resolve App]] UX when the stack is running [2026-05-11 Resolve-UX-Onboarding]. The current state is functional but not polished — the architecture (outer frame + per-resolver viewport repos) is solid, so improvements are additive rather than structural rewrites. Brian's framing: *"If things go crazy this week and you don't even get to the point where we've got anything that anybody could use, not the end of the world, we have something. And when you come back again, like you can pick up from there and continue on."* [2026-05-11 Resolve-UX-Onboarding]

The first feature request from Salil on this call: light mode support. Brian's note: also verify dark mode, since a prior contributor shipped washed-out light-gray-on-lighter-gray contrast [2026-05-11 Resolve-UX-Onboarding]. Alex's response: both modes are table stakes and will be covered.

By May 13, Alex had the stack running and was actively executing [2026-05-13 Resolve-Jam]. His approach: run a **design intelligence audit** first to surface a structured list of improvements, then prioritize the changes most likely to prevent early user drop-off ("bounce points") [2026-05-13 Resolve-Jam]. Light/dark mode was the first applied change — chosen partly as a visible confirmation that changes were taking effect. Alex is exploring trying multiple approaches in parallel (up to three different design directions in a single day) to identify which works best before tomorrow's check-in [2026-05-13 Resolve-Jam].

Brian gave Alex explicit permission to redesign the entire `amplifier-app-resolve` frontend, including rethinking the viewport concept if a better model emerges [2026-05-13 Resolve-Jam]. Brian also suggested Alex consider using **Resolve itself (via understudy) to do UX work on Resolve** — the "use the tool to build the tool" pattern [2026-05-13 Resolve-Jam]. Gurkaran is Alex's primary pairing partner on the UX side; the two planned to sync at noon on May 13 to align on a prioritized list of improvements to target that day [2026-05-13 Resolve-Jam].

**Concrete UX issues surfaced (May 13):**
- Input request UI covers the viewport entirely, creating a loss-of-control feeling — Gurkaran's example of a blocking form that should not fully obscure the session [2026-05-13 Resolve-Jam].
- Sidebar clutter from accumulated instances (failed, running, completed) with no management affordance [2026-05-13 Resolve-Jam].
- Config screen ambiguity: required vs. optional fields are not clearly distinguished; onboarding flow is not clearly defined [2026-05-13 Resolve-Jam].
- Collapsed rail of limited use in current state [2026-05-13 Resolve-Jam].

## Alex's floating champion role

Brian characterized Alex as a **"design champion" available across workstreams** rather than dedicated to a single one [2026-05-12 Team-Catchup-Resolve]. The role operates as a floating resource: some weeks Alex is fully embedded in Resolve UX work (as in the May 12–13 sprint), other weeks he may be doing a round of design intelligence work that produces tools the broader team can use at design time [2026-05-12 Team-Catchup-Resolve]. Brian's framing: Alex brings a particular skill set and experience that didn't have its own dedicated workstream but is valuable across many workstreams — the champion slot exists to make that value available without over-committing Alex to one place [2026-05-12 Team-Catchup-Resolve].

A practical consequence: other team members and workstream leads will try to pull Alex into their work when they have design needs [2026-05-12 Team-Catchup-Resolve]. Brian's guidance to Alex: guard time and stay focused on the primary assignment (Resolve UX for the May sprint), doing only brief consulting for other requests rather than context-switching fully. Direction on what to prioritize comes from Brian; Alex should treat requests from other team members as advisory unless Brian confirms them [2026-05-12 Team-Catchup-Resolve].

A future planned investment: revisiting Alex's **design intelligence** work with newer techniques developed in the preceding weeks, then packaging it so the rest of the team can use it at design time with zero cost on a "hello world" — a version that is less discoverable but can be included everywhere, with a more discoverable version available selectively [2026-05-12 Team-Catchup-Resolve].

## Alex's May 18 sprint framing

In the May 18 sprint planning meeting, Alex walked Brian through his thinking on the Resolve frame and UX architecture [2026-05-18 Resolve-Sprint-Plan]. Key elements:

**Three UI states.** Alex was working from a framework of three distinct states for the Resolve surface [2026-05-18 Resolve-Sprint-Plan]:
1. **Base surface** — what's running, observable; the dominant mode when arriving at the surface
2. **Acting space** — going into a specific resolver (understudy, dot-graph, etc.); base-surface content rolls up and out of the way; other instances remain as a lightweight indicator
3. **Peak** — a third, deeper state still being designed

**Design aesthetic: "tech enlightened humanism."** Alex described trying to distill a design aesthetic that is *"reliable, not sterile structure, but not bland"* — a quality he called "tech enlightened humanism" [2026-05-18 Resolve-Sprint-Plan]. The intent: qualities that would be consistent regardless of whether the user is Brian, Sam, or anyone else — not tied to a specific font or color, but to expression patterns. Brian's guidance: don't invest heavily in visual style right now ("throw a dart, pick one — slightly better than random"), but do think about the adaptive attention-management layer as the real design investment [2026-05-18 Resolve-Sprint-Plan].

**Scope for the sprint.** Alex had today (May 18), tomorrow, and Wednesday before going out for approximately two weeks [2026-05-18 Resolve-Sprint-Plan]. Brian committed to making himself available for 30-minute check-ins as needed to keep Alex moving — grab a slot in the afternoon, first thing in the morning, or whenever needed [2026-05-18 Resolve-Sprint-Plan]. The sprint goal was to deliver a framework for hosting observability and wrapping the Resolve experience, with design perspective on the three-state model.

**Viewport vs. system boundary clarity.** Alex noted that working through the current system, he was observing that things he expected to change didn't change because they were controlled in different spots or the viewport was different — which helped him understand what is part of a particular viewport vs. part of the system itself [2026-05-18 Resolve-Sprint-Plan]. This boundary clarity was a key output of the sprint work.

## June 29 UX push — outcomes-first design approach

In the June 29, 2026 catch-up (Alex Lopez, Ken Chau, Marc Goodner, Gurkaran Singh), Alex returned from an extended period of intermittent engagement and joined a focused four-day UX push [2026-06-29 Alex-Resolve-UX-Push]. Alex framed his design process explicitly: he goes wide first ("boiling the ocean") to understand the full space, then zooms in on the right things — Marc had defended this as Alex's natural design process rather than a distraction [2026-06-29 Alex-Resolve-UX-Push].

Alex's key methodological stance: to design the right UX, he needs to understand the *outcomes* the team is trying to drive with Resolve and resolvers — not the internal resolver taxonomy or platform architecture [2026-06-29 Alex-Resolve-UX-Push]. His framing: *"I want to know if I can understand what outcomes we're trying to drive with resolve and resolvers, then I can reverse engineer that from a design perspective and say, okay, what are the things that need to be there?"* This outcomes-first approach produces a philosophy about how the UX lays out, which then drives execution decisions [2026-06-29 Alex-Resolve-UX-Push].

Alex also raised the fundamental question of whether the UX artifacts being built for the Resolve surface are intended to be used as-is or to be lifted and embedded in Team Pulse — because the answer changes what to prioritize in design [2026-06-29 Alex-Resolve-UX-Push]. The team's response: both, with the Resolve UI serving as an operator/admin surface and embeddable UX pieces surfacing into Team Pulse and other end-user surfaces (see [[resolve-platform-core|Resolve Platform Core]] for the full architectural debate).

**Use of external tools.** Ken explicitly told Alex to feel free to use Claude Designer or any other external tool if it helps the UX work — citing Paul's lesson from the preceding Thursday (Paul had been churning on an Amplifier bug and made progress by pointing Claude Code at it directly) [2026-06-29 Alex-Resolve-UX-Push]. The team's stance: use whatever tool is best for the task, even if it's not an Amplifier-built tool. Alex acknowledged he had pulled back from external tools and needed to re-engage with them [2026-06-29 Alex-Resolve-UX-Push].

**Working approach for the sprint.** Alex committed to not going dark: checking in the next morning with what he had processed, where he was at, and potentially early design explorations [2026-06-29 Alex-Resolve-UX-Push]. Ken's ask: throw any mockups or directional work into the team chat immediately for rapid feedback — the creative process benefits from fast reaction loops even when work is still directional [2026-06-29 Alex-Resolve-UX-Push]. Alex had been using Resolve as a backend in his own work (running dot graphs before his Mac mini update broke things) and planned to reconnect to that hands-on experience as a design input [2026-06-29 Alex-Resolve-UX-Push].

## Design quality evaluation for dynamic surfaces (June 2026)

In late June 2026, the team converged on a cluster of related design quality ideas through the Amp-Up team chat [2026-06-29 Amp-Up]. MJ Jabbour built a personal tool called **Page Worth** — a rubric for evaluating page design quality — and shared it with the team as a potential Amplifier integration candidate [2026-06-29 Amp-Up]. Mollie Munoz noted that **Impeccable** (added as a VS Code/GitHub extension) was implementing static analysis as a hook — regex and DOM-based checks — similar to how Lighthouse runs, and was useful but limited to static page analysis [2026-06-29 Amp-Up].

Alex Lopez re-engaged with these ideas after returning from an extended period and articulated the key distinction his design intelligence work was targeting: *"what does design quality evaluation mean when the design surface is dynamic?"* [2026-06-29 Amp-Up] The direction is away from static page analysis ("hide the slop") and toward a deeper level of integration and intelligence that makes the whole system stronger — anticipating that static analysis will soon be table stakes. Alex proposed renaming the next iteration of design intelligence enhanced to **Studious** and framed it as an opportunity to collaborate with MJ [2026-06-29 Amp-Up]. MJ proposed a **design loop** as the mechanism, and Mollie expressed interest in joining the collaboration [2026-06-29 Amp-Up].

This conversation connects to the **design intelligence repackaging** open question (item 8 below): the Studious concept is the candidate form for the next-generation design intelligence tool, oriented toward dynamic surface evaluation rather than static analysis.

## MJ's Amplifier conformance bundle (June 2026)

MJ Jabbour created and published `amplifier-bundle-conformance` — a compliance bundle for auditing whether work follows "the Amplifier way" — to reduce the need for manual conformance checking in sessions [2026-06-29 Amp-Up]. The bundle is available at `github.com/michaeljabbour/amplifier-bundle-conformance` and is designed to be invoked in Amplifier sessions to audit bundle recipes and practices for conformance with Amplifier's design patterns [2026-06-29 Amp-Up]. This is a concrete instantiation of the design championship role: packaging design expertise so the team can use it at design time with minimal friction.

## Amplifier Agent website and community storytelling (July 2026)

The July 7, 2026 planning session (Brian, David, Alex, Salil) activated Alex's role in the Amplifier Agent release push with a specific new deliverable: **a simple marketing website** for Amplifier Agent [2026-07-07 Agent-Planning]. The team's diagnosis was that the existing repo README, while comprehensive, is too technical and too comprehensive for the target audience — it lists all the different ways to use Amplifier Agent and all the things it can do, but does not communicate a clear, simple call to action.

Alex's framing of the challenge: prior Amplifier marketing efforts felt open-ended — *"okay, great, I want to get started. How do I do that?"* — and the team needed to hone in on who the target users are and what they want to do before designing the site [2026-07-07 Agent-Planning]. His approach: rather than trying to communicate that Amplifier is for everybody, focus on a **general user profile** that helps clarify which people to optimize the site for (see [[amplifier-opencode-integration|Amplifier Agent and OpenCode Integration]] for the user profile the team converged on).

The site's primary promotion target is **Amplifier Agent running under OpenCode** — the most accessible entry point, requiring no deep understanding of bundles or harness internals [2026-07-07 Agent-Planning]. From there, the site can link to the Amplifier Agent layer itself as something users can embed in other harnesses (NanoClaw, Paperclip, custom builds). Brian's framing: *"we can actually educate a little bit potentially on harness and go like, look, it's more than the model, it's what you bring"* [2026-07-07 Agent-Planning].

Alex also raised a key messaging question: whether the site should explicitly communicate what users are *missing out on* by using Claude or ChatGPT as a harness directly — shifting from activity-based to outcome-based framing. The team agreed this was the right angle: those systems are still very activity-based, whereas Amplifier Agent is designed to push past the activity loop and actually achieve outcomes [2026-07-07 Agent-Planning].

The team planned to test the "Amplifier Agent" name and the site's messaging on the WhatsApp community group (Jesse, Dans, Joyito, and others) before committing — getting feedback from people who are not already inside the team's mental model [2026-07-07 Agent-Planning].

## Open questions

The following questions remain unresolved as of the source [src: design-and-promotion]:

1. **Focused community definition** — which subset of the external community is the first audience?
2. **Internal-group identification** — which Microsoft-internal groups are prioritized for first-wave promotion?
3. **Design championship operating shape** — anchored at Alex; how does it reach into the other pods' work without overhead-loading Alex? Brian's May 12 answer: time-guard and treat other requests as advisory [2026-05-12 Team-Catchup-Resolve].
4. **Promotion-materials format** — docs, video, demo, blog, conference?
5. **Resolve App UX pairing timing** — Alex active as of May 13; pairing with Gurkaran when available; Gurkaran's orchestrator work is the competing priority [2026-05-13 Resolve-Jam].
6. **Viewport redesign scope** — Alex has permission to redesign the entire frontend including the viewport concept; how far does the redesign go before it becomes a structural rewrite vs. additive improvement? [2026-05-13 Resolve-Jam]
7. **Use-Resolve-to-build-Resolve** — how much of Alex's UX work can be done via understudy vs. direct coding? [2026-05-13 Resolve-Jam]
8. **Design intelligence repackaging** — when does the team revisit Alex's design intelligence work with newer techniques to produce a zero-cost team-wide tool? [2026-05-12 Team-Catchup-Resolve]

## Sources

[^12]: design-and-promotion.md
