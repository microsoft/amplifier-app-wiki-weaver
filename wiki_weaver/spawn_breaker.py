# pyright: reportMissingImports=false
"""Spawn circuit breaker -- fail LOUDLY on repeated no-progress node re-execution.

THE FAILURE MODE THIS KILLS (2026-07 incident, graph-verified): on a monster
meeting-transcript source, the inner ``ingest`` (synthesis) node's child
session was killed at exactly the spawn timeout (``asyncio.wait_for`` in
pipeline-runner's ``make_spawn_fn``), then the shared attractor engine
re-executed the SAME node -- ``execution_index`` 1..8, ``attempt: 1`` every
time (the engine hard-codes attempt=1 in PipelineNodeStartEvent; the
within-handler retry counter never engages because the timeout surfaces as a
FAIL outcome, not a RETRY) -- 8 times over 3.6 hours: 265 LLM calls, hundreds
of re-orientation reads of the same files, ZERO file writes. The mechanism:

1. ``make_spawn_fn`` (pipeline-runner) wraps each child spawn in
   ``asyncio.wait_for(spawn_coro, timeout=spawn_timeout)`` -- the kill.
2. The loop-pipeline backend's broad ``except Exception`` converts the
   ``TimeoutError`` into ``Outcome(FAIL)``.
3. ``synthesize.dot``'s ingest/assess nodes declare ``retry_target`` pointing
   at THEMSELVES; a FAILed node matches no outgoing edge (engine fail-fast
   rule), so the engine's failure routing jumps straight back to the same
   node -- a fresh full re-execution, with the graph-level bound
   ``_MAX_GOAL_GATE_RETRIES = 50``. 50 x 1800s = 25 hours of silent waste
   per source: never converges, never fails. A prior 20-hour incident
   (16 children, same shape) retrospectively matches.

THE MECHANISM (wiki-weaver-side only; the attractor engine is shared and is
NOT touched): ``run_pipeline``'s ``child_constraint`` seam is invoked
in-process immediately BEFORE every child spawn (pipeline-runner
``make_spawn_fn`` applies it per spawn). Wiki-weaver wraps its existing
filesystem constraint with :meth:`SpawnCircuitBreaker.on_spawn`, which:

- fingerprints the wiki's artifact tree (pages + ``.ai/`` scratch;
  ``.wiki/`` process state and logs excluded) -- the PROGRESS signal;
- reads the latest ``pipeline:node_start`` from the run's ``events.jsonl``
  (written live by the wiki-weaver-owned hook-run-events sink) -- the NODE
  IDENTITY signal (best-effort; the breaker still works without it);
- counts consecutive spawns of the same node with a byte-identical
  fingerprint. In a HEALTHY run consecutive spawns always differ: ingest
  writes pages + the touched manifest, assess writes ``.ai/assessment.md``,
  feedback writes ``.ai/feedback/`` -- and different nodes alternate.
  Consecutive same-node spawns with zero writes only happen in the
  failure-routing re-execution loop above.

When the count reaches the threshold the breaker writes a marker file
(consumed by the fail path to stamp the distinct ledger ``failure_kind``
``spawn_timeout_no_progress``) and raises :class:`SpawnBreakerTripped`.
The raise converts each subsequent re-execution into an INSTANT failure
(milliseconds instead of another 30-60 minute spawn), so the engine's own
bounded failure routing exhausts immediately, the synthesize run fails,
ingest.dot routes to ``fail_handler`` (quarantine + ledger + loop_restart),
and the drain moves on to the next source.

Threshold knob: ``WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD`` (default 3 --
trip after 3 full re-executions produced nothing).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .lib import FAILURE_KIND_SPAWN_BREAKER, spawn_breaker_marker_path

DEFAULT_BREAKER_THRESHOLD = 3
_THRESHOLD_ENV = "WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD"

# Bounded tail read of events.jsonl -- never load the whole file (it grows
# unboundedly during a run; the latest node_start is always near the end).
_TAIL_BYTES = 65536

_NODE_START_EVENT = "pipeline:node_start"


def breaker_threshold() -> int:
    """Re-executions-with-zero-writes allowed before the breaker trips.

    Env-configurable via ``WIKI_WEAVER_SPAWN_BREAKER_THRESHOLD``; floor of 1
    (0/negative/garbage fall back to the default -- a breaker that trips on
    the FIRST execution would break healthy runs).
    """
    raw = os.environ.get(_THRESHOLD_ENV, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_BREAKER_THRESHOLD
    return value if value >= 1 else DEFAULT_BREAKER_THRESHOLD


class SpawnBreakerTripped(RuntimeError):
    """Raised pre-spawn when a node keeps re-executing with zero writes.

    Deliberately a REGULAR ``Exception`` subclass: the shared loop-pipeline
    backend converts it into ``Outcome(FAIL, failure_reason=str(exc))``, the
    engine's own (bounded) failure routing exhausts in milliseconds, and the
    existing non-convergence machinery (ingest.dot ``fail_handler`` /
    lib.py's drain loop) quarantines the source and CONTINUES the drain --
    exactly the loud-fail path wiki-weaver already owns.
    """

    def __init__(self, node_id: str, executions: int, wiki_dir: Path) -> None:
        self.node_id = node_id
        self.executions = executions
        super().__init__(
            f"spawn circuit breaker tripped ({FAILURE_KIND_SPAWN_BREAKER}): "
            f"node '{node_id}' executed {executions}x with ZERO artifact "
            f"writes under {wiki_dir} between executions -- refusing to spawn "
            f"it again (each re-execution burns a full spawn-timeout window "
            f"in LLM calls and produces nothing)"
        )


def read_marker(wiki_dir: Path) -> dict[str, Any] | None:
    """The breaker marker's payload, or None. Never raises."""
    try:
        raw = spawn_breaker_marker_path(wiki_dir).read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def clear_marker(wiki_dir: Path) -> None:
    """Remove a stale marker (call at run start / after consuming). Never raises."""
    try:
        spawn_breaker_marker_path(wiki_dir).unlink(missing_ok=True)
    except OSError:
        pass


class SpawnCircuitBreaker:
    """Per-run breaker state. Create one per ``run_pipeline`` call and invoke
    :meth:`on_spawn` from the ``child_constraint`` seam before every spawn.
    """

    def __init__(
        self,
        wiki_dir: Path,
        events_path: Path | None = None,
        *,
        threshold: int | None = None,
    ) -> None:
        self.wiki_dir = Path(wiki_dir).resolve()
        self.events_path = events_path
        self.threshold = threshold if threshold is not None else breaker_threshold()
        self._last_fingerprint: dict[str, tuple[int, int]] | None = None
        self._last_node: str | None = None
        self._stale_spawns = 0  # consecutive re-executions with zero writes

    # -- observation -------------------------------------------------------

    def _fingerprint(self) -> dict[str, tuple[int, int]]:
        """Artifact-tree fingerprint: relpath -> (mtime_ns, size).

        Scope: everything under the wiki EXCEPT ``.wiki/`` (process state,
        run logs, checkpoints -- they churn constantly and are not synthesis
        progress) and the breaker's own marker file (excluded so the trip
        itself never masquerades as progress and resets the count).
        """
        marker = spawn_breaker_marker_path(self.wiki_dir)
        fp: dict[str, tuple[int, int]] = {}
        for root, dirs, files in os.walk(self.wiki_dir):
            dirs[:] = [d for d in dirs if d != ".wiki"]
            for name in files:
                path = Path(root) / name
                if path == marker:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                fp[str(path.relative_to(self.wiki_dir))] = (st.st_mtime_ns, st.st_size)
        return fp

    def _latest_node_start(self) -> str | None:
        """node_id of the newest ``pipeline:node_start`` in events.jsonl.

        Bounded tail read; tolerates a torn trailing line. ``None`` when the
        sink is absent/unreadable -- the breaker then counts on the
        fingerprint alone (unknown node treated as "same node",
        conservatively toward tripping; healthy pipelines always write
        between spawns, so this cannot false-trip them).
        """
        if self.events_path is None:
            return None
        try:
            with self.events_path.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - _TAIL_BYTES))
                chunk = f.read()
        except OSError:
            return None
        for raw in reversed(chunk.split(b"\n")):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue  # torn / partial line
            if not isinstance(obj, dict) or obj.get("event") != _NODE_START_EVENT:
                continue
            data = obj.get("data")
            if isinstance(data, dict) and data.get("node_id"):
                return str(data["node_id"])
        return None

    # -- the check ----------------------------------------------------------

    def on_spawn(self) -> None:
        """Called immediately before EVERY child spawn. Raises when tripped."""
        node = self._latest_node_start()
        fp = self._fingerprint()

        if self._last_fingerprint is None:
            # First spawn of the run -- baseline only.
            self._stale_spawns = 0
        elif fp == self._last_fingerprint and (
            node is None or self._last_node is None or node == self._last_node
        ):
            self._stale_spawns += 1
        else:
            # Progress (writes happened) or a different node -- healthy.
            self._stale_spawns = 0

        self._last_fingerprint = fp
        self._last_node = node

        if self._stale_spawns >= self.threshold:
            node_id = node or self._last_node or "unknown"
            executions = self._stale_spawns + 1  # re-executions + the original
            self._write_marker(node_id, executions)
            raise SpawnBreakerTripped(node_id, executions, self.wiki_dir)

    def _write_marker(self, node_id: str, executions: int) -> None:
        """Persist the trip for the fail path (ledger ``failure_kind`` /
        result.json advisory). FAIL-SOFT: the raise is the load-bearing act.
        """
        marker = spawn_breaker_marker_path(self.wiki_dir)
        payload = {
            "failure_kind": FAILURE_KIND_SPAWN_BREAKER,
            "node_id": node_id,
            "executions": executions,
            "threshold": self.threshold,
            "tripped_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
