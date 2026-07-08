"""Hatchling build hook: bakes a git-derived version string into the wheel.

wiki-weaver is installed via ``uv tool install git+https://...`` -- a
git-source install. When uv/pip install from a VCS URL, they clone the repo
to a temp directory and invoke the build backend (hatchling) THERE, where
full ``.git`` history is present. The resulting wheel/site-packages does NOT
ship ``.git``, so ``--version`` cannot query git live at runtime.

This hook runs during the wheel build (while ``.git`` IS present), computes
``<commit-date>-<short-sha>`` for HEAD, and overwrites
``wiki_weaver/_version.py`` with that value before the build packages files
-- so the baked value ships inside the wheel. At runtime, ``--version`` just
reads the already-baked value: no git needed, no network, deterministic.

The date is the COMMIT's date, not "the day someone happened to build it" --
deliberate: two people building from the identical commit must get the
identical version string, matching this repo's "track @main, fix-forward,
no SHA pinning" versioning philosophy (see AGENTS.md / wiki_weaver/updater.py).

On failure (git commands fail -- e.g. building from an sdist with no
``.git``, or git genuinely unavailable), the existing on-disk
``_version.py`` ships untouched. This is a clearly-labeled fallback (a
build-time warning is printed via the hatchling ``app``), never a silent
success look-alike.

Intentionally self-contained: this file does NOT import from the
``wiki_weaver`` package. The package being built is not guaranteed
importable at hook time, and importing a partially-built package from its
own build hook is a fragile dependency to take on for ~15 lines of logic.
The runtime dev-mode fallback (``wiki_weaver/_version_resolve.py``) uses the
same two git commands independently -- see that module's docstring.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_VERSION_FILE_RELATIVE = Path("wiki_weaver") / "_version.py"


class GitVersionBuildHook(BuildHookInterface):
    """Overwrites wiki_weaver/_version.py with a git-derived version at build time."""

    PLUGIN_NAME = "git-version"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Runs immediately before each build. See module docstring for rationale."""
        computed = self._compute_version(Path(self.root))
        if computed is None:
            self.app.display_warning(
                "git-version build hook: could not compute a git-derived version "
                f"(no .git history or git unavailable at {self.root}) -- shipping "
                "the existing on-disk wiki_weaver/_version.py unchanged."
            )
            return

        version_path = Path(self.root) / _VERSION_FILE_RELATIVE
        version_path.write_text(
            '"""Single source of truth for the wiki-weaver package version.\n'
            "\n"
            "Kept as a leaf module with no imports so that any submodule can safely\n"
            "import __version__ without risk of triggering a circular import through\n"
            "the wiki_weaver package __init__.\n"
            "\n"
            "This value is baked in at wheel-build time by the git-version hatchling\n"
            "build hook (see hatch_build.py at the repo root) -- it is the commit date\n"
            "+ short SHA of the exact commit this wheel was built from, not the build\n"
            "date. Do not hand-edit; it is overwritten on the next build.\n"
            '"""\n'
            "\n"
            f'__version__ = "{computed}"\n',
            encoding="utf-8",
        )
        self.app.display_info(f"git-version build hook: baked __version__ = {computed}")

    @staticmethod
    def _compute_version(root: Path) -> str | None:
        """Return ``<commit-date:%Y.%m.%d>-<short-sha>`` for HEAD, or None on failure.

        Runs both git commands with ``cwd=root`` explicitly (never relies on
        process cwd, since a build can be invoked from anywhere).
        """
        try:
            date = subprocess.run(  # noqa: S603
                ["git", "log", "-1", "--format=%cd", "--date=format:%Y.%m.%d"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            sha = subprocess.run(  # noqa: S603
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return None
        if not date or not sha:
            return None
        return f"{date}-{sha}"
