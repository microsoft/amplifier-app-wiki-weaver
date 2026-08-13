# Known issues — circle back

## 1. Pipeline runs emit no Context Intelligence telemetry

**Found:** 2026-08-05, during live monitoring of the PROXYFIX run.

Pipeline invocations pass `--cwd <lab>`, so every session they spawn registers
under the lab directory rather than the project workspace. Result: **a 5-hour,
32-source run produces zero CI session data.**

Confirmed by `context-intelligence:graph-analyst`:

> Workspace `-home-bkrabach-dev-wiki-weaver-improvements` had exactly 2 sessions
> active in the window — the monitoring session and the analyst itself. No
> workspace containing `PROXYFIX` exists on any of the three sources. This is
> not a CI outage — spark-1 ingested 11 other workspaces within the same second.

**Cost:** every health check on a running job is filesystem forensics
(`ledger.jsonl`, `.ai/*`, `E*.log`, process state) instead of one Cypher query.
Tool-level time attribution is impossible. Errors are only visible if they
happen to reach a log file.

**Worth fixing because** these runs are long, expensive, and the failure modes
we actually hit (a mid-run dependency break, a gate refusing every source, a
silent quarantine loop) are exactly what session telemetry surfaces early.

**Not yet investigated:** whether the hook can be pointed at the project
workspace while `--cwd` stays on the lab, or whether the lab dir needs
registering as its own workspace. Either could be right; neither is tested.

## 2. `amplifier` cache can update mid-session and break the venv

**Found:** 2026-08-05, 10:45 — the PROXYFIX run died at launch on:

```
cannot import name 'resolve_bool_attr' from 'amplifier_module_loop_pipeline.graph'
```

A 3-source run had passed cleanly 90 minutes earlier. Between the two, the
amplifier cache refreshed `amplifier-bundle-attractor`, bringing in code that
calls `resolve_bool_attr`. The venv still held a **2026-07-25** copy of
`amplifier_module_loop_pipeline` without that symbol.

**Fix applied:** reinstall the module from the refreshed cache.

```bash
.venv/bin/python -m pip install --force-reinstall --no-deps \
  ~/.amplifier/cache/amplifier-bundle-attractor-*/modules/loop-pipeline
```

**Why it matters:** it failed loud and cost about ten minutes. Had it landed
four hours into the run rather than at startup, it would have cost the run.
Worth a preflight import check before any long run, and worth understanding why
the cache refreshes without reinstalling into venvs that depend on it.

## 3. Segment 2+ almost always quarantines

**Found:** 2026-08-05, in a 73-source production run.

Across that run, quarantine decisions split by segment index like this:

```
quarantines by segment_index   {2:7, 3:1, 4:1, 6:1, 7:1, 8:1, 9:1, 10:1}
accepts     by segment_index   {1:73, 2:3, 5:1}
```

**Segment 1 quarantined zero times in 73 attempts. Segments 2+ quarantined 14
times against 4 accepts** -- a 78% failure rate on everything after the first
segment. 7 of the 10 multi-segment sources produced at least one quarantine.

Each quarantine costs three reweave attempts before it gives up, so a source
that splits into 10 segments burns roughly 30 wasted weave cycles.

**The content is not lost.** Verified separately: pages from quarantined
sources carry their `(segment 2)` sections, and re-ingesting a quarantined
segment produced `created=0 updated=0`. The cost is time, not data.

**Mid-run I raised `segment_max_bytes` from 80,000 to 220,000 and the
quarantines stopped** -- 27 subsequent sources, zero multi-segment, zero
quarantines. That is a **mask, not a fix**: it works by making almost every
source a single segment, so the failing path is simply never taken.

The 80,000 default has an articulated rationale (see the comment above
`DEFAULT_SEGMENT_BYTES` -- measured against 110 Teams transcripts, mean 65.9KB,
median 49KB, max 241.5KB, chosen so the common case stays single-segment and
the worst case bounds to 4 segments at ~20K tokens each). **That reasoning is
sound and the default has been left alone.** Raising it trades a bounded
context for an unbounded one, and the evidence for 220,000 is one corpus of
meeting transcripts.

**The question worth answering is why segment 2+ fails validation when segment
1 never does.** The likely suspects are that a mid-source segment lacks the
header/context segment 1 carries, or that validation expects a shape only a
source-opening has. Until that is understood, any threshold change is guesswork.

