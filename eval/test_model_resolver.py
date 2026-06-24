"""Unit tests for wiki_weaver.model_resolver.

All tests are keyless and deterministic — they mock _fetch_anthropic_models so
no real network calls are made.  Tests verify the resolver's:

  - family-token resolution (newest served model in the family)
  - explicit-id pass-through (no network call)
  - fail-loud on empty family match
  - fail-loud for non-anthropic provider with a family token
  - deterministic date tiebreak
  - process-level cache (second call returns cached value)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make wiki_weaver importable without installing.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.model_resolver import (  # noqa: E402
    _clear_cache,
    _parse_created_at,
    resolve_model,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_MOCK_LIST = [
    # Two sonnet models — different dates; the 2026 one should win.
    {
        "id": "claude-sonnet-4-2",
        "created_at": "2024-03-15T00:00:00Z",
        "display_name": "Claude Sonnet 4.2",
    },
    {
        "id": "claude-sonnet-4-6",
        "created_at": "2026-05-01T00:00:00Z",
        "display_name": "Claude Sonnet 4.6",
    },
    # One opus model.
    {
        "id": "claude-opus-4-8",
        "created_at": "2026-05-28T00:00:00Z",
        "display_name": "Claude Opus 4.8",
    },
    # No haiku in this list — used to test the empty-family error.
]

_MOCK_LIST_WITH_HAIKU = _MOCK_LIST + [
    {
        "id": "claude-haiku-4-5",
        "created_at": "2025-11-01T00:00:00Z",
        "display_name": "Claude Haiku 4.5",
    },
]

# Tiebreak list: two sonnet models with THE SAME release date.
_MOCK_TIED = [
    {
        "id": "claude-sonnet-4-6",
        "created_at": "2026-05-01T00:00:00Z",
        "display_name": "Claude Sonnet 4.6",
    },
    {
        "id": "claude-sonnet-4-7",
        "created_at": "2026-05-01T00:00:00Z",
        "display_name": "Claude Sonnet 4.7",
    },
]


@pytest.fixture(autouse=True)
def clear_resolver_cache():
    """Clear the process-level cache before and after each test for isolation."""
    _clear_cache()
    yield
    _clear_cache()


# ---------------------------------------------------------------------------
# _parse_created_at
# ---------------------------------------------------------------------------


class TestParseCreatedAt:
    def test_full_iso8601_with_z(self):
        from datetime import date

        assert _parse_created_at("2026-05-28T00:00:00Z") == date(2026, 5, 28)

    def test_iso8601_without_z(self):
        from datetime import date

        assert _parse_created_at("2024-03-15T00:00:00") == date(2024, 3, 15)

    def test_date_only(self):
        from datetime import date

        assert _parse_created_at("2025-11-01") == date(2025, 11, 1)

    def test_empty_string_returns_none(self):
        assert _parse_created_at("") is None

    def test_none_returns_none(self):
        assert _parse_created_at(None) is None

    def test_garbage_returns_none(self):
        assert _parse_created_at("not-a-date") is None


# ---------------------------------------------------------------------------
# resolve_model — explicit id pass-through
# ---------------------------------------------------------------------------


class TestExplicitIdPassthrough:
    """Explicit model ids are returned unchanged — no network call."""

    def test_explicit_id_returned_unchanged(self, monkeypatch):
        # Patch _fetch to raise so we know it wasn't called.
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )
        assert resolve_model("anthropic", "claude-sonnet-4-6") == "claude-sonnet-4-6"

    def test_explicit_id_with_non_anthropic_provider(self, monkeypatch):
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )
        assert resolve_model("openai", "gpt-4o") == "gpt-4o"

    def test_hyphenated_id_not_a_family(self, monkeypatch):
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not fetch")),
        )
        # "claude-opus-4-8" contains "opus" as a substring but is NOT a bare
        # family token — it must pass through unchanged.
        assert resolve_model("anthropic", "claude-opus-4-8") == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# resolve_model — family token resolution
# ---------------------------------------------------------------------------


class TestFamilyTokenResolution:
    """Family tokens resolve to the newest served model in that family."""

    def test_sonnet_returns_newest_sonnet(self, monkeypatch):
        """'sonnet' → newest sonnet model by created_at, not the older one."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_LIST,
        )
        result = resolve_model("anthropic", "sonnet")
        assert result == "claude-sonnet-4-6", (
            f"Expected newest sonnet 'claude-sonnet-4-6' but got {result!r}. "
            "Resolver may be using list order instead of release_date."
        )

    def test_opus_returns_correct_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_LIST,
        )
        assert resolve_model("anthropic", "opus") == "claude-opus-4-8"

    def test_haiku_returns_correct_model(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_LIST_WITH_HAIKU,
        )
        assert resolve_model("anthropic", "haiku") == "claude-haiku-4-5"

    def test_family_token_case_insensitive(self, monkeypatch):
        """'SONNET' (uppercase) should resolve the same as 'sonnet'."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_LIST,
        )
        assert resolve_model("anthropic", "SONNET") == "claude-sonnet-4-6"

    def test_result_is_cached(self, monkeypatch):
        """Second call for the same (provider, family) must not fetch again."""
        call_count = [0]

        def counting_fetch(*a, **kw):
            call_count[0] += 1
            return _MOCK_LIST

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models", counting_fetch
        )

        resolve_model("anthropic", "sonnet")
        resolve_model("anthropic", "sonnet")

        assert call_count[0] == 1, f"Expected 1 fetch call but got {call_count[0]}"


# ---------------------------------------------------------------------------
# resolve_model — fail-loud cases
# ---------------------------------------------------------------------------


class TestFailLoud:
    """Resolver raises clear errors rather than silently falling back."""

    def test_empty_family_raises_value_error(self, monkeypatch):
        """A family with zero matching models must raise ValueError (not return None)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_LIST,  # No haiku in _MOCK_LIST
        )
        with pytest.raises(ValueError, match="haiku"):
            resolve_model("anthropic", "haiku")

    def test_non_anthropic_provider_with_family_raises(self):
        """Family tokens for non-anthropic providers must raise ValueError immediately."""
        with pytest.raises(ValueError, match="anthropic"):
            resolve_model("openai", "sonnet")

    def test_missing_api_key_raises_runtime_error(self, monkeypatch):
        """Missing ANTHROPIC_API_KEY must raise RuntimeError before any network call."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            resolve_model("anthropic", "sonnet")


# ---------------------------------------------------------------------------
# resolve_model — deterministic tiebreak
# ---------------------------------------------------------------------------


class TestTiebreak:
    """When two models share the same created_at date, tiebreak by id descending."""

    def test_tiebreak_deterministic(self, monkeypatch):
        """Two models with the same date → the lexicographically LARGER id wins."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_TIED,
        )
        result = resolve_model("anthropic", "sonnet")
        # "claude-sonnet-4-7" > "claude-sonnet-4-6" lexicographically
        assert result == "claude-sonnet-4-7", (
            f"Expected 'claude-sonnet-4-7' (larger id) for tiebreak, got {result!r}"
        )

    def test_tiebreak_is_stable(self, monkeypatch):
        """Running the same resolve twice with the same list returns the same id."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: _MOCK_TIED,
        )
        first = resolve_model("anthropic", "sonnet")
        _clear_cache()
        second = resolve_model("anthropic", "sonnet")
        assert first == second

    def test_missing_date_sorts_last(self, monkeypatch):
        """A model with no created_at date should lose to any model that has a date."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        dateless = [
            {
                "id": "claude-sonnet-nodatex",
                "created_at": "",
                "display_name": "Claude Sonnet nodate",
            },
            {
                "id": "claude-sonnet-4-6",
                "created_at": "2026-05-01T00:00:00Z",
                "display_name": "Claude Sonnet 4.6",
            },
        ]
        monkeypatch.setattr(
            "wiki_weaver.model_resolver._fetch_anthropic_models",
            lambda *a, **kw: dateless,
        )
        assert resolve_model("anthropic", "sonnet") == "claude-sonnet-4-6"
