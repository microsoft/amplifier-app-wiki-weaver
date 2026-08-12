"""wiki_weaver.synthesize.record_chunk_candidates -- the record-half of the
chunked detection loop.

Required guarantees:
  - missing/malformed current-chunk-candidates-raw.json -> chunks_bad
    (fail loud, chunk NOT marked scanned)
  - missing/malformed progress (no current_index) -> chunks_bad
  - a well-formed result is UNIONED into the accumulated list: a term
    already present gets its source_ids UNIONED (never discarded), a new
    term is appended, chunk_indices tracks provenance
  - the chunk is marked scanned only on success
  - merging is idempotent by term (upsert on source_ids union, not
    duplicate entries)
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import record_chunk_candidates as rcc


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _seed_progress(wr: WikiRoot, current_index: int, scanned=None, accumulated=None) -> None:
    payload = {
        "chunks_signature": "sig",
        "scanned_indices": scanned or [],
        "accumulated": accumulated or [],
        "current_index": current_index,
    }
    wr.chunk_detection_progress_file.write_text(json.dumps(payload), encoding="utf-8")


def _write_raw(wr: WikiRoot, payload) -> None:
    if isinstance(payload, str):
        wr.current_chunk_candidates_raw_file.write_text(payload, encoding="utf-8")
    else:
        wr.current_chunk_candidates_raw_file.write_text(json.dumps(payload), encoding="utf-8")


def _main(wr: WikiRoot) -> int:
    return rcc.main(["--wiki-root", str(wr.root)])


def test_missing_raw_file_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_malformed_raw_json_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, "{not valid json")
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_candidate_missing_source_ids_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [{"term": "token economics"}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_missing_progress_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_raw(wr, [])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_empty_raw_list_is_chunks_ok_and_marks_chunk_scanned(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_progress(wr, current_index=0)
    _write_raw(wr, [])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_ok"

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    assert progress["scanned_indices"] == [0]
    assert progress["accumulated"] == []


def test_new_term_is_appended_with_chunk_index(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_progress(wr, current_index=2)
    _write_raw(wr, [{"term": "token economics", "claim": "tokens cost money", "source_ids": ["s0", "s1"]}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_ok"

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    assert progress["scanned_indices"] == [2]
    assert len(progress["accumulated"]) == 1
    entry = progress["accumulated"][0]
    assert entry["term"] == "token economics"
    assert sorted(entry["source_ids"]) == ["s0", "s1"]
    assert entry["chunk_indices"] == [2]


def test_repeated_term_across_chunks_unions_source_ids_not_discarded(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_progress(
        wr,
        current_index=1,
        scanned=[0],
        accumulated=[{"term": "token economics", "claim": "seed claim", "source_ids": ["s0"], "chunk_indices": [0]}],
    )
    _write_raw(wr, [{"term": "Token Economics", "claim": "reworded claim", "source_ids": ["s1", "s2"]}])
    code = _main(wr)
    assert code == 0

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    assert progress["scanned_indices"] == [0, 1]
    assert len(progress["accumulated"]) == 1  # merged, not duplicated
    entry = progress["accumulated"][0]
    assert sorted(entry["source_ids"]) == ["s0", "s1", "s2"]  # union, s0 never discarded
    assert entry["claim"] == "seed claim"  # first occurrence wins
    assert entry["chunk_indices"] == [0, 1]


def test_distinct_terms_from_different_chunks_both_kept(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_progress(
        wr,
        current_index=1,
        scanned=[0],
        accumulated=[{"term": "token economics", "claim": "", "source_ids": ["s0"], "chunk_indices": [0]}],
    )
    _write_raw(wr, [{"term": "local inference", "claim": "", "source_ids": ["s5"]}])
    _main(wr)

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    terms = {e["term"] for e in progress["accumulated"]}
    assert terms == {"token economics", "local inference"}


def test_chunk_not_marked_scanned_on_malformed_raw_output(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_progress(wr, current_index=0)
    _write_raw(wr, "not json at all {")
    _main(wr)

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    assert progress["scanned_indices"] == []  # never marked scanned -- retry will re-select chunk 0


def test_merge_candidate_directly_unions_and_tracks_chunk_indices():
    accumulated = [{"term": "a", "claim": "seed", "source_ids": ["s0"], "chunk_indices": [0]}]
    rcc.merge_candidate(accumulated, {"term": "A", "claim": "new", "source_ids": ["s1"]}, chunk_index=3)
    assert len(accumulated) == 1
    assert sorted(accumulated[0]["source_ids"]) == ["s0", "s1"]
    assert accumulated[0]["claim"] == "seed"
    assert accumulated[0]["chunk_indices"] == [0, 3]


def test_merge_chunk_candidates_does_not_mutate_input():
    original = [{"term": "a", "claim": "", "source_ids": ["s0"], "chunk_indices": [0]}]
    result = rcc.merge_chunk_candidates(original, [{"term": "a", "claim": "", "source_ids": ["s1"]}], chunk_index=1)
    assert original[0]["source_ids"] == ["s0"]  # untouched
    assert sorted(result[0]["source_ids"]) == ["s0", "s1"]
