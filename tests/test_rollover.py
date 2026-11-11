"""Roll-over: a hub page's oldest whole dates move, verbatim, into history volumes.

Fixtures are shortened excerpts in the shape of the real batch-19 pages (resolve.md,
weekly-commitments.md, decisions.md); page-sized hubs are generated from the same entry shape.
Model-free and file-free, except the ledger-only wiki of the Team Pulse-shaped test.
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from wiki_weaver import ledger as lg
from wiki_weaver import rollover
from wiki_weaver.checks import CITE_RE, WIKILINK_RE
from wiki_weaver.lib import wiki_ledger
from wiki_weaver.rollover import (
    DEFAULT_BUDGET,
    TYPE_BUDGETS,
    ConservationError,
    RolloverPlan,
    RolloverRefused,
    budget_for,
    check_conservation,
    parse_page,
    plan_rollover,
    render_page,
)
from wiki_weaver.sources import split_frontmatter

TODAY = "2026-11-09"
INITIATIVE = {"Owners", "Commitments", "Blockers", "Decisions", "Open questions"}
WHO = ("Abel Ashford", "Kip Cato", "Bruno Carrick", "Tobias Orr")
FILLER = "Nothing in the source says who reviewed it or when. "
STATE = (
    "## Current state (as of 2026-10-12)\n\n"
    'Team Pulse mubos weekly; Tobias Orr buvo the buseboku [s1: "Tobias buvo the Team Pulse '
    'buseboku vef to vef"].\n\n'
)

RESOLVE = """---
title: Giriden
type: initiative
sources:
  - chat-2026-01-01-b.md
  - chat-2026-01-02-0a1b2c3d4e.md
last_updated: 2026-11-04
---

# Giriden

## Current state (as of 2026-10-12)

Vosobebev: Abel Ashford mubasefu on 2026-10-12 that vo fobopu the rone and rolut vosobebev \
(kevo-mubasefu; no donunilu, KU reluti or pava ranur).

Giriden's fofuvep lilelugi is titoz fumumafap. At the 2026-10-09 bipufovap Bema Gore pasoso \
dinod sos gile the fofuvep is, and vifafip was nugolid.

## Owners

- **2026-10-12 05:46 (Abel Ashford, Pibipesov Giriden Bema chat, mek kufibek, no gazod): Abel \
vop rit fuv zaza in notirati, lotu to noto rone.** Abel: [s1634: "B vop mek in notirati ranazad, \
it was vus B was ridurig rone"].

- **2026-10-12 20:43 to 21:33 (Kip Cato and Abel Ashford, Pibipesov Giriden Bema chat; not \
kevafupi): vef bepim zug Giriden nerezaz the nigiz pife.** Kip zoluz [s1625: "va sa nogu to zug \
vip of the"] pasakavepe pakasuka.
- **Pobapepe redoteduz (2026-05-13).** Bruno: [s916: "mev's bep as ralo, fe B'l kamefoka pipo in"].
- <!-- superseded: 2026-06-03 -->**Bruno covers Giriden-makom bikipop (2026-05-12).** Vopepek was \
kosomu to bokema nare nad is bep: [s9: "Fe B zoda luli in and va that fusob mev's \
pipo."]<!-- /superseded -->
- Bruno mefase the vipemovip (runozoron, NOT). Arlo was zoluz for a ZO, as Bruno fevikif it: \
[s4: "ZO, ZO, sa nogu ZO for giriden"].

## Decisions

- **2026-06-09: dagotarut on gile Giriden is (pifa-chat of the Giriden bema fobefuk; vifafip \
nugolid).** This nonudidotog the fobefuk bosupakefe on this page.
  - Kip: [s979: "Makubupu is log fipufema to rezoga it with an fimupifuk."]
  - Delia: makubupu latut [s979: "geli B sos makubupu b bemaf to makubupu tinotezi, the ragu tig \
zuz and totigur pevik kipubip"].
<!-- superseded: 2026-05-13 -->- **Kas logonol (2026-05-13).** Arlo to geg do feke with Bruno: \
[s9: "rizir B've geg do nogi kas feke mefukepi dutulel with the pusev of de"].<!-- /superseded -->

## Open questions

- Zogidun the posomepuv rugul to Rozurarot is titoz open.
"""

WEEKLY = """---
title: Weekly commitments and blockers
type: weekly
sources:
  - chat-2026-01-01-b.md
last_updated: 2026-11-04
---

# Weekly commitments and blockers

## Current state (as of 2026-10-12)

The zodine week is 2026-10-12.

## Week of 2026-10-12

- **2026-10-12 18:17 (Kip Cato, Pibipesov Misefif Sikevifisa chat; dunile kemefu, owner Kip): \
a rerodul dolulonalur for an BOK-vumikivuvu notirati zod.** Kip: [s1640: "B'l dazig somi that do \
in a rerodul dolulonalur"].

- **Bo Lark, Orchard Crew chat 22:10 (self-reported; no reply): pruning report \
and next steps.** Bo Lark's note says [s1623: "Pruned and checked on the east row"].
- **2026-10-14 (Bo Lark, Fence Crew chat 21:05; reported by Bo): gate repairs.** \
Status: [s1532: "nine gates to mend and four of them need new hinges"].
  - **Done (Bo):** [s1532: "This week the crew mended five of the eleven gates"]; six wait for parts.
  - **Decision taken:** the north gate stays shut until spring. See [[decisions]].

## Week of 2026-10-05

- **2026-10-10 00:30 (Abel Ashford, Pibipesov Giriden Bema chat; notirati): Abel dutuza a Giriden \
fofuvep zodot lozur.** [s1612: "B'gu lona nada to reraroto mibuvi a fofuvep zodot for Giriden"]


- **2026-10-09 22:55 (VOMA Misesif Sikevifisa chat; Arlo Lanza to Bruno; open question): pifab \
dalutatuz pukabek Bruno's kemefu.** Arlo zoluz [s1604: "vosi tig tinotezi fofok galul mavumem the \
zizunage or rot safemames nare"].
"""

DECISIONS = """---
title: Decisions
type: decisions
sources:
  - bema-gore-2026-10-09.md
last_updated: 2026-11-04
---

# Decisions

