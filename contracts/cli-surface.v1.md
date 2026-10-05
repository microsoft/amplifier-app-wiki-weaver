# CLI Surface Contract — v1 (DRAFT)

**Who builds against this:** the weaver resolver, which runs `wiki-weaver` in Resolve
for both the Team Pulse wiki pipeline (it reads the exit code and cross-checks it) and
RepoWeaver's repo pipelines (it ignores the exit code); RepoWeaver, a separate app that
drives WikiWeaver from its own CLI and its own Resolve pipelines and imports the path
helpers on load; and Team Pulse's ask path, which reads `ask --json`. The kit is
`tests/test_contract_cli.py` in this repository, one test per assert.

## What it looks like

A caller sees one command and a handful of verbs, and never learns what runs underneath.

```
$ wiki-weaver --version
wiki-weaver 2026.10.02-1a2b3c4
$ wiki-weaver init ./corpus --plain
$ wiki-weaver ingest --wiki ./corpus --max-cycles 4 --limit 10
$ wiki-weaver ask "Who owns the release checklist?" --wiki ./corpus --json
{"answer": "…", "pages_used": ["release-process.md"], "refused": false}
```

## Purpose

Team Pulse and RepoWeaver were written against this surface. If a verb, flag, output
shape or exit code moves, they break in someone else's job, often silently. This
contract fixes what callers touch so the engine can change freely underneath.

## Core (the teeth)

1. **One command, `wiki-weaver`, with the verbs `doctor`, `init`, `ingest`, `ask`,
   `build-dashboard` and `update`, plus `--version`.** Renaming one breaks callers.
2. **These flags are accepted with these meanings:** `init <dir> --plain`;
   `ingest --wiki <dir>`, `--max-cycles <n>`, `--limit <n>`, and `--source <file>` to
   retry one source; `ask <question> --wiki <dir> --json`; `build-dashboard <corpus>
   --out <file> --group-by <field> --group-link-template <t> --theme <path>`.
   RepoWeaver passes every one of them.
3. **`ask --json` prints exactly one JSON object on stdout:** `answer` (text),
   `pages_used` (a list of page names) and `refused` (true or false). Anything else on
   stdout breaks the caller's parse.
4. **`--version` prints `wiki-weaver YYYY.MM.DD-<sha>` and exits 0.** The sha is 7 to
   40 hex characters. Callers record it to tie an output to the build that made it.
5. **`init` on an existing wiki leaves it intact.** The resolver runs `init` on every
   job; a wiki that `init` rewrites is a wiki lost every run.
6. **`init` with neither `--purpose` nor `--plain`, run non-interactively, behaves as
   `--plain`.** The resolver runs `init` in a container with nobody to interview.
7. **`wiki_weaver.lib` exports five path helpers:** `wiki_inbox`, `wiki_sources`,
   `wiki_failed`, `wiki_ledger`, `wiki_dashboard`, returning `_inbox`, `_sources`,
   `.wiki/failed`, `.wiki/.processed.jsonl` and `.wiki/dashboard` under the corpus.
   RepoWeaver imports them on load, so a missing one fails every RepoWeaver command,
   `doctor` included. Importing them needs no API key and no model runtime.
8. **One `ingest` invocation writes exactly one `.wiki/runs/ingest-<ts>/` directory
   holding one aggregate `result.json`, however many internal passes it takes.** The
   resolver reads only the newest file; a second directory per run hides the outcome.
9. **`--limit <n>` caps how many sources one invocation ingests; `--max-cycles <n>` is
   accepted, recorded in `result.json`, and changes nothing.** It was the old
   per-source retry budget. RepoWeaver passes about 4; reading it as a source cap would
   silently ingest four sources a run.
10. **`ingest` exit codes mean one thing each:** 0 — at least one source converged and
    none blocked or errored; 1 — infrastructure error, missing wiki directory, or failed
    preflight; 2 — usage error, including an invalid `--limit`; 3 — nothing to do
    (empty inbox or only already-ingested duplicates); 4 — gate-blocked; 5 — sources
    attempted, none converged; 75 — another ingest already holds this wiki, try later.
    *Evidence: `wiki_weaver/run_result.py:112-125` (0/1/3/4/5 and the verdict map);
    `wiki_weaver/lib.py:1095` (missing directory → 1); `wiki_weaver/cli.py:90`
    (preflight → 1); `wiki_weaver/cli.py:124` (invalid `--limit` → 2);
    `wiki_weaver/cli.py:133` with `wiki_weaver/schedule.py:38` (lock held → 75).*
11. **The command reads and writes only the corpus directory it is pointed at,** so a
    resolver can ship the wiki in and out as a tarball.

## What v1 deliberately does NOT freeze

- The `feedback` verb and its flags — promoted when an upstream app or Team Pulse calls it.
- Other verbs (`lint`, `migrate`, `schedule`, `supervise`) and unlisted flags —
  promoted when a caller outside this repository uses one.
- Human-readable stdout and stderr — never promoted; parse `--json` and files.
- Running the graph directly in Resolve — promoted, as its own contract, when Resolve hosts it.
- Answer quality and how long a verb takes — never promoted here; judged by feedback.

## Conformance kit asserts

- `--help` lists each clause-1 verb and exits 0; `<verb> --help` names its clause-2 flags.
- `--version` prints a line matching `^wiki-weaver \d{4}\.\d{2}\.\d{2}-[0-9a-f]{7,40}$`; exit 0.
- With no API key set, importing the five helpers exits 0, and each returns its
  clause-7 path for a sample directory.
- `ask --json` on a fixture prints one object with exactly `answer` (string),
  `pages_used` (list of strings) and `refused` (boolean).
- Every file in a populated fixture is byte-identical before and after `init` on it.
- `init <dir>` with no flags and stdin not a terminal exits 0 without prompting.
- One `ingest` adds exactly one `.wiki/runs/ingest-*/` directory holding `result.json`.
- `ingest --max-cycles 4 --limit 1` over two fixtures ingests exactly one source,
  and the newest `result.json` records the value 4.
- An unknown flag exits 2; `ingest` on an empty inbox exits 3; on a missing directory, 1.
- `ingest` exits 75 while another ingest holds the same wiki.

## Reserved / open questions (NOT frozen)

- An empty inbox returns 3; callers testing zero vs non-zero read that as failure.
  Intended? — for the platform owner.
- Whether `update` still runs inside Resolve jobs, or only on a person's machine.
