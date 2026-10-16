"""What a run says about itself: its version, its own run directory, a valid --limit.

No model calls: every ingest here has an empty inbox (exit 3) or is rejected at parse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from test_result import _wiki, ww

ENV = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}


def _results(w: Path) -> list[Path]:
    return sorted((w / ".wiki/runs").glob("ingest-*/result.json"))


# item 4 ------------------------------------------------------------------------------


def test_result_names_the_wiki_weaver_version(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    assert ww("ingest", "--wiki", str(w), env=ENV).returncode == 3
    version = ww("--version").stdout.strip().removeprefix("wiki-weaver ")
    data = json.loads(_results(w)[0].read_text())
    assert data["wiki_weaver_version"] == version


def test_preflight_failure_result_also_names_the_version(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    (w / "lens.md").unlink()
    assert ww("ingest", "--wiki", str(w), env=ENV).returncode == 1
    assert json.loads(_results(w)[0].read_text())["wiki_weaver_version"]
