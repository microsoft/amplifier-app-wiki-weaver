"""Phase A: one failing test per audited bug, written from its repro before the fix.

No model calls. The shell-injection test runs dot-runner on a tool-only graph.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_steps import ORCHARD, cite, fake_brief, page, step

from wiki_weaver import checks as ck

ROOT = Path(__file__).resolve().parent.parent
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]


def _to_write(corpus: Path, slugs: list[str], run: Path) -> None:
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, slugs)
    assert step(corpus, "page_select") == (0, "ok")


def _findings(corpus: Path) -> str:
    f = corpus / ".wiki/work/findings.md"
    return f.read_text() if f.exists() else ""


def _good_summary(corpus: Path) -> None:
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / "source-2031-03-02-orchard-sync.md").write_text(page("O", [ORCHARD], q + "\n"))


def _git_clean(corpus: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=corpus, capture_output=True, text=True, check=True
    ).stdout


# ------------------------------------------------------------------ 1. superseded marker


def test_superseded_block_citing_the_current_source_fails(corpus: Path, tmp_path: Path):
    _to_write(corpus, [], tmp_path / "r")
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    body = (
        "<!-- superseded: 2031-03-02 --> Overtaken on 03-02: the rollout now ships Friday "
        f"{q} <!-- /superseded -->\n"
    )
    (corpus / "source-2031-03-02-orchard-sync.md").write_text(page("O", [ORCHARD], body))
    assert step(corpus, "checks") == (0, "rewrite")
    assert "superseded block cites the current source" in _findings(corpus)


def test_superseded_block_form_parsed():
    text = (
        'keep [s1: "a b c d e"]\n<!-- superseded: 2031-03-02 -->old [s2: "f g h i j"]\n'
        'still old<!-- /superseded -->\nnew [s3: "k l m n o"]\n'
    )
    assert ck.check_superseded_blocks("p.md", text, 3) == []
    assert ck.check_superseded_blocks("p.md", text, 2) != []
    # legacy single marker: its span is the rest of that line
    assert ck.check_superseded_blocks("p.md", '<!-- superseded: 2031-03-02 --> x [s4: "a"]\n', 4)


# ------------------------------------------------------------------ 2. crash recovery


def _crash_commit_before_ledger(corpus: Path, monkeypatch) -> None:
    """Run step_commit in-process and kill it right after the source is placed in
    _sources/ (the ledger write raises)."""
    from wiki_weaver import ledger, steps

    monkeypatch.chdir(corpus)

    def boom(*a, **k):
        raise RuntimeError("process died")

    monkeypatch.setattr(ledger, "append_row", boom)
    with pytest.raises(RuntimeError):
        steps.step_commit(str(corpus / ".wiki/runs/r"))


def test_crash_between_move_and_commit_keeps_source_in_one_place(
    corpus: Path, tmp_path: Path, monkeypatch
):
    _to_write(corpus, [], tmp_path / "r")
    _good_summary(corpus)
    assert step(corpus, "checks") == (0, "pass")
    _crash_commit_before_ledger(corpus, monkeypatch)
    assert step(corpus, "recover") == (0, "recovered")
    places = [(corpus / "_inbox" / ORCHARD).exists(), (corpus / "_sources" / ORCHARD).exists()]
    assert places.count(True) == 1, places
    assert _git_clean(corpus) == ""


def test_crash_after_commit_before_inbox_removal(corpus: Path, tmp_path: Path, monkeypatch):
    """The last step is removing the inbox copy; a crash just before it leaves a duplicate
    that recover clears, keeping the committed copy."""
    from wiki_weaver import steps

    _to_write(corpus, [], tmp_path / "r")
    _good_summary(corpus)
    step(corpus, "checks")
    monkeypatch.chdir(corpus)
    real_unlink = Path.unlink

    def die_on_inbox(self, *a, **k):
        if self.parent.name == "_inbox":
            raise RuntimeError("process died")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", die_on_inbox)
    with pytest.raises(RuntimeError):
        steps.step_commit(str(tmp_path / "r"))
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert (corpus / "_inbox" / ORCHARD).exists() and (corpus / "_sources" / ORCHARD).exists()
    assert step(corpus, "recover")[0] == 0
    assert not (corpus / "_inbox" / ORCHARD).exists()
    assert (corpus / "_sources" / ORCHARD).exists()


def test_recover_restores_from_head_not_the_index(corpus: Path, tmp_path: Path):
    _to_write(corpus, [], tmp_path / "r")
    (corpus / "source-2031-03-02-orchard-sync.md").write_text("half a page")
    (corpus / "lens.md").write_text("half a lens")
    subprocess.run([*GIT, "add", "-A"], cwd=corpus, check=True)  # staged, not committed
    assert step(corpus, "recover") == (0, "recovered")
    assert not (corpus / "source-2031-03-02-orchard-sync.md").exists()
    assert (corpus / "lens.md").read_text() != "half a lens"
    assert _git_clean(corpus) == ""


def test_ingest_does_not_start_when_recovery_fails(tmp_path: Path):
    from wiki_weaver.cli import scaffold

    w = tmp_path / "w"
    w.mkdir()
    scaffold(w)
    (w / "lens.md").write_text("## Purpose\nx\n")
    (w / ".wiki/work").mkdir(parents=True)
    (w / ".wiki/work/current.json").write_text("{not json")
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "ingest", "--wiki", str(w)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    run = next((w / ".wiki/runs").glob("ingest-*"))
    assert not list(run.glob("batch-*"))
    assert "recover" in json.loads((run / "result.json").read_text())["errored"][0]["reason"]


# ------------------------------------------------------------------ 3. loss guard


def test_deleted_tracked_page_fails(corpus: Path, tmp_path: Path):
    (corpus / "orchard-rollout.md").write_text(page("R", [], "## Plan\nkept\n"))
    subprocess.run([*GIT, "add", "-A"], cwd=corpus, check=True)
    subprocess.run([*GIT, "commit", "-qm", "page"], cwd=corpus, check=True)
    _to_write(corpus, ["orchard-rollout"], tmp_path / "r")
    _good_summary(corpus)
    (corpus / "orchard-rollout.md").unlink()
    assert step(corpus, "checks") == (0, "rewrite")
    assert "orchard-rollout.md: page deleted" in _findings(corpus)


FM = "---\ntitle: T\ntype: topics\nsources: [a.md]\nlast_updated: 2031-03-02\n---\n"


def test_one_marker_does_not_exempt_line_loss():
    before = FM + "## Plan\n" + "".join(f"line {i}\n" for i in range(10))
    after = (
        FM + "## Plan\nline 0\nline 1\n"
        "<!-- superseded: 2031-03-05 -->unrelated<!-- /superseded -->\n"
    )
    assert any("lost" in e for e in ck.check_content_loss("p.md", before, after))


def test_heading_loss_needs_an_adjacent_marker():
    before = FM + "## Plan\nkept\n## Budget\nkept too\n"
    far = FM + "## Plan\nkept\n<!-- superseded: 2031-03-05 -->x<!-- /superseded -->\nkept too\n"
    assert any("budget" in e for e in ck.check_content_loss("p.md", before, far))
    adjacent = (
        FM + "## Plan\nkept\n<!-- superseded: 2031-03-05 -->\n## Budget\n"
        "<!-- /superseded -->\nkept too\n"
    )
    assert ck.check_content_loss("p.md", before, adjacent) == []


# ------------------------------------------------------------------ 4. shell injection


def _select_only_graph() -> str:
    text = (ROOT / "pipeline" / "ingest.dot").read_text()
    m = re.search(r"\n    select \[.*?\]\n", text, re.DOTALL)
    params = re.search(r'params="([^"]*)"', text).group(1)
    return (
        f'digraph T {{\n graph [goal="t", params="{params}"]\n start [shape=Mdiamond]\n'
        f" done [shape=Msquare]\n{m.group(0)}\n start -> select\n"
        ' select -> done [condition="outcome=success"]\n'
        ' select -> done [condition="outcome=fail"]\n}\n'
    )


@pytest.mark.skipif(not shutil.which("dot-runner"), reason="dot-runner not installed")
def test_parameter_with_shell_metacharacters_is_not_executed(corpus: Path, tmp_path: Path):
    evil = "Bob's chat $(touch PWNED) `touch PWNED2` (draft).md"
    shutil.copy2(corpus / "_inbox" / ORCHARD, corpus / "_inbox" / evil)
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != evil:
            f.unlink()
    g = tmp_path / "select.dot"
    g.write_text(_select_only_graph())
    run = tmp_path / "run dir $(touch PWNED3)"
    run.mkdir()
    r = subprocess.run(
        [
            "dot-runner",
            "run",
            str(g),
            "--cwd",
            ".",
            "--logs-root",
            str(tmp_path / "logs"),
            "--param",
            f"py={sys.executable}",
            "--param",
            f"run_dir={run}",
            "--param",
            "cap=0",
            "--param",
            f"only={evil}",
        ],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    for name in ("PWNED", "PWNED2", "PWNED3"):
        assert not list(corpus.rglob(name)) and not list(tmp_path.rglob(name)), name
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    cur = json.loads((corpus / ".wiki/work/current.json").read_text())
    assert cur["filename"] == evil


# ------------------------------------------------------------------ 5. lock race

HOLD = (
    "import sys, time; from pathlib import Path; from wiki_weaver import pidlock;"
    "ok = pidlock.acquire(Path(sys.argv[1])); print('held' if ok else 'busy', flush=True);"
    "time.sleep(float(sys.argv[2]))"
)


def _holder(lock: Path, secs: float = 20) -> subprocess.Popen:
    p = subprocess.Popen(
        [sys.executable, "-c", HOLD, str(lock), str(secs)], stdout=subprocess.PIPE, text=True
    )
    assert p.stdout.readline().strip() == "held"
    return p


def test_lock_is_not_stolen_mid_write(tmp_path: Path):
    """A second run that reads the lock between create and PID write must not call it
    stale."""
    from wiki_weaver import pidlock

    lock = tmp_path / ".wiki" / "ingest.lock"
    holder = _holder(lock)
    try:
        lock.write_text("")  # what a racing reader sees before the PID lands
        r = subprocess.run(
            [sys.executable, "-c", HOLD, str(lock), "0"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.stdout.strip() == "busy"
    finally:
        holder.kill()
        holder.wait()
    assert pidlock.acquire(lock)  # released with its holder
    pidlock.release(lock)


def test_concurrent_starts_exactly_one_proceeds(tmp_path: Path):
    lock = tmp_path / ".wiki" / "ingest.lock"
    for _ in range(10):
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", HOLD, str(lock), "0.5"], stdout=subprocess.PIPE, text=True
            )
            for _ in range(4)
        ]
        outs = [p.communicate()[0].strip() for p in procs]
        assert outs.count("held") == 1, outs


def _wiki(tmp_path: Path) -> Path:
    from wiki_weaver.cli import scaffold

    w = tmp_path / "w"
    w.mkdir()
    scaffold(w)
    (w / "lens.md").write_text("## Purpose\nx\n")
    return w


def test_ingest_and_init_exit_75_while_another_run_holds_the_lock(tmp_path: Path):
    from wiki_weaver import pidlock

    w = _wiki(tmp_path)
    holder = _holder(pidlock.lock_path(w))
    try:
        for args in (["ingest", "--wiki", str(w)], ["init", str(w), "--plain"]):
            r = subprocess.run(
                [sys.executable, "-m", "wiki_weaver.cli", *args],
                capture_output=True,
                text=True,
                check=False,
            )
            assert r.returncode == 75, (args, r.returncode, r.stderr)
    finally:
        holder.kill()
        holder.wait()


# ------------------------------------------------------------------ 6. smaller ones


def test_ask_never_reads_outside_the_corpus(corpus: Path):
    (corpus.parent / "outside.md").write_text("secret")
    (corpus / "inside.md").write_text("ok")
    d = corpus / ".wiki/ask/q"
    d.mkdir(parents=True)
    (d / "picks.txt").write_text("../outside.md\n/etc/hosts\ninside.md\n")
    assert step(corpus, "ask_read", str(d)) == (0, "ok")
    assert json.loads((d / "pages.json").read_text()) == ["inside.md"]
    assert "secret" not in (d / "pages.md").read_text()


def test_failed_index_step_restores_index_and_log_and_exits_1(corpus: Path):
    text = (ROOT / "pipeline" / "ingest.dot").read_text()
    assert not re.search(r"index\s*->\s*finalize\s*\[[^\]]*outcome=fail", text)
    assert re.search(r"index\s*->\s*index_restore\s*\[[^\]]*outcome=fail", text)
    (corpus / "index.md").write_text("old index\n")
    (corpus / "log.md").write_text("old log\n")
    subprocess.run([*GIT, "add", "-A"], cwd=corpus, check=True)
    subprocess.run([*GIT, "commit", "-qm", "idx"], cwd=corpus, check=True)
    (corpus / "index.md").write_text("half-written index\n")
    (corpus / "log.md").write_text("old log\nhalf entry\n")
    rc, _ = step(corpus, "index_restore")
    assert rc == 1
    assert (corpus / "index.md").read_text() == "old index\n"
    assert (corpus / "log.md").read_text() == "old log\n"
    assert _git_clean(corpus) == ""


def test_source_retry_of_a_held_file_can_pass_checks(corpus: Path, tmp_path: Path):
    _to_write(corpus, [], tmp_path / "r1")
    step(corpus, "checks")
    step(corpus, "checks")
    assert step(corpus, "hold", str(tmp_path / "r1")) == (0, "next")
    assert step(corpus, "select", str(tmp_path / "r2"), "0", ORCHARD) == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    step(corpus, "page_select")
    _good_summary(corpus)
    assert step(corpus, "checks") == (0, "pass"), _findings(corpus)


def test_bare_citation_with_unknown_id_fails():
    errs = ck.check_citations("p.md", FM + "claim [s99]\n", {1: ("a.md", "x")})
    assert any("s99" in e for e in errs)


def test_state_files_are_written_atomically(corpus: Path, tmp_path: Path, monkeypatch):
    from wiki_weaver import ledger, steps

    monkeypatch.chdir(corpus)
    replaced: list[str] = []
    real = os.replace

    def spy(src, dst):
        replaced.append(Path(dst).name)
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    steps.save_current({"filename": "x"})
    run = tmp_path / "b"
    run.mkdir()
    steps.record(
        run, ledger.make_row(Path("."), source="x.md", file_hash="h" * 64, status="failed")
    )
    assert {"current.json", ".processed.jsonl", "changes.jsonl"} <= set(replaced), replaced
    time.sleep(0)
