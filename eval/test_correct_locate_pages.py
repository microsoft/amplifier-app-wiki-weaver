"""wiki_weaver.correct.locate_pages: BM25 search for pages asserting the
corrected claim -- real matches only, never padded to a fixed count."""

from __future__ import annotations

import json

from wiki_weaver.correct import locate_pages


def test_finds_page_with_real_lexical_overlap(wiki_root):
    wiki_root.add_page(
        "migration-history.md",
        "---\ntitle: Migration History\ntype: article\n---\n"
        "Bob led the migration effort, coordinating between platform and infra teams.\n",
    )
    wiki_root.add_page(
        "unrelated-topic.md",
        "---\ntitle: Unrelated\ntype: article\n---\nThis page is about coffee brewing methods.\n",
    )
    claim_path = wiki_root.root / "correction-text.md"
    claim_path.write_text("Alice, not Bob, led the migration effort.", encoding="utf-8")
    out_path = wiki_root.root / ".ai" / "affected-pages.json"

    code = locate_pages.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--out", str(out_path)]
    )

    assert code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert "migration-history.md" in data["pages"]
    assert "unrelated-topic.md" not in data["pages"]


def test_no_matching_pages_returns_empty_list(wiki_root):
    wiki_root.add_page("coffee.md", "---\ntitle: Coffee\ntype: article\n---\nA guide to pour-over brewing methods.\n")
    claim_path = wiki_root.root / "correction-text.md"
    claim_path.write_text(
        "Never attribute a contribution to a named person without an explicit citation.", encoding="utf-8"
    )
    out_path = wiki_root.root / "affected-pages.json"

    code = locate_pages.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--out", str(out_path)]
    )

    assert code == 0
    data = json.loads(out_path.read_text(encoding="utf-8"))
    assert data["pages"] == []


def test_empty_wiki_returns_empty_list(wiki_root):
    claim_path = wiki_root.root / "correction-text.md"
    claim_path.write_text("Some claim about nothing on the wiki yet.", encoding="utf-8")
    out_path = wiki_root.root / "affected-pages.json"

    code = locate_pages.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--out", str(out_path)]
    )

    assert code == 0
    assert json.loads(out_path.read_text(encoding="utf-8"))["pages"] == []


def test_missing_claim_file_refuses(wiki_root, capsys):
    out_path = wiki_root.root / "affected-pages.json"

    code = locate_pages.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(wiki_root.root / "nope.md"), "--out", str(out_path)]
    )

    assert code == 1
    assert not out_path.exists()
    assert "does not exist" in capsys.readouterr().err


def test_empty_claim_file_refuses(wiki_root, capsys):
    claim_path = wiki_root.root / "correction-text.md"
    claim_path.write_text("   \n", encoding="utf-8")
    out_path = wiki_root.root / "affected-pages.json"

    code = locate_pages.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--out", str(out_path)]
    )

    assert code == 1
    assert not out_path.exists()
    assert "is empty" in capsys.readouterr().err
