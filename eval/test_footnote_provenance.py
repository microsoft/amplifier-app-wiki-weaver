"""Unit tests for pipeline/footnotes.py -- author+URL provenance rendering
(evolution-plan Item 3, Phase A: deepen provenance).

The registry (`.sources.json`) already captures `author`/`url`/`date` per
source when the original source had that metadata (see
`wiki_weaver/lib.py::_assign_source_id`). Before this change, the footnote
renderer discarded those fields and fell back to a de-slugged filename.

Covers:
  - `_load_sources()` surfaces the id -> provenance map alongside id -> filename
  - `_render_provenance_def()` graceful degradation across all four cases
    (author+url, author-only, url-only, neither)
  - regression guard: the "neither" case is byte-identical to the prior
    (pre-this-change) fallback behavior
  - end-to-end `footnote_citations()` backfill renders provenance into the
    actual `[^N]:` def line written to a page
  - idempotency: a second run makes no further changes
  - a real-artifact check against the frozen `runs/corpus/wiki/.sources.json`
    registry (746 sources, 601 with author+url) -- skipped gracefully if that
    corpus isn't present in this checkout
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Insert the repo root so we can import pipeline.footnotes without installing.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from pipeline.footnotes import (  # noqa: E402
    _filename_to_title,
    _load_sources,
    _render_provenance_def,
    footnote_citations,
)

# ---------------------------------------------------------------------------
# Group 1 -- _load_sources() provenance surfacing
# ---------------------------------------------------------------------------


def _write_registry(path: Path, sources: list[dict]) -> None:
    path.write_text(json.dumps({"version": 1, "sources": sources}), encoding="utf-8")


class TestLoadSourcesProvenance:
    def test_full_provenance_surfaced(self, tmp_path: Path) -> None:
        reg = tmp_path / ".sources.json"
        _write_registry(
            reg,
            [
                {
                    "id": 1,
                    "filename": "some_article.md",
                    "hash": "abc",
                    "author": "Jane Doe",
                    "url": "https://example.com/a",
                    "date": "2024-05-01",
                }
            ],
        )
        valid_ids, id_to_filename, id_to_provenance = _load_sources(reg)
        assert valid_ids == {"1"}
        assert id_to_filename["1"] == "some_article.md"
        assert id_to_provenance["1"] == {
            "author": "Jane Doe",
            "url": "https://example.com/a",
            "date": "2024-05-01",
        }

    def test_missing_fields_omitted_not_fabricated(self, tmp_path: Path) -> None:
        reg = tmp_path / ".sources.json"
        _write_registry(
            reg,
            [{"id": 1, "filename": "no_meta.md", "hash": "abc"}],
        )
        valid_ids, id_to_filename, id_to_provenance = _load_sources(reg)
        assert valid_ids == {"1"}
        # No provenance fields present -> id absent from the map entirely.
        assert "1" not in id_to_provenance

    def test_partial_provenance_author_only(self, tmp_path: Path) -> None:
        reg = tmp_path / ".sources.json"
        _write_registry(
            reg,
            [{"id": 1, "filename": "x.md", "hash": "abc", "author": "Alice Chen"}],
        )
        _, _, id_to_provenance = _load_sources(reg)
        assert id_to_provenance["1"] == {"author": "Alice Chen"}

    def test_absent_registry_returns_all_empty(self, tmp_path: Path) -> None:
        valid_ids, id_to_filename, id_to_provenance = _load_sources(
            tmp_path / "nope.json"
        )
        assert valid_ids == set()
        assert id_to_filename == {}
        assert id_to_provenance == {}


# ---------------------------------------------------------------------------
# Group 2 -- _render_provenance_def() graceful degradation
# ---------------------------------------------------------------------------


class TestRenderProvenanceDef:
    def test_author_and_url(self) -> None:
        id_to_filename = {"1": "some_great_article.md"}
        id_to_provenance = {"1": {"author": "Jane Doe", "url": "https://example.com/a"}}
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert "Jane Doe" in text
        assert "https://example.com/a" in text
        assert _filename_to_title("some_great_article.md") in text  # title present
        # No dangling punctuation artifacts.
        assert ".." not in text
        assert not text.endswith(".")

    def test_author_only_no_dangling_url_stub(self) -> None:
        id_to_filename = {"1": "some_article.md"}
        id_to_provenance = {"1": {"author": "Alice Chen"}}
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert "Alice Chen" in text
        assert "http" not in text
        assert not text.rstrip().endswith("—")  # no trailing dangling separator

    def test_url_only_no_author_stub(self) -> None:
        id_to_filename = {"1": "some_article.md"}
        id_to_provenance = {"1": {"url": "https://example.com/b"}}
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert "https://example.com/b" in text
        # No "Author." style stub -- there is no bare leading attribution
        # segment before the title when author is absent.
        assert not text.startswith("None")
        assert "Author" not in text

    def test_neither_matches_prior_fallback_exactly(self) -> None:
        """Regression guard: with no provenance captured, output must be
        byte-identical to the pre-existing de-slugged-filename fallback."""
        id_to_filename = {"1": "some_great_article.md"}
        id_to_provenance: dict[str, dict[str, str]] = {}
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert text == _filename_to_title("some_great_article.md")
        assert text == "some great article"

    def test_neither_no_filename_falls_back_to_source_n(self) -> None:
        text = _render_provenance_def("42", {}, {})
        assert text == "Source 42"

    def test_date_appended_when_present(self) -> None:
        id_to_filename = {"1": "article.md"}
        id_to_provenance = {
            "1": {
                "author": "Jane Doe",
                "url": "https://example.com/a",
                "date": "2024-05-01",
            }
        }
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert text.endswith("(2024-05-01)")

    def test_date_only_no_author_no_url_ignored(self) -> None:
        """date alone (no author/url) is not enough to escape the neither
        fallback -- date is a decoration on an attribution, not a citation
        by itself."""
        id_to_filename = {"1": "article.md"}
        id_to_provenance = {"1": {"date": "2024-05-01"}}
        text = _render_provenance_def("1", id_to_filename, id_to_provenance)
        assert text == _filename_to_title("article.md")


# ---------------------------------------------------------------------------
# Group 3 -- end-to-end footnote_citations() backfill with provenance
# ---------------------------------------------------------------------------


class TestFootnoteCitationsProvenanceEndToEnd:
    def _make_wiki(self, tmp_path: Path) -> Path:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        return wiki

    def test_backfill_renders_author_and_url(self, tmp_path: Path) -> None:
        wiki = self._make_wiki(tmp_path)
        _write_registry(
            wiki / ".sources.json",
            [
                {
                    "id": 1,
                    "filename": "some_article.md",
                    "hash": "abc",
                    "author": "Jane Doe",
                    "url": "https://example.com/a",
                }
            ],
        )
        page = wiki / "topic.md"
        page.write_text(
            "# Topic\n\nSome claim cited here [^1].\n\n## Sources\n",
            encoding="utf-8",
        )

        stats = footnote_citations(wiki)
        assert stats["defs_backfilled"] == 1

        text = page.read_text(encoding="utf-8")
        assert "[^1]: Jane Doe" in text
        assert "https://example.com/a" in text

    def test_backfill_neither_matches_old_behavior(self, tmp_path: Path) -> None:
        """A source with no captured provenance still gets the plain,
        pre-existing de-slugged-filename def -- no regression."""
        wiki = self._make_wiki(tmp_path)
        _write_registry(
            wiki / ".sources.json",
            [{"id": 1, "filename": "some_article.md", "hash": "abc"}],
        )
        page = wiki / "topic.md"
        page.write_text(
            "# Topic\n\nSome claim cited here [^1].\n\n## Sources\n",
            encoding="utf-8",
        )

        footnote_citations(wiki)
        text = page.read_text(encoding="utf-8")
        assert f"[^1]: {_filename_to_title('some_article.md')}" in text

    def test_harvested_def_still_takes_priority(self, tmp_path: Path) -> None:
        """When the LLM already wrote a real ## Sources bullet, that harvested
        text wins over the registry-provenance fallback (priority #1
        preserved, no regression to the existing harvest behavior)."""
        wiki = self._make_wiki(tmp_path)
        _write_registry(
            wiki / ".sources.json",
            [
                {
                    "id": 1,
                    "filename": "some_article.md",
                    "hash": "abc",
                    "author": "Registry Author",
                    "url": "https://example.com/registry-url",
                }
            ],
        )
        # Page A has the real harvested bullet (with its own, different URL).
        page_a = wiki / "page-a.md"
        page_a.write_text(
            "# Page A\n\nClaim [1].\n\n"
            "## Sources\n- [1] Real Author — https://example.com/harvested\n",
            encoding="utf-8",
        )
        # Page B cites the same source but has no local Sources bullet.
        page_b = wiki / "page-b.md"
        page_b.write_text(
            "# Page B\n\nAnother claim [^1].\n\n## Sources\n",
            encoding="utf-8",
        )

        footnote_citations(wiki)

        text_b = page_b.read_text(encoding="utf-8")
        # Harvested text (from page A) wins, not the registry-rendered fallback.
        assert "https://example.com/harvested" in text_b
        assert "https://example.com/registry-url" not in text_b

    def test_idempotent(self, tmp_path: Path) -> None:
        wiki = self._make_wiki(tmp_path)
        _write_registry(
            wiki / ".sources.json",
            [
                {
                    "id": 1,
                    "filename": "some_article.md",
                    "hash": "abc",
                    "author": "Jane Doe",
                    "url": "https://example.com/a",
                }
            ],
        )
        page = wiki / "topic.md"
        page.write_text(
            "# Topic\n\nSome claim cited here [^1].\n\n## Sources\n",
            encoding="utf-8",
        )

        footnote_citations(wiki)
        text_after_first = page.read_text(encoding="utf-8")

        stats2 = footnote_citations(wiki)
        text_after_second = page.read_text(encoding="utf-8")

        assert text_after_first == text_after_second
        assert stats2 == {
            "pages_changed": 0,
            "refs_converted": 0,
            "defs_converted": 0,
            "defs_backfilled": 0,
        }


# ---------------------------------------------------------------------------
# Group 4 -- real-artifact check against the frozen corpus registry
# ---------------------------------------------------------------------------

_REAL_CORPUS_REGISTRY = _REPO / "runs" / "corpus" / "wiki" / ".sources.json"


class TestRealCorpusRegistry:
    """Exercises the new code against the actual frozen 746-source registry
    (gitignored per AGENTS.md data discipline -- present in this dev checkout
    but not committed). Skips gracefully when the corpus isn't present so CI
    checkouts without it still pass."""

    def test_real_registry_author_url_renders(self) -> None:
        if not _REAL_CORPUS_REGISTRY.is_file():
            import pytest  # noqa: PLC0415

            pytest.skip(
                f"frozen corpus registry not present at {_REAL_CORPUS_REGISTRY} "
                "(gitignored data, dev-checkout only)"
            )

        valid_ids, id_to_filename, id_to_provenance = _load_sources(
            _REAL_CORPUS_REGISTRY
        )
        assert valid_ids, "expected a non-empty real registry"

        # Known real source (verified during grounding): id 146, author +
        # medium.com URL both present, no date (Medium exports lack it).
        assert "146" in id_to_provenance
        assert id_to_provenance["146"]["author"] == "Micheal Lanham"
        assert "url" in id_to_provenance["146"]
        assert "date" not in id_to_provenance["146"]

        text = _render_provenance_def("146", id_to_filename, id_to_provenance)
        assert "Micheal Lanham" in text
        assert id_to_provenance["146"]["url"] in text

        # Sanity on real-corpus coverage: the majority of the 746 sources
        # should carry author+url (grounded finding: 601/746).
        with_both = sum(
            1
            for prov in id_to_provenance.values()
            if prov.get("author") and prov.get("url")
        )
        assert with_both >= 500
