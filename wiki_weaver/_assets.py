"""Pipeline asset resolution -- single source of truth for both install layouts.

The ``pipeline/`` directory (validate_wiki.py, normalize_links.py, footnotes.py,
SCHEMA.md, CONVERGENCE_RUBRIC.md, and the *.dot pipelines) ships in two
different shapes depending on how wiki-weaver was installed:

* **Real wheel install** (``uv tool install`` / ``pip install``): pyproject's
  ``[tool.hatch.build.targets.wheel.force-include]`` maps ``"pipeline" =
  "wiki_weaver_pipeline"``, so the assets land at
  ``site-packages/wiki_weaver_pipeline/`` -- a SIBLING of the installed
  ``wiki_weaver/`` package (namespaced so it can't collide with some other
  project's top-level ``pipeline/``).

* **Dev / editable tree**: the assets live at the repo root ``pipeline/`` --
  a sibling of the ``wiki_weaver/`` package directory.

In BOTH cases the package dir is ``Path(__file__).parent`` and the assets are a
sibling of it; only the directory NAME differs. ``pipeline_dir()`` returns the
first candidate that exists, so every call site resolves correctly regardless of
install method. Editable installs masked this for a long time because the
repo-root ``pipeline/`` is always present in a dev checkout.
"""

from __future__ import annotations

from pathlib import Path

# The installed wheel ships assets under this namespaced sibling directory.
_WHEEL_PIPELINE_NAME = "wiki_weaver_pipeline"
# The dev/editable tree keeps them at the repo-root sibling directory.
_DEV_PIPELINE_NAME = "pipeline"


def pipeline_dir() -> Path:
    """Return the pipeline-asset directory for the active install layout.

    Resolution order (first existing wins):
      1. installed-wheel sibling  ``<site-packages>/wiki_weaver_pipeline/``
      2. dev/editable sibling     ``<repo-root>/pipeline/``

    Falls back to the dev path when neither exists so downstream "asset missing"
    errors name the path a developer expects.
    """
    pkg_parent = Path(__file__).resolve().parent.parent
    candidates = (
        pkg_parent / _WHEEL_PIPELINE_NAME,
        pkg_parent / _DEV_PIPELINE_NAME,
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[-1]
