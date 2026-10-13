"""Shared fixtures: a synthetic corpus (four invented meetings, default lens, git)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    from wiki_weaver.cli import scaffold

    c = tmp_path / "wiki"
    c.mkdir()
    scaffold(c)
    shutil.copy2(
        Path(__file__).parent.parent / "wiki_weaver" / "data" / "default_lens.md", c / "lens.md"
    )
    for f in FIX.glob("*.md"):
        shutil.copy2(f, c / "_inbox" / f.name)
    subprocess.run(["git", "add", "-A"], cwd=c, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "lens"],
        cwd=c,
        check=True,
    )
    return c
