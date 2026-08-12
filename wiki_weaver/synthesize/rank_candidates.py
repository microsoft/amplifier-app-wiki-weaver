"""wiki_weaver.synthesize.rank_candidates -- deterministic gate + threshold
discipline for the ATTRIBUTED candidate list.

``scan_arguments`` (LLM box, ``pipeline/synthesize.dot``) reads
``wiki/index.md`` and ``.ai/source-arguments.md`` and proposes ARGUMENTS the
corpus repeatedly contends but has never given its own page, each with a
SEED ``source_ids`` list -- a semantic judgment call, not a counting
problem. The mechanism this module replaces was a literal n-gram/document-
frequency counter: pure counting, no LLM, and it worked exactly as well as
counting can -- which is to say it could only find phrases that recur as
TEXT, and structurally could NOT recover a theme the corpus argues in
different words in different places (measured, concretely, against the
real restore-arm corpus: it ranked "final thoughts"/"open source"/"days
ago" while token economics, local inference, and human judgment -- the
three confirmed missing themes -- scored zero or near-zero, because none of
them recur as a stable literal phrase). See git history for the retired
module (``find_gaps.py``) if the full accounting is ever needed again.

ITERATION 6 (this module's input changed, its own logic did not): this
module used to read ``scan_arguments``' raw output
(``gap_candidates_raw_file``) directly. An independent evaluation of
iteration 5 found that ``scan_arguments`` can correctly DETECT an argument
(name it, one sentence, right idea) while badly under-ATTRIBUTING it --
``cross-session-statelessness-cost`` was detected with 3 cited sources
while a human reader found 9, because the sources making that argument use
entirely different vocabulary for it and a single narrowly-scoped detection
pass cannot also hold every source's own thesis in mind while judging
citations for every candidate at once. ``wiki_weaver.synthesize.
attribute_select``/``attribute_sources``/``attribute_record`` (see
``pipeline/synthesize.dot``'s header, "ITERATION 6" section) now sit
between ``scan_arguments`` and this module: for each unique candidate,
EVERY source thesis in ``.ai/source-arguments.md`` is individually judged
against that ONE argument (agree/extend/complicate/dispute, not vocabulary
matching), producing a corrected ``source_ids`` list that can be LARGER or
SMALLER than the seed. This module now reads that ATTRIBUTED list
(``gap_candidates_attributed_file``) instead -- its own validation,
filtering, and thresholding logic is unchanged; only which file holds the
counts that get thresholded moved.

This module is the code-side half of the box/tool split
(``validate_plan.py``'s exact reasoning -- "the planner cannot certify its
own plan's syntax" -- applies equally to "the model cannot certify its own
candidate list's shape"): the attribution loop's JSON is parsed, malformed
shapes are rejected (``candidates_bad``, never silently "no gap"),
hallucinated source citations (ids that do not exist under ``sources/``)
are dropped, and the SAME two empirically-measured thresholds this package
always applied -- ``min_sources`` (the source-count floor a candidate must
clear) and ``max_source_fraction`` (the ceiling above which a "theme" is
actually boilerplate/background, never a recurring subset -- a theme is a
recurring SUBSET of the corpus, not its near-totality) -- are still
enforced by CODE, never by asking the model to self-police its own
ranking.

CRITICAL ASYMMETRY vs ``validate_plan.py``: an EMPTY candidate list is a
legitimate, expected outcome (a well-covered wiki genuinely has no unnamed
argument left) and routes ``candidates_ok`` with zero candidates -- NOT
``candidates_bad``. Only a malformed / non-list top-level shape is
``candidates_bad``. Conflating "found nothing" with "failed to produce
output" would punish the exact refuse-rather-than-guess behavior this
pipeline depends on.

Usage:
    python3 -m wiki_weaver.synthesize.rank_candidates --wiki-root <path> [--min-sources 4] [--max-source-fraction 0.6]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, int_or_default, list_wiki_pages, parse_frontmatter

# Empirical floor, unchanged from the retired n-gram mechanism (see module
# docstring): 4 sources is the measured floor at which a theme, once named
# by a human, earned its own page in this project's own test wikis; all
# three confirmed-missing themes (token economics, local inference, human
# judgment) clear it by 2x-3x. A --param, never hardcoded past this one
# place.
DEFAULT_MIN_SOURCES = 4

# Same "a theme is a recurring SUBSET, never its near-totality" ceiling the
# retired mechanism used -- unchanged rationale, now applied to the LLM's
# own reported source_ids count instead of a computed document frequency.
DEFAULT_MAX_SOURCE_FRACTION = 0.6

# Hard ceiling on how many ranked candidates this module will ever return --
# select_gap only ever consumes the FIRST undecided one, so this only
# bounds the reporting surface, not the pipeline's own behavior.
MAX_CANDIDATES = 200

# NEAR-DUPLICATE DETECTION (Gate A -- independent audit, see module docstring
# addendum below dedupe_near_duplicates): exact-term dedup (rank_and_filter's
# own `seen_terms`, above) is blind to two candidates that are lexically
# DISTINCT but substantively the SAME argument -- a real live run produced
# "empirical-verification-before-shipping" and "prove-before-acting" as two
# separate pages sharing 11 of their combined 13 attributed source_ids
# (Jaccard 11/13 ~= 0.846), with near-verbatim prose in places (the SAME
# multi-clause sentence about a quadratic-scaling fix -- "(a) a synthetic
# benchmark reproduced... (b) a red/green regression cycle showed 15.7x...
# (c) a live measurement confirmed 368s -> 0.6-0.95s" -- appeared word for
# word in both). Calibration point for DEFAULT_DEDUPE_JACCARD: the SAME live
# run's next-highest overlap was a DIFFERENT, genuinely distinct theme
# ("measure-before-you-build" -- establishing a baseline BEFORE starting,
# vs. the other pages' shared theme of proving a FINISHED claim) at Jaccard
# 7/13 ~= 0.538 against either member of the true-duplicate pair. 0.75 sits
# comfortably below the confirmed duplicate (margin 0.096) and well above
# the confirmed-distinct near-miss (margin 0.212) -- biased toward NOT
# merging, because a false merge silently discards a real theme's own page,
# which is strictly worse than leaving two near-duplicates unmerged for a
# later pass to notice (this project's own repeated lesson: gates that
# quietly discard things have bitten it before -- see ISSUE_HANDLING.md).
DEFAULT_DEDUPE_JACCARD = 0.75


def _float_or_default(default: float):
    """Same absent-key handling as ``lib.int_or_default``, for the one
    float-valued CLI arg this module needs (see ``lib.int_or_default``'s
    docstring for why the bare-``$var`` substitution form requires this)."""

    def _parse(value: str) -> float:
        if value == "":
            return default
        return float(value)

    return _parse


@dataclass(frozen=True)
class GapCandidate:
    term: str
    source_count: int
    source_ids: tuple[str, ...]
    claim: str = ""

    def to_dict(self) -> dict:
        d: dict = {"term": self.term, "source_count": self.source_count, "source_ids": list(self.source_ids)}
        if self.claim:
            d["claim"] = self.claim
        return d


def named_concept_titles(wiki_dir: Path) -> list[str]:
    """Lowercased ``title`` (or filename stem, absent one) of every existing
    wiki page -- unchanged from the retired mechanism's own ground truth
    for "already has a page": a page's TITLE is what "already named" means,
    not its body."""
    titles: list[str] = []
    for path in list_wiki_pages(wiki_dir):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        meta, _ = parse_frontmatter(text)
        title = str(meta.get("title") or path.stem)
        titles.append(title.lower())
    return titles


def is_already_named(phrase: str, named_titles: list[str]) -> bool:
    """Unchanged from the retired mechanism: a phrase is "already named" if
    it appears inside an existing page title, or an existing page title is
    fully contained in it (either direction)."""
    return any(phrase in title or title in phrase for title in named_titles)


def find_candidate_issues(raw: object) -> list[str]:
    """Return a list of shape problems with ``raw`` (parsed JSON from
    ``scan_arguments``); empty list == well-formed.

    Required shape: a JSON array (possibly EMPTY -- see module docstring's
    asymmetry note) of ``{"term": str, "source_ids": [str, ...], "claim":
    str (optional)}`` objects.
    """
    if not isinstance(raw, list):
        return ["gap-candidates-raw.json must be a JSON array (possibly empty)"]

    issues: list[str] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            issues.append(f"candidate[{i}] must be an object with 'term' and 'source_ids'")
            continue
        term = entry.get("term")
        if not isinstance(term, str) or not term.strip():
            issues.append(f"candidate[{i}].term is missing or empty")
        source_ids = entry.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            issues.append(f"candidate[{i}].source_ids must be a non-empty list")
        elif not all(isinstance(s, str) and s.strip() for s in source_ids):
            issues.append(f"candidate[{i}].source_ids must be a list of non-empty strings")

    return issues


def existing_source_ids(sources_dir: Path) -> set[str]:
    """Every real filename under ``sources/`` -- the ground truth universe a
    cited ``source_id`` must belong to, or it is hallucinated and dropped.
    Public (promoted from a module-private helper in iteration 6): both this
    module's own hallucination guard AND
    ``wiki_weaver.synthesize.attribute_record``'s (a second, independent
    consumer -- see that module's docstring) need the identical check, so
    the one place this project's own convention prefers ("promote helpers to
    foundation only once 2+ independent consumers exist") applies."""
    if not sources_dir.is_dir():
        return set()
    return {p.name for p in sources_dir.iterdir() if p.is_file()}


