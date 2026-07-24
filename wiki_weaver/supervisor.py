# pyright: reportMissingImports=false
"""Run supervisor -- deterministic, observe-only monitor for a live ingest run.

WHY THIS EXISTS (the motivating incidents, one 25-source / 11-hour run):

(a) 4 sources were quarantined because assess child-sessions silently hit the
    50-LLM-call orchestrator ceiling -- nothing surfaced until the post-mortem.
(b) A wiki page written WITHOUT grounding markers at hour 4 killed the whole
    run at a certification gate at hour 11 -- catching it at hour 4 would have
    saved 7 hours.
(c) The run went quiet for 80 minutes post-drain with no way to distinguish
    "working" (one long LLM call in flight) from "wedged".
(d) One child session grew a ~2.9M-token context and got a provider 400.

A human watching ``events.jsonl`` + the ledger caught all four. This module
automates exactly that watching: **observe and report only** -- zero steering,
zero LLM calls, purely deterministic checks. Mechanism, not policy: every
threshold is a knob (``SupervisorConfig`` / CLI flag) with a sane default.

WHAT IT WATCHES (all read-only):

- ``<run_dir>/events.jsonl``    -- run-scoped event stream (hook-run-events).
  Read INCREMENTALLY and tail-safe: byte-offset resume per tick, never loads
  the whole file, tolerates a torn trailing line (an incomplete final line is
  left un-consumed until its newline arrives; a corrupt complete line is
  skipped and counted).
- ``<run_dir>/result.json``     -- incremental run result (wiki_weaver.run_result):
  ``status: in_progress`` counts-so-far mid-drain, ``final``/``superseded``
  at terminal state; ``advisories`` (gate advisories that fired but did NOT
  block, incl. retention/shrinkage/removal signals).
- ``<wiki>/.wiki/.processed.jsonl`` -- the ledger: per-source disposition
  with ISO ``timestamp`` (success AND failure records).
- ``<wiki>/_inbox/``            -- pending sources.
- ``<wiki>/**/*.md``            -- user-facing wiki pages, spot-checked for
  grounding markers when modified since the previous tick.

WHAT IT WRITES (the ONLY writes, both inside the run dir -- never elsewhere
under ``.wiki``):

- ``<run_dir>/supervisor.jsonl``     -- one JSON record appended per tick
  (``ts``, ``verdict``, ``concerns``, key counters).
- ``<run_dir>/supervisor-status.md`` -- human-readable latest snapshot,
  rewritten (atomically) each tick.

One stderr line is printed ONLY on verdict transitions (including the first
tick establishing a verdict). The loop exits cleanly when the run reaches a
terminal state (result.json ``final``/``superseded``, or the run dir is gone)
or on Ctrl-C.

VERDICTS (per tick): ``healthy`` | ``slow`` | ``stalled`` | ``anomaly``.
Precedence when concerns disagree: stalled > anomaly > slow > healthy.

THE CHECKS (each maps to an incident above):

1. Event liveness -- time since the last ``events.jsonl`` append; over the
   threshold while the run is live => ``stalled``, UNLESS an ``llm:request``
   is open (request seen, no paired ``llm:response`` yet) -- the engine may
   be inside one long LLM call, which softens the verdict to ``slow``
   (incident c).
2. Progress pace -- current source's elapsed time (since the last ledger
   completion) vs the rolling mean of this run's completed sources; over
   ``pace_factor``x the mean => ``slow``.
3. Iteration-ceiling proximity -- ``llm:request`` count within the current
   child session at >= ``ceiling_warn_frac`` of ``llm_call_ceiling`` =>
   concern naming the session (incident a).
4. Context growth -- latest per-response context size (``input_tokens`` +
   ``cache_read_tokens``) in the current session approaching
   ``context_limit_tokens`` => concern (incident d).
5. Cost accumulation -- running ``usage.cost_usd`` total, reported every
   tick; concern only when ``budget_usd`` is set and exceeded.
6. Grounding-marker spot check -- pages modified since the last tick missing
   BOTH marker forms (``[YYYY-MM-DD Label]`` / ``[src: Name]``) => concern
   naming the page (incident b).
7. Advisory surfacing -- NEW ``result.json`` advisories (not seen on a prior
   tick) are surfaced as concerns the tick they appear.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from wiki_weaver.lib import wiki_inbox, wiki_ledger, wiki_runs
from wiki_weaver.run_result import STATUS_FINAL, STATUS_SUPERSEDED

# ---------------------------------------------------------------------------
# Constants + config (mechanism, not policy: every threshold is a knob)
# ---------------------------------------------------------------------------

VERDICT_HEALTHY = "healthy"
VERDICT_SLOW = "slow"
VERDICT_STALLED = "stalled"
VERDICT_ANOMALY = "anomaly"

# Precedence when concerns disagree (higher wins).
_VERDICT_RANK = {
    VERDICT_HEALTHY: 0,
    VERDICT_SLOW: 1,
    VERDICT_ANOMALY: 2,
    VERDICT_STALLED: 3,
}

SUPERVISOR_JSONL = "supervisor.jsonl"
SUPERVISOR_STATUS_MD = "supervisor-status.md"

# Grounding-marker forms (see the entailment/certification contract): a page
# carrying claims must cite provenance as either a dated marker
# ``[2026-06-10 Standup]`` or a source marker ``[src: Some Name]``.
_DATE_MARKER = re.compile(r"\[\d{4}-\d{2}-\d{2}(?:\s[^\]]*)?\]")
_SRC_MARKER = re.compile(r"\[src:\s*[^\]]+\]", re.IGNORECASE)

# Run-dir name formats (lib.py uses microseconds; older runs may not).
_RUN_TS_FORMATS = ("%Y%m%d-%H%M%S-%f", "%Y%m%d-%H%M%S")

# Cap per-tick concern fan-out for the page spot check so one bulk re-weave
# cannot flood the record.
_MAX_PAGE_CONCERNS = 10


@dataclass
class SupervisorConfig:
    """Thresholds for the deterministic checks. Flags, not policy."""

    interval_s: float = 60.0
    # 1. liveness: age of the last events.jsonl append that counts as stalled.
    stall_after_s: float = 600.0
    # 2. pace: current source elapsed > pace_factor * rolling mean => slow.
    pace_factor: float = 3.0
    # Minimum completed sources this run before the pace check has a
    # meaningful rolling mean to compare against.
    pace_min_completed: int = 2
    # 3. ceiling: the orchestrator's per-child-session LLM-call budget, and
    # the fraction of it at which to warn (0.8 * 50 => fires at 40).
    llm_call_ceiling: int = 50
    ceiling_warn_frac: float = 0.8
    # 4. context growth: provider context limit + warn fraction.
    context_limit_tokens: int = 1_000_000
    context_warn_frac: float = 0.8
    # 5. cost: optional budget; None = report-only, never a concern.
    budget_usd: float | None = None


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _parse_iso_ts(value: str) -> float | None:
    """ISO-8601 string -> epoch seconds, or None. Tolerates trailing 'Z'."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def parse_run_start(run_dir: Path) -> float:
    """Epoch start of a run from its ``ingest-<ts>`` dir name.

    Falls back to the directory mtime when the name doesn't parse (fail-soft:
    a weird name must not break monitoring).
    """
    name = run_dir.name
    if name.startswith("ingest-"):
        stamp = name[len("ingest-") :]
        for fmt in _RUN_TS_FORMATS:
            try:
                return datetime.strptime(stamp, fmt).timestamp()
            except ValueError:
                continue
    try:
        return run_dir.stat().st_mtime
    except OSError:
        return time.time()


