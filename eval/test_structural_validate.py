"""lint.structural_validate: reuses lib.find_structural_issues (the SAME
check ingest.validate uses) and prints the routing sentinel directly on
stdout -- lint.dot has no shell wrapper around this call."""

from __future__ import annotations

from wiki_weaver.lint import structural_validate
from wiki_weaver.lib import WikiRoot

CLEAN_PAGE = """---
title: Index
type: index
---

# Index

- [[other-page|Other Page]]
"""

OTHER_PAGE = """---
title: Other Page
type: concept
---

# Other Page

Links back to [[index|Index]].
"""

BROKEN_LINK_PAGE = """---
title: Broken
type: concept
---

# Broken

See [[does-not-exist|Nowhere]].
"""


def test_clean_wiki_prints_structural_ok(wiki_root, capsys):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)

    code = structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "structural_ok"
    report = (wr.root / ".ai" / "structural-report.md").read_text(encoding="utf-8")
    assert "No structural issues found." in report


def test_broken_link_prints_structural_bad(wiki_root, capsys):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.add_page("broken.md", BROKEN_LINK_PAGE)
    wr = WikiRoot(wiki_root.root)

    code = structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])

    assert code == 0  # sentinel carries the routing signal, not the exit code
    assert capsys.readouterr().out.strip().splitlines()[-1] == "structural_bad"


def test_empty_wiki_is_structural_ok(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "structural_ok"


ANCHOR_LINK_PAGE = """---
title: Broken
type: concept
---

# Broken

See [[other-page#Some Section|Some Section]].
"""

# Index variant that also links to the anchor-link page, so this test's own
# construction can't be confused with the orphan check firing for an
# unrelated reason (page.links must be non-empty incoming for every
# non-nav page or find_structural_issues reports "orphan" independently of
# the anchor-stripping bug under test here).
INDEX_WITH_ANCHOR_LINK_REF = """---
title: Index
type: index
---

# Index

- [[other-page|Other Page]]
- [[broken|Broken]]
"""


def test_section_anchored_link_to_real_page_is_not_a_broken_link(wiki_root, capsys):
    """KNOWN_ISSUES.md #3's root cause: find_structural_issues() appended
    '.md' to the wikilink slug WITHOUT stripping a '#Section' fragment, so
    a perfectly valid section-anchored link to an EXISTING page was
    reported as broken forever (the target page 'other-page#Some
    Section.md' can never exist). One such stale link in the wiki
    bootstrap made validate() return exit 1 for every source in an entire
    73-source production run, regardless of what any given weave actually
    wrote -- which is what actually drove the segment-2+ quarantine skew
    (segment 1: 0/73 quarantines; segment 2+: 14 quarantines vs 4
    accepts), not a segment-content difference.

    Real-world example from the incident: [[amplifier-as-agent#Preset
    Concept|Preset Concept]] -- the page exists, the anchor does not
    change that."""
    wiki_root.add_page("index.md", INDEX_WITH_ANCHOR_LINK_REF)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.add_page("broken.md", ANCHOR_LINK_PAGE)
    wr = WikiRoot(wiki_root.root)

    code = structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])

    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "structural_ok"
    report = (wr.root / ".ai" / "structural-report.md").read_text(encoding="utf-8")
    assert "No structural issues found." in report
