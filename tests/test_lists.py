"""The never-move lists loop (design/plans/2026-11-10-short-lists-spec.md, sections 4-6).

After roll-over, each new - or still pending - history file of a page the brief chose, whose
type keeps sections, goes once to the `lists` model step: lists_prep hands it out, and
lists_apply checks what came back with the kept-section checks and commits it with the
file's state, or records why not and moves on. Driven the way the graph drives it (the
tests/test_steps.py pattern), with the model step's file output written by hand.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from dotparse import edges, nodes
from test_steps import FIX, ORCHARD, cite, fake_brief, fake_lists, lists_loop, page, step

from wiki_weaver import checks as ck
from wiki_weaver import cli, lists, rollover, steps
from wiki_weaver import result as rs
from wiki_weaver.lens import parse_page_types
from wiki_weaver.lib import wiki_ledger
from wiki_weaver.sources import split_frontmatter

ROOT = Path(__file__).resolve().parent.parent
GRAPH = ROOT / "pipeline" / "ingest.dot"
HUB = "resolve.md"
STATE = ".wiki/lists-state.json"
SAID = "we agreed the garden and orchard plans"
QUOTE = "The orchard sensor rollout ships on Friday"  # verbatim in the ORCHARD fixture
FILLER = "Nothing in the notes says who reviewed it or when. "
DAYS = [(date(2031, 1, 1) + timedelta(days=i)).isoformat() for i in range(60)]
CS = f"## Current state (as of {DAYS[-1]})"
LENS = """# Lens

## Purpose

The team's status memory.

## Page types

- **initiatives:** one page per initiative in flight.
  Sections: Owners \u00b7 Decisions
  Keep: Decision list \u2014 one line per decision with its current status, shaped `- YYYY-MM-DD \u00b7 <decision> \u00b7 <in force | changed YYYY-MM-DD> \u00b7 <short reason> [sN: "quote"]`. (covers: Decisions)
  Keep: Timeline \u2014 one line per turning point, shaped `- YYYY-MM-DD \u00b7 <turning point> [sN: "quote"]`.
  Keep: Question list \u2014 one line per question with where it stands.
  Keep current: Current owners \u2014 one line per area and who owns it now.
- **notes:** loose notes; nothing on them is kept.
"""
KEPT = parse_page_types(LENS)["initiative"].kept
KEEP_SENTENCE = (
    "Keep this page's never-move sections current, following their descriptions in the "
    "lens: add a line for a new item; update an existing line's status rather than adding a "
    "duplicate; never delete a line from a Keep list. They never move to history."
)
SEARCH_SENTENCE = (
    "Older entries are in the history files listed under ## History "
    "(`history/<page>--<first>--<last>.md`); they are not shown here. If the source refers to "
    "something earlier than the entries shown (an earlier decision, commitment, question or "
    "handoff), search those files for it and read only the matching entries, rather than "
    "reading files whole."
)


def said(sid: int) -> str:
    return f"{SAID} on day {sid}"


def entry(day: str, sid: int, size: int = 900) -> str:
    head = f'- **{day} (Abel, Resolve chat):** decision {sid}. Abel: [s{sid}: "{said(sid)}"]. '
    return head + (FILLER * (size // len(FILLER) + 1))[: size - len(head)].rstrip() + "\n"


def hub(title: str, kind: str = "initiative", kept: str = "") -> str:
    """A page over the 48k budget - one cited Decisions entry per day, newest first - with
    ``kept`` (kept sections) directly after its Current state."""
    return (
        f"---\ntitle: {title}\ntype: {kind}\nsources:\n"
        + "".join(f"  - chat-{d}.md\n" for d in DAYS)
        + f"last_updated: {DAYS[-1]}\n---\n\n# {title}\n\n"
        + f'{CS}\n\nOn track [s{len(DAYS)}: "{said(len(DAYS))}"].\n\n'
        + kept
        + "## Owners\n\n- Abel owns the contracts.\n\n"
        + "## Decisions\n\n"
        + "\n".join(entry(d, i) for i, d in reversed(list(enumerate(DAYS, start=1))))
    )


def git(c: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=c,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def seed(corpus: Path, kept: str = "") -> str:
    """The lens, an over-budget initiative hub, and ledger rows for the sources it cites,
    whose text is not in the job (Team Pulse's shape). Committed; returns the hub."""
    (corpus / "lens.md").write_text(LENS)
    text = hub("Resolve", kept=kept)
    (corpus / HUB).write_text(text)
    rows = [
        {"source": f"chat-{d}.md", "source_id": i, "hash": f"seed-{i}", "converged": True}
        for i, d in enumerate(DAYS, start=1)
    ]
    wiki_ledger(corpus).write_text("".join(json.dumps(r) + "\n" for r in rows))
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "seed the resolve hub")
    return text


def current(c: Path) -> dict:
    return json.loads((c / ".wiki/work/current.json").read_text())


def save(c: Path, cur: dict) -> None:
    (c / ".wiki/work/current.json").write_text(json.dumps(cur))


def next_selection(c: Path) -> None:
    """As a later source that picks the same page starts its list work: a new clock and
    nothing tried yet."""
    cur = current(c)
    cur.pop("lists", None)
    save(c, cur)


def state(c: Path) -> dict:
    p = c / STATE
    return json.loads(p.read_text()) if p.exists() else {}


