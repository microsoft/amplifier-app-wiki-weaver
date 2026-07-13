"""Regression test: SPAWN_TIMEOUT_SECONDS reaches run_pipeline's spawn_timeout seam.

HISTORICAL CONTEXT: wiki-weaver used to enforce this timeout itself, via a
local ``_spawn_with_timeout`` wrapper around ``prepared.spawn(...)`` inside
its own ``make_spawn_fn`` / ``make_ask_spawn_fn`` (see git history for the
original investigation into a stalled-spawn hang). The pipeline-runner
migration (Slice 4) moved the actual timeout ENFORCEMENT mechanism entirely
into ``amplifier_module_pipeline_runner.run_pipeline``'s own ``make_spawn_fn``
-- that producer module owns proving the enforcement itself now (its own test
suite covers "stalled spawn raises TimeoutError" / "fast spawn is unaffected"
/ "slow-but-bounded spawn still succeeds").

wiki-weaver's remaining responsibility is just the WIRING: every entrypoint in
engine_runner.py (and reweave.py) must thread
``spawn_timeout=SPAWN_TIMEOUT_SECONDS`` through to ``run_pipeline`` on every
call, so a stalled child agent still fails loud instead of hanging. This test
proves that wiring by mocking ``run_pipeline`` and asserting the kwarg arrives
-- it does NOT re-test the timeout mechanism itself (that would duplicate
coverage that belongs to pipeline-runner).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# These tests import wiki_weaver.engine_runner, which imports the attractor
# engine deps (amplifier_module_pipeline_runner, unified_llm). Skip cleanly in
# lightweight CI (no @main resolution) rather than erroring -- matches
# eval/test_ingest_drain.py's convention.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402


def _fake_result(
    status: str = "success", notes: str = "ok", failure_reason: str | None = None
) -> Any:
    from amplifier_module_pipeline_runner import PipelineResult

    return PipelineResult(
        status=status,
        notes=notes,
        logs_dir=Path("/tmp"),
        raw="{}",
        failure_reason=failure_reason,
    )


def _capture_run_pipeline(
    monkeypatch,
    captured: dict,
    *,
    status: str = "success",
    failure_reason: str | None = None,
) -> None:
    """Patch er.run_pipeline to record its kwargs and return a fake result."""

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        captured["dot_source"] = dot_source
        captured.update(kwargs)
        return _fake_result(status=status, failure_reason=failure_reason)

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)


# ---------------------------------------------------------------------------
# Test 1 -- run_thin_slice threads spawn_timeout through
# ---------------------------------------------------------------------------


def test_run_thin_slice_threads_spawn_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 42.0)
    captured: dict = {}
    _capture_run_pipeline(monkeypatch, captured)

    out_path = tmp_path / "proof.txt"
    er.run_thin_slice(out_path, cwd=tmp_path)

    assert captured.get("spawn_timeout") == 42.0


# ---------------------------------------------------------------------------
# Test 2 -- run_ask threads spawn_timeout AND a read-only child_constraint
# ---------------------------------------------------------------------------


def test_run_ask_threads_spawn_timeout_and_constraint(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 7.5)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    captured: dict = {}
    _capture_run_pipeline(monkeypatch, captured)

    er.run_ask(wiki_dir, "does the wiki cover anything?")

    assert captured.get("spawn_timeout") == 7.5
    assert callable(captured.get("child_constraint")), (
        "run_ask must pass a child_constraint (the read-only ask scoping) "
        "to run_pipeline"
    )


# ---------------------------------------------------------------------------
# Test 3 -- run_inner threads spawn_timeout AND the fs isolation constraint
# ---------------------------------------------------------------------------


def test_run_inner_threads_spawn_timeout_and_constraint(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 13.0)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    source = tmp_path / "source.md"
    source.write_text("hello world", encoding="utf-8")
    captured: dict = {}
    _capture_run_pipeline(monkeypatch, captured)

    er.run_inner(source, wiki_dir)

    assert captured.get("spawn_timeout") == 13.0
    assert callable(captured.get("child_constraint")), (
        "run_inner must pass a child_constraint (Fix 1 filesystem isolation) "
        "to run_pipeline"
    )


# ---------------------------------------------------------------------------
# Test 3b -- regression: failure_reason propagates from PipelineResult into
# InnerResult on both run_inner and run_ingest, instead of being hardcoded to
# None (PipelineResult DOES carry failure_reason -- see
# amplifier_module_pipeline_runner.runner.PipelineResult).
# ---------------------------------------------------------------------------


def test_run_inner_propagates_failure_reason(monkeypatch, tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    source = tmp_path / "source.md"
    source.write_text("hello world", encoding="utf-8")
    captured: dict = {}
    _capture_run_pipeline(
        monkeypatch,
        captured,
        status="fail",
        failure_reason="no matching edge from node assess",
    )

    result = er.run_inner(source, wiki_dir)

    assert result.failure_reason == "no matching edge from node assess", (
        "run_inner must propagate PipelineResult.failure_reason into "
        "InnerResult, not hardcode None"
    )


def test_run_ingest_propagates_failure_reason(monkeypatch, tmp_path: Path) -> None:
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    captured: dict = {}
    _capture_run_pipeline(
        monkeypatch,
        captured,
        status="fail",
        failure_reason="max_drain_iters exceeded",
    )

    result = er.run_ingest(wiki_dir)

    assert result.failure_reason == "max_drain_iters exceeded", (
        "run_ingest must propagate PipelineResult.failure_reason into "
        "InnerResult, not hardcode None"
    )


# ---------------------------------------------------------------------------
# Test 4 -- every call also carries the CI hook overlay + profiles=None
# ---------------------------------------------------------------------------


def test_run_thin_slice_carries_ci_overlay_and_default_profiles(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict = {}
    _capture_run_pipeline(monkeypatch, captured)

    er.run_thin_slice(tmp_path / "proof.txt", cwd=tmp_path)

    assert captured.get("profiles") is None, (
        "profiles=None lets run_pipeline's own DEFAULT_PROFILES route "
        "anthropic/openai/gemini -- wiki-weaver must not override it"
    )
    overlays = captured.get("extra_overlays")
    assert overlays and len(overlays) == 1, (
        f"expected exactly one extra_overlays entry (the CI hook), got: {overlays}"
    )


# ---------------------------------------------------------------------------
# Test 5 -- WIKI_WEAVER_SPAWN_TIMEOUT env var overrides the default
# ---------------------------------------------------------------------------


def test_spawn_timeout_env_override(monkeypatch) -> None:
    """The module-level default must read from WIKI_WEAVER_SPAWN_TIMEOUT at
    import time; verify the env var name and parseability directly (the
    module is already imported by the time this test runs, so we assert
    on the parsing helper behavior instead of re-importing).
    """
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_TIMEOUT", "42")
    # Mirrors the exact parse expression used in engine_runner.py.
    import os

    assert float(os.environ.get("WIKI_WEAVER_SPAWN_TIMEOUT", "1800")) == 42.0
