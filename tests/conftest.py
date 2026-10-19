"""Shared fixtures: a synthetic corpus (four invented meetings, default lens, git)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"

# The dot-runner engine is a separate `uv tool`; CI does not install it. `ingest`'s preflight
# only asks that it be on PATH, so without it a stub stands in (it refuses to run anything),
# and tests that really drive the engine skip on ENGINE, the real one or None.
ENGINE = shutil.which("dot-runner")
if ENGINE is None:
    import os
    import tempfile

    _stub = Path(tempfile.mkdtemp(prefix="ww-test-stub-")) / "dot-runner"
    _stub.write_text(
        "#!/bin/sh\necho 'stub dot-runner (tests): engine not installed' >&2\nexit 1\n"
    )
    _stub.chmod(0o755)
    os.environ["PATH"] = f"{_stub.parent}{os.pathsep}{os.environ.get('PATH', '')}"
os_environ_key = "WW_TEST_ENGINE"
__import__("os").environ[os_environ_key] = ENGINE or ""


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
