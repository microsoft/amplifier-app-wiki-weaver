"""Scheduled-ingestion command implementations.

Ties crontab + instances + pidlock + ``lib.ingest`` together. Each public
function returns an ``int`` exit code (matches the ``cmd_*`` convention in
``cli.py``). ``ingest()`` itself stays UNCHANGED (settled design decision) --
this module is only the triggering layer on top of it.
"""

from __future__ import annotations

import contextlib
import os
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from wiki_weaver import crontab as _ct
from wiki_weaver import instances as _inst
from wiki_weaver import pidlock as _lock
from wiki_weaver.lib import _fail, _ok, _warn
from wiki_weaver.lib import ingest as _ingest
from wiki_weaver.lib import preflight as _preflight

__all__ = [
    "install",
    "remove",
    "status",
    "list_all",
    "run_now",
    "interval_to_cron",
    "EXIT_SKIP",
]

EXIT_SKIP = 75  # EX_TEMPFAIL -- used by cmd_ingest's manual-guard skip (D3); run_now exits 0 on skip
_LOG_MAX_BYTES = 10 * 1024 * 1024
_LOG_BACKUPS = 5
_DEFAULT_ALERT_AFTER = 3


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark(fh: IO[str], line: str) -> None:
    fh.write(f"{line}\n")
    fh.flush()


# ---------------------------------------------------------------------------
# 7.1 Interval sugar -> cron
# ---------------------------------------------------------------------------


def interval_to_cron(spec: str) -> str:
    """Translate friendly interval sugar to a 5-field cron expression.

    Grammar: ^(\\d+)(m|h|d)$
      "Nm" -> f"*/{N} * * * *"    for 1 <= N <= 59   (N not dividing 60 is allowed
                                                       but non-uniform at the hour
                                                       boundary -- emit a _warn, do
                                                       not reject)
      "Nh" -> f"0 */{N} * * *"    for 1 <= N <= 23
      "Nd" -> f"0 0 */{N} * *"    for 1 <= N <= 31
    Raise ValueError with a clear message on bad unit / out-of-range / N==0. For
    sub-minute or irregular schedules the user is directed to --cron.
    """
    import re

    m = re.match(r"^(\d+)([mhd])$", spec.strip())
    if not m:
        raise ValueError(
            f"invalid interval {spec!r}; expected e.g. '5m', '30m', '1h', '1d' "
            f"(or use --cron for a raw 5-field expression)"
        )
    n = int(m.group(1))
    unit = m.group(2)

    if unit == "m":
        if not (1 <= n <= 59):
            raise ValueError(f"interval minutes out of range (1-59): {n}")
        if 60 % n != 0:
            _warn(
                f"--every {spec}: {n} does not evenly divide 60; the schedule will "
                f"be non-uniform across the hour boundary."
            )
        return f"*/{n} * * * *"
    if unit == "h":
        if not (1 <= n <= 23):
            raise ValueError(f"interval hours out of range (1-23): {n}")
        return f"0 */{n} * * *"
    # unit == "d"
    if not (1 <= n <= 31):
        raise ValueError(f"interval days out of range (1-31): {n}")
    return f"0 0 */{n} * *"


def _validate_cron_expr(expr: str) -> str:
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression must have 5 fields; got {len(fields)}")
    return expr


# ---------------------------------------------------------------------------
# 7.2 install
# ---------------------------------------------------------------------------


