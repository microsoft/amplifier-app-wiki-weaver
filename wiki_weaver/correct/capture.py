"""wiki_weaver.correct.capture -- write a natural-language correction to a
file, never shell-interpolated.

Mirrors ``wiki_weaver.ingest.persist_guidance``'s exact discipline (see that
module's docstring): the free-form correction text must never transit a
``$substitution`` token in ``tool_command`` (a multi-line, human/LLM-authored
string is the highest-risk shell-injection site this project has -- see
``pipeline/correct.dot``'s header). ``capture_correction``'s ``tool_env``
carries BOTH ``CORRECTION_TEXT`` (set when the correction arrived via
``--param correction_text=...``, the non-interactive path) and
``HUMAN.GATE.TEXT`` (set when it arrived via the freeform ``intake`` gate) --
exactly one is expected to be non-empty for a given run, since
``correct.dot``'s ``start`` node forks unconditionally on
``context.correction_provided`` before either path can fire.

FAIL-LOUD GUARD (``pipeline/CLI-CONTRACT.md``'s "FAIL-LOUD requirement on
every freeform capture"): this is one of the three capture tools the
contract explicitly names. Attractor's ``AutoApproveInterviewer.ask()`` on a
freeform question falls through and returns the literal string
``"auto-approved"`` -- accepting that stub would write it straight into
``lens/corrections/`` (one of the two highest-precedence layers in
``docs/DESIGN.md`` \u00a74), so it is rejected here, exit non-zero, never
persisted.

Usage:
    python3 -m wiki_weaver.correct.capture --out .ai/correction-text.md
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from wiki_weaver.lib import atomic_write_text, ensure_dir

AUTO_APPROVED_STUB = "auto-approved"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.correct.capture")
    parser.add_argument("--out", required=True)
    return parser


def _resolve_text() -> str:
    """Prefer ``CORRECTION_TEXT`` (the external ``--param`` intake path);
    fall back to ``HUMAN.GATE.TEXT`` (the freeform ``intake`` gate path).
    Read directly from ``os.environ`` -- ``HUMAN.GATE.TEXT``'s dotted name
    must never be referenced as a bash ``$VAR`` (see
    ``persist_guidance.py``'s identical precedent)."""
    correction_text = os.environ.get("CORRECTION_TEXT", "")
    if correction_text.strip():
        return correction_text
    return os.environ.get("HUMAN.GATE.TEXT", "")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    text = _resolve_text()

    if text.strip() == AUTO_APPROVED_STUB:
        print(
            "refusing to capture correction: the freeform answer is the literal "
            "auto-approve stub, not a real correction -- this text would flow into "
            "lens/corrections/, one of the two highest-precedence layers in the lens "
            "(docs/DESIGN.md \u00a74). A gate that cannot be answered must stop the run, "
            "never invent a correction",
            file=sys.stderr,
        )
        return 1

    if not text.strip():
        print(
            "refusing to capture correction: no correction text was supplied "
            "(neither --param correction_text nor a freeform intake answer) -- "
            "never persist an empty correction",
            file=sys.stderr,
        )
        return 1

    out_path = Path(args.out)
    ensure_dir(out_path.parent)
    atomic_write_text(out_path, text)
    print(f"correction captured to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
