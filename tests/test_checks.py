from pathlib import Path

from wiki_weaver import checks as ck

SRC = {1: ("a.md", "Wren Talbot: The orchard sensor rollout ships on Friday the seventh.")}
FM = "---\ntitle: T\ntype: topics\nsources:\n  - a.md\nlast_updated: 2031-03-02\n---\n"


def test_citation_ok_and_whitespace_normalized():
    page = FM + '- Ships Friday [s1: "orchard sensor   rollout ships on Friday"]\n'
    assert ck.check_citations("p.md", page, SRC) == []


def test_citation_not_verbatim_fails():
    page = FM + '- [s1: "orchard sensors roll out on Friday"]\n'
    assert any("not found verbatim" in e for e in ck.check_citations("p.md", page, SRC))


def test_citation_short_quote_and_unknown_and_bare():
    page = FM + '[s1: "ships on Friday"] [s9: "one two three four five"] [s1]\n'
    errs = ck.check_citations("p.md", page, SRC)
    assert any("under 5 words" in e for e in errs)
    assert any("unknown source" in e for e in errs)
    assert any("without a quote" in e for e in errs)


def test_cited_source_must_be_listed():
    page = FM.replace("  - a.md\n", "  - other.md\n") + '[s1: "orchard sensor rollout ships on"]\n'
    assert any("not in frontmatter" in e for e in ck.check_citations("p.md", page, SRC))


def test_frontmatter_required_fields():
    assert ck.check_frontmatter("p.md", "no frontmatter") != []
    errs = ck.check_frontmatter("p.md", "---\ntitle: T\n---\nbody")
    assert {e.split("'")[1] for e in errs} == {"type", "sources", "last_updated"}
    assert ck.check_frontmatter("p.md", FM) == []


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


def test_no_source_cap():
    many = (
        "---\ntitle: T\ntype: x\nlast_updated: 2031-01-01\nsources:\n"
        + "".join(f"  - s{i}.md\n" for i in range(40))
        + "---\nbody\n"
    )
    assert not hasattr(ck, "check_source_cap")
    assert ck.run_page_checks(Path("."), [], Path("."), {}) == []
    assert ck.check_frontmatter("p.md", many) == []


def test_check_kind():
    assert ck.check_kind('p.md: quote not found verbatim in a.md: "x"') == "citations"
    assert ck.check_kind('p.md: quote under 5 words: [s1: "worked ok"]') == "short_quote"
    assert (
        ck.check_kind("p.md: lost 3/10 lines (30%) without a superseded marker") == "content_loss"
    )
    assert ck.check_kind("wrote outside the selected pages: lens.md") == "write_scope"


def test_current_state_section_is_outside_the_loss_guard():
    before = FM + "## Current state (as of 2031-03-01)\n" + "".join(f"s{i}\n" for i in range(9))
    before += "## Record\n- kept one\n"
    after = FM + "## Current state (as of 2031-03-09)\nall new\n## Record\n- kept one\n- new\n"
    assert ck.check_content_loss("p.md", before, after) == []
    # the record below it is still guarded, headings included
    lost = FM + "## Current state (as of 2031-03-09)\nx\n"
    assert any("record" in e for e in ck.check_content_loss("p.md", before, lost))