def install(
    wiki: str,
    *,
    every: str | None,
    cron: str | None,
    alert_after: int = _DEFAULT_ALERT_AFTER,
) -> int:
    if not _ct.crontab_available():
        _fail("crontab binary not found on PATH")
        return 1

    if (every is None) == (cron is None):
        _fail("schedule install: pass exactly one of --every or --cron")
        return 2

    canon = _inst.canonical_wiki_path(wiki)
    if not canon.is_dir():
        _fail(f"wiki dir not found: {canon} (run `wiki-weaver init` first)")
        return 1

    iid = _inst.instance_id(canon)

    try:
        cron_expr = interval_to_cron(every) if every else _validate_cron_expr(cron)  # type: ignore[arg-type]
    except ValueError as exc:
        _fail(str(exc))
        return 2

    try:
        binary = _ct.resolve_binary()
    except FileNotFoundError as exc:
        _fail(str(exc))
        return 1

    # Env-file capture (D7).
    key = os.environ.get("ANTHROPIC_API_KEY")
    env_file: Path | None = None
    uses_env_file = False
    if key:
        env_file = _inst.env_file_path(iid)
        _write_env_file(env_file, key)
        uses_env_file = True
    else:
        _warn(
            "ANTHROPIC_API_KEY not set -- scheduled ingest will fail at cron "
            "runtime until a key is available"
        )

    cron_line = _ct.build_cron_line(
        cron_expr=cron_expr,
        binary_path=binary,
        canonical_wiki=canon,
        env_file=env_file,
    )

    with _lock.pid_lock(_inst.crontab_lock_path()) as got:
        if not got.acquired:
            _fail(
                f"another wiki-weaver crontab mutation is in progress "
                f"(PID {got.holder_pid}); retry"
            )
            return 1
        current = _ct.read_current_crontab()
        if iid in _ct.scan_malformed(current):
            _fail("this instance's crontab block is malformed; inspect `crontab -e`")
            return 1
        _ct.write_crontab(_ct.upsert_block(current, iid, cron_line))

    cfg = _inst.InstanceConfig(
        instance_id=iid,
        canonical_wiki=str(canon),
        cron_expr=cron_expr,
        every=every,
        alert_after=alert_after,
        uses_env_file=uses_env_file,
        created_at=utcnow_iso(),
    )
    _inst.write_instance_config(cfg)
    _inst.logs_dir(iid)  # pre-create so the first tick never fails on a missing dir

    _ok(f"scheduled: {canon}  [{cron_expr}]  instance {iid}")
    print(f"log: {_inst.ingest_log_path(iid)}")
    print(f"to remove: wiki-weaver schedule remove --id {iid}")
    return 0


def _write_env_file(path: Path, key: str) -> None:
    import shlex

    content = f"ANTHROPIC_API_KEY={shlex.quote(key)}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    finally:
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# 7.3 remove
# ---------------------------------------------------------------------------


def _resolve_id_best_effort(wiki: str | None, instance_id: str | None) -> str:
    if instance_id:
        return instance_id
    assert wiki is not None
    try:
        return _inst.instance_id(_inst.canonical_wiki_path(wiki))
    except OSError:
        fallback = Path(wiki).expanduser().absolute()
        return _inst.instance_id(fallback)


def remove(
    wiki: str | None, *, instance_id: str | None = None, purge: bool = False
) -> int:
    iid = _resolve_id_best_effort(wiki, instance_id)

    with _lock.pid_lock(_inst.crontab_lock_path()) as got:
        if not got.acquired:
            _fail(
                f"another wiki-weaver crontab mutation is in progress "
                f"(PID {got.holder_pid}); retry"
            )
            return 1
        current = _ct.read_current_crontab()
        malformed = _ct.scan_malformed(current)
        if iid in malformed:
            _fail(
                f"this instance's crontab block is malformed ({malformed[iid]}); "
                f"inspect `crontab -e`"
            )
            return 1
        blocks = _ct.scan(current)
        if iid not in blocks:
            print("no schedule installed for this wiki; nothing to remove")
        else:
            _ct.write_crontab(_ct.remove_block(current, iid))

    if purge:
        _inst.delete_instance(iid)

    _ok("schedule removed")
    return 0


# ---------------------------------------------------------------------------
# 7.4 status
# ---------------------------------------------------------------------------


