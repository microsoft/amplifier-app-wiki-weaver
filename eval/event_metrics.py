#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""CI event metrics reader for wiki-weaver ask runs.

Reads LOCAL context-intelligence events.jsonl only — no CI server dependency.
Server-optional by design: the hook always writes local JSONL regardless of whether
the server is reachable. Returns zeros/None (fail-soft) if events are not found.

Join path:
    logs_dir/{node}/status.json  →  session_id
    → ~/.amplifier/projects/*/sessions/{session_id}/context-intelligence/events.jsonl

Metrics extracted per ask run (summed across all node sessions in the run):
    cost_usd          sum of llm:response.usage.cost_usd
    input_tokens      sum of llm:response.usage.input_tokens
    output_tokens     sum of llm:response.usage.output_tokens
    cache_read_tokens sum of llm:response.usage.cache_read_tokens
    wall_time_s       time-span from first to last event timestamp (seconds)
    pages_read        distinct wiki .md files seen in artifact:read events
    tool_calls        count of tool:pre events (one per tool invocation)
    events_found      True if at least one events.jsonl was located

CLI:
    python eval/event_metrics.py <wiki> <logs_dir>
"""

from __future__ import annotations

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _find_session_events(session_id: str) -> Path | None:
    """Glob ~/.amplifier/projects/ for the CI events.jsonl of a session_id.

    Does NOT assume the project slug — searches across all projects so the
    reader is robust to different wiki_dir locations and Amplifier versions.
    """
    projects_dir = Path.home() / ".amplifier" / "projects"
    if not projects_dir.is_dir():
        return None
    pattern = f"*/sessions/{session_id}/context-intelligence/events.jsonl"
    for match in projects_dir.glob(pattern):
        if match.is_file():
            return match
    return None


def _collect_session_ids(logs_dir: Path) -> list[str]:
    """Return all non-null session_ids from logs_dir/{node}/status.json files."""
    session_ids: list[str] = []
    for status_file in sorted(logs_dir.glob("*/status.json")):
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        sid = data.get("session_id")
        if sid and isinstance(sid, str):
            session_ids.append(sid)
    return session_ids


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask_run_metrics(wiki: Path, logs_dir: Path) -> dict:
    """Read CI event metrics for a completed wiki-weaver ask run.

    Parameters
    ----------
    wiki:
        Path to the wiki directory.  Kept for API symmetry with callers that
        naturally have it; not used for path derivation (we glob instead).
    logs_dir:
        Path to the ask run logs directory, e.g.
        ``<wiki>/.runs/ask-20260613-112339/``.

    Returns
    -------
    dict with keys:
        cost_usd          float   (0.0 if not found)
        input_tokens      int     (0 if not found)
        output_tokens     int     (0 if not found)
        cache_read_tokens int     (0 if not found)
        wall_time_s       float|None  (None if not found)
        pages_read        int     (0 if not found)
        tool_calls        int     (0 if not found)
        events_found      bool

    Always returns a complete dict; never raises.
    """
    result: dict = {
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "wall_time_s": None,
        "pages_read": 0,
        "tool_calls": 0,
        "events_found": False,
    }

    try:
        logs_dir = Path(logs_dir)
        if not logs_dir.is_dir():
            return result

        session_ids = _collect_session_ids(logs_dir)
        if not session_ids:
            return result

        pages_seen: set[str] = set()
        tool_pre_count = 0
        first_ts: float | None = None
        last_ts: float | None = None
        events_found = False

        import datetime  # noqa: PLC0415

        for session_id in session_ids:
            events_file = _find_session_events(session_id)
            if events_file is None:
                continue

            events_found = True

            try:
                with events_file.open(encoding="utf-8") as fh:
                    for raw_line in fh:
                        raw_line = raw_line.strip()
                        if not raw_line:
                            continue
                        # Stream line-by-line: NEVER load entire file into
                        # memory (events.jsonl can be 100k+ tokens on long runs)
                        try:
                            obj = json.loads(raw_line)
                        except json.JSONDecodeError:
                            continue

                        event: str = obj.get("event", "")
                        data: dict = obj.get("data") or {}
                        ts_str: str = obj.get("timestamp", "")

                        # Wall-time: track first/last event timestamp
                        if ts_str:
                            try:
                                ts_dt = datetime.datetime.fromisoformat(
                                    ts_str.replace("Z", "+00:00")
                                )
                                ts = ts_dt.timestamp()
                                if first_ts is None or ts < first_ts:
                                    first_ts = ts
                                if last_ts is None or ts > last_ts:
                                    last_ts = ts
                            except (ValueError, TypeError):
                                pass

                        # LLM usage
                        if event == "llm:response":
                            usage: dict = data.get("usage") or {}
                            result["cost_usd"] += float(usage.get("cost_usd") or 0)
                            result["input_tokens"] += int(
                                usage.get("input_tokens") or 0
                            )
                            result["output_tokens"] += int(
                                usage.get("output_tokens") or 0
                            )
                            result["cache_read_tokens"] += int(
                                usage.get("cache_read_tokens") or 0
                            )

                        # Distinct wiki pages read
                        elif event == "artifact:read":
                            path_str: str = data.get("path", "")
                            if path_str and path_str.endswith(".md"):
                                pages_seen.add(path_str)

                        # Tool call count (tool:pre fires once per invocation)
                        elif event == "tool:pre":
                            tool_pre_count += 1

            except OSError:
                continue

        result["events_found"] = events_found
        result["pages_read"] = len(pages_seen)
        result["tool_calls"] = tool_pre_count

        # Wall time from event timestamps
        if first_ts is not None and last_ts is not None and last_ts > first_ts:
            result["wall_time_s"] = round(last_ts - first_ts, 2)

    except Exception:  # noqa: BLE001
        # Fail-soft: any unexpected error returns the zero-initialized result
        pass

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description="Read CI event metrics for a wiki-weaver ask run (local JSONL only)."
    )
    ap.add_argument("wiki", type=Path, help="wiki directory")
    ap.add_argument(
        "logs_dir",
        type=Path,
        help="ask run logs directory (e.g. wiki/.runs/ask-20260613-112339/)",
    )
    args = ap.parse_args()

    metrics = ask_run_metrics(args.wiki, args.logs_dir)
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
