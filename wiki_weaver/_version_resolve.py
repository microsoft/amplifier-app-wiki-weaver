"""Runtime version resolution -- the reader half of the git-derived version.

wiki-weaver's build-time hatchling hook (``hatch_build.py`` at the repo root)
bakes a git-derived version string into ``wiki_weaver/_version.py`` before a
wheel is built (see that file's docstring for the full rationale: the
package is installed from a git source, ``.git`` is present at build time
but never ships in the wheel, so the value must be computed once, at build,
and read as a plain string at runtime).

This module is the RUNTIME reader that ``--version`` calls. Two situations
exist:

1. **Installed from a wheel** (incl. ``uv tool install git+...``): the build
   hook already baked a real value into ``_version.py`` matching
   ``YYYY.MM.DD-<short-sha>`` -- use it as-is. No git call, no network.
2. **Local dev checkout run directly** (``uv run wiki-weaver --version``,
   editable/dev-mode execution): the build hook never ran, so ``_version.py``
   still holds the static placeholder (``0.1.0``). In this case ONLY, fall
   back to a LIVE git query against the current working tree -- the exact
   same two git commands the build hook itself runs.

If neither path yields a real value (no baked value, AND git is unavailable
or this isn't a git checkout -- e.g. truly installed from a wheel with a
stale/missing baked value), return a clearly-labeled "unknown" string.
Never silently reuse or fabricate a version-looking string -- fail loud and
labeled, per this repo's fallback discipline.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

# Matches the build hook's own output format: YYYY.MM.DD-<7+ hex chars>.
# `git rev-parse --short` defaults to 7 chars but auto-lengthens to stay
# unique in large repos, so the sha group is unbounded up to a full 40-char
# SHA.
_BAKED_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7,40}$")

UNKNOWN = "unknown (no baked version found, git unavailable)"

# wiki_weaver/_version_resolve.py -> wiki_weaver/ -> repo root
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent


def _git_derived_version(root: Path) -> Optional[str]:
    """Run the same two git commands the build hook uses; None on any failure."""
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


def resolve_version(baked: str, *, root: Optional[Path] = None) -> str:
    """Return the version string to display for ``--version``.

    Args:
        baked: the on-disk ``wiki_weaver.__version__`` value, as imported
            from ``_version.py``.
        root: working tree to query git in when ``baked`` doesn't match the
            build-hook-baked pattern. Defaults to this repo checkout's root
            (two parents up from this file).

    Returns:
        ``baked`` unchanged if it already matches the baked pattern; else a
        freshly computed live-git value; else the labeled ``UNKNOWN`` string.
    """
    if _BAKED_PATTERN.match(baked):
        return baked

    live = _git_derived_version(root if root is not None else _DEFAULT_ROOT)
    if live is not None:
        return live

    return UNKNOWN
