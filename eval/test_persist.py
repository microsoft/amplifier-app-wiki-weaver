"""init.persist: git init if absent (idempotent), commit if staged, clean up
ephemeral .ai/ breadcrumbs -- AGENTS.md and lens/ are the durable record."""

from __future__ import annotations

import pytest

from wiki_weaver.init import persist
from wiki_weaver.lib import WikiRoot, ensure_dir, git_available


def _has_git() -> bool:
    import shutil

    return shutil.which("git") is not None


def test_git_init_is_idempotent(tmp_path):
    if not _has_git():
        pytest.skip("git not available")
    wr = WikiRoot(tmp_path)
    assert not git_available(wr.root)

    code = persist.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert git_available(wr.root)

    # Re-run against an already-git-initialized root -- must not raise.
    code2 = persist.main(["--wiki-root", str(wr.root)])
    assert code2 == 0


def test_commits_new_content_and_cleans_up_ephemeral_files(tmp_path):
    if not _has_git():
        pytest.skip("git not available")
    wr = WikiRoot(tmp_path)
    ensure_dir(wr.ai_dir)
    (wr.ai_dir / "interview-notes.md").write_text("## Round 1\n\nPurpose: runbooks.\n", encoding="utf-8")
    (wr.ai_dir / "draft-schema.md").write_text("# Draft schema\n", encoding="utf-8")
    (wr.ai_dir / "draft-persona.md").write_text("# Draft persona\n", encoding="utf-8")
    (wr.ai_dir / "init-verdict.txt").write_text("enough", encoding="utf-8")
    (wr.root / "AGENTS.md").write_text("# AGENTS\n\nSchema goes here.\n", encoding="utf-8")

    code = persist.main(["--wiki-root", str(wr.root)])

    assert code == 0
    assert (wr.root / "AGENTS.md").exists()  # durable product survives
    assert not (wr.ai_dir / "interview-notes.md").exists()
    assert not (wr.ai_dir / "draft-schema.md").exists()
    assert not (wr.ai_dir / "draft-persona.md").exists()
    assert not (wr.ai_dir / "init-verdict.txt").exists()


def test_no_phantom_commit_when_nothing_staged(tmp_path):
    if not _has_git():
        pytest.skip("git not available")
    wr = WikiRoot(tmp_path)

    code1 = persist.main(["--wiki-root", str(wr.root)])
    assert code1 == 0

    # Second run: nothing changed since the first commit -- must not crash
    # trying to commit an empty diff.
    code2 = persist.main(["--wiki-root", str(wr.root)])
    assert code2 == 0
