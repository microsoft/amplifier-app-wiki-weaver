"""Tests for the run-scoped events.jsonl observability sink.

Covers two layers:

1. ``_ci_overlay(logs_dir)`` (wiki_weaver.engine_runner) -- the Bundle-
   composition seam. Proves:
     (a) the overlay now carries TWO hooks: hook-context-intelligence +
         hook-run-events.
     (b) the hook-context-intelligence entry is COMPLETELY UNCHANGED --
         same module id, same source, same config as ``load_ci_config()``
         returns directly (no base_path/project_slug/anything added). This
         is the regression test proving the existing CI hook's own
         path/behavior is unaffected by this change.
     (c) the hook-run-events entry's config carries the correct
         ``events_path`` for the run's own ``logs_dir``.
     (d) every public entrypoint (run_thin_slice, run_ask, run_inner,
         run_ingest, run_lint, run_init, reweave_overview) threads ITS OWN
         ``logs_dir`` into ``_ci_overlay`` (mirrors the existing
         spawn_timeout wiring tests in test_spawn_timeout.py -- mock
         ``run_pipeline``, assert on the captured kwargs).

2. ``amplifier_module_hook_run_events`` (modules/hook-run-events) -- the
   hook module itself, exercised against the REAL Rust-backed
   ``ModuleCoordinator`` (via ``amplifier_core.testing.MockCoordinator``),
   not a hand-rolled fake. Proves:
     (a) events emitted after mount()+on_session_ready() are appended to
         ``events_path`` as valid JSON lines.
     (b) a SECOND coordinator instance (simulating a spawned child that
         inherited the same overlay Bundle -- and therefore the same
         ``events_path`` config value) appends to the SAME file, not a
         separate one -- this is the mechanism that makes "parent + every
         child in one file" true.
     (c) with no ``events_path`` configured, the sink is inert (no file
         created, no error raised) -- mount()/on_session_ready() degrade
         gracefully.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "modules" / "hook-run-events"))

import pytest  # noqa: E402

# Same skip convention as test_ci_config.py / test_spawn_timeout.py: these
# tests import wiki_weaver.engine_runner, which pulls in the attractor engine
# deps. Skip cleanly (not an error) when that resolution isn't available.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402


# ---------------------------------------------------------------------------
# Layer 1: _ci_overlay(logs_dir) composition
# ---------------------------------------------------------------------------


class TestCiOverlayComposition:
    def test_overlay_carries_exactly_two_hooks(self, tmp_path: Path) -> None:
        logs_dir = tmp_path / "run-1"
        overlay = er._ci_overlay(logs_dir)

        assert len(overlay.hooks) == 2, (
            f"expected exactly 2 hooks (CI + run-events), got: {overlay.hooks}"
        )
        modules = [h["module"] for h in overlay.hooks]
        assert modules == ["hook-context-intelligence", "hook-run-events"]

    def test_ci_hook_entry_is_byte_identical_to_pre_change_behavior(
        self, tmp_path: Path
    ) -> None:
        """Regression: the CI hook's module id, source, and config must be
        EXACTLY what ``load_ci_config()`` + ``CI_HOOK_SOURCE`` produce on
        their own -- proving this change added a sibling hook without
        touching the CI hook's own inputs in any way.
        """
        logs_dir = tmp_path / "run-2"
        overlay = er._ci_overlay(logs_dir)

        ci_entry = overlay.hooks[0]
        assert ci_entry["module"] == "hook-context-intelligence"
        assert ci_entry["source"] == er.CI_HOOK_SOURCE
        # Config must equal a fresh, independent call to load_ci_config() --
        # not merely "truthy" -- proving nothing (base_path, project_slug,
        # events_path, ...) was merged into it.
        assert ci_entry["config"] == er.load_ci_config()

    def test_run_events_hook_entry_points_at_this_runs_events_path(
        self, tmp_path: Path
    ) -> None:
        logs_dir = tmp_path / "run-3"
        overlay = er._ci_overlay(logs_dir)

        run_events_entry = overlay.hooks[1]
        assert run_events_entry["module"] == "hook-run-events"
        assert run_events_entry["config"] == {
            "events_path": str(logs_dir / "events.jsonl")
        }
        # source must resolve to the local module directory shipped in this repo.
        assert Path(run_events_entry["source"]).name == "hook-run-events"
        assert (Path(run_events_entry["source"]) / "pyproject.toml").is_file()

    def test_two_different_runs_get_two_different_events_paths(
        self, tmp_path: Path
    ) -> None:
        """Each run's logs_dir is distinct, so nothing accidentally shares a
        single global events.jsonl across unrelated runs.
        """
        overlay_a = er._ci_overlay(tmp_path / "run-a")
        overlay_b = er._ci_overlay(tmp_path / "run-b")

        assert (
            overlay_a.hooks[1]["config"]["events_path"]
            != overlay_b.hooks[1]["config"]["events_path"]
        )


# ---------------------------------------------------------------------------
# Layer 1b: every public entrypoint threads ITS OWN logs_dir into _ci_overlay
# ---------------------------------------------------------------------------


def _capture_run_pipeline(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    """Mirrors test_spawn_timeout.py's helper: patch er.run_pipeline to record
    kwargs and return a minimal successful PipelineResult.
    """
    from amplifier_module_pipeline_runner import PipelineResult

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        captured["dot_source"] = dot_source
        captured.update(kwargs)
        return PipelineResult(
            status="success", notes="ok", logs_dir=Path("/tmp"), raw="{}"
        )

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)


def _events_path_from_captured(captured: dict) -> str:
    overlays = captured["extra_overlays"]
    assert len(overlays) == 1
    return overlays[0].hooks[1]["config"]["events_path"]


class TestCallSitesThreadTheirOwnLogsDir:
    """Every entrypoint's own logs_dir (the same one passed as
    ``logs_root=`` to run_pipeline) must be what the run-events hook writes
    to -- so the file created lives right next to that run's ``*.dot`` /
    session artifacts, exactly as the brief specifies
    (``<logs_dir>/events.jsonl``).
    """

    def test_run_thin_slice(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        captured: dict = {}
        _capture_run_pipeline(monkeypatch, captured)

        er.run_thin_slice(tmp_path / "proof.txt", cwd=tmp_path)

        logs_root = captured["logs_root"]
        assert _events_path_from_captured(captured) == str(logs_root / "events.jsonl")

    def test_run_ask(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        captured: dict = {}
        _capture_run_pipeline(monkeypatch, captured)

        er.run_ask(wiki_dir, "does the wiki cover anything?")

        logs_root = captured["logs_root"]
        assert _events_path_from_captured(captured) == str(logs_root / "events.jsonl")

    def test_run_inner(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        source = tmp_path / "source.md"
        source.write_text("hello world", encoding="utf-8")
        captured: dict = {}
        _capture_run_pipeline(monkeypatch, captured)

        er.run_inner(source, wiki_dir)

        logs_root = captured["logs_root"]
        assert _events_path_from_captured(captured) == str(logs_root / "events.jsonl")

    def test_run_ingest(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        captured: dict = {}
        _capture_run_pipeline(monkeypatch, captured)

        er.run_ingest(wiki_dir)

        logs_root = captured["logs_root"]
        assert _events_path_from_captured(captured) == str(logs_root / "events.jsonl")

    def test_reweave_overview(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from wiki_weaver import reweave as rw

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "index.md").write_text("# Index\n", encoding="utf-8")
        (wiki_dir / "overview.md").write_text("placeholder", encoding="utf-8")

        captured: dict = {}

        async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
            captured["dot_source"] = dot_source
            captured.update(kwargs)
            # Simulate the pipeline having written a real overview.
            (wiki_dir / "overview.md").write_text(
                "# Overview\nsomething real\n", encoding="utf-8"
            )
            from amplifier_module_pipeline_runner import PipelineResult

            return PipelineResult(
                status="success", notes="ok", logs_dir=Path("/tmp"), raw="{}"
            )

        monkeypatch.setattr(rw, "run_pipeline", fake_run_pipeline)

        rw.reweave_overview(wiki_dir)

        logs_root = captured["logs_root"]
        assert _events_path_from_captured(captured) == str(logs_root / "events.jsonl")


# ---------------------------------------------------------------------------
# Layer 2: the hook module itself, against the REAL Rust-backed coordinator
# ---------------------------------------------------------------------------

import amplifier_module_hook_run_events as run_events_hook  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestRunEventsHookMechanism:
    @pytest.mark.asyncio
    async def test_events_appended_as_valid_json_lines(self, tmp_path: Path) -> None:
        from amplifier_core.testing import MockCoordinator

        events_path = tmp_path / "run" / "events.jsonl"
        coordinator = MockCoordinator()

        cleanup = await run_events_hook.mount(
            coordinator, {"events_path": str(events_path)}
        )
        await run_events_hook.on_session_ready(coordinator)

        await coordinator.hooks.emit(
            "session:start", {"session_id": "parent-session", "timestamp": "t0"}
        )
        await coordinator.hooks.emit(
            "execution:end", {"session_id": "parent-session", "timestamp": "t1"}
        )

        assert events_path.is_file(), "events.jsonl must be created on first event"
        records = _read_jsonl(events_path)
        assert len(records) == 2
        assert records[0]["event"] == "session:start"
        assert records[0]["session_id"] == "parent-session"
        assert records[1]["event"] == "execution:end"

        await cleanup()

    @pytest.mark.asyncio
    async def test_parent_and_child_sessions_append_to_the_same_file(
        self, tmp_path: Path
    ) -> None:
        """Simulates a spawned child: a SECOND coordinator instance mounted
        with the SAME ``events_path`` config value (exactly what happens
        when PreparedBundle.spawn inherits the parent's overlay Bundle,
        whose hook config was baked once by ``_ci_overlay`` and is
        therefore identical for parent and every child).
        """
        from amplifier_core.testing import MockCoordinator

        events_path = tmp_path / "run" / "events.jsonl"
        config = {"events_path": str(events_path)}

        parent = MockCoordinator()
        parent_cleanup = await run_events_hook.mount(parent, config)
        await run_events_hook.on_session_ready(parent)
        await parent.hooks.emit(
            "session:start", {"session_id": "parent-session", "timestamp": "t0"}
        )

        child = MockCoordinator()
        child_cleanup = await run_events_hook.mount(child, config)
        await run_events_hook.on_session_ready(child)
        await child.hooks.emit(
            "session:start", {"session_id": "child-session", "timestamp": "t1"}
        )
        await child.hooks.emit(
            "execution:end", {"session_id": "child-session", "timestamp": "t2"}
        )

        records = _read_jsonl(events_path)
        session_ids = {r["session_id"] for r in records}
        assert session_ids == {"parent-session", "child-session"}, (
            "parent and child events must land in the SAME events.jsonl file, "
            f"got session_ids={session_ids}"
        )
        assert len(records) == 3

        await parent_cleanup()
        await child_cleanup()

    @pytest.mark.asyncio
    async def test_no_events_path_configured_is_a_safe_noop(
        self, tmp_path: Path
    ) -> None:
        from amplifier_core.testing import MockCoordinator

        coordinator = MockCoordinator()
        cleanup = await run_events_hook.mount(coordinator, {})
        await run_events_hook.on_session_ready(coordinator)

        # Must not raise, and must not create anything under tmp_path.
        await coordinator.hooks.emit(
            "session:start", {"session_id": "x", "timestamp": "t0"}
        )

        assert list(tmp_path.iterdir()) == []
        await cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_is_callable_and_does_not_raise(self, tmp_path: Path) -> None:
        """cleanup() must be safely callable (protocol compliance requires
        mount() return a cleanup callable). Full unregister-on-cleanup
        semantics are a kernel/HookRegistry concern (amplifier_core), not
        this module's -- out of scope here; this module mirrors the exact
        registration/cleanup pattern already used in production by
        hook-context-intelligence.
        """
        from amplifier_core.testing import MockCoordinator

        events_path = tmp_path / "run" / "events.jsonl"
        coordinator = MockCoordinator()
        cleanup = await run_events_hook.mount(
            coordinator, {"events_path": str(events_path)}
        )
        await run_events_hook.on_session_ready(coordinator)

        await coordinator.hooks.emit(
            "session:start", {"session_id": "s", "timestamp": "t0"}
        )
        assert len(_read_jsonl(events_path)) == 1

        await cleanup()  # must not raise
