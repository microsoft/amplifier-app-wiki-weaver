"""wiki_weaver.lint.structural_validate -- deterministic structural checks,
read-only.

Reuses ``wiki_weaver.lib.find_structural_issues`` -- the SAME structural
check ``wiki_weaver.ingest.validate`` uses, factored into lib.py precisely
so lint does not duplicate this logic (CLI-CONTRACT.md: "the same
structural-check contract"). Unlike ``ingest.validate`` (whose sentinel is
produced by ingest.dot's OWN shell ``&&``/``||`` wrapper around this
process's exit code), pipeline/lint.dot's tool_command has NO shell
wrapper -- this process must print the routing sentinel on stdout itself.

Never edits the wiki -- only reads it.

Usage:
    python3 -m wiki_weaver.lint.structural_validate --wiki-root <path> --out .ai/structural-report.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, find_structural_issues, resolve_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.lint.structural_validate")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    issues = find_structural_issues(wr.wiki_dir)

    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)
    report_lines = ["# Structural Validation Report", ""]
    if issues:
        report_lines.append(f"{len(issues)} issue(s) found:")
        report_lines.extend(f"- {issue}" for issue in issues)
    else:
        report_lines.append("No structural issues found.")
    atomic_write_text(out_path, "\n".join(report_lines) + "\n")

    for issue in issues:
        print(issue, file=sys.stderr)

    # lint always produces a report, even a degraded one -- never a hard
    # pipeline failure on ordinary findings; the sentinel carries the
    # routing decision, not the exit code.
    print("structural_bad" if issues else "structural_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
