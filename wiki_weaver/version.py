"""`--version` string: ``YYYY.MM.DD-<short sha>`` of the commit this install came from."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PKG = Path(__file__).resolve().parent
_BUILD_INFO = _PKG / "_build_info.txt"


def resolve_version(fallback: str) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cd-%h", "--date=format:%Y.%m.%d"],
            cwd=_PKG,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    if _BUILD_INFO.is_file():
        return _BUILD_INFO.read_text().strip()
    return f"0000.00.00-unknown ({fallback})"
