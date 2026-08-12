"""wiki_weaver.ingest -- the 9 tool subcommands behind pipeline/ingest.dot.

Fold exactly one source into the wiki per loop iteration. Every step here
is deterministic tool code; the LLM (``weave``, not part of this package)
is invoked once per source. See pipeline/CLI-CONTRACT.md for the exact
per-subcommand contract and pipeline/ingest.dot for how they compose.
"""

from __future__ import annotations

__all__: list[str] = []
