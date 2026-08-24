"""Regression tests: unicode-escape-in-prose defect (json.dumps ensure_ascii).

ROOT CAUSE: several ``json.dumps(...)`` calls in the synthesize/ask pipeline
serialize CONTENT-BEARING payloads -- real prose (an LLM-authored
term/claim, a user's own question text, a wiki page title) -- at Python's
default ``ensure_ascii=True``. The files those calls write are then read
RAW, byte-for-byte, by a downstream LLM box using its own file-read tool
(``answer_gap``, ``attribute_sources``, ``ask.dot``'s ``answer`` node) --
never through this codebase's own ``json.loads``. An em-dash/arrow/curly-
quote in that prose was therefore serialized as the literal 6-character
escape sequence ``\\u2014`` on disk, and the LLM reading the file verbatim
could copy that literal escape text into wiki page prose (the observed
defect: generated pages carrying literal ``\\uXXXX`` sequences instead of
the real character).

Each test below feeds a fixture whose prose contains an em-dash, an arrow,
and a curly quote through the exact tool that writes the file, then asserts
the ON-DISK bytes contain the real characters -- not their ``\\uXXXX``
escapes -- and that the value still round-trips correctly through
``json.loads``.

Files verified here (the content-bearing, LLM-raw-read sites -- see the bug
report for the full survey of what was checked and ruled OUT as
metadata-only, e.g. ``current_slice.json``'s page paths/BM25 scores):

  - wiki_weaver.synthesize.select_gap        -> .ai/current_gap.json
  - wiki_weaver.synthesize.retrieve_context   -> .ai/gap-context.json
  - wiki_weaver.synthesize.attribute_select   -> .ai/current-attribution-candidate.json
  - wiki_weaver.ask.load_index                -> .ai/candidate-pages.json
"""

from __future__ import annotations

import json

from wiki_weaver.ask import load_index
from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import attribute_select, retrieve_context, select_gap

EM_DASH = "\u2014"  # —
ARROW = "\u2192"  # →
CURLY_QUOTE = "\u2019"  # ’

UNICODE_CLAIM = (
    f"Session state costs more{EM_DASH} it should be dropped{ARROW}simplified, per the team{CURLY_QUOTE}s own findings."
)

PAGE_TEMPLATE = """---
title: {title}
type: concept
---

# {title}

{body}
"""


def _assert_no_raw_escapes(on_disk: str) -> None:
    """The literal 6-char escape sequences must never appear on disk --
    only the real characters."""
    assert "\\u2014" not in on_disk, f"literal \\u2014 escape leaked into {on_disk!r}"
    assert "\\u2192" not in on_disk, f"literal \\u2192 escape leaked into {on_disk!r}"
    assert "\\u2019" not in on_disk, f"literal \\u2019 escape leaked into {on_disk!r}"


def _wr(wiki_root) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    return wr


def test_select_gap_preserves_unicode_in_current_gap_json(wiki_root):
    """select_gap.py writes .ai/current_gap.json -- read RAW by answer_gap
    (pipeline/synthesize.dot's LLM box: "Read .ai/current_gap.json for the
    exact term/phrase you are answering") with its own file tools."""
    wr = _wr(wiki_root)
    wr.gap_candidates_file.write_text(
        json.dumps(
            [
                {
                    "term": "session state costs",
                    "claim": UNICODE_CLAIM,
                    "source_ids": ["001-test.md"],
                    "source_count": 3,
                }
            ]
        ),
        encoding="utf-8",
    )

    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0

    on_disk = wr.current_gap_file.read_text(encoding="utf-8")
    assert EM_DASH in on_disk
    assert ARROW in on_disk
    assert CURLY_QUOTE in on_disk
    _assert_no_raw_escapes(on_disk)

    decoded = json.loads(on_disk)
    assert decoded["claim"] == UNICODE_CLAIM


def test_retrieve_context_preserves_unicode_in_gap_context_json(wiki_root):
    """retrieve_context.py writes .ai/gap-context.json -- read RAW by
    answer_gap ("Read .ai/gap-context.json for a short list of existing
    wiki pages (related_pages)"). Both the gap's own term AND a related
    page's title can carry real prose."""
    wr = _wr(wiki_root)
    wiki_root.add_page(
        "related.md",
        PAGE_TEMPLATE.format(
            title=f"Costs{EM_DASH}A Deep Dive",
            body="session state costs and tradeoffs discussed at length here.",
        ),
    )
    wr.current_gap_file.write_text(
        json.dumps(
            {
                "term": UNICODE_CLAIM,
                "source_count": 1,
                "source_ids": ["001-test.md"],
            }
        ),
        encoding="utf-8",
    )

    code = retrieve_context.main(["--wiki-root", str(wr.root)])
    assert code == 0

    on_disk = wr.gap_context_file.read_text(encoding="utf-8")
    assert EM_DASH in on_disk
    assert ARROW in on_disk
    assert CURLY_QUOTE in on_disk
    _assert_no_raw_escapes(on_disk)

    decoded = json.loads(on_disk)
    assert decoded["term"] == UNICODE_CLAIM


def test_attribute_select_preserves_unicode_in_current_attribution_candidate_json(wiki_root):
    """attribute_select.py writes .ai/current-attribution-candidate.json --
    read RAW by attribute_sources ("read ONLY
    .ai/current-attribution-candidate.json ... its 'term' and 'claim'
    fields")."""
    wr = _wr(wiki_root)
    wr.gap_candidates_raw_file.write_text(
        json.dumps(
            [
                {
                    "term": "session state costs",
                    "claim": UNICODE_CLAIM,
                    "source_ids": ["001-test.md"],
                }
            ]
        ),
        encoding="utf-8",
    )

    code = attribute_select.main(["--wiki-root", str(wr.root)])
    assert code == 0

    on_disk = wr.current_attribution_candidate_file.read_text(encoding="utf-8")
    assert EM_DASH in on_disk
    assert ARROW in on_disk
    assert CURLY_QUOTE in on_disk
    _assert_no_raw_escapes(on_disk)

    decoded = json.loads(on_disk)
    assert decoded["claim"] == UNICODE_CLAIM


def test_load_index_preserves_unicode_in_candidate_pages_json(wiki_root, monkeypatch):
    """load_index.py writes .ai/candidate-pages.json -- read RAW by ask.dot's
    answer node ("Read .ai/candidate-pages.json with your file tools").
    Both the user's own question text and a page title can carry real
    prose."""
    wr = _wr(wiki_root)
    wiki_root.add_page(
        "page0.md",
        PAGE_TEMPLATE.format(
            title=f"Costs{EM_DASH}A Deep Dive",
            body="session state costs and tradeoffs discussed at length here.",
        ),
    )
    monkeypatch.setenv("QUESTION", UNICODE_CLAIM)

    code = load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])
    assert code == 0

    out_path = wr.root / ".ai" / "candidate-pages.json"
    on_disk = out_path.read_text(encoding="utf-8")
    assert EM_DASH in on_disk
    assert ARROW in on_disk
    assert CURLY_QUOTE in on_disk
    _assert_no_raw_escapes(on_disk)

    decoded = json.loads(on_disk)
    assert decoded["question"] == UNICODE_CLAIM