def roll(
    corpus: Path, tmp_path: Path, slugs: tuple[str, ...] = ("resolve",), run: Path | None = None
):
    """select, assemble, the brief (by hand) and rollover for the first source, ORCHARD.
    Returns the run's batch directory and the volumes the roll made, oldest first."""
    run = run or tmp_path / "run"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    fake_brief(corpus, list(slugs))
    assert step(corpus, "rollover") == (0, "ok")
    return run, sorted(current(corpus)["rolled_over"])


def heads(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("## ")]


# ---------------------------------------------------------------- lists_prep


def test_prep_hands_out_the_oldest_pending_file_of_a_chosen_page_that_keeps_lists(
    corpus: Path, tmp_path: Path
):
    seed(corpus)
    (corpus / "team-pulse.md").write_text(hub("Team Pulse"))  # keeps lists; not chosen
    (corpus / "notes.md").write_text(hub("Notes", kind="notes"))  # chosen; keeps nothing
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "two more hubs")
    report = rollover.roll_wiki(corpus, dry_run=False)  # history files from before this source
    vols = {p["page"]: p["volumes"] for p in report["pages"]}
    assert set(vols) == {HUB, "team-pulse.md", "notes.md"} and len(vols[HUB]) == 2
    v1, v2 = vols[HUB]
    roll(corpus, tmp_path, ("notes", "resolve", "brand-new-page"))
    assert step(corpus, "lists_prep") == (0, "item")
    cur = current(corpus)
    assert (cur["lists"]["file"], cur["lists"]["page"]) == (v1, HUB)
    assert cur["model_calls"] == 2  # the brief, and the call lists_prep hands this file to
    text = (corpus / ".wiki/work/lists_input.md").read_text()
    assert f"Page: {HUB} (type: initiatives)" in text
    assert all(k.description in text for k in KEPT)  # the lens's own words, quoted
    assert (corpus / v1).read_text() in text  # the whole history file
    out = text[text.index("## Output") :]
    assert [out.index(f"## {k.name}\n") for k in KEPT] == sorted(
        out.index(f"## {k.name}\n") for k in KEPT
    )  # every kept section, in lens order
    assert "24,000" in out and f"\u2192 {v1}" in out
    assert "## Decisions" in out  # the section the Decision list covers
    assert not (corpus / ".wiki/work/lists.md").exists()
    fake_lists(corpus)
    assert step(corpus, "lists_apply") == (0, "next")
    assert lists_loop(corpus) == [v2]  # oldest first; then nothing of the other two pages
    assert set(state(corpus)) == {v1, v2}


def test_prep_quotes_the_kept_sections_as_they_stand(corpus: Path, tmp_path: Path):
    kept = f'## Timeline\n\n- 2031-02-28 \u00b7 shipped weekly [s59: "{said(59)}"]\n\n'
    seed(corpus, kept=kept)
    roll(corpus, tmp_path)
    assert step(corpus, "lists_prep") == (0, "item")
    text = (corpus / ".wiki/work/lists_input.md").read_text()
    assert kept.rstrip("\n") in text
    assert "not on the page yet: Decision list, Question list, Current owners" in text


def test_prep_has_nothing_to_hand_out_without_history(corpus: Path, tmp_path: Path):
    (corpus / "lens.md").write_text(LENS)
    git(corpus, "commit", "-qam", "lens")
    run = tmp_path / "run"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    fake_brief(corpus, ["resolve", "orchard-rollout"])  # neither exists yet
    assert step(corpus, "lists_prep") == (0, "done")
    assert not (corpus / STATE).exists()


# ---------------------------------------------------------------- lists_apply


def test_apply_puts_the_lists_after_current_state_in_lens_order_and_commits(
    corpus: Path, tmp_path: Path
):
    seed(corpus)
    _, (v1, v2) = roll(corpus, tmp_path)
    assert step(corpus, "lists_prep") == (0, "item")
    abel = f'- contracts: Abel (since 2031-01-01) [s1: "{said(1)}"]'
    began = f'- 2031-01-01 \u00b7 contracts work began [s1: "{said(1)}"]'
    fake_lists(corpus, {"Timeline": [began], "Current owners": [abel]})
    assert step(corpus, "lists_apply") == (0, "next")
    text = (corpus / HUB).read_text()
    # the Question list has no lines and was not on the page: not added
    assert heads(text)[:5] == [
        CS,
        "## Decision list",
        "## Timeline",
        "## Current owners",
        "## Owners",
    ]
    assert f"\u2192 {v1}" in text and began in text and abel in text
    fm = split_frontmatter(text)[0] or {}
    assert "chat-2031-01-01.md" in fm["sources"]  # s1, cited again, is listed again
    assert git(corpus, "log", "-1", "--format=%s").strip() == f"lists: {HUB} \u2190 {v1}"
    assert git(corpus, "show", "--name-only", "--format=", "HEAD").split() == [STATE, HUB]
    assert git(corpus, "status", "--porcelain") == ""
    assert state(corpus) == {v1: {"status": "done", "attempts": 1, "reason": "", "page": HUB}}
    assert current(corpus)["lists"]["done"] == [v1]
    # the next file: the lists are replaced where they stand; a Keep current line changes
    assert step(corpus, "lists_prep") == (0, "item")
    kip = abel.replace("Abel (since 2031-01-01)", "Kip (since 2031-02-01)")
    out = fake_lists(corpus).replace(abel, kip)
    (corpus / ".wiki/work/lists.md").write_text(out)
    assert step(corpus, "lists_apply") == (0, "next")
    text = (corpus / HUB).read_text()
    assert heads(text)[:5] == [
        CS,
        "## Decision list",
        "## Timeline",
        "## Current owners",
        "## Owners",
    ]
    assert kip in text and abel not in text
    assert [ln for ln in ck.kept_sections(text, ["Decision list"])["Decision list"] if ln] == [
        ln for ln in out.split("## Timeline")[0].splitlines()[1:] if ln
    ]
    assert state(corpus)[v2]["status"] == "done"
    assert git(corpus, "status", "--porcelain") == ""


