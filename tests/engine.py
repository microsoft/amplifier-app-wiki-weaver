"""The real dot-runner engine for tests, or None (see conftest: a stub may be on PATH)."""

from __future__ import annotations

import os

import pytest

ENGINE: str | None = os.environ.get("WW_TEST_ENGINE") or None
needs_engine = pytest.mark.skipif(ENGINE is None, reason="dot-runner not installed")
