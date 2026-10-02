"""Per-wiki ingest lock: ``<wiki>/.wiki/ingest.lock`` holding the owner's PID.

Atomic O_EXCL create; a lock whose PID is dead or unreadable is stale and reclaimed.
Release removes the file only if it records this process's PID.
"""

from __future__ import annotations

import os
from pathlib import Path


def lock_path(wiki: Path) -> Path:
    return Path(wiki) / ".wiki" / "ingest.lock"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _create(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(str(os.getpid()))
    return True


def holder(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def acquire(path: Path) -> bool:
    """True if this process now holds the lock; never blocks."""
    if _create(path):
        return True
    pid = holder(path)
    if pid is not None and pid != os.getpid() and _alive(pid):
        return False
    path.unlink(missing_ok=True)  # stale or unreadable
    return _create(path)


def release(path: Path) -> None:
    if holder(path) == os.getpid():
        path.unlink(missing_ok=True)