def test_kept_lists_over_half_the_budget_are_reported_not_refused(corpus: Path, tmp_path: Path):
    seed(corpus)
    _, (v1, _) = roll(corpus, tmp_path)
    assert step(corpus, "lists_prep") == (0, "item")
    many = [
        f'- 2031-01-01 \u00b7 decision {i:03d} {"and more " * 20}\u00b7 in force [s1: "{said(1)}"]'
        for i in range(100)
    ]
    fake_lists(corpus, {"Decision list": many})
    assert step(corpus, "lists_apply") == (0, "next")
    assert state(corpus)[v1]["status"] == "done"
    [report] = current(corpus)["kept_over_half"]
    assert report["page"] == HUB and report["chars"] > report["limit"] == 24_000


def _decided(c: Path) -> list[str]:
    return [
        f'- 2031-02-28 \u00b7 ship weekly \u00b7 in force \u00b7 agreed [s59: "{said(59)}"]',
        f'- 2031-03-01 \u00b7 merge the contracts \u00b7 in force \u00b7 done [s60: "{said(60)}"]',
    ]


def _uncited(c: Path) -> None:
    fake_lists(c, {"Decision list": ["- 2031-01-02 \u00b7 an uncited decision \u00b7 in force"]})


def _unverifiable(c: Path) -> None:
    line = '- 2031-01-02 \u00b7 d \u00b7 in force \u00b7 x [s2: "words that are in no file at all"]'
    fake_lists(c, {"Decision list": [line]})


def _uncovered(c: Path) -> None:
    line = f'- 2031-01-05 \u00b7 a decision \u00b7 in force \u00b7 agreed [s60: "{said(60)}"]'
    body = "".join(f"{ln}\n" for ln in [*_decided(c), line])
    rest = "".join(f"\n## {k.name}\n" for k in KEPT[1:])
    (c / ".wiki/work/lists.md").write_text("## Decision list\n\n" + body + rest)


def _lossy(c: Path) -> None:
    # a line dropped with no new line naming a history file (one would excuse the loss)
    rest = "".join(f"\n## {k.name}\n" for k in KEPT[1:])
    (c / ".wiki/work/lists.md").write_text("## Decision list\n\n" + _decided(c)[1] + "\n" + rest)


def _pointed_and_dropped(c: Path) -> None:
    # a new line pointing at the history file, and an unrelated line silently gone
    out = fake_lists(c)
    (c / ".wiki/work/lists.md").write_text(out.replace(_decided(c)[0] + "\n", ""))


def _foreign(c: Path) -> None:
    fake_lists(c)
    with (c / ".wiki/work/lists.md").open("a") as f:
        f.write("\n## Notes\n\n- a note\n")


def _typo_pointer(c: Path) -> None:
    line = (
        f'- 2031-01-03 \u00b7 d \u00b7 in force \u00b7 x [s1: "{said(1)}"] (\u2192 history/typo.md)'
    )
    fake_lists(c, {"Decision list": [line]})


@pytest.mark.parametrize(
    ("write", "why"),
    [
        (_uncited, "is without a citation"),
        (_unverifiable, "not verifiable"),
        (_uncovered, "does not name the file"),
        (_lossy, "lost 1 line(s)"),
        (_pointed_and_dropped, "lost 1 line(s)"),
        (_foreign, "'## Notes' is not a section the lens keeps"),
        (_typo_pointer, "unknown history pointer: history/typo.md"),
        (None, "lists: no output"),
    ],
    ids=[
        "shape",
        "citation",
        "coverage",
        "loss",
        "pointer-no-excuse",
        "section",
        "typo-pointer",
        "no-output",
    ],
)
def test_a_failed_check_leaves_the_page_as_it_was_and_is_recorded(
    corpus: Path, tmp_path: Path, write, why: str
):
    seed(corpus, kept="## Decision list\n\n" + "".join(f"{ln}\n" for ln in _decided(corpus)) + "\n")
    _, (v1, _) = roll(corpus, tmp_path)
    rolled = (corpus / HUB).read_bytes()
    assert step(corpus, "lists_prep") == (0, "item")
    if write is not None:
        write(corpus)
    assert step(corpus, "lists_apply") == (0, "next")
    assert (corpus / HUB).read_bytes() == rolled
    got = state(corpus)[v1]
    assert got["status"] == "failed" and got["attempts"] == 1 and got["page"] == HUB
    assert why in got["reason"], got["reason"]
    assert current(corpus)["lists"]["failed"] == [{"file": v1, "reason": got["reason"]}]
    subject = git(corpus, "log", "-1", "--format=%s").strip()
    assert subject == f"lists: {HUB} \u2190 {v1} failed (attempt 1)"
    assert git(corpus, "show", "--name-only", "--format=", "HEAD").split() == [STATE]
    assert git(corpus, "status", "--porcelain") == ""


