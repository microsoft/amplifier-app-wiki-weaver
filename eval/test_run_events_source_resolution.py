# pyright: reportMissingImports=false
"""Part-A tests: hook-run-events module-source resolution (packaging bug fix).

THE BUG THIS PROVES FIXED: PR #39 hardcoded the run-events sink's module
source as ``WIKI_WEAVER_ROOT / "modules" / "hook-run-events"`` -- a local
path that only exists in a DEV CHECKOUT. When wiki-weaver is installed as a
package, WIKI_WEAVER_ROOT resolves to site-packages/ and the path does not
exist, so foundation's activator logged "Failed to activate hook-run-events:
File not found" and the events.jsonl sink was silently dead for every
installed consumer.

THE FIX: resolve dynamically -- local path when it exists (dev checkouts and
these tests stay offline and unchanged), else the canonical git URL (same
pattern as CI_HOOK_SOURCE).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# engine_runner pulls in the attractor engine deps at import time. Skip
# cleanly in lightweight CI -- same convention as eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402


def test_local_path_used_when_present(monkeypatch, tmp_path: Path) -> None:
    """Dev-checkout layout: the local module dir exists -> local path wins."""
    local = tmp_path / "modules" / "hook-run-events"
    local.mkdir(parents=True)
    monkeypatch.setattr(er, "_RUN_EVENTS_HOOK_LOCAL", local)

    assert er.resolve_run_events_hook_source() == str(local)


def test_git_url_used_when_local_absent(monkeypatch, tmp_path: Path) -> None:
    """Installed-package layout: site-packages/modules/hook-run-events does
    not exist -> the canonical git URL (CI_HOOK_SOURCE pattern) is used."""
    monkeypatch.setattr(
        er, "_RUN_EVENTS_HOOK_LOCAL", tmp_path / "does-not-exist" / "hook-run-events"
    )

    source = er.resolve_run_events_hook_source()
    assert source == er.RUN_EVENTS_HOOK_GIT_SOURCE
    assert source.startswith(
        "git+https://github.com/microsoft/amplifier-app-wiki-weaver"
    )
    assert source.endswith("#subdirectory=modules/hook-run-events")


def test_this_repo_resolves_to_its_own_local_module() -> None:
    """Sanity: in THIS dev checkout the real module dir exists and is chosen
    (keeps existing offline test behavior byte-identical)."""
    source = er.resolve_run_events_hook_source()
    assert source == str(er._RUN_EVENTS_HOOK_LOCAL)
    assert Path(source).is_dir()


def test_ci_overlay_uses_dynamic_resolution(monkeypatch, tmp_path: Path) -> None:
    """_ci_overlay() must consume the DYNAMIC resolution (call-time), so an
    installed package composes the git URL, not a dead site-packages path."""
    monkeypatch.setattr(
        er, "_RUN_EVENTS_HOOK_LOCAL", tmp_path / "absent" / "hook-run-events"
    )

    overlay = er._ci_overlay(tmp_path / "logs")

    run_events = [h for h in overlay.hooks if h["module"] == "hook-run-events"]
    assert len(run_events) == 1
    assert run_events[0]["source"] == er.RUN_EVENTS_HOOK_GIT_SOURCE
    # The CI hook entry is untouched by Part A.
    ci = [h for h in overlay.hooks if h["module"] == "hook-context-intelligence"]
    assert len(ci) == 1
    assert ci[0]["source"] == er.CI_HOOK_SOURCE


def test_missing_events_sink_warns_loudly(tmp_path: Path, capsys) -> None:
    """Fail-soft contract: a dead sink (no events.jsonl after a run) must
    produce a clearly visible WARNING, never silence."""
    er._warn_if_events_sink_missing(tmp_path)
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "hook-run-events" in out
    assert "events.jsonl" in str(out)


def test_present_events_sink_stays_quiet(tmp_path: Path, capsys) -> None:
    (tmp_path / "events.jsonl").write_text('{"event": "x"}\n', encoding="utf-8")
    er._warn_if_events_sink_missing(tmp_path)
    assert capsys.readouterr().out == ""
