"""Runtime-layer regression test for _resolve_agent_bundle context delivery.

Ensures that spawned pipeline node agents receive a populated Bundle.context
so that foundation's system-prompt factory produces a non-empty Layer-1 prompt.

RED on main: _resolve_agent_bundle drops the context block from the inline overlay
  -> child_bundle.context is empty -> factory guard fires -> Layer-1 empty (31x logged).
GREEN after fix: context is processed via _parse_context -> resolved to dict[str, Path]
  -> factory produces the provider system-prompt as Layer-1.

This is a RUNTIME-LAYER test: it drives the real _resolve_agent_bundle with a
realistic attractor inline-overlay config and asserts on the resolved Bundle, not
on YAML file structure.  A structural proxy already fooled us once (PR1 added
context.include to the overlay, unit test went GREEN, DTU still showed 31 empties).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

# Make wiki_weaver importable without installing.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attractor_bundle_layout(
    tmp_path: Path, provider: str, sentinel: str
) -> tuple[Path, Path]:
    """Create a minimal attractor bundle directory layout in tmp_path.

    Returns (bundles_dir, context_file) where:
      - bundles_dir is the directory containing the bundle YAML (base_path for resolution)
      - context_file is the system prompt file the include should resolve to
    """
    bundles_dir = tmp_path / "bundles"
    context_dir = tmp_path / "context"
    bundles_dir.mkdir(parents=True)
    context_dir.mkdir(parents=True)

    # Write a synthetic provider system-prompt file with a stable sentinel phrase.
    context_file = context_dir / f"system-{provider}.md"
    context_file.write_text(
        f"# Provider Instructions for {provider.upper()}\n\n{sentinel}\n",
        encoding="utf-8",
    )
    return bundles_dir, context_file


def _attractor_inline_config(provider: str) -> dict[str, Any]:
    """The realistic inline overlay config that arrives from prepared.bundle.agents.

    This mirrors the shape in attractor-pipeline.yaml agents map post-#74:
      description + session.orchestrator overlay + context.include block.
    The context block is what _resolve_agent_bundle must NOT drop.
    """
    return {
        "description": f"{provider.capitalize()} coding agent",
        "session": {
            "orchestrator": {
                "module": "loop-agent",
                "source": "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
                "#subdirectory=modules/loop-agent",
                "config": {
                    "max_tool_rounds_per_input": 50,
                    "default_command_timeout_ms": 120000,
                },
            }
        },
        "context": {
            # Relative to bundles_dir (the bundle YAML's parent dir).
            "include": [f"../context/system-{provider}.md"]
        },
    }


# ---------------------------------------------------------------------------
# Runtime-layer tests
# ---------------------------------------------------------------------------


class TestResolveAgentBundleDeliversContext:
    """_resolve_agent_bundle must populate Bundle.context from the inline overlay."""

    def test_anthropic_context_populated(self, tmp_path: Path) -> None:
        """Anthropic agent overlay: context resolves to the real system-prompt file.

        RED on main  : child_bundle.context is {} -> assertion fails
                       (or TypeError if base_path param not yet present)
        GREEN on fix : child_bundle.context is {"../context/system-anthropic.md": <Path>}
                       and the Path exists + contains the sentinel
        """
        from wiki_weaver.engine_runner import _resolve_agent_bundle

        provider = "anthropic"
        sentinel = "ANTHROPIC_TEST_SENTINEL_7a2f9c"
        bundles_dir, context_file = _make_attractor_bundle_layout(
            tmp_path, provider, sentinel
        )
        config = _attractor_inline_config(provider)

        child_bundle = asyncio.run(
            _resolve_agent_bundle(
                "attractor-agent-anthropic", config, base_path=bundles_dir
            )
        )

        # --- RUNTIME-LAYER assertions ---

        # 1. context must be non-empty (the factory guard fires on empty context)
        assert child_bundle.context, (
            "child_bundle.context is empty — _resolve_agent_bundle dropped the context "
            "block from the inline overlay. The system-prompt factory will never fire."
        )

        # 2. the include resolved to the provider system-prompt file, under the
        #    exact include name (not just "some" key -- a regression that resolved
        #    the right path under the wrong dict key would still corrupt downstream
        #    consumers that look up context by name).
        expected_key = f"../context/system-{provider}.md"
        assert list(child_bundle.context.keys()) == [expected_key], (
            f"Expected context key {expected_key!r}, got {list(child_bundle.context.keys())}"
        )
        resolved_paths = list(child_bundle.context.values())
        assert len(resolved_paths) == 1, (
            f"Expected 1 context entry, got {len(resolved_paths)}: {resolved_paths}"
        )
        resolved = resolved_paths[0]

        # 3. the resolved path actually exists on disk (proves it's the right file, not a
        #    bogus path)
        assert resolved.exists(), (
            f"Resolved context path does not exist: {resolved}\n"
            f"Expected: {context_file}"
        )

        # 4. the file contains the sentinel — the right file was wired in
        content = resolved.read_text(encoding="utf-8")
        assert sentinel in content, (
            f"Sentinel {sentinel!r} not found in resolved context file {resolved}.\n"
            f"File content: {content!r}"
        )

    def test_openai_context_populated(self, tmp_path: Path) -> None:
        """OpenAI agent overlay: same guarantee, different provider."""
        from wiki_weaver.engine_runner import _resolve_agent_bundle

        provider = "openai"
        sentinel = "OPENAI_TEST_SENTINEL_9b1e4d"
        bundles_dir, context_file = _make_attractor_bundle_layout(
            tmp_path, provider, sentinel
        )
        config = _attractor_inline_config(provider)

        child_bundle = asyncio.run(
            _resolve_agent_bundle(
                "attractor-agent-openai", config, base_path=bundles_dir
            )
        )

        assert child_bundle.context, "child_bundle.context empty for openai agent"
        resolved = list(child_bundle.context.values())[0]
        assert resolved.exists(), f"Resolved path does not exist: {resolved}"
        assert sentinel in resolved.read_text(encoding="utf-8")

    def test_gemini_context_populated(self, tmp_path: Path) -> None:
        """Gemini agent overlay: same guarantee, different provider."""
        from wiki_weaver.engine_runner import _resolve_agent_bundle

        provider = "gemini"
        sentinel = "GEMINI_TEST_SENTINEL_3c7d2e"
        bundles_dir, context_file = _make_attractor_bundle_layout(
            tmp_path, provider, sentinel
        )
        config = _attractor_inline_config(provider)

        child_bundle = asyncio.run(
            _resolve_agent_bundle(
                "attractor-agent-gemini", config, base_path=bundles_dir
            )
        )

        assert child_bundle.context, "child_bundle.context empty for gemini agent"
        resolved = list(child_bundle.context.values())[0]
        assert resolved.exists(), f"Resolved path does not exist: {resolved}"
        assert sentinel in resolved.read_text(encoding="utf-8")

    def test_two_agents_resolve_distinct_non_cross_contaminated_context(
        self, tmp_path: Path
    ) -> None:
        """Guards the gap a bare non-emptiness check cannot catch: a NON-EMPTY but
        WRONG prompt (e.g. agent A silently resolving to agent B's system prompt,
        or a hardcoded placeholder shared across agents).

        ``assert child_bundle.context`` alone would happily pass even if every
        agent resolved to the same (wrong) file. This test resolves TWO distinct
        agents in the same run and asserts each one's resolved context contains
        ONLY its own distinctive sentinel and NEVER the other agent's -- proving
        the content that flows through is agent-specific, not just "present".
        """
        from wiki_weaver.engine_runner import _resolve_agent_bundle

        bundles_dir = tmp_path / "bundles"
        context_dir = tmp_path / "context"
        bundles_dir.mkdir()
        context_dir.mkdir()

        sentinel_a = "AGENT_A_DISTINCT_SENTINEL_4f1c8e"
        sentinel_b = "AGENT_B_DISTINCT_SENTINEL_9d3a71"
        (context_dir / "system-agent-a.md").write_text(
            f"# Agent A Instructions\n\n{sentinel_a}\n", encoding="utf-8"
        )
        (context_dir / "system-agent-b.md").write_text(
            f"# Agent B Instructions\n\n{sentinel_b}\n", encoding="utf-8"
        )

        config_a = {
            "description": "Agent A",
            "session": {"orchestrator": {"module": "loop-agent", "config": {}}},
            "context": {"include": ["../context/system-agent-a.md"]},
        }
        config_b = {
            "description": "Agent B",
            "session": {"orchestrator": {"module": "loop-agent", "config": {}}},
            "context": {"include": ["../context/system-agent-b.md"]},
        }

        bundle_a = asyncio.run(
            _resolve_agent_bundle("agent-a", config_a, base_path=bundles_dir)
        )
        bundle_b = asyncio.run(
            _resolve_agent_bundle("agent-b", config_b, base_path=bundles_dir)
        )

        content_a = list(bundle_a.context.values())[0].read_text(encoding="utf-8")
        content_b = list(bundle_b.context.values())[0].read_text(encoding="utf-8")

        assert sentinel_a in content_a, (
            "agent-a's resolved context is missing its own sentinel -- "
            f"content was: {content_a!r}"
        )
        assert sentinel_b not in content_a, (
            "agent-a's resolved context leaked agent-b's system prompt content "
            "-- cross-agent contamination in _resolve_agent_bundle"
        )
        assert sentinel_b in content_b, (
            "agent-b's resolved context is missing its own sentinel -- "
            f"content was: {content_b!r}"
        )
        assert sentinel_a not in content_b, (
            "agent-b's resolved context leaked agent-a's system prompt content "
            "-- cross-agent contamination in _resolve_agent_bundle"
        )

    def test_no_context_block_still_works(self, tmp_path: Path) -> None:
        """An inline overlay with NO context block must still return a valid Bundle.

        The fix must be non-breaking: agents that legitimately have no context block
        should continue to produce a Bundle with empty context (no crash).
        """
        from wiki_weaver.engine_runner import _resolve_agent_bundle

        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        config = {
            "description": "minimal agent with no context",
            "session": {"orchestrator": {"module": "loop-agent", "config": {}}},
        }

        child_bundle = asyncio.run(
            _resolve_agent_bundle("some-agent", config, base_path=bundles_dir)
        )

        # No context -> empty context dict, but no crash
        assert isinstance(child_bundle.context, dict)
        assert child_bundle.context == {}

    def test_constrain_agent_fs_still_works(self, tmp_path: Path) -> None:
        """_constrain_agent_fs must still be able to mutate the child bundle's tools list.

        The fix must not break Fix 1 (filesystem isolation).  Verify by calling
        _constrain_agent_fs on the resolved bundle and checking the denied paths
        are injected.
        """
        from wiki_weaver.engine_runner import _constrain_agent_fs, _resolve_agent_bundle

        provider = "anthropic"
        sentinel = "CONSTRAIN_TEST_SENTINEL_5f8a"
        bundles_dir, _ = _make_attractor_bundle_layout(tmp_path, provider, sentinel)
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()

        config = {
            "description": "agent with filesystem tool",
            "session": {"orchestrator": {"module": "loop-agent", "config": {}}},
            "tools": [
                {
                    "module": "tool-filesystem",
                    "source": "git+https://example.com/tool-filesystem@main",
                    "config": {"root_path": str(wiki_dir)},
                }
            ],
            "context": {"include": [f"../context/system-{provider}.md"]},
        }

        child_bundle = asyncio.run(
            _resolve_agent_bundle(
                "attractor-agent-anthropic", config, base_path=bundles_dir
            )
        )

        # Verify tools list is mutable and _constrain_agent_fs injects deny paths
        _constrain_agent_fs(child_bundle, wiki_dir)

        fs_tool = next(
            (t for t in child_bundle.tools if t.get("module") == "tool-filesystem"),
            None,
        )
        assert fs_tool is not None, "tool-filesystem not found in child_bundle.tools"
        denied = fs_tool.get("config", {}).get("denied_write_paths", [])
        assert any(".processed.jsonl" in p for p in denied), (
            f"Expected .processed.jsonl in denied paths, got: {denied}"
        )