@pytest.mark.parametrize("which", [0, 1], ids=["the-file-holding-them", "another-file"])
def test_the_lists_step_may_condense_lines_into_the_file_that_holds_their_sources(
    corpus: Path, tmp_path: Path, which: int
):
    early = [  # s1 and s2 cite entries the roll moves into the first history file
        f'- 2031-01-01 \u00b7 decision 1 \u00b7 settled \u00b7 done [s1: "{said(1)}"]',
        f'- 2031-01-02 \u00b7 decision 2 \u00b7 settled \u00b7 done [s2: "{said(2)}"]',
    ]
    seed(corpus, kept="## Decision list\n\n" + "".join(f"{ln}\n" for ln in early) + "\n")
    _, vols = roll(corpus, tmp_path)
    assert step(corpus, "lists_prep") == (0, "item")
    v1 = current(corpus)["lists"]["file"]
    line = f'- 2031-01-01 to 2031-01-02 \u00b7 2 settled decisions \u2192 {vols[which]} [s1: "{said(1)}"]'
    rest = "".join(f"\n## {k.name}\n" for k in KEPT[1:])
    (corpus / ".wiki/work/lists.md").write_text(f"## Decision list\n\n{line}\n{rest}")
    assert step(corpus, "lists_apply") == (0, "next")
    got = state(corpus)[v1]
    text = (corpus / HUB).read_text()
    if which == 0:
        assert got["status"] == "done" and line in text
        assert not any(ln in text for ln in early)
    else:
        assert got["status"] == "failed" and "lost 2 line(s)" in got["reason"]
        assert all(ln in text for ln in early)


def test_a_failed_model_step_is_recorded_and_the_next_file_still_runs(corpus: Path, tmp_path: Path):
    seed(corpus)
    run, (v1, v2) = roll(corpus, tmp_path)
    rolled = (corpus / HUB).read_bytes()
    assert step(corpus, "lists_prep") == (0, "item")
    fake_lists(corpus)  # an output that would pass, but the engine says the step failed
    status = run / "engine" / "lists" / "status.json"
    status.parent.mkdir(parents=True)
    status.write_text((FIX / "node-status" / "timeout.json").read_text())
    assert step(corpus, "lists_apply", str(run)) == (0, "next")
    assert (corpus / HUB).read_bytes() == rolled
    got = state(corpus)[v1]
    assert (got["status"], got["attempts"]) == ("failed", 1)
    assert got["reason"] == "lists: model step failed \u2014 timeout after 900s"
    status.write_text(json.dumps({"node_id": "lists", "status": "success"}))
    assert lists_loop(corpus) == [v2]  # the failed file waits for a later selection
    assert state(corpus)[v2]["status"] == "done"


def test_a_lists_step_that_edits_files_itself_is_undone_and_recorded(corpus: Path, tmp_path: Path):
    seed(corpus)
    _, (v1, _) = roll(corpus, tmp_path)
    rolled = (corpus / HUB).read_text()
    assert step(corpus, "lists_prep") == (0, "item")
    fake_lists(corpus)
    (corpus / HUB).write_text(rolled + "\n## Decision list\n\n- edited on the page itself\n")
    (corpus / "stray.md").write_text("a page the step made\n")
    assert step(corpus, "lists_apply") == (0, "next")
    assert (corpus / HUB).read_text() == rolled and not (corpus / "stray.md").exists()
    got = state(corpus)[v1]
    assert got["status"] == "failed" and HUB in got["reason"] and "stray.md" in got["reason"]
    assert git(corpus, "status", "--porcelain") == ""


def test_a_failed_file_is_retried_on_later_selections_up_to_three_attempts(
    corpus: Path, tmp_path: Path
):
    seed(corpus)
    _, (v1, v2) = roll(corpus, tmp_path)
    (corpus / STATE).write_text(
        json.dumps(
            {
                v1: {"status": "failed", "attempts": 2, "reason": "earlier", "page": HUB},
                v2: {"status": "failed", "attempts": 3, "reason": "earlier", "page": HUB},
            }
        )
    )
    git(corpus, "add", STATE)
    git(corpus, "commit", "-qm", "two earlier failures")
    assert lists.pending_count(corpus) == 1
    assert lists_loop(corpus, write=None) == [v1]  # its third attempt; v2 had all three
    assert (state(corpus)[v1]["status"], state(corpus)[v1]["attempts"]) == ("failed", 3)
    next_selection(corpus)
    assert lists_loop(corpus) == []  # left for a later pass, and reported
    assert lists.pending_count(corpus) == 0


def test_the_valve_defers_what_is_left_and_a_later_selection_takes_it(corpus: Path, tmp_path: Path):
    seed(corpus)
    _, (v1, v2) = roll(corpus, tmp_path)
    assert step(corpus, "lists_prep", env={"WIKI_WEAVER_LISTS_MAX_SECONDS": "0"}) == (0, "done")
    assert {v: (e["status"], e["attempts"]) for v, e in state(corpus).items()} == {
        v1: ("deferred", 0),
        v2: ("deferred", 0),
    }
    assert current(corpus)["lists"]["deferred"] == [v1, v2]
    assert git(corpus, "log", "-1", "--format=%s").strip() == "lists: deferred 2 history file(s)"
    assert git(corpus, "status", "--porcelain") == ""
    assert lists.pending_count(corpus) == 2  # deferred files are still pending
    next_selection(corpus)  # its own clock: from this source's first lists_prep
    assert step(corpus, "lists_prep") == (0, "item")
    fake_lists(corpus)
    assert step(corpus, "lists_apply") == (0, "next")
    cur = current(corpus)
    cur["lists"]["t0"] -= 1801  # half an hour of list work in this source
    save(corpus, cur)
    assert step(corpus, "lists_prep") == (0, "done")
    assert state(corpus)[v1]["status"] == "done" and state(corpus)[v2]["status"] == "deferred"
    assert current(corpus)["lists"]["deferred"] == [v2]


