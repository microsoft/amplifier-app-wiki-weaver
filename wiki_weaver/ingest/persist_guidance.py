"""wiki_weaver.ingest.persist_guidance -- write review_gate steering text to
a file, never shell-interpolated.

Reads ``HUMAN.GATE.TEXT`` directly from ``os.environ`` (dots survive in the
env var name; this must never be referenced as a bash ``$VAR``, and the
free-form text itself must never transit a ``$substitution`` token).

FAIL-LOUD GUARD (flagged in the delivery report): CLI-CONTRACT.md's
per-command entry for ``persist_guidance`` does not repeat the "reject the
literal auto-approved stub" requirement -- only ``init.capture_notes``,
``canon.capture``, and ``correct.capture`` are named in the contract's
"FAIL-LOUD requirement on every freeform capture" section. But
pipeline/ingest.dot's own comment directly above ``collect_guidance`` (the
freeform hexagon this tool captures) says explicitly: "The capture tool
downstream MUST reject the literal 'auto-approved' and exit non-zero."
``persist_guidance`` IS that downstream capture tool, so the guard is
implemented here too, resolving the omission in favor of the safety-
critical requirement documented in the graph itself.

Usage:
    python3 -m wiki_weaver.ingest.persist_guidance --out .ai/review-guidance.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from wiki_weaver.lib import atomic_write_text, ensure_dir

AUTO_APPROVED_STUB = "auto-approved"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.persist_guidance")
    parser.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = os.environ.get("HUMAN.GATE.TEXT", "")

    if text.strip() == AUTO_APPROVED_STUB:
        print(
            "refusing to persist guidance: HUMAN.GATE.TEXT is the literal "
            "auto-approve stub, not real steering text -- a gate that "
            "cannot be answered must stop the run, never invent an answer",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out)
    ensure_dir(out_path.parent)
    atomic_write_text(out_path, text)
    print(f"guidance persisted to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