def status(wiki: str | None, *, instance_id: str | None = None) -> int:
    iid = _resolve_id_best_effort(wiki, instance_id)

    cfg = _inst.read_instance_config(iid)
    blocks = _ct.scan(_ct.read_current_crontab())
    installed = iid in blocks

    if cfg is None and not installed:
        _fail(f"no known schedule for instance {iid}")
        return 2

    print(f"instance: {iid}")
    if installed:
        print(f"installed: yes  [{blocks[iid].cron_line}]")
    else:
        print("installed: no")

    if cfg is not None:
        print(f"wiki: {cfg.canonical_wiki}")
        print(f"cron_expr: {cfg.cron_expr}")
        print(f"every: {cfg.every}")
        print(f"alert_after: {cfg.alert_after}")
        print(f"uses_env_file: {cfg.uses_env_file}")
        print(f"created_at: {cfg.created_at}")

    st = _inst.read_run_state(iid)
    print(f"last_run_at: {st.last_run_at}")
    print(f"last_exit: {st.last_exit}")
    print(f"last_duration_s: {st.last_duration_s}")
    print(f"consecutive_skips: {st.consecutive_skips}")
    print(f"alert_active: {st.alert_active}")
    print(f"last_skip_at: {st.last_skip_at}")
    print(f"last_holder_pid: {st.last_holder_pid}")

    lock_path = _inst.instance_data_dir(iid) / "ingest.lock"
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            print("lock: stale lock present (unreadable)")
        else:
            if _lock.pid_alive(pid):
                print(f"lock: ingest RUNNING (PID {pid})")
            else:
                print("lock: stale lock present")
    else:
        print("lock: idle")

    if st.alert_active:
        _fail(
            f"\u26a0 ALERT: {st.consecutive_skips} consecutive skips -- ingest may be "
            f"stuck or the interval is shorter than a run takes; consider a longer --every"
        )

    return 0


# ---------------------------------------------------------------------------
# 7.5 list_all
# ---------------------------------------------------------------------------


def list_all() -> int:
    crontab_ids = set(_ct.scan(_ct.read_current_crontab()))
    disk_ids = set(_inst.list_instance_ids())
    all_ids = sorted(crontab_ids | disk_ids)

    if not all_ids:
        print("no scheduled wikis")
        return 0

    blocks = _ct.scan(_ct.read_current_crontab())
    for iid in all_ids:
        cfg = _inst.read_instance_config(iid)
        st = _inst.read_run_state(iid)
        wiki = cfg.canonical_wiki if cfg else "<unknown>"
        cron_expr = (
            cfg.cron_expr
            if cfg
            else (blocks[iid].cron_line if iid in blocks else "<n/a>")
        )
        alert = " ALERT" if st.alert_active else ""
        print(
            f"{iid}  wiki={wiki}  cron={cron_expr}  "
            f"last_run={st.last_run_at}  last_exit={st.last_exit}  "
            f"skips={st.consecutive_skips}{alert}"
        )
    return 0


# ---------------------------------------------------------------------------
# 7.6 run_now -- the tick body (in-process, D2)
# ---------------------------------------------------------------------------


class _Tee:
    """Writes to both the log file handle and the original stream."""

    def __init__(self, log_fh: IO[str], original: IO[str]) -> None:
        self._log_fh = log_fh
        self._original = original

    def write(self, data: str) -> int:
        self._original.write(data)
        self._log_fh.write(data)
        return len(data)

    def flush(self) -> None:
        self._original.flush()
        self._log_fh.flush()


@contextlib.contextmanager
def _tee_stdout_stderr(fh: IO[str]):
    orig_out, orig_err = sys.stdout, sys.stderr
    sys.stdout = _Tee(fh, orig_out)  # type: ignore[assignment]
    sys.stderr = _Tee(fh, orig_err)  # type: ignore[assignment]
    try:
        yield
    finally:
        sys.stdout = orig_out
        sys.stderr = orig_err


def rotate_log_if_needed(log: Path, max_bytes: int, backups: int) -> None:
    """Size rotation, once per tick, at start.

    If log.stat().st_size >= max_bytes, shift ingest.log.{N-1}->{N} ...
    ingest.log->ingest.log.1 (drop the oldest beyond backups).
    """
    if not log.exists() or log.stat().st_size < max_bytes:
        return
    oldest = log.with_name(f"{log.name}.{backups}")
    oldest.unlink(missing_ok=True)
    for n in range(backups - 1, 0, -1):
        src = log.with_name(f"{log.name}.{n}")
        dst = log.with_name(f"{log.name}.{n + 1}")
        if src.exists():
            src.replace(dst)
    log.replace(log.with_name(f"{log.name}.1"))


