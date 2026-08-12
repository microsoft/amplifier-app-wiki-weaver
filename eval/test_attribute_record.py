"""wiki_weaver.synthesize.attribute_record -- the record-half of the
iteration-6 detection/attribution split; box/tool split for
attribute_sources' output.

Required guarantees:
  - a well-formed result REPLACES the seed's source_ids (can add AND
    remove -- the whole point of this pass)
  - a hallucinated source_id (not a real file under sources/) is dropped
  - a missing/malformed result falls back to the seed's ORIGINAL
    source_ids unchanged -- never crashes, never wedges the loop
  - term always comes from the seed, never the (possibly reworded) result
  - merging is idempotent by term (upsert, not append-forever)
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import attribute_record as arec


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _write_sources(wiki_root, names: list[str]) -> None:
    for name in names:
        wiki_root.add_source(name, "irrelevant body text.\n")


def _seed(wr: WikiRoot, term: str, source_ids: list[str], claim: str = "") -> None:
    wr.current_attribution_candidate_file.write_text(
        json.dumps({"term": term, "claim": claim, "source_ids": source_ids}), encoding="utf-8"
    )


def _result(wr: WikiRoot, payload) -> None:
    if isinstance(payload, str):
        wr.current_attribution_result_file.write_text(payload, encoding="utf-8")
    else:
        wr.current_attribution_result_file.write_text(json.dumps(payload), encoding="utf-8")


def test_missing_seed_candidate_file_fails_loud(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = arec.main(["--wiki-root", str(wr.root)])
    assert code == 1


def test_well_formed_result_can_add_and_drop_sources(wiki_root):
    """THE core guarantee: attribution can ADD a source the seed missed and
    DROP a seed source that does not actually hold, in the SAME pass."""
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt", "s1.txt", "s2.txt", "s9.txt"])
    _seed(wr, "token economics", ["s0.txt", "s1.txt"], claim="original claim")
    _result(
        wr,
        {
            "term": "token economics",
            "claim": "refined claim",
            "source_ids": ["s0.txt", "s2.txt", "s9.txt"],  # dropped s1, added s2+s9
        },
    )

    code = arec.main(["--wiki-root", str(wr.root)])
    assert code == 0

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    rec = progress["results"][0]
    assert rec["term"] == "token economics"
    assert sorted(rec["source_ids"]) == ["s0.txt", "s2.txt", "s9.txt"]
    assert rec["claim"] == "refined claim"


def test_hallucinated_source_id_in_result_is_dropped(wiki_root):
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt"])
    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, {"term": "token economics", "source_ids": ["s0.txt", "does-not-exist.txt"]})

    arec.main(["--wiki-root", str(wr.root)])

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    assert progress["results"][0]["source_ids"] == ["s0.txt"]


def test_missing_result_falls_back_to_seed_source_ids(wiki_root, capsys):
    """attribute_sources never ran or crashed mid-write -- must not wedge
    the loop; falls back to the seed, unchanged."""
    wr = _wr(wiki_root)
    _seed(wr, "token economics", ["s0.txt", "s1.txt"], claim="seed claim")

    code = arec.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert "falling back to scan_arguments' seed" in capsys.readouterr().err

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    rec = progress["results"][0]
    assert rec["source_ids"] == ["s0.txt", "s1.txt"]
    assert rec["claim"] == "seed claim"


def test_malformed_result_json_falls_back_to_seed(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, "{not valid json")

    code = arec.main(["--wiki-root", str(wr.root)])
    assert code == 0
    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    assert progress["results"][0]["source_ids"] == ["s0.txt"]


def test_result_with_malformed_source_ids_field_falls_back_to_seed(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, {"term": "token economics", "source_ids": "not a list"})

    code = arec.main(["--wiki-root", str(wr.root)])
    assert code == 0
    err = capsys.readouterr().err
    assert "malformed source_ids" in err
    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    assert progress["results"][0]["source_ids"] == ["s0.txt"]


def test_term_always_comes_from_seed_never_result(wiki_root):
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt"])
    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, {"term": "Token Economics (reworded)", "source_ids": ["s0.txt"]})

    arec.main(["--wiki-root", str(wr.root)])

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    assert progress["results"][0]["term"] == "token economics"


def test_merge_is_idempotent_upsert_by_term(wiki_root):
    wr = _wr(wiki_root)
    _write_sources(wiki_root, ["s0.txt", "s1.txt"])
    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, {"term": "token economics", "source_ids": ["s0.txt"]})
    arec.main(["--wiki-root", str(wr.root)])

    _seed(wr, "token economics", ["s0.txt"])
    _result(wr, {"term": "token economics", "source_ids": ["s0.txt", "s1.txt"]})
    arec.main(["--wiki-root", str(wr.root)])

    progress = json.loads(wr.attribution_progress_file.read_text(encoding="utf-8"))
    assert len(progress["results"]) == 1
    assert sorted(progress["results"][0]["source_ids"]) == ["s0.txt", "s1.txt"]


def test_resolve_attribution_directly_add_and_drop():
    seed = {"term": "token economics", "claim": "seed claim", "source_ids": ["s0", "s1"]}
    result = {"term": "token economics", "claim": "refined", "source_ids": ["s0", "s2"]}
    record, warnings = arec.resolve_attribution(seed, result, valid_source_ids={"s0", "s1", "s2"})
    assert record == {"term": "token economics", "source_ids": ["s0", "s2"], "claim": "refined"}
    assert warnings == []


def test_resolve_attribution_none_result_falls_back():
    seed = {"term": "token economics", "claim": "seed claim", "source_ids": ["s0", "s1"]}
    record, warnings = arec.resolve_attribution(seed, None, valid_source_ids={"s0", "s1"})
    assert record["source_ids"] == ["s0", "s1"]
    assert record["claim"] == "seed claim"
    assert len(warnings) == 1
