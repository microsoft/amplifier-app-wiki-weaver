"""Sections a lens keeps on the page - `Keep:` and `Keep current:` lines under a page type
(design/plans/2026-11-10-short-lists-spec.md, sections 1-3): they never move to history,
stay out of the page's budget, and code checks their lines."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from test_rollover import TODAY, bullet, known_for, page
from test_rollover_cli import LENS, QUOTE, git, make_wiki, rollover_cmd

from wiki_weaver import checks as ck
from wiki_weaver import cli, lens, rollover
from wiki_weaver.lens import KeptSection, describe, parse_page_types
from wiki_weaver.rollover import check_conservation, plan_rollover

KEEP_LENS = """# Lens

## Page types

- **initiatives:** one page per initiative in flight.
  Sections: Owners \u00b7 Decisions
  Keep: Decision list \u2014 one line per decision: date, decision, status, reason. (covers: Decisions)
  Keep: Timeline - one line per turning point
  Keep current: Current owners: one line per area and who owns it now
  Keep: **Question list**
  Keep: Follow-ups \u2013 one line per follow-up (covers:Owners)
- **weekly commitments and blockers:** what was committed each week.
  Sections pattern: Week of YYYY-MM-DD
"""
STATE = f'## Current state (as of 2026-11-04)\n\nOn track [s1: "{QUOTE}"].\n'
DAYS = [(date(2026, 1, 1) + timedelta(days=i)).isoformat() for i in range(90)]


def kept() -> list[KeptSection]:
    return parse_page_types(KEEP_LENS)["initiative"].kept


def item(day: str, what: str, sid: int = 1) -> str:
    return f'- {day} \u00b7 {what} \u00b7 in force \u00b7 agreed [s{sid}: "{QUOTE}"]\n'


DEC = [item(f"2026-05-0{i}", f"decision {i}").rstrip("\n") for i in range(1, 5)]


def dec_list(lines: list[str]) -> str:
    return "## Decision list\n\n" + "".join(ln + "\n" for ln in lines)


def written(*lists: str) -> str:
    """A page with Current state, then ``lists``, then one entry under Owners."""
    return page(STATE, *lists, "## Owners\n\n- **2026-11-01:** an owner noted.\n")


# ---------------------------------------------------------------- the lens


def test_keep_lines_parse_into_kept_sections():
    types = parse_page_types(KEEP_LENS)
    assert types["initiative"].kept == [
        KeptSection(
            "Decision list",
            "one line per decision: date, decision, status, reason.",
            False,
            "Decisions",
        ),
        KeptSection("Timeline", "one line per turning point", False, None),
        KeptSection("Current owners", "one line per area and who owns it now", True, None),
        KeptSection("Question list", "", False, None),
        KeptSection("Follow-ups", "one line per follow-up", False, "Owners"),
    ]
    assert types["weekly"].kept == []


def test_kept_names_are_allowed_headings():
    types = parse_page_types(KEEP_LENS)
    init, weekly = types["initiative"], types["weekly"]
    assert init.allows("Decision list") and init.allows("current owners") and init.allows("Owners")
    assert not init.allows("Notes")
    assert weekly.allows("Week of 2026-05-04") and not weekly.allows("Decision list")
    only = parse_page_types("## Page types\n\n- **log:** a log.\n  Keep: Timeline - turns\n")["log"]
    assert not only.heading_rule() and [k.name for k in only.kept] == ["Timeline"]


def test_describe_reports_kept_sections():
    by = {d["key"]: d for d in describe(parse_page_types(KEEP_LENS))}
    assert by["initiative"]["kept"] == [
        "Decision list (covers: Decisions)",
        "Timeline",
        "Current owners (current)",
        "Question list",
        "Follow-ups (covers: Owners)",
    ]
    assert by["weekly"]["kept"] == []


def test_the_default_lens_keeps_decisions_and_questions_on_topics_and_focus_on_people():
    types = parse_page_types((Path(lens.__file__).parent / "data" / "default_lens.md").read_text())
    topic, person = types["topic"].kept, types["person"].kept
    assert [(k.name, k.current) for k in topic] == [
        ("Decision list", False),
        ("Question list", False),
    ]
    assert [(k.name, k.current) for k in person] == [("Current focus", True)]
    assert types["log"].kept == [] and all(k.description for k in topic + person)


# ---------------------------------------------------------------- roll-over


def kept_lists(n: int = 40) -> str:
    """A Decision list and a Timeline full of dated `- YYYY-MM-DD` items: they look rollable."""
    days = [DAYS[i % len(DAYS)] for i in range(n)]
    return (
        "## Decision list\n\n"
        + "".join(item(d, f"decision {i}") for i, d in enumerate(days))
        + "\n## Timeline\n\n"
        + "".join(f'- {d} \u00b7 turning point {i} [s1: "{QUOTE}"]\n' for i, d in enumerate(days))
    )


def owners(days: list[str] = DAYS) -> str:
    return "## Owners\n\n" + "".join(
        bullet(d, 1, i % 3) for i, d in reversed(list(enumerate(days)))
    )


def plan(text: str, kept_names: tuple[str, ...] = ("Decision list", "Timeline")):
    return plan_rollover(
        "resolve.md",
        text,
        budget=48_000,
        keep_headings={"Owners"},
        kept_headings=set(kept_names),
        known=known_for(text),
        taken=set(),
        today=TODAY,
    )


def test_kept_sections_never_move_and_conservation_holds():
    lists = kept_lists()
    text = page(STATE, lists, owners())
    p = plan(text)
    assert p is not None and p.volumes
    moved = "".join(v for _, v in p.volumes)
    assert "## Decision list" not in moved and "turning point" not in moved
    assert "decision 0" not in moved
    assert STATE + "\n" + lists in p.hub_text  # in place, directly after Current state
    check_conservation(text, p)


def test_kept_sections_do_not_count_toward_the_budget():
    text = page(STATE, kept_lists(500), owners(DAYS[:20]))  # ~12k of entries, ~80k kept
    assert plan(text) is None
    assert plan(text, kept_names=()) is not None  # the same lines, not kept, would roll


def test_the_cut_stops_at_half_the_budget_however_big_the_kept_sections():
    p = plan(page(STATE, kept_lists(300), owners()))  # ~50k kept, ~54k of entries
    assert p is not None and p.record_after <= 24_000 and not p.over_budget


def test_the_rollover_verb_keeps_what_the_lens_keeps(tmp_path: Path):
    w = make_wiki(tmp_path, hubs=("resolve",))
    keep = "  Keep: Decision list - one line per decision\n  Sections: Owners"
    (w / "lens.md").write_text(LENS.replace("  Sections: Owners", keep, 1))
    lists = (
        "## Decision list\n\n"
        + item("2026-08-14", "a decision")
        + item("2026-08-15", "another")
        + "\n"
    )
    hub = (w / "resolve.md").read_text()
    (w / "resolve.md").write_text(hub.replace("## Owners\n", lists + "## Owners\n", 1))
    git(w, "commit", "-qam", "a kept list, dated as the oldest entries")
    report = rollover_cmd(w)
    assert [p["page"] for p in report["pages"]] == ["resolve.md"] and not report["refused"]
    vols = "".join(p.read_text() for p in (w / "history").glob("resolve--*.md"))
    assert vols and "## Decision list" not in vols and "a decision" not in vols
    assert lists in (w / "resolve.md").read_text()


# ---------------------------------------------------------------- checks


@pytest.mark.parametrize(
    ("line", "why"),
    [
        (f'2026-05-01 \u00b7 a decision [s1: "{QUOTE}"]', "not a '- ' item"),
        (f'- 2026-05-01 \u00b7 {"x" * 300} [s1: "{QUOTE}"]', "over 300 characters"),
        ("- 2026-05-01 \u00b7 a decision \u00b7 in force \u00b7 agreed", "without a citation"),
    ],
)
def test_a_kept_line_is_a_cited_item_of_at_most_300_characters(line: str, why: str):
    assert ck.check_kept_shape("resolve.md", written(dec_list(DEC)), kept()) == []
    [f] = ck.check_kept_shape("resolve.md", written(dec_list([*DEC, line])), kept())
    assert why in f and ck.check_kind(f) == "kept_shape"


def test_a_status_edit_is_a_change_not_a_loss():
    edited = [DEC[0].replace("in force", "reversed 2026-06-01"), *DEC[1:]]
    before, after = written(dec_list(DEC)), written(dec_list(edited))
    assert ck.check_kept_loss("resolve.md", before, after, kept()) == []


MAY = "resolve--2026-05-01--2026-05-31.md"  # holds the entries s1 and s2 cite
JUNE = "resolve--2026-06-01--2026-06-30.md"  # holds only s7's
CITED = [item(f"2026-05-0{i}", f"decision {i}", sid=i).rstrip("\n") for i in range(1, 5)]


def volumes(wiki: Path) -> Path:
    (wiki / "history").mkdir()
    (wiki / "history" / MAY).write_text(
        f'- **2026-05-01:** decision 1 [s1: "{QUOTE}"]\n- **2026-05-02:** decision 2 [s2: "{QUOTE}"]\n'
    )
    (wiki / "history" / JUNE).write_text(f'- **2026-06-01:** a later one [s7: "{QUOTE}"]\n')
    return wiki


def condensing(vol: str) -> str:
    return f'- 2026-05-01 to 2026-05-02 \u00b7 2 settled decisions \u2192 history/{vol} [s1: "{QUOTE}"]'


def test_a_dropped_line_is_lost_without_a_condensing_line(tmp_path: Path):
    before = written(dec_list(CITED))
    [f] = ck.check_kept_loss("resolve.md", before, written(dec_list(CITED[2:])), kept(), tmp_path)
    assert "lost 2 line(s)" in f and "decision 1" in f and ck.check_kind(f) == "kept_loss"


def test_a_condensing_line_excuses_the_lines_whose_sources_its_file_holds(tmp_path: Path):
    before = written(dec_list(CITED))
    after = written(dec_list([condensing(MAY), *CITED[2:]]))
    assert ck.check_kept_loss("resolve.md", before, after, kept(), volumes(tmp_path)) == []


@pytest.mark.parametrize(
    "vol", [JUNE, "resolve--2026-07-01--2026-07-31.md"], ids=["holds-other-sources", "missing"]
)
def test_a_condensing_line_naming_a_file_without_their_sources_excuses_nothing(
    tmp_path: Path, vol: str
):
    before = written(dec_list(CITED))
    after = written(dec_list([condensing(vol), *CITED[2:]]))
    [f] = ck.check_kept_loss("resolve.md", before, after, kept(), volumes(tmp_path))
    assert "lost 2 line(s)" in f and ck.check_kind(f) == "kept_loss"


def test_history_pointers_do_not_excuse_an_unrelated_dropped_line(tmp_path: Path):
    # the list step's output: every line pointing at a file, one unrelated line gone
    pointed = [ln + f" (\u2192 history/{MAY})" for ln in CITED[:2]]
    new = f'- 2026-05-05 \u00b7 decision 5 \u00b7 in force \u00b7 agreed [s1: "{QUOTE}"] (\u2192 history/{MAY})'
    after = written(dec_list([*pointed, CITED[3], new]))  # decision 3 (s3) dropped
    [f] = ck.check_kept_loss(
        "resolve.md", written(dec_list(CITED)), after, kept(), volumes(tmp_path)
    )
    assert "lost 1 line(s)" in f and "decision 3" in f


def test_only_a_line_this_write_adds_can_condense(tmp_path: Path):
    old_pointer = condensing(MAY)  # already on the list before this write
    before = written(dec_list([old_pointer, *CITED]))
    after = written(dec_list([old_pointer, *CITED[1:]]))  # decision 1 (s1, in MAY) dropped
    [f] = ck.check_kept_loss("resolve.md", before, after, kept(), volumes(tmp_path))
    assert "lost 1 line(s)" in f and "decision 1" in f


def test_a_dropped_line_needs_every_source_it_cites_in_a_named_file(tmp_path: Path):
    both = CITED[0].replace("agreed [", f'agreed [s3: "{QUOTE}"] [')  # cites s3 and s1
    before = written(dec_list([both, *CITED[1:]]))
    after = written(dec_list([condensing(MAY), *CITED[1:]]))
    [f] = ck.check_kept_loss("resolve.md", before, after, kept(), volumes(tmp_path))
    assert "lost 1 line(s)" in f
    assert ck.check_kept_loss("resolve.md", before, after, kept()) != []  # no wiki: no proof


EUROPE = (
    "- 2026-05-01 \u00b7 Standardize the delivery contract for every service in Europe "
    f'\u00b7 in force \u00b7 agreed [s1: "{QUOTE}"]'
)
AMERICA = EUROPE.replace("Europe", "America")


def test_a_long_subject_is_compared_whole():
    before = written(dec_list([EUROPE, AMERICA, *DEC[1:]]))
    [f] = ck.check_kept_loss("resolve.md", before, written(dec_list([EUROPE, *DEC[1:]])), kept())
    assert "lost 1 line(s)" in f and "America" in f
    asia = EUROPE.replace("Europe", "Asia")  # another decision: its first 40 characters agree
    after = written(dec_list([EUROPE, asia, *DEC[1:]]))
    [f] = ck.check_kept_loss("resolve.md", before, after, kept())
    assert "lost 1 line(s)" in f and "America" in f


def test_each_surviving_line_accounts_for_one_old_line():
    before = written(dec_list([DEC[0], DEC[0], *DEC[1:]]))  # the same line twice
    [f] = ck.check_kept_loss("resolve.md", before, written(dec_list(DEC)), kept())
    assert "lost 1 line(s)" in f


def test_a_status_reason_or_pointer_edit_is_still_a_change():
    turn = f'- 2026-05-03 \u00b7 turning point 3 [s1: "{QUOTE}"]'
    before = written(dec_list(DEC), "## Timeline\n\n" + turn + "\n")
    edited = DEC[0].replace("in force", "reversed 2026-06-01").replace("agreed", "replaced")
    pointed = DEC[1] + f" (\u2192 history/{MAY})"
    after = written(
        dec_list([edited, pointed, *DEC[2:]]),
        "## Timeline\n\n" + turn + f" ([history](history/{MAY}))\n",
    )
    assert ck.check_kept_loss("resolve.md", before, after, kept()) == []


GOAL = "we adopted the goal contract yesterday"


@pytest.mark.parametrize(
    ("volume", "excused"),
    [
        ('- **2026-05-01:** the venue [s1: "an unrelated remark about the venue"]\n', False),
        (f'- **2026-05-01:** the goal contract [s1: "{GOAL}"]\n', True),
        (
            '- **2026-05-01:** the goal contract [s1: "we adopted the goal\n  contract yesterday"]\n',
            True,
        ),
    ],
    ids=["same-id-other-quote", "same-quote", "quote-split-across-lines"],
)
def test_a_condensing_line_needs_the_dropped_lines_exact_citations(
    tmp_path: Path, volume: str, excused: bool
):
    (tmp_path / "history").mkdir()
    (tmp_path / "history" / MAY).write_text(volume)
    line = (
        f'- 2026-05-01 \u00b7 adopt the goal contract \u00b7 in force \u00b7 agreed [s1: "{GOAL}"]'
    )
    before = written(dec_list([line, *DEC[1:]]))
    after = written(dec_list([condensing(MAY), *DEC[1:]]))
    errs = ck.check_kept_loss("resolve.md", before, after, kept(), tmp_path)
    assert (errs == []) is excused, errs


def test_a_heading_inside_a_code_fence_is_no_section():
    fenced = (
        "## Owners\n\n- a template:\n\n```markdown\n## Decision list\n- not a kept line\n```\n\n"
        "- after the fence\n"
    )
    text = page(STATE, dec_list(DEC[:1]), fenced)
    assert [ln for ln in ck.kept_sections(text, ["Decision list"])["Decision list"] if ln] == [
        DEC[0]
    ]
    assert ck.kept_size(text, ["Decision list"]) == len(dec_list(DEC[:1])) + 1
    assert ck.check_kept_shape("resolve.md", text, kept()) == []
    # and the content-loss guard still sees the fenced lines: they are the Owners section's
    trimmed = text.replace("- not a kept line\n", "").replace("- after the fence\n", "")
    assert ck.check_content_loss("resolve.md", text, trimmed, ["Decision list"]) != []


def test_keep_current_lines_may_be_replaced():
    abel = f'## Current owners\n\n- platform: Abel (since 2026-01-01) [s1: "{QUOTE}"]\n'
    kip = f'## Current owners\n\n- platform: Kip (since 2026-06-01) [s1: "{QUOTE}"]\n'
    assert ck.check_kept_loss("resolve.md", written(abel), written(kip), kept()) == []
    as_kept = [s.replace("Current owners", "Decision list") for s in (abel, kip)]
    assert ck.check_kept_loss("resolve.md", written(as_kept[0]), written(as_kept[1]), kept()) != []


def test_coverage_wants_a_citation_of_each_moved_entry_or_the_file_named():
    vol = "history/resolve--2026-05-01--2026-05-31.md"
    second = "the second decision was taken today"
    moved = {
        vol: [
            f'- **2026-05-01:** decided A [s1: "{QUOTE}"]',
            f'- **2026-05-02:** decided B [s2: "{second}"]',
        ]
    }
    listed = item("2026-05-01", "A")
    [p] = ck.coverage_problems(moved, listed)
    assert "decided B" in p
    both = listed + f'- 2026-05-02 \u00b7 B \u00b7 in force [s2: "{second}"]\n'
    assert ck.coverage_problems(moved, both) == []
    assert ck.coverage_problems(moved, f"- 2026-05 \u00b7 2 decisions \u2192 {vol}\n") == []


def test_kept_size_counts_the_kept_sections_only():
    text = written(dec_list(DEC), "## Timeline\n\n" + DEC[0] + "\n")
    whole = text.index("## Owners") - text.index("## Decision list")
    assert ck.kept_size(text, ["Decision list", "Timeline"]) == whole
    assert ck.kept_size(text, ["Timeline"]) == text.index("## Owners") - text.index("## Timeline")
    assert ck.kept_size(text, []) == 0


def test_kept_sections_are_outside_the_content_loss_guard():
    lines = "".join(f'- area {i}: Abel (since 2026-01-01) [s1: "{QUOTE}"]\n' for i in range(30))
    held = "## Current owners\n\n" + lines
    before = written(held)
    after = written(held.replace("Abel (since 2026-01-01)", "Kip (since 2026-06-01)"))
    assert ck.check_content_loss("resolve.md", before, after, ["Current owners"]) == []
    assert ck.check_content_loss("resolve.md", before, after) != []  # anywhere else: a loss


def test_a_kept_heading_is_a_section_the_lens_names():
    types = parse_page_types(KEEP_LENS)
    before = written()
    assert ck.check_section_headings("resolve.md", before, written(dec_list(DEC)), types) == []
    [f] = ck.check_section_headings("resolve.md", before, written("## Notes\n\n- a note\n"), types)
    assert "Decision list" in f  # the kept names are among the sections it lists


def test_page_checks_run_the_kept_checks(tmp_path: Path):
    (tmp_path / "before").mkdir()
    (tmp_path / "before" / "resolve.md").write_text(written(dec_list(DEC)))
    uncited = "- 2026-06-01 \u00b7 an uncited decision"
    (tmp_path / "resolve.md").write_text(written(dec_list([*DEC[2:], uncited])))
    errs = ck.run_page_checks(
        tmp_path,
        ["resolve.md"],
        tmp_path / "before",
        {1: ("chat-a.md", f"Abel: {QUOTE}.")},
        1,
        parse_page_types(KEEP_LENS),
        known={1: "chat-a.md"},
    )
    assert {ck.check_kind(e) for e in errs if "kept list" in e} == {"kept_shape", "kept_loss"}
    assert "content_loss" not in {ck.check_kind(e) for e in errs}  # kept lines are not lines lost


def test_kept_sections_over_half_the_budget_are_reported(tmp_path: Path):
    types = parse_page_types(KEEP_LENS)
    long = [item("2026-05-01", f"decision {i} {'and more ' * 12}").rstrip("\n") for i in range(250)]
    big = written(dec_list(long))
    (tmp_path / "resolve.md").write_text(big)
    (tmp_path / "small.md").write_text(written(dec_list(DEC)))
    size = ck.kept_size(big, ["Decision list"])
    report = rollover.kept_over_half(tmp_path, "resolve.md", types)
    assert report == {"page": "resolve.md", "chars": size, "limit": 24_000} and size > 24_000
    assert rollover.kept_over_half(tmp_path, "small.md", types) is None


# ---------------------------------------------------------------- lint and doctor


def test_lint_and_doctor_warn_about_history_without_keep_sections(tmp_path: Path, capsys):
    w = make_wiki(tmp_path, hubs=("resolve",))
    rollover_cmd(w)

    def run(*args: str) -> tuple[int, str]:
        a = cli.build_parser().parse_args(list(args))
        rc = a.func(a)
        return rc, capsys.readouterr().out

    warning = "resolve.md: has history files but its type declares no Keep sections"
    rc, out = run("lint", "--wiki", str(w))
    assert f"warning: {warning}" in out and json.loads(out.splitlines()[-1])["warnings"] == 1
    _, out = run("doctor", "--wiki", str(w))
    assert f"[warn] {warning}" in out
    (w / "lens.md").write_text(KEEP_LENS)  # initiatives now keep sections
    rc_kept, out = run("lint", "--wiki", str(w))
    assert warning not in out and json.loads(out.splitlines()[-1])["warnings"] == 0
    assert rc_kept == rc  # a warning never changes the exit code