def rank_and_filter(
    raw: list[dict],
    *,
    named_titles: list[str],
    valid_source_ids: set[str],
    total_sources: int,
    min_sources: int = DEFAULT_MIN_SOURCES,
    max_source_fraction: float = DEFAULT_MAX_SOURCE_FRACTION,
    max_candidates: int = MAX_CANDIDATES,
) -> tuple[list[GapCandidate], list[str]]:
    """Normalize ``scan_arguments``' raw candidates into ranked, filtered
    ``GapCandidate`` objects -- the deterministic half of the box/tool
    split. Assumes ``raw`` has already passed ``find_candidate_issues``.
    Returns ``(candidates, warnings)``.

    - Drops any ``source_id`` the LLM cited that is not a real file under
      ``sources/`` (a hallucinated citation must not inflate a candidate's
      measured support).
    - Drops any candidate already named by an existing wiki page title
      (belt-and-suspenders: ``scan_arguments`` is told to do this itself,
      but the model cannot certify its own novelty check any more than
      ``plan_pages`` could certify its own plan's syntax).
    - Applies ``min_sources`` (floor) and ``max_source_fraction`` (ceiling,
      only when ``total_sources`` is known) -- same empirically-measured
      thresholds the retired n-gram mechanism used, see module docstring.
    - Ranks by source_count descending, term ascending for determinism.

    DISTINGUISHABLE REJECTION REPORTING (chunked-detection era): now that
    detection runs per-chunk and unions across chunks (see wiki_weaver.
    synthesize.select_chunk / record_chunk_candidates), substantially more
    candidates reach this threshold check than the old single-pass
    scan_arguments ever proposed, and a genuinely corpus-wide argument can
    legitimately hit the max_source_fraction ceiling rather than being
    noise. Every rejection below the floor or above the ceiling is
    reported in ``warnings`` with WHICH threshold it hit and why -- "too
    FEW sources" and "too COMMON/boilerplate" are worded distinctly and are
    never conflated into one generic "excluded" message, so a real theme
    discarded as boilerplate is visible to whoever reads this log, not
    silent.
    """
    max_sources_allowed = int(total_sources * max_source_fraction) if total_sources else MAX_CANDIDATES * 10_000

    seen_terms: set[str] = set()
    candidates: list[GapCandidate] = []
    warnings: list[str] = []
    for entry in raw:
        term = str(entry["term"]).strip()
        key = term.lower()
        if key in seen_terms:
            continue  # exact duplicate term -- keep the first occurrence
        if is_already_named(key, named_titles):
            continue
        source_ids = sorted({s for s in entry["source_ids"] if s in valid_source_ids})
        if not source_ids:
            warnings.append(f"{term!r}: rejected -- every cited source_id was hallucinated (no real support)")
            continue
        count = len(source_ids)
        if count < min_sources:
            warnings.append(
                f"{term!r}: rejected -- {count} source(s), below min_sources floor ({min_sources}) -- "
                "too FEW sources, not too common"
            )
            continue
        if count > max_sources_allowed:
            warnings.append(
                f"{term!r}: rejected -- {count} source(s) exceeds max_source_fraction ceiling "
                f"({max_source_fraction} of {total_sources} sources = {max_sources_allowed}) -- "
                "too COMMON/boilerplate, not too rare; if this looks like a real corpus-wide "
                "argument rather than noise, reconsider max_source_fraction before assuming it is boilerplate"
            )
            continue
        seen_terms.add(key)
        claim = str(entry.get("claim", "")).strip()
        candidates.append(GapCandidate(term=term, source_count=count, source_ids=tuple(source_ids), claim=claim))

    candidates.sort(key=lambda c: (-c.source_count, c.term))
    return candidates[:max_candidates], warnings


