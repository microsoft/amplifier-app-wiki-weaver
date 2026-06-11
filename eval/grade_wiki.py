# pyright: reportMissingImports=false
#!/usr/bin/env python3
"""Lightweight, reusable graders for a woven wiki (PIPELINE_DESIGN.md §6).

Deliberately SMALL: high-signal checks the orchestrator can re-run, built on
top of the existing deterministic validator (pipeline/validate_wiki.py) plus a
ledger-/registry-integrity check. No framework, no new abstractions.

Graders
-------
- structural_clean(wiki)   reuse validate_wiki: 0 broken / 0 orphan / 0 uncited.
- no_duplicate_pages(wiki)  no ``*-2.md`` / near-duplicate concept fragments.
- ledger_integrity(wiki)    every ledger line is a REAL convergence: converged
                            == true, logs_dir exists, distinct source ids, and
                            the row's source id matches the .sources.json
                            registry (no confabulated lines).
- merge_accrual(wiki, page, expected_ids)
                            a shared concept page's frontmatter ``sources:``
                            accrued exactly the expected ids (compounding).

Scenario checks (encode the two design §6 scenarios as runnable asserts)
- grade_converge(wiki, expected_source_ids)  S-converge: a cluster of
  overlapping sources ALL converged + archived, validator clean, ids distinct,
  no duplicate pages, and at least one page accrued >1 source (real merge).
- grade_recover(before_failures, after_passed)  S-recover: an injected broken
  link was present (before) and is repaired (after validator exit 0).

CLI
---
    python grade_wiki.py converge <wiki_dir> --sources 1 2 3
    python grade_wiki.py integrity <wiki_dir>

Exit 0 == all graded checks pass; non-zero == at least one failed (prints why).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Reuse the single deterministic validator (DRY — same artifact the pipeline's
# validate node runs).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from validate_wiki import validate  # noqa: E402

LEDGER_NAME = ".processed.jsonl"
REGISTRY_NAME = ".sources.json"
ARCHIVE = "_archive"
# A merge-correctness anti-pattern: a per-source duplicate fragment page.
DUP_PAGE = re.compile(r"-\d+\.md$")
# Parse a frontmatter ``sources: [1, 2]`` list into a set of ints.
SOURCES_FM = re.compile(r"^sources:\s*\[([^\]]*)\]", re.MULTILINE)


class GradeResult:
    """Tiny PASS/FAIL accumulator (one per scenario)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, ok: bool, fail_msg: str, ok_msg: str | None = None) -> None:
        if ok:
            if ok_msg:
                self.notes.append(ok_msg)
        else:
            self.failures.append(fail_msg)

    @property
    def passed(self) -> bool:
        return not self.failures

    def report(self) -> str:
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.name}"]
        for n in self.notes:
            lines.append(f"    · {n}")
        for f in self.failures:
            lines.append(f"    ✗ {f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Primitive graders
# ---------------------------------------------------------------------------


def structural_clean(wiki: Path) -> tuple[bool, list[str]]:
    r = validate(wiki)
    return bool(r.get("passed")), list(r.get("failures", []))


def no_duplicate_pages(wiki: Path) -> list[str]:
    """Return any ``*-2.md`` style per-source duplicate concept fragments."""
    return [p.name for p in sorted(wiki.glob("*.md")) if DUP_PAGE.search(p.name)]


def _read_ledger(wiki: Path) -> list[dict]:
    led = wiki / LEDGER_NAME
    if not led.exists():
        return []
    rows: list[dict] = []
    for line in led.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _registry_ids(wiki: Path) -> set[int]:
    reg = wiki / REGISTRY_NAME
    if not reg.exists():
        return set()
    try:
        data = json.loads(reg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {
        int(e["id"]) for e in data.get("sources", []) if isinstance(e.get("id"), int)
    }


def ledger_integrity(wiki: Path) -> GradeResult:
    """Every ledger line is a real convergence with a real logs_dir.

    Confabulation guard already reverts agent-written lines; this asserts the
    surviving lines are well-formed CLI convergences and distinct.
    """
    res = GradeResult("ledger-integrity")
    rows = _read_ledger(wiki)
    res.check(bool(rows), "ledger is empty (no converged sources)")
    reg_ids = _registry_ids(wiki)
    seen_ids: list[int] = []
    for i, row in enumerate(rows):
        tag = f"ledger[{i}] {row.get('source', '?')}"
        # A genuine CLI convergence line carries these fields (an old/confabulated
        # line with source_number/file_path would be flagged here).
        res.check(
            row.get("converged") is True,
            f"{tag}: converged is not True ({row.get('converged')!r})",
        )
        ld = row.get("logs_dir")
        res.check(
            bool(ld) and Path(ld).is_dir(),
            f"{tag}: logs_dir missing or not a real dir ({ld!r})",
        )
        sid = row.get("source_id")
        res.check(sid is not None, f"{tag}: no source_id field")
        if sid is not None:
            seen_ids.append(int(sid))
            res.check(
                (not reg_ids) or int(sid) in reg_ids,
                f"{tag}: source_id {sid} not in registry {sorted(reg_ids)}",
            )
        archived = row.get("archived_to")
        res.check(
            bool(archived) and Path(archived).is_file(),
            f"{tag}: archived_to missing on disk ({archived!r})",
        )
    res.check(
        len(seen_ids) == len(set(seen_ids)),
        f"duplicate source ids in ledger: {seen_ids}",
        f"{len(seen_ids)} distinct converged source id(s): {sorted(set(seen_ids))}",
    )
    return res


def page_sources(wiki: Path, page_name: str) -> set[int]:
    """The set of source ids in a page's frontmatter ``sources:`` list."""
    p = wiki / page_name
    if not p.is_file():
        return set()
    m = SOURCES_FM.search(p.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return set()
    ids: set[int] = set()
    for tok in m.group(1).split(","):
        tok = tok.strip()
        if tok.isdigit():
            ids.add(int(tok))
    return ids


def max_source_accrual(wiki: Path) -> tuple[str, set[int]]:
    """Return the content page that accrued the MOST source ids (merge proof)."""
    best_page = ""
    best: set[int] = set()
    for p in sorted(wiki.glob("*.md")):
        ids = page_sources(wiki, p.name)
        if len(ids) > len(best):
            best, best_page = ids, p.name
    return best_page, best


# ---------------------------------------------------------------------------
# Scenario checks (design §6)
# ---------------------------------------------------------------------------


def grade_converge(wiki: Path, expected_source_ids: list[int]) -> GradeResult:
    """S-converge: a cluster of overlapping sources ALL converge + archive,
    with a clean validator and a correct (compounding) merge.
    """
    res = GradeResult(f"S-converge (sources {expected_source_ids})")

    ok, failures = structural_clean(wiki)
    res.check(ok, f"validator FAIL: {'; '.join(failures)}", "validator clean (exit 0)")

    dups = no_duplicate_pages(wiki)
    res.check(
        not dups, f"duplicate concept pages present: {dups}", "no duplicate pages"
    )

    # All expected sources are archived + on the ledger as converged.
    rows = _read_ledger(wiki)
    converged_ids = {
        int(r["source_id"])
        for r in rows
        if r.get("converged") is True and r.get("source_id") is not None
    }
    missing = sorted(set(expected_source_ids) - converged_ids)
    res.check(
        not missing,
        f"source ids never converged: {missing} (ledger has {sorted(converged_ids)})",
        f"all {len(expected_source_ids)} sources converged: {sorted(converged_ids)}",
    )
    arc = wiki / ARCHIVE
    archived = {p.name for p in arc.iterdir()} if arc.is_dir() else set()
    res.check(
        len(archived) >= len(expected_source_ids),
        f"archive has {len(archived)} file(s), expected >= {len(expected_source_ids)}",
        f"{len(archived)} source(s) archived",
    )

    led = ledger_integrity(wiki)
    res.failures.extend(led.failures)
    res.notes.extend(led.notes)

    # Real merge: at least one page accrued more than one source id.
    page, ids = max_source_accrual(wiki)
    res.check(
        len(ids) >= 2,
        "no page accrued >1 source id — sources did not compound (merge failed)",
        f"merge proof: {page} accrued sources {sorted(ids)}",
    )
    return res


def grade_recover(before_failures: list[str], after_passed: bool) -> GradeResult:
    """S-recover: a deliberately injected broken link was present before, and
    the loop repaired it (validator back to exit 0) within the cycle budget.
    """
    res = GradeResult("S-recover (injected broken link repaired)")
    res.check(
        bool(before_failures),
        "no failures before — nothing to recover from (injection didn't take)",
        f"injected failure observed: {'; '.join(before_failures)}",
    )
    res.check(
        after_passed,
        "validator still FAILS after refine — loop did NOT close",
        "validator clean after refine — loop closed the failure",
    )
    return res


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade a woven wiki (design §6).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_c = sub.add_parser("converge", help="S-converge grader")
    p_c.add_argument("wiki_dir", type=Path)
    p_c.add_argument(
        "--sources",
        type=int,
        nargs="+",
        required=True,
        help="expected converged source ids (e.g. --sources 1 2 3)",
    )

    p_i = sub.add_parser("integrity", help="ledger-integrity grader")
    p_i.add_argument("wiki_dir", type=Path)

    args = ap.parse_args()
    if not args.wiki_dir.is_dir():
        print(f"FAIL: wiki dir not found: {args.wiki_dir}", file=sys.stderr)
        return 2

    if args.cmd == "converge":
        res = grade_converge(args.wiki_dir, args.sources)
    else:
        res = ledger_integrity(args.wiki_dir)

    print(res.report())
    return 0 if res.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
