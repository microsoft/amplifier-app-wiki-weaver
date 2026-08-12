"""wiki_weaver.synthesize -- the corpus-level theme-formation loop.

Closes the loop the gist describes (docs/llm-wiki-pattern.md L54/L52) and
FINDINGS.md \u00a77 documents as severed: "No step forms a whole-corpus view...
The exit condition is a queue-drain predicate... `overview.md` needs to
weigh, not log."

Two halves already existed and were never connected: ``lint.dot`` finds
gaps but has no write path (by design -- see its header); ``ask.dot`` can
file an answer back into the wiki but is never invoked except by a human
asking a question. ``pipeline/synthesize.dot`` is the missing wire: ONE
bounded LLM pass over ``wiki/index.md``/``wiki/overview.md``
(``scan_arguments``) finds arguments the corpus repeatedly contends but has
never given a page, ``wiki_weaver.synthesize.rank_candidates`` validates
and threshold-filters that list deterministically, a second corpus-scoped
LLM pass (``answer_gap``) answers the highest-mass candidate from the wiki
AND the raw sources (refusing rather than guessing), and a deterministic
writer commits it as a real page -- looping until no candidate remains
above threshold, or a bounded iteration cap is hit.

(An earlier version of the first pass was a pure n-gram/document-frequency
counter -- deterministic, no LLM. It was retired: counting recurring TEXT
cannot recover an argument the corpus makes in different words in
different places, which is exactly the property that makes a theme
"unnamed" in the first place. See ``rank_candidates.py``'s module
docstring for the full account.)
"""