def run_now(wiki: str) -> int:
    canon = _inst.canonical_wiki_path(wiki)
    if not canon.is_dir():
        _fail(
            f"wiki dir not found: {canon} \u2014 was it moved? re-run `schedule install`"
        )
        return 1

    iid = _inst.instance_id(canon)
    cfg = _inst.read_instance_config(
        iid
    )  # may be None (bare run-now); default alert_after
    alert_after = cfg.alert_after if cfg else _DEFAULT_ALERT_AFTER
    log = _inst.ingest_log_path(iid)
    rotate_log_if_needed(log, _LOG_MAX_BYTES, _LOG_BACKUPS)

    with open(log, "a", encoding="utf-8") as fh:
        _mark(fh, f"=== tick {utcnow_iso()}  wiki={canon}  id={iid} ===")
        # NOTE: ingest_lock_path takes the WIKI PATH (it derives the instance id
        # internally) -- see instances.ingest_lock_path / cli.cmd_ingest (D3). Both
        # trigger sites must pass the wiki path so they compute the identical lock
        # path for the identical wiki.
        lock = _inst.ingest_lock_path(canon)
        res = _lock.try_acquire(lock)

        # ---- SKIP PATH (must-fix #2, #6) ----
        # Note: the env-key gate is intentionally NOT checked here -- a skip is
        # never a failure regardless of environment state (D6): even a broken
        # env should not turn a normal lock-contention skip into an error exit.
        if not res.acquired:
            st = _inst.read_run_state(iid)
            st.consecutive_skips += 1
            st.last_skip_at = utcnow_iso()
            st.last_holder_pid = res.holder_pid
            if st.consecutive_skips >= alert_after:
                st.alert_active = True
                msg = (
                    f"ALERT: skipped {st.consecutive_skips} consecutive cycles -- previous "
                    f"ingest (PID {res.holder_pid}) still running for {canon}. The interval "
                    f"may be shorter than a run takes; investigate or lengthen --every."
                )
                _mark(fh, "ERROR " + msg)
                _fail(msg)
            else:
                msg = (
                    f"SKIP [{st.consecutive_skips}/{alert_after}]: previous ingest "
                    f"(PID {res.holder_pid}) still running for {canon}; skipping this cycle."
                )
                _mark(fh, "WARN " + msg)
                _warn(msg)
            _inst.write_run_state(iid, st)
            return 0  # a skip is NOT a failure -- don't trigger cron MAILTO spam

        # ---- RUN PATH ----
        # The env-key gate runs HERE (not before lock acquisition) so that a
        # broken environment fails fast, loudly, and INTO THE LOG -- but only
        # on cycles that would otherwise actually attempt a drain (D3/D6:
        # "skips do NOT need the gate").
        try:
            st = _inst.read_run_state(iid)
            st.consecutive_skips = 0
            st.alert_active = False
            _inst.write_run_state(iid, st)
            started = time.monotonic()
            gate_failed = False
            with _tee_stdout_stderr(fh):
                if rc := _gate():
                    gate_failed = True
                else:
                    rc = _ingest(canon)
            # Record run-state for BOTH outcomes (gate failure or real ingest) --
            # a persistently broken environment must show up in `status`/`list`,
            # not just the log, or an operator polling state never sees it (C1).
            st.last_run_at = utcnow_iso()
            st.last_exit = rc
            st.last_duration_s = round(time.monotonic() - started, 3)
            _inst.write_run_state(iid, st)
            suffix = " (environment gate failed)" if gate_failed else ""
            _mark(fh, f"--- tick done exit={rc} dur={st.last_duration_s}s{suffix} ---")
            return rc
        finally:
            _lock.release(lock)


def _gate() -> int:
    """Same HARD-prereq preflight cmd_ingest performs, run before any drain attempt."""
    failures = _preflight(require_api_key=True)
    if not failures:
        return 0
    for msg in failures:
        _fail(msg)
    print("Run `wiki-weaver doctor` for full diagnostics.")
    return 1
