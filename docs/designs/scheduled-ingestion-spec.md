# Scheduled Ingestion — Implementation Specification

> **Status:** BUILD-READY spec. Turns the settled design in
> `docs/designs/wiki-weaver-platform.md` §11.2 (cron-only continuous ingestion) into a
> concrete, buildable brick set. Hand directly to `modular-builder`.
>
> **Authoritative design (do not re-litigate):** `wiki-weaver-platform.md` §11.1–§11.2.
> Settled decisions inherited verbatim: N independent per-wiki instances (no multi-mapping
> daemon); cron-only trigger (no fs-watch, no persistent process, no systemd/launchd);
> the existing `ingest()` core stays **unchanged** — this feature adds only a triggering
> layer on top.
>
> **Date:** 2026-07-08

---

## 1. Scope & intent

Add a `wiki-weaver schedule` subcommand group that installs an OS-cron entry per wiki. Each
tick runs one check-and-drain of that wiki's `_inbox/` through the existing `ingest()` core,
skipping cleanly if a previous run for the same wiki is still going. Many independent wikis
can be scheduled on one device, each fully isolated.

**In scope:** instance ID canonicalization; a reusable liveness-checked PID lock; per-wiki
ingest lock; a global crontab-mutation lock; multi-instance managed crontab blocks; the
`install|remove|status|list|run-now` commands; interval sugar (`--every 5m`); loud
skip-logging with escalation; path-escaping; the rename/move workflow; per-instance env-file
capture so cron runs have the API key.

**Explicitly NOT in scope (settled):** modifying `ingest()` / `lib.py` core logic; fs-watch;
persistent daemons; systemd/launchd unit generation; observability/alerting beyond logging +
a state file (see §12 non-goal).

---

## 2. Design decisions (resolved — build to these)

**D1 — Reuse the migrate lock *algorithm*, in a new generic primitive; leave `migrate`
untouched.** `lib.py:_acquire_migration_lock` (lines 1798–1855) already does the exact
liveness check must-fix #1 demands: `open(path,"x")` O_EXCL create, write PID, on
`FileExistsError` read the PID and probe `os.kill(pid, 0)` (ProcessLookupError → stale,
PermissionError → alive/other-user, OSError → stale), reclaim stale, re-attempt once. We
**extract that algorithm into a new `wiki_weaver/pidlock.py`** rather than call migrate's
private function, and we **do not refactor `migrate`** (it works; touching it is out-of-scope
risk). A future cleanup could have migrate adopt `pidlock` — parked, not done here.

