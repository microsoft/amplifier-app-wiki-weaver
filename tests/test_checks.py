from pathlib import Path

from wiki_weaver import checks as ck

SRC = {"a.md": "Wren Talbot: The orchard sensor rollout ships on Friday the seventh."}
FM = "---\ntitle: T\ntype: topics\nsources:\n  - a.md\nlast_updated: 2031-03-02\n---\n"


def test_citation_ok_and_whitespace_normalized():
    page = FM + '- Ships Friday [a.md: "orchard sensor   rollout ships on Friday"]\n'
    assert ck.check_citations("p.md", page, SRC) == []


def test_citation_not_verbatim_fails():
    page = FM + '- [a.md: "orchard sensors roll out on Friday"]\n'
    assert any("not found verbatim" in e for e in ck.check_citations("p.md", page, SRC))


def test_citation_short_quote_and_unknown_and_bare():
    page = FM + '[a.md: "ships on Friday"] [b.md: "one two three four five"] [a.md]\n'
    errs = ck.check_citations("p.md", page, SRC)
    assert any("under 5 words" in e for e in errs)
    assert any("unknown source" in e for e in errs)
    assert any("without a quote" in e for e in errs)


def test_cited_source_must_be_listed():
    page = (
        FM.replace("  - a.md\n", "  - other.md\n") + '[a.md: "orchard sensor rollout ships on"]\n'
    )
    assert any("not in frontmatter" in e for e in ck.check_citations("p.md", page, SRC))


def test_frontmatter_required_fields():
    assert ck.check_frontmatter("p.md", "no frontmatter") != []
    errs = ck.check_frontmatter("p.md", "---\ntitle: T\n---\nbody")
    assert {e.split("'")[1] for e in errs} == {"type", "sources", "last_updated"}
    assert ck.check_frontmatter("p.md", FM) == []


def test_source_cap():
    many = (
        "---\ntitle: T\ntype: x\nlast_updated: 2031-01-01\nsources:\n"
        + "".join(f"  - s{i}.md\n" for i in range(11))
        + "---\n"
    )
    assert ck.check_source_cap("p.md", many)
    assert not ck.check_source_cap("p.md", FM)


def test_duplicate_h2():
    assert ck.check_duplicate_headings("p.md", FM + "## A\nx\n## A\ny\n")
    assert not ck.check_duplicate_headings("p.md", FM + "## A\n### A\n")


def test_links(tmp_path: Path):
    (tmp_path / "there.md").write_text("x")
    ok = FM + "[t](there.md) [w](https://example.com) [[there]] [s](#top)\n"
    assert ck.check_links("p.md", ok, tmp_path) == []
    bad = FM + "[t](missing.md) [[nowhere]]\n"
    assert len(ck.check_links("p.md", bad, tmp_path)) == 2


def test_content_loss_guard():
    before = FM + "## Plan\n" + "".join(f"line {i}\n" for i in range(10))
    grown = before + "line 10\n"
    assert ck.check_content_loss("p.md", before, grown) == []
    shrunk = FM + "## Plan\nline 0\nline 1\n"
    assert any("lost" in e for e in ck.check_content_loss("p.md", before, shrunk))
    no_heading = before.replace("## Plan\n", "")
    assert any("heading" in e for e in ck.check_content_loss("p.md", before, no_heading))
    marked = shrunk + "<!-- superseded: 2031-03-05 --> plan changed\n"
    assert ck.check_content_loss("p.md", before, marked) == []


def test_marker_on_kept_line_is_not_loss():
    before = FM + "## Plan\nShips Friday.\n"
    after = FM + "## Plan\n<!-- superseded: 2031-03-05 --> Ships Friday.\nNow Tuesday.\n"
    assert ck.check_content_loss("p.md", before, after) == []
