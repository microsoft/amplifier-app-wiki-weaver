"""Unit tests for wiki_weaver._version_resolve -- the runtime --version reader.

Validates:

  - A baked value matching the build hook's own output pattern is used as-is
    -- no git call attempted at all.
  - A non-matching (placeholder) value triggers a live git query, mirroring
    the build hook's own two commands, scoped to the given root.
  - When both the baked value AND the live git query are unavailable, the
    labeled UNKNOWN string is returned -- never a fabricated version.
"""

from __future__ import annotations

import subprocess as _subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver._version_resolve import UNKNOWN, resolve_version  # noqa: E402


# ---------------------------------------------------------------------------
# Baked value already matches the pattern -> used as-is, no git call
# ---------------------------------------------------------------------------


class TestResolveVersionBakedValue:
    def test_baked_value_used_as_is_no_git_call(self):
        with patch("wiki_weaver._version_resolve.subprocess.run") as run:
            result = resolve_version("2026.07.08-7c54cf4")
        assert result == "2026.07.08-7c54cf4"
        run.assert_not_called()

    def test_baked_value_with_full_length_sha_used_as_is(self):
        full_sha = "a" * 40
        with patch("wiki_weaver._version_resolve.subprocess.run") as run:
            result = resolve_version(f"2026.07.08-{full_sha}")
        assert result == f"2026.07.08-{full_sha}"
        run.assert_not_called()


# ---------------------------------------------------------------------------
# Placeholder value (doesn't match pattern) -> live git fallback attempted
# ---------------------------------------------------------------------------


class TestResolveVersionPlaceholderFallback:
    def test_placeholder_triggers_live_git_query(self, tmp_path):
        completed_date = MagicMock(stdout="2026.07.08\n")
        completed_sha = MagicMock(stdout="7c54cf4\n")
        with patch(
            "wiki_weaver._version_resolve.subprocess.run",
            side_effect=[completed_date, completed_sha],
        ) as run:
            result = resolve_version("0.1.0", root=tmp_path)
        assert result == "2026.07.08-7c54cf4"
        assert run.call_count == 2
        for call in run.call_args_list:
            assert call.kwargs["cwd"] == tmp_path

    def test_placeholder_and_git_binary_missing_returns_labeled_unknown(self, tmp_path):
        with patch(
            "wiki_weaver._version_resolve.subprocess.run",
            side_effect=FileNotFoundError(),
        ):
            result = resolve_version("0.1.0", root=tmp_path)
        assert result == UNKNOWN

    def test_placeholder_and_git_command_error_returns_labeled_unknown(self, tmp_path):
        with patch(
            "wiki_weaver._version_resolve.subprocess.run",
            side_effect=_subprocess.CalledProcessError(128, ["git"]),
        ):
            result = resolve_version("0.1.0", root=tmp_path)
        assert result == UNKNOWN

    def test_placeholder_and_empty_git_output_returns_labeled_unknown(self, tmp_path):
        completed_empty = MagicMock(stdout="\n")
        with patch(
            "wiki_weaver._version_resolve.subprocess.run",
            side_effect=[completed_empty, completed_empty],
        ):
            result = resolve_version("0.1.0", root=tmp_path)
        assert result == UNKNOWN
