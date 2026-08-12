"""wiki_weaver.synthesize.rank_candidates -- the deterministic gate + threshold
discipline that replaced the retired n-gram counter (find_gaps.py).

Task's own required guarantees, carried over unchanged:
  - does NOT propose a concept that already has a page
  - ranks by distinct-source count (not raw term frequency / list order)

New guarantees this module adds (the box/tool split for scan_arguments'
LLM output):
  - malformed/non-list raw JSON -> candidates_bad (fail loud)
  - a well-formed EMPTY list -> candidates_ok with 0 candidates (never bad --
    this is the legitimate "no genuine argument found" outcome)
  - a source_id the LLM cited that is not a real file under sources/ is
    dropped (hallucinated citations must not inflate a candidate's support)

ITERATION 6: this module now reads ``gap_candidates_attributed_file`` (the
attribution loop's output), not ``gap_candidates_raw_file`` (scan_arguments'
own output) directly -- see rank_candidates.py's own ITERATION 6 docstring
section and pipeline/synthesize.dot's header. These tests seed the
ATTRIBUTED file directly (the shape is identical either way -- ``rank_and_
filter``'s own logic did not change, only which file ``main()`` reads).
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import rank_candidates as rc


def _write_sources(wiki_root, names: list[str]) -> None:
    for name in names:
        wiki_root.add_source(name, "irrelevant body text -- rank_candidates never reads source content.\n")


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _write_raw(wr: WikiRoot, payload) -> None:
    """Seeds ``gap_candidates_attributed_file`` -- the file rank_candidates'
    ``main()`` actually reads as of iteration 6 (name kept for the smallest
    possible diff across this test module's many call sites)."""
    if isinstance(payload, str):
        wr.gap_candidates_attributed_file.write_text(payload, encoding="utf-8")
    else:
        wr.gap_candidates_attributed_file.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# find_candidate_issues -- shape validation
# ---------------------------------------------------------------------------


def test_missing_raw_file_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def rank_candidates_main(wr: WikiRoot, min_sources: str = "4", max_source_fraction: str = "0.6") -> int:
    return rc.main(
        [
            "--wiki-root",
            str(wr.root),
            "--min-sources",
            min_sources,
            "--max-source-fraction",
            max_source_fraction,
        ]
    )


def test_invalid_json_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, "{not valid json")
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_non_list_top_level_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, {"term": "not a list"})
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_candidate_missing_term_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [{"source_ids": ["s0.txt"]}])
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_candidate_with_empty_source_ids_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics", "source_ids": []}])
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_find_candidate_issues_directly_for_non_list_message():
    issues = rc.find_candidate_issues({"not": "a list"})
    assert len(issues) == 1
    assert "JSON array" in issues[0]


# ---------------------------------------------------------------------------
# THE critical asymmetry: empty is OK, not bad
# ---------------------------------------------------------------------------


def test_well_formed_empty_list_is_candidates_ok_with_zero_candidates(wiki_root, capsys):
    """A well-covered wiki genuinely has no unnamed argument left --
    scan_arguments declining honestly must route candidates_ok, never
    candidates_bad."""
    wr = _wr(wiki_root)
    _write_raw(wr, [])
    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_ok"
    assert json.loads(wr.gap_candidates_file.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# rank_and_filter -- thresholds, already-named exclusion, hallucination guard
# ---------------------------------------------------------------------------


def test_well_formed_candidate_above_threshold_is_candidates_ok(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_sources(wiki_root, [f"s{i}.txt" for i in range(4)] + [f"pad{i}.txt" for i in range(4)])
    _write_raw(
        wr,
        [
            {
                "term": "token economics",
                "claim": "tokens cost real money",
                "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"],
            }
        ],
    )

    code = rank_candidates_main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_ok"

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert len(candidates) == 1
    assert candidates[0]["term"] == "token economics"
    assert candidates[0]["source_count"] == 4
    assert candidates[0]["claim"] == "tokens cost real money"


def test_does_not_propose_a_concept_that_already_has_a_page(wiki_root):
    """THE required guarantee, carried over from the retired mechanism: a
    page titled to match the phrase excludes it."""
    wiki_root.add_page(
        "token-economics.md",
        '---\ntitle: "Token Economics"\ntype: concept\n---\n\nAlready has a page.\n',
    )
    wr = _wr(wiki_root)
    _write_sources(wiki_root, [f"s{i}.txt" for i in range(4)])
    _write_raw(wr, [{"term": "token economics", "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"]}])

    rank_candidates_main(wr)

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert candidates == []


def test_below_min_sources_is_excluded(wiki_root):
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt", "s1.txt"])
    _write_raw(wr, [{"term": "token economics", "source_ids": ["s0.txt", "s1.txt"]}])

    rank_candidates_main(wr, min_sources="4")

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert candidates == []


def test_above_max_source_fraction_is_excluded_as_boilerplate(wiki_root):
    """A phrase \"discussed\" by every source in the corpus is boilerplate,
    not a theme -- same ceiling the retired mechanism used."""
    wr = _wr(wiki_root)
    names = [f"s{i}.txt" for i in range(10)]
    _write_sources(wiki_root, names)
    _write_raw(wr, [{"term": "press enter", "source_ids": names}])

    rank_candidates_main(wr, min_sources="4", max_source_fraction="0.6")

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert candidates == []


