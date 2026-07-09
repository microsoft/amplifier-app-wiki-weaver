"""Unit tests for wiki_weaver.pidlock -- the liveness-checked PID lock primitive.

Coverage (per docs/designs/scheduled-ingestion-spec.md \u00a714):
  - Acquire/release round-trip.
  - Liveness: a STALE lock (dead PID) is reclaimed -- file existence alone does
    NOT block acquisition. This is the #1 flagged gap the whole feature exists
    to close.
  - Liveness: a LIVE holder blocks acquisition.
  - Invalid/garbage PID content is treated as stale and reclaimed.
  - release() never steals a lock held by a different (live) PID.
  - pid_lock() context manager releases on normal exit AND on exception, and
    never releases when it did not acquire.

Pure stdlib -- no engine, no LLM, no network.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver import pidlock  # noqa: E402


# ---------------------------------------------------------------------------
# Acquire/release round-trip
# ---------------------------------------------------------------------------


def test_acquire_release_round_trip(tmp_path: Path) -> None:
    lock_path = tmp_path / "sub" / "test.lock"

    result = pidlock.try_acquire(lock_path)
    assert result.acquired is True
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())

    pidlock.release(lock_path)
    assert not lock_path.exists()


def test_try_acquire_blocked_by_other_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "test.lock"
    other_pid = 999999  # arbitrary, will be forced "alive" below
    lock_path.write_text(str(other_pid), encoding="utf-8")

    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: pid == other_pid)

    result = pidlock.try_acquire(lock_path)
    assert result.acquired is False
    assert result.holder_pid == other_pid
    # File existence alone must not have been mutated / stolen.
    assert lock_path.read_text(encoding="utf-8").strip() == str(other_pid)


# ---------------------------------------------------------------------------
# Liveness -- stale reclaim (the #1 flagged gap)
# ---------------------------------------------------------------------------


def test_stale_dead_pid_lock_is_reclaimed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock file recording a DEAD pid must be reclaimed, not treated as a block.

    This is the critical liveness assertion: file existence alone does NOT
    block acquisition -- only a LIVE holder does.
    """
    lock_path = tmp_path / "test.lock"
    dead_pid = 123456
    lock_path.write_text(str(dead_pid), encoding="utf-8")

    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: False)

    result = pidlock.try_acquire(lock_path)
    assert result.acquired is True
    assert result.reclaimed_stale is True
    # The lock now records OUR pid, not the dead one.
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_live_holder_blocks_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "test.lock"
    live_pid = 424242
    lock_path.write_text(str(live_pid), encoding="utf-8")

    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    result = pidlock.try_acquire(lock_path)
    assert result.acquired is False
    assert result.holder_pid == live_pid
    assert result.reclaimed_stale is False


@pytest.mark.parametrize("garbage", ["garbage", "", "  ", "12.5", "not-a-pid"])
def test_invalid_pid_content_is_treated_stale(tmp_path: Path, garbage: str) -> None:
    lock_path = tmp_path / "test.lock"
    lock_path.write_text(garbage, encoding="utf-8")

    result = pidlock.try_acquire(lock_path)
    assert result.acquired is True
    assert result.reclaimed_stale is True
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


# ---------------------------------------------------------------------------
# pid_alive() itself
# ---------------------------------------------------------------------------


def test_pid_alive_true_for_self() -> None:
    assert pidlock.pid_alive(os.getpid()) is True


def test_pid_alive_false_for_process_lookup_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _raise)
    assert pidlock.pid_alive(1) is False


def test_pid_alive_true_for_permission_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(os, "kill", _raise)
    assert pidlock.pid_alive(1) is True


# ---------------------------------------------------------------------------
# release() never steals
# ---------------------------------------------------------------------------


def test_release_never_steals_foreign_live_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    foreign_pid = os.getpid() + 1  # guaranteed different from our own pid
    lock_path.write_text(str(foreign_pid), encoding="utf-8")

    pidlock.release(lock_path)

    # File must remain untouched -- release() only removes locks that record
    # THIS process's own PID.
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8").strip() == str(foreign_pid)


def test_release_missing_file_is_noop(tmp_path: Path) -> None:
    lock_path = tmp_path / "does-not-exist.lock"
    pidlock.release(lock_path)  # must not raise
    assert not lock_path.exists()


# ---------------------------------------------------------------------------
# pid_lock() context manager
# ---------------------------------------------------------------------------


def test_pid_lock_releases_on_normal_exit(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    with pidlock.pid_lock(lock_path) as result:
        assert result.acquired is True
        assert lock_path.exists()
    assert not lock_path.exists()


def test_pid_lock_releases_on_exception(tmp_path: Path) -> None:
    lock_path = tmp_path / "test.lock"
    with pytest.raises(RuntimeError):
        with pidlock.pid_lock(lock_path) as result:
            assert result.acquired is True
            raise RuntimeError("boom")
    assert not lock_path.exists()


def test_pid_lock_does_not_release_when_not_acquired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "test.lock"
    other_pid = 555555
    lock_path.write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    with pidlock.pid_lock(lock_path) as result:
        assert result.acquired is False

    # The foreign lock must still be there -- we never acquired it, so exiting
    # the context manager must not touch it.
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8").strip() == str(other_pid)
