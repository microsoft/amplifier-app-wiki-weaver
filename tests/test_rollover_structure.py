"""Roll-over keeps a page's structure, not only its lines: a label stays above the bullets it
heads, and a fenced code block is text, never headings or entries. Fixtures keep the real
weekly page's shape (a bold label per chat, its bullets directly under it)."""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest
from test_rollover import FILLER, bullet, headings, page, roll

from wiki_weaver.rollover import RolloverRefused, parse_page, render_page

MARKER_ONLY = re.compile(r"\s*(?:<!--\s*/?superseded[^>]*-->\s*)+")
BOLD_LINE = re.compile(r"\*\*[^*]+\*\*(?::|[ \t]*\u2014.*)?")


def labelled(text: str) -> dict[str, list[str]]:
    """Each label - an unindented line, not a bullet, heading or bare superseded marker,
    above a bullet directly or across blank lines; glued under other lines, only one bold
    line directly above the bullet - and the bullet lines that follow it."""
    lines = text.splitlines()
    out = {}
    for i, ln in enumerate(lines):
        if not ln.strip() or ln.startswith(("- ", "#", " ", "\t")) or MARKER_ONLY.fullmatch(ln):
            continue
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j == len(lines) or not lines[j].startswith("- "):
            continue
        glued = i > 0 and lines[i - 1].strip() and not lines[i - 1].startswith("#")
        if glued and (j > i + 1 or not BOLD_LINE.fullmatch(ln.strip())):
            continue  # glued to the lines above: their continuation, unless one bold line
        under = []
        for nxt in lines[j:]:
            if not nxt.startswith("- "):
                break
            under.append(nxt)
        out[ln] = under
    return out


def assert_labels_keep_their_bullets(text: str, plan) -> None:
    docs = [plan.hub_text, *(v for _, v in plan.volumes)]
    labels = labelled(text)
    assert labels
    for label, under in labels.items():
        [doc] = [d for d in docs if label in d.splitlines()]
        assert labelled(doc).get(label) == under, label


# ---------------------------------------------------------------- labels


def test_a_label_heads_the_bullets_below_it():
    text = page(
        "## Projects\n\n**Project A:**\n- 2026-01-01 old A\n\n**Project B:**\n- 2026-01-03 new B\n"
    )
    plan = roll(text, budget=40, keep=set())
    [(_, vol)] = plan.volumes
    assert "**Project A:**\n- 2026-01-01 old A\n" in vol
    assert "**Project B:**\n- 2026-01-03 new B\n" in plan.hub_text
    assert "Project A" not in plan.hub_text and "Project B" not in vol


MIXED = (
    "## Week of 2026-05-18\n\n"
    "**Lilurin Bibivusikebo Sikevifisa chat (Delia and Bruno).**\n"
    "- **Delia: tane fusi PI vikif movapa (dog 2026-05-20).** " + "x" * 300 + "\n"
    "- **Delia: open Laz for bafepubu, mekaf-mese and Bipebivopopamovi (2026-05-19).** kube\n"
    "- **2026-06-01, Bruno zilera Delia's zaza.** mupebe\n"
    "\n"
    "**Ralog chat.**\n"
    "- **2026-05-02 buv item.** " + "y" * 300 + "\n"
)


def test_a_mixed_date_group_stays_whole_on_the_hub():
    # Its newest bullet is the page's newest date, so the group never moves - not even the
    # bullets dated earlier.
    text = page(MIXED, title="Weekly commitments and blockers", type_="weekly")
    plan = roll(text, name="weekly-commitments.md", budget=400, keep=set())
    group = MIXED[MIXED.index("**Lilurin") : MIXED.index("\n\n**Ralog") + 1]
    assert group in plan.hub_text
    assert not any("Delia" in v for _, v in plan.volumes)
    assert "**Ralog chat.**\n- **2026-05-02 buv item.**" in plan.volumes[0][1]
    assert_labels_keep_their_bullets(text, plan)