## 4. Segmentation runs before the watermark filters

**Found:** 2026-08-05, same run.

A stream source re-pulled on a later date has the same `Chat ID`, and the
watermark correctly records the prior pull. But **segmentation splits the whole
file before the watermark filters anything**, so a 705KB chat whose delta is 28
turns still becomes 10 segments and costs 10 weave cycles.

Measured across 14 re-pulled streams in that run:

```
re-pulled bytes being re-segmented   2,300KB
genuinely new content                   ~4KB   (0%)
streams with ZERO new turns            8 of 14
```

One stream (`Show and Tell MADE`) had actually *shrunk* between pulls, 174KB to
154KB, and was still fully re-segmented.

Worked around by pre-trimming the sources to header + new turns before staging
(1,657KB -> 26KB), which is a caller-side fix and does not belong in every
caller. Consulting the watermark before splitting would make the pipeline do it.

## 5. Weave emits bare display-title wikilinks for pages it did not create

**Found:** 2026-08-06, in the first minutes of a fresh-start A/B run.

A newly-built wiki at 6 pages reported 20 structural issues, all of this shape:

```
broken link: amplifier-as-agent.md -> [[Adapter Pattern for Host Harnesses]]
orphan:      adapter-pattern-host-harnesses.md has no incoming wikilinks
```

Both lines are about the same page. It exists. Three pages link to it. The
checker cannot connect them because the link carries the **display title**, not
the slug -- `[[Adapter Pattern for Host Harnesses]]` resolves to
`Adapter Pattern for Host Harnesses.md`, which is not a filename this wiki
would ever produce.

This is distinct from issue #3 (anchor fragments) and is **not** fixed by that
change. It is also not fixable checker-side by slugifying the title, because
the weave's slug convention is not a pure slugify -- it drops stopwords and
parenthetical suffixes:

```
slugify("Adapter Pattern for Host Harnesses")   adapter-pattern-for-host-harnesses
actual file                                     adapter-pattern-host-harnesses      ("for" dropped)

slugify("Amplifier Agent Workstream Chat (May-Aug 2026)")
                                     amplifier-agent-workstream-chat-may-aug-2026
actual file                          amplifier-agent-workstream-chat               (suffix dropped)
```

Two of four test cases fail a naive round-trip. The checker cannot reconstruct
a slug it did not generate.

**The fix belongs in the weave:** emit `[[slug|Display Text]]` using the slug it
actually created, which is what it does everywhere else. The shipped 130-page
wiki carries 1,686 links in proper `[[understudy-resolver|Understudy Resolver]]`
form with zero broken -- so the weave gets this right when the target already
exists, and falls back to a bare title when writing a forward reference.

**Consequence while unfixed:** `validate.py` returns exit 1 on any wiki
containing a forward reference, which re-arms the reweave/quarantine path that
issue #3 was about. Cost is wasted cycles, not lost content.

**Not fixed during the A/B run that found it.** Both arms run byte-identical
code and hit it equally, so it cannot bias the comparison -- and changing weave
behaviour mid-experiment would change the thing being measured.

## 6. Long chat sources lose whole months in synthesis

**Found:** 2026-08-06, auditing the shipped 130-page wiki after a blind judge
flagged that chat-page titles overstate what the pages deliver.

Five chat source pages carry a title spanning months their body never covers.
Every one is a **synthesis gap, not a source gap** -- the raw transcript has
dated activity for the missing month:

```
page                                    missing   source has
amplifier-resolve-team-chat             Jul       24 Jul date-headers
basecamp-workstream-chat                Jun/Jul   10 Jul headers (Jun genuinely empty)
context-intelligence-workstream-chat    Jun/Jul   26 Jun + 21 Jul headers
show-and-tell-made-chat                 Jun        5 Jun headers
team-pulse-weekly-planning-chat         Jun        4 Jun headers
```

Worst case is Context Intelligence: **1,293 substantive turns** across June and
July in the source, zero dates from either month in the page body.

