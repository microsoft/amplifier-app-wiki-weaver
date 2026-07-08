"""Instance ID canonicalization, storage layout, and config/state IO.

This is the most safety-critical module of the scheduled-ingestion feature:
get canonicalization wrong and two aliases of one wiki get two locks, and the
parallelism=1 concurrent-write race returns. See ``instance_id`` below.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "canonical_wiki_path",
    "instance_id",
    "data_root",
    "config_root",
    "instance_data_dir",
    "instance_config_dir",
    "ingest_lock_path",
    "crontab_lock_path",
    "logs_dir",
    "ingest_log_path",
    "env_file_path",
    "InstanceConfig",
    "RunState",
    "write_instance_config",
    "read_instance_config",
    "read_run_state",
    "write_run_state",
    "list_instance_ids",
    "delete_instance",
]


# ---------------------------------------------------------------------------
# Canonicalization (must-fix #3 -- the critical one)
# ---------------------------------------------------------------------------


def canonical_wiki_path(wiki: str | Path) -> Path:
    """Fully canonical, alias-free wiki path.

    expanduser() -> resolve() (resolves symlinks, normalizes '..', absolutizes,
    strips trailing slash). Matches the existing convention in lib.migrate /
    lib.ingest (``Path(x).expanduser().resolve()``). All of
    {./my-wiki, my-wiki/, /abs/my-wiki, /symlink/to/my-wiki} collapse to ONE
    path here.
    """
    return Path(wiki).expanduser().resolve()


def instance_id(wiki: str | Path) -> str:
    """Deterministic, filesystem-safe, collision-free instance id from the canonical path.

    id = "{slug}-{digest}" where:
      canon  = canonical_wiki_path(wiki)
      slug   = re.sub(r'[^a-z0-9]+','-', canon.name.lower()).strip('-')  or 'wiki'
      digest = sha256(str(canon).encode('utf-8')).hexdigest()[:12]
    slug is human-readable (the wiki dir's basename); digest guarantees uniqueness
    and absorbs any two distinct canonical paths that share a basename. Windows-safe
    by construction (slug is [a-z0-9-] only; see IMPLEMENTATION_PHILOSOPHY
    path-sanitization).
    """
    canon = canonical_wiki_path(wiki)
    slug = re.sub(r"[^a-z0-9]+", "-", canon.name.lower()).strip("-") or "wiki"
    digest = hashlib.sha256(str(canon).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


# ---------------------------------------------------------------------------
# Storage layout & the single env override
# ---------------------------------------------------------------------------


def data_root() -> Path:
    env = os.environ.get("WIKI_WEAVER_DATA_DIR")
    if env:
        return Path(env).expanduser() / "data"
    base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base).expanduser() / "wiki-weaver"


def config_root() -> Path:
    env = os.environ.get("WIKI_WEAVER_DATA_DIR")
    if env:
        return Path(env).expanduser() / "config"
    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base).expanduser() / "wiki-weaver"


def instance_data_dir(id: str) -> Path:
    d = data_root() / "instances" / id
    d.mkdir(parents=True, exist_ok=True)
    return d


def instance_config_dir(id: str) -> Path:
    d = config_root() / "instances" / id
    d.mkdir(parents=True, exist_ok=True)
    return d


def ingest_lock_path(wiki: str | Path) -> Path:
    return instance_data_dir(instance_id(wiki)) / "ingest.lock"


def crontab_lock_path() -> Path:
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    return root / "crontab.lock"


def logs_dir(id: str) -> Path:
    d = instance_data_dir(id) / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ingest_log_path(id: str) -> Path:
    return logs_dir(id) / "ingest.log"


def env_file_path(id: str) -> Path:
    return instance_config_dir(id) / "env"


# ---------------------------------------------------------------------------
# Config & state dataclasses + atomic IO
# ---------------------------------------------------------------------------


@dataclass
class InstanceConfig:
    instance_id: str
    canonical_wiki: str  # str(canonical_wiki_path(...)) -- the stored, display path
    cron_expr: str  # the 5-field cron expression actually installed
    every: str | None  # friendly interval as given ("5m"), or None if --cron used
    alert_after: int  # consecutive-skip threshold for escalation (default 3)
    uses_env_file: bool  # True if an env snapshot was written at install
    created_at: str  # ISO-8601 UTC


@dataclass
class RunState:
    consecutive_skips: int = 0
    alert_active: bool = False
    last_run_at: str | None = None
    last_exit: int | None = None
    last_duration_s: float | None = None
    last_skip_at: str | None = None
    last_holder_pid: int | None = None  # PID that held the lock on the most recent skip


def _atomic_write_json(path: Path, data: dict) -> None:
    """Atomic JSON write: tempfile.mkstemp + os.replace, BaseException-safe cleanup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink(missing_ok=True)
        raise


def _instance_config_path(id: str) -> Path:
    return instance_config_dir(id) / "instance.json"


def _run_state_path(id: str) -> Path:
    return instance_data_dir(id) / "run-state.json"


def write_instance_config(cfg: InstanceConfig) -> None:
    _atomic_write_json(_instance_config_path(cfg.instance_id), asdict(cfg))


def read_instance_config(id: str) -> InstanceConfig | None:
    path = _instance_config_path(id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return InstanceConfig(**raw)


def read_run_state(id: str) -> RunState:
    path = _run_state_path(id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return RunState()
    return RunState(**raw)


def write_run_state(id: str, state: RunState) -> None:
    _atomic_write_json(_run_state_path(id), asdict(state))


def list_instance_ids() -> list[str]:
    root = config_root() / "instances"
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir())


def delete_instance(id: str) -> bool:
    """Remove both the config and data instance dirs. Returns True if anything existed."""
    existed = False
    for root in (config_root() / "instances" / id, data_root() / "instances" / id):
        if root.exists():
            existed = True
            shutil.rmtree(root, ignore_errors=True)
    return existed
