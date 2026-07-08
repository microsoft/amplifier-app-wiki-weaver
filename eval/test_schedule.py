"""Unit tests for wiki_weaver.schedule -- the command implementations tying
crontab + instances + pidlock + lib.ingest together.

Coverage (per docs/designs/scheduled-ingestion-spec.md \u00a714):
  - interval_to_cron: friendly sugar -> 5-field cron expressions + validation.
  - install/remove/status/list with `crontab -l`/`-` monkeypatched to an
    in-memory string. Multi-instance coexistence.
  - Crontab-mutation lock (must-fix #5): held lock blocks install, no write.
  - run_now skip path (must-fix #2/#6): contention -> no ingest call, WARN/ERROR
    logged, consecutive_skips incremented, escalation after alert_after.
  - run_now run path: no contention -> ingest called once, state reset+recorded,
    lock released.
  - run_now moved-wiki: nonexistent dir -> return 1, loud message (must-fix #9).
  - cmd_ingest guard (D3): held ingest lock -> EXIT_SKIP, ingest NOT called.

All tests are pure/offline -- no engine, no LLM, no network. `ingest` is
monkeypatched to a stub wherever run_now/cmd_ingest would otherwise invoke it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from wiki_weaver import instances as inst  # noqa: E402
from wiki_weaver import pidlock  # noqa: E402
from wiki_weaver import schedule as sched  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point WIKI_WEAVER_DATA_DIR at a tmp dir for every test in this module."""
    monkeypatch.setenv("WIKI_WEAVER_DATA_DIR", str(tmp_path / "ww-data"))
    yield


@pytest.fixture(autouse=True)
def _bypass_env_gate(monkeypatch: pytest.MonkeyPatch):
    """Bypass the run_now/cmd_ingest env preflight so tests exercise scheduling
    logic itself rather than environment probes (covered by
    eval/test_preflight_gate.py)."""
    monkeypatch.setattr(sched, "_preflight", lambda **_kw: [])
    monkeypatch.setattr("wiki_weaver.cli.preflight", lambda **_kw: [])


class _FakeCrontab:
    """In-memory stand-in for the real `crontab -l` / `crontab -` binary."""

    def __init__(self, initial: str = "") -> None:
        self.text = initial
        self.write_calls: list[str] = []

    def read(self) -> str:
        return self.text

    def write(self, text: str) -> None:
        self.write_calls.append(text)
        self.text = text


def _patch_crontab_backend(monkeypatch: pytest.MonkeyPatch, fake: _FakeCrontab) -> None:
    monkeypatch.setattr(sched._ct, "crontab_available", lambda: True)
    monkeypatch.setattr(sched._ct, "read_current_crontab", fake.read)
    monkeypatch.setattr(sched._ct, "write_crontab", fake.write)
    monkeypatch.setattr(
        sched._ct, "resolve_binary", lambda: "/usr/local/bin/wiki-weaver"
    )


# ---------------------------------------------------------------------------
# interval_to_cron
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("5m", "*/5 * * * *"),
        ("30m", "*/30 * * * *"),
        ("1h", "0 */1 * * *"),
        ("2h", "0 */2 * * *"),
        ("1d", "0 0 */1 * *"),
    ],
)
def test_interval_to_cron_valid(spec: str, expected: str) -> None:
    assert sched.interval_to_cron(spec) == expected


@pytest.mark.parametrize("bad", ["0m", "61m", "5x", ""])
def test_interval_to_cron_invalid_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        sched.interval_to_cron(bad)


def test_interval_to_cron_non_dividing_minutes_warns_but_returns(
    capsys: pytest.CaptureFixture,
) -> None:
    result = sched.interval_to_cron("7m")
    assert result == "*/7 * * * *"
    captured = capsys.readouterr()
    assert "7" in captured.out  # the _warn() call prints to stdout


# ---------------------------------------------------------------------------
# install / remove / status / list -- happy path, multi-instance
# ---------------------------------------------------------------------------


def _make_wiki(tmp_path: Path, name: str) -> Path:
    wiki = tmp_path / name
    wiki.mkdir()
    return wiki


