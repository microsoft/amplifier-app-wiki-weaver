"""wiki_weaver.ingest.retrieve_slice --emit-strength -- arm B's routing signal.

Additive/default-OFF: arm A (ingest.dot) never passes this flag, so its
existing behavior (tests/test_retrieve_slice.py) must be completely
unaffected. These tests cover the NEW behavior only.
"""

from __future__ import annotations

import json

from wiki_weaver.ingest import retrieve_slice
from wiki_weaver.lib import WikiRoot, ensure_dir

NAV_ONLY_INDEX = """---
title: Index
type: index
---

# Index
"""

QUOKKA_HABITAT = """---
title: Quokka Habitat
type: concept
---

# Quokka Habitat

Quokkas live on Rottnest Island. See [[quokka-diet|Quokka Diet]].
"""

QUOKKA_DIET = """---
title: Quokka Diet
type: concept
---

# Quokka Diet

Quokkas eat native vegetation.
"""

UNRELATED_ASTRONOMY = """---
title: Unrelated Astronomy Notes
type: concept
---

# Unrelated Astronomy Notes

Neutron stars are extremely dense.
"""


def _set_current_source(wiki_root, source_id: str = "s1.txt") -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(source_id, encoding="utf-8")
    return wr


def test_default_off_stdout_still_empty_without_flag(wiki_root, capsys):
    """Arm A's contract: omitting --emit-strength must leave stdout exactly
    as before -- no JSON, nothing at all."""
    wiki_root.add_page("index.md", NAV_ONLY_INDEX)
    wiki_root.add_source("s1.txt", "kind: article\n\nSome content.\n")
    wr = _set_current_source(wiki_root)

    code = retrieve_slice.main(["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_emit_strength_weak_when_wiki_has_no_content_pages(wiki_root, capsys):
    """THE bug scenario this whole arm targets: a near-empty wiki (nav pages
    only) must classify as weak regardless of BM25 scoring, since there is
    nothing for the source to fold into."""
    wiki_root.add_page("index.md", NAV_ONLY_INDEX)
    wiki_root.add_source("s1.txt", "kind: article\n\nSome content about anything.\n")
    wr = _set_current_source(wiki_root)

    code = retrieve_slice.main(
        ["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical", "--emit-strength"]
    )
    assert code == 0
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    payload = json.loads(out)
    assert payload["slice_strength"] == "weak"
    assert payload["top_score"] == 0.0
    # CHANGED (byte-budget fix, see tests/test_retrieve_slice_budget.py): --k is
    # omitted here, so this now exercises the default byte-budgeted path, not
    # the deprecated fixed-count default. retrieve_slice.DEFAULT_K (6) no
    # longer applies when --k is omitted -- "slice_k" reports how many
    # candidates the budget/floor/ceiling logic ACTUALLY selected, which is
    # bounded by how many pages exist at all. This wiki has exactly one page
    # (the nav-only index.md), so exactly one candidate comes back.
    assert payload["slice_k"] == 1


def test_emit_strength_weak_when_zero_lexical_overlap(wiki_root, capsys):
    """Content pages exist, but none share any vocabulary with the source --
    BM25's top score is structurally 0.0 (see bm25.BM25.score()'s early
    continue on zero term-frequency), so this must classify as weak too."""
    wiki_root.add_page("index.md", NAV_ONLY_INDEX)
    wiki_root.add_page("astronomy-notes.md", UNRELATED_ASTRONOMY)
    wiki_root.add_source("s1.txt", "kind: article\n\nCompletely different subject matter, zero shared words.\n")
    wr = _set_current_source(wiki_root)

    code = retrieve_slice.main(
        ["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical", "--emit-strength", "--k", "1"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["slice_strength"] == "weak"
    assert payload["top_score"] == 0.0


def test_emit_strength_strong_when_bm25_hit_scores_positive(wiki_root, capsys):
    """A genuine lexical match (shares vocabulary with an existing page)
    must classify as strong -- weave should fold into the existing pages,
    not fork to create_page."""
    wiki_root.add_page("quokka-habitat.md", QUOKKA_HABITAT)
    wiki_root.add_page("quokka-diet.md", QUOKKA_DIET)
    wiki_root.add_page("index.md", NAV_ONLY_INDEX)
    wiki_root.add_source("s1.txt", "kind: article\n\nQuokka habitat and diet on Rottnest Island.\n")
    wr = _set_current_source(wiki_root)

    code = retrieve_slice.main(
        ["--wiki-root", str(wr.root), "--strategy", "index+link-graph+lexical", "--emit-strength", "--k", "1"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["slice_strength"] == "strong"
    assert payload["top_score"] > 0.0
