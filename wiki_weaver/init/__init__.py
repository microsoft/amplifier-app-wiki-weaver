"""wiki_weaver.init -- the 4 tool subcommands behind pipeline/init.dot.

Interview a human (or proxy) until there is enough signal to write
AGENTS.md and seed lens/persona.md + lens/canon/, then persist. See
pipeline/CLI-CONTRACT.md for the exact per-subcommand contract and
pipeline/init.dot for how they compose.
"""

from __future__ import annotations

__all__: list[str] = []
