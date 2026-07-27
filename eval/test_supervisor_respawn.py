# pyright: reportMissingImports=false
"""Supervisor check 8: node-respawn anomaly (spawn-timeout silent-waste).

Synthetic-fixture tests (no engine, no LLM, injected ``now_fn``) for the live
detectable signal of the 2026-07 incident: the SAME pipeline node emitting
back-to-back ``pipeline:node_start`` events (execution_index climbing, attempt
pinned at 1, no other node in between) while the wiki's artifact tree receives
ZERO writes. Mirrors the fixture style of eval/test_supervisor.py.
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

from wiki_weaver.supervisor import Supervisor, SupervisorConfig  # noqa: E402

_RUN_STAMP = "20260726-120000-000000"
T0 = datetime.strptime(_RUN_STAMP, "%Y%m%d-%H%M%S-%f").timestamp()


def make_wiki(tmp_path: Path) -> tuple[Path, Path]:
    wiki = tmp_path / "wiki"
    (wiki / "_inbox").mkdir(parents=True)
    run_dir = wiki / ".wiki" / "runs" / f"ingest-{_RUN_STAMP}"
    run_dir.mkdir(parents=True)
    # One pre-existing page, mtime pinned BEFORE the run starts.
    page = wiki / "index.md"
    page.write_text("# Index\n\n[2026-07-01 Seed]\n", encoding="utf-8")
    os.utime(page, (T0 - 100, T0 - 100))
    return wiki, run_dir


def node_start(node_id: str, execution_index: int) -> dict:
    return {
        "event": "pipeline:node_start",
        "timestamp": "",
        "session_id": "sess-1",
        "data": {
            "node_id": node_id,
            "handler_type": "box",
            "attempt": 1,  # the engine hard-codes this -- never increments
            "execution_index": execution_index,
        },
    }


def write_events(run_dir: Path, events: list[dict], mtime: float) -> None:
    path = run_dir / "events.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, separators=(",", ":")) + "\n")
    os.utime(path, (mtime, mtime))


def respawn_concerns(record: dict) -> list[dict]:
    return [c for c in record["concerns"] if c.get("check") == "node-respawn"]


def test_repeated_same_node_zero_writes_flags_anomaly(tmp_path: Path):
    """The incident shape: 4+ back-to-back starts of 'ingest', nothing written."""
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, [node_start("ingest", i) for i in range(1, 5)], mtime=now)
    sup = Supervisor(wiki, run_dir, SupervisorConfig(), now_fn=lambda: now)
    record = sup.tick()

    concerns = respawn_concerns(record)
    assert len(concerns) == 1
    assert concerns[0]["level"] == "anomaly"
    assert concerns[0]["node"] == "ingest"
    assert "3x" in concerns[0]["message"]
    assert record["verdict"] == "anomaly"
    assert record["counters"]["respawn_streak_node"] == "ingest"
    assert record["counters"]["respawn_streak"] == 4


def test_progressing_source_not_flagged(tmp_path: Path):
    """Same repeated starts, but artifact writes since the streak began --
    a long-but-progressing source must NOT be flagged."""
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, [node_start("ingest", i) for i in range(1, 5)], mtime=now)
    # A page written AFTER the streak began (streak start == absorb time).
    page = wiki / "concept.md"
    page.write_text("# Concept\n\n[2026-07-26 Sync]\n", encoding="utf-8")
    os.utime(page, (now + 10, now + 10))

    sup = Supervisor(wiki, run_dir, SupervisorConfig(), now_fn=lambda: now)
    record = sup.tick()
    assert respawn_concerns(record) == []


def test_healthy_node_alternation_not_flagged(tmp_path: Path):
    """Normal refine loop: nodes alternate, so no same-node streak forms."""
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    seq = ["ingest", "normalize", "assess", "feedback"] * 3
    write_events(run_dir, [node_start(n, i) for i, n in enumerate(seq, 1)], mtime=now)
    sup = Supervisor(wiki, run_dir, SupervisorConfig(), now_fn=lambda: now)
    record = sup.tick()
    assert respawn_concerns(record) == []
    assert record["counters"]["respawn_streak"] == 1


def test_below_threshold_not_flagged(tmp_path: Path):
    """3 consecutive starts = 2 re-executions < respawn_warn_count(3)."""
    wiki, run_dir = make_wiki(tmp_path)
    now = T0 + 100
    write_events(run_dir, [node_start("ingest", i) for i in range(1, 4)], mtime=now)
    sup = Supervisor(wiki, run_dir, SupervisorConfig(), now_fn=lambda: now)
    record = sup.tick()
    assert respawn_concerns(record) == []
    assert record["counters"]["respawn_streak"] == 3


def test_streak_accumulates_across_ticks(tmp_path: Path):
    """Starts arriving one tick at a time (the live shape: ~30 min apart)
    still accumulate into a flagged streak."""
    wiki, run_dir = make_wiki(tmp_path)
    clock = {"now": T0 + 60}
    sup = Supervisor(wiki, run_dir, SupervisorConfig(), now_fn=lambda: clock["now"])

    path = run_dir / "events.jsonl"
    record: dict = {}
    for i in range(1, 5):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(node_start("ingest", i)) + "\n")
        os.utime(path, (clock["now"], clock["now"]))
        record = sup.tick()
        clock["now"] += 60

    assert len(respawn_concerns(record)) == 1
    assert record["counters"]["respawn_streak"] == 4
