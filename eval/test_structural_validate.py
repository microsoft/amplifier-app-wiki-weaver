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
