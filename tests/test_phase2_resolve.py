"""Phase 2: what the first V4 job inside Resolve found (docs/LEARNINGS.md, 2026-10-21).

1. A corpus unpacked foreign-owned (packed by uid 501, unpacked by a root worker): git
   refuses it as "dubious ownership". V4 trusts its corpus (process-scoped, via git's
   environment config) before any git call.
2. A git failure in a step or in recovery reported `kind: engine` with git's message
   dropped. The first `fatal:` line now rides in the reason.
3. `.wiki/ingest.lock` was left behind and travelled in the archive.
4. result.json lives under .wiki/runs/ (never archived); `.wiki/last-run.json` travels.

Git's own test hook `GIT_TEST_ASSUME_DIFFERENT_OWNER=1` makes git treat every repository
as foreign-owned, so the ownership refusal is reproduced without root. Model-free except
where marked: the engine is the stand-in from test_contract_corpus.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from engine import needs_engine_and_key
from test_contract_corpus import _ingest
from test_steps import ORCHARD, fake_brief

from wiki_weaver import cli, pidlock

FOREIGN = {"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1"}


@pytest.fixture
def one(corpus: Path) -> Path:
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != ORCHARD:
            f.unlink()
    return corpus


def _git(w: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=w, capture_output=True, text=True, check=False,
        env={**os.environ, **(env or {})},
    )  # fmt: skip


def _ww(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True, text=True, check=False, timeout=300,
        env={**os.environ, **(env or {})},
    )  # fmt: skip


def _newest_result(w: Path) -> dict:
    return json.loads(max((w / ".wiki/runs").glob("ingest-*/result.json")).read_text())


# 1 ownership ----------------------------------------------------------------------------
def test_foreign_owned_corpus_is_refused_by_git_without_trust(one: Path) -> None:
    """The precondition the fixes below answer: git really does refuse it."""
    r = _git(one, "status", env=FOREIGN)
    assert r.returncode != 0 and "dubious ownership" in r.stderr


@needs_engine_and_key
def test_init_trusts_a_foreign_owned_corpus(tmp_path: Path) -> None:
    w = tmp_path / "fresh"
    r = _ww("init", str(w), "--plain", env=FOREIGN)
    assert r.returncode == 0, r.stderr
    assert (w / ".git").is_dir()
    # init's own commits landed: git worked in the foreign-owned directory. The trust was
    # init's process environment only, so this reader supplies its own.
    trusted = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(w.resolve()),
    }
    log = _git(w, "log", "--format=%s", env={**FOREIGN, **trusted})
    assert log.returncode == 0, log.stderr
    assert "init: lens.md" in log.stdout.splitlines()


@needs_engine_and_key
@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="chown to another uid needs root (the Resolve worker's case); "
    "test_init_trusts_a_foreign_owned_corpus covers it via GIT_TEST_ASSUME_DIFFERENT_OWNER",
)
def test_init_on_a_corpus_chowned_to_another_uid(tmp_path: Path) -> None:
    w = tmp_path / "chowned"
    w.mkdir()
    (w / "lens.md").write_text("## Purpose\nx\n")
    for p in [w, *w.rglob("*")]:
        os.chown(p, 501, 501)
    r = _ww("init", str(w), "--plain")
    assert r.returncode == 0, r.stderr
    assert (w / ".git").is_dir()


def test_ingest_trusts_a_foreign_owned_corpus(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve's archive never carries .git, but a corpus can reach `ingest` foreign-owned
    with .git present (init was not the first verb); every step's git must still work."""
    import signal

    def too_long(*_):  # untrusted, the stand-in engine's select loop never drains
        raise TimeoutError("ingest did not finish: git refused the corpus")

    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    prev = signal.signal(signal.SIGALRM, too_long)
    signal.alarm(60)
    try:
        assert _ingest(one, monkeypatch) == 0
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev)
    res = _newest_result(one)
    assert res["errored"] == [] and res["counts"]["converged"] == 1


