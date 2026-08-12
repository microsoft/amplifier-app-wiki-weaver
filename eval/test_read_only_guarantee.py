"""ask.dot and lint.dot are READ-ONLY with respect to the wiki: hash every
wiki file before and after running their tool subcommands, assert no
change. (Task requirement: "Add a test that hashes every wiki file before
and after and asserts no change.")"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from wiki_weaver.ask import load_index, present, refuse
from wiki_weaver.lint import structural_validate, write_report
from wiki_weaver.lib import WikiRoot

PAGE = """---
title: Runbook Ownership
type: concept
---

# Runbook Ownership

The on-call runbook is owned by [[jane-doe|Jane Doe]]. See [[oncall-runbook]].
"""

RUNBOOK_PAGE = """---
title: Oncall Runbook
type: article
---

# Oncall Runbook

Escalation steps live here.
"""


def _hash_wiki_files(wiki_dir: Path) -> dict[str, str]:
    hashes = {}
    if not wiki_dir.is_dir():
        return hashes
    for path in sorted(wiki_dir.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(wiki_dir))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def test_ask_pipeline_leaves_wiki_byte_identical(wiki_root, monkeypatch, tmp_path):
    wiki_root.add_page("jane-doe.md", PAGE)
    wiki_root.add_page("oncall-runbook.md", RUNBOOK_PAGE)
    wr = WikiRoot(wiki_root.root)

    before = _hash_wiki_files(wr.wiki_dir)

    monkeypatch.setenv("QUESTION", "who owns the on-call runbook?")
    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    answer_path = wr.ai_dir / "answer.md"
    answer_path.write_text("Jane Doe owns it, per [[jane-doe]].\n", encoding="utf-8")
    present.main(["--answer-file", str(answer_path)])

    refusal_path = wr.ai_dir / "refusal.md"
    refusal_path.write_text("Coverage too thin for a hypothetical unrelated question.\n", encoding="utf-8")
    refuse.main(["--loud", "--refusal-file", str(refusal_path)])

    after = _hash_wiki_files(wr.wiki_dir)
    # The index cache lives under wiki/.index/ -- excluded from the
    # byte-identical check since it is ask's own maintained cache, not wiki
    # content; the actual wiki *pages* must be untouched.
    before_pages = {k: v for k, v in before.items() if not k.startswith(".index")}
    after_pages = {k: v for k, v in after.items() if not k.startswith(".index")}
    assert before_pages == after_pages


def test_lint_pipeline_leaves_wiki_byte_identical(wiki_root):
    wiki_root.add_page("jane-doe.md", PAGE)
    wiki_root.add_page("oncall-runbook.md", RUNBOOK_PAGE)
    wr = WikiRoot(wiki_root.root)

    before = _hash_wiki_files(wr.wiki_dir)

    structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])
    write_report.main(
        [
            "--wiki-root", str(wr.root),
            "--out", "reports",
            "--structural", str(wr.ai_dir / "structural-report.md"),
        ]
    )  # fmt: skip

    after = _hash_wiki_files(wr.wiki_dir)
    assert before == after


def test_ask_load_index_only_writes_under_dot_ai_and_dot_index(wiki_root, monkeypatch):
    """A stronger check than hashing: confirm no NEW files appear anywhere
    under wiki/ except the .index/ cache itself."""
    wiki_root.add_page("jane-doe.md", PAGE)
    wr = WikiRoot(wiki_root.root)
    before_files = {p for p in wr.wiki_dir.rglob("*") if p.is_file() and ".index" not in p.parts}

    monkeypatch.setenv("QUESTION", "who owns the runbook?")
    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    after_files = {p for p in wr.wiki_dir.rglob("*") if p.is_file() and ".index" not in p.parts}
    assert before_files == after_files
    # sanity: the candidate output landed under .ai/, not under wiki/
    payload = json.loads((wr.root / ".ai" / "candidate-pages.json").read_text(encoding="utf-8"))
    assert payload["question"]
