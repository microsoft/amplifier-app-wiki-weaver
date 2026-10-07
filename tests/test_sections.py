"""B-lean 4: the heading rule per page type, read from the lens. No model calls."""

from __future__ import annotations

from wiki_weaver import checks as ck
from wiki_weaver.lens import parse_page_types, type_key

LENS = """# Lens

## Page types

- **initiatives:** one page per initiative.
  Current state covers: where it stands, who owns what.
  Sections: Owners · Commitments · Blockers · Decisions · Open questions
- **weekly commitments and blockers:** what was committed each week.
  Sections pattern: Week of YYYY-MM-DD
- **decisions:** what was decided.
  Current state: none
- **topics:** cross-cutting subjects.

## Owner
x
"""
T = parse_page_types(LENS)


def fm(t: str) -> str:
    return f"---\ntitle: X\ntype: {t}\nsources: [a.md]\nlast_updated: 2031-03-02\n---\n# X\n"


def test_lens_parse_and_type_matching():
    assert T[type_key("initiative")].sections[0] == "Owners"
    assert T[type_key("weekly-commitments")].pattern == "Week of YYYY-MM-DD"
    assert T[type_key("decision")].current_state is False
    assert not T[type_key("topic")].heading_rule()


def test_added_heading_must_be_in_the_set():
    before = fm("initiative") + "## Current state (as of 2031-03-01)\nx\n## Owners\n- a\n"
    ok = before + "## Blockers\n- b\n"
    assert ck.check_section_headings("i.md", before, ok, T) == []
    bad = before + "## Sync of 2031-03-02\n- b\n"
    assert any(
        "not a section the lens names" in e
        for e in ck.check_section_headings("i.md", before, bad, T)
    )
    # an existing off-set heading is not re-judged; only added ones are
    legacy = fm("initiative") + "## Old meeting notes\nx\n"
    assert ck.check_section_headings("i.md", legacy, legacy + "more\n", T) == []
    # a new page: every ## is new
    assert ck.check_section_headings("i.md", None, bad, T)


def test_pattern_type_and_exemptions():
    weekly = (
        fm("weekly-commitments")
        + "## Current state (as of 2031-03-09)\nx\n## Week of 2031-03-02\n- a\n"
    )
    assert ck.check_section_headings("w.md", None, weekly, T) == []
    assert ck.check_section_headings("w.md", None, weekly + "## Week of March\n", T)
    anything = fm("topic") + "## Whatever the writer likes\nx\n"
    assert ck.check_section_headings("t.md", None, anything, T) == []  # no set: no rule
    src = fm("initiative") + "## Meeting of 2031-03-02\nx\n"
    assert ck.check_section_headings("source-a.md", None, src, T) == []  # source exempt


def test_decisions_opt_out_of_current_state():
    d = fm("decision") + "## Current state (as of 2031-03-02)\nx\n"
    assert any("has no Current state" in e for e in ck.check_section_headings("d.md", None, d, T))


def test_current_state_rules_skip_source_pages():
    bad = fm("source") + "## Notes\nx\n## Current state, as Wren described it\ny\n"
    assert ck.check_current_state("source-a.md", bad) == []
    assert ck.check_current_state("a.md", bad)