def _jaccard(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Set-overlap similarity over two candidates' attributed source_ids.
    ``0.0`` for either-empty (never divide by zero; an empty source_ids
    tuple cannot happen post-``rank_and_filter``, but this stays safe if
    ever called on unfiltered input)."""
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def dedupe_near_duplicates(
    candidates: list[GapCandidate],
    *,
    threshold: float = DEFAULT_DEDUPE_JACCARD,
) -> tuple[list[GapCandidate], list[str]]:
    """Gate A -- collapse candidates whose ATTRIBUTED ``source_ids`` overlap
    at or above ``threshold`` (Jaccard similarity). See ``DEFAULT_DEDUPE_
    JACCARD``'s comment above for the full calibration story (an independent
    audit's 11-of-12-shared-sources finding).

    WHY THIS CANNOT BE EXACT-TERM DEDUP (``rank_and_filter``'s own
    ``seen_terms``, upstream): two candidates can be lexically UNRELATED
    ("prove before acting" vs. "empirical verification before shipping")
    while citing almost the same sources and, empirically, near-verbatim
    prose -- exact-term matching is structurally blind to this, by
    definition (the whole premise of two DIFFERENT terms).

    DETERMINISTIC, NO LLM, NO EMBEDDING: pure set overlap over each
    candidate's own already-validated, already-hallucination-filtered
    ``source_ids`` (``rank_and_filter``'s output) -- no additional model
    call, no vector index, nothing beyond stdlib set arithmetic. Runs
    AFTER ``rank_and_filter``'s own min_sources/max_source_fraction
    threshold filtering -- only already-qualifying candidates are
    considered here.

    MERGE, NEVER SILENT SUPPRESSION: every above-threshold pair is grouped
    into a connected cluster (union-find -- handles a chain of 3+ mutually
    overlapping candidates, not just pairs) and merged into ONE surviving
    candidate: ``source_ids`` is the UNION (a citation that justified
    EITHER candidate is never discarded), and ``term``/``claim`` are taken
    from whichever cluster member had the higher ``source_count`` BEFORE
    merging (ties broken alphabetically -- the same tie-break
    ``rank_and_filter`` itself already uses). Every merge is reported in
    the returned warnings list -- this project has been bitten before by
    gates that quietly discard candidates (see ISSUE_HANDLING.md); this one
    does not.

    STRUCTURAL FIX FOR THE NEAR-VERBATIM-PROSE DEFECT: this gate runs
    BEFORE ``select_gap``/``answer_gap`` ever see the candidate list (see
    ``pipeline/synthesize.dot``'s ``rank_gap_candidates`` node), so only
    ONE surviving candidate per duplicate cluster ever reaches page-writing
    -- the two-pages-with-the-same-sentence defect this gate exists to fix
    cannot recur structurally, because a second page for the SAME cluster
    is never proposed to ``answer_gap`` in the first place.

    Returns ``(survivors, warnings)`` -- ``survivors`` re-sorted by the
    same ``(-source_count, term)`` order ``rank_and_filter`` uses.
    """
    n = len(candidates)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    warnings: list[str] = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = _jaccard(candidates[i].source_ids, candidates[j].source_ids)
            if sim >= threshold:
                shared = set(candidates[i].source_ids) & set(candidates[j].source_ids)
                total = set(candidates[i].source_ids) | set(candidates[j].source_ids)
                warnings.append(
                    f"near-duplicate: {candidates[i].term!r} and {candidates[j].term!r} share "
                    f"{len(shared)} of {len(total)} combined source(s) (jaccard {sim:.3f} >= "
                    f"{threshold}) -- merging"
                )
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    survivors: list[GapCandidate] = []
    for members in clusters.values():
        if len(members) == 1:
            survivors.append(candidates[members[0]])
            continue
        group = sorted((candidates[m] for m in members), key=lambda c: (-c.source_count, c.term))
        canonical = group[0]
        merged_sources = sorted(set().union(*(set(c.source_ids) for c in group)))
        merged_claim = next((c.claim for c in group if c.claim), "")
        merged_from = [c.term for c in group[1:]]
        survivors.append(
            GapCandidate(
                term=canonical.term,
                source_count=len(merged_sources),
                source_ids=tuple(merged_sources),
                claim=merged_claim,
            )
        )
        warnings.append(
            f"merged {merged_from!r} into {canonical.term!r} -- {len(merged_sources)} union "
            f"source(s) total (canonical term alone had {canonical.source_count})"
        )

    survivors.sort(key=lambda c: (-c.source_count, c.term))
    return survivors, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.rank_candidates")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--min-sources",
        required=True,
        type=int_or_default(DEFAULT_MIN_SOURCES),
        help=f"distinct-source floor a candidate must clear (default {DEFAULT_MIN_SOURCES})",
    )
    parser.add_argument(
        "--max-source-fraction",
        required=True,
        type=_float_or_default(DEFAULT_MAX_SOURCE_FRACTION),
        help=f"ceiling on source_count / total corpus sources (default {DEFAULT_MAX_SOURCE_FRACTION})",
    )
    parser.add_argument(
        "--dedupe-threshold",
        required=False,
        default=str(DEFAULT_DEDUPE_JACCARD),
        type=_float_or_default(DEFAULT_DEDUPE_JACCARD),
        help=(
            "Jaccard similarity (over attributed source_ids) at or above which two "
            f"candidates are merged as near-duplicates (default {DEFAULT_DEDUPE_JACCARD}; "
            "see dedupe_near_duplicates' docstring for calibration)"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    # ITERATION 6: reads the ATTRIBUTED list (attribute_select/
    # attribute_sources/attribute_record's output), not scan_arguments' raw
    # seed directly -- see module docstring's ITERATION 6 section.
    raw_path = wr.gap_candidates_attributed_file
    if not raw_path.is_file():
        print(f"{raw_path} does not exist -- the attribution pass must run first", file=sys.stderr)
        print("candidates_bad")
        return 0

    try:
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{raw_path} is not valid JSON: {exc}", file=sys.stderr)
        print("candidates_bad")
        return 0

    issues = find_candidate_issues(raw)
    if issues:
        for issue in issues:
            print(f"gap-candidates-attributed.json: {issue}", file=sys.stderr)
        print("candidates_bad")
        return 0

    named_titles = named_concept_titles(wr.wiki_dir)
    valid_source_ids = existing_source_ids(wr.sources_dir)
    total_sources = len(valid_source_ids)

    candidates, warnings = rank_and_filter(
        raw,
        named_titles=named_titles,
        valid_source_ids=valid_source_ids,
        total_sources=total_sources,
        min_sources=args.min_sources,
        max_source_fraction=args.max_source_fraction,
    )
    for warning in warnings:
        print(warning, file=sys.stderr)

    # Gate A -- near-duplicate detection (see dedupe_near_duplicates' module
    # docstring): runs on the already-filtered candidate list, BEFORE
    # select_gap/answer_gap ever see it, so a duplicate cluster can only
    # ever produce ONE page.
    before_dedupe = len(candidates)
    candidates, dedupe_warnings = dedupe_near_duplicates(candidates, threshold=args.dedupe_threshold)
    for warning in dedupe_warnings:
        print(warning, file=sys.stderr)

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.gap_candidates_file, json.dumps([c.to_dict() for c in candidates], indent=2) + "\n")

    print(
        f"{len(raw)} raw candidate(s) from scan_arguments -> {before_dedupe} ranked, above-threshold "
        f"candidate(s) -> {len(candidates)} after near-duplicate merge (jaccard >= {args.dedupe_threshold})",
        file=sys.stderr,
    )
    print("candidates_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
