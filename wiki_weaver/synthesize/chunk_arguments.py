"""wiki_weaver.synthesize.chunk_arguments -- deterministic, fixed-size
chunking of the corpus's source theses; the fix for scan_arguments'
measured attention-dilution at full-corpus scale.

THE PROVEN FINDING (documented here, not in any prompt -- see
pipeline/synthesize.dot's header "DOES NOT" section: prompts stay fully
general, but this docstring, like the header's own iteration-history
sections, is allowed to name what was actually observed): a live
experiment ran scan_arguments' own detection prompt against ALL 46 of a
real corpus's source theses (~44 KB, the full .ai/source-arguments.md)
in one pass. Across six separate iterations of that pass, it proposed
only 2-3 candidate arguments every time. The IDENTICAL prompt, run
against a SINGLE CHUNK of 8 theses drawn from that same file, returned 11
distinct arguments in one pass -- including ``token-cost-is-the-
governing-constraint`` (5 sources), a theme that scored zero across every
one of the six full-corpus runs. The chunk run also surfaced arguments
spanning sources with no shared vocabulary at all -- visible, in the
runner's own words, "only after you stop reading for topic and start
reading for the structure of the recommendation."

Detection triages to the strongest few signals in whatever set it is
shown. At 46 theses, an argument dispersed across sources that share no
vocabulary loses to lexically-anchored ones (recurring phrases, named
tools). At 8, it surfaces. SET SIZE was the only variable that changed
between the full-corpus run and the chunk run -- same prompt, same
corpus, same model.

THE FIX: split the deterministic ``.ai/source-arguments.md`` extract
(``wiki_weaver.synthesize.extract_source_arguments``' output; this module
reuses its ``build_source_argument_entries`` builder rather than
re-parsing that file's markdown) into fixed-size groups, and run
scan_arguments once PER CHUNK -- see ``wiki_weaver.synthesize.
select_chunk`` for the loop this feeds and ``wiki_weaver.synthesize.
record_chunk_candidates`` for how each chunk's output is unioned back
together. The SAME argument proposed by several different chunks (under
different wording, since each chunk sees a different subset of the
corpus) is expected and is itself evidence the argument is genuinely
dispersed, not noise -- see record_chunk_candidates' module docstring for
the merge discipline.

CHUNK SIZE IS EMPIRICAL, NOT A GENERAL PRINCIPLE: ``DEFAULT_CHUNK_SIZE =
8`` is the EXACT size the live experiment above used and measured
working -- it is not derived from any general claim about LLM attention
span or context window limits, and a different corpus's optimal chunk
size has not been measured. Exposed as ``--chunk-size`` (a ``--param`` in
pipeline/synthesize.dot) so a denser or sparser corpus can retune it
without editing this module.

Usage:
    python3 -m wiki_weaver.synthesize.chunk_arguments --wiki-root <path> [--chunk-size 8]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, int_or_default
from wiki_weaver.synthesize.extract_source_arguments import build_source_argument_entries, format_argument_section

# Empirically derived, not arbitrary -- see module docstring's "THE PROVEN
# FINDING" section: this is the exact chunk size the live experiment
# measured surfacing 11 distinct arguments (vs 2-3 at full-corpus scale)
# from the same source theses, same prompt, same model. Retune via
# --chunk-size for a corpus this has not been measured against.
DEFAULT_CHUNK_SIZE = 8


def chunk_entries(entries: list[tuple[str, str, str]], chunk_size: int) -> list[list[tuple[str, str, str]]]:
    """Split ``entries`` (``build_source_argument_entries``' output, already
    sorted by filename for deterministic output) into consecutive groups of
    at most ``chunk_size`` -- no shuffling, no re-ranking: chunk membership
    is purely positional, so the same corpus always produces the same
    chunks. A ``chunk_size`` below 1 is treated as 1 (never divide by zero
    or produce an infinite/empty chunk)."""
    size = max(1, chunk_size)
    return [entries[i : i + size] for i in range(0, len(entries), size)]


def format_chunk_doc(chunk: list[tuple[str, str, str]], chunk_index: int, total_chunks: int) -> str:
    """Render one chunk's entries as a self-contained markdown document --
    same ``## <title>\\n(source_id: ...)`` section format as the full
    ``source_arguments_file`` (via ``format_argument_section``, shared with
    ``extract_source_arguments``), so a chunk reads exactly like a subset
    of that file, never a different shape scan_arguments has to learn."""
    sections = [format_argument_section(title, source_id, extract) for title, source_id, extract in chunk]
    header = (
        f"# Source Arguments -- chunk {chunk_index + 1} of {total_chunks}\n\n"
        "A FIXED-SIZE SUBSET of the corpus's source theses (see "
        "wiki_weaver.synthesize.chunk_arguments' module docstring for why "
        "detection runs per-chunk rather than over the full extract) -- one "
        "argument-bearing excerpt per source-level wiki page in THIS CHUNK "
        "ONLY, each labeled with the source_id it came from. This is one "
        "bounded slice of the corpus, not the whole thing: the same "
        "argument may recur in another chunk under different words -- that "
        "is expected, and a downstream union step combines candidates "
        "across every chunk before attribution runs against the full "
        "corpus.\n\n"
    )
    if sections:
        return header + "\n".join(sections)
    return header + "(this chunk is empty)\n"


def build_chunk_docs(wiki_dir: Path, chunk_size: int) -> tuple[list[str], int]:
    """Return ``(chunk_docs, total_entries)`` -- ``chunk_docs`` is the list
    of rendered chunk documents ``wiki_weaver.synthesize.select_chunk``
    writes to ``current_chunk_file`` one at a time; ``total_entries`` is
    how many source theses were split (for the stderr summary line)."""
    entries = build_source_argument_entries(wiki_dir)
    chunks = chunk_entries(entries, chunk_size)
    docs = [format_chunk_doc(chunk, i, len(chunks)) for i, chunk in enumerate(chunks)]
    return docs, len(entries)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.chunk_arguments")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--chunk-size",
        required=False,
        default=str(DEFAULT_CHUNK_SIZE),
        type=int_or_default(DEFAULT_CHUNK_SIZE),
        help=f"source theses per detection chunk (default {DEFAULT_CHUNK_SIZE}, empirically derived -- see module docstring)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    chunk_docs, total_entries = build_chunk_docs(wr.wiki_dir, args.chunk_size)

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.argument_chunks_file, json.dumps(chunk_docs, indent=2) + "\n")
    print(
        f"{total_entries} source thesis entries split into {len(chunk_docs)} chunk(s) "
        f"of up to {max(1, args.chunk_size)} each -- {wr.argument_chunks_file}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