**The content is mostly not lost from the wiki** -- it is represented on the
topic and person pages. Probing the missing Context Intelligence months against
the whole wiki, the dominant terms are well covered (`context` 111 pages,
`intelligence` 73, `sessions` 91). But coverage is uneven, and low-frequency
items disappear entirely: `redaction` appears 42 times in the missing months and
on exactly one wiki page (the index). `gh token`, `resolve-ux`, and
`cache image` from the missing July of the Resolve chat appear on zero pages.

So the per-source page is not a reliable record of what its source contained,
and the gap is invisible to a reader unless the title says so.

**Mitigated, not fixed.** Titles in the shipped package now declare the gap
explicitly -- `Context Intelligence Workstream Chat (May-Aug 2026, gap: Jun/Jul)`
-- and the README explains the convention and points at `sources/`. That makes
the limit honest; it does not recover the content.

**~~Likely cause, unconfirmed~~ -- CORRECTED (2026-08-06, later the same day):**
the segmentation/time-ceiling hypothesis above was investigated and is WRONG.
The real mechanism has nothing to do with weave, segmentation cost, or the
per-source time ceiling -- it never gets that far.

**Root cause: `select_source`'s ledger-by-filename resume gate, combined with
a feed that reuses the SAME filename on every periodic re-export instead of
embedding a growing date range in a new one.** `watermark.py`'s whole design
(this file's own `## 5`-referenced fix) assumes a growing stream always
arrives under a NEW filename -- verified true for the production Teams
export naming convention, but FALSE for this eval corpus's `epoch-feed/`:
`Team Pulse Weekly Planning__chat__pulled-2026-08-04-1352__2026-05-29_to_2026-07-31.md`
is the exact same path in `epoch-feed/E2` through `epoch-feed/E7`, growing in
place underneath it (E2 body: 951 chars; E7 body: 28,280 chars; `E7_body[:951]
== E2_body` byte-for-byte -- pure append). `select_source.compute_pending`
(`sources-on-disk MINUS ledger.jsonl ids`, keyed purely on filename) ledgers
this filename the first time it's selected (epoch E2, when it was 951 chars)
and then treats it as PERMANENTLY done -- `detect_kind`/`watermark`/
`segment_source` never run again for it, no matter how large the file grows
afterward. `advance_watermark_if_stream` correctly stamped 951 chars as the
watermark for what it saw at that moment; the file just never got looked at
again. This is why "all the affected sources show `accept` rows" -- nothing
ever failed, because nothing after the first small ingest was ever attempted.

**Verified against every source in the phenomenon table, not just the five
above** -- each one's `watermarks.json` value matches, byte for byte, the
body length of whichever `epoch-feed/E<N>` snapshot first got it ledgered
(`E1` in most cases; `E5` for Amplifier Leads, whose file first appears
later in the feed sequence). Two independent eval runs on different days
produced byte-identical watermark values for every shared source -- not a
race, simply the same static feed processed in the same order both times.

**This also explains the `segment_total: 1` anomaly noted separately for
the 352KB Context Intelligence source**: it is the SAME root cause, not a
second bug. At the moment it was actually segmented (epoch E1), its body
was 9,682 chars -- comfortably under `DEFAULT_SEGMENT_BYTES = 80_000` --
so one segment was exactly correct for what was fed in. The file simply
grew to 352KB afterward, unseen.

**Fix status:** detection landed in `select_source.py` (the
`StaleWatermarkGrowthError` / `growth_detected` sentinel -- see its module
docstring) -- an already-ledgered stream source that has grown past its
own watermark now fails the run loud instead of silently draining forward.
**Automatic recovery was attempted and proven UNSAFE**, not shipped:
reopening the ledgered filename for reselection collides with
`segment_source`'s per-`(source_id, segment_index)` ledger check, which is
not generation-aware -- a fresh delta re-split of the same filename lands
on the same segment index the OLD ledger row already marked done, so
`select_segment` reports `no_segment` before weave ever sees the new
content, while `advance_watermark_if_stream` still stamps the watermark at
the file's new FULL length. That would have recorded the growth as
durably covered while silently never weaving it -- worse than this bug.
Actually recovering the already-lost content (and safely handling any
future occurrence) needs generation-aware segment/ledger bookkeeping
(e.g. scoping segment identity by watermark position, not a bare
1..N index) -- real, non-trivial work, intentionally out of scope for the
small, safe fix that landed here.

**CORRECTION (2026-08-09) -- two claims above are wrong, and the proposed fix
addresses a case the real usage does not emphasise.**

*1. The guard's own error.* `find_stale_watermark_growth` was described as
detecting a source grown past **its own** watermark. It did not check that. The
store is keyed by IDENTITY; the guard iterated FILES and compared each against
whatever record the identity currently held, never checking
`record["source_id"] == sid` -- a field `advance_watermark_if_stream` has always
written and nothing read. Several files routinely share one identity, so an
untouched file tripped the guard the moment a SHORTER window of the same
conversation stamped the record. Reproduced: file A ledgered at 2165 chars, a
shorter window B stamps 227, A is then reported as "grown from 227 to 2165" with
nothing on disk changed -- halting the whole run on healthy files. Fixed; the
regression test fails without the change.

*2. The `no_segment` mechanism is half the story.* That account holds when the
delta re-split yields FEWER segments than the old ledger. When it yields MORE you
get a PARTIAL weave -- some new segments woven, the remainder silently dropped,
watermark still stamped at full length. Reproduced: pass 1 total=2, pass 2
delta_total=78, segments 3..12 woven, 391 of 400 new days woven, the rest lost.
Harder to catch than the documented no-op because the output looks plausible.

*3. Generation-awareness fixes same-filename growth, not the real case.* The
intended usage is windows of a conversation arriving over time, in arbitrary
order, with overlap. A scalar offset cannot represent a set of intervals.
Measured: windows fed [d1-d2], [d5-d6], [d3-d4] produced three FULL re-ingests,
no delta ever computed, no gap reported. `watermark_chars` is not a high-water
mark -- it is the length of the last body seen, and it moves backwards.

**Both unknowns behind the estimate are now measured:**

- *Unit identity.* No per-message ID in the export. But turn text is byte-stable
  across re-exports -- 7,932 turns shared between pulls on different dates, zero
  drift. `(day, HH:MM, speaker)` addresses a turn uniquely; a body hash detects
  change. No normalisation design needed.
- *Discontiguous deltas are SAFE.* The generator honours a gap even with no
  structural boundary at the seam, and names the marker's reasoning back: "the
  segment has a watermark gap indicating earlier content was already woven."
- *Overlap is UNSAFE.* One day fed twice, marked as overlap, produced a verified
  fabrication -- a 2026-07-24 exchange presented as a 07-31 session, including a
  participant with zero turns that day. Zero repeated sentences, no marker
  leakage: invisible to a mechanical check. Overlap must be removed structurally
  before the generator sees it, not suppressed by instruction. A coverage-set
  model gets that for free.

See `corpus-scrubber/findings/EXPERIMENT-A.md` and `EXPERIMENT-B.md`.


## 7. Synthesis has no notion of person-sensitivity

**Status:** open, structural. Found during a pre-share safety review, 2026-08-08.

A pre-share review of a 253-page wiki built from 164 team transcripts found
roughly **forty passages** that should not circulate team-wide. Four independent
reviewers, then a second line-by-line pass over all 26 one-on-one and planning
recordings. Categories: health and personal circumstance, formal
performance-review content, candid assessment of named individuals discussed in
their absence, and people outside the team discussed candidly.

The pipeline had no mechanism to catch any of it. It ingests whatever it is
given and synthesizes faithfully.

**The synthesis layer is worse than the raw layer, not better.** Two findings
make this concrete:

- A concept page gathered scattered moments across three months into a named
  "recurring failure pattern," with one engineer the subject of three of its five
  instances. **No source transcript makes that claim.** Compression manufactured
  it, and `overview.md` -- the most-read page -- independently re-argued the same
  pattern with its own instance list and a methodology defense. The page was
  deleted and 18 inbound links repaired.

- Formal performance-review content ("setback in terms of impact," career-level
  expectations) landed on an engineer's own bio page, where colleagues read it as
  a characterization rather than a decision he made.

Sharpest detail: the wiki's own `personal-knowledge-architecture.md` page
documents the team's stated policy that performance discussions, 1:1
conversations, and personal reflections must never cross into team knowledge.
**The pipeline recorded that policy and then violated it four times.**

Remediation cost: two full agent passes plus a line-by-line read of 26
transcripts, 2 pages deleted, 24 links repaired, 32 redaction markers placed.
Entirely manual. It will cost the same on every future run.

**What a fix has to distinguish.** Not a keyword filter -- two keyword-driven
passes each reported the same gap and each missed items the other caught, and the
line-by-line pass then found five more. The signal is structural: someone
described in the third person, evaluatively, while absent. Two cases show the
boundary:

- *Kept:* a speaker's repeated references to an "ADHD mode" -- the name of a
  feature he built and documents openly across six wiki pages. His own product
  narrative.
- *Removed:* a different speaker's incidental disclosure of the same diagnosis
  while explaining a work struggle. An unplanned personal disclosure.

Same word, opposite verdicts. A rule keyed on terms cannot separate them; only
one keyed on *who is speaking about whom, and whether they chose to* can.

Blunt technical criticism must survive. Architecture disagreements, someone being
wrong about a design, frustration at a system -- that is the record the wiki
exists to preserve. The line is criticism of *work* versus characterization of
*people*.

**Minimum viable fix:** a gate that flags, per source, whether it contains
third-party evaluative content, and refuses to synthesize a person page from a
source so flagged without explicit review. Loud, not silent -- consistent with #6.

---

## 8. A cache refresh mid-run breaks a venv installed from that cache

**Status:** environmental, fixed for this venv, will recur. Found 2026-08-11 by
the epoch it silently skipped.

A 215-source chronological run completed E01–E06 normally, then E07 ingested
**zero of its 27 staged sources** and the driver wrote `ALLDONE`.

E07's log:

```
Failed to load module 'loop-pipeline': FAILED: 0/1 checks passed
  module_importable: No module named 'amplifier_module_loop_pipeline.preflight'
attractor-aitl: Session initialization failed:
  Cannot initialize without orchestrator
```

**Root cause.** The project venv holds a *non-editable* install of
`loop-pipeline` taken from the amplifier cache:

```
direct_url.json -> file:///home/<user>/.amplifier/cache/
                   amplifier-bundle-attractor-<hash>/modules/loop-pipeline
```

The cache refreshed at **08-11 05:17** — the same morning the run was
relaunched — adding `preflight.py` to the module and updating the loader that
demands it. The venv's copy was installed **08-05** and had no `preflight.py`.
E01–E06 ran against the old loader; E07 was the first epoch to start after the
refresh, and it hit the new one.

**Why it is easy to miss.** The failure is invisible from inside the run:
`amplifier` itself keeps working, other epochs completed normally, and the only
artifact is one epoch's log. Nothing in the wiki looks wrong — a month of
content is simply absent.

**Fix (per venv, when it happens):**

```bash
.venv/bin/python -m pip install -q --no-deps --force-reinstall \
  ~/.amplifier/cache/amplifier-bundle-attractor-<hash>/modules/loop-pipeline
```

Confirm with a file count against the cache source before and after — the stale
install here had 25 `.py` files against the cache's 26.

**Durable mitigations, in order of preference:**

1. **Editable-install the module** (`pip install -e`) so the venv follows the
   cache rather than snapshotting it. Trades a stale-code failure for a
   changing-underfoot failure, which at least fails loudly and immediately.
2. **Pin the module by copying it out of the cache** into the project, and
   update deliberately. Best for long multi-day runs where mid-run drift is the
   thing you most want to avoid.
3. **Smoke-test module load at the start of every epoch**, not just the run.
   Cheap, and turns a silent skip into an immediate loud failure.

**Related — the driver hole this exposed.** The epoch driver read the terminal
reason out of each node's `status.json`. When session init dies *before* the
pipeline starts there is no run log, no `status.json`, and no sentinel to find,
so the guard saw nothing and the drain loop concluded the corpus was empty.
Two conditions were added: *no run log produced* and *ingested nothing while
this epoch's sources remain unledgered*. See `corpus-scrubber/run-epoch.sh`.

**Meta-lesson worth more than the fix.** The first version of that guard was
validated fires-on-bad and quiet-on-good, but never against **no-output-at-all**
— which is precisely the case that lost 27 sources. Then the *replacement* guard
shipped with an unquoted-heredoc bug (`.split("\n")` reaching Python as
`.split("n")`) that made it fire on a run which had ingested everything
correctly.

A detector that never fires and a detector that always fires are the same
defect. Validate both directions, and validate the third case: **no signal at
all.**

---

## 9. Weave writes the same section twice into one page

**Status:** root-caused, detector shipped, prevention not available. Found
2026-08-12 by an overlap experiment that was looking for something else.

Across eight ingest epochs the generator emitted byte-identical `## ` sections
into the same page. Nothing ever noticed.

```
pages with repeated ## headers    5 of 350
duplicate section instances      36    (8 from the newest batch, 28 older)
```

Worst case, `team-pulse.md`:

```
L1955  ## Repo-Weaver Design for Team Pulse — Inbox-Based Architecture (2026-08-11)
L2024  ## Repo-Weaver Design for Team Pulse — Inbox-Based Architecture (2026-08-11)
```

Byte-identical heading, byte-identical body, **same single source cited by both
copies** — not two sources merged badly. One source, written twice.

A reader cannot distinguish a re-emitted section from a genuine second
discussion of the same topic. That is what makes it expensive: the page looks
longer and better-sourced than it is.

### Root cause — the LLM's own output, not a deterministic code path

Ruled out in order:

| Suspect | Why it is not this |
|---|---|
| `write_gap_page` | Full `atomic_write_text` overwrite. Also: none of the affected pages carry `type: theme`, so it never touched them |
| `persist_lens` (correction pipeline) | `lens/corrections/` is empty — it has never run against this wiki |
| `retract.py` absorb mode | Tags its output `## Absorbed from <page>`. No such heading exists anywhere |
| `select_source` re-ingest | Ledger holds exactly one `accept` row per affected source. A re-ingest would show a second row or a `watermark_reset` |

What remains is `weave`'s own prompt (`pipeline/ingest.dot`), which mandates
**two unconditional writes per source** — per-entity updates, and one page
capturing what the source says as a whole — with nothing instructing it to check
whether those two resolve to the *same page*.

When a source's subject matter is also one of its extracted entities — a Team
Pulse workstream chat whose entity *is* Team Pulse — both mandatory writes land
on the same page, in the same turn, each narrating the same material under a
heading derived from the same source.

That mechanism predicts the observed split, which is the strongest evidence for
it: **6 pairs byte-identical, 12 reworded.** The reworded ones are two
independent synthesis attempts at one source — different bullet structure,
different emphasis, occasionally a different claimed meeting duration — under
the same heading with the same citation.

### The fix — catch it, because prevention is not available

`weave` is a raw LLM box with file-editing tools. Nothing short of removing its
autonomy makes duplicate output *impossible to attempt*. What is now impossible
is **committing** it.

`find_duplicate_sections_in_page` joins broken-links, orphans, and zero-touch in
`validate`'s issues list. A page with a duplicate section fails `structural_bad`
and routes to the existing bounded reweave retry. No new machinery — the same
discipline as `retention_check.py`'s shrinkage detector: detect
deterministically, never trust the LLM to grade itself.

A prompt clarification naming the entity-vs-source-page collision was also
added. That is best-effort mitigation, explicitly **not** the load-bearing fix.

### Detector proven in both directions

Exact match on the stripped heading. Deliberately not fuzzy, and deliberately
**not** dependent on body similarity — the 12 reworded pairs prove body-diffing
alone would miss real duplicates.

```
fires on    5 pages / 36 instances — matches an independent measurement exactly
quiet on    345 pages
near-miss   "3 Outcomes" vs "4 Outcomes", "## X" vs "### X"  -> no false fire
unicode     'Résumé Review — Déjà Vu — "Quoted" Title'       -> fires on a real dupe
```

That last row is not decoration. **Three checkers in this project have shipped
broken in exactly this way** — one split filenames on whitespace, one had an
unquoted heredoc that mangled `.split("\n")` into `.split("n")`, one used a
pattern so loose that everything matched. Validate against real inputs
containing spaces, em-dashes, and non-ASCII, or the detector is theatre.

### What is still open

6 byte-identical pairs were removed after programmatic byte-equality
verification. **12 reworded pairs remain** and need a human read — deciding
which of two write-ups is more accurate is a content judgement, not a
deduplication.

### What could not be settled

Which of the two mandated writes produced which copy. The pipeline logs record
provider-level chatter, not per-tool-call file paths, so there is no captured
trace of the two write calls. The account above is the mechanism that fits every
piece of evidence without contradiction — converging inference, not a
transcript.
