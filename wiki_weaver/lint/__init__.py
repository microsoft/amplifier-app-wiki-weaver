"""wiki_weaver.lint -- the 2 tool subcommands behind pipeline/lint.dot.

Periodic, read-only health check: deterministic structural validation
first, then a formatted report. lint never edits the wiki -- fixes flow
through correct.dot or canon.dot, both explicit human/proxy-gated actions.
See pipeline/CLI-CONTRACT.md for the exact per-subcommand contract and
pipeline/lint.dot for how they compose.
"""

from __future__ import annotations

__all__: list[str] = []
