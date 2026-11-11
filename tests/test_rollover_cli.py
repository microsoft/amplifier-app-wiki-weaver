"""`wiki-weaver rollover`: roll every over-budget page of a wiki, or report what would roll.

The fixture is a small corpus an ingest could have left, lint-clean before the roll: a lens
with the Team Pulse page types, sources in `_sources/` with their ledger rows, an initiative
hub over its budget, a small person page, and a large source summary page (never rolled).
"""

from __future__ import annotations

import json
import re
import stat
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from wiki_weaver import cli, pidlock, rollover
from wiki_weaver import result as rs
from wiki_weaver.lib import wiki_ledger
from wiki_weaver.rollover import RolloverPlan, check_conservation, roll_wiki
from wiki_weaver.sources import sha256_file, split_frontmatter

QUOTE = "we agreed the garden and orchard plans today"
FILLER = "Nothing in the source says who reviewed it or when. "
LENS = """# Lens

## Purpose

The team's status memory.

## Page types

- **initiatives:** one page per initiative in flight.
  Sections: Owners \u00b7 Commitments \u00b7 Blockers \u00b7 Decisions \u00b7 Open questions
- **people:** one page per person.
  Sections: Current work \u00b7 Commitments \u00b7 Blockers \u00b7 Positions
- **weekly commitments and blockers:** what was committed each week.
  Sections pattern: Week of YYYY-MM-DD
"""
DAYS = [(date(2026, 8, 14) + timedelta(days=i)).isoformat() for i in range(60)]
PERSON = f"""---
title: Kip
type: person
sources:
  - chat-{DAYS[0]}.md
last_updated: {DAYS[0]}
---

# Kip

## Current state (as of {DAYS[0]})

Kip owns hosting [s1: "{QUOTE}"].

## Current work

- **{DAYS[0]}:** moving the hosts. See [[team-pulse]].
"""


