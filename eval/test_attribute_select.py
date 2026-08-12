"""wiki_weaver.synthesize.attribute_select -- the select-half of the
iteration-6 detection/attribution split.

Required guarantees:
  - missing/malformed gap-candidates-raw.json -> candidates_bad (fail loud,
    same discipline rank_candidates used to apply to this file directly)
  - unique candidates (dedup by lowercased term) are proposed one at a time
  - a candidate already named by an existing wiki page is dropped BEFORE
    ever costing an attribution call
  - all candidates attributed -> all_attributed, and the FINAL flat
    gap-candidates-attributed.json is written
  - progress is resumable within one scan_arguments run (same raw
    signature) and reset across a fresh one (different raw signature)
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import attribute_select as asel


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _write_raw(wr: WikiRoot, payload) -> None:
    wr.gap_candidates_raw_file.write_text(json.dumps(payload), encoding="utf-8")


def _main(wr: WikiRoot, max_candidates: str = "25") -> int:
    return asel.main(["--wiki-root", str(wr.root), "--max-candidates", max_candidates])


def test_missing_raw_file_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_malformed_raw_json_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    wr.gap_candidates_raw_file.write_text("{not valid json", encoding="utf-8")
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_candidate_missing_source_ids_is_candidates_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics"}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "candidates_bad"


def test_empty_raw_list_routes_all_attributed_with_empty_final_file(wiki_root, capsys):
    """A well-formed EMPTY detection list means nothing to attribute --
    routes all_attributed immediately with an empty final artifact, never
    candidates_bad (mirrors rank_candidates' own asymmetry)."""
    wr = _wr(wiki_root)
    _write_raw(wr, [])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all_attributed"
    assert json.loads(wr.gap_candidates_attributed_file.read_text(encoding="utf-8")) == []


def test_selects_one_candidate_and_writes_current_attribution_candidate(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(
        wr,
        [{"term": "token economics", "claim": "tokens cost real money", "source_ids": ["s0.txt", "s1.txt"]}],
    )
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_candidate"

    candidate = json.loads(wr.current_attribution_candidate_file.read_text(encoding="utf-8"))
    assert candidate["term"] == "token economics"
    assert candidate["claim"] == "tokens cost real money"
    assert candidate["source_ids"] == ["s0.txt", "s1.txt"]


def test_dedupes_by_lowercased_term_first_occurrence_wins(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(
        wr,
        [
            {"term": "Token Economics", "source_ids": ["s0.txt"]},
            {"term": "token economics", "source_ids": ["s1.txt", "s2.txt"]},
        ],
    )
    _main(wr)
    candidate = json.loads(wr.current_attribution_candidate_file.read_text(encoding="utf-8"))
    assert candidate["source_ids"] == ["s0.txt"]  # first occurrence kept


def test_already_named_candidate_is_dropped_before_costing_attribution(wiki_root, capsys):
    """The cost optimization: a term matching an existing wiki page title
    never reaches attribute_sources at all."""
    wiki_root.add_page(
        "token-economics.md",
        '---\ntitle: "Token Economics"\ntype: concept\n---\n\nAlready has a page.\n',
    )
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics", "source_ids": ["s0.txt"]}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all_attributed"
    assert json.loads(wr.gap_candidates_attributed_file.read_text(encoding="utf-8")) == []


def test_max_candidates_ceiling_drops_excess(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(
        wr,
        [{"term": f"term-{i}", "source_ids": [f"s{i}.txt"]} for i in range(5)],
    )
    code = _main(wr, max_candidates="2")
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_candidate"
    candidate = json.loads(wr.current_attribution_candidate_file.read_text(encoding="utf-8"))
    assert candidate["term"] == "term-0"


def test_second_call_picks_next_candidate_once_first_is_attributed(wiki_root, capsys):
    """Simulates the loop: after attribute_record merges a result for the
    first candidate into progress, the next attribute_select call must
    pick the SECOND candidate, not re-select the first."""
    wr = _wr(wiki_root)
    _write_raw(
        wr,
        [
            {"term": "token economics", "source_ids": ["s0.txt"]},
            {"term": "local inference", "source_ids": ["s1.txt"]},
        ],
    )
    _main(wr)
    capsys.readouterr()

    # Simulate attribute_record having completed "token economics".
    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    progress["results"].append({"term": "token economics", "source_ids": ["s0.txt", "s2.txt"], "claim": ""})
    wr.attribution_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_candidate"
    candidate = json.loads(wr.current_attribution_candidate_file.read_text(encoding="utf-8"))
    assert candidate["term"] == "local inference"


def test_all_attributed_flattens_progress_into_final_file(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics", "source_ids": ["s0.txt"]}])
    _main(wr)
    capsys.readouterr()

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    progress["results"].append(
        {"term": "token economics", "source_ids": ["s0.txt", "s3.txt", "s4.txt"], "claim": "tokens cost money"}
    )
    wr.attribution_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all_attributed"
    final = json.loads(wr.gap_candidates_attributed_file.read_text(encoding="utf-8"))
    assert len(final) == 1
    assert final[0]["term"] == "token economics"
    assert final[0]["source_ids"] == ["s0.txt", "s3.txt", "s4.txt"]


def test_different_raw_signature_resets_progress(wiki_root, capsys):
    """A restarted invocation whose scan_arguments produced DIFFERENT raw
    content must not reuse stale progress from a prior, different run."""
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics", "source_ids": ["s0.txt"]}])
    _main(wr)
    capsys.readouterr()

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    progress["results"].append({"term": "token economics", "source_ids": ["s0.txt", "s9.txt"], "claim": ""})
    wr.attribution_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    # A fresh scan_arguments run proposes a DIFFERENT candidate list.
    _write_raw(wr, [{"term": "local inference", "source_ids": ["s5.txt"]}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_candidate"
    candidate = json.loads(wr.current_attribution_candidate_file.read_text(encoding="utf-8"))
    assert candidate["term"] == "local inference"


def test_dedupe_and_filter_directly():
    raw = [
        {"term": "A", "source_ids": ["s0"]},
        {"term": "a", "source_ids": ["s1"]},
        {"term": "B", "source_ids": ["s2"]},
    ]
    out = asel.dedupe_and_filter(raw, named_titles=["b"])
    assert [e["term"] for e in out] == ["A"]
