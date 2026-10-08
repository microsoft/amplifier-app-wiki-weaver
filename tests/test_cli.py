import json
import subprocess
import sys
from pathlib import Path

from wiki_weaver import lib
from wiki_weaver.cli import build_parser, scaffold
from wiki_weaver.sources import source_meta

FIX = Path(__file__).parent / "fixtures"


def ww(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_v1_verbs_and_flags_parse():
    p = build_parser()
    p.parse_args(["doctor"])
    p.parse_args(["init", "w", "--plain"])
    p.parse_args(["init", "w", "--purpose", "x"])
    p.parse_args(["ingest", "--wiki", "w", "--max-cycles", "3", "--source", "f.md"])
    a = p.parse_args(["ask", "q", "--wiki", "w", "--json"])
    assert a.json_out
    p.parse_args(
        [
            "build-dashboard",
            "w",
            "--out",
            "o.html",
            "--group-by",
            "repos",
            "--group-link-template",
            "https://x/{group}",
            "--theme",
            "t.json",
        ]
    )
    p.parse_args(["feedback", "--wiki", "w", "text", "--why", "because", "--page", "p.md"])
    p.parse_args(["update", "--check"])


def test_version_format():
    r = ww("--version")
    assert r.returncode == 0
    import re

    assert re.match(r"^wiki-weaver \d{4}\.\d{2}\.\d{2}-[0-9a-f]{7,}$", r.stdout.strip()), r.stdout


def test_lib_helpers(tmp_path: Path):
    assert lib.wiki_inbox(tmp_path) == tmp_path / "_inbox"
    assert lib.wiki_sources(tmp_path) == tmp_path / "_sources"
    assert lib.wiki_failed(tmp_path) == tmp_path / ".wiki" / "failed"
    assert lib.wiki_ledger(tmp_path) == tmp_path / ".wiki" / ".processed.jsonl"
    assert lib.wiki_dashboard(tmp_path) == tmp_path / ".wiki" / "dashboard"


def test_scaffold_idempotent_and_feedback_dashboard(tmp_path: Path):
    scaffold(tmp_path)
    (tmp_path / "READING.md").write_text("mine")
    scaffold(tmp_path)
    assert (tmp_path / "READING.md").read_text() == "mine"
    assert (
        ww("feedback", "--wiki", str(tmp_path), "wrong date", "--why", "it was Tuesday").returncode
        == 0
    )
    row = json.loads((tmp_path / "feedback/log.jsonl").read_text().splitlines()[-1])
    assert row["why"] == "it was Tuesday" and "page" not in row
    (tmp_path / "a.md").write_text(
        "---\ntitle: A\ntype: people\nsources: []\nlast_updated: x\n---\n"
    )
    out = tmp_path / "d.html"
    assert ww("build-dashboard", str(tmp_path), "--out", str(out)).returncode == 0
    assert "people" in out.read_text() and "a.md" in out.read_text()


def test_source_meta_fallbacks():
    m = source_meta(FIX / "Kettle Planning__rec-2031-03-04-0930.md")
    assert (m["title"], m["date"], m["kind"]) == ("Kettle Planning", "2031-03-04", "transcript")
    m = source_meta(FIX / "Lantern Chat__chat__2031-03-05_to_2031-03-06.md")
    # a windowed export is dated by its window end (B-lean.2)
    assert (m["kind"], m["date"], m.get("date_start")) == ("chat", "2031-03-06", "2031-03-05")
    m = source_meta(FIX / "Quiet Room Notes 2031-03-09.md")
    assert (m["date"], m["kind"]) == ("2031-03-09", "document")
    m = source_meta(FIX / "2031-03-02 Orchard Sync.md")
    assert (m["title"], m["date"]) == ("Orchard Sync", "2031-03-02")


def test_reserved_slugs():
    for s in ("index", "log", "lens", "feedback", "_inbox", "_sources", "reading"):
        assert not lib.is_valid_slug(s)
    assert lib.is_valid_slug("orchard-rollout")