def test_trust_is_idempotent_and_process_scoped(one: Path) -> None:
    cfg = Path(os.environ["GIT_CONFIG_GLOBAL"])
    before = cfg.read_text()
    cli.trust_corpus(one)
    cli.trust_corpus(one)
    n = int(os.environ["GIT_CONFIG_COUNT"])
    pairs = [
        (os.environ[f"GIT_CONFIG_KEY_{i}"], os.environ[f"GIT_CONFIG_VALUE_{i}"]) for i in range(n)
    ]
    assert pairs.count(("safe.directory", str(one.resolve()))) == 1, pairs
    assert cfg.read_text() == before  # nothing written to the global config
    # a git child inherits the trust
    r = _git(one, "status", env=FOREIGN)
    assert r.returncode == 0, r.stderr


# 2 the git cause surfaces ---------------------------------------------------------------
def _break_git(w: Path) -> None:
    (w / ".git" / "HEAD").write_text("not a ref\n")


def _tool_engine(nodes: tuple[str, ...]):
    """Stand-in for dot-runner that runs real tool steps and records them the way the
    engine does: <logs_root>/<node>/status.json (failure_reason = "Command exited with
    code N: <stderr>"), <node>/output.txt and trace.jsonl. Stops at the first failure."""

    def run(graph, corpus, params, logs_root, **kw):
        env = {**os.environ, **{k.upper(): v for k, v in params.items()}}
        logs_root.mkdir(parents=True, exist_ok=True)
        for node in nodes:
            if node == "page_select":  # the brief (a model step) by hand
                fake_brief(corpus, ["orchard-rollout"])
            r = subprocess.run(
                [sys.executable, "-m", "wiki_weaver.steps", node],
                cwd=corpus, capture_output=True, text=True, check=False, env=env,
            )  # fmt: skip
            ok = r.returncode == 0
            d = logs_root / node
            d.mkdir()
            (d / "output.txt").write_text(r.stdout + r.stderr)
            (d / "status.json").write_text(
                json.dumps(
                    {
                        "node_id": node,
                        "status": "success" if ok else "fail",
                        "failure_reason": None
                        if ok
                        else f"Command exited with code {r.returncode}: {r.stderr.strip()}",
                    }
                )
            )
            with (logs_root / "trace.jsonl").open("a") as fh:
                fh.write(json.dumps({"node_id": node, "status": "success" if ok else "fail"}))
                fh.write("\n")
            if not ok:
                return 1
        return 0

    return run


def test_engine_git_failure_reason_carries_the_fatal_line(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_git(one)
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(
        cli, "run_graph", _tool_engine(("select", "assemble", "page_select", "checks"))
    )
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(one)])
    a.func(a)
    errored = _newest_result(one)["errored"]
    assert errored and errored[0]["kind"] == "engine"
    assert "fatal: not a git repository" in errored[0]["reason"], errored[0]["reason"]


def test_recovery_git_failure_reason_carries_the_fatal_line(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = one / ".wiki/work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "journal.json").write_text(
        json.dumps(
            {"op": "write", "source": None, "dest": None, "paths": ["x.md"], "committed": False}
        )
    )
    _break_git(one)
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(one)])
    a.func(a)
    errored = _newest_result(one)["errored"]
    assert errored and errored[0]["kind"] == "recovery"
    assert "fatal: not a git repository" in errored[0]["reason"], errored[0]["reason"]


DUBIOUS_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "wiki_weaver/steps.py", line 86, in git\n'
    "RuntimeError: git status --porcelain -z --untracked-files=all --no-renames: "
    "fatal: detected dubious ownership in repository at '/project/.resolve/corpus'\n"
    "To add an exception for this directory, call:\n\n"
    "\tgit config --global --add safe.directory /project/.resolve/corpus\n"
)  # verbatim shape of the checks node's failure in the first Resolve job


