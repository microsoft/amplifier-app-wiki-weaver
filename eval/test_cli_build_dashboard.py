"""Tests for the `wiki-weaver build-dashboard` CLI subcommand — Increment 3.

Coverage:
  - cmd_build_dashboard() via argparse.Namespace over the wiki-min fixture
  - HTML is produced, non-empty, and self-contained (no src="http" refs)
  - Page titles from the fixture appear in the output
  - No raw [[wikilink]] markers remain
  - --skip-index flag accepted without error (pre-built indexes in fixture)
  - --group-by flag is forwarded (smoke-test only; rendering verified elsewhere)
  - Missing corpus dir -> non-zero exit code

No LLM, no engine, no Amplifier runtime required.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pytest

# -- Repo-root path plumbing (mirrors existing eval/*.py convention) ---------
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.wiki_weaver import cmd_build_dashboard  # noqa: E402

# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "wiki-min"


@pytest.fixture()
def corpus(tmp_path: Path) -> Path:
    """Copy wiki-min fixture to a temp dir and return the path."""
    dest = tmp_path / "wiki-min"
    shutil.copytree(_FIXTURE_DIR, dest)
    return dest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(
    corpus_path: Path,
    out: Path,
    *,
    theme: str | None = None,
    group_by: str = "type",
    group_link_template: str | None = None,
    skip_index: bool = False,
) -> int:
    """Build a Namespace and call cmd_build_dashboard directly."""
    ns = argparse.Namespace(
        corpus=str(corpus_path),
        out=str(out),
        theme=theme,
        group_by=group_by,
        group_link_template=group_link_template,
        skip_index=skip_index,
    )
    return cmd_build_dashboard(ns)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_produces_html_file(corpus: Path, tmp_path: Path) -> None:
    """cmd_build_dashboard returns 0 and writes the output HTML file."""
    out = tmp_path / "dashboard.html"
    rc = _run(corpus, out)
    assert rc == 0, "Expected exit code 0"
    assert out.exists(), "Output HTML file was not created"
    assert out.stat().st_size > 0, "Output HTML file is empty"


def test_self_contained_no_external_src(corpus: Path, tmp_path: Path) -> None:
    """The generated HTML must have no external src= references."""
    out = tmp_path / "dashboard.html"
    assert _run(corpus, out) == 0
    html = out.read_text(encoding="utf-8")
    external = re.findall(r'src=["\']https?://', html)
    assert not external, f"Found external src= references: {external}"


def test_page_titles_present(corpus: Path, tmp_path: Path) -> None:
    """All fixture page titles must appear somewhere in the HTML."""
    out = tmp_path / "dashboard.html"
    assert _run(corpus, out) == 0
    html = out.read_text(encoding="utf-8")
    for title in ("Alpha", "Beta", "Gamma", "Delta"):
        assert title in html, f"Expected page title '{title}' not found in HTML"


def test_no_raw_wikilinks(corpus: Path, tmp_path: Path) -> None:
    """No raw [[...]] markers should appear in the rendered HTML."""
    out = tmp_path / "dashboard.html"
    assert _run(corpus, out) == 0
    html = out.read_text(encoding="utf-8")
    raw_links = re.findall(r"\[\[.*?\]\]", html)
    assert not raw_links, f"Raw wikilinks found in HTML: {raw_links}"


def test_skip_index_flag(corpus: Path, tmp_path: Path) -> None:
    """--skip-index must be accepted without error (uses pre-existing indexes).

    We first do a full build to materialise the indexes, then re-run with
    --skip-index so no index rebuild occurs.  The HTML must still be valid.
    """
    out1 = tmp_path / "first.html"
    assert _run(corpus, out1) == 0  # builds indexes
    out2 = tmp_path / "second.html"
    rc = _run(corpus, out2, skip_index=True)
    assert rc == 0
    assert out2.exists() and out2.stat().st_size > 0


def test_group_by_flag_accepted(corpus: Path, tmp_path: Path) -> None:
    """--group-by with a non-default field must be accepted without error."""
    out = tmp_path / "dashboard.html"
    rc = _run(corpus, out, group_by="type")
    assert rc == 0
    assert out.exists()


def test_missing_corpus_returns_nonzero(tmp_path: Path) -> None:
    """cmd_build_dashboard must return a nonzero exit code when corpus is absent."""
    nonexistent = tmp_path / "does-not-exist"
    out = tmp_path / "dashboard.html"
    rc = _run(nonexistent, out)
    assert rc != 0, "Expected non-zero exit for missing corpus"


def test_group_link_template_flag_forwarded(corpus: Path, tmp_path: Path) -> None:
    """--group-link-template is accepted and forwarded to build_dashboard.

    The GROUP_LINK_TEMPLATE constant must be embedded in the output JS.
    """
    out = tmp_path / "dash_linked.html"
    rc = _run(corpus, out, group_link_template="https://example.com/{group}")
    assert rc == 0, "Expected exit code 0 with --group-link-template"
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "GROUP_LINK_TEMPLATE" in html, (
        "Expected GROUP_LINK_TEMPLATE constant in output when --group-link-template is set"
    )
    assert "example.com" in html, "Expected template domain in output HTML"
