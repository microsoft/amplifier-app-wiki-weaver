"""The `rollover` step: after the brief, the pages it chose that are over their budget shed
their oldest dates into history/ volumes - committed at once, so a hold that reverts the
source's pages keeps the roll. Driven the way the graph drives it (the `tests/test_steps.py`
pattern), with the model steps' file outputs written by hand.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import pytest
from dotparse import edges, nodes
from test_steps import ORCHARD, cite, fake_brief, lists_loop, page, step

from wiki_weaver import cli, rollover, steps
from wiki_weaver.lib import wiki_ledger
from wiki_weaver.rollover import RolloverPlan, check_conservation

ROOT = Path(__file__).resolve().parent.parent
HUB = "orchard-rollout.md"
SAID = "the sensors are installed in the north block"
QUOTE = "The orchard sensor rollout ships on Friday"  # verbatim in the ORCHARD fixture
FILLER = "Nothing in the notes says who checked the install or when. "
DAYS = [(date(2031, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]


def entry(day: str, sid: int, size: int = 900) -> str:
    head = f'- **{day} (Wren Talbot, orchard chat):** progress. Wren: [s{sid}: "{SAID}"]. '
    return head + (FILLER * (size // len(FILLER) + 1))[: size - len(head)].rstrip() + "\n"


def seed(corpus: Path, days: list[str] = DAYS, tail: str = "") -> str:
    """An orchard hub with one cited entry per day, newest first - over the 48k budget
    with 60 days - and ledger rows for the sources it cites, whose text is not in the job
    (a Team Pulse-shaped job). Committed; returns the hub's text."""
    text = (
        "---\ntitle: Orchard rollout\ntype: topics\nsources:\n"
        + "".join(f"  - chat-{d}.md\n" for d in days)
        + f"last_updated: {days[-1]}\n---\n\n# Orchard rollout\n\n"
        + f'## Current state (as of {days[-1]})\n\nOn track [s{len(days)}: "{SAID}"].\n\n'
        + "## Updates\n\n"
        + "\n".join(entry(d, i) for i, d in reversed(list(enumerate(days, start=1))))
        + tail
    )
    (corpus / HUB).write_text(text)
    rows = [
        {"source": f"chat-{d}.md", "source_id": i, "hash": f"seed-{i}", "converged": True}
        for i, d in enumerate(days, start=1)
    ]
    wiki_ledger(corpus).write_text("".join(json.dumps(r) + "\n" for r in rows))
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "seed the orchard hub")
    return text


