# Findings — current perspective

**Not a changelog.** This is what we believe *right now* about building a compiling wiki.
Superseded items are deleted, not struck through. Last updated 2026-07-30.

---

## 1. The wiki earns its keep only where raw search fails

Measured on four corpora, blind-graded against ground truth authored from raw sources
before any wiki existed. "Lift" = wiki+sources vs sources-only, same grader session.

```
corpus         raw rubric  raw cov   wiki lift   what the raw corpus looks like
transcripts        1.90     0.30       +1.70     110 sprawling 3-hr meetings, 7.2 MB
sessions           3.00     0.53       −0.10     60 structured session renders
reposnap           3.70     0.76       −0.20     27 snapshots, predictable headers
articles           3.90     0.84       +0.20     50 titled, well-structured essays
```

**Read the raw column, not the wiki column.** The wiki's value scales *inversely* with how
searchable the sources already are. It was never "compilation beats retrieval" — it was
"compilation beats retrieval **when retrieval is hard**." Meeting transcripts are hard: the
answer is one line buried in a three-hour conversation with no title and no structure.
Articles have titles. Grep already wins there.

If you are choosing whether to build one of these: look at whether a competent person with
grep can already answer your questions. If yes, a wiki adds cost and risk, not value.

---

## 2. The biggest quality win was one paragraph in a prompt

Nine arms measured at N=110. The two that won were both prompt-level. Every structural
intervention — page splitting, routing changes, sibling cross-links, cross-episode
threading — did nothing or actively backfired.

**The DISAGREEMENT CONTRACT** (+1.20 rubric, on its own): instruct the writer never to
collapse a two-sided exchange into a settled conclusion. Phrases like *"the team converged
on X"* erase who objected and why. Record who held which position, and whether it resolved,
was dropped, or was left open.

Why it matters: a summarizer's default is to smooth. Six weeks later the thing you need is
exactly the part that got smoothed away.

**The HYBRID QUERY FLOW** (+1.80 vs raw): the wiki is a navigation layer, not a
replacement. Locate the relevant page, then *follow its citations into the raw sources* and
answer from those. Following citations added a fact the wiki had compressed away on **9 of
10 questions**.

The wiki reliably preserves the decision and the winner's position. It reliably drops the
rationale, the motivating example, the scope qualifier, and who-said-what-in-what-order.

---

## 3. Fixed-k retrieval silently caps compounding

We had `DEFAULT_K = 6` on the BM25 slice. The writer knew every page (the catalog lists
all) but could only *read* six — and you cannot merge into a page you have not read.

```
wiki pages   fraction readable at k=6
     2               100.0%
    16                37.5%
    45                13.3%
   751                 0.8%
```

**Measured consequence — touches per source *declined* as the wiki grew:**

```
transcripts run   first quarter: 3.03 touches/source
                  last  quarter: 2.19 touches/source
```

That is anti-compounding, the exact opposite of the design intent. It was invisible in
every quality metric we ran for weeks, and it gets worse precisely as the corpus becomes
valuable.

Fix: budget by **bytes**, not page count. Page sizes vary 10x across corpora (~14 KB for
articles, ~138 KB for transcripts), so a fixed count starves one and floods the other.

---

## 4. Touches per source is a health metric — but it stopped discriminating

A wiki that files one page per source is a filing cabinet. Page-touches per ingested source
distinguishes filing from compiling, and moving it took real work:

```
arm                          touches/src   pages   overview.md maintained
one page per source                 1.96      45   49/51  (incidental)
+ entity decomposition              5.33     114    3/51  (regression)
+ pre-write steering                5.53     105    4/51
+ claim page type                   5.08      99   14/51
+ restored source-level page        7.83     157   47/51  (explicit)
reference implementation            5.49     751        —
gist                             "10–15"       —        —
```

Two things that took us a long time to learn:

**The slope matters more than the level.** An early run averaged 2.63 touches/source while
*declining* the whole way — the level was mediocre, the slope was the real defect. A flat
mean hides that completely.

**But at n=1 per arm, small differences are noise.** Bootstrapped 95% CIs on the per-source
trajectory overlap zero *and* overlap each other; per-source stdev is ~1.8 against a mean of
~5.4. We nearly reverted a change on a slope difference the data could not support. Anything
under ~1.0 touches/source needs repeated runs, not a single measurement.

Caveat on the gist's 10–15: that is Karpathy's **supervised, one-source-at-a-time** figure,
and it is the *sum* of a source-level summary page plus N entity pages — not entity pages
alone.

---

## 5. Touching more pages is not the same as forming themes

This is the finding that cost us the most to learn. Across four wikis over the same
50-article corpus, tested identically:

```
wiki                        pages   single-source (entity layer)   themes with a real page
one page per source            45              —                    0 of 3 largest
entity decomposition          114           67.5%                   0 of 3
+ claim page type              99           68.7%                   1 of 3
+ source-level restored       157           68.8%                   1 of 3
```