def test_install_writes_block_persists_config_precreates_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    wiki = _make_wiki(tmp_path, "w")
    rc = sched.install(str(wiki), every="5m", cron=None, alert_after=3)

    assert rc == 0
    assert len(fake.write_calls) == 1

    canon = inst.canonical_wiki_path(wiki)
    iid = inst.instance_id(canon)
    assert f"# >>> wiki-weaver:{iid} >>>" in fake.text
    assert "schedule run-now --wiki" in fake.text
    assert str(canon) in fake.text

    cfg = inst.read_instance_config(iid)
    assert cfg is not None
    assert cfg.cron_expr == "*/5 * * * *"
    assert cfg.every == "5m"
    assert cfg.canonical_wiki == str(canon)

    assert inst.logs_dir(iid).is_dir()


def test_install_requires_exactly_one_of_every_or_cron(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    wiki = _make_wiki(tmp_path, "w")

    rc = sched.install(str(wiki), every="5m", cron="*/5 * * * *", alert_after=3)
    assert rc == 2
    assert fake.write_calls == []

    rc2 = sched.install(str(wiki), every=None, cron=None, alert_after=3)
    assert rc2 == 2
    assert fake.write_calls == []


def test_install_missing_wiki_dir_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)

    rc = sched.install(str(tmp_path / "does-not-exist"), every="5m", cron=None)
    assert rc == 1
    assert fake.write_calls == []


def test_two_installs_for_different_wikis_coexist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)

    wiki_a = _make_wiki(tmp_path, "wiki-a")
    wiki_b = _make_wiki(tmp_path, "wiki-b")

    assert sched.install(str(wiki_a), every="5m", cron=None) == 0
    assert sched.install(str(wiki_b), every="10m", cron=None) == 0

    from wiki_weaver import crontab as ct

    blocks = ct.scan(fake.text)
    iid_a = inst.instance_id(wiki_a)
    iid_b = inst.instance_id(wiki_b)
    assert set(blocks) == {iid_a, iid_b}


def test_status_reports_installed_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    wiki = _make_wiki(tmp_path, "w")
    sched.install(str(wiki), every="5m", cron=None)

    rc = sched.status(str(wiki))
    assert rc == 0
    out = capsys.readouterr().out
    assert "installed: yes" in out
    assert "cron_expr: */5 * * * *" in out


def test_list_all_shows_installed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    wiki = _make_wiki(tmp_path, "w")
    sched.install(str(wiki), every="5m", cron=None)

    rc = sched.list_all()
    assert rc == 0
    out = capsys.readouterr().out
    iid = inst.instance_id(wiki)
    assert iid in out


def test_remove_strips_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    wiki = _make_wiki(tmp_path, "w")
    sched.install(str(wiki), every="5m", cron=None)

    iid = inst.instance_id(wiki)
    rc = sched.remove(str(wiki))
    assert rc == 0

    from wiki_weaver import crontab as ct

    assert iid not in ct.scan(fake.text)
    # Instance dir survives by default (no --purge).
    assert inst.read_instance_config(iid) is not None


def test_remove_purge_deletes_instance_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    wiki = _make_wiki(tmp_path, "w")
    sched.install(str(wiki), every="5m", cron=None)
    iid = inst.instance_id(wiki)

    rc = sched.remove(str(wiki), purge=True)
    assert rc == 0
    assert inst.read_instance_config(iid) is None


# ---------------------------------------------------------------------------
# Crontab-mutation lock (must-fix #5)
# ---------------------------------------------------------------------------


def test_install_fails_when_crontab_lock_held_by_live_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeCrontab()
    _patch_crontab_backend(monkeypatch, fake)
    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    lock_path = inst.crontab_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("424242", encoding="utf-8")

    wiki = _make_wiki(tmp_path, "w")
    rc = sched.install(str(wiki), every="5m", cron=None)

    assert rc == 1
    assert fake.write_calls == []


# ---------------------------------------------------------------------------
# run_now -- skip path (must-fix #2/#6)
# ---------------------------------------------------------------------------


