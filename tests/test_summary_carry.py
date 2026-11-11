"""Proper summary carry: on a page with history (its oldest entries rolled into volumes the
writer reads only by searching), page_select shows the previous Current state for the writer
to update, its size is reported - never limited - and `## History` is code's alone.
"""

from __future__ import annotations

import json
from pathlib import Path

from dotparse import nodes
from test_rollover_step import HUB, begin, current, git, seed
from test_steps import ORCHARD, cite, page, step

from wiki_weaver import checks as ck
from wiki_weaver.rollover import has_history

ROOT = Path(__file__).resolve().parent.parent
QUOTE = "The orchard sensor rollout ships on Friday"  # verbatim in the ORCHARD fixture
VOLUME = "history/orchard-rollout--2031-01-01--2031-02-27.md"
ITEM = f"- [2031-01-01 to 2031-02-27 \u00b7 58 entries]({VOLUME})"
FOUND = f"{HUB}: History section is maintained by code"
HEAD = "has history: previous Current state included - update it and replace it whole"


def hub(state: str = "On track; the north block is wired.", history: bool = True) -> str:
    text = (
        "---\ntitle: Orchard rollout\ntype: topics\nsources:\n  - chat-2031-02-28.md\n"
        "last_updated: 2031-02-28\n---\n\n# Orchard rollout\n\n"
        f"## Current state (as of 2031-02-28)\n\n{state}\n\n"
        "## Updates\n\n- **2031-02-28 (Wren Talbot, orchard chat):** the north block is wired.\n"
    )
    return text + (f"\n## History\n{ITEM}\n" if history else "")


def test_has_history_reads_text_or_a_path(tmp_path: Path):
    assert has_history(hub()) and not has_history(hub(history=False))
    # a History section only counts when it lists a volume
    assert not has_history(hub(history=False) + "\n## History\n\nIt began as a pilot.\n")
    (tmp_path / HUB).write_text(hub())
    assert has_history(tmp_path / HUB)


# ---------------------------------------------------------------- page_select


def pages_md(corpus: Path, tmp_path: Path, text: str) -> tuple[str, str]:
    """The hub's header line in pages.md, and pages.md."""
    (corpus / HUB).write_text(text)
    begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "page_select") == (0, "ok")
    pages = (corpus / ".wiki/work/pages.md").read_text()
    return next(ln for ln in pages.splitlines() if ln.startswith(f"## {HUB} (")), pages


def test_page_select_keeps_current_state_on_history_page(corpus: Path, tmp_path: Path):
    head, pages = pages_md(corpus, tmp_path, hub())
    assert head.endswith(f"; {HEAD})")
    assert "## Current state (as of 2031-02-28)\n\nOn track; the north block is wired.\n" in pages
    assert ITEM in pages


def test_page_select_strips_current_state_without_history(corpus: Path, tmp_path: Path):
    head, pages = pages_md(corpus, tmp_path, hub(history=False))
    assert "has history" not in head
    assert "On track; the north block is wired." not in pages
    assert "## Updates\n\n- **2031-02-28" in pages


# ---------------------------------------------------------------- the checks


def test_history_edit_is_a_finding():
    before = hub()
    assert ck.check_history_unchanged(HUB, before, before) == []
    for after in (
        before.replace("58 entries", "59 entries"),  # an item edited
        before.replace(ITEM + "\n", ""),  # an item dropped
        before + "- A note the writer left under History.\n",  # a line added
        before.replace("\n## History\n", "\n## Archive\n"),  # renamed: no History left
    ):
        assert ck.check_history_unchanged(HUB, before, after) == [FOUND]
    assert ck.check_kind(FOUND) == "history"
    # edits elsewhere, and blank lines at the end of the file, leave History as it was
    after = before.replace("is wired.", "is wired and tested.") + "\n\n"
    assert ck.check_history_unchanged(HUB, before, after) == []


def test_history_added_by_writer_is_a_finding():
    plain = hub(history=False)
    added = plain + "\n## History\n\nThe orchard project began as a pilot in 2030.\n"
    assert ck.check_history_unchanged(HUB, plain, added) == [FOUND]
    assert ck.check_history_unchanged(HUB, None, added) == [FOUND]  # nor may a new page
    assert ck.check_history_unchanged(HUB, None, plain) == []


