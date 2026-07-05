#!/usr/bin/env python3
"""Repair stray \\uXXXX JSON-escape artifacts in prose (in-place).

Shared Unicode-artifact normalizer for the wiki-weaver pipeline and standalone
use.

Why this exists (bridge, not a fix): amplifier-core's ``get_serialized_output``
JSON-serializes tool results with ``ensure_ascii=True``, so a real character
(e.g. an em-dash) that a model reads back from a tool result (a ``grep``
match, a re-read page) is shown to the model as the literal escape sequence
``\\u2014``. Models sometimes copy that literal escape straight into generated
page content instead of the real character. This is a latent amplifier-core
bug (amplifier-support #306, ``get_serialized_output``). This module is a
deterministic, code-fence-aware repair pass that runs BEFORE ``validate`` so
the artifact never reaches a persisted page, regardless of when the upstream
fix lands.

Public API:
    normalize_wiki(wiki_dir) -> (files_changed, escapes_repaired)
        Repairs all ``\\uXXXX`` artifacts in prose IN-PLACE, no backup
        created. Idempotent.

Scope discipline (why this is SAFE on technical docs -- wiki-weaver ingests
Rust source and MCP configs that can legitimately contain ``\\uXXXX``):
  - Only a bare ``\\uXXXX`` (4 hex digits) escape is EVER in scope. ``\\"``,
    ``\\\\``, ``\\n``, ``\\t``, ``\\/``, and any other escape are left
    untouched.
  - Only a CURATED ALLOWLIST of prose-punctuation / accented-Latin codepoint
    ranges is decoded (dashes, curly quotes, general punctuation, the minus
    sign). Anything outside the allowlist (CJK, emoji, control chars, plain
    ASCII like ``\\u0041``) is left verbatim -- far more likely to be an
    intentional escape in a technical doc than an LLM-copied artifact.
  - Fenced code blocks (``` / ~~~, any length >=3) and inline code spans
    (single- or multi-backtick) are NEVER touched.

CLI usage (in-pipeline mode -- no backup, always exits 0):
    python pipeline/normalize_unicode.py <wiki_dir>

CLI usage (standalone / one-time mode -- creates a dated backup first):
    python pipeline/normalize_unicode.py <wiki_dir> --backup
"""

from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Curated allowlist: only these codepoint ranges are ever decoded. Chosen to
# cover the JSON-escaped prose punctuation / accented Latin an LLM might copy
# verbatim from a tool result, while excluding everything a technical doc
# might legitimately escape on purpose (CJK, emoji, control chars, plain
# ASCII like \u0041).
# ---------------------------------------------------------------------------
_ALLOWED_RANGES: tuple[tuple[int, int], ...] = (
    (0x00A0, 0x024F),  # Latin-1 Supplement + Latin Extended-A/B (accented letters)
    (0x2010, 0x2027),  # hyphens/dashes (en/em) + curly quotes
    (0x2030, 0x205E),  # general punctuation incl. ellipsis
    (0x2212, 0x2212),  # minus sign
)

# Bare \uXXXX escape -- 4 hex digits only. Deliberately does NOT match \", \\,
# \n, \t, \/, or any other JSON escape sequence.
_UESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")

# Fenced code block delimiters (``` or ~~~, 3+ chars) -- same convention as
# footnotes.py's _FENCE.
_FENCE = re.compile(r"^(`{3,}|~{3,})")

# Inline code span: a run of N backticks ... the same run of N backticks.
# The backreference (\1) is what makes this handle multi-backtick spans
# (e.g. ``a`b``) the same way CommonMark's matching-length-delimiter rule
# does -- a plain `[^`\n]*` pattern would stop at the FIRST embedded
# backtick instead of the matching close.
_INLINE_CODE = re.compile(r"(`+)[^\n]*?\1")


def _is_allowed(codepoint: int) -> bool:
    """True if codepoint falls within the curated prose-artifact allowlist."""
    return any(lo <= codepoint <= hi for lo, hi in _ALLOWED_RANGES)