LABEL_WEEK = (  # weekly-commitments.md, week of 2026-05-18, lines shortened
    "## Week of 2026-05-18\n\n"
    "**Lagezalened Mupavofu chat (2026-05-18 to 2026-05-24).** Piet tirigir to open Pibipesov "
    "Tatuta to the bema on Zoradoru 2026-05-21.\n\n"
    "**Lilurin Bibivusikebo Sikevifisa chat (Delia and Bruno, 2026-05-18 to 2026-05-22; "
    "simikaf).**\n"
    '- **Delia: tane fusi PI vikif movapa (dog 2026-05-20).** [s961: "the lilurin bibivusikebo '
    'tanera and bivuki pava are kab movapa"].\n'
    "- **Delia: open Laz for bafepubu, mekaf-mese and Bipebivopopamovi (2026-05-19; nagela "
    'fobopu by 2026-05-20).** [s961: "laz are lalo, nogi didal titoz with"].\n'
    "- **Delia: semafev and voseb digera zaza (Bruno's dunelulu 2026-05-20).** [s961: \"sa'to "
    'titoz pibomus for semafev + voseb digera zaza"].\n'
    "- **Delia: Bema Lagit question-tanedeguz nagot, popeb gigu (2026-05-21), sopusa gigu "
    "luziduna.** Kemefu bafo bobifo; lotitoni of the nagot bupikemu.\n"
    "- **2026-05-22, Bruno zilera Delia's zaza.** [s961: \"sa nogu to bufev the zagunoz fokuvu "
    'tig dido in dagize and gore digera kovakiveko"].\n\n'
    "**Pibipesov Fapos Sikevifisa chat (Mirko and Bruno, 2026-05-19 to 2026-05-20; simikaf).**\n"
    "- **Bruno's sibuv for Mirko for the week (geg 2026-05-19).** [s960: \"That've galen tig do "
    'on the sibuv for this week rulu."]\n'
    '- **Mirko: pava tidotoz, vumikufo with Soren, and fufib to (dog).** [s960: "B tidotoz the '
    'movapa pava tinelogiz/pibipesov-fapos bimovemim the zutanizad"]\n'
    "- **Mirko: kobifiso and vomu nurelazi in vobefipe (dunile 2026-05-20).** Mubobuvu "
    "kobifiso, rulu vomu nurelazi.\n"
    '- **Mirko: bivuki-to-kabak-kumo pife in vobefipe (dunile 2026-05-20).** [s960: "B tozu a '
    'pifa ruzu on that and zoda zaza on it in vobefipe"].\n'
)


def test_every_label_keeps_its_bullets_in_a_labelled_week():
    # The batch-19 failure: the week's dates went to different volumes, and Delia's entries
    # left without the label that headed them.
    newest = "## Week of 2026-10-12\n\n" + bullet("2026-10-12", 9, 0, 300)
    text = page(newest, LABEL_WEEK, title="Weekly commitments and blockers", type_="weekly")
    plan = roll(text, name="weekly-commitments.md", budget=600, keep=set())
    assert len(plan.volumes) >= 2
    assert_labels_keep_their_bullets(text, plan)
    [vol] = [v for _, v in plan.volumes if "Delia: open Laz" in v]
    assert "**Lilurin Bibivusikebo Sikevifisa chat (Delia and Bruno," in vol


def week(monday: date, n: int) -> str:
    d = [(monday + timedelta(days=k)).isoformat() for k in range(5)]
    return (
        f"## Week of {d[0]}\n\n"
        f"**Opening notes for week {n}.** Nothing else was said.\n\n"  # a blank apart: a label
        f"- **{d[0]} unlabelled item {n}.** {FILLER}\n"
        f"**Chat A of week {n}.**\n"  # straight after a bullet: still a label
        f"- **{d[1]} item A{n}.** {FILLER}\n"
        f"- **An item whose own date sits past the first sixty characters ({d[3]}).** {FILLER}\n"
        "<!-- superseded: 2026-11-01 -->\n"  # a bare marker never heads anything
        f"- **{d[2]} a superseded item A{n}.**\n"
        "<!-- /superseded -->\n\n"
        f"**Chat B of week {n}.**\n"
        f"- **{d[4]} item B{n}.** {FILLER}\n"
    )


def test_every_label_keeps_its_bullets_across_volumes():
    weeks = [week(date(2026, 5, 4) + timedelta(weeks=n), n) for n in range(12)]
    text = page(*reversed(weeks), title="Weekly commitments and blockers", type_="weekly")
    plan = roll(text, name="weekly-commitments.md", budget=2_000, keep=set())
    assert len(plan.volumes) >= 3
    assert_labels_keep_their_bullets(text, plan)
    docs = [plan.hub_text, *(v for _, v in plan.volumes)]
    for n in range(12):  # a bare superseded marker heads nothing: its bullet stays in chat A
        [doc] = [d for d in docs if f"**Chat A of week {n}.**" in d]
        assert f"a superseded item A{n}.**" in doc


