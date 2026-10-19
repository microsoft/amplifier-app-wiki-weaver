"""contracts/corpus-package.v1.md - one test per numbered clause, model-free.

A whole `ingest` runs through the real CLI with the engine replaced by a stand-in that
drives the real glue steps and writes the two model steps' file outputs by hand (the
`tests/test_steps.py` pattern), so result.json, the ledger and the layout are V4's own.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from engine import needs_engine_and_key
from test_steps import ORCHARD, cite, fake_brief, page, step

from wiki_weaver import cli
from wiki_weaver.sources import source_meta

FIX = Path(__file__).parent / "fixtures"
QUOTE = "The orchard sensor rollout ships on Friday"
QUIET = "Quiet Room Notes 2031-03-09.md"  # no frontmatter
NOT_ROUND_TRIP = (".wiki/runs", ".wiki/snapshots", ".wiki/work", ".git")
LEDGER_KEYS = {"source", "hash", "status", "reason", "converged"}


def _engine(hold: bool = False):
    """Stand-in for `dot-runner run ingest.dot`: the real glue steps, model outputs by hand."""

    def run(graph, corpus, params, logs_root, **kw):
        run_dir = params["run_dir"]
        while step(corpus, "select", run_dir, params["cap"], params["only"])[1] == "source":
            step(corpus, "assemble")
            cur = json.loads((corpus / ".wiki/work/current.json").read_text())
            fake_brief(corpus, ["orchard-rollout"])
            step(corpus, "page_select")
            summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
            q = cite(corpus, QUOTE) if cur["filename"] == ORCHARD else ""
            body = (cite(corpus, "words that are not in it") if hold else q) + "\n"
            for name, first in ((summary, ""), ("orchard-rollout.md", "## Plan\n")):
                p = corpus / name
                if p.exists():  # integrate, never replace: the loss guard checks this
                    p.write_text(p.read_text().rstrip("\n") + "\n" + body)
                else:
                    p.write_text(page(name, [cur["filename"]], first + body))
            verdict = step(corpus, "checks")[1]
            if verdict == "rewrite":
                step(corpus, "write_guard")
                verdict = step(corpus, "checks")[1]
            step(corpus, "commit" if verdict == "pass" else "hold", run_dir)
        return 0

    return run


def _ingest(w: Path, monkeypatch: pytest.MonkeyPatch, hold: bool = False) -> int:
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", _engine(hold))
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(w)])
    return a.func(a)


@pytest.fixture
def one(corpus: Path) -> Path:
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != ORCHARD:
            f.unlink()
    return corpus


def _ledger(w: Path) -> list[dict]:
    return [json.loads(x) for x in (w / ".wiki/.processed.jsonl").read_text().splitlines() if x]


def _newest_result(w: Path) -> dict:
    return json.loads(max((w / ".wiki/runs").glob("ingest-*/result.json")).read_text())


def _pack(src: Path, dest: Path) -> None:
    """Tar with the clause-10 exclusions, untar elsewhere."""
    tar = dest.parent / "corpus.tar"

    def keep(ti: tarfile.TarInfo):
        rel = ti.name.split("/", 1)[1] if "/" in ti.name else ""
        if any(
            rel == x or rel.startswith(x + "/") or f"/{x}/" in f"/{rel}/" for x in NOT_ROUND_TRIP
        ):
            return None
        return ti

    with tarfile.open(tar, "w") as t:
        t.add(src, arcname="c", filter=keep)
    with tarfile.open(tar) as t:
        t.extractall(dest.parent / "x", filter="data")
    shutil.move(dest.parent / "x" / "c", dest)


# 1 + 10 -------------------------------------------------------------------------------
def test_c1_c10_state_travels_and_the_rest_does_not(
    one: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _ingest(one, monkeypatch) == 0
    (one / ".wiki/snapshots").mkdir(exist_ok=True)
    lines = len(_ledger(one))
    there = tmp_path / "elsewhere" / "c"
    there.parent.mkdir()
    _pack(one, there)
    for x in NOT_ROUND_TRIP:
        assert not (there / x).exists(), x
    for x in ("_sources", ".wiki/.processed.jsonl", ".wiki/failed", "lens.md", "feedback"):
        assert (there / x).exists(), x
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "init", str(there), "--plain"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert (there / ".git").is_dir()
    shutil.copy2(FIX / ORCHARD, there / "_inbox" / ORCHARD)  # the unchanged inbox, again
    assert _ingest(there, monkeypatch) == 3
    assert len(_ledger(there)) == lines


# 2 ------------------------------------------------------------------------------------
def test_c2_inbox_takes_markdown_without_frontmatter(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != QUIET:
            f.unlink()
    meta = source_meta(corpus / "_inbox" / QUIET)
    assert str(meta.get("date")) == "2031-03-09" and meta.get("title")
    _ingest(corpus, monkeypatch)
    assert not (corpus / "_inbox" / QUIET).exists()
    assert _ledger(corpus)[-1]["source"] == QUIET


# 3 ------------------------------------------------------------------------------------
def test_c3_sources_holds_the_ingested_bytes_and_keeps_prior_versions(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (FIX / ORCHARD).read_bytes()
    assert _ingest(one, monkeypatch) == 0
    assert (one / "_sources" / ORCHARD).read_bytes() == original
    assert not (one / "_inbox" / ORCHARD).exists()
    old_id = _ledger(one)[-1]["source_id"]
    edited = original.replace(b"Friday the seventh", b"Friday the seventh, confirmed")
    (one / "_inbox" / ORCHARD).write_bytes(edited)
    assert _ingest(one, monkeypatch) == 0
    assert (one / "_sources" / ORCHARD).read_bytes() == edited
    assert (one / f".wiki/source-versions/s{old_id}.md").read_bytes() == original


# 4 ------------------------------------------------------------------------------------
def test_c4_held_source_moves_to_failed_with_a_reason_and_is_retried(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _ingest(one, monkeypatch, hold=True) == 5
    assert (one / ".wiki/failed" / ORCHARD).exists()
    row = _ledger(one)[-1]
    assert row["status"] == "failed" and row["reason"]
    assert _newest_result(one)["counts"]["failed"] == 1
    shutil.move(one / ".wiki/failed" / ORCHARD, one / "_inbox" / ORCHARD)
    assert _ingest(one, monkeypatch) == 0
    assert _ledger(one)[-1]["converged"] is True


# 5 ------------------------------------------------------------------------------------
def test_c5_ledger_keys_and_content_keying(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ingest(one, monkeypatch) == 0
    rows = _ledger(one)
    for r in rows:
        assert LEDGER_KEYS <= set(r) and isinstance(r["converged"], bool)
    assert rows[-1]["converged"] is True
    first_hash = rows[-1]["hash"]
    (one / "_inbox" / ORCHARD).write_bytes((FIX / ORCHARD).read_bytes() + b"\nA late note.\n")
    _ingest(one, monkeypatch)
    assert _ledger(one)[-1]["hash"] != first_hash


# 6 ------------------------------------------------------------------------------------
def test_c6_one_aggregated_result_per_ingest(one: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _ingest(one, monkeypatch) == 0
    runs = list((one / ".wiki/runs").glob("ingest-*"))
    assert len(runs) == 1
    data = json.loads((runs[0] / "result.json").read_text())
    assert data["total"] == data["converged"] == 1
    assert data["counts"]["failed"] == data["counts"]["blocked"] == data["counts"]["errored"] == 0


# 7 ------------------------------------------------------------------------------------
def test_c7_lens_and_its_learning_live_in_the_corpus(tmp_path: Path) -> None:
    w = tmp_path / "w"
    w.mkdir()
    cli.scaffold(w)
    assert (w / "lens" / "corrections").is_dir()
    assert (w / "feedback" / "log.jsonl").is_file()
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "feedback", "--wiki", str(w),
         "kickoff moved", "--why", "the team said so"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    entry = json.loads((w / "feedback/log.jsonl").read_text().splitlines()[-1])
    assert entry["why"] == "the team said so"


# 8 ------------------------------------------------------------------------------------
def test_c8_pages_live_at_the_root_with_their_frontmatter(
    one: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _ingest(one, monkeypatch) == 0
    assert (one / "lens.md").is_file()
    pages = [
        p for p in one.glob("*.md") if p.name not in ("index.md", "log.md", "READING.md", "lens.md")
    ]
    assert any(p.name.startswith("source-") for p in pages)
    from wiki_weaver.sources import split_frontmatter

    for p in pages:
        fm, _ = split_frontmatter(p.read_text())
        assert fm and {"title", "type", "sources", "last_updated"} <= set(fm), p.name
    # index.md and log.md are written by the index model step; not exercised model-free.


# 9 ------------------------------------------------------------------------------------
@pytest.mark.parametrize("where", [".wiki/policy/schema.md", "policy/schema.md"])
def test_c9_upstream_policy_reaches_the_writer(one: Path, tmp_path: Path, where: str) -> None:
    p = one / where
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("## Page types\n- repos: one page per repository\n")
    step(one, "select", str(tmp_path / "run"), "0", "-")
    step(one, "assemble")
    ctx = (one / ".wiki/work/context.md").read_text()
    assert "one page per repository" in ctx


# layout: what init creates, and what it never overwrites ---------------------------------
@needs_engine_and_key
def test_layout_after_init(tmp_path: Path) -> None:
    w = tmp_path / "fresh"
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "init", str(w), "--plain"],
        capture_output=True, text=True, check=False, timeout=300,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    for d in ("_inbox", "_sources", ".wiki/failed", ".wiki/runs", "lens/corrections", ".git"):
        assert (w / d).is_dir(), d
    for f in ("lens.md", "READING.md", "feedback/log.jsonl", ".gitignore"):
        assert (w / f).is_file(), f
    ignores = (w / ".gitignore").read_text().splitlines()
    assert ".wiki/work/" in ignores and ".wiki/runs/" in ignores


def test_init_never_overwrites_an_existing_corpus(tmp_path: Path) -> None:
    w = tmp_path / "w"
    w.mkdir()
    mine = {
        "lens.md": "## Purpose\nmine\n",
        "READING.md": "my reading guide\n",
        "feedback/log.jsonl": '{"text": "t", "why": "w"}\n',
        "lens/corrections/c.md": "a correction\n",
        ".gitignore": "custom/\n",
    }
    for rel, text in mine.items():
        (w / rel).parent.mkdir(parents=True, exist_ok=True)
        (w / rel).write_text(text)
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "init", str(w), "--plain"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    for rel, text in mine.items():
        got = (w / rel).read_text()
        if rel == ".gitignore":  # appended to, never replaced
            assert got.startswith(text)
        else:
            assert got == text, rel


def test_init_leaves_no_gitkeep_anywhere(tmp_path: Path) -> None:
    """RepoWeaver enumerates .wiki/failed/ as held sources with no dotfile filter."""
    w = tmp_path / "w"
    w.mkdir()
    (w / "lens.md").write_text("## Purpose\nx\n")
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "init", str(w), "--plain"],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert not [str(p.relative_to(w)) for p in w.rglob(".gitkeep") if ".git/" not in str(p)]
    assert (w / ".wiki/failed").is_dir() and (w / "_sources").is_dir()


def test_scaffold_removes_gitkeep_an_older_init_left(tmp_path: Path) -> None:
    w = tmp_path / "w"
    for d in ("_sources", ".wiki/failed", "lens/corrections"):
        (w / d).mkdir(parents=True)
        (w / d / ".gitkeep").touch()
    (w / ".wiki/failed/held.md").write_text("a held source\n")
    cli.scaffold(w)
    assert not list(w.rglob(".gitkeep"))
    assert (w / ".wiki/failed/held.md").read_text() == "a held source\n"
