"""result.json shape and the exit-code table (no model calls, no engine runs for 1/3/75)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from wiki_weaver import pidlock
from wiki_weaver import result as rs


def classify(raw: dict) -> tuple[bool, str]:
    """Condition from amplifier-resolver-weaver resolver.py::_classify, applied to the
    counts its progress.py::_parse extracts (raw["counts"][...])."""
    keys = ("total", "converged", "failed", "blocked", "errored", "skipped")
    counts = {k: int((raw.get("counts") or {}).get(k, 0) or 0) for k in keys}
    total, converged = counts["total"], counts["converged"]
    if (
        total > 0
        and converged == total
        and counts["failed"] == 0
        and counts["blocked"] == 0
        and counts["errored"] == 0
    ):
        return True, f"fully converged ({converged}/{total})"
    if converged > 0:
        return False, "partial"
    return False, "zero converged"


def row(name: str, status: str) -> dict:
    return {
        "source": name,
        "status": status,
        "converged": status == "converged",
        "reason": "r" if status != "converged" else "",
        "failure_kind": "",
    }


def test_clean_result_passes_classify():
    rows = [row("a.md", "converged"), row("b.md", "converged"), row("z.md", "skipped")]
    data = rs.build(run_id="ingest-x", status="final", rows=rows, errored=[])
    assert data["counts"] == {
        "total": 2,
        "converged": 2,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 1,
    }
    assert data["total"] == 2 and data["converged"] == 2
    assert data["status"] == "final" and data["verdict"] == "converged"
    assert classify(json.loads(json.dumps(data))) == (True, "fully converged (2/2)")
    assert rs.EXIT_FOR_VERDICT[data["verdict"]] == 0


def test_verdicts_and_exit_codes():
    def ex(rows, errored=()):
        d = rs.build(run_id="r", status="final", rows=rows, errored=list(errored))
        return d["verdict"], rs.EXIT_FOR_VERDICT[d["verdict"]], classify(d)[0]

    assert ex([row("a", "converged"), row("b", "failed")]) == ("partial", 0, False)
    assert ex([row("a", "failed")]) == ("failed", 5, False)
    assert ex([row("a", "skipped")]) == ("empty", 3, False)
    assert ex([row("a", "converged")], [{"reason": "index failed"}]) == ("errored", 1, False)
    d = rs.build(run_id="r", status="final", rows=[row("a", "failed")], errored=[])
    assert d["failed"] == [{"source": "a", "reason": "r", "failure_kind": ""}]


def ww(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _wiki(tmp_path: Path) -> Path:
    from wiki_weaver.cli import scaffold

    w = tmp_path / "w"
    w.mkdir()
    scaffold(w)
    (w / "lens.md").write_text("## Purpose\nx\n")
    return w


def test_exit_missing_dir_and_usage(tmp_path: Path):
    assert ww("ingest", "--wiki", str(tmp_path / "nope")).returncode == 1
    assert ww("ingest", "--bogus").returncode == 2


def test_exit_nothing_to_do_writes_one_result(tmp_path: Path):
    w = _wiki(tmp_path)
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    r = ww("ingest", "--wiki", str(w), env=env)
    assert r.returncode == 3, r.stderr
    results = list((w / ".wiki/runs").glob("ingest-*/result.json"))
    assert len(results) == 1
    data = json.loads(results[0].read_text())
    assert data["verdict"] == "empty" and data["status"] == "final"
    assert not pidlock.is_held(pidlock.lock_path(w))


def test_exit_75_when_locked_and_lock_released(tmp_path: Path):
    w = _wiki(tmp_path)
    hold = (
        "import sys, time; from pathlib import Path; from wiki_weaver import pidlock;"
        "print(pidlock.acquire(Path(sys.argv[1])), flush=True); time.sleep(30)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", hold, str(pidlock.lock_path(w))], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout.readline().strip() == "True"
        assert ww("ingest", "--wiki", str(w)).returncode == 75
        assert pidlock.holder(pidlock.lock_path(w)) == holder.pid  # not stolen
    finally:
        holder.kill()
        holder.wait()
    # a dead holder's lock is released by the OS
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    assert ww("ingest", "--wiki", str(w), env=env).returncode == 3
    assert not pidlock.is_held(pidlock.lock_path(w))


def test_preflight_failure_is_exit_1_with_result(tmp_path: Path):
    w = _wiki(tmp_path)
    (w / "lens.md").unlink()
    assert ww("ingest", "--wiki", str(w)).returncode == 1
    data = json.loads(next((w / ".wiki/runs").glob("ingest-*/result.json")).read_text())
    assert data["verdict"] == "errored" and "lens.md" in data["errored"][0]["reason"]
