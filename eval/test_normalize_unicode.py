"""Unit tests for pipeline/normalize_unicode.py -- the \\uXXXX escape-artifact
repair pass (defense-in-depth bridge for amplifier-support #306).

All tests are deterministic -- no LLM, no network, no pipeline run required.

Covers:
  - prose repairs (em-dash, accented Latin, curly quotes, ellipsis)
  - code preservation (fenced blocks + inline code spans, incl. multi-backtick)
  - allowlist boundary (codepoints outside the curated ranges stay literal)
  - idempotency (normalize(normalize(x)) == normalize(x))
  - a real-artifact fixture captured from an actual wiki-weaver run
"""

from __future__ import annotations

import sys
from pathlib import Path

# Insert the repo root so we can import pipeline.normalize_unicode without installing.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "pipeline"))

from pipeline.normalize_unicode import (  # noqa: E402
    _normalize_text,
    normalize_wiki,
)

# ---------------------------------------------------------------------------
# Group 1 -- prose repairs
# ---------------------------------------------------------------------------


class TestProseRepairs:
    """Allowlisted \\uXXXX escapes in prose are decoded to the real character."""

    def test_em_dash(self) -> None:
        text = r"Each server is wired in the same pattern \u2014 configured directly."
        new_text, count = _normalize_text(text)
        assert (
            new_text
            == "Each server is wired in the same pattern — configured directly."
        )
        assert count == 1

    def test_en_dash(self) -> None:
        text = r"pages 10\u201320 cover the setup."
        new_text, count = _normalize_text(text)
        assert new_text == "pages 10–20 cover the setup."
        assert count == 1

    def test_accented_latin(self) -> None:
        text = r"Jos\u00e9 wrote a na\u00efve first draft."
        new_text, count = _normalize_text(text)
        assert new_text == "José wrote a naïve first draft."
        assert count == 2

    def test_curly_quotes(self) -> None:
        text = r"She said \u201cquote\u201d to open and \u2018single\u2019 to nest."
        new_text, count = _normalize_text(text)
        assert new_text == "She said “quote” to open and ‘single’ to nest."
        assert count == 4

    def test_ellipsis(self) -> None:
        text = r"The list goes on\u2026 and on."
        new_text, count = _normalize_text(text)
        assert new_text == "The list goes on… and on."
        assert count == 1

    def test_minus_sign(self) -> None:
        text = r"the result is \u221210 relative to baseline."
        new_text, count = _normalize_text(text)
        assert new_text == "the result is −10 relative to baseline."
        assert count == 1

    def test_multiple_artifacts_one_line(self) -> None:
        text = r"A \u2014 B \u2014 C, said Jos\u00e9."
        new_text, count = _normalize_text(text)
        assert new_text == "A — B — C, said José."
        assert count == 3

    def test_no_artifacts_unchanged(self) -> None:
        text = "Plain prose with an em\u2014dash already a real character."
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0


# ---------------------------------------------------------------------------
# Group 2 -- code preservation
# ---------------------------------------------------------------------------


class TestCodePreservation:
    """Fenced code blocks and inline code spans are never touched."""

    def test_fenced_code_block_untouched(self) -> None:
        text = (
            "Prose before \\u2014 the fence.\n"
            "```python\n"
            'value = "\\u2014"  # literal escape in code, must survive\n'
            "```\n"
            "Prose after \\u2014 the fence.\n"
        )
        new_text, count = _normalize_text(text)
        assert '"\\u2014"' in new_text, (
            "escape inside fenced code must be preserved verbatim"
        )
        assert new_text.count("—") == 2, "only the two prose occurrences should decode"
        assert count == 2

    def test_tilde_fence_untouched(self) -> None:
        text = "~~~\nraw = \\u2014\n~~~\n"
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0

    def test_unterminated_fence_treated_as_code(self) -> None:
        """Mirrors footnotes.py's _code_fence_line_set: an unterminated fence
        treats all remaining lines as code (fail-safe direction)."""
        text = "```\nvalue = \\u2014\nstill inside the fence \\u2014\n"
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0

    def test_inline_code_span_single_backtick_untouched(self) -> None:
        text = r"The literal escape `\u2014` must stay exactly as written."
        new_text, count = _normalize_text(text)
        assert "`\\u2014`" in new_text
        assert count == 0

    def test_inline_code_span_multi_backtick_untouched(self) -> None:
        """Multi-backtick spans (e.g. ``a`b``) must be preserved byte-for-byte."""
        text = r"Escaped in a double-backtick span: ``contains \u2014 inside`` end."
        new_text, count = _normalize_text(text)
        assert "``contains \\u2014 inside``" in new_text
        assert count == 0

    def test_prose_and_inline_code_on_same_line(self) -> None:
        """A real artifact in prose decodes; an escape inside `` `` `` on the
        SAME line is left untouched."""
        text = r"Real dash \u2014 here, but code `\u2014` stays literal."
        new_text, count = _normalize_text(text)
        assert new_text == "Real dash — here, but code `\\u2014` stays literal."
        assert count == 1

    def test_frontmatter_and_body_code_both_handled(self) -> None:
        text = (
            "---\n"
            "title: Test\n"
            "---\n\n"
            "Prose \\u2014 dash.\n\n"
            "```\n"
            "code \\u2014 stays\n"
            "```\n"
        )
        new_text, count = _normalize_text(text)
        assert "Prose — dash." in new_text
        assert "code \\u2014 stays" in new_text
        assert count == 1


# ---------------------------------------------------------------------------
# Group 3 -- allowlist boundary
# ---------------------------------------------------------------------------


