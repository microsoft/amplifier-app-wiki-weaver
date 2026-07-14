"""Run-events hook -- appends every session event to ONE run-scoped events.jsonl.

Wiki-weaver-owned observability sink. ADDITIVE to (and completely independent
of) the ``hook-context-intelligence`` hook composed alongside it in
``wiki_weaver.engine_runner._ci_overlay()``. This hook never touches, reads,
or relocates anything the CI hook writes -- it targets its own file:
``<logs_dir>/events.jsonl``, where ``logs_dir`` is the run's own working
directory (e.g. ``.wiki/runs/<timestamp>/``), passed in via
``config["events_path"]``.

Purpose: give downstream consumers (e.g. Team Pulse) one predictable,
run-scoped file to poll for real-time progress, instead of reverse-engineering
internal checkpoint files or per-session Context Intelligence output.

Write pattern mirrors hook-context-intelligence's own LoggingHandler
deliberately: open-in-append-mode, write one JSON line, close -- one syscall
per event, no long-lived buffered handle. This is what makes a SIGKILL
mid-run benign: at most the last in-flight line can be truncated, never an
earlier one (see LoggingHandler._append_event in
amplifier_module_hook_context_intelligence.handlers.logging_handler for the
pattern this mirrors).

Because this hook is composed into the SAME overlay Bundle passed to
``run_pipeline``'s ``extra_overlays`` (parity with ``hook-context-
intelligence``), it is inherited by every spawned child session exactly the
same way -- so a run's full event stream (parent + every child) lands in
this ONE file, not a scattered per-session tree.

Configuration keys
-------------------
events_path : str, required
    Absolute path to the run-scoped events.jsonl file this session (and
    every child it spawns) appends to. When absent, the sink is disabled
    (mount() still succeeds -- a missing config value degrades to a no-op,
    it never breaks the run).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from amplifier_core.models import HookResult

log = logging.getLogger(__name__)

__amplifier_module_type__ = "hook"

# Capability name used to hand mount()'s state to on_session_ready() -- same
# split CI's own hook uses (mount() cannot enumerate every event name until
# every module has mounted, since some events are module-contributed).
_STATE_CAPABILITY = "wiki_weaver_run_events._state"


def _canonical_json(record: dict[str, Any]) -> str:
    """Compact, stable JSON encoding. ``default=str`` so an odd value (e.g. a
    stray non-serializable object slipping through event data) degrades to
    its string form instead of raising and losing the whole event.
    """
    return json.dumps(record, default=str, separators=(",", ":"))


class _RunEventsSink:
    """Appends one JSON line per event to a fixed run-scoped file.

    Open-per-event, no buffered/long-lived file handle: a killed process can
    lose at most the single in-flight write, never a line already flushed to
    disk (matches the append pattern used by hook-context-intelligence's
    LoggingHandler._append_event).
    """

    def __init__(self, events_path: Path) -> None:
        self._events_path = events_path

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        try:
            record = {
                "event": event,
                "timestamp": data.get("timestamp", ""),
                "session_id": data.get("session_id", ""),
                "data": data,
            }
            self._events_path.parent.mkdir(parents=True, exist_ok=True)
            with self._events_path.open("a", encoding="utf-8") as f:
                f.write(_canonical_json(record) + "\n")
        except Exception:
            # Never let a write failure break the run -- this is a best-
            # effort observability sink, not a correctness-critical path.
            log.warning(
                "hook-run-events: failed to append event %s", event, exc_info=True
            )
        return HookResult(action="continue")


async def _discover_events(coordinator: Any) -> set[str]:
    """Union of ALL_EVENTS + module contributions + legacy capability.

    Deliberately mirrors hook-context-intelligence's own ``_discover_events``
    so this sink captures the SAME event surface -- including orchestrator-
    contributed events (node start/complete/checkpoint, subgraph boundaries)
    that are not in the kernel's static ALL_EVENTS list.
    """
    from amplifier_core.events import ALL_EVENTS  # type: ignore[import-not-found]

    discovered: set[str] = set(ALL_EVENTS)

    contributions = await coordinator.collect_contributions("observability.events")
    for event_list in contributions:
        discovered.update(event_list)

    capability = coordinator.get_capability("observability.events")
    if capability is not None:
        raw = capability() if callable(capability) else capability
        if isinstance(raw, (list, set, frozenset, tuple)):
            discovered.update(raw)

    return discovered


async def mount(
    coordinator: Any, config: dict[str, Any]
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Mount the run-events sink.

    Stashes ``events_path`` + a mutable unregister-list behind a private
    capability; actual event registration happens in ``on_session_ready``
    (see module docstring for why). Always returns a cleanup callable so
    protocol compliance holds even when ``events_path`` is absent.
    """
    events_path = config.get("events_path")
    unregister_fns: list[Callable[[], None]] = []
    state = {"events_path": events_path, "unregister_fns": unregister_fns}
    coordinator.register_capability(_STATE_CAPABILITY, state)

    async def cleanup() -> None:
        for unreg in unregister_fns:
            try:
                unreg()
            except Exception:
                pass
        try:
            coordinator.register_capability(_STATE_CAPABILITY, None)
        except Exception:
            pass

    return cleanup


async def on_session_ready(coordinator: Any) -> None:
    """Finalize event subscription once every module has mounted.

    No-op when ``events_path`` was never configured (sink disabled) or when
    ``mount()`` never ran (defensive; should be unreachable in normal flow).
    """
    state = coordinator.get_capability(_STATE_CAPABILITY)
    if state is None:
        log.warning(
            "hook-run-events: on_session_ready called before mount() -- skipping"
        )
        return

    events_path = state.get("events_path")
    if not events_path:
        log.debug("hook-run-events: no events_path configured -- sink disabled")
        return

    sink = _RunEventsSink(Path(events_path))
    events = await _discover_events(coordinator)
    for event in sorted(events):
        unreg = coordinator.hooks.register(
            event, sink, priority=100, name="RunEventsSink"
        )
        state["unregister_fns"].append(unreg)

    log.info("hook-run-events: registered %d events -> %s", len(events), events_path)
