# Corpus Package Contract — v1 (DRAFT)

**Who builds against this:** the weaver resolver, which ships the corpus into Resolve
and back for both the Team Pulse wiki pipeline and RepoWeaver's repo pipelines;
RepoWeaver, a separate app that drives WikiWeaver from its own CLI and its own Resolve
pipelines — it writes the inbox and reads the done signal, the failed folder and the
ledger; future upstream apps (a conversation weaver); and the answering agent, which
reads the pages. The kit is `tests/test_contract_corpus.py`, one test per assert.

## What it looks like

One directory holds a wiki and everything it needs. It travels whole — as a tarball
under a resolver, as a folder on disk locally — under the names callers already use.

```
_inbox/                      pending sources (markdown, frontmatter optional)
_sources/<name>              retained raw source; appearing here = ingested
.wiki/failed/<name>          held source; reason in the ledger
.wiki/skipped/<name>         skipped source (e.g. empty); reason in the ledger
.wiki/source-versions/s<id>.md   prior text of an edited source; old citations resolve here
.wiki/.processed.jsonl       ledger, one JSON object per line
.wiki/runs/ingest-<ts>/result.json    outcome of one run (does not travel)
lens.md   lens/corrections/   feedback/log.jsonl
index.md  log.md  source-<name>.md  <topic>.md
```

## Purpose

Several systems read and write the same directory without talking to each other. If
a name moves or a signal changes meaning, an upstream app silently re-sends work, misfiles a
failure, or loses a correction in transit. This contract fixes the layout and what
each part means, so any caller can swap the engine underneath without noticing.

## Core (the teeth)

1. **All state a later run needs lives in the corpus directory.** Broken looks like a
   job that works on one machine and forgets its history in a fresh container.
2. **`_inbox/` takes markdown, and frontmatter is optional.** Date, title and kind fall
   back to header lines and the filename. Upstream apps write here; ingest drains it.
3. **`_sources/<name>`, under the original filename, means ingested.** It holds the most
   recently ingested version of that file, byte-for-byte as it arrived. When a source is
   edited and re-dropped, this copy is replaced and the prior text is kept at
   `.wiki/source-versions/s<id>.md`, so older citations still resolve.
4. **A held source moves to `.wiki/failed/<name>`, and the ledger says why.** A
   caller retries or reports it; the resolver sees it as `counts.failed` (clause 6),
   and the file itself travels.
5. **The ledger is `.wiki/.processed.jsonl`, keyed on content.** Each line is a JSON
   object carrying `source`, `hash`, `status`, `reason` and `converged` (true or
   false). A source is done only when a line for it has `converged` true; the next run
   reads that across a tarball round-trip. An edited file under an old name has a new
   hash and is read again.
6. **One ingest invocation writes one `.wiki/runs/ingest-<ts>/result.json`, aggregated
   over the whole run.** It carries `total`, `converged`, and `counts` with `failed`,
   `blocked` and `errored`; `total` excludes skipped sources. The resolver reads only
   the newest file, and for repository pipelines it ignores the exit code — this file
   is the only evidence of what happened.
7. **The lens and its learning travel with the wiki.** `lens.md` sits at the root;
   standing corrections live in `lens/corrections/`, each with its reason; raw
   feedback lives in `feedback/log.jsonl`, each entry with a why.
8. **Pages live at the root.** Each page has frontmatter `title`, `type`, `sources`
   and `last_updated`; per-source summaries are `source-*.md`; `index.md` has one line
   per page (title, date range, one-sentence summary); `log.md` records what was
   ingested, what changed, and what was skipped and why.
9. **Upstream-app policy at `.wiki/policy/schema.md` or `policy/schema.md` is read if
   present** and folded into the lens, so an upstream app's page types reach the writer.
10. **Pages, `_sources/`, the ledger, `.wiki/failed/`, `.wiki/source-versions/`, the lens,
    corrections and feedback round-trip; `.wiki/runs`, `.wiki/snapshots` and every `.git` do not.**
    The corpus may carry its own git for page history, but that history does not
    survive Resolve; `init` re-initializes git when `.git` is absent.

## What v1 deliberately does NOT freeze

- The inside of `lens.md` and of a correction file — promoted when anything other
  than WikiWeaver edits them (for example, proposed lens edits).
- The citation syntax inside page bodies — promoted when a reader outside WikiWeaver
  parses citations rather than following them by eye.
- Other `result.json` keys and ledger fields — promoted when a caller reads one.
- The name of the reading guide that travels with the wiki for the answering agent —
  promoted when an agent outside this repository loads it by path.
- Page quality, coverage and ingest time — never promoted here; judged by feedback.

## Conformance kit asserts

- After ingesting a one-file fixture: `_sources/<name>` is byte-identical to the
  input, and `_inbox/<name>` no longer exists. A fixture with no frontmatter passes too.
- Every ledger line parses as JSON with keys `source`, `hash`, `status`, `reason` and a
  boolean `converged`; the one-fixture ingest leaves a line with `converged` true.
- Re-ingesting an edited file under the same name adds a ledger line with a new `hash`.
- A fixture forced to hold lands in `.wiki/failed/<name>` with a non-empty `reason`.
- That file, moved back into `_inbox/`, is ingested on the next run (anything not
  converged is eligible).
- A clean one-fixture ingest yields `total == converged == 1` and `counts.failed ==
  counts.blocked == counts.errored == 0` in the newest `result.json`.
- `lens.md` and `index.md` exist at the root; every page other than `index.md` and
  `log.md` has the four frontmatter keys in clause 8.
- Tar with the clause-10 exclusions, untar elsewhere: `init` exits 0, `.git` exists
  again, and ingesting the unchanged inbox adds no ledger lines.

## Reserved / open questions (NOT frozen)

- Whether an empty (0-byte) source counts as skipped (left out of `total`) or failed.
  Proposed: empty is skipped, oversized is failed.