def test_continuations_and_fences_are_never_labels():
    # An indented continuation, or a fence closing right above the next bullet, belongs to
    # the entry above: it moves with that entry and heads nothing.
    cont = "- **2026-01-01 (Wren):** the old entry,\n  continued on an indented line.\n"
    script = "- **2026-01-02 (Wren):** the old entry's script:\n```\nrun-the-backfill --all\n```\n"
    text = page("## Updates\n\n" + cont + script + "- **2026-02-01 (Wren):** a newer entry.\n")
    plan = roll(text, budget=80, keep=set())
    moved = "".join(v for _, v in plan.volumes)
    assert cont in moved and script in moved
    assert "continued on an indented" not in plan.hub_text and "backfill" not in plan.hub_text


def test_a_group_with_an_undated_bullet_never_moves():
    text = page(
        "## Projects\n\n**Project A:**\n- no date on this one\n- 2026-01-01 old A\n\n"
        "**Project B:**\n- 2026-01-03 new B\n"
    )
    plan = roll(text, budget=40, keep=set())
    assert plan.volumes == [] and plan.hub_text == text


def test_blank_separated_labels_head_their_bullets():
    old = "- 2026-01-01 old A " + "a" * 50_000 + "\n"
    text = page(
        "## Projects\n\n**Project A:**\n\n" + old + "\n**Project B:**\n\n- 2026-01-03 new B\n"
    )
    plan = roll(text, keep=set())
    [(_, vol)] = plan.volumes
    assert "**Project A:**\n\n- 2026-01-01 old A" in vol
    assert "**Project B:**\n\n- 2026-01-03 new B\n" in plan.hub_text
    assert "Project A" not in plan.hub_text and "Project B" not in vol
    assert_labels_keep_their_bullets(text, plan)
    # glued to a bullet's lines and then apart from the next bullet: that bullet's continuation
    glued = page("## Projects\n\n" + old + "still about old A.\n\n- 2026-01-03 new B\n")
    [(_, vol)] = roll(glued, keep=set()).volumes
    assert old + "still about old A.\n" in vol


def test_indented_code_and_headings_stay_with_their_bullet():
    old = (
        "- **2026-01-01 (Wren):** the old entry " + "x" * 50_000 + "\n"
        "\n"
        "      ## This is indented code\n"
        "      print(123)\n"
        "  - 2026-01-20 an indented sub-bullet\n"
        "\n"
    )
    text = page("## Updates\n\n" + old + "- **2026-02-01 (Wren):** a newer entry.\n")
    assert headings(text) == ["# Resolve", "## Updates"]
    plan = roll(text, keep=set())
    [(_, vol)] = plan.volumes
    assert old.rstrip("\n") in vol
    assert "indented code" not in plan.hub_text and "print(123)" not in plan.hub_text
    assert headings(plan.hub_text) == ["# Resolve", "## Updates", "## History"]


def test_a_quote_or_superseded_text_under_a_bullet_is_its_continuation():
    # As on person and project pages: a quote's second line, or the item's superseded old
    # text, sits between two bullets. It continues the bullet above; it labels nothing.
    quote = (
        '- **2026-01-01 (Piet):** an didal [s1: "the popeb pabe of a tiduz\n'
        '> and its sopusa pabe"] rirug.\n'
    )
    superseded = (
        "- **2026-01-02 (Gisselle):** sofevobe.\n"
        "<!-- superseded: 2026-01-02 -->**Pava pumava (open, 2026-01-01).** The buv sesa.\n"
    )
    text = page("## Updates\n\n" + quote + superseded + "- **2026-02-01 (Wren):** newer.\n")
    plan = roll(text, budget=200, keep=set())
    moved = "".join(v for _, v in plan.volumes)
    assert quote in moved and superseded in moved
    assert "sopusa pabe" not in plan.hub_text and "The buv sesa" not in plan.hub_text


