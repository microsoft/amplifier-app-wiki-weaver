"""wiki-weaver v2 -- the ingest path.

One LLM pass per source, everything else deterministic. See
``pipeline/ingest.dot`` and ``pipeline/CLI-CONTRACT.md`` for the contract
this package implements, and ``docs/DESIGN.md`` / ``docs/FLOW.md`` for why.
"""

from __future__ import annotations

__all__: list[str] = []
