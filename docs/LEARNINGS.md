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
    **Status.** Plan and vision fixed; writer prompt pending (CP4).

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
    **Status.** Pending, before E03.

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
    **Status.** Pending, before E03 (owner's call to defer).

    ### Stale quotes after an edit that removes text — 2026-10-03
    **Problem.** Re-ingesting a changed source touched only the pages the brief selected;
    pages elsewhere could keep quotes the source no longer contains.
    **How it showed.** Not yet — CP3's edit was append-only. Identified by the builder as an
    open item. Personal notes are where it would bite; transcripts never change.
    **Fix.** On re-ingest: tell the writer the source changed, give it a diff of old vs new,
    add every citing page to its selection, mark stale quotes superseded rather than deleting.
    All in tool-node glue.
    **Status.** Pending, before the personal run.

    ### The twelve CP3 citation misses were short quotes, not mismatches — 2026-10-05
    **Problem.** The open question was whether the check was failing on whitespace,
    paraphrase or fabrication.
    **How it showed.** All 12 findings were "quote under 5 words", and all 12 quotes were
    present verbatim in their source (7 distinct quotes over 5 sources; 4 of the 5 were chat
    exports). Examples: "worked ok", "not in yet", "Tested five bundles end-to-end" (four
    words), a bare URL. The personal run: 20 distinct first-write findings — 11 short
    quotes, 8 cited-but-not-in-`sources:`, 1 broken wikilink, 0 verbatim misses.
    **Fix.** None to the check. The writer reaches for short, punchy fragments; the
    rewrite fixes them every time (0 second-write failures across E02 and personal).
    **Status.** No change. REVISIT only if the rewrite stops fixing them.

    ### A dead network hung the writer for an hour, and the next run would have committed its half-page — 2026-10-05
    **Problem.** Box nodes had no timeout. A run killed mid-source left the writer's
    uncommitted page edits in the corpus, and the next ingest's pre-run snapshot commits
    whatever is uncommitted.
    **How it showed.** During a network outage the personal run's write node sat for 63
    minutes on a 5 KB note. Killing it left `source-people-chris.md` untracked in the corpus.
    In the restarted run the outage turned into fast failures instead: five briefs failed
    with "Connection error", were held, and the index step failed (exit 1, errored).
    **Fix.** Every box node has a timeout; ingest begins with a `recover` step that reverts
    an in-flight source's edits before the snapshot. Verified: the restart reported
    `recovered interrupted source People - Chris.md` and committed nothing from it.
    **Status.** Applied (5f390bb).

    ### Held sources could not be retried by re-dropping them — 2026-10-05
    **Problem.** Eligibility skipped any (name, hash) already in the ledger, failed rows
    included. V1 counts only converged rows as processed.
    **How it showed.** The five outage-held personal sources, moved back to _inbox/, would
    have been ignored.
    **Fix.** Eligibility excludes converged content only. Re-dropped, all five converged.
    **Status.** Applied (2f90b2c).

    ### Removing quoted text from a source: the citing pages kept it, marked superseded — 2026-10-05
    **Problem.** The CP3 open item: what happens to quotes when a source loses the passage
    they came from.
    **How it showed.** One bullet removed from the personal note on Ken. Four pages quoted
    it (ken, wiki-weaver, samuel-lee, team-pulse, plus the source summary); all were
    selected because they cite the source, and every quote stayed, wrapped in a superseded
    marker and still citing `s11`, the old version, with a note that s26 dropped the line.
    `resolve.md`, which cites other passages of the note, was not touched. `lint`: 0 errors.
    No page lost a line; no rewrite was needed.
    **Fix.** Prior versions retained at `.wiki/source-versions/s<id>.md` so the old id still
    resolves.
    **Status.** Applied (d9a8b6d). Writer input for that source was 112 K characters.
