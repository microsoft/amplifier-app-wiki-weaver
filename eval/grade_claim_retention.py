# pyright: reportMissingImports=false
#!/usr/bin/env python3
"""Claim-retention grader CLI -- thin re-export shim.

WHY THIS FILE IS NOW A SHIM: ``RetentionResult`` and ``grade_claim_retention()``
(plus the ``_build_judge_fn`` LLM-judge plumbing they depend on) now live in
``wiki_weaver/grading.py`` (SHIPPED) -- the same dependency-inversion fix
applied to ``GradeResult``/``grade_overview()`` in commit 7062a17 (PR #29).

``wiki_weaver/retention.py`` (SHIPPED runtime code, wired into
``wiki_weaver/lib.py``'s ``ingest()``) needs to call ``grade_claim_retention()``
as an independent claim-retention re-check, and ``eval/`` is deliberately
excluded from the installed wheel (see ``pyproject.toml``'s
``[tool.hatch.build.targets.wheel]``) -- so shipped code cannot import from
here. This module now imports FROM ``wiki_weaver.grading`` and re-exports, so
every existing caller (this file's own CLI below, ``eval/test_claim_retention.py``)
keeps working completely unchanged. Pure relocation, not a behavior change.

Usage (standalone CLI)
----------------------
    python eval/grade_claim_retention.py before_page.md after_wiki_dir/

Programmatic API
----------------
    from wiki_weaver.grading import grade_claim_retention
    result = grade_claim_retention(before_text, Path("wiki/"))
    print(result.report())
    assert result.passed   # zero SILENTLY_LOST
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path: make wiki_weaver importable regardless of cwd (dev checkout).
# ---------------------------------------------------------------------------
_EVAL = Path(__file__).resolve().parent
_REPO = _EVAL.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# RetentionResult, grade_claim_retention(), and the _build_judge_fn LLM-judge
# plumbing now live in the SHIPPED wiki_weaver package (wiki_weaver/grading.py),
# not here -- see module docstring above. Re-exported for backward compat with
# this file's own CLI and eval/test_claim_retention.py.
from wiki_weaver.grading import (  # noqa: E402
    RetentionResult,
    _build_judge_fn,
    grade_claim_retention,
)

__all__ = [
    "RetentionResult",
    "grade_claim_retention",
    "_build_judge_fn",
]


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Grade claim retention across a wiki re-write."
    )
    parser.add_argument(
        "before_page",
        type=Path,
        help="Path to the page .md file BEFORE the re-write.",
    )
    parser.add_argument(
        "after_wiki_dir",
        type=Path,
        help="Directory containing wiki .md files AFTER the re-write.",
    )
    args = parser.parse_args()

    if not args.before_page.is_file():
        print(f"ERROR: before_page not found: {args.before_page}", file=sys.stderr)
        sys.exit(2)
    if not args.after_wiki_dir.is_dir():
        print(
            f"ERROR: after_wiki_dir not found: {args.after_wiki_dir}", file=sys.stderr
        )
        sys.exit(2)

    before_text = args.before_page.read_text(encoding="utf-8")
    result = grade_claim_retention(before_text, args.after_wiki_dir)
    print(result.report())
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    _cli()
