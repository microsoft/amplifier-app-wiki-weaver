# Citations Page

This page simulates the PRE-#25 renderer regression: the registry captures
`author` + `url` for every source (see `.sources.json`), but the compiled
footnote definitions below render only a bare de-slugged filename with no
attribution at all -- exactly what `pipeline/footnotes.py` produced before
`_render_provenance_def` started consuming the captured provenance.

Context window limits are discussed by one source.[^1] Tool-use patterns are
covered by another.[^2] A third piece addresses prompting.[^3] Multi-agent
coordination is surveyed elsewhere.[^4] Retrieval-augmented generation
trade-offs are reviewed too.[^5]

## Sources

[^1]: article one
[^2]: article two
[^3]: article three
[^4]: article four
[^5]: article five
