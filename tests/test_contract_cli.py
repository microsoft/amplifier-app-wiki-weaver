"""contracts/cli-surface.v1.md - one test per numbered clause, model-free.

Ingests here have an empty inbox, or point the provider at a closed local port
(ANTHROPIC_BASE_URL=http://127.0.0.1:9) so every model step fails fast with a connection
error and nothing reaches a model. Tests that drive the engine skip when dot-runner is absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from engine import ENGINE, needs_engine_and_key

from wiki_weaver import cli, ledger
from wiki_weaver import result as rs

FIX = Path(__file__).parent / "fixtures"
VERBS = ("doctor", "init", "ingest", "ask", "build-dashboard", "update")


def ww(*args: str, env: dict | None = None, cwd: Path | None = None, **kw):
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=cwd,
        **kw,
    )


def _env(**extra: str) -> dict:
    e = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    e.update(extra)
    return e


NO_MODEL = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:9"}


def _wiki(tmp_path: Path, sources: int = 0) -> Path:
    w = tmp_path / "w"
    w.mkdir()
    cli.scaffold(w)
    shutil.copy2(cli.PKG / "data" / "default_lens.md", w / "lens.md")
    for f in sorted(FIX.glob("*.md"))[:sources]:
        shutil.copy2(f, w / "_inbox" / f.name)
    return w


def _result(w: Path) -> dict:
    (p,) = (w / ".wiki/runs").glob("ingest-*/result.json")
    return json.loads(p.read_text())


# 1 ------------------------------------------------------------------------------------
def test_c1_one_command_with_the_verbs_and_version() -> None:
    r = ww("--help")
    assert r.returncode == 0
    for verb in VERBS:
        assert verb in r.stdout, verb
        assert ww(verb, "--help").returncode == 0, verb
    assert ww("--version").returncode == 0


# 2 ------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("verb", "flags"),
    [
        ("init", ["--plain"]),
        ("ingest", ["--wiki", "--max-cycles", "--source"]),
        ("ask", ["--wiki", "--json"]),
        ("build-dashboard", ["--out", "--group-by", "--group-link-template", "--theme"]),
    ],
)
def test_c2_flags_are_named_in_help(verb: str, flags: list[str]) -> None:
    out = ww(verb, "--help").stdout
    for f in flags:
        assert f in out, f"{verb} {f}"


def test_c2_every_listed_flag_parses() -> None:
    p = cli.build_parser()
    p.parse_args(["init", "w", "--plain"])
    a = p.parse_args(
        ["ingest", "--wiki", "w", "--max-cycles", "4", "--limit", "10", "--source", "f"]
    )
    assert (a.wiki, a.max_cycles, a.limit, a.source) == ("w", 4, 10, "f")
    assert p.parse_args(["ask", "q", "--wiki", "w", "--json"]).json_out
    p.parse_args(
        ["build-dashboard", "c", "--out", "o", "--group-by", "repos"]
        + ["--group-link-template", "https://x/{group}", "--theme", "t.css"]
    )


# 3 ------------------------------------------------------------------------------------
def test_c3_ask_json_prints_exactly_one_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    w = _wiki(tmp_path)
    (w / "orchard.md").write_text("x")

    def fake_graph(graph, corpus, params, logs_root, **kw):  # the real emit step, no model
        d = Path(params["ask_dir"])
        (d / "pages.json").write_text('["orchard.md"]')
        (d / "answer.json").write_text(
            '{"answer": "Friday.", "pages_used": ["orchard"], "refused": false}'
        )
        r = subprocess.run(
            [sys.executable, "-m", "wiki_weaver.steps", "ask_emit", str(d), params["fmt"]],
            cwd=corpus,
            check=False,
        )
        return r.returncode

    monkeypatch.setattr(cli, "run_graph", fake_graph)
    a = cli.build_parser().parse_args(["ask", "When?", "--wiki", str(w), "--json"])
    assert a.func(a) == 0
    out = capsys.readouterr().out
    obj = json.loads(out)  # one object and nothing else, or this raises
    assert set(obj) == {"answer", "pages_used", "refused"}
    assert isinstance(obj["answer"], str) and isinstance(obj["refused"], bool)
    assert all(isinstance(x, str) for x in obj["pages_used"])


# 4 ------------------------------------------------------------------------------------
def test_c4_version_line() -> None:
    r = ww("--version")
    assert r.returncode == 0
    assert re.fullmatch(r"wiki-weaver \d{4}\.\d{2}\.\d{2}-[0-9a-f]{7,40}\n", r.stdout)


# 5 ------------------------------------------------------------------------------------
def test_c5_init_on_an_existing_wiki_leaves_it_intact(tmp_path: Path) -> None:
    w = _wiki(tmp_path, sources=2)
    (w / "orchard.md").write_text("---\ntitle: Orchard\n---\n# Orchard\n")
    subprocess.run(["git", "add", "-A"], cwd=w, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"],
        cwd=w,
        check=True,
    )

    def snap() -> dict[str, bytes]:
        return {
            str(p.relative_to(w)): p.read_bytes()
            for p in w.rglob("*")
            if p.is_file() and ".git/" not in str(p.relative_to(w)) + "/"
        }

    before = snap()
    assert ww("init", str(w), "--plain").returncode == 0
    after = snap()
    assert {k: after.get(k) for k in before} == before  # every existing file byte-identical
    assert set(after) - set(before) == set()  # not even the run lock: it is removed on exit


# 6 ------------------------------------------------------------------------------------
@needs_engine_and_key
def test_c6_init_without_flags_non_interactive_is_plain(tmp_path: Path) -> None:
    w = tmp_path / "fresh"
    r = ww("init", str(w), stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    assert (w / "lens.md").read_text() == (cli.PKG / "data" / "default_lens.md").read_text()


# 7 ------------------------------------------------------------------------------------
def test_c7_five_path_helpers_import_without_a_key(tmp_path: Path) -> None:
    code = (
        "import sys; from pathlib import Path;"
        "from wiki_weaver.lib import wiki_inbox, wiki_sources, wiki_failed, wiki_ledger,"
        " wiki_dashboard;"
        "w = Path(sys.argv[1]);"
        "print([str(f(w).relative_to(w)) for f in"
        " (wiki_inbox, wiki_sources, wiki_failed, wiki_ledger, wiki_dashboard)])"
    )
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    r = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert eval(r.stdout) == [
        "_inbox",
        "_sources",
        ".wiki/failed",
        ".wiki/.processed.jsonl",
        ".wiki/dashboard",
    ]


# 8 ------------------------------------------------------------------------------------
def test_c8_one_ingest_one_run_directory_one_result(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    assert ww("ingest", "--wiki", str(w), env=_env()).returncode == 3
    runs = list((w / ".wiki/runs").glob("ingest-*"))
    assert len(runs) == 1 and [p.name for p in runs[0].iterdir()].count("result.json") == 1


# 9 + 12 (one engine run serves both) -----------------------------------------------------
@pytest.fixture(scope="module")
def limited_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, subprocess.CompletedProcess]:
    if ENGINE is None:
        pytest.skip("dot-runner not installed")
    w = _wiki(tmp_path_factory.mktemp("c9"), sources=2)
    r = ww(
        "ingest", "--wiki", str(w), "--max-cycles", "4", "--limit", "1",
        env=_env(**NO_MODEL), timeout=600,
    )  # fmt: skip
    return w, r


def test_c9_limit_caps_sources_and_max_cycles_is_recorded(limited_run) -> None:
    w, _ = limited_run
    data = _result(w)
    assert data["counts"]["total"] == 1  # one source attempted
    assert len(list((w / "_inbox").glob("*.md"))) == 1  # the other untouched
    assert data["max_cycles"] == 4 and data["limit"] == 1


def test_c12_result_json_says_what_ran_and_why_not(limited_run) -> None:
    w, r = limited_run
    data = _result(w)
    version = ww("--version").stdout.strip().removeprefix("wiki-weaver ")
    assert data["wiki_weaver_version"] == version
    for key in ("status", "verdict", "counts", "failed", "errored"):
        assert key in data
    (f,) = data["failed"]
    assert set(f) >= {"source", "reason", "failure_kind", "errors"}
    assert f["failure_kind"] == "model_connection_error"
    assert f["reason"] == "brief: model step failed \u2014 connection error"
    assert f["errors"] == [f["reason"]]  # the full list behind `reason`; here, one cause
    assert f["failure_kind"] in ledger.FAILURE_KINDS
    assert r.returncode == 5


def test_c12_errored_entries_have_a_kind(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    (w / "lens.md").unlink()
    assert ww("ingest", "--wiki", str(w), env=_env()).returncode == 1
    (e,) = _result(w)["errored"]
    assert e["kind"] == "preflight" and e["kind"] in rs.ERRORED_KINDS and e["reason"]


# 10 -----------------------------------------------------------------------------------
def test_c10_exit_codes(tmp_path: Path) -> None:
    assert ww("ingest", "--wiki", str(tmp_path / "missing"), env=_env()).returncode == 1
    assert ww("ingest", "--bogus").returncode == 2
    assert ww("ingest", "--wiki", str(tmp_path), "--limit", "0").returncode == 2
    assert ww("ingest", "--wiki", str(_wiki(tmp_path)), env=_env()).returncode == 3
    # 0, 4 and 5 by verdict (0 needs a converged source, i.e. a model; 5 also runs live in c9)
    assert rs.EXIT_FOR_VERDICT["converged"] == 0
    assert rs.EXIT_FOR_VERDICT["partial"] == 0
    assert rs.EXIT_FOR_VERDICT["blocked"] == 4
    assert rs.EXIT_FOR_VERDICT["failed"] == 5
    assert rs.EXIT_FOR_VERDICT["errored"] == 1
    assert rs.EXIT_FOR_VERDICT["empty"] == 3


def test_c10_exit_75_while_another_ingest_holds_the_wiki(tmp_path: Path) -> None:
    from wiki_weaver import pidlock

    w = _wiki(tmp_path)
    hold = (
        "import sys, time; from pathlib import Path; from wiki_weaver import pidlock;"
        "print(pidlock.acquire(Path(sys.argv[1])), flush=True); time.sleep(30)"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", hold, str(pidlock.lock_path(w))], stdout=subprocess.PIPE, text=True
    )
    try:
        assert holder.stdout and holder.stdout.readline().strip() == "True"
        assert ww("ingest", "--wiki", str(w), env=_env()).returncode == 75
    finally:
        holder.kill()
        holder.wait()


# 11 -----------------------------------------------------------------------------------
def test_c11_reads_and_writes_only_the_corpus(tmp_path: Path) -> None:
    w = _wiki(tmp_path)
    elsewhere, home = tmp_path / "cwd", tmp_path / "home"
    elsewhere.mkdir()
    home.mkdir()
    r = ww("ingest", "--wiki", str(w), env=_env(HOME=str(home)), cwd=elsewhere)
    assert r.returncode == 3
    assert not any(elsewhere.iterdir()) and not any(home.iterdir())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cwd", "home", "w"]