def test_list_work_stops_at_the_jobs_file_budget_and_defers_the_rest(corpus: Path, tmp_path: Path):
    assert steps.LISTS_MAX_FILES_PER_JOB == 150
    seed(corpus)
    job = tmp_path / "ingest-x"
    run, (v1, v2) = roll(corpus, tmp_path, run=job / "batch-02")

    def prep(budget: int) -> tuple[int, str]:
        return step(
            corpus, "lists_prep", str(run), env={"WIKI_WEAVER_LISTS_MAX_FILES_PER_JOB": str(budget)}
        )

    assert prep(1) == (0, "item")
    fake_lists(corpus)
    assert step(corpus, "lists_apply", str(run)) == (0, "next")
    assert prep(1) == (0, "done")  # one history file read in this job: the rest wait
    assert state(corpus)[v1]["status"] == "done"
    assert (state(corpus)[v2]["status"], state(corpus)[v2]["attempts"]) == ("deferred", 0)
    assert "1 history file(s) read in this job" in state(corpus)[v2]["reason"]
    assert current(corpus)["lists"]["deferred"] == [v2]
    assert git(corpus, "status", "--porcelain") == ""
    # a later source of the same job counts what earlier ones read: their ledger rows, in
    # this batch and in the job's other batches
    rows = {"batch-01": {"lists_done": 1, "lists_failed": 0}, "batch-02": {"lists_failed": 1}}
    for batch, counts in rows.items():
        (job / batch).mkdir(parents=True, exist_ok=True)
        row = {"source": f"{batch}.md", "status": "converged", **counts}
        (job / batch / "changes.jsonl").write_text(json.dumps(row) + "\n")
    next_selection(corpus)
    assert prep(2) == (0, "done") and state(corpus)[v2]["status"] == "deferred"
    next_selection(corpus)
    assert prep(3) == (0, "item") and current(corpus)["lists"]["file"] == v2


def _boom(*args, **kwargs):
    raise RuntimeError("the run was killed here")


@pytest.mark.parametrize(
    "where", ["after the page, before the state", "before the commit", "after the commit"]
)
def test_a_run_killed_inside_lists_apply_leaves_page_and_state_both_old_or_both_new(
    corpus: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
):
    seed(corpus)
    _, (v1, _) = roll(corpus, tmp_path)
    assert step(corpus, "lists_prep") == (0, "item")
    fake_lists(corpus)
    head, rolled = git(corpus, "rev-parse", "HEAD"), (corpus / HUB).read_text()
    module, name = {
        "after the page, before the state": (lists, "write_state"),
        "before the commit": (steps, "commit_paths"),
        "after the commit": (steps, "save_current"),
    }[where]
    with monkeypatch.context() as m:
        m.setattr(module, name, _boom)
        m.chdir(corpus)
        with pytest.raises(RuntimeError, match="killed"):
            steps.step_lists_apply("")
    if where != "after the commit":
        assert (corpus / HUB).read_text() != rolled  # the page was written, not committed
    assert step(corpus, "recover") == (0, "recovered")
    if where == "after the commit":
        assert git(corpus, "rev-parse", "HEAD") != head
        assert f"\u2192 {v1}" in (corpus / HUB).read_text()
        assert state(corpus)[v1]["status"] == "done"
    else:
        assert git(corpus, "rev-parse", "HEAD") == head
        assert (corpus / HUB).read_text() == rolled and state(corpus) == {}
    assert git(corpus, "status", "--porcelain") == ""


# ---------------------------------------------------------------- the pieces


def test_the_output_must_hold_exactly_the_kept_sections():
    good = "## Decision list\n\n- d  \n\n## Timeline\n\n## Question list\n## Current owners\n- o\n"
    sections, problems = lists.parse_output(good, KEPT)
    assert problems == []
    assert sections == {
        "Decision list": ["- d"],
        "Timeline": [],
        "Question list": [],
        "Current owners": ["- o"],
    }
    for bad, why in [
        ("Here are the lists.\n\n" + good, "text outside the kept sections"),
        ("# Lists\n\n" + good, "'# Lists' is not a section the lens keeps"),
        (good + "## Notes\n\n- n\n", "'## Notes' is not a section the lens keeps"),
        (good + "## timeline\n\n- t\n", "'## timeline' appears twice"),
        (good.replace("## Question list\n", ""), "missing '## Question list'"),
    ]:
        problems = lists.parse_output(bad, KEPT)[1]
        assert any(why in p for p in problems), (why, problems)


FM = "---\ntitle: R\ntype: initiative\nsources:\n  - a.md\nlast_updated: 2031-03-01\n---\n\n"


def test_with_sections_inserts_missing_lists_in_lens_order_and_replaces_in_place():
    text = (
        FM
        + "# R\n\n"
        + f"{CS}\n\nOn track.\n## Owners\n\n- Abel.\n\n## Timeline\n\n- old\n\n"
        + "## History\n\n- [2031-01-01 to 2031-01-27 \u00b7 27 entries](history/r--x.md)\n"
    )
    new = {
        "Decision list": ["- d"],
        "Timeline": ["- ta", "- tb"],
        "Question list": [],
        "Current owners": ["- o"],
    }
    out = lists.with_sections(text, new, KEPT)
    assert heads(out) == [
        CS,
        "## Decision list",
        "## Owners",
        "## Timeline",
        "## Current owners",
        "## History",
    ]
    # every byte outside the kept sections stays: no blank line is added before them
    assert "On track.\n## Decision list\n\n- d\n\n## Owners" in out
    assert "## Timeline\n\n- ta\n- tb\n\n## Current owners\n\n- o\n\n## History" in out
    assert out.startswith(FM + "# R\n\n") and out.endswith("(history/r--x.md)\n")


