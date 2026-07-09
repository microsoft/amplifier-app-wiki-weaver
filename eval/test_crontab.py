"""Unit tests for wiki_weaver.crontab -- multi-instance managed-block text ops
+ crontab subprocess wrappers.

Coverage (per docs/designs/scheduled-ingestion-spec.md \u00a714): the pure text
layer (upsert/remove/scan/malformed-isolation) needs NO real crontab binary.
Subprocess wrapper behavior is verified by monkeypatching subprocess.run.

Pure stdlib -- no engine, no LLM, no network.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver import crontab as ct  # noqa: E402


# ---------------------------------------------------------------------------
# upsert_block: insert then replace, multi-instance isolation
# ---------------------------------------------------------------------------


def test_upsert_insert_then_replace_leaves_other_instance_untouched() -> None:
    text = ""

    text = ct.upsert_block(text, "instance-a", "*/5 * * * * cmd --wiki a")
    text = ct.upsert_block(text, "instance-b", "*/10 * * * * cmd --wiki b")

    blocks = ct.scan(text)
    assert set(blocks) == {"instance-a", "instance-b"}
    assert blocks["instance-a"].cron_line == "*/5 * * * * cmd --wiki a"
    assert blocks["instance-b"].cron_line == "*/10 * * * * cmd --wiki b"

    b_block_before = blocks["instance-b"].block_text

    # Replace A's line only.
    text = ct.upsert_block(text, "instance-a", "*/1 * * * * cmd --wiki a --new")

    blocks_after = ct.scan(text)
    assert blocks_after["instance-a"].cron_line == "*/1 * * * * cmd --wiki a --new"
    # B's block is byte-for-byte unchanged.
    assert blocks_after["instance-b"].block_text == b_block_before


def test_upsert_preserves_unrelated_cron_lines() -> None:
    text = "0 3 * * * /usr/bin/backup.sh\n"
    text = ct.upsert_block(text, "instance-a", "*/5 * * * * cmd --wiki a")

    assert "0 3 * * * /usr/bin/backup.sh" in text
    blocks = ct.scan(text)
    assert "instance-a" in blocks


# ---------------------------------------------------------------------------
# remove_block: idempotent, isolated
# ---------------------------------------------------------------------------


def test_remove_block_removes_only_targeted_id() -> None:
    text = ""
    text = ct.upsert_block(text, "instance-a", "*/5 * * * * cmd a")
    text = ct.upsert_block(text, "instance-b", "*/10 * * * * cmd b")

    text = ct.remove_block(text, "instance-a")

    blocks = ct.scan(text)
    assert "instance-a" not in blocks
    assert "instance-b" in blocks
    assert blocks["instance-b"].cron_line == "*/10 * * * * cmd b"


def test_remove_block_idempotent_on_absent() -> None:
    text = "0 3 * * * /usr/bin/backup.sh\n"
    result = ct.remove_block(text, "never-installed")
    assert result == text


# ---------------------------------------------------------------------------
# scan / scan_malformed
# ---------------------------------------------------------------------------


def test_scan_returns_all_well_formed_blocks_keyed_by_id() -> None:
    text = ""
    text = ct.upsert_block(text, "a", "* * * * * cmd a")
    text = ct.upsert_block(text, "b", "* * * * * cmd b")
    text = ct.upsert_block(text, "c", "* * * * * cmd c")

    blocks = ct.scan(text)
    assert set(blocks) == {"a", "b", "c"}
    for iid, block in blocks.items():
        assert block.instance_id == iid


def test_malformed_orphan_begin_marker_isolated_from_sibling() -> None:
    good = ct.upsert_block("", "b", "* * * * * cmd b")
    orphan_begin = f"{ct.marker_begin('a')}\n* * * * * cmd a\n"
    text = orphan_begin + good

    malformed = ct.scan_malformed(text)
    assert "a" in malformed
    assert "b" not in malformed

    # A well-formed sibling can still be upserted/removed without corruption.
    text2 = ct.upsert_block(text, "b", "*/2 * * * * cmd b2")
    blocks = ct.scan(text2)
    assert blocks["b"].cron_line == "*/2 * * * * cmd b2"

    text3 = ct.remove_block(text, "b")
    assert "b" not in ct.scan(text3)
    # Orphan marker for "a" survives untouched (we don't touch malformed ids).
    assert ct.marker_begin("a") in text3


def test_malformed_orphan_end_marker_detected() -> None:
    text = f"* * * * * cmd a\n{ct.marker_end('a')}\n"
    malformed = ct.scan_malformed(text)
    assert "a" in malformed


def test_malformed_duplicate_begin_markers_detected() -> None:
    text = (
        f"{ct.marker_begin('a')}\n"
        f"{ct.marker_begin('a')}\n"
        "* * * * * cmd a\n"
        f"{ct.marker_end('a')}\n"
    )
    malformed = ct.scan_malformed(text)
    assert "a" in malformed


def test_malformed_misordered_markers_detected() -> None:
    # end appears BEFORE begin
    text = f"{ct.marker_end('a')}\n* * * * * cmd a\n{ct.marker_begin('a')}\n"
    malformed = ct.scan_malformed(text)
    assert "a" in malformed


def test_upsert_raises_on_malformed_existing_block() -> None:
    text = f"{ct.marker_begin('a')}\n* * * * * cmd a\n"  # orphan begin, no end
    with pytest.raises(ValueError):
        ct.upsert_block(text, "a", "* * * * * cmd a2")


def test_remove_raises_on_malformed_existing_block() -> None:
    text = f"{ct.marker_begin('a')}\n* * * * * cmd a\n"  # orphan begin, no end
    with pytest.raises(ValueError):
        ct.remove_block(text, "a")


# ---------------------------------------------------------------------------
# Escaping (must-fix #7)
# ---------------------------------------------------------------------------


def test_build_cron_line_escapes_path_with_space_dollar_semicolon_quote() -> None:
    tricky = Path("/tmp/my wiki $HOME; 'quoted'")
    line = ct.build_cron_line(
        cron_expr="*/5 * * * *",
        binary_path="/usr/local/bin/wiki-weaver",
        canonical_wiki=tricky,
        env_file=None,
    )

    assert line.startswith("*/5 * * * * ")
    command = line[len("*/5 * * * * ") :]
    tokens = shlex.split(command)
    assert tokens[0] == "/usr/local/bin/wiki-weaver"
    assert tokens[1] == "schedule"
    assert tokens[2] == "run-now"
    assert tokens[3] == "--wiki"
    assert tokens[4] == str(tricky)


def test_build_cron_line_with_env_file_wraps_in_bash_lc_round_trips() -> None:
    tricky = Path("/tmp/my wiki $HOME; 'quoted'")
    env_file = Path("/tmp/env file; with $pecial chars")

    line = ct.build_cron_line(
        cron_expr="*/5 * * * *",
        binary_path="/usr/local/bin/wiki-weaver",
        canonical_wiki=tricky,
        env_file=env_file,
    )

    command = line[len("*/5 * * * * ") :]
    outer_tokens = shlex.split(command)
    assert outer_tokens[0] == "bash"
    assert outer_tokens[1] == "-lc"
    inner_script = outer_tokens[2]

    # The inner script is itself built from two independently shlex.quote()d
    # values (env_file and canonical_wiki); verify each round-trips as its own
    # quoted substring rather than naively splitting the script on ";" (which
    # would corrupt paths that themselves contain a literal semicolon).
    assert shlex.quote(str(env_file)) in inner_script
    assert shlex.quote(str(tricky)) in inner_script
    assert inner_script.startswith("set -a; source ")


# ---------------------------------------------------------------------------
# Subprocess wrappers (monkeypatched -- no real crontab binary required)
# ---------------------------------------------------------------------------


def test_crontab_available_uses_shutil_which(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ct.shutil,
        "which",
        lambda name: "/usr/bin/crontab" if name == "crontab" else None,
    )
    assert ct.crontab_available() is True

    monkeypatch.setattr(ct.shutil, "which", lambda name: None)
    assert ct.crontab_available() is False


def test_read_current_crontab_empty_on_nonzero_rc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="no crontab for user")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ct.read_current_crontab() == ""


def test_read_current_crontab_returns_stdout_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="* * * * * cmd\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert ct.read_current_crontab() == "* * * * * cmd\n"


def test_write_crontab_raises_runtime_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad crontab")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        ct.write_crontab("* * * * * cmd\n")


def test_write_crontab_succeeds_silently_on_rc_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ct.write_crontab("* * * * * cmd\n")  # must not raise


def test_resolve_binary_raises_file_not_found_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ct.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        ct.resolve_binary()


def test_resolve_binary_returns_which_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ct.shutil, "which", lambda name: "/usr/local/bin/wiki-weaver")
    assert ct.resolve_binary() == "/usr/local/bin/wiki-weaver"