def git(c: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=c,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def begin(corpus: Path, tmp_path: Path, slugs: list[str]) -> Path:
    """select, assemble and the brief (by hand) for the first source, ORCHARD."""
    run = tmp_path / "run"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    fake_brief(corpus, slugs)
    return run


def current(corpus: Path) -> dict:
    return json.loads((corpus / ".wiki/work/current.json").read_text())


def mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_rollover_step_rolls_and_commits(corpus: Path, tmp_path: Path):
    original = seed(corpus)
    (corpus / HUB).chmod(0o640)
    begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    cur = current(corpus)
    vols = cur["rolled_over"]
    assert vols and cur["over_budget"] == [] and cur["refused"] == []
    hub = (corpus / HUB).read_text()
    written = [(v, (corpus / v).read_text()) for v in vols]
    check_conservation(original, RolloverPlan(hub, written, 0, 0, 0, False))
    # committed at once, the hub and its volumes alone; the journal owns them too
    assert (
        git(corpus, "log", "-1", "--format=%s").strip()
        == f"rollover: {HUB} \u2192 {', '.join(vols)}"
    )
    assert git(corpus, "show", "--name-only", "--format=", "HEAD").split() == sorted([HUB, *vols])
    assert git(corpus, "status", "--porcelain") == ""
    journal = json.loads((corpus / ".wiki/work/journal.json").read_text())
    assert {HUB, *vols} <= set(journal["paths"]) and journal["committed"] is False
    # the hub keeps its mode; volumes are 0644
    assert mode(corpus / HUB) == 0o640 and {mode(corpus / v) for v in vols} == {0o644}
    assert cur["stage"] == "brief"  # a later page_select failure still reads as the brief's


def test_small_pages_untouched(corpus: Path, tmp_path: Path):
    seed(corpus, DAYS[:5])
    head, hub = git(corpus, "rev-parse", "HEAD"), (corpus / HUB).read_bytes()
    begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    cur = current(corpus)
    assert cur["rolled_over"] == [] and cur["over_budget"] == [] and cur["refused"] == []
    assert (corpus / HUB).read_bytes() == hub and not (corpus / "history").exists()
    assert git(corpus, "rev-parse", "HEAD") == head


def test_only_the_briefs_usable_slugs_roll(corpus: Path, tmp_path: Path):
    original = seed(corpus)
    (corpus / "source-orchard-notes.md").write_text(original)  # big, but a source page
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "a source page")
    nine = [f"new-page-{i}" for i in range(8)] + ["orchard-rollout"]  # past MAX_SLUGS
    begin(corpus, tmp_path, ["source-orchard-notes", "INVALID SLUG", "../escape", *nine])
    assert step(corpus, "rollover") == (0, "ok")
    assert current(corpus)["rolled_over"] == [] and not (corpus / "history").exists()
    fake_brief(corpus, ["source-orchard-notes", "orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    vols = current(corpus)["rolled_over"]
    assert vols and all(v.startswith("history/orchard-rollout--") for v in vols)
    assert (corpus / "source-orchard-notes.md").read_text() == original


def test_refused_page_is_skipped_and_recorded(corpus: Path, tmp_path: Path):
    seed(corpus, tail="\n## History\n\nThe orchard project began as a pilot in 2030.\n")
    hub = (corpus / HUB).read_bytes()
    begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    cur = current(corpus)
    [refused] = cur["refused"]  # the reason travels to result.json and the ledger row
    assert refused["page"] == HUB and "## History" in refused["reason"]
    assert cur["rolled_over"] == [] and cur["over_budget"] == []
    assert (corpus / HUB).read_bytes() == hub and not (corpus / "history").exists()


def test_conservation_failure_stops(
    corpus: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # A planning defect (injected: the volume entries lose their quotes) must not be written.
    original = seed(corpus)
    head = git(corpus, "rev-parse", "HEAD")
    begin(corpus, tmp_path, ["orchard-rollout"])
    real = rollover._trimmed
    monkeypatch.setattr(rollover, "_trimmed", lambda t: real(t).split(" Wren:")[0] + "\n")
    monkeypatch.chdir(corpus)
    with pytest.raises(SystemExit) as stop:
        steps.main(["rollover"])
    assert stop.value.code == 1
    err = capsys.readouterr().err
    assert HUB in err and "left the hub and is in no volume" in err
    assert (corpus / HUB).read_text() == original and not (corpus / "history").exists()
    assert git(corpus, "rev-parse", "HEAD") == head


def test_hold_after_rollover_keeps_roll(corpus: Path, tmp_path: Path):
    seed(corpus)
    run = begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    rolled, vols = (corpus / HUB).read_text(), current(corpus)["rolled_over"]
    assert step(corpus, "page_select") == (0, "ok")
    (corpus / HUB).write_text("The writer replaced the page.\n")  # a write that is held
    assert step(corpus, "hold", str(run)) == (0, "next")
    assert (corpus / HUB).read_text() == rolled  # back to HEAD, which holds the roll
    assert all((corpus / v).is_file() for v in vols)
    assert git(corpus, "status", "--porcelain") == ""
    row = json.loads(wiki_ledger(corpus).read_text().splitlines()[-1])
    assert row["source"] == ORCHARD and row["status"] == "failed"
    assert row["rolled_over"] == vols and row["over_budget"] == [] and row["refused"] == []


def test_graph_brief_rollover_page_select():
    g = ROOT / "pipeline" / "ingest.dot"
    assert nodes(g)["rollover"] == {
        "shape": "parallelogram",
        "max_retries": "0",
        "tool_command": '"$PY" -m wiki_weaver.steps rollover',
        "tool_env": "py",
    }
    out = edges(g)
    assert [(t, a) for s, t, a in out if s == "brief"] == [
        ("rollover", {}),
        ("hold", {"condition": "outcome=fail", "label": "model step failed"}),
    ]
    assert [(t, a) for s, t, a in out if s == "rollover"] == [
        ("lists_prep", {"condition": "context.tool.last_line=ok && outcome=success"}),
        ("stop", {"condition": "outcome=fail"}),
    ]  # the list loop (tests/test_lists.py) ends at page_select
    assert "rollover" in steps.STEPS


def one_day_page(title: str) -> str:
    """Over its budget, but every entry on one date: nothing can move."""
    return (
        f"---\ntitle: {title}\ntype: topics\nsources:\n  - chat-{DAYS[0]}.md\n"
        f"last_updated: {DAYS[0]}\n---\n\n# {title}\n\n"
        f'## Current state (as of {DAYS[0]})\n\nBusy [s1: "{SAID}"].\n\n## Updates\n\n'
        + "\n".join(entry(DAYS[0], 1) for _ in range(60))
    )


def _engine(graph, corpus: Path, params: dict, logs_root, after_commit=None, **kw) -> int:
    """Stand-in for the dot-runner batch, in the graph's order: select, assemble, brief (by
    hand), rollover, the list loop (lists by hand), page_select, write (by hand: the
    source's summary page), checks, commit. ``after_commit``, if given, is called with the
    batch directory after each source commits."""
    run_dir = params["run_dir"]
    while step(corpus, "select", run_dir, params["cap"], params["only"])[1] == "source":
        assert step(corpus, "assemble") == (0, "ok")
        cur = current(corpus)
        fake_brief(corpus, ["orchard-rollout", "orchard-log"])
        assert step(corpus, "rollover") == (0, "ok")
        lists_loop(corpus)
        assert step(corpus, "page_select") == (0, "ok")
        summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
        q = cite(corpus, QUOTE) if cur["filename"] == ORCHARD else ""
        (corpus / summary).write_text(page(summary, [cur["filename"]], f"- Notes. {q}\n"))
        assert step(corpus, "checks") == (0, "pass")
        assert step(corpus, "commit", run_dir) == (0, "next")
        if after_commit is not None:
            after_commit(Path(run_dir))
    return 0


def test_ingest_rolls_an_oversized_selected_page(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    seed(corpus)
    (corpus / "orchard-log.md").write_text(one_day_page("Orchard log"))
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "a page over budget on one date")
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", _engine)
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(corpus)])
    assert a.func(a) == 0
    rows = [json.loads(ln) for ln in wiki_ledger(corpus).read_text().splitlines()][len(DAYS) :]
    assert rows[0]["source"] == ORCHARD and rows[0]["converged"]
    vols = rows[0]["rolled_over"]
    assert vols and rows[0]["refused"] == []
    assert len(rows) > 1 and all(r["rolled_over"] == [] for r in rows[1:])  # once is enough
    assert all(r["over_budget"] == ["orchard-log.md"] for r in rows)  # every time it is chosen
    assert all(r["kept_over_half"] == [] for r in rows)
    # the list loop read each new history file once, in the source that made it
    assert [r["lists_done"] for r in rows] == [len(vols)] + [0] * (len(rows) - 1)
    log = git(corpus, "log", "--format=%s").splitlines()
    assert log.index(f"ingest: {ORCHARD}") < log.index(f"rollover: {HUB} \u2192 {', '.join(vols)}")
    result = json.loads(max((corpus / ".wiki/runs").glob("ingest-*/result.json")).read_text())
    assert result["rollovers"] == len(vols)
    assert result["over_budget_pages"] == ["orchard-log.md"]
    assert "\n## History\n" in (corpus / HUB).read_text()


def test_recovery_removes_journaled_temp_files_and_nothing_commits_one(corpus: Path):
    # A roll killed just before its renames: the journal names the hub and the volume, and
    # each has an atomic-write temp file beside it.
    seed(corpus)
    vol = "history/orchard-rollout--2031-01-01--2031-01-20.md"
    (corpus / "history").mkdir()
    temps = [
        corpus / "history" / ".orchard-rollout--2031-01-01--2031-01-20.md.k3j2h1g0.tmp",
        corpus / f".{HUB}.a1b2c3d4.tmp",
    ]
    stray = corpus / ".notes.md.abcd1234.tmp"  # the same naming, but no journal names notes.md
    for t in [*temps, stray]:
        t.write_text("a write killed before its rename\n")
    (corpus / HUB).write_text("half rolled\n")
    work = corpus / ".wiki" / "work"
    work.mkdir(parents=True)
    journal = {"op": "source", "source": None, "dest": None, "paths": [HUB, vol]}
    (work / "journal.json").write_text(json.dumps(journal | {"committed": False}))
    assert step(corpus, "recover") == (0, "recovered")
    assert not any(t.exists() for t in temps)
    assert stray.exists()  # not this operation's: left as it is
    assert (corpus / HUB).read_text() == git(corpus, "show", f"HEAD:{HUB}")
    cli.commit_pending(corpus, "chore: snapshot before ingest")
    assert not [p for p in git(corpus, "ls-files").split() if p.endswith(".tmp")]


def test_result_json_reports_refused_pages(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    seed(corpus, tail="\n## History\n\nThe orchard project began as a pilot in 2030.\n")
    progress: list[dict] = []

    def snapshot(batch: Path) -> None:  # the run's result.json is one level up (steps.record)
        progress.append(json.loads((batch.parent / "result.json").read_text()))

    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", partial(_engine, after_commit=snapshot))
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(corpus)])
    assert a.func(a) == 0
    first = progress[0]  # refreshed when the first source committed, mid-run
    assert first["status"] == "in_progress"
    [refused] = first["refused_pages"]
    assert refused["page"] == HUB and refused["source"] == ORCHARD
    assert "## History" in refused["reason"]
    final = json.loads(max((corpus / ".wiki/runs").glob("ingest-*/result.json")).read_text())
    assert final["status"] == "final" and final["refused_pages"] == [refused]


def test_rollover_reports_kept_sections_over_half_the_budget(corpus: Path, tmp_path: Path):
    # The default lens keeps a Decision list on topics pages: the hub's never moves, and
    # over half the page's budget it is reported, not refused.
    kept = "".join(
        f'- {d} \u00b7 decided on {d} \u00b7 in force \u00b7 {FILLER * 8}[s1: "{SAID}"]\n'
        for d in DAYS
    )
    seed(corpus, tail="\n## Decision list\n\n" + kept)
    begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    [report] = current(corpus)["kept_over_half"]
    assert report["page"] == HUB and report["chars"] > report["limit"] == 24_000
    assert "## Decision list\n\n" + kept in (corpus / HUB).read_text()
    assert "decided on" not in "".join(p.read_text() for p in (corpus / "history").glob("*.md"))
