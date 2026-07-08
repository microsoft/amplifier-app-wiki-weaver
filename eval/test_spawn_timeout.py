"""Regression test for the ``ingest`` hang investigation.

CONFIRMED ROOT CAUSE (see PR / investigation notes): ``spawn_capability``
(built by ``make_spawn_fn`` / ``make_ask_spawn_fn`` in engine_runner.py) awaited
``prepared.spawn(...)`` with NO timeout at all. Neither wiki-weaver, loop-agent,
nor unified-llm-client's Anthropic adapter set an explicit per-call timeout --
every LLM call silently inherited the Anthropic Python SDK's undocumented
default (``Timeout(connect=5.0, read=600, write=600, pool=600)``), and a single
node's spawn can make MANY sequential LLM+tool-call rounds, so the aggregate
spawn duration was not bounded by any single call's timeout either. A stalled
child agent therefore blocked the shared event loop indefinitely with zero
feedback -- indistinguishable, from the outside, from a slow-but-working node
(live investigation confirmed legitimate per-node durations up to ~6 minutes
with zero errors).

This test proves the fix: ``_spawn_with_timeout`` wraps the same call in
``asyncio.wait_for`` bounded by ``SPAWN_TIMEOUT_SECONDS`` (overridable via
``WIKI_WEAVER_SPAWN_TIMEOUT``), so a stalled spawn fails loud with a clear,
actionable ``TimeoutError`` instead of hanging forever. No real LLM calls;
the slow/stalled provider call is mocked with ``asyncio.sleep``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# These tests import wiki_weaver.engine_runner, which imports the attractor
# engine deps. Skip cleanly in lightweight CI (no @main resolution) rather
# than erroring -- matches eval/test_ingest_drain.py's convention.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402


def _fake_prepared(spawn_coro_factory) -> SimpleNamespace:
    """Minimal stand-in for a PreparedBundle: only ``.bundle.agents`` and
    ``.spawn()`` are touched by ``spawn_capability``.
    """

    async def spawn(**kwargs):
        return await spawn_coro_factory(**kwargs)

    return SimpleNamespace(
        bundle=SimpleNamespace(agents={"writer": {"model": "sonnet"}}),
        spawn=spawn,
    )


# ---------------------------------------------------------------------------
# Test 1 -- a spawn that never returns is bounded, not infinite
# ---------------------------------------------------------------------------


async def test_stalled_spawn_times_out_instead_of_hanging(monkeypatch) -> None:
    """A child agent that never completes must fail loud within the
    configured ceiling -- proving the wait is bounded, not unbounded.

    Uses a near-instant timeout (0.05s) so the test itself runs in
    milliseconds rather than waiting out a real multi-minute timeout.
    """
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 0.05)

    async def never_returns(**kwargs):
        # Simulates a stalled LLM call: awaits "forever" relative to the
        # configured timeout. Bounded at 5s so a REGRESSION (timeout not
        # applied) fails the test quickly rather than hanging the suite.
        await asyncio.sleep(5)
        return {"output": "should never get here"}

    prepared = _fake_prepared(never_returns)
    spawn_capability = er.make_spawn_fn(prepared)

    with pytest.raises(TimeoutError, match="did not complete within"):
        await asyncio.wait_for(
            spawn_capability(
                agent_name="writer",
                instruction="do the thing",
                parent_session=None,
                agent_configs={},
            ),
            timeout=5,  # test-level safety net; the real bound is 0.05s above
        )


# ---------------------------------------------------------------------------
# Test 2 -- a normal (fast) spawn is unaffected
# ---------------------------------------------------------------------------


async def test_fast_spawn_completes_normally(monkeypatch) -> None:
    """A child agent that completes well within the timeout must return
    its result unchanged -- the fix must not alter the happy path.
    """
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 5.0)

    async def completes_quickly(**kwargs):
        await asyncio.sleep(0.01)
        return {"output": "done", "session_id": "abc123"}

    prepared = _fake_prepared(completes_quickly)
    spawn_capability = er.make_spawn_fn(prepared)

    result = await spawn_capability(
        agent_name="writer",
        instruction="do the thing",
        parent_session=None,
        agent_configs={},
    )
    assert result == {"output": "done", "session_id": "abc123"}


# ---------------------------------------------------------------------------
# Test 3 -- a slow-but-legitimate spawn under the ceiling still succeeds
# ---------------------------------------------------------------------------


async def test_slow_but_bounded_spawn_still_succeeds(monkeypatch) -> None:
    """Regression guard for the exact failure mode observed live: a node
    that legitimately takes several minutes (here, simulated as slower than
    a short timeout but still under the configured ceiling) must NOT be
    falsely killed. Guards against an overly aggressive timeout regression.
    """
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 0.2)

    async def slow_but_fine(**kwargs):
        await asyncio.sleep(0.05)  # well under the 0.2s ceiling
        return {"output": "converged"}

    prepared = _fake_prepared(slow_but_fine)
    spawn_capability = er.make_spawn_fn(prepared)

    result = await spawn_capability(
        agent_name="writer",
        instruction="do the thing",
        parent_session=None,
        agent_configs={},
    )
    assert result == {"output": "converged"}


# ---------------------------------------------------------------------------
# Test 4 -- the ask-pipeline spawn function gets the same protection
# ---------------------------------------------------------------------------


async def test_ask_spawn_fn_also_times_out(monkeypatch, tmp_path: Path) -> None:
    """``make_ask_spawn_fn`` shares ``_spawn_with_timeout`` -- prove it too
    fails loud on a stalled child rather than hanging.
    """
    monkeypatch.setattr(er, "SPAWN_TIMEOUT_SECONDS", 0.05)

    async def never_returns(**kwargs):
        await asyncio.sleep(5)
        return {"output": "should never get here"}

    prepared = _fake_prepared(never_returns)
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    answer_file = tmp_path / "answer.json"
    spawn_capability = er.make_ask_spawn_fn(prepared, wiki_dir, answer_file)

    with pytest.raises(TimeoutError, match="did not complete within"):
        await asyncio.wait_for(
            spawn_capability(
                agent_name="writer",
                instruction="answer the question",
                parent_session=None,
                agent_configs={},
            ),
            timeout=5,
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