**D2 — `run-now` runs in-process** (per §11.2 shape #2: "acquires the per-wiki lock, drains
`_inbox/` via the existing `ingest()` core, and exits"). It calls the `ingest()` **lib
function** directly — not a subprocess, not `cmd_ingest`. This avoids a second engine import,
a double preflight, and any self-deadlock. Output capture is handled by a stdout tee into a
size-rotated log (§10).

**D3 — The ingest lock is a *triggering-layer* concern, applied at BOTH trigger sites.**
`ingest()` core stays lock-free (settled). The lock is acquired by `schedule run-now` **and**
by the manual-CLI wrapper `cmd_ingest` — both are the triggering layer, both derive the same
instance-scoped lock path from the same canonical wiki path, so a manual `wiki-weaver ingest`
and a scheduled tick on the same wiki cannot run concurrently. This closes the real
concurrent-write race (must-fix #3) without editing the settled-unchanged core. Wiring
`cmd_ingest` is a wrapper change only (`cli.py`), never `lib.ingest()`.

**D4 — Multi-instance managed crontab blocks, keyed by instance ID.** medium-tools uses ONE
global marker block; we cannot. Markers embed the instance ID
(`# >>> wiki-weaver:<id> >>>` / `# <<< wiki-weaver:<id> <<<`) so N wikis coexist in one
user crontab, each block independently upsert/removable without disturbing the others.

**D5 — Two distinct locks.** The per-wiki **ingest-execution** lock (short-lived per run,
lives in the instance data dir) is separate from the global **crontab-mutation** lock (guards
read-modify-write of the shared user crontab during install/remove). Both use the `pidlock`
primitive; different paths, different lifetimes (must-fix #5).

**D6 — Escalation is state-tracked, not exit-code-signalled.** A skip is not a failure — it
exits 0 so cron `MAILTO` doesn't spam on every normal collision. Consecutive skips are counted
in the instance run-state; crossing the `alert_after` threshold flips `alert_active` and emits
an ERROR-level log line. `status`/`list` surface the alert. This is the single mechanism for
both must-fix #2 (loud skip logging) and must-fix #6 (interval-shorter-than-runtime
starvation) — a starving schedule simply escalates.

**D7 — Env-file capture so cron actually works.** cron runs with a minimal environment; the
API key won't be present. At install, if `ANTHROPIC_API_KEY` is set it is snapshotted to a
per-instance `env` file (mode 0600) and the cron command sources it. Documented as a snapshot
(rotate key → re-run `install`). Mirrors medium-tools' `use_env_file` path.

---

## 3. New & modified files (exact paths)

```
NEW  wiki_weaver/pidlock.py        generic liveness-checked PID lock primitive (from migrate algo)
NEW  wiki_weaver/instances.py      instance ID canonicalization + storage layout + config/state IO
NEW  wiki_weaver/crontab.py        pure multi-instance managed-block text ops + crontab subprocess wrappers
NEW  wiki_weaver/schedule.py       command implementations: install/remove/status/list/run-now + interval sugar + log rotation

EDIT wiki_weaver/cli.py            add `schedule` nested subparser + cmd_schedule dispatch;
                                   wire cmd_ingest to acquire the ingest lock (D3)

NEW  eval/test_pidlock.py          liveness/staleness/reclaim/contention unit tests
NEW  eval/test_instances.py        canonicalization + aliasing-safety + storage layout + atomic state
NEW  eval/test_crontab.py          multi-instance block upsert/remove/scan + escaping (pure text)
NEW  eval/test_schedule.py         interval sugar, install/remove/status/list, skip-logging + escalation, run-now
```

No new third-party dependencies. Everything is stdlib (`os`, `subprocess`, `shlex`,
`hashlib`, `re`, `json`, `tempfile`, `contextlib`, `logging`) plus existing `wiki_weaver.lib`.

Canonical test command (from `pyproject.toml`): `uv run pytest eval/ -q`. Tests live in
`eval/`, insert repo root on `sys.path` (see `eval/test_migrate.py` header for the pattern).

---

## 4. Module: `wiki_weaver/pidlock.py`

Generic, reusable, liveness-checked PID lock. Pure stdlib. No wiki-weaver imports.

```python
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
    holder_pid: int | None = None    # live holder's PID when acquired is False
    reclaimed_stale: bool = False    # a stale lock was detected & removed during acquire


def pid_alive(pid: int) -> bool:
    """True if a process with *pid* is alive (probe via os.kill(pid, 0)).

    ProcessLookupError -> False (gone). PermissionError -> True (exists, other user).
    Any other OSError -> False (treat as not-provably-alive / stale).
    """


def try_acquire(lock_path: Path) -> LockResult:
    """Atomically attempt to acquire *lock_path*; never blocks, never raises on contention.

    Algorithm (identical in spirit to lib._acquire_migration_lock):
      1. Try open(lock_path, "x") (O_EXCL); on success write str(os.getpid()), fsync-not-
         required, return LockResult(acquired=True).
      2. On FileExistsError, read the recorded PID:
         - unreadable/invalid int  -> stale: unlink, retry create once.
         - pid_alive(pid) is True   -> return LockResult(False, holder_pid=pid).
         - pid_alive(pid) is False  -> stale: unlink, retry create once
                                       (reclaimed_stale=True).
      3. If the single retry also loses the race -> return LockResult(False, holder_pid=<pid
         re-read or None>).  Losing the reclaim race means someone else won; do not force.
    Creates lock_path.parent (mkdir parents=True, exist_ok=True) before the first create.
    """


def release(lock_path: Path) -> None:
    """Remove *lock_path* iff it records THIS process's PID (never steal another's lock).
    Missing file -> no-op. Unreadable/foreign PID -> leave it (loud is the caller's job).
    """


@contextmanager
def pid_lock(lock_path: Path) -> Iterator[LockResult]:
    """Context manager: yields the LockResult; on exit, release() iff we acquired.
    Callers inspect result.acquired to branch (run vs skip). Does NOT raise on contention —
    the skip path is a normal outcome, not an error.
    """
```

**Contract notes for the builder:**
- Write PID as a bare decimal string, UTF-8, no newline required (match migrate: `f.write(str(os.getpid()))`).
- `release()` must read-and-compare the PID before unlinking so a crashed-and-reclaimed lock now held by another process is never deleted out from under it.
- No global state, no threading locks — this guards *cross-process* concurrency (cron ticks are separate processes), which is exactly what O_EXCL + PID-liveness handles and what `threading.Lock` does not.

---

## 5. Module: `wiki_weaver/instances.py`

Instance ID canonicalization (must-fix #3), the storage layout, and config/state IO. This is
the **most safety-critical module** — get canonicalization wrong and two aliases of one wiki
get two locks and the parallelism=1 race returns.

```python
from __future__ import annotations
import hashlib, json, os, re, tempfile, contextlib
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "canonical_wiki_path", "instance_id",
    "data_root", "config_root",
    "instance_data_dir", "instance_config_dir",
    "ingest_lock_path", "crontab_lock_path",
    "logs_dir", "ingest_log_path", "env_file_path",
    "InstanceConfig", "RunState",
    "write_instance_config", "read_instance_config",
    "read_run_state", "write_run_state",
    "list_instance_ids", "delete_instance",
]
```

### 5.1 Canonicalization (must-fix #3 — the critical one)

```python
def canonical_wiki_path(wiki: str | Path) -> Path:
    """Fully canonical, alias-free wiki path.

    expanduser() -> resolve() (resolves symlinks, normalizes '..', absolutizes, strips
    trailing slash). Matches the existing convention in lib.migrate / lib.ingest
    (`Path(x).expanduser().resolve()`). All of {./my-wiki, my-wiki/, /abs/my-wiki,
    /symlink/to/my-wiki} collapse to ONE path here.
    """
    return Path(wiki).expanduser().resolve()


def instance_id(wiki: str | Path) -> str:
    """Deterministic, filesystem-safe, collision-free instance id from the canonical path.

    id = "{slug}-{digest}" where:
      canon  = canonical_wiki_path(wiki)
      slug   = re.sub(r'[^a-z0-9]+','-', canon.name.lower()).strip('-')  or 'wiki'
      digest = sha256(str(canon).encode('utf-8')).hexdigest()[:12]
    slug is human-readable (the wiki dir's basename); digest guarantees uniqueness and
    absorbs any two distinct canonical paths that share a basename. Windows-safe by
    construction (slug is [a-z0-9-] only; see IMPLEMENTATION_PHILOSOPHY path-sanitization).
    """
```

> **Builder MUST verify:** `instance_id("./w")`, `instance_id("w/")`, `instance_id(abspath)`
> and `instance_id(symlink_to_w)` all return the **same** id when they resolve to the same
> real directory, and **different** ids for genuinely different directories that happen to
> share a basename. This is the aliasing-safety test (§13).

### 5.2 Storage layout & the single env override

Honor the design's "single `WIKI_WEAVER_DATA_DIR` override" while keeping the XDG split when
it is unset:

```python
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
```

- When `WIKI_WEAVER_DATA_DIR` is set, **both** config and data live under it
  (`$DIR/config`, `$DIR/data`) — one env var relocates everything (tests point it at a tmp
  dir; this is the sole knob the test-suite needs).
- When unset: config under `~/.config/wiki-weaver/`, state/PID/logs under
  `~/.local/share/wiki-weaver/` — the split §11.2 shape #1 specifies.

Per-instance paths (all create parents on demand, `exist_ok=True`):

```
config_root()/instances/{id}/instance.json    # install-time config (stable)
config_root()/instances/{id}/env              # 0600 API-key snapshot (only if captured)
data_root()/instances/{id}/ingest.lock        # per-wiki ingest PID lock (D5)
data_root()/instances/{id}/run-state.json     # runtime tracking (skips, last run)
data_root()/instances/{id}/logs/ingest.log    # rotated run log (+ .1 .. .N)
data_root()/crontab.lock                       # GLOBAL crontab-mutation lock (D5)
```

```python
def instance_data_dir(id: str)   -> Path      # data_root()/instances/{id}   (mkdir)
def instance_config_dir(id: str) -> Path      # config_root()/instances/{id} (mkdir)
def ingest_lock_path(wiki) -> Path            # instance_data_dir(instance_id(wiki))/ingest.lock
def crontab_lock_path() -> Path               # data_root()/crontab.lock
def logs_dir(id: str) -> Path                 # instance_data_dir(id)/logs (mkdir)
def ingest_log_path(id: str) -> Path          # logs_dir(id)/ingest.log
def env_file_path(id: str) -> Path            # instance_config_dir(id)/env
```

### 5.3 Config & state dataclasses + atomic IO

```python
@dataclass
class InstanceConfig:
    instance_id: str
    canonical_wiki: str            # str(canonical_wiki_path(...)) — the stored, display path
    cron_expr: str                 # the 5-field cron expression actually installed
    every: str | None              # friendly interval as given ("5m"), or None if --cron used
    alert_after: int               # consecutive-skip threshold for escalation (default 3)
    uses_env_file: bool            # True if an env snapshot was written at install
    created_at: str                # ISO-8601 UTC

@dataclass
class RunState:
    consecutive_skips: int = 0
    alert_active: bool = False
    last_run_at: str | None = None
    last_exit: int | None = None
    last_duration_s: float | None = None
    last_skip_at: str | None = None
    last_holder_pid: int | None = None   # PID that held the lock on the most recent skip

def write_instance_config(cfg: InstanceConfig) -> None       # atomic JSON, ensure_ascii=False
def read_instance_config(id: str) -> InstanceConfig | None
def read_run_state(id: str) -> RunState                       # missing file -> fresh RunState()
def write_run_state(id: str, state: RunState) -> None         # atomic
def list_instance_ids() -> list[str]                          # dirnames under config_root()/instances
def delete_instance(id: str) -> bool                          # rm -rf both config & data instance dirs
```

**Atomic write** — reuse the skill's `tempfile.mkstemp` + `os.replace` pattern with
`BaseException` cleanup and `encoding="utf-8"`, `ensure_ascii=False`, `default=str`
(see instance-storage-patterns §4). Do not hand-roll a different variant.

---

## 6. Module: `wiki_weaver/crontab.py`

Two layers: **pure text ops** (fully unit-testable, no subprocess) and thin **subprocess
wrappers** around the real `crontab` binary. Adapted from `medium-tools/schedule.py`, changed
from single-global-block to **per-instance blocks**.

```python
from __future__ import annotations
import re, shlex, shutil, subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "marker_begin", "marker_end", "ManagedBlock", "scan",
    "upsert_block", "remove_block",
    "crontab_available", "read_current_crontab", "write_crontab",
    "resolve_binary", "build_cron_line",
]

_MARKER_BEGIN = "# >>> wiki-weaver:{id} >>>"
_MARKER_END   = "# <<< wiki-weaver:{id} <<<"

def marker_begin(instance_id: str) -> str: ...    # _MARKER_BEGIN.format(id=instance_id)
def marker_end(instance_id: str) -> str:   ...
```

### 6.1 Pure text ops (multi-instance)

```python
@dataclass
class ManagedBlock:
    instance_id: str
    cron_line: str        # the single cron entry line inside the block (schedule + command)
    block_text: str       # full block incl. both markers

def scan(text: str) -> dict[str, ManagedBlock]:
    """Return every well-formed wiki-weaver managed block, keyed by instance_id.

    Discovers instance ids by regex over begin markers:
        r'^# >>> wiki-weaver:(?P<id>[^\s>]+) >>>$'  (MULTILINE)
    For each discovered id, locate its matching begin/end pair. A block is well-formed iff
    exactly one begin and one matching end exist, begin precedes end. Malformed blocks
    (orphan/duplicate/misordered markers for a given id) are SKIPPED and their ids returned
    via the companion function below rather than silently corrupting siblings.
    """

def scan_malformed(text: str) -> dict[str, str]:
    """id -> human reason, for any id whose markers are orphaned/duplicated/misordered.
    Callers refuse to mutate a malformed id's block and tell the user to inspect `crontab -e`.
    (Same four malformed conditions medium-tools.parse_crontab detects, scoped per-id.)
    """

def upsert_block(text: str, instance_id: str, cron_line: str) -> str:
    """Insert or replace ONLY this instance's block; leave all other text (incl. other
    wiki-weaver instances and unrelated cron entries) byte-for-byte intact.
    Rendered block:  "{begin}\n{cron_line}\n{end}\n". If absent, append (ensuring a single
    newline separator). Raise ValueError if this id's existing block is malformed.
    """

def remove_block(text: str, instance_id: str) -> str:
    """Remove ONLY this instance's block. Idempotent (no block -> unchanged). Raise
    ValueError if this id's block is malformed (tell the user to repair manually)."""
```

### 6.2 Subprocess wrappers & command construction

```python
def crontab_available() -> bool:                 # shutil.which("crontab") is not None
def read_current_crontab() -> str:               # `crontab -l`; rc!=0 -> "" (no crontab yet)
def write_crontab(text: str) -> None:            # `crontab -` via stdin; RuntimeError on rc!=0
def resolve_binary() -> str:                     # shutil.which("wiki-weaver"); FileNotFoundError if absent

def build_cron_line(*, cron_expr: str, binary_path: str,
                    canonical_wiki: Path, env_file: Path | None) -> str:
    """Build the full cron entry line.

    Base command (path-escaped — must-fix #7):
        {binary_path} schedule run-now --wiki {shlex.quote(str(canonical_wiki))}
    If env_file is not None (D7), wrap so cron sources the key first:
        bash -lc {shlex.quote(f"set -a; source {shlex.quote(str(env_file))}; {base}")}
    Return: f"{cron_expr} {command}".
    """
```

> **must-fix #7 (path escaping):** every interpolation of a user-supplied path into the cron
> line goes through `shlex.quote`. `canonical_wiki` and `env_file` are both quoted; the
> `bash -lc` inner script is quoted as a whole. A wiki path with spaces, `$`, `;`, or quotes
> must round-trip through `run-now --wiki` unharmed — this is a required test (§13).

---

## 7. Module: `wiki_weaver/schedule.py`

The command implementations. Ties crontab + instances + pidlock + `lib.ingest` together.
Each public function returns an `int` exit code (matches the `cmd_*` convention in `cli.py`).

```python
from __future__ import annotations
import re, shlex, sys, os, contextlib
from datetime import datetime, timezone
from pathlib import Path

from wiki_weaver import crontab as _ct
from wiki_weaver import instances as _inst
from wiki_weaver import pidlock as _lock
from wiki_weaver.lib import ingest as _ingest, _fail, _warn, _ok   # existing loud-logging helpers

__all__ = ["install", "remove", "status", "list_all", "run_now",
           "interval_to_cron", "EXIT_SKIP"]

EXIT_SKIP = 75   # EX_TEMPFAIL — used by cmd_ingest's manual-guard skip (D3); run_now exits 0 on skip
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 5
_DEFAULT_ALERT_AFTER = 3
```

### 7.1 Interval sugar → cron (must-fix: friendly `--every`)

```python
def interval_to_cron(spec: str) -> str:
    """Translate friendly interval sugar to a 5-field cron expression.

    Grammar:  ^(\\d+)(m|h|d)$
      "Nm" -> f"*/{N} * * * *"    for 1 <= N <= 59   (N not dividing 60 is allowed but
                                                       non-uniform at the hour boundary —
                                                       emit a _warn, do not reject)
      "Nh" -> f"0 */{N} * * *"    for 1 <= N <= 23
      "Nd" -> f"0 0 */{N} * *"    for 1 <= N <= 31
    Raise ValueError with a clear message on bad unit / out-of-range / N==0. For sub-minute or
    irregular schedules the user is directed to --cron.
    """
```

`--cron` escape hatch validation: split on whitespace, require exactly 5 fields; otherwise
`ValueError("cron expression must have 5 fields; got N")`. Deeper validity is left to the
`crontab` binary at `write_crontab` time (fail-loud there).

### 7.2 `install`

```python
def install(wiki: str, *, every: str | None, cron: str | None,
            alert_after: int = _DEFAULT_ALERT_AFTER) -> int:
```

1. `if not _ct.crontab_available(): _fail("crontab binary not found on PATH"); return 1`.
2. Exactly one of `every` / `cron` must be provided → else `_fail(...); return 2`.
3. `canon = _inst.canonical_wiki_path(wiki)`; `if not canon.is_dir(): _fail("wiki dir not
   found: … (run `wiki-weaver init` first)"); return 1`.
4. `iid = _inst.instance_id(canon)`.
5. `cron_expr = interval_to_cron(every) if every else cron` (catch `ValueError` → `_fail`; return 2).
6. `binary = _ct.resolve_binary()` (catch `FileNotFoundError` → `_fail`; return 1).
7. **Env-file capture (D7):** `key = os.environ.get("ANTHROPIC_API_KEY")`. If set, write
   `env_file_path(iid)` (mode 0600, atomic, `ANTHROPIC_API_KEY=<shlex.quote(key)>`),
   `uses_env_file=True`; else `_warn("ANTHROPIC_API_KEY not set — scheduled ingest will fail
   at cron runtime until a key is available")`, `uses_env_file=False`, `env_file=None`.
8. `cron_line = _ct.build_cron_line(cron_expr=cron_expr, binary_path=binary,
   canonical_wiki=canon, env_file=<env_file or None>)`.
9. **Under the GLOBAL crontab-mutation lock (D5, must-fix #5):**
   ```python
   with _lock.pid_lock(_inst.crontab_lock_path()) as got:
       if not got.acquired:
           _fail(f"another wiki-weaver crontab mutation is in progress (PID {got.holder_pid}); retry")
           return 1
       current = _ct.read_current_crontab()
       if iid in _ct.scan_malformed(current):
           _fail("this instance's crontab block is malformed; inspect `crontab -e`"); return 1
       _ct.write_crontab(_ct.upsert_block(current, iid, cron_line))
   ```
10. Persist `InstanceConfig` (write_instance_config). Pre-create `logs_dir(iid)` so the first
    tick never silently fails on a missing dir.
11. `_ok(f"scheduled: {canon}  [{cron_expr}]  instance {iid}")`; print the log path and the
    `remove` hint. `return 0`.

### 7.3 `remove`

```python
def remove(wiki: str | None, *, instance_id: str | None = None, purge: bool = False) -> int:
```

- Resolve the target id: if `instance_id` given, use it verbatim (this is the moved/missing-
  wiki escape hatch, must-fix #9); else `iid = _inst.instance_id(wiki)`. `remove` MUST work
  even if the wiki dir no longer exists — so when resolving from `wiki`, canonicalize
  **best-effort**: try `canonical_wiki_path`; on failure fall back to
  `Path(wiki).expanduser().absolute()` (lexical) and compute the id from that. Because a
  moved dir may not reproduce the original realpath, `--id` (surfaced by `list`) is the
  reliable path and is documented as such.
- Under the global crontab-mutation lock: read crontab; if `iid` malformed → `_fail`,
  return 1; `write_crontab(remove_block(current, iid))`.
- If no block existed: `print("no schedule installed for this wiki; nothing to remove")` —
  still proceed to optional purge, still `return 0` (idempotent).
- `if purge: _inst.delete_instance(iid)` (removes config+data+logs+env). Default (no purge)
  leaves the instance dir so logs/state survive for inspection.
- `_ok("schedule removed")`; `return 0`.

### 7.4 `status`

```python
def status(wiki: str | None, *, instance_id: str | None = None) -> int:
```

Resolve id (same rule as `remove`). Gather and print, as a stable text block (code-fenced in
CLI output is not required, but keep it aligned/scannable):
- installed? (is `iid` in `scan(read_current_crontab())`) + the cron line if so.
- `InstanceConfig` fields (canonical_wiki, cron_expr/every, alert_after, uses_env_file, created_at).
- `RunState` (last_run_at, last_exit, last_duration_s, consecutive_skips, alert_active, last_skip_at, last_holder_pid).
- **Live lock state:** read `ingest_lock_path(iid)`; if present and `pidlock.pid_alive(pid)` →
  "ingest RUNNING (PID …)", else "idle" (or "stale lock present" if file exists but pid dead).
- If `alert_active`: print a loud `⚠ ALERT: N consecutive skips — ingest may be stuck or the
  interval is shorter than a run takes; consider a longer --every` (must-fix #6 surfaced here).
Return 0 (2 if the instance is unknown/uninstalled — nothing to report).

### 7.5 `list_all`

```python
def list_all() -> int:
```

Union of crontab-installed ids (`scan`) and on-disk instance ids (`list_instance_ids`) so both
"scheduled" and "orphaned state" show up. Print one row per id: `id`, canonical_wiki (from
config or the cron line), cron_expr, last_run/last_exit, consecutive_skips, alert flag. This is
where the user reads the `id` needed for `remove --id` after a move (must-fix #9). Return 0.

### 7.6 `run_now` — the tick body (in-process, D2)

```python
def run_now(wiki: str) -> int:
```

```
canon = canonical_wiki_path(wiki)
if not canon.is_dir():
    _fail(f"wiki dir not found: {canon} — was it moved? re-run `schedule install`")
    return 1
iid  = instance_id(canon)
cfg  = read_instance_config(iid)                 # may be None (bare run-now); default alert_after
alert_after = cfg.alert_after if cfg else _DEFAULT_ALERT_AFTER
log  = ingest_log_path(iid)
rotate_log_if_needed(log, _LOG_MAX_BYTES, _LOG_BACKUPS)   # §10 — size rotate ONCE at start

with open(log, "a", encoding="utf-8") as fh:
    _mark(fh, f"=== tick {utcnow_iso()}  wiki={canon}  id={iid} ===")
    lock = ingest_lock_path(iid)
    res  = pidlock.try_acquire(lock)

    # ---- SKIP PATH (must-fix #2, #6) ----
    if not res.acquired:
        st = read_run_state(iid)
        st.consecutive_skips += 1
        st.last_skip_at = utcnow_iso()
        st.last_holder_pid = res.holder_pid
        if st.consecutive_skips >= alert_after:
            st.alert_active = True
            msg = (f"ALERT: skipped {st.consecutive_skips} consecutive cycles — previous "
                   f"ingest (PID {res.holder_pid}) still running for {canon}. The interval "
                   f"may be shorter than a run takes; investigate or lengthen --every.")
            _mark(fh, "ERROR " + msg); _fail(msg)                 # loud to BOTH log and stderr
        else:
            msg = (f"SKIP [{st.consecutive_skips}/{alert_after}]: previous ingest "
                   f"(PID {res.holder_pid}) still running for {canon}; skipping this cycle.")
            _mark(fh, "WARN " + msg); _warn(msg)
        write_run_state(iid, st)
        return 0            # a skip is NOT a failure — don't trigger cron MAILTO spam

    # ---- RUN PATH ----
    try:
        st = read_run_state(iid)
        st.consecutive_skips = 0
        st.alert_active = False
        write_run_state(iid, st)
        started = time.monotonic()
        with _tee_stdout_stderr(fh):          # §10 — capture ingest() print/_ok/_warn/_fail
            rc = _ingest(canon)               # existing core, UNCHANGED (drain _inbox/)
        st.last_run_at = utcnow_iso()
        st.last_exit = rc
        st.last_duration_s = round(time.monotonic() - started, 3)
        write_run_state(iid, st)
        _mark(fh, f"--- tick done exit={rc} dur={st.last_duration_s}s ---")
        return rc
    finally:
        pidlock.release(lock)
```

`_mark(fh, line)` writes `f"{line}\n"` and flushes. `utcnow_iso()` = `datetime.now(timezone.utc).isoformat()`.

---

## 8. `cli.py` modifications (exact)

### 8.1 Add the `schedule` nested subparser (in `main()`, alongside the others)

```python
p_sched = sub.add_parser("schedule", help="manage unattended, cron-scheduled ingestion")
sched_sub = p_sched.add_subparsers(dest="schedule_command")

s_install = sched_sub.add_parser("install", help="install a cron entry for a wiki")
s_install.add_argument("--wiki", required=True, help="wiki directory to schedule")
g = s_install.add_mutually_exclusive_group(required=True)
g.add_argument("--every", metavar="INTERVAL",
               help="friendly interval sugar, e.g. 5m, 30m, 1h, 1d")
g.add_argument("--cron", metavar="EXPR",
               help="raw 5-field cron expression (power-user escape hatch)")
s_install.add_argument("--alert-after", type=int, default=3, dest="alert_after",
                       help="escalate after N consecutive skipped cycles (default: 3)")

s_remove = sched_sub.add_parser("remove", help="remove a wiki's cron entry")
s_remove.add_argument("--wiki", help="wiki directory (canonicalized to find the instance)")
s_remove.add_argument("--id", dest="instance_id", help="instance id (use after moving a wiki)")
s_remove.add_argument("--purge", action="store_true",
                      help="also delete the instance's stored config/state/logs")

s_status = sched_sub.add_parser("status", help="show a wiki's schedule + last-run state")
s_status.add_argument("--wiki", help="wiki directory")
s_status.add_argument("--id", dest="instance_id", help="instance id")

sched_sub.add_parser("list", help="list all scheduled wikis and their state")

s_run = sched_sub.add_parser("run-now", help="run one ingest tick now (what cron invokes)")
s_run.add_argument("--wiki", required=True, help="wiki directory")
```

### 8.2 Dispatch

```python
def cmd_schedule(args: argparse.Namespace) -> int:
    from wiki_weaver import schedule as sched
    cmd = args.schedule_command
    if cmd == "install":
        return sched.install(args.wiki, every=args.every, cron=args.cron,
                             alert_after=args.alert_after)
    if cmd == "remove":
        if not args.wiki and not args.instance_id:
            _fail("schedule remove: pass --wiki or --id"); return 2
        return sched.remove(args.wiki, instance_id=args.instance_id, purge=args.purge)
    if cmd == "status":
        if not args.wiki and not args.instance_id:
            _fail("schedule status: pass --wiki or --id"); return 2
        return sched.status(args.wiki, instance_id=args.instance_id)
    if cmd == "list":
        return sched.list_all()
    if cmd == "run-now":
        return sched.run_now(args.wiki)
    # no subcommand -> print schedule help
    _fail("schedule: specify install|remove|status|list|run-now"); return 2
```

Add `"schedule": cmd_schedule` to the `dispatch` dict. No new top-level `--version`/gate
interaction — `run-now` performs the same `_gate(require_api_key=True)` as `cmd_ingest`
does **before** attempting a drain (so a broken env fails fast in the log rather than minutes
into the engine). Put the gate at the top of `sched.run_now` (import `preflight`/reuse the
`_gate` helper — simplest is to call the `run_now` body only after a preflight check; the
builder may factor `_gate` into a small shared import). Skips do NOT need the gate.

### 8.3 Wire `cmd_ingest` to the ingest lock (D3, must-fix #3)

`cmd_ingest` currently calls `ingest(...)` directly. Wrap that call so a manual ingest and a
scheduled tick on the same wiki are mutually exclusive — **without touching `lib.ingest()`**:

```python
def cmd_ingest(args: argparse.Namespace) -> int:
    if rc := _gate(require_api_key=True):
        return rc
    from wiki_weaver import instances as _inst
    from wiki_weaver import pidlock as _lock
    from wiki_weaver.schedule import EXIT_SKIP
    lock = _inst.ingest_lock_path(args.wiki)
    res = _lock.try_acquire(lock)
    if not res.acquired:
        _fail(f"another ingest is already running for this wiki (PID {res.holder_pid}); "
              f"skipping to avoid a concurrent-write race.")
        return EXIT_SKIP
    try:
        return ingest(args.wiki, source=args.source,
                      max_cycles=args.max_cycles, keep_going=args.keep_going)
    finally:
        _lock.release(lock)
```

This is the only behavior change to an existing command, and it is deliberate: it is exactly
the concurrent-write race the whole feature exists to prevent, and it only ever triggers when
a run is genuinely already in flight.

---

## 9. Instance storage layout (summary)

```
$WIKI_WEAVER_DATA_DIR set  ->  everything under $DIR/{config,data}/...
unset                      ->  config under ~/.config/wiki-weaver, data under ~/.local/share/wiki-weaver

<config>/instances/{id}/instance.json     InstanceConfig (stable, install-time)
<config>/instances/{id}/env               0600 API-key snapshot (only if captured)
<data>/instances/{id}/ingest.lock         per-wiki ingest PID lock (short-lived per tick)
<data>/instances/{id}/run-state.json      RunState (skips, last run) — atomic writes
<data>/instances/{id}/logs/ingest.log     rotated tick log (+ .1 .. .5)
<data>/crontab.lock                        GLOBAL crontab-mutation lock
```

`id = "{basename-slug}-{sha256(canonical_path)[:12]}"`.

---

## 10. Logging & rotation (must-fix #2 mechanics)

- One log file per instance: `logs/ingest.log`. Every tick appends a `=== tick … ===` header,
  then either a SKIP/ALERT line or the full teed `ingest()` output + a `--- tick done … ---`
  trailer.
- **Size rotation, once per tick, at start** (`rotate_log_if_needed`): if
  `log.stat().st_size >= max_bytes`, shift `ingest.log.{N-1}->{N}` … `ingest.log->ingest.log.1`
  (drop the oldest beyond `backups`). Rotating only at tick boundaries (never mid-write) keeps
  it simple and correct — a tick is minutes-scale, so per-tick granularity is ample. Do NOT use
  `logging.handlers.RotatingFileHandler` here (it rotates mid-stream and fights the stdout tee);
  a plain size-check-and-rename is the right tool.
- **stdout/stderr capture:** `_tee_stdout_stderr(fh)` is a context manager that swaps
  `sys.stdout`/`sys.stderr` for a small `Tee` writing to BOTH the log file handle and the
  original stream (so a human running `run-now` by hand still sees output, and cron's own
  capture still works). `ingest()` writes via `print`/`_ok`/`_warn`/`_fail` to stdout/stderr —
  the tee captures all of it. Restore the originals in `finally`.
- All file opens use `encoding="utf-8"` (Windows-safety per IMPLEMENTATION_PHILOSOPHY).

---

## 11. Rename / move a scheduled wiki (must-fix #9 — explicit answer)

Because the instance id derives from the canonical path, moving the directory changes the id;
the old cron block and old instance dir are now orphaned but harmless (no persistent process
holds anything — cron-only). The supported workflow:

```
1. wiki-weaver schedule list                       # note the OLD instance id + path
2. wiki-weaver schedule remove --id <OLD_ID> --purge   # cleans crontab block + old state
   #  (or: schedule remove --wiki <OLD_PATH> if the old path still resolves)
3. mv <OLD_PATH> <NEW_PATH>                         # or it's already moved
4. wiki-weaver schedule install --wiki <NEW_PATH> --every 5m
```

`remove --id` is the reliable path when the old directory is already gone (canonicalizing a
missing path can't reproduce a symlinked realpath). `list` exists precisely to surface that id.
This is documented in `schedule remove --help` and in AGENTS.md's schedule section (builder to
add a short paragraph mirroring the `migrate` entry).

A stale orphan (old block whose `run-now` now fails "wiki dir not found") is self-announcing:
`run-now` returns 1 and logs the not-found error loudly; `status`/`list` show the last exit.
No silent rot.

---

## 12. Non-goal (must-fix #8 — named and parked)

**Observability & alerting beyond local logging + the run-state file is a stated non-goal of
this phase.** What ships: loud per-tick logs, a per-instance `run-state.json`, escalation via
`alert_active`, and `status`/`list` to read them. What is **deliberately deferred** to a named
future **"Phase 2b — scheduled-ingest observability"**: metrics export, external alert delivery
(email/Slack/webhook on escalation), a health endpoint, cross-instance dashboards, and log
aggregation. Cron's own `MAILTO` remains available to users as a zero-infra escalation channel
if they want run failures mailed; we neither require nor manage it. Parked with a reason, per
the design doc's own "don't kill a good idea, park it" rule.

---

## 13. Must-fix coverage matrix

| # | §11.2 must-fix | Satisfied by |
|---|----------------|--------------|
| 1 | PID lock checks **liveness** (`kill(pid,0)`), not mere file existence | `pidlock.pid_alive` + `try_acquire` (§4); ports the migrate algorithm |
| 2 | Every skip logged loudly + escalating alert after N | `run_now` skip path + `RunState.consecutive_skips`/`alert_active` (§7.6, §10) |
| 3 | Instance id = **canonicalized** path (no alias double-lock race) | `instances.canonical_wiki_path`/`instance_id` (§5.1); lock keyed by id at BOTH trigger sites (D3) |
| 4 | Full lifecycle CLI: `remove`/`status`/`list`, not just `install` | §7.2–7.5, §8 |
| 5 | Crontab mutation has its **own** lock, separate from ingest lock | global `crontab.lock` around install/remove (D5, §7.2/7.3); per-wiki `ingest.lock` is distinct |
| 6 | Cron+lock starvation (ingest > interval) surfaced, not silent | same escalation as #2; `status` prints the "interval too short" hint (§7.4, §7.6) |
| 7 | Path-escaping for wiki paths in the cron command line | `shlex.quote` in `crontab.build_cron_line` (§6.2) |
| 8 | Observability beyond logging named as non-goal | §12 |
| 9 | Rename/move-while-scheduled workflow | `remove --id` + `list` + documented workflow (§11) |

---

## 14. Test files & required assertions

All tests are pure/offline — **no engine, no LLM, no network**. They monkeypatch
`WIKI_WEAVER_DATA_DIR` to a tmp dir and (for crontab) test the pure text layer directly plus
monkeypatch the subprocess wrappers. `ingest` is monkeypatched to a stub in `run_now` tests so
no engine runs.

### `eval/test_pidlock.py`
- **Acquire/release round-trip:** fresh path → `acquired=True`; second `try_acquire` from a
  simulated other PID → `acquired=False, holder_pid` set; `release` frees it.
- **Liveness — stale reclaim:** write a lock file containing a **dead** PID (e.g. a pid known
  gone, or monkeypatch `pid_alive`→False) → `try_acquire` reclaims (`acquired=True,
  reclaimed_stale=True`). **This is the #1 flagged gap — assert file existence alone does NOT
  block.**
- **Liveness — live holder blocks:** lock file with a PID `pid_alive`→True → `try_acquire`
  returns `acquired=False, holder_pid=<that pid>`.
- **Invalid PID content** (`"garbage"`, empty) → treated stale → reclaimed.
- **`release` never steals:** lock owned by a different (live) PID → `release` is a no-op
  (file remains).
- **`pid_lock` context manager** releases on normal exit AND on exception; does not release
  when it did not acquire.

### `eval/test_instances.py`
- **Aliasing safety (must-fix #3, critical):** create a real dir `w`; assert
  `instance_id("w") == instance_id("w/") == instance_id(<abs>) == instance_id(<symlink→w>)`.
  Assert two different dirs sharing basename (`a/w` and `b/w`) get **different** ids.
- **Canonical path** resolves symlinks and strips trailing slash.
- **Storage layout honors `WIKI_WEAVER_DATA_DIR`:** with it set, config & data land under
  `$DIR/config` and `$DIR/data`; lock/log/state paths are under the right roots.
- **Atomic config/state IO:** write→read round-trips `InstanceConfig`/`RunState`; a partial
  write leaves no `.tmp` and never a truncated JSON (simulate by asserting `os.replace`
  semantics — write, read back, assert equal; assert no leftover `*.tmp`).
- **`list_instance_ids` / `delete_instance`** enumerate and remove both config+data dirs.

### `eval/test_crontab.py` (pure text — no real crontab)
- **`upsert_block` insert then replace:** inserting instance A then instance B leaves BOTH
  blocks; re-`upsert` A with a new cron line replaces only A's line and leaves B **byte-for-
  byte unchanged**. Unrelated non-managed cron lines are preserved.
- **`remove_block`** removes only the targeted id; idempotent on absent; other blocks intact.
- **`scan`** returns all well-formed blocks keyed by id with their cron lines.
- **Malformed isolation:** an orphan marker for id A is reported by `scan_malformed` and does
  NOT corrupt a well-formed sibling B on `upsert`/`remove` of B.
- **Escaping (must-fix #7):** `build_cron_line` with a wiki path containing a space, `$`, `;`,
  and a quote produces a line where the path is a single `shlex.quote`d token; `shlex.split`
  of the command round-trips the exact canonical path. With `env_file` set, the `bash -lc`
  wrapper is a single quoted script and round-trips.

### `eval/test_schedule.py`
- **`interval_to_cron`:** `5m→"*/5 * * * *"`, `30m→"*/30 * * * *"`, `1h→"0 */1 * * *"`,
  `2h→"0 */2 * * *"`, `1d→"0 0 */1 * *"`; `0m`/`61m`/`5x`/`""` raise `ValueError`; `7m` warns
  but returns `"*/7 * * * *"`.
- **`install`/`remove`/`status`/`list`** with `crontab -l/-` monkeypatched to an in-memory
  string: install writes a block with the escaped `run-now --wiki` line + persists config +
  pre-creates logs dir; status reports installed + config; list shows the row; remove strips
  the block; `remove --purge` deletes the instance dir. Two installs for different wikis
  coexist (multi-instance).
- **Crontab-mutation lock (must-fix #5):** with `crontab.lock` pre-held by a live PID,
  `install` fails loudly (`return 1`) and does NOT write the crontab.
- **`run_now` skip path (must-fix #2/#6):** pre-acquire the wiki's `ingest.lock` with a live
  PID; `run_now` (with `ingest` stubbed) must NOT call ingest, must increment
  `consecutive_skips`, log a WARN skip line to the instance log, and `return 0`. After
  `alert_after` consecutive skips, `alert_active` flips True and an ERROR line is written.
- **`run_now` run path:** no contention → lock acquired, stubbed `ingest` called once,
  `consecutive_skips` reset to 0, `last_run_at`/`last_exit`/`last_duration_s` recorded, lock
  released (file gone) after the call; returns the stub's rc.
- **`run_now` moved-wiki:** nonexistent wiki dir → `return 1`, loud "wiki dir not found" in
  log (must-fix #9 self-announcement).
- **`cmd_ingest` guard (D3):** with the wiki's `ingest.lock` held by a live PID, `cmd_ingest`
  returns `EXIT_SKIP` and does NOT call `ingest` (monkeypatch to assert not-called).

---

## 15. Success criteria (evidence-based)

The feature is done when, on a machine with `crontab` available:

1. `uv run pytest eval/ -q` passes, including every assertion in §14. In particular the
   **liveness** test (stale-PID lock is reclaimed) and the **aliasing** test (4 aliases → 1
   id) pass — these are the two flagged-critical gaps.
2. `python_check` (ruff + pyright) is clean on all four new modules and the `cli.py` edit.
3. **End-to-end, as a user (manual DTU / local proof):**
   - `wiki-weaver init /tmp/w --plain` then `wiki-weaver schedule install --wiki /tmp/w
     --every 5m` → `crontab -l` shows exactly one `# >>> wiki-weaver:w-<hash> >>>` block whose
     command is `… schedule run-now --wiki /tmp/w`.
   - Drop a file in `/tmp/w/_inbox/`, run `wiki-weaver schedule run-now --wiki /tmp/w` → file
     is drained (moved to `_sources/`, ledger appended), a `=== tick … ===` + `--- tick done
     exit=0 ---` pair is in `<data>/instances/<id>/logs/ingest.log`.
   - Hold the lock (start a long `run-now`, or manually create `ingest.lock` with your own live
     PID) and fire a second `run-now` → it logs a `SKIP [1/3]` line and exits 0; the inbox is
     untouched by the second call.
   - Install a second wiki; `crontab -l` shows TWO independent blocks; `wiki-weaver schedule
     list` shows both; `remove --wiki /tmp/w` strips only the first block.
   - `wiki-weaver schedule status --wiki /tmp/w` reports installed/cron/last-run/skip count.
4. `ingest()` / `lib.py` core is **unmodified** (diff shows changes only in the four new
   modules + `cli.py`). The settled "reuse ingest() unchanged" invariant holds.
5. No new third-party dependency added to `pyproject.toml`.

---

## 16. Handoff notes for `modular-builder`

- Build order: `pidlock.py` → `instances.py` → `crontab.py` → `schedule.py` → `cli.py` edits,
  writing each module's tests alongside it (each is an independently testable brick).
- Reuse, do not reinvent: the migrate lock algorithm (`lib.py:1798–1855`) for `pidlock`; the
  medium-tools marker-block ops (`medium-tools/src/medium_tools/schedule.py`) adapted to
  per-instance keys for `crontab`; the instance-storage skill's atomic-write + single-env-var
  patterns for `instances`.
- Loud-logging helpers already exist in `lib.py`: `_fail`, `_warn`, `_ok` — use them, don't add
  a parallel logging convention.
- Do NOT touch `lib.ingest()`, `lib.migrate`, or the `.dot` pipelines. The only edit to
  existing code is `cli.py` (new subparser + dispatch + the `cmd_ingest` lock guard).
- After the code lands, add a short `schedule` section to the repo `AGENTS.md` command list
  (mirroring the existing `migrate` paragraph) and the rename/move workflow (§11).
```
