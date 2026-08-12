"""wiki_weaver.synthesize.extract_source_arguments -- the iteration-4 fix:
condenses every type:source wiki page's own "## Thesis"/"## Thesis and
Scope" section into .ai/source-arguments.md, the ARGUMENT-bearing view
scan_arguments reads instead of wiki/overview.md (see
pipeline/synthesize.dot's header for the full account of why).
"""

from __future__ import annotations

from wiki_weaver.lib import WikiRoot
from wiki_weaver.synthesize import extract_source_arguments as esa


def _page(type_: str, title: str, body: str, sources: list[str] | None = None) -> str:
    fm = f'---\ntitle: "{title}"\ntype: {type_}\n'
    if sources:
        fm += "sources:\n" + "".join(f"  - {s}\n" for s in sources)
    fm += "---\n\n"
    return fm + body


def test_is_source_page_matches_only_type_source():
    assert esa.is_source_page({"type": "source"})
    assert esa.is_source_page({"type": "Source"})  # case-insensitive
    assert not esa.is_source_page({"type": "concept"})
    assert not esa.is_source_page({})


def test_source_citation_id_prefers_inline_citation_in_extract():
    from pathlib import Path

    path = Path("wiki/032-example.md")
    extract = "Open-source tools cut token costs by 60-90%. (032-Real_Source.md)"
    assert esa.source_citation_id({}, extract, extract, path) == "032-Real_Source.md"


def test_source_citation_id_falls_back_to_citation_elsewhere_in_body():
    from pathlib import Path

    path = Path("wiki/032-example.md")
    extract = "Open-source tools cut token costs by 60-90%, no citation here."
    body = extract + "\n\nSee (032-Real_Source.md) for the full breakdown."
    assert esa.source_citation_id({}, extract, body, path) == "032-Real_Source.md"


def test_source_citation_id_falls_back_to_frontmatter_then_filename():
    from pathlib import Path

    path = Path("wiki/032-example.md")
    assert (
        esa.source_citation_id({"sources": ["032-Real_Source.md"]}, "no citation", "no citation", path)
        == "032-Real_Source.md"
    )
    assert (
        esa.source_citation_id({"source_id": "032-Real_Source.md"}, "no citation", "no citation", path)
        == "032-Real_Source.md"
    )
    assert esa.source_citation_id({}, "no citation", "no citation", path) == "032-example.md"  # last-resort fallback


def test_extract_thesis_takes_content_up_to_next_heading():
    body = (
        "# Title\n\n**Author:** Someone\n\n"
        "## Thesis and Scope\n\n"
        "This source argues that token costs are the real bottleneck.\n\n"
        "## Scope\n\nMore detail that must NOT be included.\n"
    )
    extract = esa.extract_thesis(body)
    assert "token costs are the real bottleneck" in extract
    assert "must NOT be included" not in extract


def test_extract_thesis_is_case_insensitive_and_tolerates_either_wording():
    body_a = "## THESIS\n\nContent A.\n\n## Next\n\nOther.\n"
    body_b = "### Thesis and Scope\n\nContent B.\n\n## Next\n\nOther.\n"
    assert "Content A" in esa.extract_thesis(body_a)
    assert "Content B" in esa.extract_thesis(body_b)


def test_extract_thesis_falls_back_to_first_prose_paragraph_when_no_heading():
    body = (
        "# Title\n\n**Author:** Someone\n\n**Published:** today\n\n"
        "This is the real opening argument of the source, with no Thesis heading at all.\n\n"
        "## Later Section\n\nMore stuff.\n"
    )
    extract = esa.extract_thesis(body)
    assert "real opening argument" in extract
    assert "Author" not in extract


def test_extract_thesis_returns_empty_string_when_nothing_usable():
    body = "# Title\n\n**Author:** Someone\n\n**Published:** today\n"
    assert esa.extract_thesis(body) == ""


def test_find_source_pages_only_returns_type_source_and_is_sorted(wiki_root):
    wiki_root.add_page("index.md", _page("index", "Index", "# Index\n"))
    wiki_root.add_page("002-b.md", _page("source", "B", "## Thesis\n\nB argues X.\n", sources=["002-b-src.md"]))
    wiki_root.add_page("001-a.md", _page("source", "A", "## Thesis\n\nA argues Y.\n", sources=["001-a-src.md"]))
    wiki_root.add_page("concept.md", _page("concept", "Concept", "## Thesis\n\nNot a source page.\n"))

    wr = WikiRoot(wiki_root.root)
    pages = esa.find_source_pages(wr.wiki_dir)

    names = [p.name for p, _meta, _body in pages]
    assert names == ["001-a.md", "002-b.md"]  # sorted, concept.md and index.md excluded


def test_build_source_arguments_doc_includes_title_and_source_id(wiki_root):
    # Realistic shape: the citation is an inline (NNN-Name.md) marker inside
    # the thesis prose itself -- the actual convention observed in every
    # sampled source page, not a frontmatter sources: dash-list (which this
    # project's own minimal frontmatter parser does not parse into a list).
    wiki_root.add_page(
        "032-cut-costs.md",
        _page(
            "source",
            "Cut Claude Code Token Costs",
            "## Thesis and Scope\n\nOpen-source tools cut token costs by 60-90%. "
            "(032-Cut_Claude_Code_Token_Costs.md)\n\n## Scope\n\nDetail.\n",
        ),
    )
    wr = WikiRoot(wiki_root.root)

    doc, included = esa.build_source_arguments_doc(wr.wiki_dir)

    assert included == 1
    assert "Cut Claude Code Token Costs" in doc
    assert "032-Cut_Claude_Code_Token_Costs.md" in doc
    assert "60-90%" in doc


def test_build_source_arguments_doc_excludes_pages_with_no_usable_extract(wiki_root):
    wiki_root.add_page("stub.md", _page("source", "Stub", "", sources=["stub-src.md"]))
    wr = WikiRoot(wiki_root.root)

    doc, included = esa.build_source_arguments_doc(wr.wiki_dir)

    assert included == 0
    assert "no source-level page produced a usable extract" in doc


def test_main_writes_source_arguments_file(wiki_root, capsys):
    wiki_root.add_page(
        "010-a.md",
        _page("source", "A", "## Thesis\n\nA argues token economics matters.\n", sources=["010-a-src.md"]),
    )
    wr = WikiRoot(wiki_root.root)

    code = esa.main(["--wiki-root", str(wr.root)])

    assert code == 0
    assert wr.source_arguments_file.is_file()
    content = wr.source_arguments_file.read_text(encoding="utf-8")
    assert "token economics matters" in content
    assert "1 source-level page(s) extracted" in capsys.readouterr().err
