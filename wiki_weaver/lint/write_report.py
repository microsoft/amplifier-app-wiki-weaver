"""wiki_weaver.lint.write_report -- deterministic report formatting.

Reads ``--structural`` (always present -- ``structural_validate`` always
runs) and ``--findings`` (present only when the structural gate let
``analyze`` run; pipeline/lint.dot skips ``analyze`` entirely on
``structural_bad``, so this must handle the argument's absence gracefully).
Writes a formatted report under ``--out`` (default ``reports/``, relative
to ``--wiki-root``). NEVER edits the wiki tree itself -- read-only w.r.t.
``wiki/``, ``sources/``, ``lens/``.

Usage:
    python3 -m wiki_weaver.lint.write_report --wiki-root <path> --out <report-dir> \\
        --structural .ai/structural-report.md --findings .ai/lint-findings.md
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, resolve_path

NO_STRUCTURAL = "(no structural report found)\n"
NO_FINDINGS = "(analysis pass skipped -- structural gate did not allow it to run)\n"


# THE single source of truth for this default. pipeline/lint.dot passes
# --out via the bare $report_dir substitution form (never
# ${report_dir:-reports} -- see wiki_weaver.lib.int_or_default's docstring
# for the general rationale); when the context key is absent the CLI
# receives an empty string, which main() below resolves to DEFAULT_OUT.
DEFAULT_OUT = "reports"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.lint.write_report")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--structural", required=True)
    # Optional: absent whenever lint.dot's structural gate routed straight to
    # write_report without running analyze.
    parser.add_argument("--findings", default=None)
    return parser


def _read_or_default(path_str: str | None, default: str) -> str:
    if not path_str:
        return default
    path = Path(path_str)
    if not path.is_file():
        return default
    return path.read_text(encoding="utf-8")


def build_report(structural: str, findings: str) -> str:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"
    return (
        "# wiki-weaver lint report\n\n"
        f"Generated: {timestamp}\n\n"
        "## Structural Validation\n\n"
        f"{structural}\n"
        "## Analysis Findings\n\n"
        f"{findings}\n"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    structural = _read_or_default(args.structural, NO_STRUCTURAL)
    findings = _read_or_default(args.findings, NO_FINDINGS)
    report = build_report(structural, findings)

    # args.out may be "" (bare-$var substitution's absent-key expansion --
    # see DEFAULT_OUT's comment above); argparse's own `default=` only
    # applies when the flag is omitted entirely, never when it's passed with
    # an empty string, so the empty-string fallback must be handled here.
    report_dir = resolve_path(wr, args.out or DEFAULT_OUT)
    ensure_dir(report_dir)
    out_file = report_dir / f"lint-{int(time.time())}.md"
    atomic_write_text(out_file, report)

    print(f"report written to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
