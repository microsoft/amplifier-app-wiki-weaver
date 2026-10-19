"""The real dot-runner engine for tests, or None (see conftest: a stub may be on PATH)."""

from __future__ import annotations

import os

import pytest

ENGINE: str | None = os.environ.get("WW_TEST_ENGINE") or None
needs_engine = pytest.mark.skipif(ENGINE is None, reason="dot-runner not installed")
# The engine's own preflight refuses to start without provider credentials, even for a graph
# with no model nodes (a plain init, a tool-only graph). Tests that run it need both.
needs_engine_and_key = pytest.mark.skipif(
    ENGINE is None or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="dot-runner not installed, or ANTHROPIC_API_KEY unset (the engine requires it)",
)
