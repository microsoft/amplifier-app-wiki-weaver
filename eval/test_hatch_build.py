"""Unit tests for the git-version hatchling build hook (hatch_build.py).

All tests mock subprocess.run -- no real git process is ever spawned. They
validate:

  - GitVersionBuildHook._compute_version: git success, git command failure,
    git binary missing, and empty-output edge cases.
  - GitVersionBuildHook.initialize: writes a baked _version.py on success;
    leaves the file untouched (with a build-time warning, never silent) on
    failure.
"""

from __future__ import annotations

import subprocess as _subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make hatch_build importable without installing (it's a top-level script at
# the repo root, not part of the wiki_weaver package).
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from hatch_build import GitVersionBuildHook  # noqa: E402


def _make_hook(root: Path) -> tuple[GitVersionBuildHook, MagicMock]:
    """Build a hook instance for testing, plus the MagicMock passed as `app`.

    Returning the mock separately (rather than reading it back via
    `hook.app`) keeps the mock's static type as MagicMock for assertions --
    the `app` property is typed as the real hatchling `Application`.
    """
    app = MagicMock()
    hook = GitVersionBuildHook(
        root=str(root),
        config={},
        build_config=None,  # type: ignore[arg-type]
        metadata=None,  # type: ignore[arg-type]
        directory=str(root),
        target_name="wheel",
        app=app,
    )
    return hook, app


# ---------------------------------------------------------------------------
# _compute_version
# ---------------------------------------------------------------------------


class TestComputeVersion:
    def test_success_returns_date_dash_sha(self, tmp_path):
        hook, _app = _make_hook(tmp_path)
        completed_date = MagicMock(stdout="2026.07.08\n")
        completed_sha = MagicMock(stdout="7c54cf4\n")
        with patch(
            "hatch_build.subprocess.run",
            side_effect=[completed_date, completed_sha],
        ) as run:
            result = hook._compute_version(tmp_path)
        assert result == "2026.07.08-7c54cf4"
        assert run.call_count == 2
        # Both calls must pin cwd to the build root explicitly.
        for call in run.call_args_list:
            assert call.kwargs["cwd"] == tmp_path

    def test_git_command_failure_returns_none(self, tmp_path):
        hook, _app = _make_hook(tmp_path)
        with patch(
            "hatch_build.subprocess.run",
            side_effect=_subprocess.CalledProcessError(128, ["git"]),
        ):
            assert hook._compute_version(tmp_path) is None

    def test_git_binary_missing_returns_none(self, tmp_path):
        hook, _app = _make_hook(tmp_path)
        with patch("hatch_build.subprocess.run", side_effect=FileNotFoundError()):
            assert hook._compute_version(tmp_path) is None

    def test_empty_date_output_returns_none(self, tmp_path):
        hook, _app = _make_hook(tmp_path)
        completed_date = MagicMock(stdout="\n")
        completed_sha = MagicMock(stdout="7c54cf4\n")
        with patch(
            "hatch_build.subprocess.run",
            side_effect=[completed_date, completed_sha],
        ):
            assert hook._compute_version(tmp_path) is None


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------


class TestInitialize:
    def test_success_writes_version_file_and_logs_info(self, tmp_path):
        pkg_dir = tmp_path / "wiki_weaver"
        pkg_dir.mkdir()
        version_file = pkg_dir / "_version.py"
        version_file.write_text('__version__ = "0.1.0"\n', encoding="utf-8")

        hook, app = _make_hook(tmp_path)
        with patch.object(hook, "_compute_version", return_value="2026.07.08-7c54cf4"):
            hook.initialize("0.1.0", {})

        content = version_file.read_text(encoding="utf-8")
        assert '__version__ = "2026.07.08-7c54cf4"' in content
        app.display_info.assert_called_once()
        app.display_warning.assert_not_called()

    def test_failure_leaves_file_untouched_and_warns(self, tmp_path):
        pkg_dir = tmp_path / "wiki_weaver"
        pkg_dir.mkdir()
        version_file = pkg_dir / "_version.py"
        original = '__version__ = "0.1.0"\n'
        version_file.write_text(original, encoding="utf-8")

        hook, app = _make_hook(tmp_path)
        with patch.object(hook, "_compute_version", return_value=None):
            hook.initialize("0.1.0", {})

        assert version_file.read_text(encoding="utf-8") == original
        app.display_warning.assert_called_once()
        app.display_info.assert_not_called()
