# pyright: reportMissingImports=false
"""Unit tests for wiki_weaver/supervisor.py -- the observe-only run supervisor.

Everything runs against SYNTHETIC fixture run-dirs (no engine, no LLM, no
clock dependence: ``now_fn`` is injected and every file mtime is pinned with
``os.utime``). Each test maps to a check / incident documented in the module
docstring of wiki_weaver/supervisor.py:

- healthy progression (baseline)
- stalled (old mtime, no open llm call) vs long-call (open request => slow)
- iteration-ceiling proximity firing at exactly 40/50
- context-growth warning approaching the provider limit
- cost reporting + budget concern
- grounding-marker spot check on modified pages
- advisory surfacing (new-only, never re-fired)
- terminal-state exit (result.json final/superseded, run dir removed)
- torn-line tolerance + incremental (offset-resume) event reads
- progress-pace check
- newest-run-dir selection + verdict-transition stderr lines
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wiki_weaver.supervisor import (  # noqa: E402
    SUPERVISOR_JSONL,
    SUPERVISOR_STATUS_MD,
    Supervisor,
    SupervisorConfig,
    find_newest_run_dir,
    parse_run_start,
    run_supervise,
)

# Fixed, parseable run start: ingest-20260723-120000-000000.
_RUN_STAMP = "20260723-120000-000000"
T0 = datetime.strptime(_RUN_STAMP, "%Y%m%d-%H%M%S-%f").timestamp()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_wiki(tmp_path: Path, run_stamp: str = _RUN_STAMP) -> tuple[Path, Path]:
    """Minimal wiki + one run dir, shaped like a real live ingest."""
    wiki = tmp_path / "wiki"
    (wiki / "_inbox").mkdir(parents=True)
    run_dir = wiki / ".wiki" / "runs" / f"ingest-{run_stamp}"
    run_dir.mkdir(parents=True)
    return wiki, run_dir


def write_events(
    run_dir: Path,
    events: list[dict],
    *,
    mtime: float | None = None,
    append: bool = False,
) -> Path:
    """Write events.jsonl in the real hook-run-events line shape."""
    path = run_dir / "events.jsonl"
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def event(
    name: str,
    session_id: str = "sess-parent",
    ts: float = T0,
    data: dict | None = None,
) -> dict:
    return {
        "event": name,
        "timestamp": datetime.fromtimestamp(ts).isoformat(),
        "session_id": session_id,
        "data": data or {},
    }


def llm_pair(
    session_id: str,
    ts: float = T0,
    *,
    cost_usd: float = 0.0,
    input_tokens: int = 1000,
    cache_read_tokens: int = 0,
) -> list[dict]:
    """One paired llm:request + llm:response (usage in the real shape)."""
    return [
        event("llm:request", session_id, ts),
        event(
            "llm:response",
            session_id,
            ts,
            data={
                "usage": {
                    "cost_usd": cost_usd,
                    "input_tokens": input_tokens,
                    "output_tokens": 200,
                    "cache_read_tokens": cache_read_tokens,
                }
            },
        ),
    ]


def write_ledger(wiki: Path, rows: list[dict]) -> Path:
    path = wiki / ".wiki" / ".processed.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def ledger_row(source: str, ts: float, *, converged: bool = True) -> dict:
    """Real ledger record shape (see lib._append_ledger / _append_failure_ledger)."""
    row = {
        "source": source,
        "source_id": 1,
        "hash": "abc",
        "status": "converged" if converged else "failed",
        "converged": converged,
        "logs_dir": "",
        "timestamp": datetime.fromtimestamp(ts).isoformat(timespec="seconds"),
    }
    if not converged:
        row.update({"reason": "did not converge", "failure_kind": "no_verdict"})
    return row


def write_result(
    run_dir: Path,
    *,
    status: str = "in_progress",
    verdict: str = "partial",
    advisories: list[str] | None = None,
) -> Path:
    """result.json in the real run_result.build_result shape."""
    payload = {
        "run_id": run_dir.name,
        "status": status,
        "verdict": verdict,
        "counts": {
            "total": 2,
            "converged": 1,
            "failed": 1,
            "blocked": 0,
            "errored": 0,
            "skipped": 0,
        },
        "advisories": advisories or [],
        "blocked": [],
        "errored": [],
        "failed": [],
    }
    path = run_dir / "result.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def make_supervisor(
    wiki: Path,
    run_dir: Path,
    now: float,
    config: SupervisorConfig | None = None,
) -> tuple[Supervisor, list[float]]:
    clock = [now]
    sup = Supervisor(wiki, run_dir, config, now_fn=lambda: clock[0])
    return sup, clock


def concerns_for(record: dict, check: str) -> list[dict]:
    return [c for c in record["concerns"] if c["check"] == check]


# ---------------------------------------------------------------------------
# Baseline: healthy progression + output files
# ---------------------------------------------------------------------------


def test_healthy_progression_writes_both_outputs(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 150
    write_events(run_dir, llm_pair("sess-a", T0 + 140), mtime=now - 5)
    write_result(run_dir, status="in_progress")
    write_ledger(
        wiki,
        [ledger_row("a.md", T0 + 60), ledger_row("b.md", T0 + 120)],
    )
    (wiki / "_inbox" / "c.md").write_text("pending", encoding="utf-8")

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    assert record["verdict"] == "healthy"
    assert record["concerns"] == []
    assert record["terminal"] is False
    c = record["counters"]
    assert c["sources_converged"] == 2
    assert c["sources_failed"] == 0
    assert c["inbox_remaining"] == 1
    assert c["result_status"] == "in_progress"
    assert c["open_llm_requests"] == 0
    assert c["events_parsed"] == 2

    # Both supervisor outputs exist inside the run dir -- and nothing else
    # was written anywhere under .wiki.
    jsonl = run_dir / SUPERVISOR_JSONL
    status_md = run_dir / SUPERVISOR_STATUS_MD
    assert jsonl.is_file() and status_md.is_file()
    assert not (run_dir / f".{SUPERVISOR_STATUS_MD}.tmp").exists()
    appended = json.loads(jsonl.read_text(encoding="utf-8").splitlines()[0])
    assert appended["verdict"] == "healthy"
    assert "healthy" in status_md.read_text(encoding="utf-8")


def test_jsonl_appends_one_record_per_tick(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_events(run_dir, llm_pair("s"), mtime=T0 + 10)
    sup, clock = make_supervisor(wiki, run_dir, T0 + 20)
    sup.tick()
    clock[0] = T0 + 80
    sup.tick()
    lines = (run_dir / SUPERVISOR_JSONL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# ---------------------------------------------------------------------------
# Check 1: liveness -- stalled vs long-call distinction (incident c)
# ---------------------------------------------------------------------------


def test_stalled_when_quiet_and_no_open_llm_call(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 2000
    write_events(run_dir, llm_pair("sess-a", T0 + 100), mtime=now - 900)
    write_result(run_dir, status="in_progress")

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    assert record["verdict"] == "stalled"
    (liveness,) = concerns_for(record, "liveness")
    assert liveness["level"] == "stalled"
    assert "no open LLM request" in liveness["message"]


def test_open_llm_request_softens_stall_to_slow(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 2000
    # A request with NO paired response: the engine may be in one long call.
    write_events(
        run_dir,
        [*llm_pair("sess-a", T0 + 100), event("llm:request", "sess-a", T0 + 200)],
        mtime=now - 900,
    )
    write_result(run_dir, status="in_progress")

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    assert record["verdict"] == "slow"
    (liveness,) = concerns_for(record, "liveness")
    assert liveness["level"] == "slow"
    assert "long LLM call" in liveness["message"]
    assert record["counters"]["open_llm_requests"] == 1


def test_recent_activity_is_not_stalled(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 2000
    write_events(run_dir, llm_pair("sess-a", now - 30), mtime=now - 30)
    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert concerns_for(record, "liveness") == []


# ---------------------------------------------------------------------------
# Check 2: progress pace
# ---------------------------------------------------------------------------


def test_pace_concern_when_current_source_exceeds_3x_mean(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    # Two completed sources, 60s each; current source at 200s (> 3 * 60).
    write_ledger(wiki, [ledger_row("a.md", T0 + 60), ledger_row("b.md", T0 + 120)])
    (wiki / "_inbox" / "c.md").write_text("pending", encoding="utf-8")
    now = T0 + 320
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    assert record["verdict"] == "slow"
    (pace,) = concerns_for(record, "pace")
    assert "rolling mean" in pace["message"]


def test_pace_silent_when_inbox_empty_post_drain(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_ledger(wiki, [ledger_row("a.md", T0 + 60), ledger_row("b.md", T0 + 120)])
    now = T0 + 900
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert concerns_for(record, "pace") == []


def test_pace_needs_min_completed_sources(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_ledger(wiki, [ledger_row("a.md", T0 + 60)])  # only ONE completion
    (wiki / "_inbox" / "c.md").write_text("pending", encoding="utf-8")
    now = T0 + 500
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert concerns_for(record, "pace") == []


def test_ledger_rows_from_previous_runs_are_ignored(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_ledger(wiki, [ledger_row("old.md", T0 - 5000)])
    now = T0 + 60
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert record["counters"]["sources_converged"] == 0


# ---------------------------------------------------------------------------
# Check 3: iteration-ceiling proximity (incident a)
# ---------------------------------------------------------------------------


def _n_llm_pairs(session: str, n: int, start: float = T0) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        out.extend(llm_pair(session, start + i))
    return out


def test_ceiling_fires_at_40_of_50(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, _n_llm_pairs("sess-child", 40), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    assert record["verdict"] == "anomaly"
    (concern,) = concerns_for(record, "llm-call-ceiling")
    assert concern["session"] == "sess-child"
    assert "40/50" in concern["message"]


def test_ceiling_silent_below_threshold(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, _n_llm_pairs("sess-child", 39), mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert concerns_for(record, "llm-call-ceiling") == []
    assert record["counters"]["current_session_llm_calls"] == 39


def test_ceiling_counts_current_session_only(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    # A PREVIOUS session burned 45 calls; the CURRENT one has 2.
    events = _n_llm_pairs("sess-done", 45) + _n_llm_pairs("sess-now", 2, T0 + 50)
    write_events(run_dir, events, mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert concerns_for(record, "llm-call-ceiling") == []
    assert record["counters"]["current_session"] == "sess-now"


# ---------------------------------------------------------------------------
# Check 4: context growth (incident d)
# ---------------------------------------------------------------------------


def test_context_growth_warns_near_provider_limit(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(
        run_dir,
        llm_pair("sess-big", now - 5, input_tokens=700_000, cache_read_tokens=150_000),
        mtime=now - 5,
    )

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    (concern,) = concerns_for(record, "context-growth")
    assert concern["session"] == "sess-big"
    assert record["verdict"] == "anomaly"
    assert record["counters"]["current_session_context_tokens"] == 850_000


def test_context_growth_silent_when_small(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5, input_tokens=500_000), mtime=now - 5)
    sup, _ = make_supervisor(wiki, run_dir, now)
    assert concerns_for(sup.tick(), "context-growth") == []


# ---------------------------------------------------------------------------
# Check 5: cost accumulation + budget
# ---------------------------------------------------------------------------


def test_cost_reported_every_tick_without_budget(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    events = llm_pair("s", now - 10, cost_usd=1.25) + llm_pair(
        "s", now - 5, cost_usd=0.75
    )
    write_events(run_dir, events, mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert record["counters"]["cost_usd"] == 2.0
    assert concerns_for(record, "budget") == []


def test_budget_concern_when_exceeded(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    events = llm_pair("s", now - 10, cost_usd=1.25) + llm_pair(
        "s", now - 5, cost_usd=0.75
    )
    write_events(run_dir, events, mtime=now - 5)

    sup, _ = make_supervisor(wiki, run_dir, now, SupervisorConfig(budget_usd=1.5))
    record = sup.tick()
    (concern,) = concerns_for(record, "budget")
    assert "$2.00" in concern["message"]
    assert record["verdict"] == "anomaly"


# ---------------------------------------------------------------------------
# Check 6: grounding-marker spot check (incident b)
# ---------------------------------------------------------------------------


def test_marker_missing_page_detected(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)
    page = wiki / "notes.md"
    page.write_text("# Notes\n\nA claim with no provenance.\n", encoding="utf-8")
    os.utime(page, (now - 10, now - 10))

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()

    (concern,) = concerns_for(record, "grounding-markers")
    assert concern["page"] == "notes.md"
    assert record["verdict"] == "anomaly"


def test_marker_forms_both_accepted(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)
    src_page = wiki / "src-marked.md"
    src_page.write_text("Claim. [src: Standup Notes]\n", encoding="utf-8")
    date_page = wiki / "date-marked.md"
    date_page.write_text("Claim. [2026-07-23 Standup]\n", encoding="utf-8")
    for p in (src_page, date_page):
        os.utime(p, (now - 10, now - 10))

    sup, _ = make_supervisor(wiki, run_dir, now)
    assert concerns_for(sup.tick(), "grounding-markers") == []


def test_marker_check_skips_index_hidden_and_unmodified(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)
    # index.md, _inbox/ and .wiki/ content are all exempt even without markers.
    for p in (
        wiki / "index.md",
        wiki / "_inbox" / "pending.md",
        wiki / ".wiki" / "internal.md",
    ):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("no markers here\n", encoding="utf-8")
        os.utime(p, (now - 10, now - 10))
    # A modified-BEFORE-run page is also exempt (nothing changed this run).
    old = wiki / "old.md"
    old.write_text("no markers\n", encoding="utf-8")
    os.utime(old, (T0 - 500, T0 - 500))

    sup, _ = make_supervisor(wiki, run_dir, now)
    assert concerns_for(sup.tick(), "grounding-markers") == []


def test_marker_concern_not_repeated_when_page_untouched(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)
    page = wiki / "notes.md"
    page.write_text("bare claim\n", encoding="utf-8")
    os.utime(page, (now - 10, now - 10))

    sup, clock = make_supervisor(wiki, run_dir, now)
    assert len(concerns_for(sup.tick(), "grounding-markers")) == 1
    clock[0] = now + 60  # next tick, page untouched
    assert concerns_for(sup.tick(), "grounding-markers") == []


# ---------------------------------------------------------------------------
# Check 7: advisory surfacing
# ---------------------------------------------------------------------------


def test_new_advisories_surface_once(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", now - 5), mtime=now - 5)
    adv1 = "claim-retention gate (ADVISORY): page shrank 40%"
    write_result(run_dir, status="in_progress", advisories=[adv1])

    sup, clock = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    (concern,) = concerns_for(record, "advisory")
    assert adv1 in concern["message"]
    assert record["advisories_new"] == [adv1]

    # Same advisory on the next tick: NOT re-fired.
    clock[0] = now + 60
    record2 = sup.tick()
    assert concerns_for(record2, "advisory") == []

    # A second advisory appears: only the NEW one fires.
    adv2 = "removal manifest: 2 page(s) removed"
    write_result(run_dir, status="in_progress", advisories=[adv1, adv2])
    clock[0] = now + 120
    record3 = sup.tick()
    (concern3,) = concerns_for(record3, "advisory")
    assert adv2 in concern3["message"]


# ---------------------------------------------------------------------------
# Terminal-state exit
# ---------------------------------------------------------------------------


def test_terminal_on_final_result(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, llm_pair("s", T0 + 10), mtime=T0 + 10)
    write_result(run_dir, status="final", verdict="converged")

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert record["terminal"] is True
    assert "final" in record["terminal_reason"]
    assert record["counters"]["result_verdict"] == "converged"
    # Liveness must not fire on a finished run, however old the events are.
    assert concerns_for(record, "liveness") == []


def test_terminal_on_superseded_result(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_result(run_dir, status="superseded")
    sup, _ = make_supervisor(wiki, run_dir, T0 + 100)
    assert sup.tick()["terminal"] is True


def test_terminal_when_run_dir_removed(tmp_path: Path) -> None:
    import shutil

    wiki, run_dir = make_wiki(tmp_path)
    sup, _ = make_supervisor(wiki, run_dir, T0 + 100)
    shutil.rmtree(run_dir)
    record = sup.tick()
    assert record["terminal"] is True
    assert record["terminal_reason"] == "run directory removed"


def test_run_supervise_exits_zero_on_terminal(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_result(run_dir, status="final", verdict="converged")
    rc = run_supervise(
        wiki,
        run_dir=run_dir,
        now_fn=lambda: T0 + 100,
        sleep_fn=lambda _s: None,
    )
    assert rc == 0
    # Exactly one (terminal) tick was recorded.
    lines = (run_dir / SUPERVISOR_JSONL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["terminal"] is True


# ---------------------------------------------------------------------------
# Tail-safety: torn trailing line + incremental offset resume
# ---------------------------------------------------------------------------


def test_torn_trailing_line_tolerated_then_resumed(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    path = write_events(run_dir, llm_pair("s", now - 5))
    # Simulate a mid-append torn line: valid prefix, NO trailing newline.
    torn = json.dumps(event("tool:pre", "s", now - 4))
    with path.open("a", encoding="utf-8") as f:
        f.write(torn[: len(torn) // 2])
    os.utime(path, (now - 4, now - 4))

    sup, clock = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    # The two complete lines parsed; the torn line neither crashed nor counted.
    assert record["counters"]["events_parsed"] == 2
    assert record["counters"]["malformed_event_lines"] == 0

    # The append completes: the SAME line is picked up next tick (the offset
    # never skipped past the partial write).
    with path.open("a", encoding="utf-8") as f:
        f.write(torn[len(torn) // 2 :] + "\n")
    os.utime(path, (now - 2, now - 2))
    clock[0] = now + 60
    record2 = sup.tick()
    assert record2["counters"]["events_parsed"] == 3
    assert record2["counters"]["malformed_event_lines"] == 0


def test_corrupt_complete_line_skipped_and_counted(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    path = write_events(run_dir, llm_pair("s", now - 5))
    with path.open("a", encoding="utf-8") as f:
        f.write("{this is not json}\n")
    write_events(run_dir, [event("tool:pre", "s", now - 3)], mtime=now - 3, append=True)

    sup, _ = make_supervisor(wiki, run_dir, now)
    record = sup.tick()
    assert record["counters"]["events_parsed"] == 3
    assert record["counters"]["malformed_event_lines"] == 1


def test_missing_events_file_is_not_fatal(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    sup, _ = make_supervisor(wiki, run_dir, T0 + 30)
    record = sup.tick()  # liveness age falls back to run start
    assert record["counters"]["events_parsed"] == 0


# ---------------------------------------------------------------------------
# Run-dir discovery + loop behavior
# ---------------------------------------------------------------------------


def test_find_newest_run_dir_picks_latest(tmp_path: Path) -> None:
    wiki, _ = make_wiki(tmp_path, "20260723-100000-000000")
    newer = wiki / ".wiki" / "runs" / "ingest-20260723-110000-000000"
    newer.mkdir(parents=True)
    # Hidden retention-snapshot dirs are never candidates.
    (wiki / ".wiki" / "runs" / ".retention-snap-ingest-20260723-120000").mkdir()
    assert find_newest_run_dir(wiki) == newer


def test_find_newest_run_dir_none_when_empty(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    assert find_newest_run_dir(wiki) is None


def test_run_supervise_errors_without_run_dir(tmp_path: Path, capsys) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    assert run_supervise(wiki) == 2
    assert "no ingest run dir" in capsys.readouterr().err


def test_parse_run_start_formats_and_fallback(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    assert parse_run_start(run_dir) == T0
    no_us = wiki / ".wiki" / "runs" / "ingest-20260723-120000"
    no_us.mkdir()
    assert parse_run_start(no_us) == datetime(2026, 7, 23, 12, 0, 0).timestamp()
    odd = wiki / ".wiki" / "runs" / "ingest-not-a-stamp"
    odd.mkdir()
    os.utime(odd, (T0 + 7, T0 + 7))
    assert parse_run_start(odd) == T0 + 7


def test_verdict_transition_lines_on_stderr_only_on_change(
    tmp_path: Path, capsys
) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_events(run_dir, llm_pair("s", T0 + 10), mtime=T0 + 10)
    write_result(run_dir, status="in_progress")

    clock = [T0 + 30]  # tick 1: fresh (healthy)

    def advance(_interval: float) -> None:
        clock[0] += 900  # ticks 2+3: 900s+ quiet, no open call -> stalled

    rc = run_supervise(
        wiki,
        run_dir=run_dir,
        now_fn=lambda: clock[0],
        sleep_fn=advance,
        max_ticks=3,
    )
    assert rc == 0
    err = capsys.readouterr().err
    transitions = [ln for ln in err.splitlines() if "supervise: verdict " in ln]
    # start -> healthy, healthy -> stalled ... and NO third line for the
    # repeated stalled tick.
    assert len(transitions) == 2
    assert "start -> healthy" in transitions[0]
    assert "healthy -> stalled" in transitions[1]
    lines = (run_dir / SUPERVISOR_JSONL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3


def test_run_supervise_once_ticks_exactly_once(tmp_path: Path) -> None:
    wiki, run_dir = make_wiki(tmp_path)
    write_events(run_dir, llm_pair("s", T0 + 10), mtime=T0 + 10)
    rc = run_supervise(
        wiki,
        run_dir=run_dir,
        once=True,
        now_fn=lambda: T0 + 30,
        sleep_fn=lambda _s: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )
    assert rc == 0
    lines = (run_dir / SUPERVISOR_JSONL).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_cli_supervise_once_smoke(tmp_path: Path, monkeypatch, capsys) -> None:
    """End-to-end through the argparse surface: `supervise <wiki> --once`."""
    from wiki_weaver.cli import main

    wiki, run_dir = make_wiki(tmp_path)
    write_events(run_dir, llm_pair("s", T0 + 10))
    write_result(run_dir, status="in_progress")
    monkeypatch.setattr(sys, "argv", ["wiki-weaver", "supervise", str(wiki), "--once"])
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
    assert (run_dir / SUPERVISOR_JSONL).is_file()
    assert (run_dir / SUPERVISOR_STATUS_MD).is_file()
