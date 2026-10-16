"""What a run says about itself: its version, its own run directory, a valid --limit.

No model calls: every ingest here has an empty inbox (exit 3) or is rejected at parse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_result import _wiki, ww

from wiki_weaver import cli

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


# item 5 ------------------------------------------------------------------------------


def _ingest_in_process(w: Path) -> int:
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(w)])
    return a.func(a)


def test_two_runs_in_the_same_second_each_write_their_own_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = _wiki(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ENV["ANTHROPIC_API_KEY"])
    monkeypatch.setattr(cli, "stamp", lambda: "20990101T000000Z")  # one frozen second
    assert _ingest_in_process(w) == 3
    assert _ingest_in_process(w) == 3
    results = _results(w)
    assert len(results) == 2, results
    assert all(json.loads(p.read_text())["status"] == "final" for p in results)


def test_back_to_back_ingests_both_write_result(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    assert ww("ingest", "--wiki", str(w), env=ENV).returncode == 3
    assert ww("ingest", "--wiki", str(w), env=ENV).returncode == 3
    assert len(_results(w)) == 2


def test_run_ids_sort_in_start_order(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    for _ in range(3):
        _ingest_in_process(w)
    dirs = [p.parent for p in _results(w)]
    started = [json.loads((d / "result.json").read_text())["started"] for d in dirs]
    assert started == sorted(started) and len({d.name for d in dirs}) == 3