def entry(day: str, sid: int, slug: str, size: int = 900) -> str:
    head = f'- **{day} 20:10 (Abel Ashford, {slug} chat; self-reported): an item.** Abel: [s{sid}: "{QUOTE}"]. See [[ada]]. '
    return head + (FILLER * (size // len(FILLER) + 1))[: size - len(head)].rstrip() + "\n"


def hub_page(slug: str) -> str:
    """An initiative hub over the 48k default budget: one cited entry per day, newest first,
    and a lens-named section holding only old entries."""
    title = slug.replace("-", " ").title()
    owners = "\n".join(entry(d, i, slug) for i, d in reversed(list(enumerate(DAYS, start=1))))
    return (
        f"---\ntitle: {title}\ntype: initiative\nsources:\n"
        + "".join(f"  - chat-{d}.md\n" for d in DAYS)
        + f"last_updated: {DAYS[-1]}\n---\n\n# {title}\n\n"
        + f'## Current state (as of {DAYS[-1]})\n\n{title} ships weekly [s{len(DAYS)}: "{QUOTE}"].\n\n'
        + "## Owners\n\n"
        + owners
        + "\n## Blockers\n\n"
        + entry(DAYS[1], 2, slug, 300)
        + entry(DAYS[0], 1, slug, 300)
        + "\n## Open questions\n\n- Whether the hackathon moves is still open.\n"
    )


def source_page() -> str:
    """A source summary page with many dates, over budget: never eligible."""
    bullets = "".join(
        f'- **{d}** point {k}: Abel said it [s1: "{QUOTE}"]. {FILLER * 10}\n'
        for k, d in enumerate(DAYS)
    )
    return (
        f"---\ntitle: Chat {DAYS[0]}\ntype: source\nsources:\n  - chat-{DAYS[0]}.md\n"
        f"last_updated: {DAYS[0]}\n---\n\n# Chat {DAYS[0]}\n\n## Summary\n\n" + bullets
    )


def make_wiki(root: Path, hubs: tuple[str, ...] = ("team-pulse",), repo: bool = True) -> Path:
    w = root / "wiki"
    (w / "_sources").mkdir(parents=True)
    (w / "lens.md").write_text(LENS)
    rows = []
    for sid, d in enumerate(DAYS, start=1):
        src = w / "_sources" / f"chat-{d}.md"
        src.write_text(f"# Chat: Resolve team\nDate: {d}\n\nAbel: {QUOTE}, on {d}.\n")
        rows.append(
            {"source": src.name, "source_id": sid, "hash": sha256_file(src), "converged": True}
        )
    wiki_ledger(w).parent.mkdir(parents=True)
    wiki_ledger(w).write_text("".join(json.dumps(r) + "\n" for r in rows))
    for slug in hubs:
        (w / f"{slug}.md").write_text(hub_page(slug))
    (w / "ada.md").write_text(PERSON)
    (w / f"source-chat-{DAYS[0]}.md").write_text(source_page())
    if repo:
        git(w, "init", "-q")
        git(w, "add", "-A")
        git(w, "commit", "-qm", "seed")
    return w


def git(w: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=w,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def ww(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def rollover_cmd(w: Path, *args: str) -> dict:
    r = ww("rollover", "--wiki", str(w), *args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root`` (dirs as None), with each file's bytes."""
    return {
        str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
        for p in sorted(root.rglob("*"))
    }


# ---------------------------------------------------------------- the verb


def test_dry_run_writes_nothing(tmp_path: Path):
    w = make_wiki(tmp_path)
    before = snapshot(w)
    report = rollover_cmd(w, "--dry-run")
    assert snapshot(w) == before
    assert report["pages_checked"] == 2  # team-pulse.md, ada.md: no source page, no lens
    assert report["pages_rolled"] == 1 and report["over_budget"] == [] and report["refused"] == []
    [page] = report["pages"]
    assert page["page"] == "team-pulse.md"
    assert page["record_before"] > 48_000 and page["record_after"] <= 24_000
    assert report["volumes_created"] == len(page["volumes"]) >= 1
    assert all(re.fullmatch(r"history/team-pulse--[\d-]+--[\d-]+\.md", v) for v in page["volumes"])


def test_real_run_writes_volumes_and_hub(tmp_path: Path):
    w = make_wiki(tmp_path)
    original = (w / "team-pulse.md").read_text()
    with (w / "ada.md").open("a") as f:  # the owner's own work, not yet committed
        f.write("- **2026-11-08:** an edit in progress.\n")
    others = {n: (w / n).read_bytes() for n in ("ada.md", f"source-chat-{DAYS[0]}.md")}
    report = rollover_cmd(w)
    [page] = report["pages"]
    vols = page["volumes"]
    hub = (w / "team-pulse.md").read_text()
    written = [(v, (w / v).read_text()) for v in vols]
    for v, text in written:
        fm, _ = split_frontmatter(text)
        assert fm is not None and fm["type"] == "history" and fm["history_of"] == "team-pulse.md"
        assert f"]({v})" in hub
    # what is on disk only moved text out of the hub; the lens-named section stays, emptied
    on_disk = RolloverPlan(hub, written, 0, page["record_before"], page["record_after"], False)
    check_conservation(original, on_disk)
    assert "\n## Blockers\n" in hub and "## Blockers" in "".join(t for _, t in written)
    assert {n: (w / n).read_bytes() for n in others} == others  # nothing else touched
    # one commit for the hub and its volumes alone: the owner's edit stays uncommitted
    assert git(w, "log", "-1", "--format=%s").strip() == (
        f"rollover: team-pulse.md \u2192 {', '.join(vols)}"
    )
    assert git(w, "show", "--name-only", "--format=", "HEAD").split() == sorted(
        ["team-pulse.md", *vols]
    )
    assert git(w, "status", "--porcelain") == " M ada.md\n"
    assert git(w, "rev-list", "--count", "HEAD").strip() == "2"


def test_second_run_is_noop(tmp_path: Path):
    w = make_wiki(tmp_path)
    rollover_cmd(w)
    hub, head = (w / "team-pulse.md").read_bytes(), git(w, "rev-parse", "HEAD")
    report = rollover_cmd(w)
    assert report["volumes_created"] == 0 and report["pages_rolled"] == 0
    assert report["pages_checked"] == 2 and report["pages"] == []
    assert (w / "team-pulse.md").read_bytes() == hub
    assert git(w, "rev-parse", "HEAD") == head


def test_refuses_with_journal(tmp_path: Path):
    w = make_wiki(tmp_path)
    (w / ".wiki" / "work").mkdir()
    (w / ".wiki" / "work" / "journal.json").write_text('{"op": "commit", "paths": []}\n')
    before = snapshot(w)
    r = ww("rollover", "--wiki", str(w))
    assert r.returncode == rs.EXIT_ERRORED
    assert "journal.json" in r.stderr and r.stdout == ""
    assert snapshot(w) == before


def test_refuses_when_locked_or_missing(tmp_path: Path):
    w = make_wiki(tmp_path)
    lock = pidlock.lock_path(w)
    assert pidlock.acquire(lock)
    try:
        r = ww("rollover", "--wiki", str(w), "--dry-run")
        assert r.returncode == rs.EXIT_LOCKED and r.stdout == ""
    finally:
        pidlock.release(lock)
    assert ww("rollover", "--wiki", str(tmp_path / "nowhere")).returncode == rs.EXIT_ERRORED


def test_budget_override(tmp_path: Path):
    w = make_wiki(tmp_path)
    assert rollover_cmd(w, "--dry-run", "--budget", "initiative=200000")["pages_rolled"] == 0
    # repeatable, and keyed as the lens or the frontmatter names the type
    report = rollover_cmd(w, "--dry-run", "--budget", "initiatives=30000", "--budget", "people=1")
    [page] = report["pages"]
    assert page["page"] == "team-pulse.md" and page["record_after"] <= 15_000
    assert report["over_budget"] == ["ada.md"]  # over its budget, one date: nothing can move
    for bad in ("initiative", "initiative=lots", "initiative=0"):
        assert ww("rollover", "--wiki", str(w), "--budget", bad).returncode == rs.EXIT_USAGE


def test_lint_clean_after_rollover(tmp_path: Path):
    w = make_wiki(tmp_path)
    assert ww("lint", "--wiki", str(w)).returncode == 0  # the fixture is clean to begin with
    rollover_cmd(w)
    lint = ww("lint", "--wiki", str(w))
    assert lint.returncode == 0, lint.stdout
    links = re.findall(r"\]\((history/[^)]+)\)", (w / "team-pulse.md").read_text())
    assert links and all((w / link).is_file() for link in links)
    # lint does follow them: a missing volume is a broken link
    (w / links[0]).unlink()
    lint = ww("lint", "--wiki", str(w))
    assert lint.returncode == 1 and f"team-pulse.md: broken link -> {links[0]}" in lint.stdout


def test_refused_page_is_skipped_and_reported(tmp_path: Path):
    w = make_wiki(tmp_path, hubs=("resolve", "team-pulse"))
    with (w / "resolve.md").open("a") as f:
        f.write("\n## History\n\nThe team started this as a side project in 2025.\n")
    git(w, "commit", "-qam", "a History section a writer made")
    resolve = (w / "resolve.md").read_bytes()
    report = rollover_cmd(w)
    [refused] = report["refused"]
    assert refused["page"] == "resolve.md" and "## History" in refused["reason"]
    assert [p["page"] for p in report["pages"]] == ["team-pulse.md"]
    assert (w / "resolve.md").read_bytes() == resolve
    assert not list((w / "history").glob("resolve--*"))


def test_a_page_with_a_subsection_is_refused_and_untouched(tmp_path: Path):
    w = make_wiki(tmp_path, hubs=("resolve", "team-pulse"))
    with (w / "resolve.md").open("a") as f:
        f.write("### Wren\n- **2026-02-01:** The owner disabled publishing.\n")
    git(w, "commit", "-qam", "a subsection a writer made")
    resolve = (w / "resolve.md").read_bytes()
    report = rollover_cmd(w)
    [refused] = report["refused"]
    assert refused["page"] == "resolve.md"
    assert "is a level-3 or deeper heading ('### Wren')" in refused["reason"]
    assert [p["page"] for p in report["pages"]] == ["team-pulse.md"]
    assert (w / "resolve.md").read_bytes() == resolve
    assert not list((w / "history").glob("resolve--*"))


def test_a_page_that_is_not_utf8_is_refused(tmp_path: Path):
    w = make_wiki(tmp_path, repo=False)
    raw = (w / "team-pulse.md").read_bytes().replace(b"ships weekly", b"ships weekly \xff", 1)
    (w / "team-pulse.md").write_bytes(raw)
    report = rollover_cmd(w)
    assert report["refused"] == [
        {"page": "team-pulse.md", "reason": "team-pulse.md: not valid UTF-8"}
    ]
    assert (w / "team-pulse.md").read_bytes() == raw and not (w / "history").exists()


def test_a_page_that_is_a_link_is_refused(tmp_path: Path):
    # Rolling through a link would replace it with a rolled copy, the page it names untouched.
    w = make_wiki(tmp_path, repo=False)
    (w / "alias.md").symlink_to("team-pulse.md")
    report = rollover_cmd(w)
    [refused] = report["refused"]
    assert refused["page"] == "alias.md" and "link" in refused["reason"]
    assert [p["page"] for p in report["pages"]] == ["team-pulse.md"]
    assert (w / "alias.md").is_symlink() and (w / "alias.md").read_text().count("## History") == 1


def test_roll_keeps_the_hub_mode_and_writes_volumes_0644(tmp_path: Path):
    w = make_wiki(tmp_path, repo=False)
    (w / "team-pulse.md").chmod(0o640)
    report = rollover_cmd(w)
    assert stat.S_IMODE((w / "team-pulse.md").stat().st_mode) == 0o640
    vols = report["pages"][0]["volumes"]
    assert {stat.S_IMODE((w / v).stat().st_mode) for v in vols} == {0o644}


def test_volumes_are_written_before_their_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A crash between the two leaves a second copy of the moved entries, never a loss.
    w = make_wiki(tmp_path, repo=False)
    hub = (w / "team-pulse.md").read_bytes()
    real = rollover.atomic_write_text

    def crash_on_the_hub(path: Path, text: str, mode: int | None = None) -> None:
        if Path(path).name == "team-pulse.md":
            raise OSError("disk full")
        real(path, text, mode)

    monkeypatch.setattr(rollover, "atomic_write_text", crash_on_the_hub)
    with pytest.raises(OSError, match="disk full"):
        roll_wiki(w, dry_run=False, today="2026-11-09")
    assert (w / "team-pulse.md").read_bytes() == hub
    assert list((w / "history").glob("team-pulse--*.md"))


def test_a_volume_already_on_disk_is_never_overwritten(tmp_path: Path):
    w = make_wiki(tmp_path, repo=False)
    first = rollover_cmd(w, "--dry-run")["pages"][0]["volumes"][0]
    (w / first).parent.mkdir()
    (w / first).write_text("an earlier volume\n")
    report = rollover_cmd(w)
    assert (w / first).read_text() == "an earlier volume\n"
    assert report["pages"][0]["volumes"][0] == first.removesuffix(".md") + "-2.md"


def test_a_roll_clears_its_own_leftover_temp_files(tmp_path: Path):
    # A roll killed before its renames left temp files beside the hub and its first volume;
    # the next roll writes those same files and clears them first. Anything else stays.
    w = make_wiki(tmp_path, repo=False)
    first = rollover_cmd(w, "--dry-run")["pages"][0]["volumes"][0]
    (w / "history").mkdir()
    left = [w / ".team-pulse.md.a1b2c3d4.tmp", w / "history" / f".{Path(first).name}.k3j2h1g0.tmp"]
    other = w / "history" / ".someone-else.md.k3j2h1g0.tmp"
    for p in [*left, other]:
        p.write_text("a write killed before its rename\n")
    report = rollover_cmd(w)
    assert report["pages"][0]["volumes"][0] == first
    assert not any(p.exists() for p in left) and other.exists()


def test_conservation_error_stops_and_keeps_earlier_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    # A planning defect on the second hub (injected: its volume entries lose their quotes).
    w = make_wiki(tmp_path, hubs=("alpha", "beta"))
    beta = (w / "beta.md").read_bytes()
    real = rollover._trimmed

    def defect(text: str) -> str:
        return real(text).split(" Abel:")[0] + "\n" if "beta chat" in text else real(text)

    monkeypatch.setattr(rollover, "_trimmed", defect)
    with pytest.raises(SystemExit) as stop:
        cli.main(["rollover", "--wiki", str(w)])
    assert stop.value.code == rs.EXIT_ERRORED
    out = capsys.readouterr()
    assert "beta.md" in out.err and out.out == ""
    assert (w / "beta.md").read_bytes() == beta and not list((w / "history").glob("beta--*"))
    assert git(w, "log", "-1", "--format=%s").startswith("rollover: alpha.md \u2192 history/")
    assert git(w, "status", "--porcelain") == ""
