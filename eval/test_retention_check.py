"""retention_check: deterministic shrinkage/heading-loss detector (NOT an
LLM judge). Compares each page against its last-committed (git HEAD)
version."""

from __future__ import annotations

import json
import shutil

import pytest

from wiki_weaver.ingest import retention_check
from wiki_weaver.lib import WikiRoot

GIT_MISSING = shutil.which("git") is None

ORIGINAL_PAGE = """---
title: Long Page
type: concept
---

# Long Page

## Background

This section has a good amount of detail about the background of the
topic, spanning several sentences so that word count is meaningfully large
enough for a shrinkage percentage to be measurable in this test.

## Analysis

Further detail here, again with enough words to matter for the shrink
threshold calculation used by the deterministic detector.
"""

SHRUNK_PAGE = """---
title: Long Page
type: concept
---

# Long Page

## Background

Short now.
"""


@pytest.mark.skipif(GIT_MISSING, reason="git not available")
def test_shrinkage_and_heading_loss_flagged_as_suspicious(wiki_root, capsys):
    wiki_root.init_git()
    wiki_root.add_page("long-page.md", ORIGINAL_PAGE)
    wiki_root.commit_all("seed")

    # Simulate weave shrinking the page and dropping the "## Analysis" heading.
    wiki_root.add_page("long-page.md", SHRUNK_PAGE)

    wr = WikiRoot(wiki_root.root)
    code = retention_check.main(
        ["--wiki-root", str(wr.root), "--snapshot-on-suspicion", "--out", ".ai/retention-report.md"]
    )
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert code == 0
    assert payload["retention_flag"].startswith("suspicious:")
    report = (wr.root / ".ai" / "retention-report.md").read_text(encoding="utf-8")
    assert "shrinkage" in report or "heading loss" in report


@pytest.mark.skipif(GIT_MISSING, reason="git not available")
def test_no_change_is_ok(wiki_root, capsys):
    wiki_root.init_git()
    wiki_root.add_page("long-page.md", ORIGINAL_PAGE)
    wiki_root.commit_all("seed")

    wr = WikiRoot(wiki_root.root)
    code = retention_check.main(
        ["--wiki-root", str(wr.root), "--snapshot-on-suspicion", "--out", ".ai/retention-report.md"]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload == {"retention_flag": "ok"}


def test_no_git_is_ok_not_a_crash(wiki_root, capsys):
    wiki_root.add_page("long-page.md", ORIGINAL_PAGE)
    wr = WikiRoot(wiki_root.root)
    code = retention_check.main(
        ["--wiki-root", str(wr.root), "--snapshot-on-suspicion", "--out", ".ai/retention-report.md"]
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert code == 0
    assert payload == {"retention_flag": "ok"}


def test_output_is_exactly_one_json_line(wiki_root, capsys):
    wiki_root.add_page("long-page.md", ORIGINAL_PAGE)
    wr = WikiRoot(wiki_root.root)
    retention_check.main(["--wiki-root", str(wr.root), "--snapshot-on-suspicion", "--out", ".ai/retention-report.md"])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
