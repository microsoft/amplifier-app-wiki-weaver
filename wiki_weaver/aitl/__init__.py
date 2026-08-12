"""wiki_weaver.aitl -- the agent-in-the-loop (AITL) proxy.

Answers human-gate (hexagon) questions on the human's behalf, in character,
driven by a persistent, editable character document (``lens/persona.md`` --
see ``wiki_weaver.aitl.persona``). Fails loud rather than inventing an
answer whenever it lacks a grounded basis to answer -- see
``wiki_weaver.aitl.proxy_interviewer``.

See docs/DESIGN.md Sec 7 ("AITL -- the agent proxy") for the design intent
this package implements, and pipeline/CLI-CONTRACT.md's "AITL proxy" section
for the CLI contract.
"""

from __future__ import annotations
