"""wiki_weaver.synthesize.attribute_record -- THE record-half of the
iteration-6 detection/attribution split; the box/tool split for
``attribute_sources``' output.

``attribute_sources`` (LLM box, ``pipeline/synthesize.dot``) judges ONE
candidate's ``.ai/current-attribution-candidate.json`` seed against every
source thesis in ``.ai/source-arguments.md`` and writes its corrected
verdict to ``.ai/current-attribution-result.json`` -- the SAME "the model
cannot certify its own output's shape" reasoning ``validate_plan.py`` and
``wiki_weaver.synthesize.rank_candidates`` already apply elsewhere in this
pipeline applies here too: this module parses that JSON, drops any cited
source_id that is not a real file under ``sources/`` (hallucination guard,
reusing ``rank_candidates.existing_source_ids`` -- the SAME universe check
``rank_candidates`` itself uses, now a second independent consumer), and
merges the result into ``.ai/attribution-progress.json`` before looping
back to ``attribute_select`` for the next candidate.

GRACEFUL FALLBACK, NOT FAIL-LOUD, ON A MALFORMED SINGLE RESULT: unlike
``rank_candidates``' ``candidates_bad`` (which fails the WHOLE detection
pass loud, because a malformed top-level shape there means scan_arguments
produced nothing trustworthy at all), a malformed
``current-attribution-result.json`` here means only ONE candidate's
attribution attempt misfired mid-loop. Failing the entire pipeline over one
bad attribution call would be strictly worse than simply keeping that one
candidate's ORIGINAL seed source_ids unchanged and moving on -- the exact
"one stubborn candidate can never wedge the whole loop" discipline
``retry_bound``'s ``give_up`` path already applies to ``answer_gap``,
applied here to a lower-stakes, purely-additive step. This is never a
silent success: every fallback is logged to stderr.

Usage:
    python3 -m wiki_weaver.synthesize.attribute_record --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text
from wiki_weaver.synthesize.rank_candidates import existing_source_ids


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.attribute_record")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _read_json(path: Path) -> dict | None:
    """``None`` on any missing/invalid/non-object read -- callers treat that
    as \"nothing trustworthy was written,\" never crash on it."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def resolve_attribution(
    seed: dict,
    result: dict | None,
    *,
    valid_source_ids: set[str],
) -> tuple[dict, list[str]]:
    """Combine ``seed`` (``current-attribution-candidate.json``, always
    trustworthy -- it is CODE-written, not LLM-written) with ``result``
    (``current-attribution-result.json``, the LLM's judgment, possibly
    ``None`` or malformed) into the final ``{"term", "source_ids", "claim"}``
    record. Returns ``(record, warnings)``.

    - ``term`` ALWAYS comes from ``seed`` -- the canonical identity
      ``attribute_select``'s completed-terms tracking keys on; the model is
      never trusted to reproduce it exactly.
    - ``source_ids``: from ``result`` when it is a well-formed list of
      strings (after dropping any not in ``valid_source_ids`` --
      hallucination guard), else falls back to ``seed``'s original list
      unchanged (graceful fallback -- see module docstring).
    - ``claim``: ``result``'s, when a non-empty string; else ``seed``'s.
    """
    term = str(seed["term"]).strip()
    warnings: list[str] = []

    source_ids = list(seed.get("source_ids", []))
    claim = str(seed.get("claim", "")).strip()

    if result is None:
        warnings.append(
            f"attribution result missing/malformed for {term!r} -- falling back to scan_arguments' seed source_ids"
        )
    else:
        raw_ids = result.get("source_ids")
        if not isinstance(raw_ids, list) or not all(isinstance(s, str) and s.strip() for s in raw_ids):
            warnings.append(
                f"attribution result for {term!r} has a malformed source_ids field -- "
                "falling back to scan_arguments' seed source_ids"
            )
        else:
            kept = sorted({s for s in raw_ids if s in valid_source_ids})
            hallucinated = sorted({s for s in raw_ids if s not in valid_source_ids})
            if hallucinated:
                warnings.append(
                    f"attribution result for {term!r} cited non-existent source_id(s), dropped: {hallucinated}"
                )
            source_ids = kept

        result_claim = str(result.get("claim", "")).strip()
        if result_claim:
            claim = result_claim

    record = {"term": term, "source_ids": source_ids, "claim": claim}
    return record, warnings


def merge_into_progress(progress: dict, record: dict) -> dict:
    """Upsert ``record`` into ``progress["results"]`` by lowercased term --
    replaces an existing entry for the same term (idempotent re-run of this
    node for the same candidate is a no-op-or-refresh, same discipline as
    ``write_gap_page``'s own idempotency note), else appends."""
    key = record["term"].strip().lower()
    results = [r for r in progress.get("results", []) if str(r.get("term", "")).strip().lower() != key]
    results.append(record)
    return {"raw_signature": progress.get("raw_signature", ""), "results": results}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    seed = _read_json(wr.current_attribution_candidate_file)
    if seed is None or "term" not in seed:
        print(
            f"{wr.current_attribution_candidate_file} missing or malformed -- attribute_select must run first",
            file=sys.stderr,
        )
        return 1

    result = _read_json(wr.current_attribution_result_file)
    valid_source_ids = existing_source_ids(wr.sources_dir)

    record, warnings = resolve_attribution(seed, result, valid_source_ids=valid_source_ids)
    for warning in warnings:
        print(warning, file=sys.stderr)

    progress_path = wr.attribution_progress_file
    progress = _read_json(progress_path) or {"raw_signature": "", "results": []}
    progress = merge_into_progress(progress, record)
    atomic_write_text(progress_path, json.dumps(progress, indent=2) + "\n")

    print(
        f"attributed {record['term']!r}: {len(record['source_ids'])} source(s) "
        f"(seed had {len(seed.get('source_ids', []))})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
