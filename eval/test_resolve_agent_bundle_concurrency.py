"""Concurrency regression test for make_spawn_fn's shared _agent_cache.

Context: make_spawn_fn's ``_agent_cache: dict[str, Any]`` is closure-scoped and
SHARED across every concurrent ``spawn_capability`` call within one process --
the same shared-mutable-state shape that caused a real cross-agent-leak bug
fixed in PR #32 (`_resolve_agent_bundle` resolving one agent's system prompt
into another's `Bundle.context`, guarded by
`test_two_agents_resolve_distinct_non_cross_contaminated_context` in
eval/test_spawn_agent_system_prompt.py).

This test targets the SPECIFIC race introduced by removing the legacy
``{"bundle": "attractor:agents/<name>"}`` config shape (now a fail-loud
``ValueError`` instead of a resolved Bundle): does a FAILING resolution for a
legacy-shaped ``agent_name`` ever leave a partial/incorrect entry in
``_agent_cache[agent_name]`` that a second, concurrent caller for the SAME
``agent_name`` could then read back as if it were valid?

What this proves (not just "ran under load, didn't crash"):

1. Every one of 40 CONCURRENT callers targeting the SAME legacy-shaped
   ``agent_name`` ("legacy-shared") raises the actionable ``ValueError`` --
   none silently succeed, none see a different error.
2. After that concurrent failure storm, swapping "legacy-shared"'s config to a
   valid inline shape and re-resolving it SUCCEEDS cleanly. If any concurrent
   failure had left a partial/corrupt cache entry under that key, this call
   would either reuse the corrupt entry (wrong result) or blow up -- it does
   neither, proving the cache never contains a partial entry for a name whose
   resolution raised.
3. 40 CONCURRENT callers targeting the SAME inline-shaped ``agent_name``
   ("inline-shared") all succeed and all observe the SAME, correct resolved
   context -- the cache is safely reused across concurrent callers, not
   corrupted by the interleaving with the legacy failures happening at the
   same time.
4. 20 distinct concurrent inline agents (unique names) each resolve their OWN
   sentinel-bearing context file and never another's -- the PR #32
   cross-agent-leak guard, now exercised under real concurrency instead of
   sequential calls.

Total concurrent tasks: 40 + 40 + 20 = 100.

Real interleaving (not just sequential coroutines that happen to run inside
one asyncio.gather): the mocked ``prepared.spawn`` performs a genuine
``await asyncio.sleep(...)`` AFTER the cache read/write in
``spawn_capability``, so other scheduled tasks get a real chance to run
in between one task's cache write and its eventual return -- exactly the
window where a caching bug would show up.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Matches the convention used by every other eval/ file that transitively
# imports wiki_weaver.engine_runner (see test_spawn_agent_system_prompt.py,
# test_spawn_timeout.py, etc.) -- skip cleanly in CI's lightweight env
# (no unified_llm installed) instead of erroring.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402

LEGACY_REF = "attractor:agents/attractor-agent-anthropic"


def _fake_prepared(spawn_coro_factory, base_path: Path | None) -> SimpleNamespace:
    """Minimal PreparedBundle stand-in -- only `.bundle.agents`, `.bundle.base_path`,
    and `.spawn()` are touched by spawn_capability / _spawn_with_timeout."""

    async def spawn(**kwargs: Any) -> dict[str, Any]:
        return await spawn_coro_factory(**kwargs)

    return SimpleNamespace(
        bundle=SimpleNamespace(agents={}, base_path=base_path),
        spawn=spawn,
    )


def _inline_config(context_file_name: str) -> dict[str, Any]:
    """A valid inline agent config (current, supported shape)."""
    return {
        "description": "inline agent",
        "session": {"orchestrator": {"module": "loop-agent", "config": {}}},
        "context": {"include": [f"../context/{context_file_name}"]},
    }


async def _mock_spawn(**kwargs: Any) -> dict[str, Any]:
    """Stand-in for prepared.spawn(): a genuine suspension point (await) AFTER
    the cache read/write in spawn_capability, so concurrent callers targeting
    the same agent_name actually interleave around the cache -- mirroring how
    the real prepared.spawn() awaits network I/O in production.

    NOTE: `_spawn_with_timeout` does NOT forward `agent_name` into the kwargs
    it passes to `prepared.spawn(...)` (only `child_bundle`, `instruction`,
    `session_id`, `parent_session`, `orchestrator_config`, `parent_messages`,
    `provider_preferences`, `self_delegation_depth`). Identity is recovered
    from `child_bundle.name`, which `_resolve_agent_bundle` sets to the
    resolved agent's name.
    """
    await asyncio.sleep(0.01)
    child_bundle = kwargs["child_bundle"]
    return {
        "agent_name": child_bundle.name,
        "context_keys": sorted(child_bundle.context.keys()),
    }


class TestConcurrentAgentCacheSafety:
    async def test_concurrent_legacy_failures_and_inline_successes_dont_corrupt_cache(
        self, tmp_path: Path
    ) -> None:
        bundles_dir = tmp_path / "bundles"
        context_dir = tmp_path / "context"
        bundles_dir.mkdir()
        context_dir.mkdir()

        (context_dir / "agent-shared.md").write_text(
            "SHARED_SENTINEL\n", encoding="utf-8"
        )
        num_distinct = 20
        for i in range(num_distinct):
            (context_dir / f"agent-{i}.md").write_text(
                f"SENTINEL_{i}\n", encoding="utf-8"
            )

        agent_configs: dict[str, dict[str, Any]] = {
            "legacy-shared": {"bundle": LEGACY_REF},
            "inline-shared": _inline_config("agent-shared.md"),
        }
        for i in range(num_distinct):
            agent_configs[f"inline-distinct-{i}"] = _inline_config(f"agent-{i}.md")

        prepared = _fake_prepared(_mock_spawn, base_path=bundles_dir)
        # ONE make_spawn_fn call -> ONE shared _agent_cache, exactly the
        # closure-scoped shared-mutable-state shape under test.
        spawn_capability = er.make_spawn_fn(prepared)

        async def call(agent_name: str) -> dict[str, Any]:
            return await spawn_capability(
                agent_name=agent_name,
                instruction="do work",
                parent_session=None,
                agent_configs=agent_configs,
            )

        tasks = (
            [call("legacy-shared") for _ in range(40)]
            + [call("inline-shared") for _ in range(40)]
            + [call(f"inline-distinct-{i}") for i in range(num_distinct)]
        )
        assert len(tasks) == 100, "test must exercise exactly 100 concurrent attempts"

        results = await asyncio.gather(*tasks, return_exceptions=True)
        legacy_results = results[:40]
        inline_shared_results = results[40:80]
        distinct_results = results[80:]

        # --- (1) every concurrent legacy-shaped call fails loud, actionably ---
        for r in legacy_results:
            assert isinstance(r, ValueError), (
                f"expected every legacy-shaped concurrent call to raise "
                f"ValueError, got: {r!r}"
            )
            msg = str(r)
            assert "legacy-shared" in msg, msg
            assert LEGACY_REF in msg, msg
            assert "no longer supported" in msg, msg
            assert "session.orchestrator" in msg, msg
            assert "loop-agent" in msg, msg

        # --- (2) no partial/corrupt cache entry survived the failure storm ---
        # Re-point "legacy-shared" at a valid inline config and resolve again
        # through the SAME spawn_capability / _agent_cache. A corrupt partial
        # entry would either be reused (wrong result, no fresh resolution) or
        # blow up differently. Neither happens: fresh resolution succeeds.
        agent_configs["legacy-shared"] = _inline_config("agent-shared.md")
        recovery = await call("legacy-shared")
        assert recovery["context_keys"] == ["../context/agent-shared.md"], (
            f"cache for 'legacy-shared' did not resolve cleanly after the "
            f"concurrent failure storm -- possible corrupted/partial entry: "
            f"{recovery}"
        )

        # --- (3) concurrent SAME-name inline callers: cache reused, not corrupted ---
        for r in inline_shared_results:
            assert isinstance(r, dict), f"unexpected failure: {r!r}"
            assert r["context_keys"] == ["../context/agent-shared.md"], (
                f"'inline-shared' concurrent caller saw wrong/corrupted context: {r}"
            )

        # --- (4) distinct concurrent agents: no cross-agent contamination ---
        for i, r in enumerate(distinct_results):
            assert isinstance(r, dict), f"unexpected failure: {r!r}"
            assert r["context_keys"] == [f"../context/agent-{i}.md"], (
                f"inline-distinct-{i} resolved the WRONG context under "
                f"concurrency -- cross-agent leak: {r}"
            )

    async def test_legacy_error_message_is_actionable_for_every_shape(self) -> None:
        """The fail-loud message must name the offending value and point to the
        fix, regardless of the exact legacy ref string used."""
        for ref in (
            "attractor:agents/attractor-agent-anthropic",
            "attractor:agents/attractor-agent-openai",
            "some-other-namespace:agents/custom",
        ):
            with pytest.raises(ValueError) as exc_info:
                await er._resolve_agent_bundle("some-agent", {"bundle": ref})
            msg = str(exc_info.value)
            assert "some-agent" in msg
            assert ref in msg
            assert "no longer supported" in msg
            assert "inline" in msg
            assert "session.orchestrator" in msg
