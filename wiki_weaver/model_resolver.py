"""Runtime model resolver for wiki-weaver.

Resolves family tokens ("opus", "sonnet", "haiku") to the newest served model id
in that family by querying the provider's live model list at runtime.  Explicit
model ids (e.g. "claude-sonnet-4-6") are returned unchanged — no network call.

Design goals
------------
- Zero-maintenance: users specify a family name and always get the newest model
  the provider *actually serves* — no version-pin to keep up to date.
- Fail loud: network failure, missing API key, or a family that matches zero
  served models raises a clear, actionable error.  No silent fallback to a
  hardcoded id.
- Process-level cache: resolution per (provider, family) happens at most once
  per process run, so a long ingest loop pays one round-trip per family.

Supported providers for family-token resolution
------------------------------------------------
Only "anthropic" supports family tokens today.  For other providers the caller
must pass an explicit model id; a family token raises ValueError.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

# Known family tokens (case-insensitive substring match against model id /
# display_name returned by the provider's live list).
KNOWN_FAMILIES: frozenset[str] = frozenset({"opus", "sonnet", "haiku"})

# Process-level cache: (provider, family_token) -> concrete_model_id
# Populated lazily; cleared by tests via _clear_cache().
_CACHE: dict[tuple[str, str], str] = {}


def _clear_cache() -> None:
    """Clear the process-level resolution cache.  Used by tests."""
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Internal: fetch from Anthropic
# ---------------------------------------------------------------------------


def _fetch_anthropic_models(base_url: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch the live model list from the Anthropic API.

    Returns a list of dicts with at minimum the keys ``"id"`` and
    ``"created_at"`` (ISO 8601 string, may be empty).

    Raises RuntimeError on network / auth failure.

    Strategy: prefer the ``anthropic`` SDK if available (richer type info);
    fall back to a raw ``urllib`` GET so the package has no hard dep on the SDK.
    """
    # --- SDK path ---
    try:
        from anthropic import Anthropic  # type: ignore[import-not-found]

        client = Anthropic(api_key=api_key, base_url=base_url)
        page = client.models.list()
        return [
            {
                "id": m.id,
                "created_at": str(getattr(m, "created_at", "") or ""),
                "display_name": str(getattr(m, "display_name", m.id) or m.id),
            }
            for m in page.data
        ]
    except ImportError:
        pass  # Fall through to urllib

    # --- urllib fallback ---
    import json
    import urllib.request

    url = base_url.rstrip("/") + "/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch model list from {url!r}: {exc}") from exc

    return [
        {
            "id": str(m.get("id", "")),
            "created_at": str(m.get("created_at", "") or ""),
            "display_name": str(m.get("display_name", "") or m.get("id", "")),
        }
        for m in body.get("data", [])
    ]


# ---------------------------------------------------------------------------
# Internal: date parsing
# ---------------------------------------------------------------------------


def _parse_created_at(raw: Any) -> date | None:
    """Parse an ISO 8601 ``created_at`` value to a ``date``.

    Returns ``None`` on any parse failure so callers can sort those models
    last without crashing.
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Try the most specific format first, then fall back to shorter forms.
    # All formats are tried against the full string (strptime requires an exact
    # match — do not slice by format length, which gives the wrong character count).
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # Last-resort: grab the 10-char date prefix (handles timestamps with sub-second
    # precision or non-standard suffixes like "+00:00").
    if len(s) >= 10:
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_model(provider: str, spec: str) -> str:
    """Resolve *spec* to a concrete served model id for *provider*.

    Parameters
    ----------
    provider:
        Provider name (e.g. ``"anthropic"``).
    spec:
        Either a family token (``"opus"``, ``"sonnet"``, ``"haiku"``) or an
        explicit model id (``"claude-sonnet-4-6"``).

    Returns
    -------
    str
        Concrete model id that the provider currently serves.

    Raises
    ------
    ValueError
        * Family token used with a provider that doesn't support them.
        * Family token matches zero models in the live list.
    RuntimeError
        * Live model list can't be fetched (network / auth failure).
        * ANTHROPIC_API_KEY is not set when a family token is requested.
    """
    family = spec.strip().lower()

    # Explicit id — no network call needed.
    if family not in KNOWN_FAMILIES:
        return spec

    # Only anthropic supports family tokens today.
    if provider != "anthropic":
        raise ValueError(
            f"Family tokens ({spec!r}) are only supported for provider='anthropic' today. "
            f"Pass an explicit model id for provider={provider!r}."
        )

    cache_key = (provider, family)
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    # Require an API key (can't resolve without one).
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            f"Cannot resolve family token {spec!r}: ANTHROPIC_API_KEY is not set. "
            "Set the environment variable or pass an explicit model id."
        )

    raw_models = _fetch_anthropic_models(base_url, api_key)

    # Filter: id or display_name contains the family token (case-insensitive).
    candidates = [
        m
        for m in raw_models
        if family in m.get("id", "").lower()
        or family in m.get("display_name", "").lower()
    ]
    if not candidates:
        served_ids = [m.get("id") for m in raw_models]
        raise ValueError(
            f"No served {provider!r} models match family {spec!r}. "
            f"Models available from {base_url}: {served_ids}. "
            "Pass an explicit model id if this family is not available at your endpoint."
        )

    # Sort: newest first (date DESC), then id DESC for deterministic tiebreak.
    def _sort_key(m: dict[str, Any]) -> tuple[date, str]:
        d = _parse_created_at(m.get("created_at"))
        return (d if d is not None else date.min, m.get("id", ""))

    candidates.sort(key=_sort_key, reverse=True)
    best = candidates[0]["id"]

    _CACHE[cache_key] = best
    return best
