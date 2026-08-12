"""retrieve_slice: BM25 top-k UNION link-graph neighbors UNION nav pages,
written deterministically to .ai/current_slice.json."""

from __future__ import annotations

import json

from wiki_weaver.ingest import retrieve_slice
from wiki_weaver.lib import WikiRoot, ensure_dir

PAGE_A = """---
title: Quokka Habitat
type: concept
---

# Quokka Habitat

Quokkas live on Rottnest Island. See [[quokka-diet|Quokka Diet]].
"""

PAGE_B = """---
title: Quokka Diet
type: concept
---

# Quokka Diet

Quokkas eat native vegetation.
"""

PAGE_C = """---
title: Unrelated Astronomy Notes
type: concept
---

# Unrelated Astronomy Notes

Neutron stars are extremely dense.
"""

INDEX_PAGE = """---
title: Index
type: index
---

# Index
"""


def _seed(wiki_root) -> WikiRoot:
    wiki_root.add_page("quokka-habitat.md", PAGE_A)
    wiki_root.add_page("quokka-diet.md", PAGE_B)
    wiki_root.add_page("astronomy-notes.md", PAGE_C)
    wiki_root.add_page("index.md", INDEX_PAGE)
    wiki_root.add_source("s1.txt", "kind: article\n\nQuokka habitat and diet on Rottnest Island.\n")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    return wr


def test_slice_includes_bm25_hit_link_neighbor_and_nav_page(wiki_root):
    wr = _seed(wiki_root)
    code = retrieve_slice.main(["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical", "--k", "1"])
    assert code == 0

    data = json.loads(wr.current_slice_file.read_text(encoding="utf-8"))
    assert data["source_id"] == "s1.txt"
    # Pages are rendered wiki/-relative (the write-path bug fix, see
    # lib.to_wiki_relpath) -- never a bare filename weave's file tools
    # could misresolve against a cwd rooted at wiki_root.
    # Top BM25 hit should be quokka-habitat.md (matches query terms directly).
    assert data["pages"][0] == "wiki/quokka-habitat.md"
    # Its link-graph neighbor (quokka-diet.md) must be pulled in even though
    # k=1 would otherwise exclude it.
    assert "wiki/quokka-diet.md" in data["pages"]
    # Nav page always included.
    assert "wiki/index.md" in data["pages"]
    # The unrelated astronomy page should NOT be pulled in by BM25 or the link graph.
    assert "wiki/astronomy-notes.md" not in data["pages"]


def test_missing_current_source_fails_with_message(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = retrieve_slice.main(["--wiki-root", str(wr.root)])
    assert code == 1
    assert "select_source must run first" in capsys.readouterr().err


def test_stdout_has_no_content_only_stderr(wiki_root, capsys):
    wr = _seed(wiki_root)
    retrieve_slice.main(["--wiki-root", str(wr.root)])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""