def test_with_sections_without_current_state_goes_after_the_intro():
    text = FM + "# Decisions\n\nWhat was decided.\n\n## 2031-01-05 (chat)\n\n- decided\n"
    out = lists.with_sections(text, {"Decision list": ["- d"]}, KEPT)
    assert out == (
        FM + "# Decisions\n\nWhat was decided.\n\n## Decision list\n\n- d\n\n"
        "## 2031-01-05 (chat)\n\n- decided\n"
    )
    bare = lists.with_sections(FM + "# Decisions\n", {"Decision list": ["- d"]}, KEPT)
    assert bare == FM + "# Decisions\n## Decision list\n\n- d\n"


def test_with_sections_merges_a_list_the_page_holds_twice():
    text = FM + f"{CS}\n\nx\n\n## Decision list\n\n- a\n\n## Owners\n\n## Decision list\n\n- b\n"
    out = lists.with_sections(text, {"Decision list": ["- a", "- b"]}, KEPT)
    assert out == FM + f"{CS}\n\nx\n\n## Decision list\n\n- a\n- b\n\n## Owners\n\n"


VOL = "history/resolve--2031-01-01--2031-01-27.md"
OUTPUT = (
    f'## Decision list\n\n- 2031-01-05 \u00b7 decided early \u00b7 in force \u00b7 agreed [s1: "{said(1)}"] '
    f"(\u2192 {VOL})\n\n## Timeline\n\n## Question list\n\n"
    f'## Current owners\n\n- contracts: Abel (since 2031-01-05) [s1: "{said(1)}"]\n'
)


def small_hub(decisions: str, state: str = "On track.\n") -> str:
    """A hub with one history file, its Current state ``state`` and its Decisions."""
    return (
        "---\ntitle: Resolve\ntype: initiative\nsources:\n  - chat-a.md\n"
        "last_updated: 2031-03-01\n---\n\n# Resolve\n\n"
        f"{CS}\n\n{state}\n## Owners\n\n- Abel owns the contracts.\n\n## Decisions\n\n{decisions}\n"
        f"## History\n\n- [2031-01-01 to 2031-01-27 \u00b7 1 entries]({VOL})\n"
    )


def apply_to(tmp_path: Path, hub_text: str, output: str = OUTPUT) -> tuple[str, list[str]]:
    """`lists.apply` on a wiki holding ``hub_text`` and its one history file."""
    w = tmp_path / "w"
    (w / "history").mkdir(parents=True)
    (w / "lens.md").write_text(LENS)
    volume = f'## Decisions\n\n- **2031-01-05:** decided early [s1: "{said(1)}"]\n'
    (w / VOL).write_text("---\ntitle: Resolve history\ntype: history\n---\n\n" + volume)
    (w / "history" / "team-pulse--2031-01-01--2031-01-27.md").write_text("another hub's\n")
    (w / HUB).write_text(hub_text)
    return lists.apply(w, HUB, VOL, output, parse_page_types(LENS), {}, {1: "chat-1.md"})


TEMPLATE = "```markdown\n## Current owners\n- area: person\n```\n"


def test_a_heading_inside_a_code_fence_is_not_a_kept_section_to_replace(tmp_path: Path):
    decisions = (
        f"- **2031-02-01:** adopted this template:\n\n{TEMPLATE}\n"
        "- **2031-01-31:** another decision, after the fence.\n"
    )
    after, problems = apply_to(tmp_path, small_hub(decisions))
    assert problems == []
    assert decisions in after  # the fence, closed, and the decision after it
    owners = ck.kept_sections(after, ["Current owners"])["Current owners"]
    assert [ln for ln in owners if ln] == [
        '- contracts: Abel (since 2031-01-05) [s1: "' + said(1) + '"]'
    ]
    assert after.index("## Current owners\n\n- contracts") < after.index("## Owners")


def test_lists_go_after_a_current_state_holding_a_fenced_heading(tmp_path: Path):
    state = "On track; the template:\n\n```markdown\n## Owners\n- area: person\n```\n"
    after, problems = apply_to(tmp_path, small_hub("- **2031-01-31:** a decision.\n", state))
    assert problems == []
    assert state + "\n## Decision list\n\n- 2031-01-05" in after  # after the fence closes


def test_a_page_whose_code_fence_never_closes_gets_no_lists(tmp_path: Path):
    hub = small_hub(f"- **2031-02-01:** a template:\n\n{TEMPLATE[:-4]}")  # no closing fence
    after, problems = apply_to(tmp_path, hub)
    assert after == hub and any("never closes" in p for p in problems)


def test_lists_that_would_change_the_rest_of_the_page_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    real = lists.with_sections

    def careless(text: str, sections: dict, kept: list) -> str:  # a defect, simulated
        return real(text, sections, kept).replace("- Abel owns the contracts.\n", "")

    monkeypatch.setattr(lists, "with_sections", careless)
    hub = small_hub("- **2031-01-31:** a decision.\n")
    after, problems = apply_to(tmp_path, hub)
    assert after == hub
    assert problems == [f"{HUB}: the lists would change the page outside its kept sections"]