def test_hallucinated_source_id_is_dropped_and_does_not_inflate_count(wiki_root):
    """A source_id the LLM cited that is not a real file under sources/
    must not count toward the candidate's measured support."""
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt", "s1.txt", "s2.txt", "s3.txt"])
    _write_raw(
        wr,
        [
            {
                "term": "token economics",
                "source_ids": ["s0.txt", "s1.txt", "s2.txt", "does-not-exist.txt"],
            }
        ],
    )

    rank_candidates_main(wr, min_sources="4")

    # Only 3 real source_ids survive -- below the min_sources=4 floor.
    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert candidates == []


def test_ranking_order_is_highest_source_count_first(wiki_root):
    wr = _wr(wiki_root)
    names = [f"s{i}.txt" for i in range(10)]
    _write_sources(wiki_root, names)
    _write_raw(
        wr,
        [
            {"term": "harness engineering", "source_ids": names[:4]},
            {"term": "token economics", "source_ids": names[:8]},
        ],
    )

    rank_candidates_main(wr, min_sources="4", max_source_fraction="1.0")

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert candidates[0]["term"] == "token economics"
    assert candidates[0]["source_count"] == 8
    assert candidates[0]["source_count"] >= candidates[-1]["source_count"]


def test_duplicate_term_keeps_first_occurrence(wiki_root):
    wr = _wr(wiki_root)
    names = [f"s{i}.txt" for i in range(4)]
    _write_sources(wiki_root, names)
    _write_raw(
        wr,
        [
            {"term": "Token Economics", "source_ids": names},
            {"term": "token economics", "source_ids": names},
        ],
    )

    rank_candidates_main(wr, min_sources="4", max_source_fraction="1.0")

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert len(candidates) == 1


def test_is_already_named_matches_either_direction():
    assert rc.is_already_named("token economics", ["token economics in llm deployments"])
    assert rc.is_already_named("harness engineering", ["harness engineering"])
    assert not rc.is_already_named("token economics", ["local inference"])


def test_named_concept_titles_reads_frontmatter_title(wiki_root):
    wiki_root.add_page("x.md", '---\ntitle: "Loop Engineering"\ntype: concept\n---\n\nBody.\n')
    wr = WikiRoot(wiki_root.root)

    titles = rc.named_concept_titles(wr.wiki_dir)

    assert "loop engineering" in titles


# ---------------------------------------------------------------------------
# Gate A -- dedupe_near_duplicates (near-duplicate detection, independent
# audit): exact-term dedup above is blind to two LEXICALLY DISTINCT
# candidates that are substantively the same argument. This is the audited,
# real-world calibration case: two synthesized pages,
# "empirical-verification-before-shipping" (12 sources) and
# "prove-before-acting" (12 sources), shared 11 of their combined 13
# attributed source_ids -- jaccard 11/13 ~= 0.846.
# ---------------------------------------------------------------------------

_EMPIRICAL_VERIFICATION_SOURCES = (
    "008.md",
    "010.md",
    "011.md",
    "015.md",
    "020.md",
    "023.md",
    "027.md",
    "034.md",
    "036.md",  # unique to this candidate
    "040.md",
    "049.md",
    "060.md",
)
_PROVE_BEFORE_ACTING_SOURCES = (
    "008.md",
    "010.md",
    "011.md",
    "015.md",
    "020.md",
    "023.md",
    "027.md",
    "034.md",
    "040.md",
    "049.md",
    "059.md",  # unique to this candidate
    "060.md",
)
# A genuinely distinct real-audit theme ("measure-before-you-build" --
# establishing a baseline BEFORE starting, vs. the other two pages' shared
# theme of proving a FINISHED claim): jaccard ~0.538 against either of the
# pair above -- must NOT be merged despite still-substantial source overlap.
_MEASURE_BEFORE_YOU_BUILD_SOURCES = (
    "011.md",
    "015.md",
    "023.md",
    "027.md",
    "031.md",
    "040.md",
    "049.md",
    "060.md",
)


def test_dedupe_near_duplicates_merges_the_11_of_12_audited_case():
    candidates = [
        rc.GapCandidate(
            term="empirical verification before shipping",
            source_count=len(_EMPIRICAL_VERIFICATION_SOURCES),
            source_ids=_EMPIRICAL_VERIFICATION_SOURCES,
            claim="assertions must be grounded in real, observable evidence",
        ),
        rc.GapCandidate(
            term="prove before acting",
            source_count=len(_PROVE_BEFORE_ACTING_SOURCES),
            source_ids=_PROVE_BEFORE_ACTING_SOURCES,
            claim="no claim is accepted until real empirical evidence matches it",
        ),
    ]

    survivors, warnings = rc.dedupe_near_duplicates(candidates, threshold=rc.DEFAULT_DEDUPE_JACCARD)

    assert len(survivors) == 1
    survivor = survivors[0]
    # Union of both candidates' sources -- 13 combined, nothing discarded.
    assert survivor.source_count == 13
    assert set(survivor.source_ids) == set(_EMPIRICAL_VERIFICATION_SOURCES) | set(_PROVE_BEFORE_ACTING_SOURCES)
    # Both original terms are equally supported (12 each) -- canonical term
    # is tie-broken alphabetically, same tie-break rank_and_filter uses.
    assert survivor.term == "empirical verification before shipping"
    assert any("near-duplicate" in w and "merging" in w for w in warnings)
    assert any("merged" in w for w in warnings)


