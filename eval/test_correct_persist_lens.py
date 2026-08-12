"""wiki_weaver.correct.persist_lens: THE load-bearing step -- writes
lens/corrections/<id>.md, idempotently, with scope decided from
locate_pages's own output, then commits wiki + lens together."""

from __future__ import annotations

import json

from wiki_weaver.correct import persist_lens


def _write_claim(wiki_root, text: str):
    path = wiki_root.root / "correction-text.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_pages(wiki_root, pages: list[str]):
    path = wiki_root.root / "affected-pages.json"
    path.write_text(json.dumps({"pages": pages}), encoding="utf-8")
    return path


def test_persists_page_scoped_correction(wiki_root):
    claim_path = _write_claim(wiki_root, "Alice, not Bob, led the migration.")
    pages_path = _write_pages(wiki_root, ["migration-history.md"])

    code = persist_lens.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]
    )

    assert code == 0
    corrections_dir = wiki_root.root / "lens" / "corrections"
    files = list(corrections_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "scope: page" in content
    assert "migration-history.md" in content
    assert "Alice, not Bob, led the migration." in content


def test_persists_general_scoped_correction_when_no_pages_found(wiki_root):
    claim_path = _write_claim(
        wiki_root, "Never attribute a contribution to a named person without an explicit citation."
    )
    pages_path = _write_pages(wiki_root, [])

    code = persist_lens.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]
    )

    assert code == 0
    files = list((wiki_root.root / "lens" / "corrections").glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "scope: general" in content
    assert "pages: []" in content


def test_idempotent_on_rerun_same_claim(wiki_root):
    claim_path = _write_claim(wiki_root, "Alice, not Bob, led the migration.")
    pages_path = _write_pages(wiki_root, ["migration-history.md"])
    args = ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]

    assert persist_lens.main(args) == 0
    files_after_first = list((wiki_root.root / "lens" / "corrections").glob("*.md"))
    assert persist_lens.main(args) == 0
    files_after_second = list((wiki_root.root / "lens" / "corrections").glob("*.md"))

    assert len(files_after_first) == 1
    assert files_after_first == files_after_second


def test_same_claim_text_maps_to_same_correction_id(wiki_root):
    claim_path = _write_claim(wiki_root, "Alice, not Bob, led the migration.")
    pages_path = _write_pages(wiki_root, ["migration-history.md"])
    persist_lens.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]
    )
    first_id = next((wiki_root.root / "lens" / "corrections").glob("*.md")).stem

    assert first_id == persist_lens.correction_id("Alice, not Bob, led the migration.")


def test_different_claims_get_different_ids(wiki_root):
    id_a = persist_lens.correction_id("Alice led the migration.")
    id_b = persist_lens.correction_id("Bob led the migration.")
    assert id_a != id_b


def test_rejects_missing_claim_file(wiki_root, capsys):
    pages_path = _write_pages(wiki_root, [])
    code = persist_lens.main(
        [
            "--wiki-root",
            str(wiki_root.root),
            "--claim-file",
            str(wiki_root.root / "nope.md"),
            "--pages-file",
            str(pages_path),
        ]
    )
    assert code == 1
    assert not (wiki_root.root / "lens" / "corrections").exists()
    assert "does not exist" in capsys.readouterr().err


def test_rejects_auto_approved_stub(wiki_root, capsys):
    claim_path = _write_claim(wiki_root, "auto-approved")
    pages_path = _write_pages(wiki_root, [])

    code = persist_lens.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]
    )

    assert code == 1
    assert not (wiki_root.root / "lens" / "corrections").exists()
    assert "auto-approve stub" in capsys.readouterr().err


def test_commits_wiki_and_lens_together(wiki_root, git_available):
    if not git_available:
        return
    claim_path = _write_claim(wiki_root, "Alice, not Bob, led the migration.")
    pages_path = _write_pages(wiki_root, ["migration-history.md"])
    wiki_root.init_git()

    code = persist_lens.main(
        ["--wiki-root", str(wiki_root.root), "--claim-file", str(claim_path), "--pages-file", str(pages_path)]
    )

    assert code == 0
    import subprocess

    status = subprocess.run(
        ["git", "-C", str(wiki_root.root), "status", "--porcelain"], capture_output=True, text=True, check=True
    )
    assert status.stdout.strip() == ""  # everything staged and committed, nothing left dirty
