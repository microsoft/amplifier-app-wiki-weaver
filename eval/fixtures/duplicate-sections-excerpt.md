---
title: "Signal Deck"
type: source
---

# Signal Deck

Renata Ossovski described two parallel tracks: Yusuf Adeyemi helping with Loomwright (the data pipeline side), and Tomas Berglund focused on getting all the data into Signal Deck. Renata also clarified the architectural division: Signal Deck is responsible for figuring out the right data, schedule, and triggers, then pushing that into Ferryman work tasks — rather than Ferryman having connectors into everything directly. (Renata O - Crew Sync__rec-2026-07-30-1219-0szk0x__pulled-2026-08-07-1441.transcript.md)

This framing is consistent with the corpus's earlier records of Signal Deck as a coordination hub, but is the clearest on-record statement that the current engineering investment is specifically about removing human gates from the data pipeline — not UX or new features.

See [[source-renata-o-crew-sync-2026-07-30-1219|Renata O Crew Sync — 2026-07-30 (12:19 PM) — Working-Mode Shift, Tractor-as-Intended, and Signal Deck Automation]] for the full session record.

## Positioning Debate — "Meet Crews Where They Are" vs. "Reshape the Crew" (2026-07-24)

Priya Raghavan raised a key strategic question in the 2026-07-24 weekly planning chat: **Can Signal Deck help crews that already run like the Platform Group run better** (serving already-advanced crews) **vs. can Signal Deck reshape a crew where they are to run like the Platform Group?** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

This is a product-market-fit question with implications for features, onboarding, and target customers. The question was raised but **not resolved** in this session. It is the first on-record articulation of this specific framing tension.

## Organizational Wisdom as Signal Deck's Distinctive Value (2026-07-24)

Priya Raghavan articulated what she saw as Signal Deck's most powerful and underexperienced capability:

> "One of the most powerful things I see Signal Deck bringing that people have not really experienced is bringing organizational wisdom (different than knowledge or 'insights') which I don't feel like we've ever experienced in any technology that has tried to achieve this (e.g. the engagement-dashboard category)."

She framed this as something that — once demonstrated clearly — would be very convincing. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Loomwright as the Most Important Under-the-Hood Investment (2026-07-24)

Renata Ossovski made an explicit strategic statement about Signal Deck's architecture:

> "This whole conversation makes it feel like our most important under-the-hood investment may be in Loomwright and tendrils into all of the places we do work to pull from, and then making it broadly available for the many different use cases that could leverage it - while any bespoke experience should be built on top of that and more narrow/refined."

(Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

This is the clearest statement in the corpus that Loomwright is framed as a foundational layer — not just a Signal Deck tool — that many experiences should build on.

## Confidence Levels — Direction to Bake Into Loomwright (2026-07-24)

Renata Ossovski directed Tomas Berglund and Yusuf Adeyemi to discuss confidence levels and how they should flow through agent/human context:

> "we should talk about the idea of confidence levels and carrying that through in our agent/human context - we can label things as brainstorm, etc. - we can also choose to hold some of it back from use but keep it for processing so that it can 'graduate' to the for-use scenario."

Tomas Berglund noted he had been playing with this concept in the pr-review loop and crew-wiki loop. Renata followed up: **"We want to bake it into the Loomwright layer"** and **"We can take lessons from what you've done here, but let's put the ongoing investment in this space into Loomwright."** Renata also said: **"Loop me in, I have strong opinions on what Loomwright is and where it is going."** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## "Leads Can Self-Serve" and Definition of Done (2026-07-24)

Renata Ossovski highlighted a specific Signal Deck value proposition: **"Leads can self-serve w/o the crew having to spend time to make this available — that is a HUGE win on its own, apart from everything else we could do."**

She also articulated a concrete definition of done for Signal Deck: **"'Done' / 'Win' === 'Ingrid can talk to Signal Deck and have confidence that she knows what the crew is doing, where we're going, what is done, how we're progressing, etc.'"** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## FAQ-First Before Press Releases — Nolan Fitzgerald's Recommendation (2026-07-24)

Nolan Fitzgerald recommended that the crew should **write an FAQ first before writing press releases** — the working-backwards approach to a future press release process. The FAQ drives the press release, rather than the other way around. Renata noted this touched on feedback she had shared about knowing what questions would be asked and being prepared with answers. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## "Accurate, Precise, and Fresh" — Core Requirement (2026-07-31)

Nolan Fitzgerald articulated the core requirement for Signal Deck data: **"The information in Signal Deck is accurate, precise and fresh."** Tomas Berglund complemented this: **"It's weaving the right information."** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Marta's Challenge — "Why Isn't This Just Deskmate + Workstream Insights?" (2026-07-31)

Renata Ossovski noted that Marta Kovács's challenge — **"why isn't this just Deskmate + Workstream Insights and such?"** — was a useful forcing function for the crew's differentiation story. Renata also articulated a key differentiator: **"We do a lot of ferrying of context today"** — and that Signal Deck can build context for the benefit of the crew to leverage to help accelerate/guide agent experiences they are already doing, improving alignment. The question of how to answer this challenge was **left open**. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Positioning Debate — "Meet Crews Where They Are" vs. "Reshape the Crew" (2026-07-24)

Priya Raghavan raised a key strategic question in the 2026-07-24 weekly planning chat: **Can Signal Deck help crews that already run like the Platform Group run better** (serving already-advanced crews) **vs. can Signal Deck reshape a crew where they are to run like the Platform Group?** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

This is a product-market-fit question with implications for features, onboarding, and target customers. The question was raised but **not resolved** in this session. It is the first on-record articulation of this specific framing tension.

Renata Ossovski added context: **"I think many crews across Harborline are 'open minded' at this point, as all are getting pressure to make these shifts w/o clear direction/guidance or tooling to do so, so I think there is an opportunity for us there if we want to lean in there..."** She also raised the idea that if people "dip their toes into something that proves to be of significant value, might they be more willing to then start considering adjustments to how they've done things." (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Organizational Wisdom as Signal Deck's Distinctive Value (2026-07-24)

Priya Raghavan articulated what she saw as Signal Deck's most powerful and underexperienced capability:

> "One of the most powerful things I see Signal Deck bringing that people have not really experienced is bringing organizational wisdom (different than knowledge or 'insights') which I don't feel like we've ever experienced in any technology that has tried to achieve this (e.g. the engagement-dashboard category)."

She framed this as something that — once demonstrated clearly — would be very convincing. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Loomwright as the Most Important Under-the-Hood Investment (2026-07-24)

Renata Ossovski made an explicit strategic statement about Signal Deck's architecture:

> "This whole conversation makes it feel like our most important under-the-hood investment may be in Loomwright and tendrils into all of the places we do work to pull from, and then making it broadly available for the many different use cases that could leverage it - while any bespoke experience should be built on top of that and more narrow/refined."

(Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

This is the clearest statement in the corpus that Loomwright is framed as a foundational layer — not just a Signal Deck tool — that many experiences should build on.

## "Leads Can Self-Serve" and Definition of Done (2026-07-24)

Renata Ossovski highlighted a specific Signal Deck value proposition: **"Leads can self-serve w/o the crew having to spend time to make this available — that is a HUGE win on its own, apart from everything else we could do."**

She also articulated a concrete definition of done for Signal Deck: **"'Done' / 'Win' === 'Ingrid can talk to Signal Deck and have confidence that she knows what the crew is doing, where we're going, what is done, how we're progressing, etc.'"** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## FAQ-First Before Press Releases — Nolan Fitzgerald's Recommendation (2026-07-24)

Nolan Fitzgerald recommended that the crew should **write an FAQ first before writing press releases** — the working-backwards approach to a future press release process. The FAQ drives the press release, rather than the other way around. Renata noted this touched on feedback she had shared about knowing what questions would be asked and being prepared with answers. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Problem Statement Debate and "Why Isn't This Just Deskmate + Workstream Insights?" (2026-07-31)

Nolan Fitzgerald proposed a problem statement: **"Problem: AI makes it harder for crews to work together."** Tomas Berglund asked whether that would still be "the problem statement." Priya Raghavan noted the prior version focused on building empathy but the new statement could also be valid. The debate was exploratory, not settled. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

Renata Ossovski noted that Marta Kovács's challenge — **"why isn't this just Deskmate + Workstream Insights and such?"** — was a useful forcing function for the crew's differentiation story. Renata articulated a key differentiator: **"We do a lot of ferrying of context today"** — and that Signal Deck can build context for the benefit of the crew to leverage to help accelerate/guide agent experiences they are already doing, improving alignment. The question of how to answer this challenge was **left open**. (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## "Accurate, Precise, and Fresh" — Core Requirement (2026-07-31)

Nolan Fitzgerald articulated the core requirement for Signal Deck data: **"The information in Signal Deck is accurate, precise and fresh."** Tomas Berglund complemented this: **"It's weaving the right information."** (Signal Deck Weekly Planning__chat__pulled-2026-08-07-1440__2026-05-29_to_2026-08-07__slice-E06.md)

## Weekly Planning — Ingrid Shareout Debrief, Delivery Pulse Context, and Roadmap Coaching (2026-08-07)

In the 2026-08-07 weekly planning meeting (Renata Ossovski, Priya Raghavan, Nolan Fitzgerald), the crew debriefed the prior day's Ingrid Waverly shareout and Nolan Fitzgerald gave extended PM coaching on the Signal Deck roadmap.

**Ingrid's reaction — stronger than expected.** Renata reported that Ingrid's reaction to the first slide of Priya's presentation was "whoa, this is actually really good. Like this right here is the thing" — stronger than a simple "I get it." This validated the product framing was landing at the level that mattered. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

**Delivery Pulse — context for Signal Deck's outcome alignment opportunity.** Nolan Fitzgerald described Delivery Pulse, an internal hack project within the Correspondence org that tracks PR activity by crew and person. It was being planned for standardization across the whole division. Nolan described it as focused on engineering throughput rather than product outcomes, and named the outcome alignment layer as "the billion dollar idea" that no one can solve — "the first two will get solved. I don't want to spend too much time on one, but I think one feeds into three." He framed Signal Deck as the missing third piece in his AI acceleration framework: (1) AI adoption/tooling, (2) engineering delivery, (3) outcome alignment. See [[delivery-pulse|Delivery Pulse]] for the full tool page. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

**Beacon as "solo version of Signal Deck."** Renata introduced Beacon to Nolan Fitzgerald, framing it as "the solo version of Signal Deck" — mining suite data, driving sessions, and creating an attention ledger. Nolan's reaction: "This is a good product." Renata noted the mechanism behind Beacon (mining transcripts for friction signals without a dedicated feedback UI) might be useful at the crew level for Signal Deck without code changes. See [[beacon|Beacon]] for the full Beacon tool page. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

**Roadmap coaching — items still activity-based.** Nolan gave Priya extended coaching on roadmap discipline. His critique: Priya's roadmap items still felt like solutions/activities ("real workflows into Signal Deck with provenance") rather than outcomes. Nolan's instruction: reduce to three outcomes, rewrite each as an outcome statement, be able to state each in one sentence. He gave a concrete model: his own outcome for Correspondence was "make AI-led engineering changes fast, predictable, safe, and routine," with a KR of reducing PR merge time from 100 to 48 hours. Priya acknowledged she was still developing conviction. **Left open — Priya had not yet landed the outcome statements by the end of the meeting.** Nolan and Priya agreed to book a one-on-one working session. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

**AI cannot write the roadmap — named constraint.** Nolan stated: "You can't let AI write these things. There's too much slop out there." Priya noted AI-generated language ("provenance") was cascading back into her roadmap items despite her attempts to remove it. Nolan offered to share the Plainspoken Bundle to help. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

**Simulated user research tools confirmed as already existing.** When Nolan described wanting to automate user testing with fake user personas, Renata confirmed the crew already had this capability through product council and the simulated user research tool. (Signal Deck Weekly Planning__rec-2026-08-07-1111-9exf27__pulled-2026-08-12-1017.transcript.md)

See [[source-signal-deck-planning-2026-08-07|Signal Deck Weekly Planning — 2026-08-07]] for the full session record.

## Staleness Acknowledged — Product Surface Ahead of Freshness Guarantees (2026-08-12)

When Clara Bianchi returned from time off and was confused by Signal Deck showing April/May data, Priya Raghavan explained the current state explicitly:

> "Signal Deck is still in an in-between state. The corpus is only as fresh as the last weave/drop, so it can absolutely miss newer conversations and over-index on older April/May material. That is the staleness problem we are working through. For now, I'd use /signal-deck rather than trusting the Deck UI... The product surface is ahead of the freshness guarantees right now, and we're fixing the pipeline first." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

This is the clearest on-record acknowledgment that Signal Deck's UI was ahead of its data freshness guarantees as of early August 2026. The pipeline work (Tomas making capture automatic and durable, pushing Orchard/Loomwright through Ferryman) was explicitly named as the fix. See [[source-signal-deck-workstream-chat-E08|Signal Deck Workstream Chat — E08 (2026-08-05 to 2026-08-12)]].

## Roadmap Revised to 3 Outcomes (2026-08-10)

Priya Raghavan revised the Signal Deck roadmap from "12 seemingly disparate items" to "3 outcomes" — simpler and outcome-focused. She was scheduling time with Nolan Fitzgerald to review. The revision was driven by the packet-switched framing exercise Priya had been working through. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

A corpus freshness gap was also surfaced during this work: the repos corpus was only current as of 2026-07-06 (with some pages carrying updates up to 2026-07-27), which Priya flagged as "pretty out of date." Tomas confirmed this was a similar situation to the conversation corpus — Renata was handling the update. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

## Packet-Switched Framing — Push/Pull Debate (2026-08-10)

Otto Lindqvist posted a substantive synthesis of Ingrid Waverly's packet-switched framing, connecting it to pull-based scheduling and just-in-time production. His key articulation:

> "Crew knowledge should propagate because the work is observable and routable, not because humans repeatedly synchronize with one another. That is why 'Signal Deck removes the need to continuously synchronize humans' is much closer to her idea than 'Signal Deck is an AI dashboard.'" (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

Otto framed the concept as "Move from synchronization-by-supply to awareness-by-demand," connecting it to lean scheduling's concern with avoiding overproduction of communication.

**Priya Raghavan's counter-concern (not resolved):** Priya raised a concern that "actively routing info to people is push regardless of what level you're at" and that inferred demand push might recreate the waste of notifications, just with better intention. She was continuing to iterate on this as she reframed the roadmap based on outcomes. Priya's own synthesis: "Signal Deck turns crew awareness from something you sustain with live human connections into something you route and reconstruct on demand." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

This push/pull distinction was raised but **left open** — no resolution was recorded.

## Related (additional)

- [[source-renata-o-crew-sync-2026-07-23|Renata O Crew Sync — 2026-07-23 (12:18 PM)]] — 2026-07-23 crew sync (segment 2/3): Priya Raghavan's "operating model for AI-first crews" framing; DISAGREEMENT with Enzo Vidal on whether Signal Deck currently requires behavior change (left open); Signal Deck website vs. service distinction clarified (website for human browsing; service layer is the primary value for agents)
- [[source-renata-o-crew-sync-2026-07-30-1219|Renata O Crew Sync — 2026-07-30 (12:19 PM) — Working-Mode Shift, Tractor-as-Intended, and Signal Deck Automation]] — 2026-07-30 12:19 PM session: primary Signal Deck investment named as automating human gates and manual triggers; Yusuf helping with Loomwright; Tomas focused on data ingestion; Signal Deck as coordinator of Ferryman work tasks
- [[source-signal-deck-weekly-planning-chat-E06|Signal Deck Weekly Planning Chat — E06 (2026-07-20 to 2026-07-31)]] — 2026-07-20 to 2026-07-31: scheduling message (2026-07-20); substantive 2026-07-24 session (positioning debate — "meet crews where they are" vs. "reshape the crew"; organizational wisdom as distinctive value; Loomwright as most important under-the-hood investment; confidence levels direction; leads can self-serve; FAQ-first recommendation from Nolan Fitzgerald); substantive 2026-07-31 session (code-host assistant app discussion; outcomist persona merged; problem statement debate; Marta's challenge on Deskmate + Workstream Insights; "ferrying of context" framing; accurate/precise/fresh as core requirement)

## Ferryman × Signal Deck Integration Architecture — Settled (2026-07-27)

In the 2026-07-27 meeting (the first time Renata Ossovski, Yusuf Adeyemi, Hugo Marchetti, Neve Trentham, Tomas Berglund, and Priya Raghavan were all on a call together on this topic), the integration architecture between Signal Deck and Ferryman for Loomwright was settled:

**Signal Deck is the caller; Ferryman is the stateless executor.** Signal Deck reads from Vault Drive, passes the full wiki package plus new content to Ferryman, the pipeline processes it, and Signal Deck retrieves the updated package and writes it back. Ferryman does not connect to suite storage directly. (ferryman and signal deck__rec-2026-07-27-1639-4hfqqg__pulled-2026-08-07-1442.transcript.md)

**Trigger model**: Signal Deck triggers Ferryman (with data as input), not a Ferryman-side scheduler. Tomas Berglund had already done the work to hook up the server-to-server trigger. (ferryman and signal deck__rec-2026-07-27-1639-4hfqqg__pulled-2026-08-07-1442.transcript.md)

**Ownership handoff**: Renata Ossovski handed the Loomwright pipeline redesign to Yusuf Adeyemi, who would be the dedicated resource going forward. (ferryman and signal deck__rec-2026-07-27-1639-4hfqqg__pulled-2026-08-07-1442.transcript.md)

**Access token work**: Renata had been spiking a multi-device token provider approach and found the offline access scope (previously rejected) was now approved. Tomas Berglund was probing delegated subscriptions for real-time transcript delivery. Both were in progress at meeting end. (ferryman and signal deck__rec-2026-07-27-1639-4hfqqg__pulled-2026-08-07-1442.transcript.md)

See [[source-ferryman-and-signal-deck-2026-07-27|Ferryman and Signal Deck (2026-07-27)]] for the full meeting record.

## Three Loomwright Ingestion Use Cases for Signal Deck (2026-07-28)

In the 2026-07-28 Ferryman x Signal Deck meeting, Renata Ossovski named three main use cases for Loomwright in the Signal Deck context. She said covering all three keeps the system honest and avoids leaning too heavily on one scenario:

1. **Transcripts from chats and meeting recordings** — meeting recordings are one-time snapshots; chat conversations evolve (messages may be added hours or weeks later). How and when to process evolving chats is a different challenge from one-shot recordings.

2. **Repositories** — commit logs (commit messages), issues, PRs, and the back-and-forth in comments. All of this is rich context for understanding why things changed. Renata notes Orchard plays a similar data-gathering role here as Tomas Berglund's work plays for transcripts.

3. **Articles** (blog posts, video transcripts, etc.) — a third shape of content.

Renata: "I think there's three main ones that I would target. And that just keeps the whole thing a little bit more honest so that it's not leaning too heavily, you know, only supporting, doing well in one of these scenarios." (Ferryman x Signal Deck__rec-2026-07-28-1046-3fw8bi__pulled-2026-08-07-1442.transcript.md)

## Context Sanitization Flow for Signal Deck Data (2026-07-28)

In the same meeting, Renata described the correct flow for handling sensitive crew data when feeding it into Signal Deck's Loomwright process. The current multi-stage approach has a known failure: it builds a personal wiki and then projects from the wiki side, not from the original sources. Renata and Hugo Marchetti agree this has been a known problem for nearly a month.

The corrected sanitization flow:

1. Take the sources (transcripts, chats)
2. **Sanitize first** — remove sensitive content
3. Pass through a **human gate** to review what remains
4. Take that wholesale into the Signal Deck side to run the normal Loomwright process
5. Result: **redacted sources and full transcripts** together

Renata: "I probably wouldn't even bother building my personal wiki. I would just take those sources, sanitize them first, then just take that whole thing wholesale over to, well, human gate to review what's left, and then take that wholesale into the Signal Dec side to then build the normal Loomwright process on the Signal Dec side. Then you'd have the redacted sources and the full transcripts. And that already would be a huge improvement over what we have." (Ferryman x Signal Deck__rec-2026-07-28-1046-3fw8bi__pulled-2026-08-07-1442.transcript.md)

She notes there are other failure points beyond this, and addressing them all in one pass makes sense.

## Hugo Marchetti's Observation — Sources Must Be Accessible for Querying (2026-07-28)

Hugo Marchetti raised a practical problem he had encountered: Signal Deck does not keep sources accessible for later querying. When the model-generated wiki summarizes transcripts, the summary may miss something, and there is no easy path back to the original source to verify. He describes instances where the summary did not match his recollection of a call and he had to go find the transcript manually. He has his own crew wiki (with sources) as a workaround.

Renata agreed: "That's why the sources have to be part of it, which is actually part of the original generated-wiki concept." She confirmed sources are in Loomwright but not in Signal Deck. (Ferryman x Signal Deck__rec-2026-07-28-1046-3fw8bi__pulled-2026-08-07-1442.transcript.md)

- [[source-ferryman-x-signal-deck-2026-07-28|Ferryman x Signal Deck (2026-07-28)]] — 2026-07-28 meeting: three ingestion use cases (transcripts/chats, repos, articles); context sanitization flow; Hugo's observation on sources not being accessible; Loomwright v2 direction

## Signal Deck → Ferryman Integration — "Hand This Off for Me" Button (2026-07-20)

In the 2026-07-20 Signal Deck + Ferryman Next Steps working session, Tomas Berglund described a planned feature: a page in Signal Deck where identified tasks would have a "Hand this off for me" button that triggers a Ferryman job. Tomas's concern: sending enough context for Ferryman to actually do the work — the repo, the project context, and what the task is. (Signal Deck + Ferryman Next Steps__rec-2026-07-20-1513-mhu4cf__pulled-2026-08-07-1442.transcript.md)

Hugo Marchetti flagged that this would also require deciding which pipeline to use — the out-of-the-box pipelines might not be the right fit; the expert builder was suggested as a candidate to stress-test different scenarios. Neve noted that pipelines needed better descriptions so users (and agents) could select the right one.

Tomas and Neve planned a short spike together to experiment with the integration. No specific pipeline was committed to. (Signal Deck + Ferryman Next Steps__rec-2026-07-20-1513-mhu4cf__pulled-2026-08-07-1442.transcript.md)

This discussion is consistent with the direction established in the 2026-06-26 working meeting where Renata committed that Signal Deck would set priorities within Ferryman (not the other way around), and with the 2026-07-30 vision of Signal Deck as the coordinator that pushes work tasks into Ferryman.

- [[source-signal-deck-ferryman-next-steps-2026-07-20|Signal Deck + Ferryman Next Steps (2026-07-20)]] — 2026-07-20 working session: "Hand this off for me" button planned; Tomas and Neve to spike; pipeline selection and description gaps named; starter-prompt configuration pattern adopted (no new bundle needed)
- [[source-signal-deck-sync-2026-07-21|Signal Deck Sync — 2026-07-21]] — 2026-07-21 sync: new domain (signal-deck.harborline.dev); education bundle into briefings; Loomwright-based corpus data pipeline; human gate removal direction; PR-workflow self-improvement loop alternative proposed; markdown over JSON for content; Loomwright agent-facing vs. human-facing distinction clarified; Loomwright patterns as potential attractor bundle utilities; code-host team for Orchard access; session-based chat context raised; press release before roadmap; task priority reordering
- [[signal-deck-corpus-data-pipeline|Signal Deck Corpus Data Pipeline — Integration Architecture]] — integration architecture for the corpus data pipeline: discovery + refinement attractor pipeline, education bundle briefings, code-host access control

## Human Gating Design — Tiered Approach to Sensitive Ingestion (2026-07-31)

In the second half of the 2026-07-31 weekly planning meeting, Renata Ossovski articulated the design principle for handling sensitive recordings in Signal Deck's ingestion pipeline. The core principle: different content requires different levels of human review. Content that is "high-suspect" — one-on-ones, content with private information — requires human gating before ingestion. Content that reinforces things already said as a crew can flow more freely. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

The critical constraint: the human gate must be cheap enough to actually execute, not become rubber-stamping. Renata's approach is to use model-written summaries to draw human attention to the high-suspect areas, so the review is focused rather than exhaustive. The goal is either playing it "super safe" or, in areas of doubt, "drawing the focus there" so the human can verify. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

Renata also connected this to a trust argument: if crews are going to trust Signal Deck with their data, the crew should be transparent about how they handle sensitive content — making that transparency available as a resource without requiring everyone to read it. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Privacy-aware agent behavior — personal-messaging example:** Renata described a concrete example from a retention firewall she was building. When processing personal message archives, the agent recognized that the user was making a privacy decision for all of the people who sent them messages — not just for themselves. Therefore, the system forced the decision to use local models for this data. It then ran evals to find which local models met wall time and quality requirements. Renata's framing: "if you get the right context to help guide some of that stuff, like the agents will help us stay accountable on those things too." Priya Raghavan connected this to the idea of tracking data relationships: understanding what relationship a piece of information has with the things it's going to or not going to. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

This principle is consistent with the personal-to-crew projection architecture documented elsewhere (see [[source-boundary-as-safety-mechanism|Source Boundaries as Safety Mechanisms — Wiki Access and Sharing Control]]) and extends it specifically to the case of meeting recordings with mixed content.

## Meridian Mail Data — Nolan Fitzgerald as Individual Pilot (2026-07-31)

In the same session, Nolan Fitzgerald asked to be connected to Meridian Mail data in the crew's own instance, even just for himself: "I want to see if it's going to be useful." Renata confirmed this was possible via app registration (up to 99 people per registration for crew-level pilots). (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

Renata proposed a concrete sequencing shift to Priya Raghavan: instead of finding the first external crew next, get Nolan hooked up as an individual first. Her reasoning: Nolan can process data, experience the system, and provide feedback as the crew's ally — and then choose what to show his own crew as the capability develops. Nolan's goal was to connect Meridian Mail data, attach the bundle to his assistant, and test how other people would use it. Renata: "Yeah, I like that idea." (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Status: Direction agreed, no specific date or owner committed.**

## Weekly Planning — Feature Sequencing, Meeting Context Vision, and Compete Tool (2026-07-31, Segment 1/2)

The 2026-07-31 weekly planning meeting (Renata Ossovski, Nolan Fitzgerald, Priya Raghavan, Tomas Berglund) worked through several Signal Deck-specific threads. The meeting was shaped by the prior day's rehearsal with Marta Kovács, which exposed a concrete preparedness gap — Tomas Berglund not being in-the-loop with the narrative beats — that Renata named as a direct Signal Deck use case. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Feature Sequencing — #2 vs. #3 for the Crew's Own Use

Renata proposed flipping the priority of features #2 and #3 for the crew's own use: feature #3 (agents querying Signal Deck directly) was "actually easier to do and immediately useful and valuable, even before we get it to the point that two can be done to the level it needs to be." Nolan acknowledged this made sense for the crew specifically before other crews were using it. Renata confirmed: "for our crew, we can immediately start using #3 way more than we can use #2." (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Nolan's position on feature sequencing:** Nolan argued that features #1 and #2 were the most important overall and that #3 and #4 weren't needed until #1 and #2 were done well. He also raised a trust concern about feature #3: it felt like "just trust me, AI" — a leap that humans aren't wired to take without first building confidence through #1 and #2. He noted that 10% failure rate matters a lot even when something works 90% of the time. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**DISAGREEMENT on sequencing — partially resolved:** Renata and Nolan converged that #3 before #2 makes sense for the crew's own use, but for external crews #2 is more important first. The external sequencing was not formally decided. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Meeting Context Management — Behavior Change as the Outcome

Nolan articulated the value of feature #1 (automatic meeting ingestion) in terms of behavior change, not data ingestion:

> "It's not just about ingesting that context, but it's about processing that context in the context of like, A, we have a weekly signal deck meeting... it's not just like the context, but it's almost like, hey, like, here's the evolution of like all the stuff that's happened. Here are the outcomes that have been pulled out and then what I think should happen is that in those meetings, you start to change your behavior because of what is happening automatically in those meetings with Signal Deck." (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

Nolan's formulation: **ingestion is the task, behavior change is the outcome**. He argued that if the definition of done doesn't include behavior change, the feature isn't done. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Trust-building UX:** Nolan argued that for feature #1 to build trust, Signal Deck needs UX that lets users pick which meetings get ingested, see what context is going in, and click through to verify. "That's how you kind of like build trust and people may look at it the first few times and then never look at it again and just assume it's working." (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Renata on the "IV drip" of context:** Renata described the desired mechanism as not asking people to periodically read a document, but having the system feed updates to them: "you want something where the system's also feeding you as well and be like, hey, just so you all know, like these are the tweaks that have been being made to each of these stories because it's coming in gradually." She contrasted this with a change log; the goal was a story-level digest, not a diff. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Crew preparedness gap as a Signal Deck use case:** Renata named the crew's experience prepping for the Marta Kovács rehearsal — the places where people felt uncertain about how their piece connected to the bigger story — as exactly the challenges Signal Deck was designed to address. Tomas had not been able to be as in-the-loop with emerging narrative beats because the crew was moving fast. Renata framed this as a concrete, lived example of what Signal Deck solved. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**"Context poison" concern:** Nolan flagged that Signal Deck currently felt outdated — step #1 (automatic ingestion) was not yet done — which meant the context was potentially "context poison." He argued this was a reason to focus on #1 before anything else: if the data isn't fresh and accurate, agents acting on it would produce bad results. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Nolan's Compete Tool — Signal Deck as the Missing Link

Nolan described a compete dashboard he was building with an agentic coding assistant, structured around Signal Deck principles: using planning docs, OKRs, roadmap, and the people graph to partition the compete analysis by crew structure, so each crew can look at their partition and see competitive threats mapped against their own strategy. His three categories: mail, calendar, and AI-first workflow. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

Nolan framed this as "third-party truth telling" — using the tool to surface what the crew might not want to hear from him directly. He described a vision where Signal Deck could be attached to any crew in Harborline or any external company, using their own plans and strategy to help inform and educate the crew. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Signal Deck as the missing link for compete dashboards:** Nolan argued that a compete dashboard needs to be mapped against the crew's own product and strategy and organization — and Signal Deck is what makes that possible. Without Signal Deck, the compete dashboard is blind to what the crew's own plan is. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Nolan's Core Vision — 90% of Time on the Metric

Nolan articulated his vision for Signal Deck to his leadership: every worker should spend 90% of their time working on the metric they're being asked to move. Currently, crews in Correspondence probably spend 30–40% of their time aligning, communicating, and planning — not driving the metric. He extended this to a leader perspective: instead of figuring out what feature to have someone work on, the leader can focus on the bigger outcome, break it into 1% problems, and the system makes it a math problem instead of an art problem. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Problem Statement — Not There Yet

Priya Raghavan shared an updated problem statement for Signal Deck but noted "I don't actually still feel like it's there yet." She had been working through the repo, reviewing Nolan's Vision docs, finding a PRFAQ Nolan had written, and auditing what to keep vs. update. Nolan and Priya agreed to work on the problem statement offline rather than continuing in the meeting. **Left open.** (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

### Signal Deck as Standalone Product (Not a Loomwright Agent)

When the question arose of whether Signal Deck was a Loomwright Agent, Nolan was clear: "Signal Deck is a customer of Loomwright agents and potentially Ferryman, but I see Signal Deck as a standalone product." He described a vision where if a crew's code host is on the agentic assistant tier, Signal Deck could package out-of-the-box to ingest all that context and surface what the crew is doing. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

**Code host as go-to-market path:** Nolan suggested that if Signal Deck could be part of the code-host suite, that could be a good go-to-market path. He noted he had been pushing to move everything off the legacy tracker into the code host and that the code host was moving to the shared cloud, which would enable real product code to live there. (Signal Deck Weekly Planning__rec-2026-07-31-1148-y9ujhp__pulled-2026-08-04-1352.transcript.md)

See [[source-signal-deck-planning-2026-07-31|Signal Deck Weekly Planning — 2026-07-31]] for the full session record.

- [[source-signal-deck-planning-2026-07-31|Signal Deck Weekly Planning — 2026-07-31]] — 2026-07-31 weekly planning (Renata Ossovski + Nolan Fitzgerald + Priya Raghavan + Tomas Berglund): feature #3 before #2 for crew's own use (DISAGREEMENT on external sequencing, left open); Signal Deck as standalone product confirmed (not a Loomwright Agent); problem statement taken offline; Nolan Fitzgerald's 90% metric vision; "context poison" concern named; trust-building UX for meeting ingestion; compete tool using planning docs and people graph; code host as go-to-market path; Renata's "IV drip" of context framing; preparedness gap from Marta Kovács rehearsal named as concrete Signal Deck use case

## KeyBridge On-Demand for Meridian Sync — Architecture Decision (2026-07-15)

The architectural decision settled in E06 was not merely that KeyBridge shipped, but **why it was structured as on-demand rather than as the app's primary auth mechanism**. The split was forced by a specific constraint: the SPA code must not be downloadable without passing GateKey first. KeyBridge is then layered on top for the specific case of Meridian API data access — it is not the app login. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

**What shipped (#258, #52):** GateKey stays the sole app-login gate. KeyBridge is on-demand for Meridian API sync only, live in prod. This resolves the earlier open question from E03 about the GateKey + KeyBridge architecture for hosted Signal Deck.

**Local dev identity fix (#268):** Local dev user identity is now surfaced via in-app KeyBridge rather than a hardcoded principal. This was a separate problem from the Meridian sync KeyBridge — it was about making local development correctly identify the developer without a hardcoded account. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

**Meridian sync packaging error (#273):** A missing bundled crew-policy schema was parking sync jobs in production, blocking Meridian sync even after the KeyBridge work was complete. This was caught during the end-to-end validation walk-through with Priya Raghavan. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Q&A Download — "Two Doors" Design (#267)

The Q&A download feature was designed with two access paths rather than one, driven by an auth constraint: bulk download must be attributable to a real crew member, so a shared API key is refused (403). (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

**Door 1 — Lens API:** Per-user bearer token (`cli` / bearer auth). Attributable to a real crew member. This is the same pattern as the full-corpus download shipped in E05.

**Door 2 — Web UI:** Direct download through the web interface.

**Remaining piece:** The bundle bulk-pull tool (`signal_deck_download_answers`, bundle #36) — allowing agents to pull Q&A directly — was the next item, not yet shipped at the time of the E06 EOD update. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Split Q&A View (#260)

The problem the split Q&A view solved: in the combined view, crew members could not easily read their own data. Splitting the view into per-user and aggregate sections made the data readable. Clara Bianchi's reaction on seeing it: "I like the crew level aggregation in the Q&A part! ... this is seriously cool." (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Answer Quality and Context Poisoning Concern (2026-07-17)

After the Q&A aggregate view shipped, Priya Raghavan raised a concern that irrelevant or incorrect answers could poison context: "It's only good if the data is good IMO so calling divergences that aren't there or valid are distractions at best and context poisoning at worst." (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

Tomas proposed two mitigations: (1) an exclusion list or relevance/quality filtering in the answer-summary pipeline (#274) so summaries don't pollute context; (2) an admin control to remove specific answers as a low-cost approach. Both were filed as tracker issues; neither was built within the slice. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Q&A Questions Now Editable — Title + Prompt Distinction (#304, #93, 2026-07-24)

Q&A questions became editable with a title field separate from the prompt — the first step toward the richer question onboarding Clara Bianchi and Priya Raghavan had asked for. This also backfilled titles for existing questions. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Signal Deck → Ferryman Integration — Proof of Mechanism (#302, #94, 2026-07-24)

Tomas Berglund proved Signal Deck can invoke a basic smoke-test pipeline on Ferryman, working both locally and in production with member-gated, managed-identity auth. This was proof the mechanism works, not full pipeline support. Tomas noted three quick wins as next steps: Signal Deck tracking jobs it fired, polling for status updates, and providing a deep link to Ferryman. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Server-Side Meridian API Access — FIC Architecture Proven (2026-07-28 to 2026-07-29)

Tomas Berglund's probe work established which token patterns work and which are blocked in the Harborline corporate tenant for server-side Meridian API access without a user present. See [[platform-service-auth-architecture|Platform Service Authentication Architecture — Local vs. Cloud Deployment]] for the full technical record.

**Summary of findings:**
- SPA tokens: dead (GK-4471 — cannot be redeemed server-side)
- Web platform + cert or secret: blocked by tenant policy (`defaultAppManagementPolicy`)
- Public client: works, but the stored refresh token is the credential (security concern)
- **FIC (Federated Identity Credential): recommended** — not a key/password, so policy doesn't block it; requires the container app's managed identity; zero key material to rotate

**The irreversibility constraint:** Client type is bound at token issuance and cannot be changed later without all users re-authenticating. Going public-client now and switching to FIC later means all ~10 people re-authenticate. The design rests on "ask each person once, ever." (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

**Open disagreement (2026-07-31):** Otto Lindqvist found that the desktop credential broker changes the token math — tokens issued through the broker could be used on remote services, which invalidated his prior spikes and potentially the corp-cloud-expert bundle documentation. Tomas Berglund was on a different OS and had not used the broker. Otto: "All of the docs in the corp-cloud-expert bundle and platform bundles/docs are therefore wrong." Tomas offered to validate on his end. **Left open.** (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

## Token Auth for Automated Transcript Retrieval — Working Session (2026-07-28, 4:28 PM)

In a working session on 2026-07-28 (4:28 PM), Tomas Berglund and Renata Ossovski troubleshot the authentication problem blocking automated transcript retrieval from the Meridian API. The session was exploratory; the primary outcome was a sequencing decision rather than a solved problem.

**Architecture being designed:** Tomas proposed subscribing to the Meridian API for meeting transcript creation events, having the API call back to the Signal Deck server, queuing the transcript ID, and retrieving the transcript using an available token. The blocking question: how to obtain a token server-side without a user being present.

**Approaches ruled out or blocked:**
- **Tier 0 (browser-open token supply):** Renata explicitly ruled this out as a long-term approach — "not gonna work long term." (Signal Deck Workstream__rec-2026-07-28-1628-fim0bx__pulled-2026-08-04-1352.transcript.md)
- **App-level (application) permissions:** Not available under the crew's temporary admin consent program. (Signal Deck Workstream__rec-2026-07-28-1628-fim0bx__pulled-2026-08-04-1352.transcript.md)
- **Web platform + client secrets:** Blocked by corp tenant policy (`defaultAppManagementPolicy`), discovered in a prior probe. (Signal Deck Workstream__rec-2026-07-28-1628-fim0bx__pulled-2026-08-04-1352.transcript.md)

**Open as of this session:** Whether the web platform + certificate credential approach would work in the Harborline corporate tenant. Tomas had not yet tested this; Renata did not know the answer. Tomas committed to testing the refresh token flow before proceeding.

**Known fallback:** A background service or mobile app per crew member that stays logged in and provides tokens on demand — Renata described this as the floor that was known to work, but not the preferred solution. (Signal Deck Workstream__rec-2026-07-28-1628-fim0bx__pulled-2026-08-04-1352.transcript.md)

This session predates the FIC probe work documented in the E06 workstream chat (2026-07-28 to 2026-07-29), which appears to be the follow-through. See [[source-signal-deck-workstream-2026-07-28-1628|Signal Deck Workstream — 2026-07-28 (4:28 PM)]] for the full session record and [[platform-service-auth-architecture|Platform Service Authentication Architecture — Local vs. Cloud Deployment]] for the FIC findings.

- [[source-ferryman-and-signal-deck-2026-07-27|Ferryman and Signal Deck (2026-07-27)]] — 2026-07-27 first joint meeting on Ferryman × Signal Deck × Loomwright integration: data-passing architecture settled (caller owns suite I/O, Ferryman is stateless executor); trigger model settled (Signal Deck triggers Ferryman); Loomwright V2 design (agent-in-the-loop proxy, optimistic pass model); ownership handoff to Yusuf; access token work in progress

## Real-Time Transcript Capture — First End-to-End Preview (2026-07-31)

The FIC probe was turned into a working pipeline: a Meridian API meeting-transcript capture that reaches Signal Deck the moment a recording lands, without anyone staying logged in. Validated live — scheduled a meeting, watched it get picked up right after the crew chat showed the recording saved, confirmed the caption file. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

**Current state (as of 2026-07-31):** Meridian subscriptions and refresh tokens are in-memory. No durable store yet. Next steps: migrate to durable store (likely a managed table store), then connect live transcripts to a Ferryman pipeline for end-to-end processing. Renata directed converting the caption file to the token-friendly format used in suite-pull before storing or sending to agents. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

Tomas also kicked off a meeting extract job to Ferryman to summarize the transcript — a simple scenario to show the pipeline coming together. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E06.md)

- [[source-signal-deck-workstream-chat-E06|Signal Deck Workstream Chat — E06 (2026-07-16 to 2026-07-31)]] — 2026-07-16 to 2026-07-31: KeyBridge on-demand for Meridian API sync (GateKey stays app-login); Q&A "two doors" download design; split Q&A view; answer quality/context poisoning concern; FIC as recommended server-side Meridian API access path; real-time transcript capture first e2e preview; credential broker open disagreement (Otto Lindqvist vs. Tomas Berglund)

## Roadmap Item Review — Clarity and Structure Critique (2026-08-04)

In the second half of the 2026-08-04 demo planning meeting, Priya Raghavan and Tomas Berglund walked through the Signal Deck roadmap items one by one. The review was exploratory — no items were formally rewritten and no ordering was locked — but several soft positions emerged. (Demo Plans and Format for Thursday's Shareout__rec-2026-08-04-1650-s10m11__pulled-2026-08-07-1441.transcript.md)

**Item #1 — flagged for splitting:** Priya said item #1 "combines a bunch of things together. We should probably split those out." She proposed the headline should be category-level ("source ingestion ingests automatically") rather than combining multiple things. Tomas agreed. No specific split was drafted.

**Item #2 — vagueness contested:** Tomas raised that "agents can query and act" was too vague. "Query and act means what? People's understanding of agents could either be the chatbot version or the versions where they actually do the work for you." Priya confirmed the crew was going toward more autonomous operation but the item wording was not revised. Left open.

**Items #4 and #6 — proposed collapse:** Tomas observed that item #4 (personal one-on-one content through human-approved cleanup) was an expansion of "safely" in item #6 (whole crew contributes content safely). Priya agreed and proposed collapsing them: "Okay, so potentially collapse four and six." Tomas: "Yeah, okay." No formal merge was written.

**Roadmap "why" section missing:** Tomas pushed back on the roadmap structure overall: "Without the, like, what problem we're trying to solve... I don't know why. When I read this." He argued a preceding context section was needed before the roadmap items. Priya agreed: "Totally." Tomas also raised whether the ordering implied a commitment sequence. Neither settled on a specific structure.

**Item #9 — deprioritized:** Priya said item #9 (recurring meeting templates) was "not a thing in this version of Signal Deck." Tomas confirmed: "Not in the way we are using Signal Deck." Renata had previously said the show-and-tell rhythm was not being pursued. Effectively removed from active scope.

**Bottom items — "won't list" framing:** Priya introduced the MoSCoW framing: "the won'ts are just as important as the musts because it's on your radar, you know, you're not doing it." Tomas: "Yeah, I like that." Agreed direction: lower-priority items should be explicitly framed as a "won't" list. No specific items were formally moved.

**Real-time collaboration — named as a gap:** Priya flagged that real-time collaboration was missing from the roadmap and vision: "what's missing here is that real-time collaboration... in the roadmap and the vision." Tomas suggested adding one point at the bottom to track it. Left open as a future direction.

**Thursday demo — not finalized:** The meeting ended with Tomas acknowledging: "I know we haven't really... We didn't finalize on what we are doing on Thursday." Priya had proposed showing automatic ingestion plus a live CLI query as the ideal demo concept; Tomas proposed a branded demo page. Neither was committed. Priya stated the bar: "if it's a video, then she has to be able to go immediately and do it after." Tomas planned to list what he would try to achieve before ending the call.

**Proactive context inclusion — design direction named:** Tomas Berglund raised that Signal Deck modes currently expose tools that retrieve content reactively. He argued the system should be able to proactively include context using hooks, surfacing relevant crew conventions and patterns before being asked — connecting this to earlier Crew Knowledge bundle work. Captured for the record, not a committed decision. (Demo Plans and Format for Thursday's Shareout__rec-2026-08-04-1650-s10m11__pulled-2026-08-07-1441.transcript.md)

**Capture-server behavior as Signal Deck privacy model:** Priya Raghavan shared an observation from her personal capture server: the system distinguished personal from work content (screen recordings as personal, health data as personal) without reading that content out. Priya connected this to what Signal Deck needed to demonstrate for its own content handling. Tomas explained the mechanism was likely folder structure signals, not content reading. (Demo Plans and Format for Thursday's Shareout__rec-2026-08-04-1650-s10m11__pulled-2026-08-07-1441.transcript.md)

See [[source-demo-plans-thursday-shareout-2026-08-04|Demo Plans and Format for Thursday's Shareout — 2026-08-04]] for the full meeting record.

## Demo Planning for Thursday Shareout — Monitoring Mechanism and Framing Pivot (2026-08-04)

In a 2026-08-04 planning meeting, Priya Raghavan, Tomas Berglund, and Otto Lindqvist worked through what Signal Deck could demonstrate at the upcoming Thursday shareout with Ingrid Waverly. The meeting produced two important outputs: a description of the current monitoring mechanism and its limitations, and a demo framing pivot. (Demo Plans and Format for Thursday's Shareout__rec-2026-08-04-1650-s10m11__pulled-2026-08-07-1441.transcript.md)

### Monitoring Mechanism — Meridian Pool Limitations and API Subscriptions

Tomas Berglund described the current state: Renata Ossovski had brought the Meridian pool mechanism over to the Signal Deck site. The current implementation uses Meridian API subscriptions to detect when meeting transcripts are available. If all crew members onboard onto the API subscription through Signal Deck's web, the system can receive everyone's chat and identify when transcripts are available. (Demo Plans and Format for Thursday's Shareout)

**Key limitation:** The Meridian pool does not provide attachments. Tomas: "The Meridian pool doesn't provide that." This means Signal Deck captures the words of chat messages but not shared documents, images, or links. Priya Raghavan raised this as a completeness concern: "if you're looking at a complete picture, all of that is part of a complete picture." Tomas agreed but noted attachment handling was a question that had never been formally answered. (Demo Plans and Format for Thursday's Shareout)

**Privacy constraint:** The system cannot automatically process everything it receives. The current approach: whitelisting by meeting title, or a policy that, if passed, automatically pulls the transcript. Tomas: "we need an agreement of the product story on how do we want to handle that." (Demo Plans and Format for Thursday's Shareout)

**Transcript vs. chat distinction:** Transcripts are much easier than chat. Transcript: one file, clearly scoped. Chat: undefined scope, attachment handling unclear. Tomas proposed potentially splitting the scope — meetings first, chat later. (Demo Plans and Format for Thursday's Shareout)

### Demo Framing Pivot — Steady Stream vs. Try-It-Now

The crew was mid-pipeline-transition. Priya concluded there was nothing for Ingrid to try that week. Her pivot:

> "I'd rather pivot right now... away from, here's what you can, like, for this week and this week only... But in two weeks, I'd rather set that marker... So it would be more like a steady stream of updates with these milestones."

Priya: "I'm okay if we don't have anything to say you can try because of what we're doing." Tomas agreed. (Demo Plans and Format for Thursday's Shareout)

What would be shown: the "dovecote" page showing automatic processing happening; the connection between Signal Deck and Ferryman; talking more than showing. Tomas committed to storage setup (so token persists on server restart) and asking the crew to log in for transcript capture. (Demo Plans and Format for Thursday's Shareout)

**What was not known:** Otto Lindqvist raised the question of what Ingrid actually expected from Signal Deck. Priya: "I actually don't." Tomas: "I'm only in the conversations with Nolan and Renata." Priya: "nobody knows." This was left unresolved. (Demo Plans and Format for Thursday's Shareout)

See [[signal-deck-monitoring-mechanism-decisions-2026-08|Signal Deck Monitoring Mechanism — Meridian Pool Limitations and API Subscription Path]] and [[signal-deck-thursday-demo-framing-pivot-2026-08-04|Signal Deck Thursday Demo Framing Pivot — Steady Stream vs. Try-It-Now (2026-08-04)]] for the full concept pages, and [[source-demo-plans-thursday-shareout-2026-08-04|Demo Plans and Format for Thursday's Shareout — 2026-08-04]] for the source record.

## Ingrid Waverly's Engagement — Platform Releases Shareout (2026-08-06)

In the first two-week cadence shareout with Ingrid Waverly on 2026-08-06, Signal Deck was presented by Priya Raghavan and received substantive engagement from Ingrid.

**Ingrid's "magical moment" articulation.** Ingrid described the specific Signal Deck capability she was waiting for — the one that would make her a believer:

> "The magical moment I'm still waiting for is like, I start a fresh session somewhere with the agent or whatever, and I ask it to do something, and it's like, oh, no, no, no, like, Neve did part of this, and you know, Otto did part of this, and we've got these four tools, and like, here's the thing you want, and go, you know, like. that like we haven't, you know, system doesn't really do that yet, right?"

Priya Raghavan confirmed glimpses of this were appearing in Signal Deck. (Platform Releases Shareout__rec-2026-08-06-1502-x162mf__pulled-2026-08-07-1441.transcript.md)

**Ingrid's packet-switched framing raised live.** During the presentation, Ingrid articulated her packet-switched vs. circuit-switched framing:

> "We've been trying to do circuit switching. We've been trying to like manage this flood of information and chaos and activity by like laying down kind of fixed channels for ourselves... I actually think we do need something that is more like packet switching, where like... there's like some underlying protocol that we're all adhering to that makes this stuff easier to deal with and work on."

Renata confirmed: "You are right, that isn't the Signal Deck domain." Priya confirmed: "That's a great idea and we are building it." (Platform Releases Shareout__rec-2026-08-06-1502-x162mf__pulled-2026-08-07-1441.transcript.md)

**Ingrid's meeting-notes-app warning.** Ingrid explicitly named the failure mode to avoid:

> "The tragedy here would be like, if you spend all this time to do Signal Deck, and we wind up with like a project management, you know, to-do list. meeting notes app. Like, who gives a ****? Like, there's a million of those, right?"

She pushed the crew to think hard about where they were going before falling back to familiar patterns. Priya confirmed this was not the point; Ingrid acknowledged she knew it wasn't, but wanted to encourage the crew to resist the gravitational pull toward familiar software categories. (Platform Releases Shareout__rec-2026-08-06-1502-x162mf__pulled-2026-08-07-1441.transcript.md)

**Ingrid's hackathon vision.** Ingrid articulated what she wanted the September hackathon to prove: "can we actually be a fluid, dynamic, interacting crew at scale in the real, like, you know, can we spend, you know, why can't we build some significant thing in a day together with, you know, 10 people running in parallel with this powerful system behind us?" (Platform Releases Shareout__rec-2026-08-06-1502-x162mf__pulled-2026-08-07-1441.transcript.md)

**Signal Deck data freshness.** Renata noted that Signal Deck's data hadn't been as helpful in prior weeks because she was the bottleneck — she had to push the button to trigger ingestion. The work on automatic ingestion of meetings was underway to strip that out. Priya described the next two weeks as focused on hardening pipelines and onboarding the crew. (Platform Releases Shareout__rec-2026-08-06-1502-x162mf__pulled-2026-08-07-1441.transcript.md)

See [[source-platform-releases-shareout-2026-08-06|Platform Releases Shareout — 2026-08-06]] for the full transcript record.

## Dovecote → Ferryman Handoff — Architecture Decision (2026-08-01 to 2026-08-07)

The E07 period completed the Dovecote→Ferryman handoff: a captured transcript can now kick off a Ferryman job, with a full jobs experience (list, poll, review gate, result) surfaced in Signal Deck. The division of labor: Dovecote handles capture, Ferryman handles processing, Yusuf Adeyemi owns the Ferryman side. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E07.md)

**Dovecote hardening decisions (2026-07-31 EOD):**
- Queue keyed on artifact identity (not meeting) — prevents duplicate processing when the same meeting produces multiple events
- Capture policy survives redeployment — previously, a restart would lose capture state
- Lost transcript distinguishable from an API retry — diagnostic improvement; prior implementation used a catch-all that obscured the real cause
- Usage dashboard reports true loss reason — not a misleading catch-all

**Ferryman jobs streaming design:** Chose polling for the jobs list page, SSE for single job view. Constraint: no global SSE for a list of job IDs. Master-detail layout accommodates this. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E07.md)

**Durable store — completed in phases:**
- Phase 2 (2026-08-07): Meridian enrolments persist with refresh tokens encrypted at rest
- Phase 3 (2026-08-07): Subscription registry is durable

Problem solved: before this, a server restart required every enrolled user to re-enrol. Capture state now survives deploys. Next: notification queue durable. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E07.md)

**Stale data resolved:** Priya Raghavan raised concern about stale Signal Deck data in the corpus. Confirmed: Signal Deck uses corpus data only (not direct session data). Renata was running v2 Loomwright to produce a fresher corpus; corpus was swapped to Renata's pre-woven v2 wiki package (130 pages, 73 sources) by 2026-08-06. (Signal Deck Workstream__chat__pulled-2026-08-07-1440__2026-05-11_to_2026-08-07__slice-E07.md)

See [[source-signal-deck-workstream-chat-E07|Signal Deck Workstream Chat — E07 (2026-08-01 to 2026-08-07)]] for the full E07 record.

## Orchard Design for Signal Deck — Inbox-Based Architecture (2026-08-11)

Renata Ossovski described a detailed workflow for how Signal Deck should use the code-host app (crew-repo-app) for repo access. This is the first on-record architectural description of the full repo ingestion pipeline:

**Repo list syncing:** Include all repos the code-host app (crew-repo-app) has access to, plus all Harborline org repos starting with `platform-*`. The list should be synced regularly — adding new repos, pruning dead ones. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Two separate wiki buckets:** Route repo content to a private repos wiki and a public repos wiki separately. Renata: "this way we can choose to leverage the public one for other needs/use-cases beyond the Signal Deck needs." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Inbox-based Loomwright:** When newly added wiki source docs exist in either bucket, kick off a weaver instance if one isn't already running; contribute to its inbox; the weaver runs until the inbox is empty then shuts down. If the inbox flows faster than dequeuing and the instance never stops, "we're still getting updated wiki content while it's working." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Continuous delivery:** A long-running watcher/process integrates the resulting wiki back into Signal Deck corpus as sources are being integrated, with monitoring/events through the process.

Renata framed this as a rough design to challenge or refine. Tomas Berglund's backlog (from the same day) confirmed this direction: "Orchard visibility — onboard myself onto the code-host app process and validate it end to end" was the top priority.

## Pipeline Architecture Decisions — Week Recap (2026-08-10)

In the 2026-08-10 Signal Deck week recap meeting, Renata Ossovski made several concrete pipeline decisions:

**Scrub-before-Loomwright (settled):** Run a parallel model-review pipeline on source documents first; drop documents that are essentially empty after scrubbing. The scrubbed files (not the originals) are what get stored and fed into Loomwright. Renata described this as a recipe-styled attractor that fans out to multiple models in parallel to review each document, then has one model decide what to redact. This addresses the problem where a one-on-one got mixed into the corpus and surfaced in the wiki. (Signal Deck - Week Recap and Plan__rec-2026-08-10-1730-eyx7ig__pulled-2026-08-12-1018.transcript.md)

**Two separate wikis (settled):** Public repos and private repos should be in separate wikis. The reason: there is value in the public wiki potentially being used publicly. If merged, that option is gone. Agents can query both. (Signal Deck - Week Recap and Plan__rec-2026-08-10-1730-eyx7ig__pulled-2026-08-12-1018.transcript.md)

**Chat ingestion frequency (settled):** 4–6 hour schedule with a message threshold gate. If heavy traffic, pick it up; if only one message in a day, stretch the interval. Real-time querying of recent chat should be available separately from the Loomwright ingestion path. (Signal Deck - Week Recap and Plan__rec-2026-08-10-1730-eyx7ig__pulled-2026-08-12-1018.transcript.md)

**Documents in chat (deferred):** Focus on transcripts, chat, channels, and code first. Documents shared in meetings that are stored in the crew's Vault Drive are accessible there — that's a better path than trying to follow links in chat messages. (Signal Deck - Week Recap and Plan__rec-2026-08-10-1730-eyx7ig__pulled-2026-08-12-1018.transcript.md)

**Hackathon coordination layer gap (DISAGREEMENT — left open):** Tomas Berglund identified that Signal Deck was missing a coordination layer — the piece that would take a problem discussed in a meeting, propose distributed tasks, and enable the crew to work together from that. Tomas held that the roadmap as described doesn't lead to a good hackathon demo. Renata's position: even without the full coordination layer, demonstrating that Signal Deck can capture and query context from a kickoff meeting is a win, and the coordination layer is last priority after all other pipeline pieces are in place. **Left open.** (Signal Deck - Week Recap and Plan__rec-2026-08-10-1730-eyx7ig__pulled-2026-08-12-1018.transcript.md)

See [[source-signal-deck-week-recap-plan-2026-08-10|Signal Deck - Week Recap and Plan — 2026-08-10]] for the full session record.

## Crew-Repo-App Onboarding — Repo Access Architecture (2026-08-11 to 2026-08-12)

Tomas Berglund and Rafael Duarte validated the crew-repo-app (a code-host app for managing crew repo access) end-to-end. Key architectural clarifications from Renata Ossovski:

- **Individual repos**: crew-repo-app manages permissions for individual user repos (public and private) — this is its primary purpose
- **Harborline org repos**: Accessed separately. "This is ONLY for individual user repos, NOT Harborline repos. For Harborline repos we just need all of the public 'platform*' and then a user account that can access the private ones." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)
- **Harborline private repos**: Require adding the Harborline Platform user account to those repos directly

Tomas validated end-to-end retrieval of the list of crew-repo-app installations and the list of repositories. Renata's stated goal for the week: "enabling the scenario where Ingrid can leverage the Signal Deck bundle to access info about and repos across the entire crew and Harborline org as a first-value prop is a 'win' achievable this week." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

- [[source-signal-deck-workstream-chat-E08|Signal Deck Workstream Chat — E08 (2026-08-05 to 2026-08-12)]] — scrub-before-wiki decision; suite-pull 50% coverage bug; browser extension auto-scroll bug; Otto Lindqvist's packet-switched/lean-scheduling synthesis; Priya Raghavan's push/pull concern; roadmap revised to 3 outcomes; staleness acknowledged publicly; crew-repo-app onboarding; Renata's Orchard design

## Durable Store — Completed (2026-08-07)

Tomas Berglund completed the durable store work, completing the pipeline that had been in progress:

- **Phase 2 (2026-08-07):** Meridian enrolments persist with refresh tokens encrypted at rest
- **Phase 3 (2026-08-07):** Subscription registry is durable; infra env block in place

**Problem solved:** Before this, a server restart required every enrolled user to re-enrol. Capture state now survives deploys. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

## Packet-Switched Framing — Push/Pull Debate (2026-08-10)

Otto Lindqvist posted a substantive synthesis of Ingrid Waverly's packet-switched framing, connecting it to pull-based scheduling and just-in-time production. His key articulation:

> "Crew knowledge should propagate because the work is observable and routable, not because humans repeatedly synchronize with one another. That is why 'Signal Deck removes the need to continuously synchronize humans' is much closer to her idea than 'Signal Deck is an AI dashboard.'" (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

Otto framed the concept as "Move from synchronization-by-supply to awareness-by-demand," connecting it to lean scheduling's concern with avoiding overproduction of communication.

**Priya Raghavan's counter-concern (not resolved):** Priya raised a concern that "actively routing info to people is push regardless of what level you're at" and that inferred demand push might recreate the waste of notifications, just with a better intention. She was continuing to iterate on this as she reframed the roadmap based on outcomes. Priya's own synthesis: "Signal Deck turns crew awareness from something you sustain with live human connections into something you route and reconstruct on demand." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

This push/pull distinction was raised but **left open** — no resolution was recorded.

## Roadmap Revised to 3 Outcomes (2026-08-10)

Priya Raghavan revised the Signal Deck roadmap from "12 seemingly disparate items" to "3 outcomes" — simpler and outcome-focused. She was scheduling time with Nolan Fitzgerald to review. The revision was driven by the packet-switched framing exercise Priya had been working through. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

A corpus freshness gap was also surfaced during this work: the repos corpus was only current as of 2026-07-06 (with some pages carrying updates up to 2026-07-27), which Priya flagged as "pretty out of date." Tomas confirmed this was a similar situation to the conversation corpus — Renata was handling the update. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

## Orchard Design for Signal Deck — Inbox-Based Architecture (2026-08-11)

Renata Ossovski described a detailed workflow for how Signal Deck should use the code-host app (crew-repo-app) for repo access. This is the first on-record architectural description of the full repo ingestion pipeline:

**Repo list syncing:** Include all repos the code-host app (crew-repo-app) has access to, plus all Harborline org repos starting with `platform-*`. The list should be synced regularly — adding new repos, pruning dead ones. (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Two separate wiki buckets:** Route repo content to a private repos wiki and a public repos wiki separately. Renata: "this way we can choose to leverage the public one for other needs/use-cases beyond the Signal Deck needs." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Inbox-based Loomwright:** When newly added wiki source docs exist in either bucket, kick off a weaver instance if one isn't already running; contribute to its inbox; the weaver runs until the inbox is empty then shuts down. If the inbox flows faster than dequeuing and the instance never stops, "we're still getting updated wiki content while it's working." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)

**Continuous delivery:** A long-running watcher/process integrates the resulting wiki back into Signal Deck corpus as sources are being integrated, with monitoring/events through the process.

Renata framed this as a rough design to challenge or refine. Tomas Berglund's backlog confirmed this direction: "Orchard visibility — onboard myself onto the code-host app process and validate it end to end" was the top priority.

## Crew-Repo-App Onboarding — Repo Access Architecture (2026-08-11 to 2026-08-12)

Tomas Berglund and Rafael Duarte validated the crew-repo-app (a code-host app for managing crew repo access) end-to-end. Key architectural clarifications from Renata Ossovski:

- **Individual repos**: crew-repo-app manages permissions for individual user repos (public and private) — this is its primary purpose
- **Harborline org repos**: Accessed separately. "This is ONLY for individual user repos, NOT Harborline repos. For Harborline repos we just need all of the public 'platform*' and then a user account that can access the private ones." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)
- **Harborline private repos**: Require adding the Harborline Platform user account to those repos directly

Tomas validated end-to-end retrieval of the list of crew-repo-app installations and the list of repositories. Renata's stated goal for the week: "enabling the scenario where Ingrid can leverage the Signal Deck bundle to access info about and repos across the entire crew and Harborline org as a first-value prop is a 'win' achievable this week." (Signal Deck Workstream__chat__pulled-2026-08-12-1017__2026-08-05_to_2026-08-12.md)