## 2026-10-09 (Bema Gore fobefuk chat 13:45 and 13:46; Bruno Carrick's dunile kemefu, not nugolid)

Bruno: [s1597: "B'l ranazad runigage fupavim for Pibipesov Misefif"], a mokof bubom-lidegi \
fibekopek lotu foku from the zatigi logonol.

It is an kemefu, not a commitment or a decision.

## 2026-10-09 (Bema Gore fobefuk chat 13:00 to 13:06; Bruno Carrick; bemobem, vifafip mosasivum)

The neninadulid zulu kufibek letalet to are not sosasik, fe gile zulu dazagug is pomosuma.

- **Bemobem, Team Pulse kimebove (Bruno):** [s1570: "B ruted the samamobi Tobias Orr rez for \
Team Pulse is dute for mepuf"].
- **Tobias Orr** (not [[tul]]) is ditedo from 2026-10-09, fe no owner is safus.

## 2026-06-23 (Pibipesov Fapos Nedigerago Gore)

- **2026-06-23 (Soren's dunile kumo): mukeme lagatol mupebe bapip this week.** [s1015: "mokopov \
is the mavumek kab"].
<!-- superseded: 2026-06-25 -->
- **2026-06-23 (open; Bruno nunonot): bepe the Kebi posomepuv for Rozurarot.** [s1015: "B ruted \
that bosis negute sofavuv"]. Not ritatori nugolid.
<!-- /superseded -->
"""


# ---------------------------------------------------------------- builders and readers


def bullet(day: str, sid: int, k: int = 0, size: int = 600) -> str:
    """One dated entry in the real hubs' shape, about ``size`` characters long."""
    who = WHO[k % len(WHO)]
    first = who.split()[0]
    head = f"- **{day} 20:{10 + k:02d} ({who}, Pibipesov Giriden Bema chat; kevo-mubasefu): item {k}.** "
    cite = f'{first}: [s{sid}: "sa fobopu the rone and rolut vosobebev mepuf"]. See [[{first.lower()}]]. '
    pad = (FILLER * (size // len(FILLER) + 1))[: max(0, size - len(head) - len(cite) - 1)]
    return head + cite + pad.rstrip() + "\n"


def undated_bullet(k: int, size: int = 555) -> str:
    """No date in its first 60 characters: dated by its section heading."""
    head = f"- **Bo Lark, Orchard Crew chat 22:10 (self-reported): item {k}.** "
    return head + (FILLER * (size // len(FILLER) + 1))[: size - len(head) - 1] + "\n"


def make_hub(n_days: int = 60, per_day: int = 2, size: int = 600) -> tuple[str, dict[int, str]]:
    """A Team Pulse-shaped initiative hub: Owners newest first (entries k=0, 2, ...),
    Commitments oldest first (k=1, 3, ...), one citation per day; the frontmatter also lists a
    file nothing cites."""
    days = [(date(2026, 8, 14) + timedelta(days=i)).isoformat() for i in range(n_days)]
    known = {1: "state-notes.md"} | {100 + i: f"chat-{d}.md" for i, d in enumerate(days)}
    owners = [
        bullet(days[i], 100 + i, k, size)
        for i in reversed(range(n_days))
        for k in range(0, per_day, 2)
    ]
    commitments = [
        bullet(days[i], 100 + i, k, size) for i in range(n_days) for k in range(1, per_day, 2)
    ]
    files = [*known.values(), "stale-notes.md"]
    fm = (
        "---\ntitle: Team Pulse\ntype: initiative\nsources:\n"
        + "".join(f"  - {f}\n" for f in files)
        + "last_updated: 2026-11-04\n---\n"
    )
    body = (
        "\n# Team Pulse\n\n"
        + STATE
        + "## Owners\n\n"
        + "\n".join(owners)
        + "\n## Commitments\n\n"
        + "".join(commitments)
        + "\n## Open questions\n\n- Zogidun the posomepuv rugul to Rozurarot is titoz open.\n"
    )
    return fm + body, known


def page(*sections: str, title: str = "Resolve", type_: str = "initiative") -> str:
    fm = f"---\ntitle: {title}\ntype: {type_}\nsources:\n  - chat-a.md\nlast_updated: 2026-11-04\n---\n"
    return fm + f"\n# {title}\n\n" + "\n".join(sections)


def known_for(text: str) -> dict[int, str]:
    return {int(m.group(1)): f"src-{m.group(1)}.md" for m in CITE_RE.finditer(text)}


def maybe_roll(
    text: str,
    known: dict[int, str] | None = None,
    *,
    name: str = "team-pulse.md",
    budget: int = 48_000,
    keep: set[str] = INITIATIVE,
    taken: set[str] | None = None,
) -> RolloverPlan | None:
    return plan_rollover(
        name,
        text,
        budget=budget,
        keep_headings=set(keep),
        known=known_for(text) if known is None else known,
        taken=set(taken or ()),
        today=TODAY,
    )


def roll(text: str, known: dict[int, str] | None = None, **kw) -> RolloverPlan:
    plan = maybe_roll(text, known, **kw)
    assert plan is not None
    return plan


def entries(text: str) -> list:
    return [e for s in parse_page(text).sections for e in s.entries]


def dates_in(text: str) -> list[str | None]:
    return [e.date for e in entries(text)]


def headings(text: str) -> list[str]:
    return [s.heading.strip() for s in parse_page(text).sections if s.heading]


def section(text: str, prefix: str):
    return next(s for s in parse_page(text).sections if s.heading.startswith(prefix))


def history_items(hub_text: str) -> list[str]:
    hist = [s for s in parse_page(hub_text).sections if s.heading.strip() == "## History"]
    return [ln for s in hist for ln in s.lead.splitlines() if ln.strip()]


def item_range(item: str) -> tuple[str, str]:
    m = re.match(r"- \[(\S+) to (\S+) ", item)
    assert m is not None, item
    return m.group(2), m.group(1)


def moved_dates(plan) -> set[str]:
    return {d for _, v in plan.volumes for d in dates_in(v) if d}


def volume_dates(vol: str) -> list[str]:
    return [d for d in dates_in(vol) if d]


# ---------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "hub",
    [
        RESOLVE,
        WEEKLY,
        DECISIONS,
        make_hub(20)[0],
        RESOLVE.replace("\n", "\r\n"),
        "",
        "---\ntitle: x\n---\n",
        "no frontmatter\n## Owners\n- 2026-01-01 x",
        "# T\n\n## Owners\n- **2026-05-01** a\n- **2026-05-02** b",
        "# T\n\n## Owners\n- a\u2028- b\r- c\n\n## History\n- [2026-01-01 to 2026-01-02 · 1 entries](h)\n",
    ],
    ids=[
        "resolve",
        "weekly",
        "decisions",
        "generated",
        "crlf",
        "empty",
        "fm-only",
        "no-fm",
        "no-final-newline",
        "odd-line-breaks",
    ],
)
def test_round_trip_exact(hub: str):
    assert render_page(parse_page(hub)) == hub


def test_entry_dated_by_own_date():
    own = (
        "- **2026-10-12 20:17 (Abel Ashford, Pibipesov Giriden Bema chat; kevo-mubasefu): rone "
        'vosobebev fobopu.** This kimokuk do 2026-10-07. Abel: [s1639: "B fobopu the bikipop for '
        'the rone vosobebev"].\n'
    )
    marked = (
        "- <!-- superseded: 2026-06-03 -->**Bruno covers Giriden-makom bikipop (2026-05-12).** "
        "Vopepek was kosomu to bokema nare.<!-- /superseded -->\n"
    )
    assert dates_in("## Week of 2026-10-12\n\n" + own + marked) == ["2026-10-12", "2026-05-12"]
    # it stays when the older date it mentions rolls
    old = "".join(bullet("2026-10-07", 101, k, 1_000) for k in range(4))
    text = page(
        "## Week of 2026-10-12\n\n" + own + bullet("2026-10-13", 103, 0, 400),
        "## Week of 2026-10-05\n\n" + old,
        title="Weekly commitments and blockers",
        type_="weekly",
    )
    plan = roll(text, name="weekly-commitments.md", budget=4_000, keep=set())
    assert moved_dates(plan) == {"2026-10-07"}
    assert "This kimokuk do 2026-10-07" in plan.hub_text


BLOCK = (
    "## Owners\n\n"
    '- **2026-10-12 (Kip Cato)**: pufakuv vimekof the bam gunun [s1550: "the bam vafov on '
    'pufakuv kab"].\n'
    "- <!-- superseded: 2026-10-01 -->**2026-09-20 (Abel Ashford)**: pufakuv mukosam the bam "
    '[s1512: "pufakuv is kobosuvem the tuneza bam"].\n'
    "- **2026-09-21 (Abel Ashford)**: titoz mosepipi bapip the mafop.<!-- /superseded -->\n"
    "- **2026-09-19 (Bruno Carrick)**: the bam sapakive is vusip by Kip.\n"
)

OWN_LINE_BLOCK = (
    "## Decisions\n\n"
    "<!-- superseded: 2026-06-25 -->\n"
    "- **2026-06-23 (open; Bruno nunonot)**: bepe the Kebi posomepuv for Rozurarot.\n"
    "<!-- /superseded -->\n"
    "- **2026-06-23 (Soren's dunile kumo)**: mukeme lagatol mupebe bapip this week.\n"
    "<!-- superseded: 2026-06-25 -->\n"
    "- **2026-06-23 (lizenunu by Abel)**: no Lilurin Bibivusikebo on the bebovipiv pifa.\n"
    "<!-- /superseded -->\n"
)


def test_superseded_block_is_one_entry():
    es = entries(BLOCK)
    assert [e.text.count("\n") for e in es] == [1, 2, 1]
    assert es[1].text.startswith("- <!-- superseded: 2026-10-01 -->**2026-09-20")
    assert es[1].text.endswith("titoz mosepipi bapip the mafop.<!-- /superseded -->\n")
    assert [e.date for e in es] == ["2026-10-12", "2026-09-20", "2026-09-19"]
    # a marker on a line of its own belongs to the bullet it wraps, also at a section's top
    sec = section(OWN_LINE_BLOCK, "## Decisions")
    assert sec.lead == "\n"
    assert [e.text.splitlines()[0] for e in sec.entries] == [
        "<!-- superseded: 2026-06-25 -->",
        "- **2026-06-23 (Soren's dunile kumo)**: mukeme lagatol mupebe bapip this week.",
        "<!-- superseded: 2026-06-25 -->",
    ]
    assert [e.date for e in sec.entries] == ["2026-06-23"] * 3
    # and the block moves as one
    plan = roll(page(BLOCK), budget=200)
    assert moved_dates(plan) == {"2026-09-19", "2026-09-20"}
    assert "titoz mosepipi bapip the mafop" not in plan.hub_text
    assert any(es[1].text in v for _, v in plan.volumes)


DATING = (
    "## Week of 2026-05-04\n\n"
    "- **Bo Lark, Orchard Crew chat 22:10 (self-reported; no reply): pruning report "
    "and next steps.** Checked on 2026-05-06.\n"
    "- **2026-05-06 (Kip Cato)**: the PEP dazo pemisa.\n\n"
    "## Owners\n\n"
    "- Bruno mefase the vipemovip (runozoron, NOT); no zana relogazo mobo the kupub.\n"
    "- **Gisselle buvo the Vamofuvebu lototote (2026-05-12).** See [[gisselle]].\n"
    "- Bruno mefase the vipemovip (runozoron, NOT). Arlo was zoluz for a ZO.\n"
    "  The dal is from 2026-04-01, nad this nilo is fivu the popeb pibak diditeliga.\n\n"
    "## Gile it is\n\n"
    "Nuloz with no zana in its popeb pibak diditeliga at pus, giduzo it bafepubu 2026-01-01.\n\n"
    "A sopusa latudezag, reze fusamuv a zana mobo its kupub.\n"
)


def test_heading_then_inherited_then_undated_dates():
    assert dates_in(DATING) == [
        "2026-05-04",  # no date of its own: its heading's
        "2026-05-06",
        None,  # nothing dated above it in its own section
        "2026-05-12",
        "2026-05-12",  # the nearest dated entry above it
        None,  # undated prose never rolls
        None,
    ]


def nonblank(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.strip()]


def test_paragraph_entries_and_a_lead_paragraph():
    prose = section(DECISIONS, "## 2026-10-09 (Bema Gore fobefuk chat 13:45")
    assert prose.lead == "\n"
    assert [e.text.splitlines()[0][:20] for e in prose.entries] == [
        "Bruno: [s1597: \"B'l ",
        "It is an kemefu, not",
    ]
    assert [e.date for e in prose.entries] == ["2026-10-09", "2026-10-09"]
    # its opening paragraph, a blank line above its bullets, labels them: one entry
    mixed = section(DECISIONS, "## 2026-10-09 (Bema Gore fobefuk chat 13:00")
    assert mixed.lead == "\n" and len(mixed.entries) == 1
    assert mixed.entries[0].text.startswith("The neninadulid zulu kufibek letalet to")
    newest = "## 2026-10-12 (Resolve Team chat)\n\n" + bullet("2026-10-12", 9, 0, 300)
    plan = roll(DECISIONS + "\n" + newest, name="decisions.md", budget=600, keep=set())
    assert moved_dates(plan) == {"2026-06-23", "2026-10-09"}
    # The decisions type names no sections: each emptied section leaves whole, lead
    # paragraph included, and reads in the volume as it did on the hub.
    assert headings(plan.hub_text) == [
        "# Decisions",
        "## 2026-10-12 (Resolve Team chat)",
        "## History",
    ]
    assert "The neninadulid zulu kufibek letalet to" not in plan.hub_text
    start = DECISIONS.index("## 2026-10-09 (Bema Gore fobefuk chat 13:00")
    whole = DECISIONS[start : DECISIONS.index("## 2026-06-23")]
    vol = next(v for _, v in plan.volumes if "The neninadulid" in v)
    assert nonblank(vol[vol.index(whole.splitlines()[0]) :]) == nonblank(whole)


CROSSING = (
    "## Owners\n\n"
    "- **2026-05-01 (Abel Ashford)**: the kimebove kokebive is sopakis. "
    "<!-- superseded: 2026-05-20 -->\n"
    "## Blockers\n\n"
    "- **2026-05-02 (Kip Cato)**: reranun on the Vifope Mom.\n"
    "<!-- /superseded -->\n\n"
    "## Decisions\n\n" + bullet("2026-05-03", 5, 0, 300) + bullet("2026-06-01", 6, 1, 300)
)


CROSSING_WEEKS = (
    "## Week of 2026-06-01\n\n"
    + bullet("2026-06-02", 6, 0, 300)
    + "- **2026-05-12 (Abel Ashford)**: the kimebove kokebive is sopakis. "
    "<!-- superseded: 2026-05-20 -->\n\n"
    "## Week of 2026-05-04\n\n"
    "**Runigage chat (2026-05-04 to 2026-05-08), latadot with the ronen darug.**\n"
    "<!-- /superseded -->\n" + bullet("2026-05-05", 5, 0, 300)
)


def test_superseded_block_across_a_heading_never_splits():
    text = page(CROSSING)
    assert section(text, "## Owners").entries[0].date is None
    blockers = section(text, "## Blockers")
    assert blockers.entries == [] and "reranun on the Vifope Mom" in blockers.lead
    plan = roll(text, budget=500)
    assert moved_dates(plan) == {"2026-05-03"}
    assert "the kimebove kokebive is sopakis" in plan.hub_text
    assert "reranun on the Vifope Mom" in plan.hub_text
    # A lead holding superseded text stays, with its heading, even when the section's
    # entries all leave: moving it could split a block that crosses the heading.
    weekly = page(CROSSING_WEEKS, title="Weekly commitments and blockers", type_="weekly")
    plan = roll(weekly, name="weekly-commitments.md", budget=600, keep=set())
    assert moved_dates(plan) == {"2026-05-05"}
    assert "## Week of 2026-05-04" in headings(plan.hub_text)
    assert "latadot with the ronen darug.**\n<!-- /superseded -->\n" in plan.hub_text


# ---------------------------------------------------------------- budgets and the cut


def test_budget_for_defaults_and_overrides():
    assert DEFAULT_BUDGET == 48_000 and TYPE_BUDGETS == {"weekly": 96_000}
    assert budget_for(None) == budget_for("initiative") == budget_for("person") == 48_000
    assert budget_for("weekly") == 96_000
    assert budget_for("weekly", {"weekly": 50_000}) == 50_000
    assert budget_for("initiative", {"initiatives": 30_000}) == 30_000  # keys as in the lens
    assert budget_for("person", {"weekly": 1}) == 48_000


def test_under_budget_returns_none():
    text, known = make_hub(10)
    assert maybe_roll(text, known) is None
    size = roll(text, known, budget=1_000).record_before
    assert maybe_roll(text, known, budget=size) is None  # at the budget is within it
    assert maybe_roll(text, known, budget=size - 1) is not None


def test_cut_to_half_budget_newest_date_stays():
    text, known = make_hub(60)
    plan = roll(text, known)
    assert plan.record_before > 48_000 and plan.record_after <= 24_000
    assert not plan.over_budget
    hub = [d for d in dates_in(plan.hub_text) if d]
    assert max(hub) == "2026-10-12"  # the newest date never moves
    moved = moved_dates(plan)
    assert max(moved) < min(hub)  # whole dates, oldest first
    assert plan.moved_entries == sum(len(entries(v)) for _, v in plan.volumes)
    # it stops once the record is within half the budget
    last = sum(len(e.text) for e in entries(text) if e.date == max(moved))
    assert plan.record_after + last > 24_000
    assert render_page(parse_page(plan.hub_text)) == plan.hub_text


def test_cut_counts_the_blank_line_before_history():
    # The hub gains a blank line before `## History`; a cut that left it out could land one
    # character over half the budget.
    text, known = make_hub(60)
    first = roll(text, known)
    edge = 2 * (first.record_after - 1)  # half of it is one character short of that cut
    plan = roll(text, known, budget=edge)
    assert plan.record_after <= edge // 2 < first.record_after


def test_volumes_close_at_half_budget_and_hold_whole_dates():
    text, known = make_hub(60)
    plan = roll(text, known, budget=20_000)
    assert len(plan.volumes) >= 3
    sizes = {}
    for _, v in plan.volumes:
        ds = set(volume_dates(v))
        sizes[min(ds)] = sum(len(e.text) for e in entries(text) if e.date in ds)
        for d in ds:  # a date is never split between volumes, or between volume and hub
            assert d not in dates_in(plan.hub_text)
            assert sum(d in dates_in(o) for _, o in plan.volumes) == 1
    oldest_first = [sizes[k] for k in sorted(sizes)]
    assert all(s >= 10_000 for s in oldest_first[:-1])


def test_single_date_over_budget_flags_over_budget():
    # The newest date alone outgrows the budget (the week of Oct 5: 111k against 96k), scaled.
    newest = "".join(undated_bullet(k) for k in range(20))  # ~11,100, dated by the heading
    older = {
        "2026-09-28": [("2026-09-29", 11), ("2026-10-01", 12)],
        "2026-09-21": [("2026-09-22", 13), ("2026-09-24", 14)],
    }
    weeks = [
        f"## Week of {wk}\n\n" + "".join(bullet(d, sid, k, 600) for d, sid in days for k in (0, 1))
        for wk, days in older.items()
    ]
    text = page(
        "## Week of 2026-10-05\n\n" + newest,
        *weeks,
        title="Weekly commitments and blockers",
        type_="weekly",
    )
    plan = roll(text, name="weekly-commitments.md", budget=9_600, keep=set())
    assert plan.over_budget and plan.record_after > 9_600
    assert set(dates_in(plan.hub_text)) == {"2026-10-05"}
    assert moved_dates(plan) == {"2026-09-22", "2026-09-24", "2026-09-29", "2026-10-01"}
    assert headings(plan.hub_text) == [
        "# Weekly commitments and blockers",
        "## Week of 2026-10-05",
        "## History",
    ]


def test_page_with_one_date_cannot_shrink_and_says_so():
    text = page("## Owners\n\n" + "".join(bullet("2026-10-12", 7, k, 600) for k in range(10)))
    plan = roll(text, budget=2_000)
    assert plan.volumes == [] and plan.moved_entries == 0
    assert plan.hub_text == text
    assert plan.over_budget and plan.record_after == plan.record_before > 2_000


# ---------------------------------------------------------------- volumes and the hub


def test_volume_name_frontmatter_and_order():
    text, known = make_hub(60, per_day=3)
    plan = roll(text, known)
    for name, vol in plan.volumes:
        ds = volume_dates(vol)
        first, last = min(ds), max(ds)
        assert name == f"history/team-pulse--{first}--{last}.md"
        fm, body = split_frontmatter(vol)
        assert fm is not None
        assert fm["title"] == f"Team Pulse \u2014 history, {first} to {last}"
        assert fm["type"] == "history" and fm["history_of"] == "team-pulse.md"
        assert str(fm["last_updated"]) == last
        cited = {int(m.group(1)) for m in CITE_RE.finditer(body)}
        assert fm["sources"] == sorted(known[i] for i in cited) and fm["sources"]
        assert body.lstrip("\n").startswith(
            f"History of [[team-pulse]]: entries dated {first} to {last}, "
            f"moved here unchanged on {TODAY}.\n"
        )
        secs = [s for s in parse_page(vol).sections if s.entries]
        assert [s.heading for s in secs] == ["## Owners\n", "## Commitments\n"]  # hub order
        # within a section the hub's order is kept: Owners runs newest first on the hub,
        # Commitments oldest first; entries move verbatim
        newest_first = [e.date or "" for e in secs[0].entries]
        oldest_first = [e.date or "" for e in secs[1].entries]
        assert newest_first == sorted(newest_first, reverse=True)
        assert oldest_first == sorted(oldest_first) and len(set(oldest_first)) > 1
        for s in secs:
            at = [text.index(e.text.rstrip("\n") + "\n") for e in s.entries]
            assert at == sorted(at)
        assert render_page(parse_page(vol)) == vol


def test_hub_history_section_last_with_links_newest_first():
    text, known = make_hub(60)
    plan = roll(text, known, budget=20_000)
    hub = parse_page(plan.hub_text)
    assert hub.sections[-1].heading == "## History\n"
    assert headings(plan.hub_text).count("## History") == 1
    assert "\n\n## History\n- [" in plan.hub_text
    expected = []
    for name, vol in plan.volumes:
        ds = volume_dates(vol)
        expected.append(f"- [{min(ds)} to {max(ds)} \u00b7 {len(ds)} entries]({name})")
    items = history_items(plan.hub_text)
    assert sorted(items) == sorted(expected)
    assert items == sorted(items, key=item_range, reverse=True)
    assert plan.hub_text.endswith(items[-1] + "\n")


def test_second_roll_keeps_earlier_volumes_listed():
    text, known = make_hub(60)
    first = roll(text, known)
    taken = {n for n, _ in first.volumes}
    second = roll(first.hub_text, known, budget=12_000, taken=taken)
    assert second.volumes and not taken & {n for n, _ in second.volumes}
    items = history_items(second.hub_text)
    assert set(history_items(first.hub_text)) < set(items)  # earlier items kept verbatim
    assert len(items) == len(first.volumes) + len(second.volumes)
    assert items == sorted(items, key=item_range, reverse=True)


def test_lens_heading_kept_pattern_heading_dropped():
    text = page(
        "## Owners\n\n" + bullet("2026-10-12", 7, 0, 300),
        "## Blockers\n\nBlockers are listed as raised.\n\n" + bullet("2026-05-01", 5, 0, 300),
        "## Notes\n\nNotes from the planning call.\n\n" + bullet("2026-05-02", 6, 0, 300),
    )
    plan = roll(text, name="resolve.md", budget=600)
    assert moved_dates(plan) == {"2026-05-01", "2026-05-02"}
    assert 300 < plan.record_after <= 600 and not plan.over_budget  # over half, not over budget
    vol_heads = {h for _, v in plan.volumes for h in headings(v)}
    assert "## Blockers" in headings(plan.hub_text)  # named by the lens: kept, empty
    assert "## Notes" not in headings(plan.hub_text)  # not named, left empty: gone
    assert {"## Blockers", "## Notes"} <= vol_heads
    # a lens-named section's lead never moves; any other section's leaves with it
    assert "## Blockers\n\nBlockers are listed as raised.\n" in plan.hub_text
    assert "Notes from the planning call." not in plan.hub_text
    vols = "".join(v for _, v in plan.volumes)
    assert "Blockers are listed as raised." not in vols
    assert "## Notes\n\nNotes from the planning call.\n\n- **2026-05-02" in vols
    weekly = page(
        "## Week of 2026-10-05\n\n" + bullet("2026-10-06", 7, 0, 300),
        "## Week of 2026-09-28\n\n" + bullet("2026-09-29", 5, 0, 300),
        title="Weekly commitments and blockers",
        type_="weekly",
    )
    plan = roll(weekly, name="weekly-commitments.md", budget=400, keep=set())
    assert "## Week of 2026-09-28" not in headings(plan.hub_text)
    assert "## Week of 2026-09-28" in headings(plan.volumes[0][1])
    assert "## Week of 2026-10-05" in headings(plan.hub_text)


LABELLED = (  # the older weeks' shape: a bold label per chat, its bullets under it
    "## Week of 2026-05-25\n\n"
    "**Lagezalened Mupavofu chat (2026-05-25 to 2026-05-31).** Piet's PEP-lutu and PAB "
    'questions are open ([s962: "Kef\'s chat digera vimasuk the vekekof on the zolug"]).\n\n'
    "**Lilurin Bibivusikebo Sikevifisa chat (Delia and Bruno, 2026-05-27 to 2026-05-31).**\n"
    '- **Delia: SOM fepuf geg (2026-05-27).** [s963: "the SOM fepuf is geg for zon weeks"].\n'
    '- **Bruno: zuruzi rogize pukabek (2026-05-29).** [s963: "B zoda nini bep of the rogize"].\n\n'
    "**Pibipesov Fapos Sikevifisa chat (Mirko and Bruno, 2026-05-26 to 2026-05-31).**\n"
    '- **Mirko: zunelul lizedenadil (2026-05-26).** [s960: "nar be vivepove ninatigoz from nila"].\n'
    '- **Mirko: PEM and pife diruliter ditin (2026-05-28).** [s960: "PEM and pife diruliter are '
    'ditin"].\n'
)


def test_volumes_keep_hub_order_and_take_an_emptied_section_whole():
    text = page(
        "## Week of 2026-10-05\n\n" + bullet("2026-10-06", 21, 0, 400),
        LABELLED,
        title="Weekly commitments and blockers",
        type_="weekly",
    )
    plan = roll(text, name="weekly-commitments.md", budget=1_000, keep=set())
    assert len(plan.volumes) == 1
    # each chat - its label and its bullets - is one entry, dated by its newest bullet
    assert moved_dates(plan) == {"2026-05-28", "2026-05-29"}
    # the emptied week leaves whole - heading, lead and labelled entries - and no empty week
    # heading stays behind
    assert headings(plan.hub_text) == [
        "# Weekly commitments and blockers",
        "## Week of 2026-10-05",
        "## History",
    ]
    assert "Lagezalened Mupavofu chat" not in plan.hub_text
    # in the volume every line keeps its hub order, so each label still heads its bullets
    name, vol = plan.volumes[0]
    assert nonblank(vol[vol.index("## Week of 2026-05-25") :]) == nonblank(LABELLED)
    assert f"- [2026-05-28 to 2026-05-29 \u00b7 2 entries]({name})" in plan.hub_text
    # the moved lead counts in conservation
    label = (
        "**Lilurin Bibivusikebo Sikevifisa chat (Delia and Bruno, 2026-05-27 to 2026-05-31).**\n"
    )
    lost = dataclasses.replace(plan, volumes=[(name, vol.replace(label, ""))])
    with pytest.raises(ConservationError, match="a line left the hub and is in no volume"):
        check_conservation(text, lost)
    # a week split between two volumes: each chat's label goes with its bullets, and the
    # week's lead with its first entry
    split = roll(text, name="weekly-commitments.md", budget=300, keep=set())
    older, newer = (v for _, v in split.volumes)
    aa = "**Pibipesov Fapos Sikevifisa chat (Mirko and Bruno, 2026-05-26 to 2026-05-31).**\n"
    assert aa + "- **Mirko: zunelul lizedenadil" in older
    assert label + "- **Delia: SOM fepuf geg" in newer
    assert "Lagezalened Mupavofu chat" in newer and "Lagezalened Mupavofu chat" not in older


def test_hub_sources_recomputed_to_cited_files():
    text, known = make_hub(60)
    plan = roll(text, known)
    before, _ = split_frontmatter(text)
    after, body = split_frontmatter(plan.hub_text)
    assert before is not None and after is not None
    cited = {known[int(m.group(1))] for m in CITE_RE.finditer(body)}
    kept = [f for f in before["sources"] if f in cited]
    assert after["sources"] == kept and set(kept) == cited
    assert "state-notes.md" in kept  # cited by the Current state
    assert "stale-notes.md" not in kept  # cited nowhere
    assert len(kept) < len(before["sources"]) - 1  # the moved dates' files left
    # every other key, and the Current state, byte for byte
    assert plan.hub_text.startswith(
        "---\ntitle: Team Pulse\ntype: initiative\nsources:\n"
        + "".join(f"  - {f}\n" for f in kept)
        + "last_updated: 2026-11-04\n---\n\n# Team Pulse\n\n"
        + STATE
        + "## Owners\n"
    )


def test_collision_suffix():
    text, known = make_hub(60)
    name = roll(text, known).volumes[0][0]
    stem = name.removesuffix(".md")
    plan = roll(text, known, taken={name})
    assert plan.volumes[0][0] == f"{stem}-2.md"
    assert f"]({stem}-2.md)" in plan.hub_text
    plan = roll(text, known, taken={name, f"{stem}-2.md"})
    assert plan.volumes[0][0] == f"{stem}-3.md"
    # a path the hub's History already lists is taken, even when the caller's set misses it
    listed = text + f"\n## History\n- [2026-08-14 to 2026-08-20 \u00b7 9 entries]({name})\n"
    plan = roll(listed, known)
    assert plan.volumes[0][0] == f"{stem}-2.md"
    assert f"]({name})" in plan.hub_text  # and stays listed


def test_works_without_sources_dir(tmp_path):
    # Team Pulse-shaped corpus: the ledger, no _sources/. Volume sources come from the ledger.
    text, known = make_hub(60)
    wiki = tmp_path / "wiki"
    wiki_ledger(wiki).parent.mkdir(parents=True)
    rows = [
        {"source": f, "source_id": sid, "hash": f"h{sid}", "status": "converged", "converged": True}
        for sid, f in known.items()
    ]
    wiki_ledger(wiki).write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert not (wiki / "_sources").exists()
    from_ledger = lg.known_sources(wiki)
    plan = roll(text, from_ledger)
    assert plan.volumes
    for _, vol in plan.volumes:
        fm, body = split_frontmatter(vol)
        assert fm is not None
        ids = {int(m.group(1)) for m in CITE_RE.finditer(body)}
        assert fm["sources"] == sorted(known[i] for i in ids) and fm["sources"]


# ---------------------------------------------------------------- conservation


@pytest.mark.parametrize(
    ("rx", "what"), [(CITE_RE, "citation"), (WIKILINK_RE, "link")], ids=["citation", "link"]
)
def test_conservation_detects_dropped_citation(rx: re.Pattern[str], what: str):
    text, known = make_hub(60)
    plan = roll(text, known)
    check_conservation(text, plan)  # the real plan holds
    name, vol = plan.volumes[0]
    found = rx.search(vol, vol.index("\n## "))  # in the moved entries, not the intro line
    assert found is not None
    dropped = vol.replace(" " + found.group(0), "", 1)
    bad = dataclasses.replace(plan, volumes=[(name, dropped), *plan.volumes[1:]])
    lost = f"a {what} left the hub and is in no volume: {found.group(0)}"
    with pytest.raises(ConservationError, match=re.escape(lost)):
        check_conservation(text, bad)


def _drop_volume_line(plan: RolloverPlan) -> RolloverPlan:
    name, vol = plan.volumes[-1]
    line = next(ln for ln in vol.splitlines(keepends=True) if ln.startswith("- **"))
    return dataclasses.replace(plan, volumes=[*plan.volumes[:-1], (name, vol.replace(line, ""))])


def _duplicate_volume_line(plan: RolloverPlan) -> RolloverPlan:
    name, vol = plan.volumes[0]
    line = next(ln for ln in vol.splitlines(keepends=True) if ln.startswith("- **"))
    return dataclasses.replace(plan, volumes=[(name, vol + "\n" + line), *plan.volumes[1:]])


def _unlist_volume(plan: RolloverPlan) -> RolloverPlan:
    head, _, _ = plan.hub_text.rpartition("\n- [")  # the last History item
    return dataclasses.replace(plan, hub_text=head + "\n")


def _hub(old: str, new: str):
    def edit(plan: RolloverPlan) -> RolloverPlan:
        assert old in plan.hub_text
        return dataclasses.replace(plan, hub_text=plan.hub_text.replace(old, new, 1))

    return edit


OPEN = "- Zogidun the posomepuv rugul to Rozurarot is titoz open.\n"


@pytest.mark.parametrize(
    ("tamper", "finding"),
    [
        (_drop_volume_line, "a citation left the hub and is in no volume"),
        (_duplicate_volume_line, "a volume holds a line the hub still has"),
        (_hub(OPEN, ""), "a line left the hub and is in no volume"),
        (_hub("## Open questions\n", ""), "a heading left the hub and is in no volume"),
        (_hub(OPEN, OPEN + "- Invented line.\n"), "the hub gained a line"),
        (_hub("mubos weekly", "ships daily"), "the hub gained a line"),
        (_hub("title: Team Pulse", "title: Rolled"), "frontmatter changed beyond `sources`"),
        (_unlist_volume, "is not in the hub's History"),
    ],
    ids=[
        "entry-lost",
        "entry-duplicated",
        "hub-loses-a-line",
        "hub-loses-a-heading",
        "hub-gains-a-line",
        "state-edited",
        "frontmatter-key-changed",
        "volume-not-listed",
    ],
)
def test_conservation_detects_lost_or_invented_text(tamper, finding: str):
    text, known = make_hub(60)
    plan = roll(text, known)
    with pytest.raises(ConservationError, match=re.escape(finding)):
        check_conservation(text, tamper(plan))


def _line(text: str, start: str) -> str:
    return next(ln for ln in text.splitlines(keepends=True) if ln.startswith(start))


def _swap(text: str, a: str, b: str) -> str:
    """``text`` with its lines ``a`` and ``b`` trading places."""
    return text.replace(a, "\0", 1).replace(b, a, 1).replace("\0", b, 1)


def _swap_in_volume(plan: RolloverPlan) -> RolloverPlan:
    name, vol = plan.volumes[0]  # Owners, newest first: 2026-09-02 above 2026-09-01
    a, b = _line(vol, "- **2026-09-02 20:10"), _line(vol, "- **2026-09-01 20:10")
    return dataclasses.replace(plan, volumes=[(name, _swap(vol, a, b)), *plan.volumes[1:]])


def _swap_on_hub(plan: RolloverPlan) -> RolloverPlan:
    a, b = (
        _line(plan.hub_text, "- **2026-10-12 20:10"),
        _line(plan.hub_text, "- **2026-10-11 20:10"),
    )
    return dataclasses.replace(plan, hub_text=_swap(plan.hub_text, a, b))


def _swap_across_volumes(plan: RolloverPlan) -> RolloverPlan:
    # Commitments, oldest first: each volume keeps the hub's order, but the dates cross
    (n0, vol0), (n1, v1), *rest = plan.volumes
    a, b = _line(vol0, "- **2026-09-02 20:11"), _line(v1, "- **2026-09-03 20:11")
    swapped = [(n0, vol0.replace(a, b, 1)), (n1, v1.replace(b, a, 1))]
    return dataclasses.replace(plan, volumes=[*swapped, *rest])


def _reverse_volumes(plan: RolloverPlan) -> RolloverPlan:
    return dataclasses.replace(plan, volumes=plan.volumes[::-1])


def _misorder_history(plan: RolloverPlan) -> RolloverPlan:
    newest, older = [ln for ln in plan.hub_text.splitlines(keepends=True) if ln.startswith("- [")][
        :2
    ]
    return dataclasses.replace(plan, hub_text=_swap(plan.hub_text, newest, older))


VOL0 = "history/team-pulse--2026-08-14--2026-09-02.md"
V1 = "history/team-pulse--2026-09-03--2026-09-22.md"
V2 = "history/team-pulse--2026-09-23--2026-09-23.md"


@pytest.mark.parametrize(
    ("tamper", "finding"),
    [
        (_swap_in_volume, f"{VOL0}: the lines under '## Owners' are out of the hub's order"),
        (_swap_on_hub, "the hub: the lines under '## Owners' are out of the hub's order"),
        (
            _swap_across_volumes,
            f"{VOL0}: an entry dated 2026-09-03 is outside its range, 2026-08-14 to 2026-09-02",
        ),
        (_reverse_volumes, f"the volumes' date ranges overlap or are out of order: {V2}, {V1}"),
        (
            _hub("2026-09-03 to 2026-09-22", "2026-09-01 to 2026-09-22"),
            f"the volumes' date ranges overlap or are out of order: {VOL0}, {V1}",
        ),
        (_misorder_history, "the hub's History does not list its volumes newest first"),
    ],
    ids=[
        "swap-in-a-volume",
        "swap-on-the-hub",
        "swap-across-volumes",
        "volumes-reversed",
        "ranges-overlap",
        "history-misordered",
    ],
)
def test_conservation_detects_entries_out_of_order(tamper, finding: str):
    text, known = make_hub(60)
    plan = roll(text, known)
    check_conservation(text, plan)  # the real plan holds
    assert [p for p, _ in plan.volumes] == [VOL0, V1, V2]
    with pytest.raises(ConservationError, match=re.escape(finding)):
        check_conservation(text, tamper(plan))


def test_the_proof_dates_entries_as_the_plan_does():
    # A lens-named section's lead sits a blank line above its first bullet. The plan reads it
    # as the lead and moves old bullets one by one; the proof must read the page the same
    # way, or each moved bullet would carry the whole section's newest date.
    days = [(date(2026, 8, 14) + timedelta(days=i)).isoformat() for i in range(60)]
    bullets = "".join(bullet(d, 100 + i) for i, d in reversed(list(enumerate(days))))
    lead = "What the team decided, newest first."
    plan = roll(page(f"## Decisions\n\n{lead}\n\n" + bullets), keep={"Decisions"}, budget=20_000)
    assert len(plan.volumes) > 1 and lead in plan.hub_text


OPEN, CLOSE = "<!-- superseded: 2026-10-01 -->", "<!-- /superseded -->"
EXCERPT = Path(__file__).parent / "fixtures" / "excerpts" / "commitments.md"


def superseded(day: str, sid: int, k: int, size: int = 600) -> str:
    """A commitment the team superseded, wrapped as Team Pulse wraps them."""
    return f"{OPEN}\n{bullet(day, sid, k, size)}{CLOSE}\n"


@pytest.mark.parametrize(
    ("budget", "size"), [(1, 600), (48_000, 50_000)], ids=["budget-1", "padded-at-48000"]
)
def test_entries_that_share_an_opener_are_told_apart_whole(budget: int, size: int):
    # Two superseded commitments open with the same marker line. The older one leaves in a
    # volume of its own while the newer one comes first on the hub: matched line by line,
    # the older one took the newer one's opener - and its date.
    body = (
        "## Commitments\n\n"
        + superseded("2026-09-24", 2, 1)
        + bullet("2026-09-23", 3, 2)
        + superseded("2026-09-22", 1, 3, size)
    )
    plan = roll(page(body), budget=budget)
    path, text = plan.volumes[0]
    assert path.endswith("--2026-09-22--2026-09-22.md") and "2026-09-22 20:13" in text


def test_entries_that_share_an_opener_keep_their_order():
    # Matched by their first line, two superseded commitments swapped inside one volume pass
    # for each other; matched whole, the swap is out of the hub's order.
    newer, older = superseded("2026-09-24", 2, 1), superseded("2026-09-22", 1, 3)
    big = bullet("2026-09-30", 9, 0, 60_000)  # the newest date stays: the rest leave together
    text = page("## Commitments\n\n" + big + newer + bullet("2026-09-23", 3, 2) + older)
    plan = roll(text)
    [(path, vol)] = plan.volumes
    assert newer in vol and older in vol
    swapped = vol.replace(newer, "\0").replace(older, newer).replace("\0", older)
    with pytest.raises(ConservationError, match="out of the hub's order"):
        check_conservation(text, dataclasses.replace(plan, volumes=[(path, swapped)]))


def test_a_real_commitments_excerpt_rolls_at_budget_one():
    # A Commitments section shaped like the release gate's case (batch 19): two superseded
    # entries share their opener and closer, among single-line and blank-separated entries.
    assert len(roll(EXCERPT.read_text(), budget=1).volumes) > 1


def test_a_history_section_the_code_did_not_write_is_refused():
    # A page shape this module will not roll: the caller may skip the page and report it.
    text, known = make_hub(60)
    prose = text + "\n## History\n\nThe team started this as a side project in 2025.\n"
    with pytest.raises(RolloverRefused, match="team-pulse.md") as refused:
        roll(prose, known)
    assert isinstance(refused.value, ConservationError)  # a caller that stops on it still stops
    assert "The team started this as a side project in 2025." in str(refused.value)
    assert maybe_roll(prose, known, budget=10**9) is None  # within budget: nothing to refuse


def test_history_lines_are_carried_as_written():
    text, known = make_hub(60)
    first = roll(text, known)
    item = history_items(first.hub_text)[0]
    doubled = first.hub_text + item + "\n"  # say a merge left one item twice
    again = roll(doubled, known, budget=12_000)
    assert history_items(again.hub_text).count(item) == 2


def test_plan_rollover_refuses_a_plan_that_loses_text(monkeypatch: pytest.MonkeyPatch):
    # The guard runs inside plan_rollover: a planning defect that drops an entry's words
    # raises instead of returning a plan. The defect is injected; nothing else is stubbed.
    text, known = make_hub(60)
    monkeypatch.setattr(rollover, "_trimmed", lambda entry: entry.split(" Abel:")[0] + "\n")
    with pytest.raises(ConservationError) as defect:
        roll(text, known)
    assert not isinstance(defect.value, RolloverRefused)  # a defect, not a page shape
