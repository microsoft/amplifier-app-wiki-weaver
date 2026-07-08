"""Generic, reusable, liveness-checked PID lock primitive.

Pure stdlib. No wiki-weaver imports. Ports the algorithm already proven in
``wiki_weaver.lib._acquire_migration_lock`` (lines ~1798-1855) into a
standalone, reusable module so both the per-wiki ingest lock and the global
crontab-mutation lock (two distinct locks, different paths/lifetimes) can
share one battle-tested implementation without touching ``migrate``.

Guards *cross-process* concurrency (cron ticks are separate processes) via
``open(path, "x")`` (O_EXCL) + PID-liveness probing -- exactly what a
``threading.Lock`` cannot do, since that only guards threads within one
process.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

__all__ = ["LockResult", "pid_alive", "try_acquire", "release", "pid_lock"]


@dataclass
class LockResult:
    acquired: bool
    holder_pid: int | None = None  # live holder's PID when acquired is False
    reclaimed_stale: bool = False  # a stale lock was detected & removed during acquire


def pid_alive(pid: int) -> bool:
    """True if a process with *pid* is alive (probe via os.kill(pid, 0)).

    ProcessLookupError -> False (gone). PermissionError -> True (exists, other user).
    Any other OSError -> False (treat as not-provably-alive / stale).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _try_create(lock_path: Path) -> bool:
    """Atomic O_EXCL create + PID write. True on success, False if it already exists."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(lock_path, "x", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def try_acquire(lock_path: Path) -> LockResult:
    """Atomically attempt to acquire *lock_path*; never blocks, never raises on contention.

    Algorithm (identical in spirit to lib._acquire_migration_lock):
      1. Try open(lock_path, "x") (O_EXCL); on success write str(os.getpid()), return
         LockResult(acquired=True).
      2. On FileExistsError, read the recorded PID:
         - unreadable/invalid int  -> stale: unlink, retry create once.
         - pid_alive(pid) is True   -> return LockResult(False, holder_pid=pid).
         - pid_alive(pid) is False  -> stale: unlink, retry create once
                                       (reclaimed_stale=True).
      3. If the single retry also loses the race -> return LockResult(False, holder_pid=<pid
         re-read or None>).  Losing the reclaim race means someone else won; do not force.
    Creates lock_path.parent (mkdir parents=True, exist_ok=True) before the first create.
    """
    if _try_create(lock_path):
        return LockResult(acquired=True)

    # Lock exists -- inspect the holder.
    raw_pid = ""
    try:
        raw_pid = lock_path.read_text(encoding="utf-8").strip()
        existing_pid = int(raw_pid)
    except (ValueError, OSError):
        # Unreadable / invalid PID content -> stale, reclaim.
        lock_path.unlink(missing_ok=True)
        if _try_create(lock_path):
            return LockResult(acquired=True, reclaimed_stale=True)
        return LockResult(acquired=False, holder_pid=_reread_pid(lock_path))

    if pid_alive(existing_pid):
        return LockResult(acquired=False, holder_pid=existing_pid)

    # Stale -- reclaim and retry the create once.
    lock_path.unlink(missing_ok=True)
    if _try_create(lock_path):
        return LockResult(acquired=True, reclaimed_stale=True)
    return LockResult(acquired=False, holder_pid=_reread_pid(lock_path))


def _reread_pid(lock_path: Path) -> int | None:
    """Best-effort re-read of the PID after losing a reclaim race."""
    try:
        return int(lock_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def release(lock_path: Path) -> None:
    """Remove *lock_path* iff it records THIS process's PID (never steal another's lock).
    Missing file -> no-op. Unreadable/foreign PID -> leave it (loud is the caller's job).
    """
    try:
        raw_pid = lock_path.read_text(encoding="utf-8").strip()
        recorded_pid = int(raw_pid)
    except FileNotFoundError:
        return
    except (ValueError, OSError):
        return
    if recorded_pid == os.getpid():
        lock_path.unlink(missing_ok=True)


@contextmanager
def pid_lock(lock_path: Path) -> Iterator[LockResult]:
    """Context manager: yields the LockResult; on exit, release() iff we acquired.
    Callers inspect result.acquired to branch (run vs skip). Does NOT raise on contention --
    the skip path is a normal outcome, not an error.
    """
    result = try_acquire(lock_path)
    try:
        yield result
    finally:
        if result.acquired:
            release(lock_path)
