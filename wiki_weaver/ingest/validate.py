"""wiki_weaver.ingest.validate -- deterministic structural checks.

The writer cannot grade itself (docs/DESIGN.md §6, §9): broken links,
missing schema fields, and orphan pages are caught here, by code, not by
an LLM judge.

FLAGGED CONTRACT DISCREPANCY (see delivery report): CLI-CONTRACT.md's prose
says this tool should "never fail the pipeline on ordinary findings; only a
genuine tool crash should exit non-zero." But pipeline/ingest.dot's actual
tool_command wraps this call as:

    python3 -m wiki_weaver.ingest.validate ... && printf structural_ok || printf structural_bad

which can only route to structural_bad if THIS process exits non-zero when
ordinary findings are present -- otherwise validate -> reweave_bound is
dead code and every source structurally passes. Followed ingest.dot (the
graph that must actually run): exit 0 = no issues, exit 1 = issues found.
Both are "ordinary" outcomes handled by the graph's own bounded-retry path
(reweave_bound), not a hard pipeline crash -- an uncaught exception here
would ALSO exit non-zero but with a traceback on stderr, distinguishing a
genuine tool crash from an ordinary "bad" finding.

PAGE-SIZE TRIP-WIRE (the 55KB accretion finding, evals/results/TRANSCRIPT-EVAL.md):
the transcript eval produced a 55,022-byte page -- 5.2x the article-corpus
baseline's largest page -- with zero dangling links and zero orphans:
structurally fine by every OTHER check here, yet the same accretion
signature as the original 155KB two-file monolith, at smaller scale. A
subject recurring across many sources concentrates into one ever-growing
page. See ``PAGE_BYTE_CEILING`` below for the ceiling's justification.

This check deliberately does NOT change this tool's exit code (see
``find_oversized_pages`` call site below): PIPELINE-PHILOSOPHY.md §6
measured that an agent asked to make the SAME judgment call repeatedly
(here: how to split an oversized page) drifts -- arm-c's planner
re-decided "fold into an existing page vs create a new one" for every
source and produced fatter pages than the deterministic control, the exact
failure it was meant to fix. Routing this finding into reweave_bound's
retry loop would implicitly ask weave to "fix" the size on its own
judgment, unreviewed, every pass. Instead: detect, report LOUDLY (this
report file + stderr), and leave splitting to a human or a later, explicit,
reviewed step -- never automatic.

ZERO-TOUCH GUARD (the 27-source silent-empty-weave incident): a real ingest
run committed 26 sources as "accept" with pages_touched: 0 in every ledger
row -- the wiki contained only the two bootstrap pages afterward. weave
wrote nothing, THIS gate (before this fix) reported "No structural issues
found" every time (an empty wiki has no broken links, no missing
frontmatter, no orphans -- it has no pages to be wrong about), and the run
exited 0. Same failure class as the ${var:-default} defect (see
tests/test_dot_no_shell_default_form.py): the system reported success
while producing nothing.

No legitimate zero-touch case exists on THIS path. The two real "nothing
to ingest" outcomes in pipeline/ingest.dot are explicit and never reach
here: watermark's no_delta sentinel skips straight to commit_no_delta (no
segment_source, no retrieve_slice, no weave call at all), and a human's
explicit [C] Skip at review_gate happens AFTER this gate has already
passed, and routes to commit_skip with decision: skip -- never accept.
Every path that reaches THIS validator already ran weave expecting it to
write; the weave prompt itself always names an existing-or-new page to
fold into. So if weave ran and zero wiki pages changed, that is always the
defect this guard exists to catch -- fold it into the SAME issues list as
the structural checks above (unlike the oversized-page trip-wire) so it
exits non-zero -> structural_bad -> the existing bounded reweave_bound
retry, which already caps attempts and quarantines via commit_skip on
give-up. No new machinery, no warning-only path -- wire into what already
exists.

Measured via ``count_pages_touched`` (the same helper ``commit.py`` and
``budget.py`` already use for the ledger's ``pages_touched`` field -- git
diff + staged + untracked names under wiki/ relative to HEAD). Best-effort
like its existing callers: when ``git_available`` is false (the wiki_root
has no .git -- should not happen past init.persist, which always git
inits, but this module must not crash or fabricate a signal it cannot
measure) this check is silently skipped, exactly as count_pages_touched's
own callers already tolerate.

WRITE-PATH LEAK GUARD (the 13-of-20-pages-at-root incident): a real ingest
run wrote 13 real content pages, plus index.md, directly to wiki_root
instead of wiki/ across a single day's runs -- nondeterministically (two
sibling runs the same day landed every page correctly). Mechanism:
build_catalog/retrieve_slice handed weave BARE filenames (``"index.md"``,
never ``"wiki/index.md"``); weave's file tools are rooted at wiki_root, so
a bare relative write can resolve one directory too high. build_catalog.py,
retrieve_slice.py, and the weave prompt (ingest.dot) now all render pages
as ``wiki/<filename>`` (see ``lib.to_wiki_relpath``) to make the correct
path unambiguous -- this check is the DETECTION half, for when prevention
fails anyway: every OTHER check in this module (``find_structural_issues``,
``find_zero_touch``) is scoped to ``wr.wiki_dir`` and is structurally
incapable of seeing a page that landed one directory up, so a leaked page
sailed through validation as an invisible "success". ``find_root_level_leak``
below scans wiki_root itself (non-recursive) for ``*.md`` files that are
not legitimate root-level scaffolding (``lib.ROOT_LEVEL_MD_ALLOWLIST`` --
``log.md`` per the gist's append-only-log-at-root design, ``AGENTS.md`` per
init.persist) and folds any finding into the SAME ``issues`` list as the
structural checks above, so it fails loud -> structural_bad ->
reweave_bound, exactly like the zero-touch guard. No warning-only path, no
new machinery.

PERSON-SENSITIVITY GUARD (KNOWN_ISSUES.md #7 -- the ~40 passages a pre-share
review found in a 253-page wiki): synthesis re-derives content about named
people that raw-layer sanitization was supposed to keep out, and in one case
MANUFACTURED a characterization no source transcript makes. This module is
the one place both re-deriving paths already meet -- ``pipeline/ingest.dot``'s
``weave`` runs ``validate`` immediately downstream, and
``pipeline/synthesize.dot`` calls THIS SAME module by name after
``write_gap_page`` -- so ``wiki_weaver.ingest.person_check`` folds into the
SAME ``issues`` list as the structural checks above (unlike the oversized-page
trip-wire), exits non-zero -> ``structural_bad`` -> the existing bounded
retry, and terminally reaches review (ingest) or reverts the page
(synthesize). Scoped to content NEW since HEAD so an already-reviewed page
cannot wedge every later pass; see that module's docstring for why the check
is layered and where it is deterministic vs. heuristic. No new machinery, no
warning-only path, no flag to switch it off.

Usage:
    python3 -m wiki_weaver.ingest.validate --wiki-root <path> --out <report-path>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wiki_weaver.ingest.person_check import find_person_sensitivity
from wiki_weaver.lib import (
    NAV_PAGES,
    WikiRoot,
    atomic_write_text,
    count_pages_touched,
    ensure_dir,
    find_stray_root_pages,
    find_structural_issues,
    git_available,
    list_wiki_pages,
    resolve_path,
)

# Justified against real, measured data (see module docstring and
# evals/results/{ARM-COMPARISON,TRANSCRIPT-EVAL}.md):
#   article-corpus max page :  10,572 bytes  (largest NORMAL page ever seen)
#   transcript-corpus max   :  55,022 bytes  (the accretion FAILURE this
#                                             gate exists to catch)
#   corpus-wide mean        :  13,593 bytes
# 25,000 bytes sits at ~2.4x the largest normal page and ~1.8x the mean --
# comfortably above ordinary single-topic pages (real max stayed under
# 11KB) -- while the measured failure (55,022b) exceeds it by more than 2x,
# so this ceiling flags the real finding without flagging normal growth.
PAGE_BYTE_CEILING = 25_000


def find_oversized_pages(wiki_dir: Path, ceiling: int = PAGE_BYTE_CEILING) -> list[dict]:
    """Pages whose current size exceeds ``ceiling``, sorted largest-first.
    Re-scans the WHOLE wiki every call (like ``find_structural_issues``) --
    the accretion this catches is cumulative across many sources landing on
    the SAME page, not a single source's delta, so absolute size on every
    pass (not a step-to-step diff) is the correct signal. Detection only --
    never splits, never mutates the page."""
    findings: list[dict] = []
    for page_path in list_wiki_pages(wiki_dir):
        size = page_path.stat().st_size
        if size > ceiling:
            findings.append({"page": page_path.name, "bytes": size, "ceiling": ceiling})
    findings.sort(key=lambda f: f["bytes"], reverse=True)
    return findings


def find_zero_touch(root: Path, wiki_dir: Path) -> list[str]:
    """A weave that touched zero wiki pages -- see module docstring's
    ZERO-TOUCH GUARD. No legitimate zero-touch case exists on this path
    (see docstring); if this returns a non-empty list it MUST be folded
    into the same ``issues`` list that drives this tool's exit code, never
    a separate non-blocking section.

    Best-effort exactly like ``count_pages_touched``'s existing callers
    (``commit.py``, ``budget.py``): when ``git_available(root)`` is false
    this cannot be measured and is silently skipped, never fabricated."""
    if not git_available(root):
        return []
    if count_pages_touched(root, wiki_dir) > 0:
        return []
    return ["zero-touch: weave touched 0 wiki pages -- no content was written for this source"]


def find_root_level_leak(root: Path) -> list[str]:
    """See module docstring's WRITE-PATH LEAK GUARD: a content page that
    landed at wiki_root instead of wiki/ is always the write-path bug, never
    legitimate -- fold into the same ``issues`` list as the structural
    checks above (unlike the oversized-page trip-wire) so it fails loud ->
    structural_bad -> reweave_bound, the same bounded retry the zero-touch
    guard already uses. Not git-gated (unlike ``find_zero_touch``): a stray
    file's mere presence on disk is itself the defect, regardless of git
    state."""
    return [
        f"root-level leak: {p.name} is a content page directly at wiki_root, not wiki/ "
        "-- weave must write pages under wiki/ (see lib.to_wiki_relpath)"
        for p in find_stray_root_pages(root)
    ]


def _oversized_page_guidance(page: str, size: int, ceiling: int) -> str:
    """A specific, actionable next step for THIS oversized page -- never a
    generic "review manually" wave (the task this guidance responds to: a
    real 218,686-byte ``overview.md``, 8.7x over ceiling, with nothing ever
    pruning it).

    ``overview.md`` and ``index.md`` (``lib.NAV_PAGES``) are not ordinary
    content pages: they are the exact two files ``scan_arguments``
    (``pipeline/synthesize.dot``) is scoped to read on every corpus-wide
    argument scan (see ``check_nav_pages.py``). Unmonitored growth there
    does not just cost bytes -- it is the primary INPUT to the gap-finder,
    so continued accretion risks a silently degraded or truncated read that
    looks exactly like a clean, well-covered ``no_gap`` (the same failure
    class ``check_nav_pages.py`` already fails loud for on a missing/stub
    nav page; an oversized one is the opposite-but-related failure mode:
    too much to read, not too little). This function names the decision a
    human or a later, explicit pipeline pass needs to make -- it never
    makes that decision itself (module docstring's PAGE-SIZE TRIP-WIRE
    section: detect and report loudly, never auto-split)."""
    if page not in NAV_PAGES:
        return ""
    over_by = size / ceiling
    return (
        f"ACTION NEEDED: {page} is one of the two files scan_arguments (pipeline/synthesize.dot) reads on "
        f"every corpus-wide argument scan -- at {over_by:.1f}x the ceiling, continued growth risks a "
        "silently degraded or truncated read there (a false, clean 'no_gap' looks identical to a genuinely "
        "well-covered wiki). A human or a later, explicit pipeline pass needs to decide: (a) prune or "
        "consolidate claims that are stale, redundant, or already superseded by a page that exists, "
        f"(b) split {page} by topic/era into new pages linked from index.md, leaving a shorter synthesis "
        "in place, or (c) if the growth is legitimate for this corpus, raise --page-byte-ceiling "
        "deliberately and confirm scan_arguments' own read still fits. This is a report, not an action -- "
        "do not let the decision default by inaction."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.validate")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--page-byte-ceiling", type=int, default=PAGE_BYTE_CEILING)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    issues = find_structural_issues(wr.wiki_dir)
    issues.extend(find_zero_touch(wr.root, wr.wiki_dir))
    issues.extend(find_root_level_leak(wr.root))
    issues.extend(find_person_sensitivity(wr.root, wr.wiki_dir, wr.sources_dir))
    oversized = find_oversized_pages(wr.wiki_dir, args.page_byte_ceiling)

    out_path = resolve_path(wr, args.out)
    ensure_dir(out_path.parent)
    report_lines = ["# Structural Validation Report", ""]
    if issues:
        report_lines.append(f"{len(issues)} issue(s) found:")
        report_lines.extend(f"- {issue}" for issue in issues)
    else:
        report_lines.append("No structural issues found.")

    # A distinct, always-present section -- never buried inside the
    # structural-issues list above, and never silent when clean (see module
    # docstring: "must not silently pass like the last gate did").
    report_lines.append("")
    report_lines.append("## Page-size accretion trip-wire")
    report_lines.append("")
    if oversized:
        report_lines.append(
            f"{len(oversized)} page(s) exceed the {args.page_byte_ceiling}-byte ceiling "
            "(accretion signature -- NOT auto-split; review manually):"
        )
        for f in oversized:
            report_lines.append(f"- {f['page']}: {f['bytes']} bytes (ceiling {f['ceiling']})")
            guidance = _oversized_page_guidance(f["page"], f["bytes"], f["ceiling"])
            if guidance:
                report_lines.append(f"  {guidance}")
    else:
        report_lines.append(f"No page exceeds the {args.page_byte_ceiling}-byte ceiling.")
    atomic_write_text(out_path, "\n".join(report_lines) + "\n")

    for issue in issues:
        print(issue, file=sys.stderr)
    for f in oversized:
        print(
            f"OVERSIZED PAGE: {f['page']} is {f['bytes']} bytes (ceiling {f['ceiling']}) -- not auto-split",
            file=sys.stderr,
        )

    # Deliberately does NOT fold `oversized` into this exit code -- see
    # module docstring's PAGE-SIZE TRIP-WIRE section for why this must not
    # feed the automatic reweave_bound retry loop.
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
