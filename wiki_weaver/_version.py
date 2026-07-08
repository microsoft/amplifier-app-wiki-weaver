"""Single source of truth for the wiki-weaver package version.

Kept as a leaf module with no imports so that any submodule can safely
import __version__ without risk of triggering a circular import through
the wiki_weaver package __init__.

This value is baked in at wheel-build time by the git-version hatchling
build hook (see hatch_build.py at the repo root) -- it is the commit date
+ short SHA of the exact commit this wheel was built from, not the build
date. Do not hand-edit; it is overwritten on the next build.
"""

__version__ = "2026.07.08-7c54cf4"