def test_indented_lines_continue_a_paragraph():
    # In a section without bullets an indented block, even one with its own date, belongs
    # to the paragraph above it.
    old = "The old decision " + "y" * 50_000 + ".\n\n    2026-03-01 printed in the log\n"
    text = page(
        "## 2026-01-01 (planning call)\n\n" + old,
        "## 2026-02-01 (planning call)\n\nA newer decision.\n",
        title="Decisions",
        type_="decisions",
    )
    plan = roll(text, name="decisions.md", keep=set())
    moved = "".join(v for _, v in plan.volumes)
    assert old in moved and "printed in the log" not in plan.hub_text


def test_a_wrapped_line_under_a_bullet_continues_it():
    wren = (
        "- **2026-01-01:** Wren disabled publishing until\n"
        "the security audit is complete; this restriction applies only to Wren.\n"
    )
    rowan = "- **2026-02-01:** Rowan enabled publishing after approval.\n"
    plan = roll(page("## Updates\n\n" + wren + rowan), budget=150, keep=set())
    [(_, vol)] = plan.volumes
    assert wren in vol
    assert "security audit" not in plan.hub_text and rowan in plan.hub_text


@pytest.mark.parametrize(
    "label",
    [
        "**Zoradoru 2026-05-14 bema fobefuk (Pibipesov bema chat mebibivop)**",
        "**Commitments (Giriden Kas, Bruno, Arlo and Gisselle, 2026-05-13)**:",
        "**Team chat items** \u2014 written, 2026-05-07",
    ],
)
def test_a_bold_line_glued_under_a_bullet_labels_the_bullets_below(label):
    old = "- **2026-05-04 old item.** done\n"
    group = f"{label}\n- **2026-06-01 newer item.** open\n"
    plan = roll(page("## Week of 2026-05-04\n\n" + old + group), budget=60, keep=set())
    [(_, vol)] = plan.volumes
    assert old in vol and label not in vol and group in plan.hub_text


@pytest.mark.parametrize(
    "glued",
    [
        "**Bold start.** and then more prose on the same line\n",
        "**Note:**\nthe audit is still pending.\n",
    ],
)
def test_a_glued_bold_line_with_prose_is_a_continuation(glued):
    old = "- **2026-01-01:** Wren's item\n" + glued
    rowan = "- **2026-02-01:** Rowan's item.\n"
    plan = roll(page("## Updates\n\n" + old + rowan), budget=60, keep=set())
    [(_, vol)] = plan.volumes
    assert old in vol and rowan in plan.hub_text


# ---------------------------------------------------------------- code fences

FENCED = (
    "## Updates\n\n"
    "- **2026-01-01 (Wren):** the old entry, with its config:\n"
    "\n"
    "  ```yaml\n"
    "  ```text\n"  # a fence line with an info string never closes one
    "  ## This is a code heading\n"
    "- 2026-01-02 a bullet-looking line inside the fence\n"
    "  ```\n"
    "\n"
    "  ~~~~\n"
    "  ````\n"  # backticks never close a tilde fence, however many
    "**A label-looking line**\n"
    "- 2026-01-03 another bullet-looking line\n"
    "  ~~~\n"  # shorter than the opening: it does not close it
    "  ~~~~\n"
    "- **2026-02-01 (Wren):** a newer entry.\n"
)


def balanced(text: str) -> bool:
    fence = None
    for line in text.splitlines():
        m = re.match(r"[ \t]*(`{3,}|~{3,})(.*)$", line)
        if m is None:
            continue
        run, rest = m.groups()
        if fence is None:
            fence = run
        elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
            fence = None
    return fence is None


def test_a_fenced_block_moves_whole_with_its_entry():
    text = page(FENCED)
    assert render_page(parse_page(text)) == text
    assert headings(text) == ["# Resolve", "## Updates"]
    plan = roll(text, budget=300, keep=set())
    [(_, vol)] = plan.volumes
    old = FENCED[FENCED.index("- **2026-01-01") : FENCED.index("- **2026-02-01")]
    assert old.rstrip("\n") in vol
    assert "code heading" not in plan.hub_text and "bullet-looking" not in plan.hub_text
    assert headings(plan.hub_text) == ["# Resolve", "## Updates", "## History"]
    assert balanced(plan.hub_text) and balanced(vol)


