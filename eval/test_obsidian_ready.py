"""Gate tests for INCREMENT 3a — ``ensure_obsidian_ready()``.

Coverage:
  - ``init`` on a fresh corpus → ``.gitignore`` has the §14-F5 entries + marker;
    ``.obsidian/`` is seeded from the package template.
  - Idempotency: running ``ensure_obsidian_ready`` twice → no duplicate marker
    in ``.gitignore``; ``.obsidian/`` unchanged on second call.
  - Pre-existing ``.gitignore`` with user content → user lines preserved intact;
    wiki-weaver block appended exactly once.
  - Pre-existing ``.obsidian/`` → never touched by ``ensure_obsidian_ready``.
  - ``migrate`` also leaves the corpus Obsidian-ready.
  - Template directory contains the expected files (``app.json``,
    ``core-plugins.json``).
  - ``app.json`` template encodes ``userIgnoreFilters`` for ``.wiki/``.

No LLM, no engine, no Amplifier runtime required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.lib import (  # noqa: E402
    _OBSIDIAN_GITIGNORE_LINES,
    _OBSIDIAN_GITIGNORE_MARKER,
    _OBSIDIAN_TEMPLATE_DIR,
    ensure_obsidian_ready,
    init,
    migrate,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_fresh_corpus(tmp: Path) -> Path:
    """Return a bare new directory suitable for ``init()``."""
    corpus = tmp / "corpus"
    corpus.mkdir()
    return corpus


def _make_old_corpus(tmp: Path) -> Path:
    """Create a minimal OLD-layout corpus for migration tests.

    Mirrors the layout used in ``test_migrate.py::_make_old_corpus``.
    """
    import json as _json

    corpus = tmp / "corpus"
    corpus.mkdir()

    entries = [
        {
            "source": "src-a.md",
            "archived_to": f"{corpus}/_archive/src-a.md",
            "logs_dir": f"{corpus}/.runs/run-001",
        }
    ]
    (corpus / ".processed.jsonl").write_text(
        "\n".join(_json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )
    (corpus / ".sources.json").write_text(
        _json.dumps({"version": 1, "next_id": 2, "sources": []}) + "\n",
        encoding="utf-8",
    )
    archive = corpus / "_archive"
    archive.mkdir()
    (archive / "src-a.md").write_text("source a", encoding="utf-8")

    failed = corpus / "_failed"
    failed.mkdir()
    (failed / "fail-x.md").write_text("fail x", encoding="utf-8")

    runs = corpus / ".runs"
    runs.mkdir()
    rd = runs / "run-001"
    rd.mkdir()
    (rd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    policy = corpus / "policy"
    policy.mkdir()
    (policy / "schema.md").write_text("# Schema\n", encoding="utf-8")

    dash_old = corpus / ".wiki-dashboard"
    dash_old.mkdir()
    (dash_old / "theme.json").write_text('{"title": "test"}\n', encoding="utf-8")

    wiki_dir = corpus / ".wiki"
    wiki_dir.mkdir()

    (corpus / "page-a.md").write_text(
        "---\ntitle: Page A\ntype: concept\n---\n# Page A\n", encoding="utf-8"
    )
    return corpus


# ---------------------------------------------------------------------------
# 1. Template directory
# ---------------------------------------------------------------------------


def test_template_dir_exists() -> None:
    """Package template directory must be present on disk."""
    assert _OBSIDIAN_TEMPLATE_DIR.is_dir(), (
        f"Template dir missing: {_OBSIDIAN_TEMPLATE_DIR}"
    )


def test_template_contains_app_json() -> None:
    """``app.json`` must be present in the Obsidian template."""
    app_json = _OBSIDIAN_TEMPLATE_DIR / "app.json"
    assert app_json.exists(), f"app.json not found in {_OBSIDIAN_TEMPLATE_DIR}"


def test_template_contains_core_plugins_json() -> None:
    """``core-plugins.json`` must be present in the Obsidian template."""
    cp_json = _OBSIDIAN_TEMPLATE_DIR / "core-plugins.json"
    assert cp_json.exists(), f"core-plugins.json not found in {_OBSIDIAN_TEMPLATE_DIR}"


def test_app_json_has_user_ignore_filters() -> None:
    """``app.json`` template must declare ``userIgnoreFilters`` containing '.wiki'."""
    app_json = _OBSIDIAN_TEMPLATE_DIR / "app.json"
    data = json.loads(app_json.read_text(encoding="utf-8"))
    assert "userIgnoreFilters" in data, "app.json must have userIgnoreFilters"
    assert ".wiki" in data["userIgnoreFilters"], (
        ".wiki must be in userIgnoreFilters (belt-and-suspenders for dotfolder exclusion)"
    )


# ---------------------------------------------------------------------------
# 2. ensure_obsidian_ready — gitignore
# ---------------------------------------------------------------------------


def test_gitignore_created_on_fresh_corpus(tmp_path: Path) -> None:
    """Fresh corpus: ``.gitignore`` is created with the F5 entries."""
    corpus = _make_fresh_corpus(tmp_path)
    ensure_obsidian_ready(corpus)

    gitignore = corpus / ".gitignore"
    assert gitignore.exists(), ".gitignore must be created"
    content = gitignore.read_text(encoding="utf-8")
    assert _OBSIDIAN_GITIGNORE_MARKER in content, "Block marker must be present"


@pytest.mark.parametrize(
    "entry", [e for e in _OBSIDIAN_GITIGNORE_LINES if not e.startswith("#")]
)
def test_gitignore_has_f5_entries(tmp_path: Path, entry: str) -> None:
    """All §14-F5 pattern entries appear in the generated ``.gitignore``."""
    corpus = _make_fresh_corpus(tmp_path)
    ensure_obsidian_ready(corpus)
    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    assert entry in content, f"F5 entry {entry!r} missing from .gitignore"


def test_gitignore_idempotent_no_duplicate_marker(tmp_path: Path) -> None:
    """Running ``ensure_obsidian_ready`` twice must not duplicate the marker."""
    corpus = _make_fresh_corpus(tmp_path)
    ensure_obsidian_ready(corpus)
    ensure_obsidian_ready(corpus)

    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    count = content.count(_OBSIDIAN_GITIGNORE_MARKER)
    assert count == 1, (
        f"Marker appears {count} times; expected exactly 1 (idempotency broken)"
    )


def test_gitignore_preserves_user_content(tmp_path: Path) -> None:
    """Pre-existing user lines in ``.gitignore`` are preserved after the call."""
    corpus = _make_fresh_corpus(tmp_path)
    user_lines = "# user content\n*.log\nbuild/\n"
    (corpus / ".gitignore").write_text(user_lines, encoding="utf-8")

    ensure_obsidian_ready(corpus)

    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    assert "*.log" in content, "User entry '*.log' must be preserved"
    assert "build/" in content, "User entry 'build/' must be preserved"
    assert _OBSIDIAN_GITIGNORE_MARKER in content, "Block marker must be appended"


def test_gitignore_block_appended_once_to_existing(tmp_path: Path) -> None:
    """Block is appended once even when run multiple times on an existing file."""
    corpus = _make_fresh_corpus(tmp_path)
    (corpus / ".gitignore").write_text("# pre-existing\n", encoding="utf-8")

    ensure_obsidian_ready(corpus)
    ensure_obsidian_ready(corpus)

    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    assert content.count(_OBSIDIAN_GITIGNORE_MARKER) == 1


def test_gitignore_block_has_separator_from_user_content(tmp_path: Path) -> None:
    """Block is separated from existing user content by a newline."""
    corpus = _make_fresh_corpus(tmp_path)
    (corpus / ".gitignore").write_text("# pre-existing\n", encoding="utf-8")
    ensure_obsidian_ready(corpus)
    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    # Marker must not immediately follow user content on the same line
    marker_idx = content.index(_OBSIDIAN_GITIGNORE_MARKER)
    before_marker = content[:marker_idx]
    assert before_marker.endswith("\n"), "A newline must precede the block marker"


# ---------------------------------------------------------------------------
# 3. ensure_obsidian_ready — .obsidian/ seeding
# ---------------------------------------------------------------------------


def test_obsidian_dir_seeded_on_fresh_corpus(tmp_path: Path) -> None:
    """Fresh corpus: ``.obsidian/`` is created from the package template."""
    corpus = _make_fresh_corpus(tmp_path)
    ensure_obsidian_ready(corpus)

    obsidian_dir = corpus / ".obsidian"
    assert obsidian_dir.is_dir(), ".obsidian/ must be seeded"
    assert (obsidian_dir / "app.json").exists(), (
        "app.json must be present in seeded .obsidian/"
    )
    assert (obsidian_dir / "core-plugins.json").exists(), (
        "core-plugins.json must be present in seeded .obsidian/"
    )


def test_obsidian_seeded_app_json_has_user_ignore_filters(tmp_path: Path) -> None:
    """Seeded ``app.json`` carries the ``userIgnoreFilters`` for ``.wiki/``."""
    corpus = _make_fresh_corpus(tmp_path)
    ensure_obsidian_ready(corpus)
    data = json.loads((corpus / ".obsidian" / "app.json").read_text(encoding="utf-8"))
    assert "userIgnoreFilters" in data
    assert ".wiki" in data["userIgnoreFilters"]


def test_obsidian_existing_vault_untouched(tmp_path: Path) -> None:
    """Pre-existing ``.obsidian/`` is never modified by ``ensure_obsidian_ready``."""
    corpus = _make_fresh_corpus(tmp_path)
    obsidian_dir = corpus / ".obsidian"
    obsidian_dir.mkdir()
    sentinel = obsidian_dir / "my-custom-file.json"
    sentinel.write_text('{"custom": true}', encoding="utf-8")
    (obsidian_dir / "workspace.json").write_text('{"user":"setting"}', encoding="utf-8")

    ensure_obsidian_ready(corpus)

    # Custom file must survive untouched
    assert sentinel.exists(), "User's custom file must survive"
    assert json.loads(sentinel.read_text(encoding="utf-8")) == {"custom": True}


def test_obsidian_idempotent_existing_vault_unchanged(tmp_path: Path) -> None:
    """Running twice on a corpus that already has ``.obsidian/`` is a no-op."""
    corpus = _make_fresh_corpus(tmp_path)
    obsidian_dir = corpus / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "app.json").write_text('{"existing": true}', encoding="utf-8")

    ensure_obsidian_ready(corpus)
    ensure_obsidian_ready(corpus)

    data = json.loads((obsidian_dir / "app.json").read_text(encoding="utf-8"))
    assert data == {"existing": True}, "Existing app.json must not be overwritten"


# ---------------------------------------------------------------------------
# 4. init() integration
# ---------------------------------------------------------------------------


def test_init_seeds_gitignore(tmp_path: Path) -> None:
    """``init()`` leaves the corpus with a ``.gitignore`` containing the block."""
    corpus = _make_fresh_corpus(tmp_path)
    rc = init(corpus)
    assert rc == 0

    gitignore = corpus / ".gitignore"
    assert gitignore.exists(), ".gitignore must exist after init"
    content = gitignore.read_text(encoding="utf-8")
    assert _OBSIDIAN_GITIGNORE_MARKER in content
    assert ".obsidian/workspace.json" in content


def test_init_seeds_obsidian_dir(tmp_path: Path) -> None:
    """``init()`` creates ``.obsidian/`` when it doesn't exist."""
    corpus = _make_fresh_corpus(tmp_path)
    rc = init(corpus)
    assert rc == 0
    assert (corpus / ".obsidian").is_dir(), ".obsidian/ must be seeded by init"


