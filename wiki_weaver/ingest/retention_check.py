"""wiki_weaver.ingest.retention_check -- deterministic shrinkage / heading-
loss detector, NOT an LLM judge.

Measured (docs/DESIGN.md §1, §9): this ~50-line class of detector caught
2/3 of real content losses; an LLM judge asked to spot the same losses
caught 0/3, at ~2x latency. Compares each wiki page weave touched against
its last-committed (git HEAD) version -- word-count shrinkage and heading
removal are both structural, both checkable by code.

Usage:
    python3 -m wiki_weaver.ingest.retention_check --wiki-root <path> \
        --snapshot-on-suspicion --out <report-path>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    git_available,
    git_show_head,
    list_wiki_pages,
    resolve_path,
)

SHRINK_THRESHOLD = 0.15  # flag when word count drops by more than 15%
MAX_REASON_LEN = 80


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.retention_check")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--snapshot-on-suspicion", action="store_true")
    parser.add_argument("--out", required=True)
    return parser


def _headings(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip().startswith("#")}


def check_page(old_text: str, new_text: str, name: str) -> list[str]:
    findings: list[str] = []
    old_words = len(old_text.split())
    new_words = len(new_text.split())
    if old_words > 0:
        shrink = (old_words - new_words) / old_words
        if shrink > SHRINK_THRESHOLD:
            findings.append(f"shrinkage: {name} {old_words}->{new_words} words ({shrink:.0%} smaller)")

    lost_headings = _headings(old_text) - _headings(new_text)
    for heading in sorted(lost_headings):
        findings.append(f"heading loss: {name} removed '{heading}'")
    return findings


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)

    findings: list[str] = []
    snapshots: dict[str, str] = {}

    if git_available(wr.root):
        for page_path in list_wiki_pages(wr.wiki_dir):
            rel = f"wiki/{page_path.name}"
            old_text = git_show_head(wr.root, rel)
            if old_text is None:
                continue  # new page this round -- nothing to shrink from
            new_text = page_path.read_text(encoding="utf-8")
            page_findings = check_page(old_text, new_text, page_path.name)
            if page_findings:
                findings.extend(page_findings)
                snapshots[page_path.name] = old_text
    else:
        print("git unavailable: retention_check has no baseline to compare against", file=sys.stderr)

    report_lines = ["# Retention Check Report", ""]
    if findings:
        report_lines.append(f"{len(findings)} suspicious finding(s):")
        report_lines.extend(f"- {f}" for f in findings)
    else:
        report_lines.append("No shrinkage or heading loss detected.")
    atomic_write_text(out_path, "\n".join(report_lines) + "\n")

    if findings and args.snapshot_on_suspicion and snapshots:
        snap_dir = wr.ai_dir / f"retention-snapshot-{int(time.time())}"
        ensure_dir(snap_dir)
        for name, old_text in snapshots.items():
            atomic_write_text(snap_dir / name, old_text)
        print(f"pre-weave snapshot written: {snap_dir}", file=sys.stderr)

    if findings:
        reason = findings[0]
        if len(reason) > MAX_REASON_LEN:
            reason = reason[: MAX_REASON_LEN - 3] + "..."
        flag = f"suspicious: {reason}"
    else:
        flag = "ok"

    print(json.dumps({"retention_flag": flag}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