def find_newest_run_dir(wiki: Path) -> Path | None:
    """Newest ``<wiki>/.wiki/runs/ingest-*`` dir (names are timestamp-sorted)."""
    runs = wiki_runs(wiki)
    try:
        candidates = sorted(p for p in runs.glob("ingest-*") if p.is_dir())
    except OSError:
        return None
    return candidates[-1] if candidates else None


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read a JSON object file; None on any failure (missing/torn/invalid)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _iter_wiki_pages(wiki: Path):
    """User-facing wiki pages: ``*.md`` outside hidden/underscore subtrees.

    ``index.md`` (the re-woven overview) is exempt from the marker spot check
    -- it aggregates rather than carries per-claim provenance.
    """
    for p in sorted(wiki.rglob("*.md")):
        rel = p.relative_to(wiki)
        if any(part.startswith((".", "_")) for part in rel.parts):
            continue
        if p.name.lower() == "index.md":
            continue
        yield p


def _has_grounding_marker(text: str) -> bool:
    return bool(_DATE_MARKER.search(text) or _SRC_MARKER.search(text))


# ---------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------


@dataclass
class _SessionStats:
    llm_requests: int = 0
    llm_responses: int = 0
    last_context_tokens: int = 0


class Supervisor:
    """Deterministic tick evaluator for one run dir. Read-only against the
    run except ``supervisor.jsonl`` + ``supervisor-status.md`` in the run dir.

    Testable by construction: ``now_fn`` is injectable and ``tick()`` is a
    pure state-advance returning the record it appended.
    """

    def __init__(
        self,
        wiki: Path,
        run_dir: Path,
        config: SupervisorConfig | None = None,
        *,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.wiki = wiki
        self.run_dir = run_dir
        self.config = config or SupervisorConfig()
        self.now_fn = now_fn
        self.run_start = parse_run_start(run_dir)

        # events.jsonl incremental-read state
        self._events_offset = 0
        self._events_parsed = 0
        self._malformed_lines = 0
        self._sessions: dict[str, _SessionStats] = {}
        self._current_session: str = ""
        self._total_cost_usd = 0.0

        # advisory / page-scan / verdict state
        self._seen_advisories: set[str] = set()
        self._pages_scanned_at = self.run_start
        self.last_verdict: str | None = None

    # -- events.jsonl (tail-safe incremental reader) ------------------------

    def _read_new_events(self) -> list[dict[str, Any]]:
        """Read complete NEW lines since the last tick; never the whole file.

        Torn-line tolerance: a trailing line without its newline is left
        un-consumed (the offset stays before it) so a partially-flushed
        append is retried next tick; a complete-but-corrupt line is skipped
        and counted, never fatal.
        """
        path = self.run_dir / "events.jsonl"
        try:
            size = path.stat().st_size
        except OSError:
            return []
        if size < self._events_offset:
            # File shrank (recreated?) -- reset rather than mis-seek.
            self._events_offset = 0
        if size == self._events_offset:
            return []
        try:
            with path.open("rb") as f:
                f.seek(self._events_offset)
                chunk = f.read(size - self._events_offset)
        except OSError:
            return []
        consumed = len(chunk)
        if not chunk.endswith(b"\n"):
            last_nl = chunk.rfind(b"\n")
            if last_nl == -1:
                return []  # no complete new line yet -- don't advance
            consumed = last_nl + 1
            chunk = chunk[:consumed]
        self._events_offset += consumed

        events: list[dict[str, Any]] = []
        for raw in chunk.split(b"\n"):
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                self._malformed_lines += 1
                continue
            if isinstance(obj, dict):
                events.append(obj)
        return events

    def _absorb_events(self, events: list[dict[str, Any]]) -> None:
        for ev in events:
            self._events_parsed += 1
            sid = str(ev.get("session_id") or "")
            if sid:
                self._current_session = sid
            stats = self._sessions.setdefault(sid, _SessionStats())
            name = str(ev.get("event") or "")
            if name == "llm:request":
                stats.llm_requests += 1
            elif name == "llm:response":
                stats.llm_responses += 1
                data = ev.get("data") or {}
                usage = data.get("usage") or {} if isinstance(data, dict) else {}
                try:
                    self._total_cost_usd += float(usage.get("cost_usd") or 0)
                except (TypeError, ValueError):
                    pass
                try:
                    ctx = int(usage.get("input_tokens") or 0) + int(
                        usage.get("cache_read_tokens") or 0
                    )
                except (TypeError, ValueError):
                    ctx = 0
                if ctx:
                    stats.last_context_tokens = ctx

    def _open_llm_requests(self) -> int:
        return sum(
            max(0, s.llm_requests - s.llm_responses) for s in self._sessions.values()
        )

    # -- ledger / inbox ------------------------------------------------------

    def _ledger_rows_this_run(self) -> list[tuple[float, dict[str, Any]]]:
        """(epoch, row) for ledger rows stamped at/after this run's start.

        The ledger is small (one line per source ever processed); a full
        tolerant read per tick is fine -- the tail-safety constraint is
        specifically about events.jsonl.
        """
        path = wiki_ledger(self.wiki)
        rows: list[tuple[float, dict[str, Any]]] = []
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return rows
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            ts = _parse_iso_ts(str(row.get("timestamp") or ""))
            # 2s slack: ledger stamps have seconds precision, run_start has
            # microseconds -- a completion in the same second must count.
            if ts is not None and ts >= self.run_start - 2.0:
                rows.append((ts, row))
        rows.sort(key=lambda pair: pair[0])
        return rows

    def _inbox_remaining(self) -> int:
        inbox = wiki_inbox(self.wiki)
        try:
            return sum(
                1 for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")
            )
        except OSError:
            return 0

    # -- checks ---------------------------------------------------------------

    def _check_liveness(self, now: float, concerns: list[dict[str, Any]]) -> float:
        """Check 1 (incidents a/c). Returns last-event age for the counters."""
        events_path = self.run_dir / "events.jsonl"
        try:
            last_activity = events_path.stat().st_mtime
        except OSError:
            last_activity = self.run_start
        age = max(0.0, now - last_activity)
        if age > self.config.stall_after_s:
            open_reqs = self._open_llm_requests()
            if open_reqs > 0:
                concerns.append(
                    {
                        "check": "liveness",
                        "level": VERDICT_SLOW,
                        "message": (
                            f"no events.jsonl append for {age:.0f}s, but "
                            f"{open_reqs} LLM request(s) open -- the engine "
                            "may be inside one long LLM call"
                        ),
                    }
                )
            else:
                concerns.append(
                    {
                        "check": "liveness",
                        "level": VERDICT_STALLED,
                        "message": (
                            f"no events.jsonl append for {age:.0f}s and no "
                            "open LLM request -- run appears wedged"
                        ),
                    }
                )
        return age

    def _check_pace(
        self,
        now: float,
        ledger_rows: list[tuple[float, dict[str, Any]]],
        inbox_remaining: int,
        concerns: list[dict[str, Any]],
    ) -> None:
        """Check 2: current source elapsed vs rolling mean of completed ones."""
        if inbox_remaining <= 0:
            return  # post-drain (re-weave etc.): no "current source" exists
        completions = [ts for ts, _ in ledger_rows]
        if len(completions) < self.config.pace_min_completed:
            return
        durations: list[float] = []
        prev = self.run_start
        for ts in completions:
            durations.append(max(0.0, ts - prev))
            prev = ts
        mean = sum(durations) / len(durations)
        if mean <= 0:
            return
        current_elapsed = max(0.0, now - completions[-1])
        if current_elapsed > self.config.pace_factor * mean:
            concerns.append(
                {
                    "check": "pace",
                    "level": VERDICT_SLOW,
                    "message": (
                        f"current source at {current_elapsed:.0f}s vs "
                        f"{mean:.0f}s rolling mean over {len(durations)} "
                        f"completed source(s) "
                        f"(>{self.config.pace_factor:g}x)"
                    ),
                }
            )

    def _check_ceiling(self, concerns: list[dict[str, Any]]) -> int:
        """Check 3 (incident a). Returns current-session call count."""
        stats = self._sessions.get(self._current_session)
        calls = stats.llm_requests if stats else 0
        threshold = self.config.llm_call_ceiling * self.config.ceiling_warn_frac
        if calls >= threshold:
            concerns.append(
                {
                    "check": "llm-call-ceiling",
                    "level": VERDICT_ANOMALY,
                    "session": self._current_session,
                    "message": (
                        f"session {self._current_session or '<unknown>'} at "
                        f"{calls}/{self.config.llm_call_ceiling} LLM calls "
                        f"(>= {self.config.ceiling_warn_frac:.0%} of the "
                        "orchestrator ceiling -- may be silently quarantined "
                        "if it runs out mid-verification)"
                    ),
                }
            )
        return calls

    def _check_context(self, concerns: list[dict[str, Any]]) -> int:
        """Check 4 (incident d). Returns current-session latest context size."""
        stats = self._sessions.get(self._current_session)
        ctx = stats.last_context_tokens if stats else 0
        threshold = self.config.context_limit_tokens * self.config.context_warn_frac
        if ctx >= threshold:
            concerns.append(
                {
                    "check": "context-growth",
                    "level": VERDICT_ANOMALY,
                    "session": self._current_session,
                    "message": (
                        f"session {self._current_session or '<unknown>'} last "
                        f"request context ~{ctx} tokens, approaching the "
                        f"{self.config.context_limit_tokens}-token provider "
                        "limit"
                    ),
                }
            )
        return ctx

    def _check_budget(self, concerns: list[dict[str, Any]]) -> None:
        """Check 5: cost is reported every tick; concern only over budget."""
        budget = self.config.budget_usd
        if budget is not None and self._total_cost_usd > budget:
            concerns.append(
                {
                    "check": "budget",
                    "level": VERDICT_ANOMALY,
                    "message": (
                        f"run cost ${self._total_cost_usd:.2f} exceeds the "
                        f"${budget:.2f} budget"
                    ),
                }
            )

    def _check_markers(self, now: float, concerns: list[dict[str, Any]]) -> None:
        """Check 6 (incident b): pages modified since last tick need markers."""
        scan_started = now
        flagged = 0
        for page in _iter_wiki_pages(self.wiki):
            try:
                if page.stat().st_mtime <= self._pages_scanned_at:
                    continue
                text = page.read_text(encoding="utf-8")
            except OSError:
                continue
            if _has_grounding_marker(text):
                continue
            if flagged < _MAX_PAGE_CONCERNS:
                concerns.append(
                    {
                        "check": "grounding-markers",
                        "level": VERDICT_ANOMALY,
                        "page": str(page.relative_to(self.wiki)),
                        "message": (
                            f"page {page.relative_to(self.wiki)} was modified "
                            "this tick but carries NEITHER marker form "
                            "([YYYY-MM-DD Label] / [src: Name]) -- it will "
                            "fail grounding certification"
                        ),
                    }
                )
            flagged += 1
        if flagged > _MAX_PAGE_CONCERNS:
            concerns.append(
                {
                    "check": "grounding-markers",
                    "level": VERDICT_ANOMALY,
                    "message": (
                        f"{flagged - _MAX_PAGE_CONCERNS} additional marker-less "
                        "modified page(s) suppressed"
                    ),
                }
            )
        self._pages_scanned_at = scan_started

    def _check_advisories(
        self, result: dict[str, Any] | None, concerns: list[dict[str, Any]]
    ) -> list[str]:
        """Check 7: surface NEW result.json advisories as they appear."""
        new: list[str] = []
        for adv in (result or {}).get("advisories") or []:
            adv_s = str(adv)
            if adv_s not in self._seen_advisories:
                self._seen_advisories.add(adv_s)
                new.append(adv_s)
                concerns.append(
                    {
                        "check": "advisory",
                        "level": VERDICT_ANOMALY,
                        "message": f"new run advisory: {adv_s}",
                    }
                )
        return new

    # -- tick ------------------------------------------------------------------

    def terminal_reason(self, result: dict[str, Any] | None) -> str | None:
        """Non-None when the run has reached a terminal state."""
        if not self.run_dir.is_dir():
            return "run directory removed"
        status = (result or {}).get("status")
        if status in (STATUS_FINAL, STATUS_SUPERSEDED):
            return f"result.json status={status}"
        return None

    def tick(self) -> dict[str, Any]:
        """Evaluate every check once; append + rewrite the two outputs.

        Returns the record appended to supervisor.jsonl (with an extra
        ``terminal`` field the caller uses to stop the loop).
        """
        now = self.now_fn()
        self._absorb_events(self._read_new_events())
        result = _read_json_file(self.run_dir / "result.json")

        concerns: list[dict[str, Any]] = []
        terminal = self.terminal_reason(result)

        last_event_age = self._check_liveness(now, concerns) if not terminal else 0.0
        ledger_rows = self._ledger_rows_this_run()
        inbox_remaining = self._inbox_remaining()
        if not terminal:
            self._check_pace(now, ledger_rows, inbox_remaining, concerns)
        session_calls = self._check_ceiling(concerns) if not terminal else 0
        session_ctx = self._check_context(concerns) if not terminal else 0
        self._check_budget(concerns)
        self._check_markers(now, concerns)
        new_advisories = self._check_advisories(result, concerns)

        verdict = VERDICT_HEALTHY
        for c in concerns:
            level = str(c.get("level", VERDICT_ANOMALY))
            if _VERDICT_RANK.get(level, 0) > _VERDICT_RANK[verdict]:
                verdict = level

        converged = sum(1 for _, r in ledger_rows if r.get("converged"))
        failed = sum(1 for _, r in ledger_rows if not r.get("converged"))

        record: dict[str, Any] = {
            "ts": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
            "run_id": self.run_dir.name,
            "verdict": verdict,
            "terminal": terminal is not None,
            "concerns": concerns,
            "counters": {
                "last_event_age_s": round(last_event_age, 1),
                "open_llm_requests": self._open_llm_requests(),
                "current_session": self._current_session,
                "current_session_llm_calls": session_calls,
                "current_session_context_tokens": session_ctx,
                "sources_converged": converged,
                "sources_failed": failed,
                "inbox_remaining": inbox_remaining,
                "cost_usd": round(self._total_cost_usd, 4),
                "events_parsed": self._events_parsed,
                "malformed_event_lines": self._malformed_lines,
                "result_status": (result or {}).get("status"),
                "result_verdict": (result or {}).get("verdict"),
            },
        }
        if terminal:
            record["terminal_reason"] = terminal
        if new_advisories:
            record["advisories_new"] = new_advisories

        self._write_outputs(record)
        return record

    # -- outputs (the only writes; both scoped to the run dir) ------------------

    def _write_outputs(self, record: dict[str, Any]) -> None:
        """FAIL-SOFT by contract: observability writes never raise."""
        if not self.run_dir.is_dir():
            return  # run dir gone -- nothing to write into (terminal path)
        try:
            with (self.run_dir / SUPERVISOR_JSONL).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except OSError:
            print(
                "! WARNING: could not append supervisor.jsonl -- monitoring continues",
                file=sys.stderr,
                flush=True,
            )
        try:
            path = self.run_dir / SUPERVISOR_STATUS_MD
            tmp = self.run_dir / f".{SUPERVISOR_STATUS_MD}.tmp"
            tmp.write_text(self._render_status_md(record), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            print(
                "! WARNING: could not rewrite supervisor-status.md "
                "-- monitoring continues",
                file=sys.stderr,
                flush=True,
            )

    def _render_status_md(self, record: dict[str, Any]) -> str:
        c = record["counters"]
        lines = [
            f"# Run supervisor -- {record['run_id']}",
            "",
            f"- **Verdict:** {record['verdict']}"
            + (" (terminal)" if record["terminal"] else ""),
            f"- **As of:** {record['ts']}",
            f"- **Sources:** {c['sources_converged']} converged, "
            f"{c['sources_failed']} failed, {c['inbox_remaining']} in inbox",
            f"- **Last event:** {c['last_event_age_s']}s ago "
            f"({c['open_llm_requests']} open LLM request(s))",
            f"- **Current session:** {c['current_session'] or '<none>'} -- "
            f"{c['current_session_llm_calls']} LLM call(s), "
            f"~{c['current_session_context_tokens']} context tokens",
            f"- **Cost so far:** ${c['cost_usd']:.4f}",
            f"- **result.json:** status={c['result_status']}, "
            f"verdict={c['result_verdict']}",
        ]
        if record.get("terminal_reason"):
            lines.append(f"- **Terminal:** {record['terminal_reason']}")
        lines.append("")
        concerns = record["concerns"]
        if concerns:
            lines.append(f"## Concerns ({len(concerns)})")
            lines.append("")
            for con in concerns:
                lines.append(
                    f"- `{con.get('check')}` [{con.get('level')}]: {con.get('message')}"
                )
        else:
            lines.append("## Concerns")
            lines.append("")
            lines.append("- none")
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Loop driver (what the CLI runs)
# ---------------------------------------------------------------------------


def run_supervise(
    wiki: Path,
    *,
    run_dir: Path | None = None,
    config: SupervisorConfig | None = None,
    once: bool = False,
    max_ticks: int | None = None,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Attach to a run and tick until it reaches a terminal state.

    ``once`` runs a single tick (scripting/cron-friendly); ``max_ticks`` is a
    test seam. Prints one stderr line per verdict TRANSITION only (the first
    tick establishing a verdict counts as a transition from nothing).
    Ctrl-C exits cleanly (0).
    """
    config = config or SupervisorConfig()
    if run_dir is None:
        run_dir = find_newest_run_dir(wiki)
    if run_dir is None or not run_dir.is_dir():
        print(
            f"wiki-weaver supervise: no ingest run dir found under "
            f"{wiki_runs(wiki)} (pass --run-dir to point at one)",
            file=sys.stderr,
            flush=True,
        )
        return 2

    sup = Supervisor(wiki, run_dir, config, now_fn=now_fn)
    print(
        f"wiki-weaver supervise: watching {run_dir} (interval {config.interval_s:g}s)",
        file=sys.stderr,
        flush=True,
    )
    ticks = 0
    try:
        while True:
            record = sup.tick()
            verdict = record["verdict"]
            if verdict != sup.last_verdict:
                top = record["concerns"][0]["message"] if record["concerns"] else ""
                suffix = f" -- {top}" if top else ""
                print(
                    f"wiki-weaver supervise: verdict "
                    f"{sup.last_verdict or 'start'} -> {verdict}{suffix}",
                    file=sys.stderr,
                    flush=True,
                )
                sup.last_verdict = verdict
            ticks += 1
            if record["terminal"]:
                print(
                    f"wiki-weaver supervise: run terminal "
                    f"({record.get('terminal_reason')}) -- exiting",
                    file=sys.stderr,
                    flush=True,
                )
                return 0
            if once or (max_ticks is not None and ticks >= max_ticks):
                return 0
            sleep_fn(config.interval_s)
    except KeyboardInterrupt:
        print(
            "wiki-weaver supervise: interrupted -- exiting cleanly",
            file=sys.stderr,
            flush=True,
        )
        return 0