def test_init_does_not_clobber_existing_obsidian(tmp_path: Path) -> None:
    """``init()`` on a corpus with an existing ``.obsidian/`` leaves it untouched."""
    corpus = _make_fresh_corpus(tmp_path)
    obsidian_dir = corpus / ".obsidian"
    obsidian_dir.mkdir()
    custom_file = obsidian_dir / "custom.json"
    custom_file.write_text('{"mine": 1}', encoding="utf-8")

    rc = init(corpus)
    assert rc == 0
    assert custom_file.exists(), "Custom file must survive init"


# ---------------------------------------------------------------------------
# 5. migrate() integration
# ---------------------------------------------------------------------------


def test_migrate_leaves_corpus_obsidian_ready(tmp_path: Path) -> None:
    """``migrate()`` ensures ``.gitignore`` and ``.obsidian/`` are both present."""
    corpus = _make_old_corpus(tmp_path)
    rc = migrate(corpus)
    assert rc == 0, "migrate() should succeed"

    gitignore = corpus / ".gitignore"
    assert gitignore.exists(), ".gitignore must be present after migrate"
    content = gitignore.read_text(encoding="utf-8")
    assert _OBSIDIAN_GITIGNORE_MARKER in content

    assert (corpus / ".obsidian").is_dir(), ".obsidian/ must be seeded after migrate"


def test_migrate_gitignore_has_f5_entries(tmp_path: Path) -> None:
    """After ``migrate``, ``.gitignore`` contains the concrete F5 pattern entries."""
    corpus = _make_old_corpus(tmp_path)
    migrate(corpus)

    content = (corpus / ".gitignore").read_text(encoding="utf-8")
    for entry in _OBSIDIAN_GITIGNORE_LINES:
        if entry.startswith("#"):
            continue
        assert entry in content, (
            f"F5 entry {entry!r} missing from post-migrate .gitignore"
        )


def test_migrate_does_not_clobber_existing_obsidian(tmp_path: Path) -> None:
    """``migrate()`` does not touch a pre-existing ``.obsidian/`` vault."""
    corpus = _make_old_corpus(tmp_path)
    obsidian_dir = corpus / ".obsidian"
    obsidian_dir.mkdir()
    custom = obsidian_dir / "workspace.json"
    custom.write_text('{"user": "settings"}', encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0

    assert custom.exists()
    assert json.loads(custom.read_text(encoding="utf-8")) == {"user": "settings"}