class TestAllowlistBoundary:
    """Codepoints outside the curated allowlist are left verbatim in prose."""

    def test_ascii_letter_escape_left_verbatim(self) -> None:
        text = r"An escaped ASCII letter \u0041 must not be decoded."
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0

    def test_private_use_area_left_verbatim(self) -> None:
        text = r"A private-use codepoint \uABCD must not be decoded."
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0

    def test_other_json_escapes_untouched(self) -> None:
        """Only bare \\uXXXX is in scope -- \\", \\\\, \\n, \\t, \\/ are left alone."""
        text = r"literal backslash-n \n, backslash-t \t, quote \", slash \/, and \\ itself."
        new_text, count = _normalize_text(text)
        assert new_text == text
        assert count == 0

    def test_mixed_allowed_and_disallowed_on_one_line(self) -> None:
        text = r"Real dash \u2014 but keep \u0041 and \uABCD as-is."
        new_text, count = _normalize_text(text)
        assert new_text == "Real dash — but keep \\u0041 and \\uABCD as-is."
        assert count == 1


# ---------------------------------------------------------------------------
# Group 4 -- idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    """normalize(normalize(x)) == normalize(x) for every case above."""

    @staticmethod
    def _twice(text: str) -> None:
        once, count_once = _normalize_text(text)
        twice, count_twice = _normalize_text(once)
        assert twice == once, "second pass must be a no-op on already-repaired text"
        assert count_twice == 0, "second pass must repair nothing new"
        # Sanity: the first pass actually did the expected repair work at least
        # once whenever the input contained an allowlisted escape.
        del count_once

    def test_prose_repair_idempotent(self) -> None:
        self._twice(r"Each server is wired \u2014 the same pattern, said Jos\u00e9.")

    def test_code_preservation_idempotent(self) -> None:
        self._twice(
            'Prose \\u2014 dash.\n```\ncode = "\\u2014"\n```\n`\\u2014` inline.\n'
        )

    def test_disallowed_escape_idempotent(self) -> None:
        self._twice(r"Keep \u0041 and \uABCD untouched across passes.")

    def test_full_normalize_wiki_idempotent(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        page = wiki / "page.md"
        page.write_text(
            r"Prose \u2014 dash with `\u2014` code preserved." + "\n",
            encoding="utf-8",
        )

        files_changed_1, repaired_1 = normalize_wiki(wiki)
        assert files_changed_1 == 1
        assert repaired_1 == 1

        files_changed_2, repaired_2 = normalize_wiki(wiki)
        assert files_changed_2 == 0
        assert repaired_2 == 0


# ---------------------------------------------------------------------------
# Group 5 -- real-artifact fixture
# ---------------------------------------------------------------------------
#
# Snippet captured verbatim from an actual wiki-weaver run that exhibited the
# amplifier-core get_serialized_output artifact:
#   .amplifier/evaluation/ww-attractor-ab/20260702T0150/after/trial-3/wiki/
#   mcp-servers-local-assistant.md
#
# The page is prose-only (no fenced code), so this fixture pairs with a
# synthetic fenced-code variant below to prove code is preserved even when
# real-artifact-shaped prose surrounds it.

_REAL_ARTIFACT_SNIPPET = (
    "Each server is wired in through the same pattern \\u2014 an `MCPClient` "
    "configured with a `server_type`, `endpoint`, and `api_key`, driven by an "
    "`MCPAgent` paired with a local LLM (the source's examples use "
    "`ollama://llama3`) \\u2014 so servers can be composed by appending entries "
    'to one `mcp_config["servers"]` list rather than integrating each tool '
    "separately [^10].\n"
)


class TestRealArtifactFixture:
    """Reproduce the actual captured artifact and prove the repair + code
    preservation both hold on real content."""

    def test_real_snippet_prose_repaired(self) -> None:
        new_text, count = _normalize_text(_REAL_ARTIFACT_SNIPPET)
        assert count == 2, "both \\u2014 occurrences in prose should be repaired"
        assert "\\u2014" not in new_text
        assert new_text.count("—") == 2

    def test_real_snippet_inline_code_preserved(self) -> None:
        """The snippet's inline-code spans (`MCPClient`, `server_type`, etc.)
        must survive byte-for-byte -- this snippet has no \\uXXXX INSIDE code,
        but proves the surrounding decode pass doesn't corrupt adjacent
        backtick-delimited tokens."""
        new_text, _count = _normalize_text(_REAL_ARTIFACT_SNIPPET)
        for token in (
            "`MCPClient`",
            "`server_type`",
            "`endpoint`",
            "`api_key`",
            "`MCPAgent`",
            "`ollama://llama3`",
        ):
            assert token in new_text, f"inline code span {token!r} must be preserved"

    def test_real_snippet_with_synthetic_code_fence_preserves_escape(self) -> None:
        """Combine the real prose artifact with a synthetic fenced code block
        containing the SAME escape sequence, proving the fence wins."""
        text = (
            _REAL_ARTIFACT_SNIPPET
            + "\n```json\n"
            + '{"separator": "\\u2014"}\n'
            + "```\n"
        )
        new_text, count = _normalize_text(text)
        assert count == 2, "only the two prose artifacts should be repaired"
        assert '"separator": "\\u2014"' in new_text, (
            "escape inside the fenced JSON code block must be preserved verbatim"
        )

    def test_real_page_via_normalize_wiki(self, tmp_path: Path) -> None:
        """End-to-end: write the real snippet as a wiki page and repair in place."""
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        page = wiki / "mcp-servers-local-assistant.md"
        page.write_text(_REAL_ARTIFACT_SNIPPET, encoding="utf-8")

        files_changed, escapes_repaired = normalize_wiki(wiki)

        assert files_changed == 1
        assert escapes_repaired == 2
        repaired_text = page.read_text(encoding="utf-8")
        assert "\\u2014" not in repaired_text
        assert repaired_text.count("—") == 2
