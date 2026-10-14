"""The writer never receives a page's previous Current state.

One call integrates every section under an enforced never-delete rule; for Current state
it was asked, by a sentence only, to do the opposite. It kept the old roll-up (LEARNINGS,
2026-10-13). It cannot keep what it never received.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from wiki_weaver.steps import strip_current_state

FM = "---\ntitle: Brian\ntype: people\nsources:\n- a.md\nlast_updated: 2031-03-02\n---\n"
CS = (
    "## Current state (as of 2031-03-02)\n"
    "Brian owns the engine.\n\n"
    "Earlier roll-up, kept for history:\nAs of 2031-02-01 (planning chat): ...\n\n"
)
REST = '## Role\n- Eng lead [s1: "five words copied exactly here"]\n\n## Decisions\n- none\n'


def test_strips_current_state_heading_and_body() -> None:
    page = FM + "# Brian\n\n" + CS + REST
    out = strip_current_state(page)
    assert "Current state" not in out and "Earlier roll-up" not in out
    assert out == FM + "# Brian\n\n" + REST  # everything else byte-identical


def test_page_without_current_state_is_unchanged() -> None:
    for page in (FM + "# Brian\n\n" + REST, "", "# Bare\nno sections\n", FM):
        assert strip_current_state(page) == page


def test_current_state_as_last_section_and_crlf_rest_kept() -> None:
    page = FM + "# Brian\n\n## Role\r\n- x\r\n\n" + CS.rstrip("\n")
    assert strip_current_state(page) == FM + "# Brian\n\n## Role\r\n- x\r\n\n"


def test_frontmatter_hash_lines_are_not_headings() -> None:
    fm = "---\ntitle: x\n# a yaml comment\n---\n"
    page = fm + CS + REST
    assert strip_current_state(page) == fm + REST


def _step(corpus: Path, *args: str) -> str:
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", *args],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.strip().splitlines()[-1]


def test_writer_input_omits_current_state_and_keeps_live_page(corpus: Path, tmp_path: Path) -> None:
    live = FM.replace("Brian", "Orchard rollout") + "# Orchard rollout\n\n" + CS + REST
    (corpus / "orchard-rollout.md").write_text(live)
    _step(corpus, "select", str(tmp_path / "run"), "0", "-")
    _step(corpus, "assemble")
    (corpus / ".wiki/work/brief.md").write_text("brief")
    (corpus / ".wiki/work/slugs.txt").write_text("orchard-rollout\n")
    assert _step(corpus, "page_select") == "ok"
    pages = (corpus / ".wiki/work/pages.md").read_text()
    assert "Brian owns the engine" not in pages and "Earlier roll-up" not in pages
    assert REST in pages
    # the live page and the loss guard's baseline are untouched
    assert (corpus / "orchard-rollout.md").read_text() == live
    assert (corpus / ".wiki/work/before/orchard-rollout.md").read_text() == live


def test_writer_prompt_says_current_state_is_absent_and_must_be_new() -> None:
    dot = (Path(__file__).parent.parent / "pipeline" / "ingest.dot").read_text()
    assert "Current state is not in pages.md" in dot
    assert "write it new" in dot and "Replace it in full every pass" not in dot
