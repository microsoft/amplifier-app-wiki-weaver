"""wiki_weaver.synthesize.attribute_select -- THE select-half of the
iteration-6 detection/attribution split.

WHY THIS NODE EXISTS (see ``pipeline/synthesize.dot``'s header, "ITERATION 6"
section, for the full account): ``scan_arguments`` can correctly DETECT that
an argument recurs across the corpus while badly under-counting WHICH
sources make it -- ``cross-session-statelessness-cost`` was detected with 3
cited sources when a human reader found 9, because the sources making that
argument use entirely different vocabulary for it. Detection ("what
arguments recur") and attribution ("which sources make THIS argument") are
different judgment calls; ``scan_arguments`` was doing both in one pass and
degrading on the second. This module is the CODE-tier "select the next item"
half of a select/box/record loop (same shape as ``select_gap`` +
``answer_gap`` + ``commit_paged``/``commit_declined``, or ``detect_kind`` +
``classify_kind``): for each unique candidate ``scan_arguments`` proposed,
pick the next one that has not yet been attributed this invocation, so
``wiki_weaver.synthesize.attribute_sources`` (the LLM box) can judge it in
isolation against every source thesis -- one bounded model call per
candidate, not 46 calls per candidate and not one call across every
candidate at once (a single sprawling judgment call across N candidates at
once is exactly the failure mode this split exists to fix: narrow the
model's attention to ONE argument per call).

RESUMABLE WITHIN ONE INVOCATION, RESET ACROSS INVOCATIONS: progress is kept
in ``.ai/attribution-progress.json`` (``WikiRoot.attribution_progress_file``),
stamped with a content signature (sha256) of ``gap-candidates-raw.json`` at
the time it was written. Every call to this node recomputes the signature
of the CURRENT raw file and compares: same signature -> the same
``scan_arguments`` run is still in progress, keep whatever candidates were
already attributed and pick up where it left off (a crash mid-loop costs
zero re-work, same discipline as every other resumable node in this
project). Different signature (or no progress file yet) -> a NEW detection
pass produced this raw file (this pipeline restarted from ``start`` and
``scan_arguments`` ran again) -- start the accumulator fresh rather than
silently mixing candidates from two different detection passes.

COST BOUND: candidates are deduplicated by lowercased term (first
occurrence wins -- the same tie-break ``rank_candidates.rank_and_filter``
uses) and any term that already has a matching wiki page title is dropped
BEFORE attribution ever runs (``rank_candidates.named_concept_titles`` +
``is_already_named`` -- belt-and-suspenders re-check of what
``scan_arguments`` was already told to do, applied here so an
already-covered term never costs an attribution call at all). The
deduplicated, not-already-named list is then capped at
``MAX_ATTRIBUTION_CANDIDATES`` -- a defensive ceiling against a pathological
detection pass proposing far more candidates than this corpus could
plausibly support; candidates beyond the cap are reported (stderr) and
simply never attributed or ranked this invocation, never silently treated
as "no gap."

Usage:
    python3 -m wiki_weaver.synthesize.attribute_select --wiki-root <path> [--max-candidates 25]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, int_or_default
from wiki_weaver.synthesize.rank_candidates import find_candidate_issues, is_already_named, named_concept_titles

# Defensive ceiling on how many unique candidates this loop will ever
# attribute in one invocation -- scan_arguments is narrowly scoped and
# empirically proposes a handful (2-5) per run; this guards against a
# pathological detection pass proposing far more without letting the
# attribution loop (and its per-candidate model calls) run unbounded.
DEFAULT_MAX_ATTRIBUTION_CANDIDATES = 25


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.attribute_select")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--max-candidates",
        required=False,
        default=str(DEFAULT_MAX_ATTRIBUTION_CANDIDATES),
        type=int_or_default(DEFAULT_MAX_ATTRIBUTION_CANDIDATES),
        help=f"ceiling on unique candidates attributed per invocation (default {DEFAULT_MAX_ATTRIBUTION_CANDIDATES})",
    )
    return parser


def _signature(raw_bytes: bytes) -> str:
    """Deterministic content fingerprint of ``gap-candidates-raw.json`` --
    what makes progress resumable within one ``scan_arguments`` run and
    discarded across a fresh one (see module docstring)."""
    return hashlib.sha256(raw_bytes).hexdigest()


def dedupe_and_filter(raw: list[dict], *, named_titles: list[str]) -> list[dict]:
    """First-occurrence-wins dedup by lowercased term (same tie-break
    ``rank_candidates.rank_and_filter`` uses downstream), dropping any term
    that already matches an existing wiki page title -- the cost
    optimization described in the module docstring. Preserves original
    ``raw`` entry order; assumes ``raw`` already passed
    ``find_candidate_issues``."""
    seen: set[str] = set()
    out: list[dict] = []
    for entry in raw:
        term = str(entry["term"]).strip()
        key = term.lower()
        if key in seen:
            continue
        if is_already_named(key, named_titles):
            continue
        seen.add(key)
        out.append(entry)
    return out


def _load_progress(path: Path, signature: str) -> dict:
    """Read ``.ai/attribution-progress.json``; return a fresh
    ``{"raw_signature": signature, "results": []}`` structure whenever the
    file is absent, unparsable, or stamped with a DIFFERENT signature (a
    new detection pass ran -- see module docstring)."""
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and data.get("raw_signature") == signature and isinstance(data.get("results"), list):
            return data
    return {"raw_signature": signature, "results": []}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    raw_path = wr.gap_candidates_raw_file
    if not raw_path.is_file():
        print(f"{raw_path} does not exist -- scan_arguments must run first", file=sys.stderr)
        print("candidates_bad")
        return 0

    raw_bytes = raw_path.read_bytes()
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{raw_path} is not valid JSON: {exc}", file=sys.stderr)
        print("candidates_bad")
        return 0

    issues = find_candidate_issues(raw)
    if issues:
        for issue in issues:
            print(f"gap-candidates-raw.json: {issue}", file=sys.stderr)
        print("candidates_bad")
        return 0

    ensure_dir(wr.ai_dir)

    signature = _signature(raw_bytes)
    progress = _load_progress(wr.attribution_progress_file, signature)

    named_titles = named_concept_titles(wr.wiki_dir)
    eligible = dedupe_and_filter(raw, named_titles=named_titles)
    if len(eligible) > args.max_candidates:
        dropped = [e["term"] for e in eligible[args.max_candidates :]]
        print(
            f"{len(eligible)} unique candidate(s) proposed, exceeds --max-candidates "
            f"{args.max_candidates} -- dropping (never attributed/ranked this invocation): {dropped}",
            file=sys.stderr,
        )
        eligible = eligible[: args.max_candidates]

    completed_terms = {str(r["term"]).strip().lower() for r in progress["results"] if "term" in r}
    remaining = [c for c in eligible if str(c["term"]).strip().lower() not in completed_terms]

    # Persist (possibly freshly-reset) progress immediately, even before a
    # candidate is chosen -- makes the reset itself durable, not just
    # in-memory, so a crash right after this node still resumes correctly.
    atomic_write_text(wr.attribution_progress_file, json.dumps(progress, indent=2) + "\n")

    if not remaining:
        # All eligible candidates for this signature have been attributed --
        # flatten progress["results"] into the final artifact rank_candidates
        # reads. Order does not matter here; rank_and_filter re-sorts by
        # source_count.
        atomic_write_text(
            wr.gap_candidates_attributed_file,
            json.dumps(progress["results"], indent=2) + "\n",
        )
        print(
            f"{len(progress['results'])} candidate(s) attributed this invocation -- "
            f"writing {wr.gap_candidates_attributed_file}",
            file=sys.stderr,
        )
        print("all_attributed")
        return 0

    chosen = remaining[0]
    candidate = {
        "term": str(chosen["term"]).strip(),
        "claim": str(chosen.get("claim", "")).strip(),
        "source_ids": list(chosen["source_ids"]),
    }
    # ensure_ascii=False: "term"/"claim" are real prose (an LLM-authored
    # argument, possibly containing em-dashes, arrows, or curly quotes) and
    # this file is read RAW by attribute_sources (pipeline/synthesize.dot's
    # LLM box) with its own file tools, never through json.loads --
    # ensure_ascii=True would leak a literal "\uXXXX" escape sequence into
    # what attribute_sources reads as plain text.
    atomic_write_text(wr.current_attribution_candidate_file, json.dumps(candidate, indent=2, ensure_ascii=False) + "\n")
    print(
        f"selected candidate for attribution: {candidate['term']!r} "
        f"({len(candidate['source_ids'])} seed source(s), "
        f"{len(remaining) - 1} remaining after this one)",
        file=sys.stderr,
    )
    print("has_candidate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