@pytest.mark.parametrize(
    "target", ["history/typo.md", "history/team-pulse--2031-01-01--2031-01-27.md"]
)
def test_a_new_history_pointer_must_be_one_of_the_pages_history_files(tmp_path: Path, target: str):
    line = (
        f'- 2031-01-06 \u00b7 another \u00b7 in force \u00b7 x [s1: "{said(1)}"] ([see]({target}))'
    )
    out = OUTPUT.replace("\n\n## Timeline", f"\n{line}\n\n## Timeline")
    _, problems = apply_to(tmp_path, small_hub("- **2031-01-31:** a decision.\n"), out)
    assert problems == [f"{HUB}: unknown history pointer: {target}"]


def test_a_fenced_heading_in_the_output_is_no_section():
    text = "## Decision list\n\n- d\n```\n## Timeline\n```\n\n## Timeline\n\n## Question list\n"
    sections, problems = lists.parse_output(text + "\n## Current owners\n", KEPT)
    assert problems == []
    assert sections["Decision list"] == ["- d", "```", "## Timeline", "```"]  # shape fails it
    assert sections["Timeline"] == []


def test_a_list_goes_after_a_last_line_that_has_no_newline(tmp_path: Path):
    bare = lists.with_sections(FM + "# Decisions", {"Decision list": ["- d"]}, KEPT)
    assert bare == FM + "# Decisions\n## Decision list\n\n- d\n"
    hub = small_hub("").split("## Owners")[0].rstrip("\n")  # ends at Current state, unended
    assert hub.endswith("On track.")
    plain = OUTPUT.replace(f" (\u2192 {VOL})", "")  # cites the moved entry instead
    after, problems = apply_to(tmp_path, hub, plain)
    assert problems == []
    assert "On track.\n## Decision list\n\n- 2031-01-05 \u00b7 decided early" in after
    assert after.endswith(f'(since 2031-01-05) [s1: "{said(1)}"]\n')


def test_pending_counts_the_history_of_pages_that_keep_lists(corpus: Path):
    seed(corpus)
    (corpus / "notes.md").write_text(hub("Notes", kind="notes"))
    git(corpus, "add", "-A")
    git(corpus, "commit", "-qm", "a hub that keeps nothing")
    report = rollover.roll_wiki(corpus, dry_run=False)
    v1, v2 = {p["page"]: p["volumes"] for p in report["pages"]}[HUB]
    assert lists.pending_count(corpus) == 2  # notes.md keeps nothing: never pending
    for got, want in [
        ({v1: {"status": "done", "attempts": 1}}, 1),
        ({v1: {"status": "deferred", "attempts": 0}}, 2),
        ({v1: {"status": "failed", "attempts": 2}}, 2),
        ({v1: {"status": "failed", "attempts": 3}, v2: {"status": "done", "attempts": 1}}, 0),
    ]:
        lists.write_state(corpus, got)
        assert lists.pending_count(corpus) == want, got


# ---------------------------------------------------------------- reporting


def test_result_reports_list_work_from_the_rows(tmp_path: Path):
    missing = {"file": "history/r--2031-01-01--2031-01-27.md", "reason": "lists: no output"}
    rows = [
        {
            "source": "a.md",
            "status": "converged",
            "lists_done": 2,
            "lists_failed": 1,
            "lists_deferred": 0,
            "lists_failures": [missing],
            "kept_over_half": [{"page": "r.md", "chars": 25_000, "limit": 24_000}],
        },
        {
            "source": "b.md",
            "status": "failed",
            "lists_done": 1,
            "lists_failed": 1,
            "lists_deferred": 3,
            "lists_failures": [missing],
            "kept_over_half": [
                {"page": "r.md", "chars": 25_100, "limit": 24_000},
                {"page": "q.md", "chars": 30_000, "limit": 24_000},
            ],
        },
        {"source": "c.md", "status": "converged"},  # a row from before list work
    ]
    out = rs.build(run_id="r", status="final", rows=rows, errored=[])
    assert out["lists_done"] == 3
    assert out["lists_failed"] == [missing]  # once per file and reason
    assert out["kept_over_half_pages"] == ["q.md", "r.md"]
    path = tmp_path / "result.json"
    rs.write(path, rs.build(run_id="r", status="in_progress", rows=[], errored=[]))
    live = rs.refresh(path, rows[:1])  # after each source, not each batch
    assert live is not None and (live["lists_done"], live["lists_failed"]) == (2, [missing])
    assert live["kept_over_half_pages"] == ["r.md"]


# ---------------------------------------------------------------- the graph


