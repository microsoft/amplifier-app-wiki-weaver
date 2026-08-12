"""wiki_weaver.synthesize.check_nav_pages -- the fail-loud precondition gate
in front of scan_arguments (the LLM box that reads wiki/index.md +
.ai/source-arguments.md ONLY -- iteration-4 fix, see pipeline/synthesize.dot's
header). This project has been bitten three times by a step silently running
against a missing/stub/near-empty input and producing nothing that looked
identical to a legitimate empty result -- this test file guards the one gate
built specifically to catch that class of failure before scan_arguments'
model call ever fires.
"""

from __future__ import annotations

from wiki_weaver.lib import WikiRoot
from wiki_weaver.synthesize import check_nav_pages

INDEX_BODY = "---\ntitle: Index\ntype: index\n---\n\n" + ("# Index\n\nreal content here.\n" * 5)


def _source_page(n: int, thesis: str = "This source argues something real and specific about the corpus.") -> str:
    return (
        f'---\ntitle: "Source {n}"\ntype: source\nsources:\n  - {n:03d}-source.md\n---\n\n'
        f"# Source {n}\n\n**Author:** Someone\n\n## Thesis\n\n{thesis}\n\n## Scope\n\nMore detail.\n"
    )


def test_missing_everything_is_nav_missing(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = check_nav_pages.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_missing"


def test_missing_index_with_enough_source_pages_is_nav_missing(wiki_root, capsys):
    for i in range(4):
        wiki_root.add_page(f"{i:03d}-source.md", _source_page(i))
    wr = WikiRoot(wiki_root.root)
    code = check_nav_pages.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_missing"


def test_index_present_but_too_few_source_pages_is_nav_missing(wiki_root, capsys):
    wiki_root.add_page("index.md", INDEX_BODY)
    # Only 2 usable source pages -- below the default floor of 4.
    wiki_root.add_page("000-source.md", _source_page(0))
    wiki_root.add_page("001-source.md", _source_page(1))
    wr = WikiRoot(wiki_root.root)
    code = check_nav_pages.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_missing"


def test_source_pages_with_stub_thesis_do_not_count_toward_floor(wiki_root, capsys):
    """A near-empty class of failure this node exists to catch: source pages
    exist but their thesis extract is trivially short (or absent), so they
    must not silently count as 'usable' just because the file exists."""
    wiki_root.add_page("index.md", INDEX_BODY)
    for i in range(4):
        # "## Thesis" heading present but the section itself is a stub.
        stub = f'---\ntitle: "Source {i}"\ntype: source\nsources:\n  - {i:03d}-source.md\n---\n\n## Thesis\n\nX.\n'
        wiki_root.add_page(f"{i:03d}-source.md", stub)
    wr = WikiRoot(wiki_root.root)
    code = check_nav_pages.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_missing"


def test_index_and_enough_source_pages_is_nav_ok(wiki_root, capsys):
    wiki_root.add_page("index.md", INDEX_BODY)
    for i in range(4):
        wiki_root.add_page(f"{i:03d}-source.md", _source_page(i))
    wr = WikiRoot(wiki_root.root)

    code = check_nav_pages.main(["--wiki-root", str(wr.root)])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_ok"


def test_min_source_pages_param_is_honored(wiki_root, capsys):
    """--min-source-pages lets a denser/sparser corpus retune the floor,
    same as min_sources does for rank_candidates -- pipeline/synthesize.dot
    wires this from the same $min_sources context var."""
    wiki_root.add_page("index.md", INDEX_BODY)
    for i in range(2):
        wiki_root.add_page(f"{i:03d}-source.md", _source_page(i))
    wr = WikiRoot(wiki_root.root)

    code = check_nav_pages.main(["--wiki-root", str(wr.root), "--min-source-pages", "2"])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_ok"


def test_find_nav_problems_reports_missing_index_and_source_page_floor(wiki_root):
    wr = WikiRoot(wiki_root.root)
    problems = check_nav_pages.find_nav_problems(wr.wiki_dir)
    assert len(problems) == 2
    assert any("index.md" in p for p in problems)
    assert any("source-level" in p for p in problems)


def test_non_source_type_pages_are_never_counted_toward_the_floor(wiki_root, capsys):
    """A concept/tool/theme page must never be mistaken for a source-level
    page just because it happens to have a heading that matches -- only
    frontmatter type: source counts (see extract_source_arguments)."""
    wiki_root.add_page("index.md", INDEX_BODY)
    for i in range(4):
        not_a_source = f'---\ntitle: "Concept {i}"\ntype: concept\n---\n\n## Thesis\n\nThis looks like a thesis but is not a source page.\n'
        wiki_root.add_page(f"concept-{i}.md", not_a_source)
    wr = WikiRoot(wiki_root.root)
    code = check_nav_pages.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "nav_missing"