def test_recover_reason_keeps_the_fatal_line_not_the_hint(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git's hint lines follow its fatal: line; the last stderr line is the hint."""
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if cmd[1:] == ["-m", "wiki_weaver.steps", "recover"]:
            return subprocess.CompletedProcess(cmd, 1, "", DUBIOUS_TRACEBACK)
        return real(cmd, *a, **kw)

    monkeypatch.setattr(cli.subprocess, "run", fake)
    ok, msg = cli.recover(one)
    assert not ok
    assert "fatal: detected dubious ownership in repository at '/project/.resolve/corpus'" in msg


def test_git_fatal_line_is_the_first_fatal_line() -> None:
    from wiki_weaver.steps import git_fatal

    text = (
        "Traceback (most recent call last):\n  ...\nRuntimeError: git status --porcelain: "
        "fatal: detected dubious ownership in repository at '/project/.resolve/corpus'\n"
        "To add an exception for this directory, call:\n\n"
        "\tgit config --global --add safe.directory /project/.resolve/corpus\n"
        "fatal: a second one\n"
    )
    assert git_fatal(text) == (
        "fatal: detected dubious ownership in repository at '/project/.resolve/corpus'"
    )
    assert git_fatal("no git here") is None


# 3 the run lock does not travel ---------------------------------------------------------
LOCK = ".wiki/ingest.lock"


def test_ingest_success_leaves_no_lock(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ingest(one, monkeypatch) == 0
    assert not (one / LOCK).exists()


def test_ingest_hold_leaves_no_lock(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ingest(one, monkeypatch, hold=True)
    assert _newest_result(one)["counts"]["failed"] == 1
    assert not (one / LOCK).exists()


def test_ingest_error_leaves_no_lock(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_preflight", lambda c: ["forced preflight failure"])
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(one)])
    assert a.func(a) == 1
    assert not (one / LOCK).exists()


def test_ingest_interrupt_leaves_no_lock(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **kw):
        raise KeyboardInterrupt

    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", boom)
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(one)])
    with pytest.raises(KeyboardInterrupt):
        a.func(a)
    assert not (one / LOCK).exists()


def test_init_leaves_no_lock(tmp_path: Path) -> None:
    w = tmp_path / "w"
    w.mkdir()
    (w / "lens.md").write_text("## Purpose\nx\n")
    assert _ww("init", str(w), "--plain").returncode == 0
    assert not (w / LOCK).exists()


def test_lock_survives_a_release_racing_an_acquire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the lock on release is safe only if an acquirer that locked the old,
    now-unlinked file notices and retries on the new one."""
    import fcntl

    lock = tmp_path / ".wiki" / "ingest.lock"
    lock.parent.mkdir()
    lock.write_text("")
    real = fcntl.flock
    calls = {"n": 0}

    def racing_flock(fd, op):
        calls["n"] += 1
        if calls["n"] == 1:
            lock.unlink()  # the previous holder releases between our open and our flock
        return real(fd, op)

    monkeypatch.setattr(pidlock.fcntl, "flock", racing_flock)
    assert pidlock.acquire(lock)
    monkeypatch.setattr(pidlock.fcntl, "flock", real)
    try:
        assert lock.exists()
        assert pidlock.is_held(lock)
    finally:
        pidlock.release(lock)
    assert not lock.exists()


# 4 the run record survives the round trip -----------------------------------------------
def test_ingest_writes_last_run_equal_to_result(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ingest(one, monkeypatch) == 0
    last = one / ".wiki/last-run.json"
    assert last.is_file()
    (res,) = (one / ".wiki/runs").glob("ingest-*/result.json")
    assert json.loads(last.read_text()) == json.loads(res.read_text())


def test_last_run_is_overwritten_by_the_next_run(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _ingest(one, monkeypatch) == 0
    first = json.loads((one / ".wiki/last-run.json").read_text())["run_id"]
    assert _ingest(one, monkeypatch) == 3  # nothing new in the inbox
    second = json.loads((one / ".wiki/last-run.json").read_text())
    assert second["run_id"] != first
    assert second == _newest_result(one)


def test_last_run_written_on_an_errored_run(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_preflight", lambda c: ["forced preflight failure"])
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(one)])
    a.func(a)
    assert json.loads((one / ".wiki/last-run.json").read_text()) == _newest_result(one)