**The entity layer never changed.** Restoring the source-level page raised touches by 47%
and added 46 useful summary pages — but it sat *on top of* an unchanged entity layer, and
because per-source pages are single-source by construction, the headline ratio got worse
(77% of all pages cite one source).

**The pattern in what gets a theme page is exact.** Every concept page with 4+ sources
corresponds to a phrase an author put in a title: "Harness Engineering," "Agent Skills,"
"Loop Engineering," "Spec-Driven Development," "Mixture of Experts." Every theme with *no*
page is one nobody named: token economics (13 articles, 9 authors), local inference on
consumer hardware (11 articles, 8 authors), human judgment as the irreducible layer
(8 articles, 6 authors).

> The wiki is a very good index of the corpus's **vocabulary** and a poor index of its
> **arguments**. It can gather a theme a human already gathered. It cannot find one nobody
> named.

**And the whole-corpus artifact does not compute.** `overview.md` is now 908 lines,
maintained on 47 of 51 commits, and factually accurate where spot-checked. It is also
organized by *arrival order* — one subsection per ingested source, in sequence, with the
dominant sentence form *"this source extends/complicates the corpus's treatment of X."*
There is **not a single count, weight, or ranking in 908 lines**. It logs deltas; it never
weighs masses. The one artifact whose declared job is the whole-corpus view is structurally
incapable of producing one.

## 5. Failure modes worth knowing about

**Quiet omission of the operative clause.** A page said only *"as Sam requested."* The
cited source said *"this is 100% an ask, like, do not build any of that. UI."* The wiki
stated nothing false and read as complete. You cannot detect this from inside the wiki —
it is the strongest argument for the hybrid query flow.

**Table flattening.** Prose changes get a timestamp and a "later superseded by" clause.
Markdown tables get silently merged, latest-wins, undated. We caught a wiki pasting a
four-month-old resolver table into a page describing the current model, and promoting an
unmerged PR's row into a table framed as shipped. Every wrong answer was produced
*confidently*. If your sources contain tables, watch this specifically.

**Confident false negatives.** An answering agent that searches only the cited-source
subset will assert "the sources do not record X" when it simply did not look. The cited set
is a *starting point, not a boundary*.

**Silent source dropping.** A 50-source run reported `EXIT=0` having ingested 46. Four
sources were marked `skip` and never surfaced — and the wiki's own overview described its
scope as "forty-six sources" without disclosing the gap. 8% of the corpus vanished,
including one of its densest synthesis articles, and every downstream reader would have
believed the wiki was complete. Assert coverage against the *input set*, not the ledger.

**Silent success.** Two separate bugs where the pipeline reported `EXIT=0` having produced
nothing: `${var:-default}` swallowing CLI params, and the writer creating pages one
directory above where every downstream check looked. In both cases every component was
individually correct and the system was collectively blind. Assert on the *artifact*, not
on step completion.

---

## 5b. Invention rate — measured, gated, and wrong twice before it was right

This section has been corrected twice. Both corrections were the same mistake at different
depths, and the mistake is more instructive than the number.

**The measured truth, computed by `evals/invention_rate.py` over all 12 graded runs:**

```
                        n    invented   rate
wiki arm              140       24       17%
raw  arm              120       18       15%
```

**The wiki arm invents slightly MORE than the raw sources it claims to replace.**

Per-run, with a relative-to-raw gate (wiki must not exceed raw x1.5):

```
PASS   articles, dissplit, growth[N110], hybrid, reposnap, split
FAIL   dis, grading, growth[N10], idx, sessions, sib
N/A    thread (no raw arm)
```

**Six of twelve runs fail.** The gate is relative rather than absolute because the claim under
test is substitution: no single absolute ceiling both passes the 40% transcript case and fails
the 8% aggregate without being arbitrary.

### The two wrong versions, and why they were wrong

**Version 1** claimed *"invention rose with scale"* and cited a 40% wiki rate as higher than raw.
That came from a headline in a raw results file that **summed two arms together** (4+4 wiki vs
2+3 raw). Recomputing per-arm shows the wiki was **flat** — 40% at N=10 and 40% at N=110. The
*raw* arm rose, 20% to 30%.

**Version 2** corrected the arms but reported *"wiki 8% vs raw 15% — the wiki invents less."*
That number came from a script that **silently skipped 8 of the 12 runs** because their arms are
named non-uniformly (`dis`, `split`, `sib`, `idx`, `thread`, `wiki-N10`, ...). It measured four
easy corpora and reported the result as global.

Both versions were built on real numbers. Both conclusions were wrong. **Verifying that a figure
exists in a file is not verifying what it covers.**