def test_dedupe_near_duplicates_does_not_merge_a_genuinely_distinct_theme():
    """The calibration floor: measure-before-you-build has substantial
    source overlap (jaccard ~0.538) with the audited duplicate pair but is
    a genuinely different theme (baseline-before-starting vs.
    proving-a-finished-claim) -- must survive as its own candidate."""
    candidates = [
        rc.GapCandidate(
            term="empirical verification before shipping",
            source_count=len(_EMPIRICAL_VERIFICATION_SOURCES),
            source_ids=_EMPIRICAL_VERIFICATION_SOURCES,
        ),
        rc.GapCandidate(
            term="measure before you build",
            source_count=len(_MEASURE_BEFORE_YOU_BUILD_SOURCES),
            source_ids=_MEASURE_BEFORE_YOU_BUILD_SOURCES,
        ),
    ]

    survivors, warnings = rc.dedupe_near_duplicates(candidates, threshold=rc.DEFAULT_DEDUPE_JACCARD)

    assert len(survivors) == 2
    assert {c.term for c in survivors} == {"empirical verification before shipping", "measure before you build"}
    assert warnings == []


def test_dedupe_near_duplicates_below_threshold_is_a_noop():
    candidates = [
        rc.GapCandidate(term="a", source_count=4, source_ids=("s0", "s1", "s2", "s3")),
        rc.GapCandidate(term="b", source_count=4, source_ids=("s4", "s5", "s6", "s7")),
    ]
    survivors, warnings = rc.dedupe_near_duplicates(candidates)
    assert len(survivors) == 2
    assert warnings == []


def test_dedupe_near_duplicates_merges_a_chain_of_three_via_connected_components():
    """Transitive merge: A~B above threshold, B~C above threshold, A~C
    below threshold -- all three still land in ONE surviving candidate
    (union-find handles chains, not just pairs)."""
    a = rc.GapCandidate(term="a", source_count=4, source_ids=("s0", "s1", "s2", "s3"))
    b = rc.GapCandidate(term="b", source_count=4, source_ids=("s1", "s2", "s3", "s4"))
    c = rc.GapCandidate(term="c", source_count=4, source_ids=("s2", "s3", "s4", "s5"))

    assert rc._jaccard(a.source_ids, b.source_ids) >= 0.6
    assert rc._jaccard(b.source_ids, c.source_ids) >= 0.6
    assert rc._jaccard(a.source_ids, c.source_ids) < 0.6

    survivors, _warnings = rc.dedupe_near_duplicates([a, b, c], threshold=0.6)

    assert len(survivors) == 1
    assert set(survivors[0].source_ids) == {"s0", "s1", "s2", "s3", "s4", "s5"}


def test_dedupe_near_duplicates_never_silently_drops_a_candidate():
    """The decision must always be visible -- report length matches the
    number of merges, never zero when a merge happened."""
    candidates = [
        rc.GapCandidate(
            term="empirical verification before shipping",
            source_count=len(_EMPIRICAL_VERIFICATION_SOURCES),
            source_ids=_EMPIRICAL_VERIFICATION_SOURCES,
        ),
        rc.GapCandidate(
            term="prove before acting",
            source_count=len(_PROVE_BEFORE_ACTING_SOURCES),
            source_ids=_PROVE_BEFORE_ACTING_SOURCES,
        ),
    ]
    survivors, warnings = rc.dedupe_near_duplicates(candidates, threshold=rc.DEFAULT_DEDUPE_JACCARD)
    assert len(survivors) == 1
    assert len(warnings) >= 1


def test_rank_candidates_main_wires_in_gate_a_dedupe(wiki_root, capsys):
    """End-to-end: rank_candidates.main() itself calls dedupe_near_duplicates
    on the ranked, threshold-filtered list before writing gap-candidates.json."""
    wr = _wr(wiki_root)
    all_sources: list[str] = sorted(set(_EMPIRICAL_VERIFICATION_SOURCES) | set(_PROVE_BEFORE_ACTING_SOURCES))
    _write_sources(wiki_root, all_sources)
    _write_raw(
        wr,
        [
            {
                "term": "empirical verification before shipping",
                "source_ids": list(_EMPIRICAL_VERIFICATION_SOURCES),
            },
            {
                "term": "prove before acting",
                "source_ids": list(_PROVE_BEFORE_ACTING_SOURCES),
            },
        ],
    )

    code = rank_candidates_main(wr, min_sources="4", max_source_fraction="1.0")
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_ok"

    candidates = json.loads(wr.gap_candidates_file.read_text(encoding="utf-8"))
    assert len(candidates) == 1
    assert candidates[0]["source_count"] == 13
