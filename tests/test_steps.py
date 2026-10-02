"""Deterministic glue, driven the way the graph drives it - minus the two box nodes,
whose file outputs are written by hand here."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"
ORCHARD = "2031-03-02 Orchard Sync.md"


def step(corpus: Path, *args: str) -> tuple[int, str]:
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", *args],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = r.stdout.strip().splitlines()
    return r.returncode, (lines[-1] if lines else "")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    from wiki_weaver.cli import scaffold

    c = tmp_path / "wiki"
    c.mkdir()
    scaffold(c)
    shutil.copy2(
        Path(__file__).parent.parent / "wiki_weaver" / "data" / "default_lens.md", c / "lens.md"
    )
    for f in FIX.glob("*.md"):
        shutil.copy2(f, c / "_inbox" / f.name)
    subprocess.run(["git", "add", "-A"], cwd=c, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "lens"],
        cwd=c,
        check=True,
    )
    return c


def fake_brief(c: Path, slugs: list[str]) -> None:
    (c / ".wiki/work/brief.md").write_text("brief")
    (c / ".wiki/work/slugs.txt").write_text("\n".join(slugs) + "\n")


def page(title: str, srcs: list[str], body: str) -> str:
    s = "".join(f'  - "{x}"\n' for x in srcs)
    return f"---\ntitle: {title}\ntype: topics\nsources:\n{s}last_updated: 2031-03-02\n---\n{body}"


def test_select_orders_by_content_date_and_assembles(corpus: Path, tmp_path: Path):
    run = tmp_path / "run"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    cur = json.loads((corpus / ".wiki/work/current.json").read_text())
    assert cur["filename"] == ORCHARD
    assert cur["summary_page"] == "source-2031-03-02-orchard-sync.md"
    assert step(corpus, "assemble") == (0, "ok")
    ctx = (corpus / ".wiki/work/context.md").read_text()
    assert "# Lens" in ctx and "Standing corrections" in ctx and "ships on Friday" in ctx


def test_happy_path_commit(corpus: Path, tmp_path: Path):
    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout", "INVALID SLUG", "index"])
    assert step(corpus, "page_select") == (0, "ok")
    sel = (corpus / ".wiki/work/selected.txt").read_text().split()
    assert sel == ["source-2031-03-02-orchard-sync.md", "orchard-rollout.md"]
    q = '[2031-03-02 Orchard Sync.md: "The orchard sensor rollout ships on Friday"]'
    (corpus / sel[0]).write_text(page("Orchard Sync", [ORCHARD], f"- Rollout Friday {q}\n"))
    (corpus / sel[1]).write_text(page("Orchard rollout", [ORCHARD], f"## Plan\n- Friday {q}\n"))
    assert step(corpus, "checks") == (0, "pass")
    assert step(corpus, "commit", str(run)) == (0, "next")
    assert (corpus / "_sources" / ORCHARD).exists() and not (corpus / "_inbox" / ORCHARD).exists()
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "converged" and row["converged"] is True and row["model_calls"] == 2
    assert set(row["pages_touched"]) == set(sel)
    # same content is never selected twice; the next source is the 03-04 one
    step(corpus, "select", str(run), "0", "-")
    assert json.loads((corpus / ".wiki/work/current.json").read_text())["filename"].startswith(
        "Kettle"
    )


def test_rewrite_then_hold_reverts(corpus: Path, tmp_path: Path):
    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    step(corpus, "page_select")
    bad = page("X", [ORCHARD], '[2031-03-02 Orchard Sync.md: "words that are not in it"]\n')
    (corpus / "source-2031-03-02-orchard-sync.md").write_text(bad)
    (corpus / "lens.md").write_text("tampered")
    assert step(corpus, "checks") == (0, "rewrite")
    findings = (corpus / ".wiki/work/findings.md").read_text()
    assert "outside the selected pages: lens.md" in findings and "not found verbatim" in findings
    assert step(corpus, "checks") == (0, "hold")
    assert step(corpus, "hold", str(run)) == (0, "next")
    assert (corpus / ".wiki/failed" / ORCHARD).exists()
    assert not (corpus / "source-2031-03-02-orchard-sync.md").exists()
    assert (corpus / "lens.md").read_text() != "tampered"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=corpus, capture_output=True, text=True, check=True
    )
    assert status.stdout == ""  # hold commits its bookkeeping; the next write starts clean
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "failed" and row["converged"] is False
    assert (
        row["reason"].startswith("checks failed twice") and row["failure_kind"] == "checks_failed"
    )
    assert row["model_calls"] == 3


def test_oversized_is_failed_and_batch_cap(corpus: Path, tmp_path: Path):
    run = tmp_path / "run"
    (corpus / "_inbox" / "2030-01-01 huge.md").write_text("word " * 90_000)
    assert step(corpus, "select", str(run), "1", "-") == (0, "drained")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "failed" and row["failure_kind"] == "oversized"
    assert row["reason"].startswith("oversized")


def test_source_retry_moves_held_back(corpus: Path, tmp_path: Path):
    shutil.move(corpus / "_inbox" / ORCHARD, corpus / ".wiki/failed" / ORCHARD)
    assert step(corpus, "select", str(tmp_path / "r"), "0", ORCHARD) == (0, "source")
    assert (corpus / "_inbox" / ORCHARD).exists()


def test_missing_slugs_fails_page_select(corpus: Path, tmp_path: Path):
    step(corpus, "select", str(tmp_path / "r"), "0", "-")
    step(corpus, "assemble")
    assert step(corpus, "page_select")[0] != 0


def test_init_steps(tmp_path: Path):
    c = tmp_path / "w"
    c.mkdir()
    assert step(c, "init_mode", "purpose") == (0, "purpose")
    assert step(c, "default_lens") == (0, "ok")
    assert step(c, "init_mode", "purpose") == (0, "exists")
    assert step(c, "lens_check") == (0, "ok")
    (c / "lens.md").write_text("## Purpose\nx\n")
    assert step(c, "lens_check") == (0, "bad")
    assert not (c / "lens.md").exists()


def test_ask_emit_shape(tmp_path: Path):
    d = tmp_path / "ask"
    d.mkdir()
    (tmp_path / "p.md").write_text("x")
    (d / "pages.json").write_text('["p.md"]')
    (d / "answer.json").write_text('{"answer": "A", "pages_used": ["p"], "refused": false}')
    assert step(tmp_path, "ask_emit", str(d), "json") == (0, "ok")
    assert json.loads((d / "out.txt").read_text()) == {
        "answer": "A",
        "pages_used": ["p.md"],
        "refused": False,
    }


V1_LEDGER_KEYS = {
    "source",
    "source_id",
    "hash",
    "status",
    "converged",
    "reason",
    "failure_kind",
    "failed_to",
    "timestamp",
}


def test_ledger_rows_use_v1_shape(corpus: Path, tmp_path: Path):
    """Every row a run can write carries V1's keys; done-ness is V1's `converged` rule."""
    run = tmp_path / "run"
    (corpus / "_inbox" / "2030-01-01 empty.md").write_text("")
    (corpus / "_inbox" / "2030-01-02 huge.md").write_text("word " * 90_000)
    # converged row
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    step(corpus, "page_select")
    q = '[2031-03-02 Orchard Sync.md: "The orchard sensor rollout ships on Friday"]'
    (corpus / "source-2031-03-02-orchard-sync.md").write_text(page("O", [ORCHARD], q + "\n"))
    assert step(corpus, "checks") == (0, "pass")
    step(corpus, "commit", str(run))
    # failed (checks) row
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    step(corpus, "page_select")
    step(corpus, "checks")
    step(corpus, "checks")
    step(corpus, "hold", str(run))
    rows = [json.loads(x) for x in (corpus / ".wiki/.processed.jsonl").read_text().splitlines()]
    assert {r["status"] for r in rows} == {"skipped", "failed", "converged"}
    for r in rows:
        assert V1_LEDGER_KEYS <= set(r), r
        assert isinstance(r["converged"], bool) and isinstance(r["source_id"], int)
        assert r["converged"] == (r["status"] == "converged")
        assert len(r["hash"]) == 64 and r["timestamp"]
        if r["status"] == "failed":
            assert r["failed_to"].endswith("/.wiki/failed/" + r["source"])
            assert Path(r["failed_to"]).exists()
    assert {row["source"] for row in rows if row.get("converged")} == {ORCHARD}
    from wiki_weaver.ledger import processed_sources

    assert processed_sources(corpus) == {ORCHARD}
    # the 0-byte file is skipped once, stays in the inbox, and is not picked again
    assert (corpus / "_inbox" / "2030-01-01 empty.md").exists()
    assert sum(r["source"] == "2030-01-01 empty.md" for r in rows) == 1
    # ids are stable per content hash and distinct across sources
    assert len({r["source_id"] for r in rows}) == len({r["hash"] for r in rows})