def _decode_prose(text: str) -> tuple[str, int]:
    """Decode allowlisted \\uXXXX escapes in a prose-only string.

    Returns (new_text, count_repaired). Fail-safe: any \\uXXXX outside the
    allowlist is left verbatim, never raises.
    """
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        try:
            codepoint = int(m.group(1), 16)
        except ValueError:
            return m.group(0)  # pragma: no cover -- regex guarantees hex digits
        if not _is_allowed(codepoint):
            return m.group(0)
        count += 1
        return chr(codepoint)

    return _UESCAPE.sub(repl, text), count


def _code_fence_line_set(lines: list[str]) -> set[int]:
    """Return line indices inside (or delimiting) fenced code blocks.

    Mirrors footnotes.py's _code_fence_line_set. Both the opening and
    closing fence delimiter lines are included, as is every line between
    them. Safe on unterminated fences (all remaining lines are treated as
    code, matching footnotes.py's behavior).
    """
    in_fence = False
    fence_char = ""
    code_lines: set[int] = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = _FENCE.match(stripped)
        if not in_fence:
            if m:
                in_fence = True
                fence_char = m.group(1)[0]  # ` or ~
                code_lines.add(i)
        else:
            code_lines.add(i)
            if stripped.startswith(fence_char * 3):
                in_fence = False
    return code_lines


def _normalize_line(line: str) -> tuple[str, int]:
    """Repair allowlisted \\uXXXX escapes in one non-fenced line.

    Inline code spans are masked before decoding and restored verbatim
    afterward, so an escape inside `` `...` `` (single- or multi-backtick)
    is never touched.
    """
    saved: list[str] = []

    def _save(m: re.Match[str]) -> str:
        saved.append(m.group(0))
        return f"\x00CODE{len(saved) - 1}\x00"

    masked = _INLINE_CODE.sub(_save, line)
    decoded, count = _decode_prose(masked)
    for i, span in enumerate(saved):
        decoded = decoded.replace(f"\x00CODE{i}\x00", span)
    return decoded, count


def _normalize_text(text: str) -> tuple[str, int]:
    """Repair allowlisted \\uXXXX artifacts in one page's markdown text.

    Fenced code blocks are passed through byte-for-byte; inline code spans
    are masked and restored per line. Idempotent: a real character has no
    \\uXXXX left to decode, so re-running on already-repaired text is a
    no-op.

    Returns (new_text, count_repaired).
    """
    lines = text.splitlines()
    trailing_newline = text.endswith("\n")
    code_lines = _code_fence_line_set(lines)

    new_lines: list[str] = []
    total = 0
    for i, line in enumerate(lines):
        if i in code_lines:
            new_lines.append(line)
            continue
        new_line, count = _normalize_line(line)
        new_lines.append(new_line)
        total += count

    new_text = "\n".join(new_lines)
    if trailing_newline:
        new_text += "\n"
    return new_text, total


def normalize_wiki(wiki_dir: Path | str) -> tuple[int, int]:
    """Repair \\uXXXX artifacts in every page under wiki_dir IN-PLACE.

    Returns (files_changed, escapes_repaired).
    Idempotent: a second run on an already-repaired wiki returns (0, 0).
    """
    wiki_dir = Path(wiki_dir)
    pages = sorted(wiki_dir.glob("*.md"))
    if not pages:
        return 0, 0

    files_changed = 0
    total_repaired = 0

    for p in pages:
        text = p.read_text(encoding="utf-8", errors="replace")
        new_text, count = _normalize_text(text)
        if new_text != text:
            files_changed += 1
            total_repaired += count
            p.write_text(new_text, encoding="utf-8")

    return files_changed, total_repaired


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--backup"]
    backup = "--backup" in sys.argv
    if not args:
        print(__doc__)
        return 2

    wiki_dir = Path(args[0])
    if not wiki_dir.is_dir():
        print(f"FAIL: wiki dir not found: {wiki_dir}", file=sys.stderr)
        return 1

    if backup:
        bak = wiki_dir.parent / f"{wiki_dir.name}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copytree(wiki_dir, bak)
        print(f"backup -> {bak}")

    files_changed, escapes_repaired = normalize_wiki(wiki_dir)
    print(
        f"normalize_unicode: files_changed={files_changed}"
        f" escapes_repaired={escapes_repaired}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