def _checks(tmp_path: Path, after: str) -> list[str]:
    wiki, before = tmp_path / "wiki", tmp_path / "before"
    wiki.mkdir()
    before.mkdir()
    (before / HUB).write_text(hub())
    (wiki / HUB).write_text(after)
    return ck.run_page_checks(wiki, [HUB], before, sources={})


def test_a_long_current_state_on_a_history_page_is_no_finding(tmp_path: Path):
    # The limit is gone (docs/DECISIONS.md): a roll-up of any size is reported, not refused.
    assert _checks(tmp_path, hub("x" * 9000)) == []
    assert ck.current_state_chars(hub("x" * 9000)) == 9000
    assert ck.current_state_chars(hub("x" * 9000, history=False)) == 9000
    assert not any("budget" in needle for needle, _ in ck.CHECK_KINDS)


def test_run_page_checks_still_guards_history(tmp_path: Path):
    errs = _checks(tmp_path, hub("x" * 9000).replace("58 entries", "59 entries"))
    assert FOUND in errs and "current_state" not in {ck.check_kind(e) for e in errs}


# ---------------------------------------------------------------- the prompt


def test_write_prompt_carries_current_state_on_history_pages():
    prompt = nodes(ROOT / "pipeline" / "ingest.dot")["write"]["prompt"]
    assert (
        "On a page marked 'has history': Older entries are in the history files listed under "
        "## History (`history/<page>--<first>--<last>.md`); they are not shown here."
    ) in prompt
    assert (
        "rather than reading files whole. The previous Current state is shown; update it, keep "
        "facts from it that still hold, and replace it whole. "
        "Never edit the ## History section; code maintains it."
    ) in prompt
    assert "4,000" not in prompt  # no limit on its size: it is reported instead
    assert "previous roll-up was removed on purpose" in prompt  # every other page: as before


# ---------------------------------------------------------------- end to end


def test_rollover_then_write_does_not_trip_history_check(corpus: Path, tmp_path: Path):
    # The rollover step changes the hub's History before page_select copies the hub to
    # before/, so the writer's starting point already holds it: a normal write passes.
    seed(corpus)
    run = begin(corpus, tmp_path, ["orchard-rollout"])
    assert step(corpus, "rollover") == (0, "ok")
    assert current(corpus)["rolled_over"]
    rolled = (corpus / HUB).read_text()
    assert step(corpus, "page_select") == (0, "ok")
    assert "\n## History\n" in (corpus / ".wiki/work/before" / HUB).read_text()
    pages = (corpus / ".wiki/work/pages.md").read_text()
    assert f"; {HEAD})" in pages and "## Current state (as of" in pages
    # the writer: a new dated entry, the source listed, the Current state replaced whole
    q = cite(corpus, QUOTE)
    text = rolled.replace("sources:\n", f'sources:\n  - "{ORCHARD}"\n', 1)
    text = text.replace(
        "## Updates\n\n", f"## Updates\n\n- **2031-03-02 (Wren Talbot):** Friday. {q}\n", 1
    )
    start = text.index("## Current state")
    end = text.index("\n## ", start) + 1
    state = f"## Current state (as of 2031-03-02)\n\nThe rollout ships on Friday {q}.\n\n"
    (corpus / HUB).write_text(text[:start] + state + text[end:])
    summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
    (corpus / summary).write_text(page(summary, [ORCHARD], f"- Rollout Friday {q}\n"))
    verdict = step(corpus, "checks")
    findings = corpus / ".wiki/work/findings.md"
    assert verdict == (0, "pass"), findings.read_text() if findings.exists() else verdict
    assert step(corpus, "commit", str(run)) == (0, "next")
    after = (corpus / HUB).read_text()
    assert after[after.index("\n## History\n") :] == rolled[rolled.index("\n## History\n") :]
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["converged"] and HUB in row["pages_touched"]
    # the Current state's size, heading aside, for each page written but the source summary
    assert row["current_state_chars"] == {HUB: len(f"The rollout ships on Friday {q}.")}
    assert row["history_pages"] == [HUB]
    assert git(corpus, "status", "--porcelain") == ""
