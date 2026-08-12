"""ask.load_index: index-first candidate retrieval via BM25, maintaining a
persisted wiki/.index/pages.json so repeated calls do not reopen every
page (CLI-CONTRACT.md: "must NOT open every page in the wiki")."""

from __future__ import annotations

import json
from pathlib import Path

from wiki_weaver.ask import load_index
from wiki_weaver.lib import WikiRoot

PAGE_TEMPLATE = """---
title: {title}
type: concept
---

# {title}

{body}
"""


def _seed_pages(wiki_root, n: int = 8) -> WikiRoot:
    for i in range(n):
        wiki_root.add_page(
            f"page{i}.md",
            PAGE_TEMPLATE.format(title=f"Page {i}", body=f"Content about topic-{i} and shared-context."),
        )
    return WikiRoot(wiki_root.root)


def test_load_index_writes_candidates_and_coverage(wiki_root, monkeypatch):
    wr = _seed_pages(wiki_root)
    monkeypatch.setenv("QUESTION", "topic-3")

    code = load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    assert code == 0
    out_path = wr.root / ".ai" / "candidate-pages.json"
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["question"] == "topic-3"
    assert any(c["page"] == "page3.md" for c in payload["candidates"])
    assert payload["coverage"]["wiki_pages"] == 8


def test_load_index_does_not_reopen_unchanged_pages(wiki_root, monkeypatch):
    wr = _seed_pages(wiki_root, n=10)
    monkeypatch.setenv("QUESTION", "topic-5")

    # Cold build: necessarily opens every page once to populate the index.
    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    opened: list[Path] = []
    real_read_text = Path.read_text

    def spy_read_text(self: Path, *args, **kwargs):
        if wr.wiki_dir in self.parents and self.suffix == ".md":
            opened.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    # Warm call: the index is fresh (no wiki page mtimes changed), so this
    # must NOT reopen any of the 10 wiki pages -- index-first, per contract.
    code = load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    assert code == 0
    assert opened == []


def test_load_index_incrementally_refreshes_only_changed_pages(wiki_root, monkeypatch):
    wr = _seed_pages(wiki_root, n=5)
    monkeypatch.setenv("QUESTION", "topic-1")
    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    # Mutate exactly one page -- only that page should be reopened on refresh.
    changed_path = wr.wiki_dir / "page1.md"
    changed_path.write_text(
        PAGE_TEMPLATE.format(title="Page 1", body="Updated content about topic-1 and something new."),
        encoding="utf-8",
    )

    opened: list[Path] = []
    real_read_text = Path.read_text

    def spy_read_text(self: Path, *args, **kwargs):
        if wr.wiki_dir in self.parents and self.suffix == ".md":
            opened.append(self)
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy_read_text)

    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    assert opened == [changed_path]


def test_empty_wiki_produces_no_candidates_not_a_crash(wiki_root, monkeypatch):
    wr = WikiRoot(wiki_root.root)
    monkeypatch.setenv("QUESTION", "anything")

    code = load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])

    assert code == 0
    payload = json.loads((wr.root / ".ai" / "candidate-pages.json").read_text(encoding="utf-8"))
    assert payload["candidates"] == []
    assert payload["coverage"]["wiki_pages"] == 0
