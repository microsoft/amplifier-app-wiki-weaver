"""Per-wiki run lock: ``<wiki>/.wiki/ingest.lock``.

The lock is an exclusive ``flock`` on that file, held by the open descriptor for the life
of the run. The kernel decides who holds it, so there is no window between creating the
file and writing a PID in which a second run can mistake a live lock for a stale one, and
a crashed holder's lock is released by the OS (no stale-PID heuristics). The holder's PID
is written into the file for diagnostics only; nothing reads it to decide anything.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

_held: dict[str, int] = {}


def lock_path(wiki: Path) -> Path:
    return Path(wiki) / ".wiki" / "ingest.lock"


def holder(path: Path) -> int | None:
    """PID recorded by the current holder, for messages only."""
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def acquire(path: Path) -> bool:
    """True if this process now holds the lock; never blocks."""
    path = Path(path)
    if str(path) in _held:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.pwrite(fd, str(os.getpid()).encode(), 0)
    _held[str(path)] = fd
    return True


def release(path: Path) -> None:
    fd = _held.pop(str(Path(path)), None)
    if fd is None:
        return
    try:
        os.ftruncate(fd, 0)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_held(path: Path) -> bool:
    """True if some process holds the lock right now (probe without keeping it)."""
    path = Path(path)
    if not path.exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False
