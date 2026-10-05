# WikiWeaver — Vision (DRAFT)

*Written for the platform owner first and the team lead second. Terms of art are
defined where they first appear; the specific promises live in `contracts/`.*

---

## What WikiWeaver is

Teams and individuals accumulate hundreds of meetings, chats and documents. What was
decided, what changed, who owns what — it is all in there, and nobody can hold it in
their head. WikiWeaver is the knowledge layer an agent keeps for you over those
sources: one directory of linked markdown pages, organized around what you care
about, better each time you correct it, and cheaper for an answering agent to consult
than the sources themselves.

You drop sources into an inbox. WikiWeaver reads each one once, writes a summary of
it, and works what it learned into the topic pages it touches. Pages accumulate: new
material is added, a change carries its date, and a superseded position stays
readable. The sources sit beside the pages, unmodified.

Pages point back to their sources. Each page lists the sources it draws on, and a
specific claim — a date, a number, a name, a commitment, a position — cites the source
it came from with a few quoted words, so an agent can jump to the exact spot.
Citations are for following a thread, not proof; nothing is verified sentence by
sentence.

What steers it is the **lens** — a short, human-written file that says what this wiki
is for: its purpose, what matters, the people, the questions it is asked, the kinds
of page it keeps, and who owns it. A lens is either known in advance, written from a
stated purpose, or drawn out by a short interview. Team Pulse is one lens, a personal
knowledge base another, a project's history a third; the pipeline is the same.

A **proxy** — an agent primed by the lens — sits in the reviewer's seat, so the wiki
runs unattended. A person takes the seat back whenever they like: they answer the
brief for a source themselves, or read the output later and say what is wrong. Either
way the **correction** is applied to the pages it concerns, kept with its reason, and
honored by every run after it. That is the learning loop.

Three things carry it: a lens known in advance, a lens drawn out by interview, and a
learning loop.

Several roles meet here. The **lens owner** decides what the wiki is for. **Upstream
apps** turn one kind of source into markdown and drive WikiWeaver as their synthesis
engine. RepoWeaver is one: it turns a repository's merged PRs, commits and module
snapshots into sources, one time window at a time, and has its own commands and its
own Resolve pipelines. A conversation weaver for meetings and chats would follow the
same shape. The **resolver** carries the corpus into a Resolve job and back.
WikiWeaver writes and keeps the wiki. The **answering agent** reads it. The through-line is the lens and its corrections: the one text a
person writes and every model call obeys.

---

## Principles

1. **The lens is the steering wheel.** Nothing deployment-specific lives outside it.
   The same lens change helps one corpus and hurts another, so each deployment is
   judged on its own.
2. **Judgment happens before the write.** A lens-primed brief decides what matters in
   a source; no model grades a finished page. Grading a page cannot change how it was
   written.
3. **Pages accumulate; history stays visible.** Replacing a page with its newest view
   once hid nearly half of what had been extracted, and the pages looked fine.
4. **Checks are deterministic, and they route.** A small script passes a write, sends
   it back once, or holds the source. A guard that cannot fail is not a guard.
5. **A correction given once is honored thereafter.** This is how a person's judgment
   stays in the loop without the person staying in it.
6. **Summaries are pointers, not testimony.** For a date, number, name or commitment,
   the answering agent follows the citation; for orientation and synthesis, the page
   is enough. Telling it to verify everything makes it pay twice.
7. **Policy grows in the lens, never in the prompt.** Prompts are short and state
   intent; the model works with latitude between deterministic edges. A rule written
   as a prompt sentence gets ignored.
8. **Every model call is a node in the graph.** The pipeline is a dot graph on the
   dot-runner engine, the engine Resolve runs. Anything that is not a model node is a
   plain shell step, so the graph runs anywhere that engine does.
9. **Source-specific preparation lives in upstream apps.** WikiWeaver stays general; a
   new kind of source means a new upstream app, never a branch inside WikiWeaver.
10. **The answer is what gets measured.** Answers and cost per answer decide;
    structural counts are tripwires only, never targets.

---

## What this deliberately resists

- **Per-sentence verification and byte-level receipts.** They can be built, and they
  do not change what a reader or an agent gets.
- **A wiki written for humans to read.** Agents read it. A readable layer would be a
  new decision, recorded as one.
- **An editorial or "journalist" voice.** Specifics beat tidiness; a commitment is
  never smoothed into a vaguer one.
- **Tuning for wall time ahead of quality.** Time and model passes are recorded on
  every run; speed is never bought with the answer.
- **Converting an older wiki.** A wiki is rebuilt from its retained sources, which
  every version keeps.
- **Knowledge about a particular source living in WikiWeaver.** It belongs in an
  upstream app or in the lens.

---

## How you can tell it is working

- **A Team Pulse user** asks what patterns are emerging, where work is being
  duplicated, or who assumes someone else is handling something, and gets a named
  framing with citations instead of a fresh rebuild from transcripts. Judged by
  feedback, it is *"as good, if not better"* than what the team had. A correction
  they give once never has to be given again.
- **A personal user** runs the same pipeline under a different lens with no code
  changes, never supervises an ingest, and can read in the log what was not ingested
  and why.
- **An upstream-app author or platform owner** swaps WikiWeaver in without noticing: the same
  verbs, the same directory names, the same done signal, one job per tarball.
- **A maintainer** finds every model call in a graph node, sees the loss guard fire on
  a real loss, rebuilds any page's history from version control, and compares answers
  and cost per answer against grep over the raw sources.

---

## Changelog

- **2026-10-02** — First draft, distilled from the agent-facing plan (`docs/PLAN.md`)
  for human readers. Evidence: the plan runs past six hundred lines, too long for the
  people who must agree to it.
