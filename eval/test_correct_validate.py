"""wiki_weaver.correct.validate: delegates to wiki_weaver.ingest.validate's
exact structural-check contract (pipeline/CLI-CONTRACT.md: "Same
structural-check contract as ingest.validate")."""

from __future__ import annotations

from wiki_weaver.correct import validate


def test_delegates_to_ingest_validate_and_reports_clean_wiki(wiki_root):
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: article\n---\nHello.\n")
    out_path = wiki_root.root / ".ai" / "validation-report.md"

    code = validate.main(["--wiki-root", str(wiki_root.root), "--out", str(out_path)])

    assert code == 0
    assert "No structural issues found." in out_path.read_text(encoding="utf-8")


def test_reports_broken_link(wiki_root):
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: article\n---\nSee [[nonexistent-page]] for details.\n")
    out_path = wiki_root.root / "validation-report.md"

    code = validate.main(["--wiki-root", str(wiki_root.root), "--out", str(out_path)])

    assert code == 1
    assert "broken link" in out_path.read_text(encoding="utf-8")
