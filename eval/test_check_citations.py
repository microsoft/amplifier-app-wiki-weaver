"""wiki_weaver.synthesize.check_citations -- Gate B: every attributed source
must actually be cited in the page body, not merely listed in frontmatter.

The real-audit calibration case: `empirical-verification-before-shipping.md`
carried source 027 in its frontmatter `sources:` list (counted in its
advertised source_count) while the page BODY never mentioned it -- the
attribution was valid, but the prose silently dropped the citation.
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import check_citations


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def _seed_gap(wr: WikiRoot, term: str) -> None:
    wr.current_gap_file.write_text(json.dumps({"term": term}), encoding="utf-8")


# ---------------------------------------------------------------------------
# uncited_sources -- pure function
# ---------------------------------------------------------------------------


def test_uncited_sources_finds_the_audited_027_case():
    """The real defect, reproduced directly: 027 is attributed but the body
    text never mentions it (only 008 and 040 are actually cited in prose)."""
    body = (
        "The sources agree that empirical verification matters. "
        "Source `008-home-bkrabach-dev-medium-tools-wiki-use.md` shows this. "
        "Source `040-medium-tools-wiki-please-review-the-late.md` shows a second case."
    )
    frontmatter_sources = [
        "008-home-bkrabach-dev-medium-tools-wiki-use.md",
        "027-fable-whatsapp-use-the-load-skill-tool-t.md",
        "040-medium-tools-wiki-please-review-the-late.md",
    ]

    missing = check_citations.uncited_sources(frontmatter_sources, body)

    assert missing == ["027-fable-whatsapp-use-the-load-skill-tool-t.md"]


def test_uncited_sources_empty_when_everything_cited():
    body = "Source `008-x.md` and source `010-y.md` both agree on this point."
    frontmatter_sources = ["008-x.md", "010-y.md"]

    assert check_citations.uncited_sources(frontmatter_sources, body) == []


def test_uncited_sources_works_for_non_nnn_prefixed_ids():
    """Must not silently depend on the NNN-Name.md convention -- a literal
    substring match still catches a plain test-fixture-style id."""
    body = "s1.txt agrees with the claim; s2.txt does not mention it."
    frontmatter_sources = ["s1.txt", "s2.txt"]

    assert check_citations.uncited_sources(frontmatter_sources, body) == []
    assert check_citations.uncited_sources(["s1.txt", "s3.txt"], body) == ["s3.txt"]


# ---------------------------------------------------------------------------
# main() -- full CLI wiring
# ---------------------------------------------------------------------------


def test_main_routes_citations_bad_on_the_audited_027_case(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_gap(wr, "empirical verification before shipping")
    page_text = (
        "---\n"
        'title: "Empirical Verification Before Shipping"\n'
        "type: theme\n"
        "sources: [008-home-bkrabach-dev-medium-tools-wiki-use.md, "
        "027-fable-whatsapp-use-the-load-skill-tool-t.md, "
        "040-medium-tools-wiki-please-review-the-late.md]\n"
        "synthesized: true\n"
        "---\n\n"
        "# Empirical Verification Before Shipping\n\n"
        "Source `008-home-bkrabach-dev-medium-tools-wiki-use.md` demonstrates this discipline. "
        "Source `040-medium-tools-wiki-please-review-the-late.md` shows the same pattern.\n"
    )
    wr.wiki_dir.mkdir(parents=True, exist_ok=True)
    (wr.wiki_dir / "empirical-verification-before-shipping.md").write_text(page_text, encoding="utf-8")

    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "citations_bad"


def test_main_routes_citations_ok_when_every_source_is_cited(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_gap(wr, "token economics")
    page_text = (
        "---\n"
        'title: "Token Economics"\n'
        "type: theme\n"
        "sources: [s1.txt, s2.txt]\n"
        "synthesized: true\n"
        "---\n\n"
        "# Token Economics\n\n"
        "s1.txt and s2.txt both agree tokens cost real money.\n"
    )
    wr.wiki_dir.mkdir(parents=True, exist_ok=True)
    (wr.wiki_dir / "token-economics.md").write_text(page_text, encoding="utf-8")

    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "citations_ok"


def test_main_frontmatter_sources_line_itself_does_not_count_as_a_citation(wiki_root, capsys):
    """Regression guard for the exact defect: the frontmatter sources: list
    must be excluded from the body check (lib.split_header), or the
    uncited-source defect would never be detectable at all."""
    wr = _wr(wiki_root)
    _seed_gap(wr, "empirical verification before shipping")
    page_text = (
        "---\n"
        'title: "Empirical Verification Before Shipping"\n'
        "type: theme\n"
        "sources: [027-fable-whatsapp-use-the-load-skill-tool-t.md]\n"
        "synthesized: true\n"
        "---\n\n"
        "# Empirical Verification Before Shipping\n\n"
        "This body never mentions any source by id.\n"
    )
    wr.wiki_dir.mkdir(parents=True, exist_ok=True)
    (wr.wiki_dir / "empirical-verification-before-shipping.md").write_text(page_text, encoding="utf-8")

    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == "citations_bad"


def test_main_missing_gap_file_is_citations_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "citations_bad"


def test_main_missing_page_is_citations_bad(wiki_root, capsys):
    wr = _wr(wiki_root)
    _seed_gap(wr, "never written")
    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "citations_bad"


def test_main_no_frontmatter_sources_is_ok_not_bad(wiki_root, capsys):
    """Nothing attributed -- nothing to be uncited. Distinct from the
    malformed-input cases above (missing gap/page files), which are
    genuinely bad."""
    wr = _wr(wiki_root)
    _seed_gap(wr, "empty sources page")
    wr.wiki_dir.mkdir(parents=True, exist_ok=True)
    page_text = '---\ntitle: "Empty Sources Page"\ntype: theme\nsources: []\n---\n\nBody with no sources.\n'
    (wr.wiki_dir / "empty-sources-page.md").write_text(page_text, encoding="utf-8")

    code = check_citations.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "citations_ok"
