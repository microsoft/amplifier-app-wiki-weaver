"""wiki_weaver.synthesize.synthesis_brief -- the self-contained brief for
``synthesis_gate``, the ONE pass-level checkpoint where a human or agent
proxy can shape WHICH ranked candidate cross-source themes actually get a
page, before ``select_gap`` ever picks one to write.

THE GAP THIS FIXES (see the task that commissioned this module): the source
gist names four human jobs -- "curate sources, direct the analysis, ask good
questions, and think about what it all means." Three already have a home:
``curate_gate`` (curate sources), ``takeaways_gate``/lens-canon direction
(direct the analysis), and ``ask.dot``'s answer loop (ask good questions).
"Think about what it all means" had NONE -- ``synthesize.dot`` ran entirely
unattended, end to end, with zero human touchpoints. That gap matters most
here: synthesis is where the wiki claims its whole value (finding arguments
that span sources, which no single author states), and where measured
failures have been worst (four generated pages that were one argument under
four labels; a page citing a source zero times in its own body).

WHY THIS SITE, NOT A POST-WRITE REVIEW: this project's own measurement
(``review_gate``, ``pipeline/ingest.dot``'s header) found a POST-write review
gate moved nothing (-0.5pp, inside noise) and retired it from the default
path; PRE-write steering (``takeaways_gate``, the lens/canon direction)
measured -6.4pp and is proven effective. ``rank_gap_candidates`` (see
``pipeline/synthesize.dot``) already ranks, threshold-filters, and
near-duplicate-merges the candidate list -- but ``select_gap`` picks from it,
and ``answer_gap``/``write_gap_page`` commit a page, entirely unattended.
This node sits directly between the two: AFTER the candidate list is final
for this pass, BEFORE ``select_gap`` ever selects one to write -- pre-write
leverage over which arguments get synthesized at all, not post-write review
of prose already committed.

THE BRIEF, same self-contained-brief shape as ``takeaways_brief``/
``curate_brief`` (deterministic, no model call, assembled from disk state
already computed by an earlier node in this SAME pass -- never invented):
every pending candidate's term, claim, source count, fraction of the corpus,
and a compact sample of its supporting source ids -- everything an answerer
needs to judge fit WITHOUT re-reading the corpus or re-running attribution.

FRESHNESS CONTRACT (this gate has no ``source_id``-style identity, unlike
``takeaways_gate``/``curate_gate``): stamps a ``signature`` computed
deterministically from the pending candidate set (term + source_count pairs)
instead. ``wiki_weaver.synthesize.persist_synthesis_guidance`` verifies the
freeform answer it reads was given for THIS pass's candidate list, not a
stale prior one.

ROUTES ``has_pending`` | ``no_pending`` (AP-2, DESIGN.md Sec 12.1: a
deterministic, code-tier routing decision, never an LLM judgment) -- an empty
pending list (every candidate this pass ranked has already been decided by a
prior pass) skips the gate entirely rather than showing an answerer nothing
to discuss.

Usage:
    python3 -m wiki_weaver.synthesize.synthesis_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from wiki_weaver.lib import LedgerCorruptError, WikiRoot, atomic_write_text, ensure_dir
from wiki_weaver.synthesize.rank_candidates import existing_source_ids
from wiki_weaver.synthesize.select_gap import decided_terms, read_candidates

# Same "compact, not a log file" discipline as takeaways_brief.MAX_PAGES_LISTED /
# curate_brief.MAX_WIKI_PAGES_LISTED: this text goes into a prompt.
MAX_SOURCES_LISTED = 8

# Candidates this pass could plausibly rank are already capped at
# rank_candidates.MAX_CANDIDATES (200) -- an extreme upper bound that should
# never be hit in practice. This is a SEPARATE, much tighter display cap so a
# pathological run never produces an unusably large prompt; candidates
# beyond this cap are named as present but "not shown -- cannot be dropped
# this pass" rather than silently omitted with no trace.
MAX_CANDIDATES_SHOWN = 25


def pending_candidates(wr: WikiRoot) -> list[dict]:
    """``rank_gap_candidates``' final candidate list minus every term already
    DECIDED in ``synth-ledger.jsonl`` -- the identical computation
    ``select_gap`` performs immediately downstream of this node, so what the
    answerer is shown here is exactly what ``select_gap`` would otherwise
    pick from. Raises ``LedgerCorruptError`` on a malformed ledger (never
    silently treated as empty) -- the caller routes this to ``no_pending``
    and lets ``select_gap`` fail loud with its own ``ledger_corrupt``
    sentinel immediately after, the authoritative place this project already
    reports that failure.
    """
    already_decided = decided_terms(wr.synth_ledger_path)
    candidates = read_candidates(wr.gap_candidates_file)
    return [c for c in candidates if c.get("term") not in already_decided]


def compute_signature(pending: list[dict]) -> str:
    """Deterministic freshness stamp over the pending candidate set (term +
    source_count pairs, sorted) -- this gate's analogue of the ``source_id``
    freshness key every other stamped brief in this codebase uses, adapted
    for a gate whose identity is a CANDIDATE SET rather than a single
    source. Stable across re-runs of this exact function on the exact same
    input; changes whenever the pending set changes (a candidate added,
    removed, or re-attributed to a different source_count)."""
    key = sorted((str(c.get("term", "")), int(c.get("source_count") or 0)) for c in pending)
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def _format_sources(source_ids: list[str]) -> str:
    shown = source_ids[:MAX_SOURCES_LISTED]
    text = ", ".join(shown)
    remaining = len(source_ids) - len(shown)
    if remaining > 0:
        text += f", +{remaining} more"
    return text


def build_brief(pending: list[dict], total_sources: int) -> str:
    """Assemble the compact, human/proxy-readable pre-write brief listing
    every pending candidate theme this pass is about to consider writing a
    page for. Every field is read live from disk state already computed by
    ``rank_gap_candidates`` earlier in this SAME pass -- never invented."""
    intro = (
        f"Corpus-wide synthesis pass: {len(pending)} candidate theme(s) survived detection, "
        "attribution, and threshold filtering. Review before any page is written for any of them."
    )
    lines = [intro, ""]
    shown = pending[:MAX_CANDIDATES_SHOWN]
    for i, c in enumerate(shown, start=1):
        term = str(c.get("term", "")).strip()
        source_ids = [str(s) for s in c.get("source_ids", []) if isinstance(s, str)]
        count = int(c.get("source_count") or len(source_ids))
        fraction = f"{count / total_sources:.0%} of corpus" if total_sources else "corpus fraction unknown"
        claim = str(c.get("claim", "")).strip() or "(no claim summary provided)"
        lines.append(f'{i}. "{term}" -- {count} source(s) ({fraction})')
        lines.append(f"   Claim: {claim}")
        lines.append(f"   Sources: {_format_sources(source_ids)}")
        lines.append("")
    remaining = len(pending) - len(shown)
    if remaining > 0:
        lines.append(f"... and {remaining} more candidate(s) not shown here (cannot be dropped this pass).")
        lines.append("")
    lines.append(
        "To drop a candidate: respond with one line per candidate, in the exact form "
        '"DROP: <term>" using the EXACT term text printed above. If none should be dropped, '
        'respond with exactly "KEEP ALL". Dropping is DURABLE: a dropped candidate is ledgered '
        "as declined and never re-proposed in any future pass -- decline only with a real, "
        "statable reason (too thin a claim, boilerplate-sounding, clearly redundant with an "
        "existing page), never mere uncertainty."
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.synthesis_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _skip(reason: str) -> int:
    print(reason, file=sys.stderr)
    print(json.dumps({"synthesis_brief": "", "pending_status": "no_pending"}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    if not wr.gap_candidates_file.is_file():
        return _skip(f"{wr.gap_candidates_file} does not exist -- rank_gap_candidates must run first")

    try:
        pending = pending_candidates(wr)
    except LedgerCorruptError as exc:
        return _skip(
            f"synth ledger corrupt ({exc}) -- skipping synthesis_gate; "
            "select_gap will fail loud with its own ledger_corrupt sentinel next"
        )

    if not pending:
        return _skip("no pending candidates remain above threshold -- skipping synthesis_gate")

    total_sources = len(existing_source_ids(wr.sources_dir))
    brief = build_brief(pending, total_sources)
    signature = compute_signature(pending)

    ensure_dir(wr.ai_dir)
    atomic_write_text(
        wr.synthesis_brief_file,
        json.dumps(
            {
                "signature": signature,
                "stage": "synthesis_gate",
                "brief": brief,
                "pending_terms": [str(c.get("term", "")) for c in pending],
            },
            indent=2,
        )
        + "\n",
    )
    print(f"{len(pending)} pending candidate(s) briefed for synthesis_gate (signature {signature})", file=sys.stderr)
    print(json.dumps({"synthesis_brief": brief, "pending_status": "has_pending"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
