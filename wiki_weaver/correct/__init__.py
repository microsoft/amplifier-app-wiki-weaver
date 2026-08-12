"""wiki_weaver.correct -- the correction -> targeted reprocess loop.

Implements the CLI subcommands ``pipeline/correct.dot`` invokes (see
``pipeline/CLI-CONTRACT.md``'s ``correct.dot`` section):

- ``capture``       -- freeform correction text -> ``.ai/correction-text.md``
- ``locate_pages``  -- which wiki pages assert the corrected claim
- ``trace_sources`` -- which raw source ids fed those pages
- ``validate``      -- structural checks (delegates to ``wiki_weaver.ingest.validate``)
- ``persist_lens``  -- THE load-bearing step: write ``lens/corrections/<id>.md``

See ``persist_lens``'s module docstring for why this is the step that makes
a correction durable rather than a one-time patch to today's wiki text.
"""

from __future__ import annotations
