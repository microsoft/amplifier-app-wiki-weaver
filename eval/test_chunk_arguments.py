"""wiki_weaver.synthesize.chunk_arguments -- fixed-size chunking of the
corpus's source theses, the fix for scan_arguments' measured attention-
dilution at full-corpus scale (see module docstring for the live-run
evidence).

Required guarantees:
  - chunk membership is purely positional (same corpus -> same chunks,
    deterministic, no shuffling)
  - --chunk-size controls group size; default is 8 (empirically derived)
  - a chunk's rendered document uses the SAME section format as the full
    source-arguments.md document (format_argument_section, shared)
  - argument_chunks_file is always written, even when there are zero
    usable source pages (an empty JSON array, never a crash)
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot
from wiki_weaver.synthesize import chunk_arguments as ca


def _page(type_: str, title: str, body: str, sources: list[str] | None = None) -> str:
    fm = f'---\ntitle: "{title}"\ntype: {type_}\n'
    if sources:
        fm += "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    fm += "---\n\n"
    return fm + body


def _add_source_page(wiki_root, name: str, title: str, thesis: str) -> None:
    wiki_root.add_page(name, _page("source", title, f"## Thesis\n\n{thesis}\n\n({name.replace('.md', '-src.md')})\n"))


def _main(wr: WikiRoot, chunk_size: str = "8") -> int:
    return ca.main(["--wiki-root", str(wr.root), "--chunk-size", chunk_size])


def test_chunk_entries_splits_positionally_without_reordering():
    entries = [(f"T{i}", f"s{i}.md", f"extract {i}") for i in range(10)]
    chunks = ca.chunk_entries(entries, 4)
    assert len(chunks) == 3
    assert chunks[0] == entries[0:4]
    assert chunks[1] == entries[4:8]
    assert chunks[2] == entries[8:10]


def test_chunk_entries_defends_against_non_positive_size():
    entries = [(f"T{i}", f"s{i}.md", "x") for i in range(3)]
    chunks = ca.chunk_entries(entries, 0)
    assert len(chunks) == 3  # treated as size 1, never divides by zero


def test_format_chunk_doc_uses_same_section_format_as_full_document():
    from wiki_weaver.synthesize.extract_source_arguments import format_argument_section

    chunk = [("My Title", "001-x.md", "The real argument.")]
    doc = ca.format_chunk_doc(chunk, 0, 3)
    assert format_argument_section("My Title", "001-x.md", "The real argument.") in doc
    assert "chunk 1 of 3" in doc


def test_format_chunk_doc_handles_empty_chunk():
    doc = ca.format_chunk_doc([], 0, 1)
    assert "(this chunk is empty)" in doc


def test_main_writes_argument_chunks_file_split_into_groups(wiki_root, capsys):
    for i in range(10):
        _add_source_page(wiki_root, f"{i:03d}-src.md", f"Source {i}", f"Source {i} argues thing {i}.")
    wr = WikiRoot(wiki_root.root)

    code = _main(wr, chunk_size="4")
    assert code == 0

    chunk_docs = json.loads(wr.argument_chunks_file.read_text(encoding="utf-8"))
    assert len(chunk_docs) == 3  # ceil(10/4)
    assert all(isinstance(c, str) for c in chunk_docs)
    # First chunk holds the first 4 sources' extracts, not the last 4.
    assert "Source 0 argues" in chunk_docs[0]
    assert "Source 3 argues" in chunk_docs[0]
    assert "Source 4 argues" not in chunk_docs[0]

    err = capsys.readouterr().err
    assert "10 source thesis entries split into 3 chunk(s)" in err


def test_main_default_chunk_size_is_eight(wiki_root):
    for i in range(9):
        _add_source_page(wiki_root, f"{i:03d}-src.md", f"Source {i}", f"Source {i} argues thing {i}.")
    wr = WikiRoot(wiki_root.root)

    ca.main(["--wiki-root", str(wr.root)])

    chunk_docs = json.loads(wr.argument_chunks_file.read_text(encoding="utf-8"))
    assert len(chunk_docs) == 2  # ceil(9/8)
    assert ca.DEFAULT_CHUNK_SIZE == 8


def test_main_writes_empty_array_when_no_usable_source_pages(wiki_root):
    wr = WikiRoot(wiki_root.root)
    code = _main(wr)
    assert code == 0
    assert json.loads(wr.argument_chunks_file.read_text(encoding="utf-8")) == []


def test_build_chunk_docs_directly_reports_total_entries(wiki_root):
    for i in range(5):
        _add_source_page(wiki_root, f"{i:03d}-src.md", f"Source {i}", f"Source {i} argues thing {i}.")
    wr = WikiRoot(wiki_root.root)

    docs, total = ca.build_chunk_docs(wr.wiki_dir, chunk_size=2)
    assert total == 5
    assert len(docs) == 3  # ceil(5/2)
