# pyright: reportMissingImports=false
"""Unit tests for the spawn circuit breaker (wiki_weaver/spawn_breaker.py).

The silent-waste failure mode under test (2026-07 incident): a child session
killed at the spawn timeout is failure-routed by the shared engine straight
back to the SAME node (attempt never increments), re-executing it for hours
with zero file writes -- never converging, never failing. The breaker rides
``run_pipeline``'s ``child_constraint`` seam (invoked in-process before every
spawn), so these tests drive :meth:`SpawnCircuitBreaker.on_spawn` directly
with the exact observable sequence the engine produces:

- repeated spawns, same node, zero writes  -> trips at the threshold
- writes between spawns (progress)          -> never trips
- a different node spawning                 -> streak resets
- threshold env override                    -> respected
- marker file                               -> written on trip, carries the
  distinct failure kind, consumed by the fail path (ingest_fail)
- classify_failure_kind                     -> marker beats the mtime
  heuristics; a stale marker from a previous source does not
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

# Skip this test module entirely if wiki_weaver.engine_runner cannot be imported
# (it depends on amplifier-module-pipeline-runner, which may not be installed in
# CI test environments that use --no-deps pip install).
pytest.importorskip("wiki_weaver.engine_runner")

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wiki_weaver.lib import (  # noqa: E402
    FAILURE_KIND_JUDGED,
    FAILURE_KIND_NO_VERDICT,
    FAILURE_KIND_SPAWN_BREAKER,
    classify_failure_kind,
    spawn_breaker_marker_path,
)
from wiki_weaver.spawn_breaker import (  # noqa: E402
    DEFAULT_BREAKER_THRESHOLD,
    SpawnBreakerTripped,
    SpawnCircuitBreaker,
    breaker_threshold,
    clear_marker,
    read_marker,
)

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def make_wiki(tmp_path: Path) -> Path:
    """Minimal wiki tree: one page, .ai scratch, .wiki process state."""
    wiki = tmp_path / "wiki"
    (wiki / ".ai").mkdir(parents=True)
    (wiki / ".wiki" / "runs").mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    return wiki


def write_node_start(events: Path, node_id: str, execution_index: int = 1) -> None:
    """Append one pipeline:node_start line in the hook-run-events shape."""
    record = {
        "event": "pipeline:node_start",
        "timestamp": "",
        "session_id": "sess-1",
        "data": {
            "node_id": node_id,
            "handler_type": "box",
            "attempt": 1,
            "execution_index": execution_index,
        },
    }
    with events.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


# ---------------------------------------------------------------------------
# Breaker core behavior
# ---------------------------------------------------------------------------


def test_trips_after_threshold_reexecutions_same_node_zero_writes(tmp_path: Path):
    """The incident shape: same node re-spawned with nothing written."""
    wiki = make_wiki(tmp_path)
    events = wiki / ".wiki" / "runs" / "r" / "events.jsonl"
    events.parent.mkdir(parents=True)
    breaker = SpawnCircuitBreaker(wiki, events, threshold=3)

    for i in range(1, 4):  # executions 1..3: baseline + 2 stale re-executions
        write_node_start(events, "ingest", execution_index=i)
        breaker.on_spawn()  # must not raise yet

    write_node_start(events, "ingest", execution_index=4)
    with pytest.raises(SpawnBreakerTripped) as exc:
        breaker.on_spawn()

    assert FAILURE_KIND_SPAWN_BREAKER in str(exc.value)
    assert "ingest" in str(exc.value)
    marker = read_marker(wiki)
    assert marker is not None
    assert marker["failure_kind"] == FAILURE_KIND_SPAWN_BREAKER
    assert marker["node_id"] == "ingest"
    assert marker["executions"] == 4
    assert marker["threshold"] == 3


def test_progressing_source_never_trips(tmp_path: Path):
    """Long-but-progressing work (writes between spawns) is NOT tripped."""
    wiki = make_wiki(tmp_path)
    events = wiki / ".wiki" / "runs" / "r" / "events.jsonl"
    events.parent.mkdir(parents=True)
    breaker = SpawnCircuitBreaker(wiki, events, threshold=3)

    for i in range(1, 12):  # far past the threshold
        write_node_start(events, "ingest", execution_index=i)
        breaker.on_spawn()
        # Real progress each cycle: a page write (mtime/size change).
        (wiki / "index.md").write_text(f"# Index\ncycle {i}\n", encoding="utf-8")

    assert read_marker(wiki) is None


def test_node_change_resets_streak(tmp_path: Path):
    """Healthy alternation (ingest -> assess -> feedback) never accumulates,
    even if a node happens to write nothing."""
    wiki = make_wiki(tmp_path)
    events = wiki / ".wiki" / "runs" / "r" / "events.jsonl"
    events.parent.mkdir(parents=True)
    breaker = SpawnCircuitBreaker(wiki, events, threshold=2)

    for i, node in enumerate(
        ["ingest", "assess", "feedback", "ingest", "assess", "feedback"], start=1
    ):
        write_node_start(events, node, execution_index=i)
        breaker.on_spawn()  # must never raise

    assert read_marker(wiki) is None


def test_missing_events_sink_still_trips_on_fingerprint_alone(tmp_path: Path):
    """The events.jsonl sink is fail-soft; the breaker must not depend on it."""
    wiki = make_wiki(tmp_path)
    breaker = SpawnCircuitBreaker(
        wiki, wiki / ".wiki" / "runs" / "r" / "events.jsonl", threshold=3
    )
    for _ in range(3):
        breaker.on_spawn()
    with pytest.raises(SpawnBreakerTripped) as exc:
        breaker.on_spawn()
    assert "unknown" in str(exc.value)


def test_keeps_raising_after_trip(tmp_path: Path):
    """Post-trip re-spawns (the engine's fast failure-routing laps) must fail
    INSTANTLY every time -- and the breaker's own marker write must not
    masquerade as progress and reset the count."""
    wiki = make_wiki(tmp_path)
    breaker = SpawnCircuitBreaker(wiki, None, threshold=1)
    breaker.on_spawn()  # baseline
    for _ in range(5):
        with pytest.raises(SpawnBreakerTripped):
            breaker.on_spawn()


def test_wiki_process_state_churn_is_not_progress(tmp_path: Path):
    """.wiki/ (checkpoints, logs, ledger) churns constantly during the
    re-execution loop; it must be excluded from the progress fingerprint."""
    wiki = make_wiki(tmp_path)
    breaker = SpawnCircuitBreaker(wiki, None, threshold=2)
    breaker.on_spawn()
    for i in range(2):
        (wiki / ".wiki" / "runs" / f"checkpoint-{i}.json").write_text(
            "{}", encoding="utf-8"
        )
        if i < 1:
            breaker.on_spawn()
        else:
            with pytest.raises(SpawnBreakerTripped):
                breaker.on_spawn()


def test_threshold_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD", "1")
    assert breaker_threshold() == 1
    wiki = make_wiki(tmp_path)
    breaker = SpawnCircuitBreaker(wiki, None)  # threshold from env
    breaker.on_spawn()
    with pytest.raises(SpawnBreakerTripped):
        breaker.on_spawn()


def test_threshold_env_garbage_falls_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD", "zero")
    assert breaker_threshold() == DEFAULT_BREAKER_THRESHOLD
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD", "0")
    assert breaker_threshold() == DEFAULT_BREAKER_THRESHOLD
    monkeypatch.delenv("WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD")
    assert breaker_threshold() == DEFAULT_BREAKER_THRESHOLD


def test_clear_marker(tmp_path: Path):
    wiki = make_wiki(tmp_path)
    spawn_breaker_marker_path(wiki).write_text("{}", encoding="utf-8")
    clear_marker(wiki)
    assert read_marker(wiki) is None
    clear_marker(wiki)  # idempotent on absence


def test_torn_events_line_tolerated(tmp_path: Path):
    """A truncated trailing line (SIGKILL mid-append) must not break node
    identification -- the breaker falls back to the previous complete line."""
    wiki = make_wiki(tmp_path)
    events = wiki / ".wiki" / "runs" / "r" / "events.jsonl"
    events.parent.mkdir(parents=True)
    write_node_start(events, "ingest")
    with events.open("a", encoding="utf-8") as f:
        f.write('{"event":"pipeline:node_st')  # torn line, no newline
    breaker = SpawnCircuitBreaker(wiki, events, threshold=3)
    assert breaker._latest_node_start() == "ingest"


# ---------------------------------------------------------------------------
# Failure-kind classification (ledger vocabulary)
# ---------------------------------------------------------------------------


def test_classify_marker_beats_assessment_heuristic(tmp_path: Path):
    wiki = make_wiki(tmp_path)
    started = time.time() - 60
    # A fresh assessment file alone would classify as judged_non_converged...
    (wiki / ".ai" / "assessment.md").write_text("scores", encoding="utf-8")
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_JUDGED
    # ...but a fresh breaker marker wins.
    spawn_breaker_marker_path(wiki).write_text(
        json.dumps({"failure_kind": FAILURE_KIND_SPAWN_BREAKER}), encoding="utf-8"
    )
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_SPAWN_BREAKER
    # started_at=None with a marker present still names the breaker.
    assert classify_failure_kind(wiki, None) == FAILURE_KIND_SPAWN_BREAKER


def test_classify_stale_marker_from_previous_source_ignored(tmp_path: Path):
    """A marker whose mtime predates THIS source's synthesis window must not
    leak the breaker kind onto an unrelated failure."""
    wiki = make_wiki(tmp_path)
    marker = spawn_breaker_marker_path(wiki)
    marker.write_text(
        json.dumps({"failure_kind": FAILURE_KIND_SPAWN_BREAKER}), encoding="utf-8"
    )
    old = time.time() - 1000
    os.utime(marker, (old, old))
    started = time.time() - 60
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_NO_VERDICT


# ---------------------------------------------------------------------------
# Fail path: quarantine carries the kind + consumes the marker, drain continues
# ---------------------------------------------------------------------------


def test_ingest_fail_quarantines_with_breaker_kind_and_consumes_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End of the chain: breaker tripped -> fail_handler quarantines the
    source with the DISTINCT failure kind, consumes the marker so the next
    source cannot inherit it, and exits 0 so the drain loop CONTINUES."""
    from wiki_weaver import ingest_fail
    from wiki_weaver.lib import _read_ledger, wiki_failed

    wiki = make_wiki(tmp_path)
    inbox = wiki / "_inbox"
    inbox.mkdir()
    source = inbox / "monster-transcript.md"
    source.write_text("# Transcript\nhuge\n", encoding="utf-8")

    # The breaker tripped during this source's synthesis window.
    spawn_breaker_marker_path(wiki).write_text(
        json.dumps(
            {
                "failure_kind": FAILURE_KIND_SPAWN_BREAKER,
                "node_id": "ingest",
                "executions": 4,
                "threshold": 3,
            }
        ),
        encoding="utf-8",
    )

    started_at = time.time() - 60
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ingest_fail.py",
            str(wiki),
            str(source),
            "7",
            str(started_at),
        ],
    )
    assert ingest_fail.main() == 0  # exit 0 == the drain moves on

    # Quarantined, not lost.
    assert not source.exists()
    failed_files = list(wiki_failed(wiki).iterdir())
    assert [p.name for p in failed_files] == ["monster-transcript.md"]

    # Ledger row carries the distinct kind + the breaker specifics.
    rows = _read_ledger(wiki)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "failed"
    assert row["converged"] is False
    assert row["failure_kind"] == FAILURE_KIND_SPAWN_BREAKER
    assert "spawn circuit breaker" in row["reason"]
    assert "'ingest'" in row["reason"]
    assert "4x" in row["reason"]

    # Marker consumed: the NEXT source of this drain starts clean.
    assert read_marker(wiki) is None


# ---------------------------------------------------------------------------
# Fix (a): the per-spawn budget knob
# ---------------------------------------------------------------------------


def test_spawn_timeout_default_and_env_override(monkeypatch: pytest.MonkeyPatch):
    from wiki_weaver.engine_runner import (
        DEFAULT_SPAWN_TIMEOUT_SECONDS,
        spawn_timeout_seconds,
    )

    monkeypatch.delenv("WIKI_WEAVER_SPAWN_TIMEOUT", raising=False)
    assert spawn_timeout_seconds() == DEFAULT_SPAWN_TIMEOUT_SECONDS == 3600.0

    monkeypatch.setenv("WIKI_WEAVER_SPAWN_TIMEOUT", "7200")
    assert spawn_timeout_seconds() == 7200.0

    # Fail-safe: garbage / non-positive never yields an instant-kill timeout.
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_TIMEOUT", "not-a-number")
    assert spawn_timeout_seconds() == DEFAULT_SPAWN_TIMEOUT_SECONDS
    monkeypatch.setenv("WIKI_WEAVER_SPAWN_TIMEOUT", "0")
    assert spawn_timeout_seconds() == DEFAULT_SPAWN_TIMEOUT_SECONDS
