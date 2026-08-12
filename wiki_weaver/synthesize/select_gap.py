"""wiki_weaver.synthesize.select_gap -- THE resume gate for the synthesis loop.

pending = ``rank_candidates``' already-ranked, threshold-filtered candidate
list (``.ai/gap-candidates.json``, written ONCE per invocation by
``scan_arguments`` + ``rank_candidates`` upstream of this node -- see
``pipeline/synthesize.dot``) MINUS terms already DECIDED in
``synth-ledger.jsonl`` (paged or declined -- either way, never propose it
again), recomputed fresh on every pass -- same resume doctrine as
``ingest.select_source`` (REWRITE-GUIDE.md \u00a72.4): kill this pipeline
mid-run, restart from ``start``, this recomputes and resumes with zero
re-work and zero duplicate pages.

THIS is the "predicate about the wiki" FINDINGS.md \u00a77 asks for: ``no_gap``
means no unnamed concept remains above threshold -- a statement about the
ARTIFACT (the wiki against the corpus), not about an input queue being
empty (contrast ``ingest.select_source``'s queue-drain predicate).

PROVENANCE NOTE (this node's mechanism was rewritten; its CONTRACT was not):
candidates used to come from a live n-gram/document-frequency count over
``sources/`` -- pure counting, deterministic, no LLM. That mechanism could
only find phrases that recur as literal TEXT, and structurally could not
recover an argument the corpus makes in different words in different
places (measured against the real restore-arm corpus: it ranked chrome like
"final thoughts"/"open source" while the three actual missing themes never
surfaced). Candidates now come from ONE bounded LLM pass over
``wiki/index.md`` + ``wiki/overview.md`` (``scan_arguments``), validated and
threshold-filtered by ``wiki_weaver.synthesize.rank_candidates``, and
cached to disk for this invocation. This node's OWN contract -- recompute
pending fresh from the candidate list minus the ledger, every pass -- is
unchanged; only where the candidate list comes from moved upstream.

Usage:
    python3 -m wiki_weaver.synthesize.select_gap --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import LedgerCorruptError, WikiRoot, atomic_write_text, ensure_dir, read_ledger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.select_gap")
    parser.add_argument("--wiki-root", required=True)
    return parser


def decided_terms(ledger_path: Path) -> set[str]:
    """Every term this loop has already reached a decision on (paged or
    declined -- both are final, see ``wiki_weaver.synthesize.commit``).
    Reuses ``lib.read_ledger`` -- the SAME durable-JSONL reader
    ``ingest.select_source`` uses, so a corrupt synth ledger fails exactly
    as loud (``LedgerCorruptError``), never silently treated as "nothing
    decided yet."""
    return {rec["term"] for rec in read_ledger(ledger_path) if "term" in rec}


def read_candidates(path: Path) -> list[dict]:
    """``rank_candidates``' normalized, already-validated output --
    ``.ai/gap-candidates.json``. This node is only ever reached (per
    ``pipeline/synthesize.dot``'s edges) after ``rank_gap_candidates``
    routed ``candidates_ok``, so an absent or malformed file here is
    unexpected -- but treating that as "no candidates" is still the honest,
    fail-safe reading rather than crashing the resume gate on a state it
    cannot itself repair.

    Public (promoted from a module-private helper): ``wiki_weaver.synthesize.
    synthesis_brief`` (the ``synthesis_gate`` pre-write brief, sitting
    between ``rank_gap_candidates`` and this node -- see
    ``pipeline/synthesize.dot``) is now a second, independent consumer of
    this exact read -- this project's own convention promotes a helper once
    2+ independent consumers exist (see ``rank_candidates.
    existing_source_ids``'s docstring for the same reasoning applied
    earlier in this package)."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        already_decided = decided_terms(wr.synth_ledger_path)
    except LedgerCorruptError as exc:
        print(f"synth ledger corrupt, failing loud rather than treating as empty: {exc}", file=sys.stderr)
        print("ledger_corrupt")
        return 0

    candidates = read_candidates(wr.gap_candidates_file)
    remaining = [c for c in candidates if c.get("term") not in already_decided]

    if not remaining:
        if candidates:
            print(
                f"no gap candidates remain above threshold ({len(candidates)} candidate(s) found, all already decided)",
                file=sys.stderr,
            )
        else:
            print(
                "the corpus-wide argument scan found no unnamed argument -- "
                "a legitimate, expected outcome on a well-covered wiki",
                file=sys.stderr,
            )
        print("no_gap")
        return 0

    chosen = remaining[0]
    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.current_gap_file, json.dumps(chosen))
    print(
        f"selected gap: {chosen.get('term')!r} "
        f"({chosen.get('source_count', 0)} source(s), {len(remaining)} candidate(s) remaining)",
        file=sys.stderr,
    )
    print("has_gap")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
