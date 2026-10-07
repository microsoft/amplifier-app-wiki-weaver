# How to read this wiki

1. `index.md` - one line per page: title, date range, one sentence; a source page's line
   also carries its `s<id>`. Start here and pick the few pages that fit the question.
2. A page opens with `## Current state (as of YYYY-MM-DD)`: a roll-up of where things stand,
   dated by the latest source on the page. For orientation it is often all you need.
3. Below it are the sections the lens names for that page type (`lens.md`, Page types).
   Entries inside a section are dated, newest first.
4. `source-*.md` pages carry the per-meeting view: what one source said, in its order.

Citations read `[s<id>: "five or more words quoted from the source"]`. `s<id>` is
`source_id` in `.wiki/.processed.jsonl` (its `source` field is the file in `_sources/`; an
earlier version of an edited source is `.wiki/source-versions/s<id>.md`). Search the
source for the quoted words to land on the passage.

- For orientation and synthesis, the pages are enough.
- For a specific date, number, name or commitment, follow the citation and read the
  surrounding passage before you state it.

Text between `<!-- superseded: YYYY-MM-DD -->` and `<!-- /superseded -->` was true earlier
and has since changed; what replaced it sits outside the block. Do not report it as current.
(An older page may carry the opening marker alone; it covers the rest of that line.)

If no page covers the question, say so rather than guessing.
Never edit the corpus during an ingest: such an edit looks like writer output and is reverted.