The 8%/15% figure is still true *for those four corpora* and the tool reports it explicitly as a
subset cross-check. Scope is the whole finding: on articles, hybrid, reposnap and sessions the
wiki is safer than raw; add the ablations and the transcript runs and it reverses.

### What survives, verified independently at page scale

```
attributed sources    verdict
    <= 7              honest — every sampled source verified, quotes verbatim
   13                 diluting — "compatible with" counted as "takes a position on"
   17-18              degraded — 2 of 6 sampled weak-to-wrong, one FABRICATED
```

The fabrication is concrete: a page claimed a source argued its point *"regardless of model
version."* That phrase is not in the source, and **the source does not contain the word "model"
at all.**

### What follows

- **Invention rate is now gated.** `python -m evals.invention_rate evals/compounding --gate all`
  returns a CI-usable exit code. It should run on every graded run.
- The tool refuses to silently drop runs — unknown arm names come back `UNCLASSIFIED` with a
  visible count. That behavior exists specifically because its absence produced version 2.
- Cap page promotion around 8–10 attributed sources, or require verification above that line.
- Deduplicate candidates semantically, not by exact term string.

## 6. What we got wrong, so you don't repeat it

- **We fixed the wrong end of an axis.** Adding a full-catalog view to the writer took us
  from "never creates pages" (14 from 110 sources) to "always creates exactly one" (45 from
  50). It was a create-enabler with no merge counterpart. Both extremes are wrong.
- **We measured fact retrieval and called it synthesis.** Our ground truth was lists of
  discrete attributable facts. Grep is *excellent* at fact retrieval — so we built a test
  that measures what raw search is best at and concluded raw search wins. Nearly circular.
- **We ran six arms inside a false dichotomy.** Wiki-only vs sources-only. The gist never
  asks for that: raw sources are the source of truth and the wiki *sits between* you and
  them. Three layers, not two.
- **Combining two proven wins made things worse.** Disagreement-preservation (+1.20) and
  byte-splitting (+0.60) are individually good and *fight each other*: splitting severs a
  position/counter-position block, which is a coherent retrieval unit. −0.40 combined.
  Test combinations; do not assume additivity.

---

## 7. Open, honestly

- **No step forms a whole-corpus view.** Every LLM pass is scoped to one source, or one
  segment of one source. In the gist's loop the human is the only participant who ever holds
  more than one source in mind — *"curate sources, direct the analysis, ask good questions,
  and think about what it all means."* We automated that role away and put nothing in the
  slot. A pipeline where nobody holds the corpus cannot emit a corpus-level claim, and the
  gist offers no mechanism for automating it. It diagnoses the hole; it does not fill it.

- **The exit condition is a queue-drain predicate.** "Is the input empty?" — never anything
  about the wiki. Ten fragmented pages satisfies it perfectly, forever. We still have no
  machine-checkable definition of a *coherent* wiki.

- **`overview.md` needs to weigh, not log.** It is maintained and accurate, but it has no
  counts. Something in the pipeline has to rank clusters by mass before the whole-corpus
  view can exist.

- **The human-proxy is now built** (`src/wiki_weaver/aitl/`, 2026-08-01) — of the four
  training approaches named below, only bootstrap-by-interview (`init.dot`'s existing loop,
  now naming the persona's five required sections explicitly) and retroactive-steering (the
  pre-existing corrections mechanism, unchanged) are wired up; persona-import and
  interactive-run-recording remain unbuilt, named so they aren't assumed done. The proxy
  answers gates from `lens/persona.md` via a tool-free LLM call (no bash/filesystem/search —
  a capability restriction, not a request), fails loud (never guesses) when the persona is
  missing/incomplete or the backend can't ground an answer, and logs every decision to
  `.ai/gate-decisions.jsonl`. Selectable via `python3 -m wiki_weaver.aitl.run --gate-mode
  {fail,auto-approve,console,proxy}` — see `pipeline/CLI-CONTRACT.md`'s "AITL proxy" section.
  Still true as originally written: it steers *placement*, not *emphasis* — nobody yet reads
  across sources (see the bullet above this one).

  Original four training approaches, for reference: bootstrap by interview at init, import a
  persona from another wiki, record interactive runs as training exchanges, and retroactive
  steering via durable corrections. The design explicitly rejects a training pipeline:
  corrections persist to `lens/corrections/` and are re-read on every weave — never learned
  into weights.

- **A landmine ahead of any proxy work.** `AutoApproveInterviewer.ask()` returns the literal
  string `"auto-approved"` on freeform gates. Three gates are freeform and their text flows
  into `lens/canon/` and `lens/corrections/` — the two highest-precedence layers. Unattended
  mode would write `"auto-approved"` into canon and treat it as ground truth outranking
  every source. Our eval labs skip `init.dot`, so we dodged it; it is still live.

- **All quality numbers are n=10 questions per corpus.** Directionally useful, not tight.
