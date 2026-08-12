"""wiki_weaver.synthesize.select_chunk -- the select-half of the chunked
detection loop.

Required guarantees:
  - missing/malformed argument-chunks.json -> chunks_bad (fail loud)
  - chunks are selected in order, one at a time, resumable via a
    signature-stamped progress file (same discipline as attribute_select)
  - a different chunks signature resets progress rather than mixing two
    different chunkings' candidates
  - once every chunk is scanned -> all_chunks_scanned, and the
    accumulated union is flattened into gap_candidates_raw_file (the SAME
    file attribute_select already reads, unchanged)
  - a well-formed EMPTY chunk list -> all_chunks_scanned immediately, with
    an empty gap_candidates_raw_file (never chunks_bad)
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import select_chunk as sc


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _write_chunks(wr: WikiRoot, chunk_docs) -> None:
    wr.argument_chunks_file.write_text(json.dumps(chunk_docs), encoding="utf-8")


def _main(wr: WikiRoot) -> int:
    return sc.main(["--wiki-root", str(wr.root)])


def test_missing_chunks_file_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_malformed_chunks_json_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    wr.argument_chunks_file.write_text("{not valid json", encoding="utf-8")
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_non_list_of_strings_is_chunks_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, [{"not": "a string"}])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "chunks_bad"


def test_empty_chunk_list_routes_all_chunks_scanned_with_empty_union(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, [])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all_chunks_scanned"
    assert json.loads(wr.gap_candidates_raw_file.read_text(encoding="utf-8")) == []


def test_selects_first_chunk_and_writes_current_chunk_file(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, ["chunk zero content", "chunk one content"])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_chunk"
    assert wr.current_chunk_file.read_text(encoding="utf-8") == "chunk zero content"

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    assert progress["current_index"] == 0


def test_second_call_picks_next_chunk_once_first_is_scanned(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, ["chunk zero", "chunk one"])
    _main(wr)
    capsys.readouterr()

    # Simulate record_chunk_candidates having completed chunk 0.
    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    progress["scanned_indices"] = [0]
    progress["accumulated"] = [{"term": "x", "claim": "", "source_ids": ["s0"], "chunk_indices": [0]}]
    wr.chunk_detection_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_chunk"
    assert wr.current_chunk_file.read_text(encoding="utf-8") == "chunk one"


def test_all_scanned_flattens_accumulated_into_gap_candidates_raw_file(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, ["chunk zero"])
    _main(wr)
    capsys.readouterr()

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    progress["scanned_indices"] = [0]
    progress["accumulated"] = [
        {"term": "token economics", "claim": "tokens cost money", "source_ids": ["s0", "s1"], "chunk_indices": [0]}
    ]
    wr.chunk_detection_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "all_chunks_scanned"

    final = json.loads(wr.gap_candidates_raw_file.read_text(encoding="utf-8"))
    assert len(final) == 1
    assert final[0]["term"] == "token economics"
    assert final[0]["source_ids"] == ["s0", "s1"]
    assert final[0]["chunk_count"] == 1


def test_different_chunks_signature_resets_progress(wiki_root, capsys):
    wr = _wr(wiki_root)
    _write_chunks(wr, ["chunk zero"])
    _main(wr)
    capsys.readouterr()

    progress = json.loads(wr.chunk_detection_progress_file.read_text(encoding="utf-8"))
    progress["scanned_indices"] = [0]
    progress["accumulated"] = [{"term": "stale", "claim": "", "source_ids": ["s9"], "chunk_indices": [0]}]
    wr.chunk_detection_progress_file.write_text(json.dumps(progress), encoding="utf-8")

    # A fresh chunk_arguments run produced a DIFFERENT chunk list.
    _write_chunks(wr, ["different chunk zero", "different chunk one"])
    code = _main(wr)
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_chunk"
    assert wr.current_chunk_file.read_text(encoding="utf-8") == "different chunk zero"


def test_finalize_union_reports_chunk_count_directly():
    accumulated = [
        {"term": "a", "claim": "c1", "source_ids": ["s0"], "chunk_indices": [0]},
        {"term": "b", "claim": "c2", "source_ids": ["s1", "s2"], "chunk_indices": [0, 1, 2]},
    ]
    final = sc.finalize_union(accumulated)
    assert final[0]["chunk_count"] == 1
    assert final[1]["chunk_count"] == 3
