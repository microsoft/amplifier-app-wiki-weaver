"""wiki_weaver.ingest.curate_brief -- the self-contained brief for
``curate_gate``, the ONE source-level "does this belong in this wiki"
checkpoint the source pattern names but this pipeline never built.

THE GAP THIS FIXES (see the task that commissioned this module): the gist's
pattern names four human jobs -- "curate sources, direct the analysis, ask
good questions, and think about what it all means." Three already have a
dedicated gate: ``takeaways_gate`` (direct the analysis, pre-write),
``review_gate``/``collect_guidance`` (ask good questions, via the
quarantine-rescue path), and ``ask.dot``'s answer loop (think about what it
all means). Curation had NONE -- every file dropped into ``sources/`` was
ingested unconditionally, the moment ``select_source`` picked it, with no
checkpoint for "does this even belong in this wiki at all."

THE FIX: a deterministic node (no model call) between ``drain_bound`` and
``detect_kind`` -- BEFORE any kind-detection/segmentation/retrieval work is
spent on a source that might get declined -- assembling everything an
answerer needs to judge fit, without having read the source or the wiki
itself:

  - a DETERMINISTIC gist of the candidate source's own content (reuses
    ``takeaways_brief.gist_from_content`` verbatim -- same gloss, not a
    judgment, the answerer is free to disagree with it entirely). Read from
    the RAW file under ``sources/`` -- ``detect_kind``/``segment_source``
    haven't run yet at this point in the pipeline, so there is no bounded
    segment content to prefer over it yet.
  - a compact, deterministic overview of what pages already exist in this
    wiki (reuses ``build_catalog.build_catalog_entries`` -- the same
    read-only bookkeeping ``build_catalog`` itself uses -- but rendered as a
    short "what this wiki already covers" gloss, not the full per-page
    inventory ``build_catalog`` writes for ``weave``; a curator judging fit
    needs the wiki's SCOPE, not a page-by-page merge-target prediction).

DEFAULT TOWARD INGEST (the task's explicit framing: "a wrongly-declined
source is permanently lost signal, and this project has already destroyed
good sources through an over-eager automatic path"): the brief itself states
the default plainly -- accept unless there is an affirmative, statable reason
this source does not belong. Declining is never the safe default; ingesting
too eagerly is corrigible later (a page can be re-edited or merged), but a
source that is declined at this gate is never offered again (see
``pipeline/ingest.dot``'s ``commit_decline`` -- a ``--decision declined``
ledger row, so ``select_source`` never re-selects it).

FAIL LOUD, NEVER FABRICATE: identical ``current_source_id`` contract as
``review_brief.py``/``takeaways_brief.py`` -- no current source means
nothing real to brief, and this tool refuses to invent one.

Usage:
    python3 -m wiki_weaver.ingest.curate_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.ingest.build_catalog import build_catalog_entries
from wiki_weaver.ingest.takeaways_brief import gist_from_content
from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, read_current_source_id

# Keep the overview compact -- same discipline as review_brief.MAX_PAGES_LISTED
# / takeaways_brief.MAX_PAGES_LISTED: this goes into a prompt, not a log file.
MAX_WIKI_PAGES_LISTED = 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.curate_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _source_content(source_path: Path) -> str:
    """The candidate source's raw content. detect_kind/segment_source have
    not run yet at this point in the pipeline (curate_gate sits BEFORE both
    -- see module docstring), so there is no bounded segment to prefer;
    this always reads the whole raw file, exactly like takeaways_brief's
    own fallback when no segment content is available yet."""
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")
    return ""


def wiki_about_text(wiki_dir: Path) -> str:
    """A compact, deterministic gloss of what this wiki already covers --
    page count plus up to ``MAX_WIKI_PAGES_LISTED`` titles/summaries.
    Distinct from build_catalog's own full per-page inventory (which
    ``weave`` needs for precise merge decisions): a curator judging whether
    a NEW source belongs here needs the wiki's SCOPE, not a page-by-page
    listing. Never fabricated -- an empty wiki says so plainly."""
    entries = build_catalog_entries(wiki_dir)
    if not entries:
        return "This wiki is currently empty -- no pages exist yet."

    shown = entries[:MAX_WIKI_PAGES_LISTED]
    lines = [f"This wiki has {len(entries)} page(s). It currently covers:"]
    for entry in shown:
        suffix = f": {entry['summary']}" if entry.get("summary") else ""
        lines.append(f"- {entry['title']}{suffix}")
    remaining = len(entries) - len(shown)
    if remaining > 0:
        lines.append(f"- ... and {remaining} more page(s)")
    return "\n".join(lines)


_DEFAULT_TOWARD_INGEST_NOTE = (
    "Default toward ACCEPTING this source into the wiki -- ingesting too eagerly is "
    "corrigible later (a page can always be re-edited or merged), but a declined source is "
    "NEVER offered again. Decline ONLY if you can state a clear, affirmative reason this "
    "source does not belong here (e.g. off-topic for this wiki's stated scope, spam or "
    "boilerplate, not real content) -- uncertainty alone is never a reason to decline."
)


def build_brief(wr: WikiRoot, source_id: str) -> str:
    """Assemble the compact, human/proxy-readable curation brief for
    ``source_id``. Every field is read live from disk state -- never
    invented."""
    source_path = wr.sources_dir / source_id
    content = _source_content(source_path)
    gist = gist_from_content(content)
    wiki_about = wiki_about_text(wr.wiki_dir)

    lines = [
        f"Candidate source: {source_id}",
        (
            "What it looks like it's about (deterministic gist of the opening substantive "
            "prose, not an LLM summary -- confirm or reject freely):"
        ),
        gist,
        "",
        "What this wiki is already about:",
        wiki_about,
        "",
        _DEFAULT_TOWARD_INGEST_NOTE,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        # FAIL LOUD: no identifiable current source means there is nothing
        # real to brief. Never emit an empty or invented brief -- see
        # module docstring / review_brief.py's identical contract.
        print(str(exc), file=sys.stderr)
        return 1

    brief = build_brief(wr, source_id)

    # Same two-channel delivery as review_brief.py/takeaways_brief.py: stdout
    # JSON only reaches $curate_brief / Question.metadata["description"] -- a
    # channel the proxy's own question-reduction never reads. The stamped
    # file lets ProxyInterviewer read it directly, verifying freshness
    # against current_source.txt before trusting it.
    ensure_dir(wr.ai_dir)
    atomic_write_text(
        wr.curate_brief_file,
        json.dumps({"source_id": source_id, "stage": "curate_gate", "brief": brief}, indent=2) + "\n",
    )

    print(json.dumps({"curate_brief": brief}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
