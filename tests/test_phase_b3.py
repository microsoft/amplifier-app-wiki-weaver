"""Before E02: transcript header dates and source-page links. No model calls."""

from __future__ import annotations

from pathlib import Path

from wiki_weaver import checks as ck


def test_transcript_header_date_m_d_yyyy_becomes_iso(tmp_path: Path):
    from wiki_weaver.sources import source_meta

    t = tmp_path / "Orchard Sync recording 2031-03.md"
    t.write_text("# Transcript: Orchard Sync\n\nDate: 3/7/2031, 3:45:31 PM\nSpeakers: Wren\n")
    assert source_meta(t)["date"] == "2031-03-07"
    t.write_text("# Transcript: Orchard Sync\n\nDate: 12/15/2031, 9:05:00 AM\n")
    assert source_meta(t)["date"] == "2031-12-15"
    # ISO still wins as written; dates now sort as dates
    t.write_text("Date: 2031-03-02\n")
    assert source_meta(t)["date"] == "2031-03-02"
    assert max(["2031-03-07", "2031-12-15", "2031-03-02"]) == "2031-12-15"


FM = "---\ntitle: T\ntype: topics\nsources: [a.md]\nlast_updated: 2031-03-02\n---\n"


def test_a_link_to_a_source_page_fails_the_citation_check():
    src = {1: ("a.md", "Wren Talbot: The orchard sensor rollout ships on Friday the seventh.")}
    ok = FM + 'claim [s1: "The orchard sensor rollout ships on Friday"]\n'
    assert ck.check_citations("p.md", ok, src) == []
    bad = ok + "See the [Source summary](source-orchard-sync.md).\n"
    errs = ck.check_citations("p.md", bad, src)
    assert any("links to a source page" in e for e in errs), errs
    assert ck.check_kind(errs[0]) == "source_link"
    assert ck.check_citations("p.md", ok + "[the topic](orchard-rollout.md)\n", src) == []
