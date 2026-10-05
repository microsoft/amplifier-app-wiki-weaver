# Learnings — what went wrong, how it showed, what we did

One entry per problem found by building or running V4. Each has the problem, how it
showed up (with the number or example that made it visible), the fix, and status.
Decisions and their reasons are one line each in `docs/DECISIONS.md`; this file is the
longer record of what the running system taught us. Newest at the bottom.

### Contract shapes written from memory — 2026-10-02
**Problem.** The ledger row and `result.json` shapes in the builder prompt were specified
from memory rather than from the code that consumes them.
**How it showed.** CP1's `result.json` had no `counts` object and no `converged` field;
the resolver's `_classify` requires both, and for repo pipelines ignores the exit code —
so every V4 run would have reported *failed* in Resolve while working. The ledger used
`ts`/`filename`/`sha256`/`outcome` where RepoWeaver reads `timestamp`/`source`/`hash`/
`status`/`converged`.
**Fix.** Both shapes verified against V1's `lib.py` and the resolver's parser; the builder
told to read the parser, not a paraphrase; a deterministic test for each shape.
**Status.** Applied at CP2. (The Converge contract draft independently caught the ledger
keys — the exercise's first concrete return.)

### Held or skipped sources left the corpus dirty — 2026-10-02
**Problem.** A hold or skip wrote its ledger row and moved the file without committing.
**How it showed.** The next source's write-scope check would have seen those files as
writes outside the selected pages and failed. CP1 never hit it because nothing was held.
**Fix.** Skip and hold commit their bookkeeping immediately; a test asserts the corpus is
clean afterwards. Found by the builder while applying the CP1 corrections.
**Status.** Applied at CP2.

### Engine step cap — 2026-10-02
**Problem.** dot-runner stops any run at nodes × 50 steps: 650 for the 13-node graph,
about 85 sources.
**How it showed.** The full 215-source corpus could not run in one invocation.
**Fix.** The CLI runs the graph in 40-source batches until the inbox is empty; one run
directory and one aggregate `result.json` per invocation, because the resolver reads only
the newest file. The loop is deterministic glue; no inference left the graph.
**Status.** Applied at CP2. REVISIT if the engine's bound changes.

### Per-page source cap bound on the pages that must accumulate — 2026-10-02
**Problem.** The cap of ~10 sources per page (from the prior program's attribution
finding) collided with pages accumulating across the whole corpus.
**How it showed.** 7 of 40 topic pages were at the cap after one epoch; E02 would have
scattered Team Pulse and Resolve material across spillover pages or held sources.
**Fix.** Cap removed. The citation check guards attribution mechanically instead —
6,609 citations, 0 misses, with pages citing up to 37 sources.
**Status.** Applied at CP3. What the cap was incidentally bounding — page size — is the
next two entries.

### "Each sentence names its source" — 2026-10-05
**Problem.** PLAN.md §2 said each *sentence* cites. The vision copied it; the builder
prompt said "every claim." The design intent was always principle 6: specific claims
cite, orientation and synthesis need not.
**How it showed.** Dense cite-everything prose on every page; 513 citations on one
620-line page. A synthesized paragraph is hard to write when every sentence needs a
quote — this is likely upstream of the journal-not-synthesis pattern below. Caught by
the owner reviewing the vision, traced back to the plan.
**Fix.** PLAN.md §2 reworded to principle 6; vision text replaced; writer prompt aligned.
The check is unchanged — it never required a citation per sentence, only that present
citations resolve.
**Status.** Applied — plan, vision and writer prompt (d9a8b6d).

Where V4's citations land relative to the earlier versions:

| | V1 | V2 | V4 |
|---|---|---|---|
| Form | numbered footnote `[N]`; `sources:` as integer ids | the source filename | `[s42: "quote"]` — V1's id form plus a few quoted words |
| Points at | a whole file | a whole file | the exact spot — the quote is a grep target inside a 150 KB transcript |
| Verified | no (an LLM assess node scored pages; it did not resolve citations) | no | yes, mechanically — `grep` confirms the quote is in the file; no model |
| Density | no stated rule | no stated rule; the eval found V2's wrong answers "well-cited" | explicit — specific claims only; synthesis and orientation uncited |
| Measured value | — | +1.80 for following file-level citations back to sources, the prior program's largest win | keeps that; the quote makes "follow the thread" cheap |

The one property V4 shares with V3 that V1 and V2 lacked is verification. The difference
is `grep` versus a second model grading every sentence, and no rule that a sentence must
carry a citation at all.

### Citation strings are half the page — 2026-10-05
**Problem.** Citations carried the full ~100-character source filename.
**How it showed.** `team-pulse.md`: 149 KB, 513 citations averaging 154 characters —
79 KB, 53% of the page, is citation text.
**Fix.** Cite by the ledger's stable `source_id` (`[s42: "quote"]`); transform existing
pages mechanically with the ledger as the map; `lint` must report zero citation errors
afterwards or the transform is wrong.
**Status.** Applied before E03 (d9a8b6d). 6,609 citations transformed, 0 unmapped, lint 0.
Citation share on the five largest pages fell from 51–55% to 32–37%; Team Pulse went from
149 KB to 106 KB. The quotes are now the bulk; the density rule is the remaining lever.

### Initiative pages are journals, not syntheses — 2026-10-05
**Problem.** The writer appends a dated section per source instead of maintaining a
current view. "Integrate, never replace" plus a loss guard that fails on a lost heading
make consolidation expensive, so the writer never tries (zero loss-guard failures across
65 sources).
**How it showed.** `team-pulse.md`: 41 `##` sections, almost all dated, several named for
the *meeting* rather than what happened; only 7 superseded markers in 620 lines, so it is
not history bloat — it is 41 live entries. V2's own Team Pulse page was 13 sections by
aspect with a "Current State" section.
**Fix.** Two-layer hub pages: a `## Current state` section rewritten each pass on top, the
dated record below unchanged. Policy in the lens's page-type definitions, one writer line,
one loss-guard exemption keyed on the heading. Principle 3 holds: the record accumulates;
the current view is derived from it.
**Status.** Applied before E03 (d9a8b6d). First contact, personal corpus: 25 of 30 topic
pages gained a `## Current state` section (median 6 lines) on first touch; 37% of headings
carry a date, against nearly all of them on the team corpus before. E03 is the real test.

### Stale quotes after an edit that removes text — 2026-10-03
**Problem.** Re-ingesting a changed source touched only the pages the brief selected;
pages elsewhere could keep quotes the source no longer contains.
**How it showed.** Not yet — CP3's edit was append-only. Identified by the builder as an
open item. Personal notes are where it would bite; transcripts never change.
**Fix.** On re-ingest: tell the writer the source changed, give it a diff of old vs new,
add every citing page to its selection, mark stale quotes superseded rather than deleting.
All in tool-node glue.
**Status.** Applied (d9a8b6d) and proven on the personal run: a bullet removed from one
note, four pages quoted it, every one kept the quote inside a superseded marker with its
original citation; no page lost a line; first-write pass; lint 0.

### The citation check has never caught a misquote — 2026-10-05
**Problem.** We assumed the check was guarding against fabricated or paraphrased quotes,
and weighed its cost (about 15% of first writes failing) against that.
**How it showed.** All 12 CP3 misses, classified: every one was a verbatim quote of
fewer than five words — "worked ok", "not in yet", "how does it authenticate". Zero
whitespace, paraphrase, dropped-prefix or not-present cases. The personal run repeated it:
11 short-quote findings, 0 quotes not found. Across ~8,000 citations on two corpora the
verification half has never fired; the 5-word minimum is the only friction the citation
machinery has ever produced.
**Fix.** None yet — the 5-word rule is held through E03 so the density change is the only
variable. If short-quote failures persist, lower the minimum to three words or drop it
and accept any verbatim quote.
**Status.** Measuring. Short-quote failures are now counted separately in every report.

### Model steps had no timeout — 2026-10-05
**Problem.** A model step could wait forever on a dead connection.
**How it showed.** During a network outage the writer sat 63 minutes on a 5 KB note.
Killing it left a half-written page uncommitted, and the next run's opening snapshot
would have committed it as if it were the owner's edit.
**Fix.** Every model step has a timeout (brief 900 s, write 2400 s, index 1800 s, ask and
init 900 s); ingest begins by undoing any half-written source. In the restarted run the
outage became fast failures — five briefs held with "Connection error", index step exit 1.
**Status.** Applied (5f390bb). Timeouts fired per epoch are now reported.

### Held sources could not be retried — 2026-10-05
**Problem.** Only content that had never been tried was eligible for ingest.
**How it showed.** Five sources held during the outage; moving them back into `_inbox/`
did nothing. RepoWeaver's retry path works exactly that way — move the failed file back,
run ingest — so its retries would have silently done nothing, and every contract assert
would still have passed.
**Fix.** Anything not converged is eligible, as in V1. Re-dropped, all five converged. A
conformance assert for the retry path is added to the corpus contract.
**Status.** Applied (2f90b2c). The lesson: the contracts lock the surface; behavior
underneath it still needs the V1-parity test.

### Old citations need the old source text — 2026-10-05
**Problem.** Editing a source replaces `_sources/<name>`; pages still cite the previous
version by its id, and those citations can no longer be checked.
**How it showed.** The Ken note edit: s11 became s26; four pages cite s11.
**Fix.** Prior versions are kept at `.wiki/source-versions/s<id>.md`, under `.wiki/` but
outside the resolver's exclusions, so they travel. Old citations resolve against them.
**Status.** Applied. Added to the corpus contract's layout and round-trip set.

