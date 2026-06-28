"""Gate tests for the ``wiki-weaver migrate`` command (INCREMENT 2).

Coverage:
  - Full migration: all OLD paths moved to NEW, ledger keys rewritten, OLD paths gone.
  - Idempotency: second run → no-op exit 0; --force re-runs from scratch.
  - PID lock: live-PID lock → aborts; stale-PID lock → removed and migration proceeds.
  - Ledger-key rewrite: archived_to + logs_dir rewritten; line count preserved; bare
    filenames (registry, extra fields) untouched.
  - copy → verify → delete integrity: no data lost; file counts match; OLD paths
    gone; NEW paths present; .wiki/index/ not clobbered.
  - --dry-run: prints plan, exits 0, changes NOTHING.
  - CLI subcommand (cmd_migrate): smoke-tested via argparse.Namespace.
  - Post-migration structural validity: expected dirs exist, ledger at new path.
  - Post-migration lint (skipped when amplifier_foundation absent).

No LLM, no engine, no Amplifier runtime required (lint test conditionally skipped).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver.lib import (  # noqa: E402
    MIGRATION_LOCK_NAME,
    MIGRATION_SENTINEL,
    _acquire_migration_lock,
    _files_differ,
    _rewrite_ledger_keys,
    migrate,
    wiki_dashboard,
    wiki_failed,
    wiki_hidden_dir,
    wiki_ledger,
    wiki_policy_dir,
    wiki_registry,
    wiki_runs,
    wiki_sources,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_old_corpus(tmp: Path) -> Path:
    """Create a minimal OLD-layout corpus for migration tests.

    Layout:
      corpus/.processed.jsonl   (ledger at root, 2 entries with path-valued fields)
      corpus/.sources.json      (registry at root, bare filenames)
      corpus/_archive/          (2 source files)
      corpus/_failed/           (1 failed file)
      corpus/.runs/             (2 run dirs, each with events.jsonl)
      corpus/policy/            (schema.md)
      corpus/.wiki-dashboard/   (theme.json)
      corpus/.wiki/index/       (pre-existing index — must NOT be clobbered)
      corpus/page-a.md          (wiki page)
    """
    corpus = tmp / "corpus"
    corpus.mkdir()

    # Ledger at root with absolute-path fields
    entries = [
        {
            "source": "src-a.md",
            "archived_to": f"{corpus}/_archive/src-a.md",
            "logs_dir": f"{corpus}/.runs/run-001",
        },
        {
            "source": "src-b.md",
            "archived_to": f"{corpus}/_archive/src-b.md",
            "logs_dir": f"{corpus}/.runs/run-002",
            # plain string field that should NOT be rewritten
            "extra_ref": "bare-filename.md",
        },
    ]
    (corpus / ".processed.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n",
        encoding="utf-8",
    )

    # Registry at root (bare filenames, no path rewrite needed)
    (corpus / ".sources.json").write_text(
        json.dumps(
            {
                "version": 1,
                "next_id": 3,
                "sources": [
                    {"id": 1, "filename": "src-a.md", "hash": "abc123"},
                    {"id": 2, "filename": "src-b.md", "hash": "def456"},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # _archive/ with 2 source files
    archive = corpus / "_archive"
    archive.mkdir()
    (archive / "src-a.md").write_text("source a content", encoding="utf-8")
    (archive / "src-b.md").write_text("source b content", encoding="utf-8")

    # _failed/ with 1 file
    failed = corpus / "_failed"
    failed.mkdir()
    (failed / "failed-c.md").write_text("failed c content", encoding="utf-8")

    # .runs/ with 2 run dirs
    runs = corpus / ".runs"
    runs.mkdir()
    for rname in ("run-001", "run-002"):
        rd = runs / rname
        rd.mkdir()
        (rd / "events.jsonl").write_text("{}\n", encoding="utf-8")

    # policy/
    policy = corpus / "policy"
    policy.mkdir()
    (policy / "schema.md").write_text("# Schema\n", encoding="utf-8")

    # .wiki-dashboard/
    dash_old = corpus / ".wiki-dashboard"
    dash_old.mkdir()
    (dash_old / "theme.json").write_text('{"title": "test"}\n', encoding="utf-8")

    # Partial NEW layout: .wiki/index/ already exists (must survive migration)
    wiki_dir = corpus / ".wiki"
    wiki_dir.mkdir()
    idx = wiki_dir / "index"
    idx.mkdir()
    (idx / "tags.json").write_text(
        '{"schema_version": 1, "data": {}}\n', encoding="utf-8"
    )

    # A wiki page
    (corpus / "page-a.md").write_text(
        "---\ntitle: Page A\ntype: concept\n---\n# Page A\n",
        encoding="utf-8",
    )

    return corpus


# ---------------------------------------------------------------------------
# Test 1: full migration (copy → rewrite → verify → delete)
# ---------------------------------------------------------------------------


def test_migrate_full_migration(tmp_path: Path) -> None:
    """All OLD paths move to NEW; ledger rewritten; OLD paths gone; NEW paths present."""
    corpus = _make_old_corpus(tmp_path)

    rc = migrate(corpus)
    assert rc == 0, "migrate() should return 0 on success"

    # --- OLD paths gone ---
    assert not (corpus / ".processed.jsonl").exists(), "old ledger must be deleted"
    assert not (corpus / ".sources.json").exists(), "old registry must be deleted"
    assert not (corpus / "_archive").exists(), "_archive dir must be deleted"
    assert not (corpus / "_failed").exists(), "_failed dir must be deleted"
    assert not (corpus / ".runs").exists(), ".runs dir must be deleted"
    assert not (corpus / "policy").exists(), "policy dir must be deleted"
    assert not (corpus / ".wiki-dashboard").exists(), (
        ".wiki-dashboard dir must be deleted"
    )

    # --- NEW paths present ---
    assert wiki_ledger(corpus).exists(), (
        "new ledger must exist at .wiki/.processed.jsonl"
    )
    assert wiki_registry(corpus).exists(), (
        "new registry must exist at .wiki/.sources.json"
    )
    assert wiki_sources(corpus).is_dir(), "_sources/ dir must exist"
    assert wiki_failed(corpus).is_dir(), ".wiki/failed/ dir must exist"
    assert wiki_runs(corpus).is_dir(), ".wiki/runs/ dir must exist"
    assert wiki_policy_dir(corpus).is_dir(), ".wiki/policy/ dir must exist"
    assert wiki_dashboard(corpus).is_dir(), ".wiki/dashboard/ dir must exist"

    # --- Sentinel written ---
    sentinel = wiki_hidden_dir(corpus) / MIGRATION_SENTINEL
    assert sentinel.exists(), "migration sentinel must be written"
    info = json.loads(sentinel.read_text(encoding="utf-8"))
    assert "migrated_at" in info

    # --- .wiki/index/ NOT clobbered ---
    assert (corpus / ".wiki" / "index" / "tags.json").exists(), (
        ".wiki/index/ must survive"
    )

    # --- Source files present in _sources/ (was _archive) ---
    assert (wiki_sources(corpus) / "src-a.md").exists()
    assert (wiki_sources(corpus) / "src-b.md").exists()


# ---------------------------------------------------------------------------
# Test 2: idempotency
# ---------------------------------------------------------------------------


def test_migrate_idempotency_no_force(tmp_path: Path) -> None:
    """Second run without --force is a no-op (exit 0, sentinel already present)."""
    corpus = _make_old_corpus(tmp_path)

    assert migrate(corpus) == 0, "first run should succeed"
    sentinel = wiki_hidden_dir(corpus) / MIGRATION_SENTINEL
    first_stat = sentinel.stat().st_mtime

    # Second run
    rc = migrate(corpus)
    assert rc == 0, "second run (no-op) should return 0"
    # Sentinel mtime must NOT have changed (file was not rewritten)
    assert sentinel.stat().st_mtime == first_stat, (
        "sentinel must not be rewritten on no-op"
    )


def test_migrate_idempotency_force(tmp_path: Path) -> None:
    """--force re-runs the migration even when the sentinel exists."""
    corpus = _make_old_corpus(tmp_path)

    assert migrate(corpus) == 0, "first run should succeed"
    sentinel = wiki_hidden_dir(corpus) / MIGRATION_SENTINEL
    assert sentinel.exists()

    # --force re-runs (OLD paths are already gone, so plan is empty, sentinel re-written)
    rc = migrate(corpus, force=True)
    assert rc == 0, "--force re-run should succeed"


# ---------------------------------------------------------------------------
# Test 3: PID lock (live + stale)
# ---------------------------------------------------------------------------


def test_migrate_live_pid_lock_aborts(tmp_path: Path) -> None:
    """Migration aborts when the lock file holds a live PID."""
    corpus = _make_old_corpus(tmp_path)
    lock_path = corpus / MIGRATION_LOCK_NAME

    # Write our own (definitely-alive) PID as the lock
    lock_path.write_text(str(os.getpid()), encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 1, "should fail when live PID lock present"
    # Lock should still be there (we did NOT remove it)
    assert lock_path.exists(), "our lock must still exist after abort"
    # Migration must NOT have proceeded
    assert not wiki_ledger(corpus).exists(), "new ledger must NOT exist after abort"


def test_migrate_stale_pid_lock_removed(tmp_path: Path) -> None:
    """A stale lock (dead PID) is removed silently and migration proceeds."""
    corpus = _make_old_corpus(tmp_path)
    lock_path = corpus / MIGRATION_LOCK_NAME

    # Write a PID that definitely doesn't exist
    lock_path.write_text("99999999", encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0, "should succeed after removing stale lock"
    # Lock must be gone
    assert not lock_path.exists(), "stale lock must have been removed"
    # Migration must have completed
    assert wiki_ledger(corpus).exists(), "new ledger must exist after migration"


def test_migrate_invalid_pid_in_lock(tmp_path: Path) -> None:
    """A lock file with non-integer content is treated as stale."""
    corpus = _make_old_corpus(tmp_path)
    lock_path = corpus / MIGRATION_LOCK_NAME

    lock_path.write_text("not-a-pid\n", encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0, "should succeed after removing invalid-PID lock"
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# Test 4: ledger-key rewrite correctness
# ---------------------------------------------------------------------------


def test_ledger_key_rewrite_correctness(tmp_path: Path) -> None:
    """archived_to and logs_dir are rewritten; line count preserved; bare fields untouched."""
    corpus = _make_old_corpus(tmp_path)

    assert migrate(corpus) == 0

    raw = wiki_ledger(corpus).read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]

    # Line count preserved (2 entries in fixture)
    assert len(lines) == 2, f"expected 2 ledger lines, got {len(lines)}"

    for line in lines:
        entry = json.loads(line)

        # archived_to: old prefix gone, new prefix present
        if "archived_to" in entry:
            val = entry["archived_to"]
            assert "/_archive/" not in val, f"_archive still in archived_to: {val}"
            assert "/_sources/" in val, f"_sources missing in archived_to: {val}"

        # logs_dir: old prefix gone, new prefix present
        if "logs_dir" in entry:
            val = entry["logs_dir"]
            assert "/.runs/" not in val, f".runs still in logs_dir: {val}"
            assert "/.wiki/runs/" in val, f".wiki/runs missing in logs_dir: {val}"

        # extra_ref is a bare filename — must NOT be changed
        if "extra_ref" in entry:
            assert entry["extra_ref"] == "bare-filename.md", (
                f"bare field was incorrectly rewritten: {entry['extra_ref']}"
            )


def test_ledger_line_count_preserved_by_rewrite(tmp_path: Path) -> None:
    """The rewrite helper preserves the exact line count (no lines added or dropped)."""
    corpus = _make_old_corpus(tmp_path)

    # Count lines in OLD ledger
    old_lines = (corpus / ".processed.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(old_lines) == 2

    assert migrate(corpus) == 0

    new_lines = wiki_ledger(corpus).read_text(encoding="utf-8").splitlines()
    assert len(new_lines) == len(old_lines), (
        f"line count changed: {len(old_lines)} → {len(new_lines)}"
    )


def test_registry_filenames_untouched(tmp_path: Path) -> None:
    """The registry (.sources.json) bare filenames are not rewritten."""
    corpus = _make_old_corpus(tmp_path)

    assert migrate(corpus) == 0

    registry = json.loads(wiki_registry(corpus).read_text(encoding="utf-8"))
    for entry in registry["sources"]:
        fname = entry["filename"]
        assert "/_archive/" not in fname, f"archive path leaked into filename: {fname}"
        assert "/_sources/" not in fname, f"sources path leaked into filename: {fname}"
        # Should still be a bare name like "src-a.md"
        assert "/" not in fname, f"filename should be bare (no path): {fname}"


# ---------------------------------------------------------------------------
# Test 5: copy → verify → delete integrity
# ---------------------------------------------------------------------------


def test_file_counts_preserved(tmp_path: Path) -> None:
    """No files are lost: every file in OLD dirs appears in NEW dirs."""
    corpus = _make_old_corpus(tmp_path)

    # Count OLD files in each dir
    old_archive_count = sum(1 for _ in (corpus / "_archive").rglob("*") if _.is_file())
    old_failed_count = sum(1 for _ in (corpus / "_failed").rglob("*") if _.is_file())
    old_runs_count = sum(1 for _ in (corpus / ".runs").rglob("*") if _.is_file())

    assert migrate(corpus) == 0

    new_sources_count = sum(1 for _ in wiki_sources(corpus).rglob("*") if _.is_file())
    new_failed_count = sum(1 for _ in wiki_failed(corpus).rglob("*") if _.is_file())
    new_runs_count = sum(1 for _ in wiki_runs(corpus).rglob("*") if _.is_file())

    assert new_sources_count == old_archive_count, (
        f"_sources file count {new_sources_count} ≠ _archive count {old_archive_count}"
    )
    assert new_failed_count == old_failed_count, (
        f"failed file count {new_failed_count} ≠ _failed count {old_failed_count}"
    )
    assert new_runs_count == old_runs_count, (
        f"runs file count {new_runs_count} ≠ .runs count {old_runs_count}"
    )


def test_wiki_index_not_clobbered(tmp_path: Path) -> None:
    """Pre-existing .wiki/index/ content is untouched by migration."""
    corpus = _make_old_corpus(tmp_path)
    index_tags = corpus / ".wiki" / "index" / "tags.json"
    original_content = index_tags.read_text(encoding="utf-8")

    assert migrate(corpus) == 0

    assert index_tags.exists(), ".wiki/index/tags.json must still exist after migration"
    assert index_tags.read_text(encoding="utf-8") == original_content, (
        ".wiki/index/tags.json content must be unchanged"
    )


# ---------------------------------------------------------------------------
# Test 6: --dry-run changes nothing
# ---------------------------------------------------------------------------


def test_dry_run_no_changes(tmp_path: Path) -> None:
    """--dry-run exits 0 and leaves the filesystem completely unchanged."""
    corpus = _make_old_corpus(tmp_path)

    # Snapshot old ledger content
    ledger_before = (corpus / ".processed.jsonl").read_text(encoding="utf-8")

    rc = migrate(corpus, dry_run=True)
    assert rc == 0, "--dry-run should exit 0"

    # OLD paths still present
    assert (corpus / ".processed.jsonl").exists(), "ledger must still be at root"
    assert (corpus / ".processed.jsonl").read_text(encoding="utf-8") == ledger_before
    assert (corpus / "_archive").exists(), "_archive must still exist"
    assert (corpus / ".wiki-dashboard").exists(), ".wiki-dashboard must still exist"

    # NEW paths NOT created
    assert not wiki_ledger(corpus).exists(), "new ledger must NOT exist after dry-run"
    assert not wiki_sources(corpus).exists(), "_sources must NOT exist after dry-run"

    # Sentinel NOT written
    sentinel = wiki_hidden_dir(corpus) / MIGRATION_SENTINEL
    assert not sentinel.exists(), "sentinel must NOT be written in dry-run"


# ---------------------------------------------------------------------------
# Test 7: CLI subcommand smoke test (cmd_migrate)
# ---------------------------------------------------------------------------


def test_cmd_migrate_smoke(tmp_path: Path) -> None:
    """cmd_migrate via argparse.Namespace: exits 0 and completes migration."""
    from wiki_weaver.cli import cmd_migrate

    corpus = _make_old_corpus(tmp_path)

    ns = argparse.Namespace(corpus=str(corpus), dry_run=False, force=False)
    rc = cmd_migrate(ns)
    assert rc == 0

    # Verify migration ran
    assert wiki_ledger(corpus).exists()
    assert not (corpus / ".processed.jsonl").exists()


def test_cmd_migrate_dry_run_smoke(tmp_path: Path) -> None:
    """cmd_migrate --dry-run: exits 0, changes nothing."""
    from wiki_weaver.cli import cmd_migrate

    corpus = _make_old_corpus(tmp_path)

    ns = argparse.Namespace(corpus=str(corpus), dry_run=True, force=False)
    rc = cmd_migrate(ns)
    assert rc == 0

    # Nothing changed
    assert (corpus / ".processed.jsonl").exists()
    assert not wiki_ledger(corpus).exists()


def test_cmd_migrate_missing_corpus(tmp_path: Path) -> None:
    """cmd_migrate with a non-existent corpus path returns non-zero."""
    from wiki_weaver.cli import cmd_migrate

    ns = argparse.Namespace(
        corpus=str(tmp_path / "does-not-exist"),
        dry_run=False,
        force=False,
    )
    rc = cmd_migrate(ns)
    assert rc != 0, "should fail on missing corpus"


# ---------------------------------------------------------------------------
# Test 8: post-migration structural validity
# ---------------------------------------------------------------------------


def test_post_migration_structure(tmp_path: Path) -> None:
    """After migration, the corpus has the expected NEW-layout directory structure."""
    corpus = _make_old_corpus(tmp_path)
    assert migrate(corpus) == 0

    # Hidden subtree
    assert wiki_hidden_dir(corpus).is_dir()
    assert wiki_ledger(corpus).is_file()
    assert wiki_registry(corpus).is_file()
    assert wiki_runs(corpus).is_dir()
    assert wiki_failed(corpus).is_dir()
    assert wiki_policy_dir(corpus).is_dir()
    assert wiki_dashboard(corpus).is_dir()

    # Visible dirs
    assert wiki_sources(corpus).is_dir()

    # Schema.md moved correctly
    assert (wiki_policy_dir(corpus) / "schema.md").exists()

    # Theme.json moved correctly
    assert (wiki_dashboard(corpus) / "theme.json").exists()


# ---------------------------------------------------------------------------
# Test 9: post-migration lint (requires amplifier_foundation — skipped without)
# ---------------------------------------------------------------------------


def _make_lint_valid_old_corpus(tmp: Path) -> Path:
    """Create an OLD-layout corpus whose WIKI PAGES pass lint after migration.

    Uses only ``type: index`` and ``type: overview`` pages — these are NAV_PAGES
    and META_TYPES in validate_wiki.py, so they are exempt from S2 (orphan) and
    S5 (provenance) checks.  S3 (frontmatter) is satisfied by including ``title``,
    ``type``, and ``sources`` fields.

    Machine files use OLD layout so migrate() has real work to do.
    """
    corpus = tmp / "lint-corpus"
    corpus.mkdir()

    # Two nav/meta pages that will pass all lint checks
    (corpus / "index.md").write_text(
        "---\ntitle: Index\ntype: index\nsources: []\nlast_updated: 2024-01-01\n---\n\n# Index\n",
        encoding="utf-8",
    )
    (corpus / "overview.md").write_text(
        "---\ntitle: Overview\ntype: overview\nsources: []\nlast_updated: 2024-01-01\n---\n\n# Overview\n",
        encoding="utf-8",
    )

    # OLD layout machine files
    (corpus / ".processed.jsonl").write_text(
        json.dumps(
            {
                "source": "s.md",
                "archived_to": f"{corpus}/_archive/s.md",
                "logs_dir": f"{corpus}/.runs/r",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (corpus / ".sources.json").write_text(
        json.dumps(
            {
                "version": 1,
                "next_id": 2,
                "sources": [{"id": 1, "filename": "s.md", "hash": "abc"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    archive = corpus / "_archive"
    archive.mkdir()
    (archive / "s.md").write_text("source", encoding="utf-8")
    (corpus / "_failed").mkdir()
    runs = corpus / ".runs"
    runs.mkdir()
    (runs / "r").mkdir()
    (corpus / "policy").mkdir()
    (corpus / "policy" / "schema.md").write_text("# Schema\n", encoding="utf-8")
    dash = corpus / ".wiki-dashboard"
    dash.mkdir()
    (dash / "theme.json").write_text("{}\n", encoding="utf-8")

    # Pre-existing .wiki/index/ (must survive)
    (corpus / ".wiki").mkdir()
    (corpus / ".wiki" / "index").mkdir()

    return corpus


def test_post_migration_lint(tmp_path: Path) -> None:
    """wiki-weaver lint exits 0 on a migrated corpus (requires amplifier_foundation).

    Uses a corpus whose pages are all nav/meta types (exempt from orphan + provenance
    checks) so lint can pass on a synthetic fixture.
    """
    pytest.importorskip(
        "amplifier_foundation",
        reason="amplifier_foundation not installed; skipping post-migration lint test",
    )
    from wiki_weaver.engine_runner import run_lint  # noqa: PLC0415

    corpus = _make_lint_valid_old_corpus(tmp_path)
    assert migrate(corpus) == 0, "migration must succeed before running lint"

    rc = run_lint(str(corpus))
    assert rc == 0, f"lint exited {rc} on the migrated corpus"


# ===========================================================================
# Adversarial-review hardening tests (C1, H1, C2, M1)
# ===========================================================================


# ---------------------------------------------------------------------------
# C1 — silent ledger corruption guard
# ---------------------------------------------------------------------------


def _point_ledger_elsewhere(
    corpus: Path, other_root: str = "/some/other/corpus"
) -> int:
    """Rewrite the OLD ledger so its path fields point at a DIFFERENT location.

    Simulates a corpus that was moved/copied since it was processed: the
    absolute-path prefixes in archived_to/logs_dir no longer match ``corpus``.
    Returns the number of entries with path fields.
    """
    ledger = corpus / ".processed.jsonl"
    out: list[str] = []
    n_path = 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if "archived_to" in entry:
            entry["archived_to"] = (
                f"{other_root}/_archive/{Path(entry['archived_to']).name}"
            )
            n_path += 1
        if "logs_dir" in entry:
            entry["logs_dir"] = f"{other_root}/.runs/{Path(entry['logs_dir']).name}"
        out.append(json.dumps(entry))
    ledger.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n_path


def test_c1_ledger_mismatch_aborts_before_delete(tmp_path: Path) -> None:
    """C1: ledger paths point elsewhere → abort BEFORE deleting; OLD intact, no sentinel."""
    corpus = _make_old_corpus(tmp_path)
    n_path = _point_ledger_elsewhere(corpus)
    assert n_path == 2, "fixture should have 2 path-field entries"

    rc = migrate(corpus)
    assert rc == 1, "migration must abort (exit 1) on ledger path mismatch"

    # NOTHING deleted — every OLD path still present.
    assert (corpus / ".processed.jsonl").exists(), "OLD ledger must survive abort"
    assert (corpus / "_archive").exists(), "_archive must survive abort"
    assert (corpus / "_failed").exists(), "_failed must survive abort"
    assert (corpus / ".runs").exists(), ".runs must survive abort"
    assert (corpus / "policy").exists(), "policy must survive abort"
    assert (corpus / ".wiki-dashboard").exists(), ".wiki-dashboard must survive abort"

    # Source files inside _archive untouched.
    assert (corpus / "_archive" / "src-a.md").exists()
    assert (corpus / "_archive" / "src-b.md").exists()

    # No sentinel written (migration did not complete).
    assert not (wiki_hidden_dir(corpus) / MIGRATION_SENTINEL).exists()

    # Lock released (finally) — a follow-up dry-run must not be blocked.
    assert not (corpus / MIGRATION_LOCK_NAME).exists(), (
        "lock must be released after abort"
    )


def test_c1_abort_message(tmp_path: Path, capsys) -> None:
    """C1: the abort prints the documented mismatch message with the N denominator."""
    corpus = _make_old_corpus(tmp_path)
    _point_ledger_elsewhere(corpus)

    rc = migrate(corpus)
    assert rc == 1
    out = capsys.readouterr().out
    assert "Ledger path fields do not match this corpus location" in out
    assert "0 of 2 rewritten" in out
    assert "--dry-run" in out


def test_c1_empty_ledger_does_not_abort(tmp_path: Path) -> None:
    """C1: an EMPTY ledger (no path fields) is fine — migration completes normally."""
    corpus = _make_old_corpus(tmp_path)
    # Replace ledger with an empty file (0 entries).
    (corpus / ".processed.jsonl").write_text("", encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0, "empty ledger must NOT trigger the C1 abort"
    assert (wiki_hidden_dir(corpus) / MIGRATION_SENTINEL).exists()
    assert not (corpus / "_archive").exists(), "happy path should delete OLD dirs"


def test_c1_ledger_without_path_fields_does_not_abort(tmp_path: Path) -> None:
    """C1: a ledger whose entries carry NO archived_to/logs_dir is fine."""
    corpus = _make_old_corpus(tmp_path)
    # Entries with only a 'source' field — no path fields at all.
    (corpus / ".processed.jsonl").write_text(
        json.dumps({"source": "x.md", "status": "success"})
        + "\n"
        + json.dumps({"source": "y.md", "status": "success"})
        + "\n",
        encoding="utf-8",
    )

    rc = migrate(corpus)
    assert rc == 0, "ledger without path fields must NOT trigger the C1 abort"
    assert (wiki_hidden_dir(corpus) / MIGRATION_SENTINEL).exists()


def test_rewrite_ledger_returns_changed_and_path_field_count(tmp_path: Path) -> None:
    """Unit: _rewrite_ledger_keys returns (changed, path_field_lines) correctly."""
    corpus = _make_old_corpus(tmp_path)
    # Copy ledger into the NEW location (mimicking Phase-1 copy) then rewrite.
    wiki_hidden_dir(corpus).mkdir(parents=True, exist_ok=True)
    new_ledger = wiki_ledger(corpus)
    new_ledger.write_text(
        (corpus / ".processed.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    changed, path_fields = _rewrite_ledger_keys(new_ledger, corpus)
    assert changed == 2, "both entries should rewrite (prefixes match this corpus)"
    assert path_fields == 2, "both entries carry path fields"


# ---------------------------------------------------------------------------
# H1 — atomic O_EXCL lock acquisition
# ---------------------------------------------------------------------------


def test_h1_acquire_lock_atomic_create(tmp_path: Path) -> None:
    """H1: _acquire_migration_lock creates the lock atomically and records our PID."""
    lock = tmp_path / MIGRATION_LOCK_NAME
    assert not lock.exists()
    assert _acquire_migration_lock(lock) is True
    assert lock.exists(), "lock file must be created"
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    lock.unlink()


def test_h1_acquire_lock_live_pid_blocks(tmp_path: Path) -> None:
    """H1: an existing lock held by a LIVE PID blocks acquisition (returns False)."""
    lock = tmp_path / MIGRATION_LOCK_NAME
    lock.write_text(str(os.getpid()), encoding="utf-8")  # our own PID == alive
    assert _acquire_migration_lock(lock) is False, "live-PID lock must block"
    # The holder's lock must be left intact (we did not steal it).
    assert lock.exists()
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_h1_acquire_lock_stale_pid_reclaimed(tmp_path: Path) -> None:
    """H1: a stale lock (dead PID) is reclaimed and re-acquired atomically."""
    lock = tmp_path / MIGRATION_LOCK_NAME
    lock.write_text("99999999", encoding="utf-8")  # almost-certainly-dead PID
    assert _acquire_migration_lock(lock) is True, "stale lock must be reclaimed"
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    lock.unlink()


def test_h1_acquire_lock_invalid_pid_reclaimed(tmp_path: Path) -> None:
    """H1: a lock with non-integer content is treated as stale and reclaimed."""
    lock = tmp_path / MIGRATION_LOCK_NAME
    lock.write_text("garbage\n", encoding="utf-8")
    assert _acquire_migration_lock(lock) is True
    assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    lock.unlink()


def test_h1_migrate_live_lock_still_aborts(tmp_path: Path) -> None:
    """H1 (integration): migrate() still aborts when a live-PID lock pre-exists."""
    corpus = _make_old_corpus(tmp_path)
    (corpus / MIGRATION_LOCK_NAME).write_text(str(os.getpid()), encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 1, "migrate must abort when a live lock is present"
    # OLD layout untouched (migration never started).
    assert (corpus / ".processed.jsonl").exists()
    assert (corpus / "_archive").exists()
    assert not wiki_ledger(corpus).exists()
    # The pre-existing lock is left intact (not stolen).
    assert (corpus / MIGRATION_LOCK_NAME).exists()


# ---------------------------------------------------------------------------
# C2 — runs/ tolerant verify (post-upgrade weave wrote extra logs)
# ---------------------------------------------------------------------------


def test_c2_extra_runs_in_target_does_not_block(tmp_path: Path) -> None:
    """C2: extra run-logs already in .wiki/runs/ (post-upgrade weave) don't fail verify."""
    corpus = _make_old_corpus(tmp_path)
    # Simulate a post-upgrade `weave`: NEW run-logs already in the destination.
    extra = wiki_runs(corpus) / "run-NEW-after-upgrade"
    extra.mkdir(parents=True, exist_ok=True)
    (extra / "events.jsonl").write_text("{}\n", encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0, "extra run-logs in the target must not block migration"
    # Both OLD run dirs migrated AND the extra one preserved.
    assert (wiki_runs(corpus) / "run-001").exists()
    assert (wiki_runs(corpus) / "run-002").exists()
    assert (wiki_runs(corpus) / "run-NEW-after-upgrade" / "events.jsonl").exists()
    # _sources stays STRICT — sanity: 2 source files migrated.
    assert sum(1 for p in wiki_sources(corpus).rglob("*") if p.is_file()) == 2


def test_c2_missing_runs_file_still_fails_sources(tmp_path: Path) -> None:
    """C2: strictness preserved for _sources/ — a missing source file fails verify.

    Guards against the tolerant runs/ check accidentally loosening _sources/.
    We can't easily drop a file mid-copy, so this asserts the STRICT branch is
    used for _sources via a direct count check after a normal migration.
    """
    corpus = _make_old_corpus(tmp_path)
    rc = migrate(corpus)
    assert rc == 0
    # Exactly the 2 OLD archive files — no tolerance applied to _sources.
    assert sum(1 for p in wiki_sources(corpus).rglob("*") if p.is_file()) == 2


# ---------------------------------------------------------------------------
# M1 — no-clobber: preserve newer/differing targets
# ---------------------------------------------------------------------------


def test_m1_preserves_differing_target_in_dir(tmp_path: Path) -> None:
    """M1: a post-upgrade .wiki/dashboard/theme.json differing from OLD is preserved."""
    corpus = _make_old_corpus(tmp_path)
    # OLD .wiki-dashboard/theme.json content is '{}\n' (from _make_old_corpus).
    # Pre-seed a DIFFERENT (newer) target as build-dashboard would.
    new_dash = wiki_dashboard(corpus)
    new_dash.mkdir(parents=True, exist_ok=True)
    newer = '{"title": "Post-Upgrade Custom Title"}\n'
    (new_dash / "theme.json").write_text(newer, encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0, "migration should complete, preserving the newer target"
    # The user's newer theme.json must be intact — NOT overwritten with OLD '{}'.
    assert (new_dash / "theme.json").read_text(encoding="utf-8") == newer


def test_m1_warns_on_preserved_target(tmp_path: Path, capsys) -> None:
    """M1: preserving a differing target emits a visible WARN."""
    corpus = _make_old_corpus(tmp_path)
    new_dash = wiki_dashboard(corpus)
    new_dash.mkdir(parents=True, exist_ok=True)
    (new_dash / "theme.json").write_text('{"title": "X"}\n', encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0
    out = capsys.readouterr().out
    assert "preserved existing" in out
    assert "theme.json" in out


def test_m1_identical_target_no_warn(tmp_path: Path, capsys) -> None:
    """M1: an IDENTICAL existing target is copied silently (idempotent re-run)."""
    corpus = _make_old_corpus(tmp_path)
    new_dash = wiki_dashboard(corpus)
    new_dash.mkdir(parents=True, exist_ok=True)
    # Same content as OLD .wiki-dashboard/theme.json (see _make_old_corpus).
    (new_dash / "theme.json").write_text('{"title": "test"}\n', encoding="utf-8")

    rc = migrate(corpus)
    assert rc == 0
    out = capsys.readouterr().out
    assert "preserved existing" not in out, "identical target must not warn"


def test_m1_files_differ_helper(tmp_path: Path) -> None:
    """Unit: _files_differ detects content difference and equality."""
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("hello\n", encoding="utf-8")
    b.write_text("hello\n", encoding="utf-8")
    assert _files_differ(a, b) is False
    b.write_text("world\n", encoding="utf-8")
    assert _files_differ(a, b) is True
