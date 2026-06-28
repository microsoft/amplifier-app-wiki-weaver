# pyright: reportMissingImports=false
"""Thin-page advisory WARN eval gate (test_thin_page_warn.py).

S6 is a COUNT-BASED advisory: a content page drawn from a single source with a
body under THIN_PAGE_WORD_MIN words is the listicle name-drop sprawl pattern.
It is a WARNING, never a failure -- a structurally clean wiki with thin pages
still PASSES, but surfaces them for human review.

These tests pin that contract: the thin single-source page is flagged in
S6_thin_pages and produces a warning, while a substantive page and a
multi-source page are not flagged, and the overall result still passes.

SAFETY: isolated tmp_path dirs only; never touches live wiki runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "pipeline"))

from validate_wiki import THIN_PAGE_WORD_MIN, validate  # noqa: E402


def _page(d: Path, slug: str, *, title: str, sources: str, body_words: int) -> None:
    body = " ".join(["word"] * body_words)
    (d / f"{slug}.md").write_text(
        f"---\ntitle: {title}\ntype: tool\nsources: {sources}\n"
        f"last_updated: 2026-06-25\n---\n\n# {title}\n\n{body}\n",
        encoding="utf-8",
    )


def _nav(d: Path, slug: str, links: list[str]) -> None:
    body = "\n".join(f"- [[{s}]]" for s in links)
    (d / f"{slug}.md").write_text(
        f"---\ntitle: {slug.title()}\ntype: {slug}\nsources: []\n"
        f"last_updated: 2026-06-25\n---\n\n# {slug.title()}\n\n{body}\n",
        encoding="utf-8",
    )


def test_thin_single_source_page_warns_but_passes(tmp_path: Path) -> None:
    # thin: 1 source, tiny body -> flagged. substantive: 1 source, big body -> not.
    # multi: 2 sources, tiny body -> not flagged (corroborated).
    _page(tmp_path, "thin", title="Thin", sources="[4]", body_words=20)
    _page(tmp_path, "substantive", title="Substantive", sources="[4]", body_words=400)
    _page(tmp_path, "multi", title="Multi", sources="[4, 7]", body_words=20)
    _nav(tmp_path, "index", ["thin", "substantive", "multi"])
    _nav(tmp_path, "overview", ["thin", "substantive", "multi"])

    r = validate(tmp_path)

    flagged = r["checks"]["S6_thin_pages"]["detail"]
    assert any("thin.md" in f for f in flagged), f"thin page must be flagged: {flagged}"
    assert not any("substantive.md" in f for f in flagged), "substantive must NOT flag"
    assert not any("multi.md" in f for f in flagged), "multi-source must NOT flag"

    assert r["checks"]["S6_thin_pages"]["flagged"] == 1
    assert any("S6" in w for w in r["warnings"]), "a WARN message must be present"
    # Advisory only: thin pages do NOT fail the wiki.
    assert r["passed"] is True, "S6 is advisory; wiki must still PASS"


def test_no_thin_pages_no_warning(tmp_path: Path) -> None:
    _page(tmp_path, "a", title="A", sources="[1]", body_words=THIN_PAGE_WORD_MIN + 50)
    _nav(tmp_path, "index", ["a"])
    _nav(tmp_path, "overview", ["a"])

    r = validate(tmp_path)

    assert r["checks"]["S6_thin_pages"]["flagged"] == 0
    assert not r["warnings"]
    assert r["passed"] is True
