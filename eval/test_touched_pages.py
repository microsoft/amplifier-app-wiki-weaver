"""Tests for the deterministic ingest touched-pages manifest derivation.

The production break this guards: an ingest agent can successfully edit wiki
pages but be stopped before it writes its cooperative manifest.  The manifest
must therefore be derived from the root-page delta, while retaining any
agent-written paths that did make it to disk.
"""

from __future__ import annotations

from pathlib import Path

from wiki_weaver.touched_pages import derive_manifest, snapshot_pages


def _manifest_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _snapshot(wiki: Path) -> Path:
    snapshot = wiki / ".ai" / "pages.snapshot"
    snapshot_pages(wiki, snapshot)
    return snapshot


def test_derive_manifest_lists_added_root_page(tmp_path: Path) -> None:
    """An agent-created page reaches assess even with no cooperative manifest."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"

    (wiki / "new-concept.md").write_text("# New concept\n", encoding="utf-8")

    assert derive_manifest(wiki, snapshot, manifest) == ["new-concept.md"]
    assert _manifest_lines(manifest) == ["new-concept.md"]


def test_derive_manifest_lists_modified_root_page(tmp_path: Path) -> None:
    """A content change to an existing page is listed from its SHA-256 delta."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "concept.md"
    page.write_text("# Before\n", encoding="utf-8")
    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"

    page.write_text("# After\n", encoding="utf-8")

    assert derive_manifest(wiki, snapshot, manifest) == ["concept.md"]
    assert _manifest_lines(manifest) == ["concept.md"]


def test_derive_manifest_leaves_no_file_for_an_untouched_wiki(tmp_path: Path) -> None:
    """No page delta and no agent manifest preserves the gate's failure signal."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "concept.md").write_text("# Stable\n", encoding="utf-8")
    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"

    assert derive_manifest(wiki, snapshot, manifest) == []
    assert not manifest.exists()


def test_derive_manifest_merges_agent_manifest_deduped_and_sorted(
    tmp_path: Path,
) -> None:
    """Agent paths and real page deltas form one sorted, duplicate-free work-list."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "changed.md"
    page.write_text("# Before\n", encoding="utf-8")
    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"
    manifest.write_text(
        "agent-listed.md\nchanged.md\nagent-listed.md\n",
        encoding="utf-8",
    )
    page.write_text("# After\n", encoding="utf-8")

    assert derive_manifest(wiki, snapshot, manifest) == [
        "agent-listed.md",
        "changed.md",
    ]
    assert _manifest_lines(manifest) == ["agent-listed.md", "changed.md"]


def test_derive_manifest_works_when_agent_manifest_is_absent(tmp_path: Path) -> None:
    """Missing cooperative state never hides a real page edit."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    page = wiki / "changed.md"
    page.write_text("# Before\n", encoding="utf-8")
    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"
    page.write_text("# After\n", encoding="utf-8")

    assert derive_manifest(wiki, snapshot, manifest) == ["changed.md"]


def test_derive_manifest_ignores_process_directories(tmp_path: Path) -> None:
    """Only root pages, the same set validate() assesses, can satisfy the gate."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "stable.md").write_text("# Stable\n", encoding="utf-8")
    ignored = [".ai", ".wiki", "_inbox", "_sources", "_failed", "nested"]
    for dirname in ignored:
        page = wiki / dirname / "hidden.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text("# Before\n", encoding="utf-8")

    snapshot = _snapshot(wiki)
    manifest = wiki / ".ai" / "touched-pages.txt"
    for dirname in ignored:
        (wiki / dirname / "hidden.md").write_text("# After\n", encoding="utf-8")

    assert derive_manifest(wiki, snapshot, manifest) == []
    assert not manifest.exists()