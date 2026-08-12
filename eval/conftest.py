"""Shared test fixtures: a minimal on-disk wiki-root builder."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

GIT_AVAILABLE = shutil.which("git") is not None


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


class WikiRootBuilder:
    """Builds a temp wiki-root directory with sources/, wiki/, and (optionally) git history."""

    def __init__(self, root: Path):
        self.root = root
        (root / "sources").mkdir(parents=True, exist_ok=True)
        (root / "wiki").mkdir(parents=True, exist_ok=True)

    def add_source(self, name: str, content: str = "Sample source content.\n") -> Path:
        path = self.root / "sources" / name
        path.write_text(content, encoding="utf-8")
        return path

    def add_page(self, name: str, content: str) -> Path:
        path = self.root / "wiki" / name
        path.write_text(content, encoding="utf-8")
        return path

    def init_git(self) -> None:
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "test@example.com")
        _git(self.root, "config", "user.name", "Test")

    def commit_all(self, message: str = "snapshot") -> None:
        _git(self.root, "add", "-A")
        result = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--staged", "--quiet"],
            capture_output=True,
        )
        if result.returncode != 0:
            _git(self.root, "commit", "-q", "-m", message)


@pytest.fixture
def wiki_root(tmp_path: Path) -> WikiRootBuilder:
    return WikiRootBuilder(tmp_path)


@pytest.fixture
def git_available() -> bool:
    return GIT_AVAILABLE
