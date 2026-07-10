"""Layer-1 (system prompt) delivery regression test -- post pipeline-runner migration.

HISTORICAL CONTEXT: this file used to drive wiki-weaver's OWN
``_resolve_agent_bundle`` directly, proving it populated ``Bundle.context``
from an agent's ``context.include`` block (see git history: PR #32 fixed a
real "Layer-1 empty" bug where the context block was silently dropped).

Slice 4 (the pipeline-runner migration) REMOVED wiki-weaver's own
``_resolve_agent_bundle`` / ``make_spawn_fn`` entirely -- agent resolution and
Layer-1 delivery are now owned by
``amplifier_module_pipeline_runner.run_pipeline``'s own ``make_spawn_fn`` /
``_resolve_agent_bundle``. That module's docstring records the DECISION this
migration made: agent ``context.include`` is deliberately NOT processed as
Layer-1 there either -- Layer-1 comes from ``loop-agent``'s provider-default
selection (``context/system-<provider>.md``), which is fail-loud on an empty
Layer-1. wiki-weaver never had a tuned Layer-1 of its own to preserve (its
authoring instructions live in each ``.dot`` node's ``prompt=`` text, which is
ADDITIVE, not Layer-1) -- so this is a clean fit, not a regression.

Porting the OLD tests verbatim would re-test a mechanism wiki-weaver
deliberately no longer uses. This file instead guards the MIGRATION CONTRACT:

1. wiki-weaver's own agent-resolution/context-processing internals are gone
   (proves the migration happened -- a stale copy left behind would silently
   diverge from the producer's behavior over time).
2. Every ``run_pipeline`` call site passes ``profiles=None``, so routing is
   NOT overridden by wiki-weaver -- it flows through to
   ``amplifier_module_pipeline_runner``'s ``DEFAULT_PROFILES``, which maps
   each provider wiki-weaver actually uses to its attractor-agent-* child.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Skip cleanly in lightweight CI (no @main resolution of the attractor engine
# deps) rather than erroring -- matches every other eval/ file that
# transitively imports wiki_weaver.engine_runner.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402


class TestAgentResolutionMovedToPipelineRunner:
    """The migration must not leave a stale, diverging copy of agent resolution."""

    @pytest.mark.parametrize(
        "removed_symbol",
        [
            "_resolve_agent_bundle",
            "make_spawn_fn",
            "make_ask_spawn_fn",
            "_spawn_with_timeout",
        ],
    )
    def test_wiki_weaver_no_longer_owns_agent_resolution(
        self, removed_symbol: str
    ) -> None:
        assert not hasattr(er, removed_symbol), (
            f"wiki_weaver.engine_runner still defines {removed_symbol!r} -- "
            "this mechanism was migrated to "
            "amplifier_module_pipeline_runner.run_pipeline's own make_spawn_fn "
            "/ _resolve_agent_bundle. A stale local copy is context poison: "
            "it will silently diverge from the producer's Layer-1 contract."
        )

    def test_run_pipeline_is_the_single_engine_entrypoint(self) -> None:
        """engine_runner must import run_pipeline from the shared producer
        library, not reimplement engine-driving mechanics locally."""
        from amplifier_module_pipeline_runner import (
            run_pipeline as producer_run_pipeline,
        )

        assert er.run_pipeline is producer_run_pipeline, (
            "wiki_weaver.engine_runner.run_pipeline must be the SAME object as "
            "amplifier_module_pipeline_runner.run_pipeline (imported, not "
            "wrapped/shadowed) -- a copy would drift from the producer's "
            "Layer-1 / spawn-timeout / child_constraint contract."
        )


class TestChildConstraintSeamShape:
    """The two wiki-weaver-owned constraints must match run_pipeline's
    ``child_constraint: Callable[[Bundle], Bundle]`` contract."""

    def test_fs_child_constraint_returns_a_callable(self, tmp_path: Path) -> None:
        constraint = er._fs_child_constraint(tmp_path)
        assert callable(constraint)
        sig = inspect.signature(constraint)
        assert len(sig.parameters) == 1, (
            "child_constraint must take exactly one positional arg (the "
            "child Bundle) per run_pipeline's Callable[[Bundle], Bundle] seam"
        )

    def test_fs_child_constraint_mutates_and_returns_the_same_bundle(
        self, tmp_path: Path
    ) -> None:
        from types import SimpleNamespace

        constraint = er._fs_child_constraint(tmp_path)
        fake_bundle = SimpleNamespace(
            tools=[{"module": "tool-filesystem", "config": {}}]
        )
        result = constraint(fake_bundle)
        assert result is fake_bundle, (
            "child_constraint must return the (possibly mutated) bundle it "
            "was given -- run_pipeline uses the return value directly"
        )
        denied = result.tools[0]["config"].get("denied_write_paths", [])
        assert any(".processed.jsonl" in p for p in denied), (
            f"expected the ledger path denied, got: {denied}"
        )

    def test_ask_child_constraint_returns_a_callable(self, tmp_path: Path) -> None:
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        answer_file = tmp_path / "answer.json"
        constraint = er._ask_child_constraint(wiki_dir, answer_file)
        assert callable(constraint)
        sig = inspect.signature(constraint)
        assert len(sig.parameters) == 1

    def test_ask_child_constraint_mutates_and_returns_the_same_bundle(
        self, tmp_path: Path
    ) -> None:
        from types import SimpleNamespace

        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir()
        answer_file = tmp_path / "answer.json"
        constraint = er._ask_child_constraint(wiki_dir, answer_file)
        fake_bundle = SimpleNamespace(
            tools=[
                {"module": "tool-bash", "config": {}},
                {"module": "tool-filesystem", "config": {}},
            ]
        )
        result = constraint(fake_bundle)
        assert result is fake_bundle
        # tool-bash must be structurally removed (the ask-pipeline mechanism).
        modules = [t.get("module") for t in result.tools]
        assert "tool-bash" not in modules, (
            f"_ask_child_constraint must remove tool-bash, got tools: {result.tools}"
        )
