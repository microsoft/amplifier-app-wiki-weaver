"""wiki_weaver.init.capture_notes -- append this round's freeform interview
answer to the accumulating notes file.

Reads ``HUMAN.GATE.TEXT`` directly from ``os.environ`` (dots survive in the
env var name; this must never be referenced as a bash ``$VAR``, and the
free-form text itself must never transit a ``$substitution`` token).
APPENDS (never overwrites) -- pipeline/init.dot's interview loops multiple
rounds, and each round's answer must accumulate for ``evaluate`` to read.

FAIL-LOUD GUARD (CLI-CONTRACT.md "FAIL-LOUD requirement on every freeform
capture"): attractor's ``AutoApproveInterviewer.ask()`` returns the literal
string ``"auto-approved"`` for freeform questions. This text flows into
AGENTS.md and lens/ -- the highest-precedence layers (docs/DESIGN.md) -- so
the literal stub must never be accepted. Mirrors
``wiki_weaver.ingest.persist_guidance``'s guard.

Usage:
    python3 -m wiki_weaver.init.capture_notes --out .ai/interview-notes.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from wiki_weaver.lib import atomic_write_text, ensure_dir

AUTO_APPROVED_STUB = "auto-approved"
_ROUND_MARKER = "## Round "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.init.capture_notes")
    parser.add_argument("--out", required=True)
    return parser


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def append_round(out_path: Path, text: str) -> int:
    """Append this round's answer as a new section. Returns the round number
    just written (1-indexed)."""
    existing = out_path.read_text(encoding="utf-8") if out_path.is_file() else ""
    round_num = existing.count(_ROUND_MARKER) + 1
    separator = "" if not existing or existing.endswith("\n\n") else "\n"
    entry = f"{separator}{_ROUND_MARKER}{round_num} ({_timestamp()})\n\n{text}\n\n"
    ensure_dir(out_path.parent)
    atomic_write_text(out_path, existing + entry)
    return round_num


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = os.environ.get("HUMAN.GATE.TEXT", "")

    stripped = text.strip()

    # MEASURED 2026-07-26, live init.dot run under --on-human-gate auto-approve:
    # the freeform gate returned an EMPTY string, NOT the literal "auto-approved"
    # stub the councils found in interviewer.py. The stub-only guard below never
    # fired, empty text was appended as a "round", and the interview loop spun 5
    # rounds capturing 0 bytes while paying for an LLM `evaluate` call each time.
    #
    # An unanswerable gate must STOP the run. Both shapes of non-answer are fatal.
    if not stripped:
        print(
            "refusing to capture interview notes: HUMAN.GATE.TEXT is empty -- "
            "a freeform gate cannot be auto-answered, and an empty round would "
            "spin the interview loop while writing nothing. Re-run with "
            "--on-human-gate console (a real person) or supply the text directly.",
            file=sys.stderr,
        )
        return 1

    if stripped == AUTO_APPROVED_STUB:
        print(
            "refusing to capture interview notes: HUMAN.GATE.TEXT is the literal "
            "auto-approve stub, not a real answer -- a gate that cannot be answered "
            "must stop the run, never invent an answer",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out)
    round_num = append_round(out_path, text)
    print(f"round {round_num} appended to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