def test_run_now_skip_path_does_not_call_ingest_and_logs_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _make_wiki(tmp_path, "w")
    iid = inst.instance_id(wiki)

    ingest_calls: list[Path] = []
    monkeypatch.setattr(sched, "_ingest", lambda canon: ingest_calls.append(canon) or 0)

    # Pre-acquire the ingest lock with a "live" PID.
    lock_path = inst.ingest_lock_path(wiki)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("777777", encoding="utf-8")
    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    rc = sched.run_now(str(wiki))

    assert rc == 0
    assert ingest_calls == []

    st = inst.read_run_state(iid)
    assert st.consecutive_skips == 1
    assert st.last_holder_pid == 777777

    log_text = inst.ingest_log_path(iid).read_text(encoding="utf-8")
    assert "WARN SKIP [1/3]" in log_text


def test_run_now_skip_path_escalates_after_alert_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _make_wiki(tmp_path, "w")
    iid = inst.instance_id(wiki)

    monkeypatch.setattr(sched, "_ingest", lambda canon: 0)

    lock_path = inst.ingest_lock_path(wiki)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("777777", encoding="utf-8")
    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    # Install a config with alert_after=3 so run_now can read it.
    cfg = inst.InstanceConfig(
        instance_id=iid,
        canonical_wiki=str(inst.canonical_wiki_path(wiki)),
        cron_expr="*/5 * * * *",
        every="5m",
        alert_after=3,
        uses_env_file=False,
        created_at="2026-07-08T00:00:00+00:00",
    )
    inst.write_instance_config(cfg)

    for _ in range(3):
        rc = sched.run_now(str(wiki))
        assert rc == 0

    st = inst.read_run_state(iid)
    assert st.consecutive_skips == 3
    assert st.alert_active is True

    log_text = inst.ingest_log_path(iid).read_text(encoding="utf-8")
    assert "ERROR ALERT: skipped 3 consecutive cycles" in log_text


# ---------------------------------------------------------------------------
# run_now -- run path
# ---------------------------------------------------------------------------


def test_run_now_run_path_calls_ingest_once_and_records_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _make_wiki(tmp_path, "w")
    iid = inst.instance_id(wiki)

    ingest_calls: list[Path] = []

    def _fake_ingest(canon: Path) -> int:
        ingest_calls.append(canon)
        return 0

    monkeypatch.setattr(sched, "_ingest", _fake_ingest)

    rc = sched.run_now(str(wiki))

    assert rc == 0
    assert len(ingest_calls) == 1
    assert ingest_calls[0] == inst.canonical_wiki_path(wiki)

    st = inst.read_run_state(iid)
    assert st.consecutive_skips == 0
    assert st.last_exit == 0
    assert st.last_run_at is not None
    assert st.last_duration_s is not None

    # Lock released -- file gone after the call.
    lock_path = inst.ingest_lock_path(wiki)
    assert not lock_path.exists()

    log_text = inst.ingest_log_path(iid).read_text(encoding="utf-8")
    assert "--- tick done exit=0" in log_text


def test_run_now_run_path_returns_stub_rc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = _make_wiki(tmp_path, "w")
    monkeypatch.setattr(sched, "_ingest", lambda canon: 1)

    rc = sched.run_now(str(wiki))
    assert rc == 1

    iid = inst.instance_id(wiki)
    st = inst.read_run_state(iid)
    assert st.last_exit == 1


# ---------------------------------------------------------------------------
# run_now -- moved-wiki (must-fix #9 self-announcement)
# ---------------------------------------------------------------------------


def test_run_now_moved_wiki_returns_1_and_fails_loud(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    missing = tmp_path / "gone"
    rc = sched.run_now(str(missing))
    assert rc == 1
    out = capsys.readouterr().out
    assert "wiki dir not found" in out


# ---------------------------------------------------------------------------
# cmd_ingest guard (D3)
# ---------------------------------------------------------------------------


def test_cmd_ingest_guard_returns_exit_skip_and_does_not_call_ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wiki_weaver import cli

    wiki = _make_wiki(tmp_path, "w")

    ingest_calls: list[tuple] = []
    monkeypatch.setattr(cli, "ingest", lambda *a, **kw: ingest_calls.append(a) or 0)

    lock_path = inst.ingest_lock_path(str(wiki))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("888888", encoding="utf-8")
    monkeypatch.setattr(pidlock, "pid_alive", lambda pid: True)

    args = argparse.Namespace(
        wiki=str(wiki), source=None, max_cycles=None, keep_going=False
    )
    rc = cli.cmd_ingest(args)

    assert rc == sched.EXIT_SKIP
    assert ingest_calls == []