def test_an_unclosed_fence_is_refused():
    text = page(
        "## Updates\n\n"
        + bullet("2026-01-01", 1, 0, 400)
        + "\n  ```\n  the fence never closes\n\n"
        + bullet("2026-02-01", 2, 1, 400)
    )
    with pytest.raises(RolloverRefused, match="fence"):
        roll(text, budget=300, keep=set())
    assert render_page(parse_page(text)) == text  # parsing alone does not refuse
    # a backtick run whose info holds a backtick is inline code, not a fence
    inline = page(
        "## Updates\n\n"
        + bullet("2026-01-01", 1, 0, 400)
        + "``` a ` b is inline code\n\n"
        + bullet("2026-02-01", 2, 1, 400)
    )
    assert roll(inline, budget=300, keep=set()).volumes


# ---------------------------------------------------------------- layouts refused

SUBSECTIONS = (
    "## Commitments\n"
    "- **2026-03-01:** Shared status.\n"
    "### Wren\n"
    "- **2026-02-01:** The owner disabled publishing.\n"
    "### Rowan\n"
    "- **2026-03-01:** The owner enabled publishing.\n"
)


def test_a_subsection_refuses_the_page():
    # Rolling it would move Wren's entry with "### Rowan", leaving Rowan's under "### Wren".
    with pytest.raises(RolloverRefused, match=r"line \d+ is a level-3 or deeper heading"):
        roll(page(SUBSECTIONS), budget=60, keep=set())


LAYOUTS = [
    ("### Wren\n", "level-3 or deeper heading"),
    ("#### Wren's notes\n", "level-3 or deeper heading"),
    ("1. a numbered point\n", "numbered list item"),
    ("2) another numbered point\n", "numbered list item"),
    ("* a star bullet\n", r"\* or \+ bullet"),
    ("+ a plus bullet\n", r"\* or \+ bullet"),
    ("| owner | state |\n", "table row"),
    ("<!-- a note that\ngoes on -->\n", "HTML comment that spans lines"),
]


@pytest.mark.parametrize(("block", "what"), LAYOUTS)
def test_an_unsupported_layout_refuses_the_page(block, what):
    old, new = bullet("2026-01-01", 1, 0, 400), bullet("2026-02-01", 2, 1, 400)
    with pytest.raises(RolloverRefused, match=what):
        roll(page("## Updates\n\n" + old + "\n" + block + "\n" + new), budget=300, keep=set())


@pytest.mark.parametrize("wrap", ["fence", "indent"])
@pytest.mark.parametrize("block", [b for b, _ in LAYOUTS])
def test_a_layout_in_a_fence_or_indented_under_a_bullet_is_its_text(block, wrap):
    if wrap == "fence":
        inner = "```\n" + block + "```\n"
    else:
        inner = "".join(f"  {ln}\n" for ln in block.splitlines())
    first = bullet("2026-01-01", 1, 0, 400) + inner
    text = page("## Updates\n\n" + first + "\n" + bullet("2026-02-01", 2, 1, 400))
    plan = roll(text, budget=300, keep=set())
    assert first in "".join(v for _, v in plan.volumes)


def test_a_one_line_html_comment_is_text():
    first = bullet("2026-01-01", 1, 0, 400) + "<!-- a note for editors -->\n"
    text = page("## Updates\n\n" + first + "\n" + bullet("2026-02-01", 2, 1, 400))
    plan = roll(text, budget=300, keep=set())
    assert first in "".join(v for _, v in plan.volumes)


def test_a_layout_behind_a_superseded_marker_still_refuses():
    old, new = bullet("2026-01-01", 1, 0, 400), bullet("2026-02-01", 2, 1, 400)
    retired = "<!-- superseded: 2026-01-02 -->* a retired star bullet\n"
    with pytest.raises(RolloverRefused, match=r"\* or \+ bullet"):
        roll(page("## Updates\n\n" + old + "\n" + retired + "\n" + new), budget=300, keep=set())


def test_a_layout_in_text_that_never_moves_is_not_refused():
    # As on the batch-19 source page whose intro is a numbered list: the intro above the
    # first `## ` heading and the Current state stay put, so nothing there can be split.
    intro = "1. **Where the bundles live**, and who owns them\n2. What ships next\n\n"
    state = "## Current state (as of 2026-02-01)\n\n| owner | state |\n| --- | --- |\n\n"
    first = bullet("2026-01-01", 1, 0, 400)
    text = page(intro + state + "## Updates\n\n" + first + "\n" + bullet("2026-02-01", 2, 1, 400))
    plan = roll(text, budget=300, keep=set())
    assert first in "".join(v for _, v in plan.volumes) and intro in plan.hub_text
