# How to read this wiki

This wiki is pre-computed synthesis over raw sources. It has three layers:

1. `index.md` - one line per page: title, date range, one sentence. Start here; pick the
   few pages that look relevant and read those in full.
2. Pages (`*.md`) - topic pages and per-source summaries (`source-*.md`). Specific claims
   cite a source as `[s<id>: "five or more words quoted from it"]`.
3. `_sources/` - the raw sources. `s<id>` is `source_id` in `.wiki/.processed.jsonl`; its
   `source` field is the file name. Search that file for the quoted words to land on the
   spot (an earlier version of an edited source is `.wiki/source-versions/s<id>.md`).

Summaries are pointers, not testimony.

- For orientation, synthesis, patterns and "what is going on with X", the pages are enough.
- For a specific date, number, name or commitment, follow the citation to the source and
  read the surrounding passage before you state it.

Text between `<!-- superseded: YYYY-MM-DD -->` and `<!-- /superseded -->` was true
earlier and has since changed; what replaced it is written outside the block. Do not report
superseded text as current. (Older pages may carry the opening marker alone; it then covers
the rest of that line.)

If no page covers the question, say so rather than guessing.