def test_graph_runs_the_list_loop_between_rollover_and_page_select():
    n, out = nodes(GRAPH), edges(GRAPH)
    assert n["lists"]["shape"] == "box"
    assert {k: n["lists"][k] for k in ("class", "timeout", "fidelity")} == {
        k: n["brief"][k] for k in ("class", "timeout", "fidelity")
    }
    for node, env in (("lists_prep", "py,run_dir"), ("lists_apply", "py,run_dir")):
        assert n[node] == {
            "shape": "parallelogram",
            "max_retries": "0",
            "tool_command": f'"$PY" -m wiki_weaver.steps {node}',
            "tool_env": env,
        }

    def leaving(node: str) -> list[tuple[str, dict]]:
        return [(t, a) for s, t, a in out if s == node]

    ok = "context.tool.last_line={} && outcome=success"
    assert leaving("rollover") == [
        ("lists_prep", {"condition": ok.format("ok")}),
        ("stop", {"condition": "outcome=fail"}),
    ]
    assert leaving("lists_prep") == [
        ("lists", {"condition": ok.format("item")}),
        ("page_select", {"condition": ok.format("done")}),
        ("stop", {"condition": "outcome=fail"}),
    ]
    assert leaving("lists") == [  # a failed model step is lists_apply's to record
        ("lists_apply", {}),
        ("lists_apply", {"condition": "outcome=fail", "label": "model step failed"}),
    ]
    assert leaving("lists_apply") == [
        ("lists_prep", {"condition": ok.format("next"), "label": "next history file"}),
        ("stop", {"condition": "outcome=fail"}),
    ]
    assert {"lists_prep", "lists_apply"} <= set(steps.STEPS)
    assert steps.STEP_ENV["lists_prep"] == steps.STEP_ENV["lists_apply"] == ("RUN_DIR",)


def test_lists_prompt_asks_for_the_whole_updated_lists():
    prompt = nodes(GRAPH)["lists"]["prompt"]
    for want in (
        ".wiki/work/lists_input.md",
        ".wiki/work/lists.md",
        "one ## <Name> block per kept section, in lens order",
        "Follow each section's description",
        "at most 300 characters",
        "Copy citation ids and quotes exactly as they appear in the history file's entries",
        "five or more",
        "update an existing line's status rather than",
        "never drop a line from a Keep: list",
        "half the page's budget",
        "condense older settled items into one line",
        "\u2192 history/<file>.md",
    ):
        assert want.lower() in prompt.lower(), want


def test_writer_prompt_keeps_the_lists_and_searches_the_history_files():
    prompt = nodes(GRAPH)["write"]["prompt"]
    assert KEEP_SENTENCE in prompt and SEARCH_SENTENCE in prompt
    assert (
        "each line is one `- ` item of at most 300 characters with at least one citation"
        in (prompt[prompt.index("never-move sections") :])
    )  # in the kept-sections paragraph: the line rule the checks enforce
    assert "history volumes you cannot see" not in prompt
    assert "replace it whole." in prompt and "4,000" not in prompt
    assert "Never edit the ## History section; code maintains it." in prompt


# ---------------------------------------------------------------- end to end


def _engine(graph, corpus: Path, params: dict, logs_root, **kw) -> int:
    """Stand-in for the dot-runner batch, in the graph's order: select, assemble, brief (by
    hand), rollover, the list loop (lists by hand), page_select, write (by hand: a new
    entry on the hub and the source's summary page), checks, commit."""
    run_dir = params["run_dir"]
    while step(corpus, "select", run_dir, params["cap"], params["only"])[1] == "source":
        assert step(corpus, "assemble") == (0, "ok")
        fake_brief(corpus, ["resolve"])
        assert step(corpus, "rollover") == (0, "ok")
        lists_loop(corpus)
        assert step(corpus, "page_select") == (0, "ok")
        q = cite(corpus, QUOTE)
        summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
        (corpus / summary).write_text(page(summary, [ORCHARD], f"- Notes. {q}\n"))
        text = (corpus / HUB).read_text().replace("sources:\n", f'sources:\n  - "{ORCHARD}"\n', 1)
        new = f"- **2031-03-02 (Wren Talbot):** the rollout ships on Friday. {q}\n\n"
        (corpus / HUB).write_text(text.replace("## Decisions\n\n", "## Decisions\n\n" + new, 1))
        verdict = step(corpus, "checks")
        findings = corpus / ".wiki/work/findings.md"
        assert verdict == (0, "pass"), findings.read_text() if findings.exists() else verdict
        assert step(corpus, "commit", run_dir) == (0, "next")
    return 0


def test_ingest_rolls_fills_the_lists_from_each_new_history_file_then_writes(
    corpus: Path, monkeypatch: pytest.MonkeyPatch
):
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != ORCHARD:
            f.unlink()
    seed(corpus)
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", _engine)
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(corpus)])
    assert a.func(a) == 0
    row = json.loads(wiki_ledger(corpus).read_text().splitlines()[-1])
    vols = row["rolled_over"]
    assert row["source"] == ORCHARD and row["converged"] and len(vols) == 2
    assert (row["lists_done"], row["lists_failed"], row["lists_deferred"]) == (2, 0, 0)
    assert row["lists_failures"] == [] and row["kept_over_half"] == []
    assert row["model_calls"] == 4  # the brief, a lists call per history file, the writer
    log = git(corpus, "log", "--format=%s").splitlines()
    assert log[:4] == [
        f"ingest: {ORCHARD}",
        f"lists: {HUB} \u2190 {vols[1]}",
        f"lists: {HUB} \u2190 {vols[0]}",
        f"rollover: {HUB} \u2192 {', '.join(vols)}",
    ]
    text = (corpus / HUB).read_text()
    assert heads(text)[:3] == [CS, "## Decision list", "## Owners"]
    assert all(f"\u2192 {v}" in text for v in vols) and QUOTE in text
    assert {v: e["status"] for v, e in state(corpus).items()} == dict.fromkeys(vols, "done")
    result = json.loads(max((corpus / ".wiki/runs").glob("ingest-*/result.json")).read_text())
    assert (result["lists_done"], result["lists_failed"], result["lists_pending"]) == (2, [], 0)
    assert result["kept_over_half_pages"] == []
    assert git(corpus, "status", "--porcelain") == ""
