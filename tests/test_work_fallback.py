"""Scratch left in .wiki/work/ with no git history to recover against is cleared - only then.

Resolve's archive never carries .git, so every job starts a corpus with no history. If an
earlier job died, its .wiki/work/ scratch can arrive with nothing to recover against, and the
"scratch without a journal stops the run" rule would stop every job after it. When init (via
scaffold) creates .git from nothing, there is provably no history: clear the scratch. When
.git exists, the rule stands. No model calls: lens.md exists, so init stops after scaffold.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from test_result import ww


def _corpus(tmp_path: Path) -> Path:
    c = tmp_path / "w"
    (c / ".wiki/work").mkdir(parents=True)
    (c / "lens.md").write_text("## Purpose\nx\n")
    (c / ".wiki/work/x").write_text("scratch from a job that died\n")
    return c


def test_init_without_git_clears_orphan_scratch(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    r = ww("init", str(c), "--plain")
    assert r.returncode == 0, r.stderr
    assert (c / ".git").is_dir()
    assert not (c / ".wiki/work").exists() or not any((c / ".wiki/work").iterdir())
    assert "cleared .wiki/work/" in r.stderr
    assert len([ln for ln in r.stderr.splitlines() if ".wiki/work" in ln]) == 1


def test_init_with_git_still_refuses_orphan_scratch(tmp_path: Path) -> None:
    c = _corpus(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=c, check=True)
    r = ww("init", str(c), "--plain")
    assert r.returncode != 0
    assert (c / ".wiki/work/x").read_text() == "scratch from a job that died\n"


def test_ingest_without_git_clears_orphan_scratch(tmp_path: Path) -> None:
    import os

    c = _corpus(tmp_path)
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    r = ww("ingest", "--wiki", str(c), env=env)
    assert r.returncode == 3, r.stderr  # empty inbox, not "scratch from a run that died"
    assert not (c / ".wiki/work").exists() or not any((c / ".wiki/work").iterdir())
