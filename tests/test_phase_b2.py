"""B-lean.2 item 1: every writer invocation is measured, rewrites included. No model calls."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from test_steps import ORCHARD, cite, fake_brief, page, step

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = "source-2031-03-02-orchard-sync.md"


def _cur(corpus: Path) -> dict:
    return json.loads((corpus / ".wiki/work/current.json").read_text())


def test_every_writer_call_is_measured_and_a_rewrite_can_be_held(corpus: Path, tmp_path: Path):
    text = (ROOT / "pipeline" / "ingest.dot").read_text()
    # the rewrite goes through the same measurement as the first write
    assert not re.search(r"checks\s*->\s*write\s*\[[^\]]*last_line=rewrite", text)
    assert re.search(r"checks\s*->\s*write_guard\s*\[[^\]]*last_line=rewrite", text)
    assert re.search(r"write_guard\s*->\s*write\s*\[[^\]]*last_line=ok", text)
    assert re.search(r"write_guard\s*->\s*hold\s*\[[^\]]*last_line=oversized", text)

    run = tmp_path / "r"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    assert step(corpus, "page_select") == (0, "ok")
    first = _cur(corpus)["writer_chars_per_call"]
    work = corpus / ".wiki/work"
    sent = sum(len((work / f).read_text()) for f in ("context.md", "pages.md", "brief.md"))
    assert first == [sent]

    bad = page("O", [ORCHARD], cite(corpus, "words that are not in the source at all") + "\n")
    (corpus / SUMMARY).write_text(bad)
    assert step(corpus, "checks") == (0, "rewrite")
    actual = sent + len((work / "findings.md").read_text())
    assert actual > sent

    # a limit the first call fit under but the rewrite does not
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", "write_guard"],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "WIKI_WEAVER_MAX_WRITER_CHARS": str(sent + 1)},
    )
    assert r.stdout.strip().splitlines()[-1] == "oversized", r.stderr
    assert _cur(corpus)["writer_chars_per_call"] == [sent, actual]
    assert step(corpus, "hold", str(run)) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["reason"] == f"oversized-context: {actual} chars"
    assert row["writer_chars_per_call"] == [sent, actual]
    assert row["writer_input_chars"] == actual  # the largest call
    assert row["model_calls"] == 2  # brief + first write; the rewrite never ran


def test_a_rewrite_under_the_limit_proceeds_and_is_recorded(corpus: Path, tmp_path: Path):
    run = tmp_path / "r"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    step(corpus, "page_select")
    (corpus / SUMMARY).write_text(
        page("O", [ORCHARD], cite(corpus, "words not in it at all") + "\n")
    )
    assert step(corpus, "checks") == (0, "rewrite")
    assert step(corpus, "write_guard") == (0, "ok")
    cur = _cur(corpus)
    assert len(cur["writer_chars_per_call"]) == 2 and cur["model_calls"] == 3
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / SUMMARY).write_text(page("O", [ORCHARD], q + "\n"))
    assert step(corpus, "checks") == (0, "pass")
    step(corpus, "commit", str(run))
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["writer_input_chars"] == max(row["writer_chars_per_call"])
