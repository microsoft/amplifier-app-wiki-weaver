"""wiki_weaver.ingest.quarantine_brief -- route an exhausted re-weave to
``review_gate`` ONCE, before automatic quarantine takes over.

THE DEFECT THIS FIXES (see delivery report): ``reweave_bound``'s ``give_up``
branch went straight to ``commit_skip`` -- the reviewer (human or proxy)
never saw the source at all. In a live 20-source run this silently
destroyed three substantive, on-topic sources, one of them assessed as
"arguably the single most central-to-thesis article in the whole corpus."
The ledger recorded these as ``decision: skip``, indistinguishable from a
reviewer's deliberate Skip unless you read the commit contents.

THE FIX: on the FIRST ``give_up`` for a source, build a ``review_gate``
brief that makes the quarantine situation unmistakable -- ``validate``'s
actual findings, quoted verbatim, plus how many re-weave attempts were
spent -- and route to ``review_gate``, the SAME three choices (Accept /
Guide / Skip) a normal review pass gets. The reviewer decides; nothing is
silently thrown away.

THE TERMINATION GUARANTEE (see pipeline/ingest.dot's header and the
delivery report's termination argument): ``reweave_bound``'s own attempt
counter is UNTOUCHED by this fix -- it still caps retries at ``--max``
exactly as before (no eval arm, no existing test, no existing contract of
that module changes; ``evals/arms/ingest-b.dot`` and ``ingest-c.dot`` also
call ``reweave_bound`` and remain completely unaffected). This module adds
a SEPARATE, one-shot latch -- ``quarantine_state_file``, keyed by
(source id, segment index) exactly like ``reweave_attempts_file`` -- that
is consulted ONLY when ``reweave_bound`` has already said ``give_up``:

  - latch not yet set for this (source, segment) -> this is the FIRST
    exhaustion for this segment. Write the quarantine brief, set the
    latch, route to ``review_gate`` (``quarantine_status=needs_review``).
  - latch already set for this (source, segment) -> a SECOND ``give_up``
    has occurred for the SAME segment (e.g. a guided re-weave, offered by
    the first pass through this node, itself failed validation again).
    Route straight to ``commit_quarantine``
    (``quarantine_status=already_reviewed``) -- no second chance, no
    possibility of an unbounded reviewer<->reweave cycle.

SEGMENT-SCOPED, NOT JUST SOURCE-SCOPED (docs/KNOWN_ISSUES.md #3): this
latch predates segmentation (DESIGN.md §5) and was keyed by source id
alone. Since every segment of a multi-segment source shares one source id,
whichever segment first exhausted its retries (nearly always segment 1)
consumed the rescue for the ENTIRE source -- every later segment that also
exhausted (which a source-wide validation defect makes near-certain, since
``reweave_bound``'s blind retry cannot fix an issue unrelated to what that
segment itself wrote) found the latch already burned and auto-quarantined
with no review, ever. Confirmed against a real 73-source production run:
segment 1 quarantined 0/73 times (it reliably spent the source's one
rescue) while segment 2+ quarantined 14 times against 4 accepts. Keying by
(source_id, segment_index) instead gives every segment -- exactly like a
freshly-selected source -- its own one-shot rescue, restoring
segment_source.py's stated design intent ("each segment is a unit of
work") at the accounting level. See ``lib.read_current_segment_index``'s
docstring for the full mechanism and evidence.

Because the latch is a one-way flip (never reset except for a genuinely
different (source, segment) identity, identical discipline to
``reweave_bound.py``'s own stale-attempts handling), a single SEGMENT can
reach ``review_gate`` via this path AT MOST ONCE, ever. Combined with
``reweave_bound``'s own unmodified cap, the total possible ``weave``
invocations for one SEGMENT is bounded by a fixed constant:
``(1 initial weave) + (max_reweave retries) + (1 additional guided
re-weave, if a human/proxy chooses Guide on the one quarantine review)`` --
never unbounded, regardless of how many times a reviewer might otherwise
be tempted to choose Guide. Summed across however many segments a source
splits into, the total per-SOURCE bound is still finite (segment count
itself is bounded by source size / segment_max_bytes) -- this generalizes
the termination guarantee, it does not weaken it.

Usage:
    python3 -m wiki_weaver.ingest.quarantine_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.ingest.review_brief import build_brief
from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    read_current_segment_index,
    read_current_source_id,
)

# Keep the quoted validation findings bounded -- this text goes into a
# prompt, not a log file (same discipline as review_brief.MAX_PAGES_LISTED).
MAX_REPORT_CHARS = 2000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.quarantine_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _load_latch(path: Path, source_id: str, segment_index: int) -> bool:
    """Whether THIS (source, segment) has already been offered its one
    post-exhaustion review. A latch left over from a PREVIOUS source id --
    OR a previous SEGMENT of this same source -- resets to False -- never
    silently reused (identical discipline to reweave_bound.py's own
    stale-attempts handling, extended to segment granularity; see module
    docstring's SEGMENT-SCOPED section)."""
    if not path.is_file():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(state, dict) or state.get("source_id") != source_id:
        return False
    try:
        stale_segment = int(state.get("segment_index", 1))
    except (TypeError, ValueError):
        stale_segment = 1
    if stale_segment != segment_index:
        return False
    return bool(state.get("reviewed", False))


def _attempts_spent(wr: WikiRoot, source_id: str, segment_index: int) -> int:
    """Best-effort read of reweave_bound's own counter, for the brief text
    only -- never re-derives, resets, or mutates reweave_bound's state
    (that module's contract is completely untouched by this fix)."""
    path = wr.reweave_attempts_file
    if not path.is_file():
        return 0
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(state, dict) or state.get("source_id") != source_id:
        return 0
    try:
        if int(state.get("segment_index", 1)) != segment_index:
            return 0
    except (TypeError, ValueError):
        pass
    try:
        return int(state.get("attempts", 0))
    except (TypeError, ValueError):
        return 0


def _validation_findings(wr: WikiRoot) -> str:
    """The real, quotable structural-validation findings -- never a
    paraphrase. Best-effort: an unreadable/missing report is reported
    honestly, never fabricated as clean."""
    path = wr.validation_report_file
    if not path.is_file():
        return "(no validation report available)"
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "(validation report could not be read)"
    if len(text) > MAX_REPORT_CHARS:
        text = text[:MAX_REPORT_CHARS] + "\n... (truncated)"
    return text


def build_quarantine_brief(wr: WikiRoot, source_id: str, segment_index: int) -> str:
    """The normal weave brief (what review_gate would always show), plus a
    distinct, unmissable quarantine section: how many re-weave attempts
    were spent, and the real structural-validation findings from the most
    recent attempt."""
    base = build_brief(wr, source_id)
    attempts = _attempts_spent(wr, source_id, segment_index)
    findings = _validation_findings(wr)
    lines = [
        base,
        "",
        f"=== QUARANTINE: structural validation failed {attempts} time(s) for this source ===",
        "This weave has exhausted its automatic re-weave attempts. Without your input it will be",
        "silently skipped (quarantined) and this content will be LOST -- no further review will",
        "ever be offered for this source.",
        "",
        "Structural validation findings (most recent attempt):",
        findings,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    segment_index = read_current_segment_index(wr)
    ensure_dir(wr.ai_dir)
    latch_path = wr.quarantine_state_file
    already_reviewed = _load_latch(latch_path, source_id, segment_index)

    if already_reviewed:
        print(
            f"{source_id} segment {segment_index} already had its one post-exhaustion review "
            "-- auto-quarantining, no more review",
            file=sys.stderr,
        )
        print(json.dumps({"review_brief": "", "quarantine_status": "already_reviewed"}))
        return 0

    brief = build_quarantine_brief(wr, source_id, segment_index)
    atomic_write_text(
        wr.review_brief_file,
        json.dumps(
            {"source_id": source_id, "stage": "review_gate", "brief": brief, "quarantined": True},
            indent=2,
        )
        + "\n",
    )
    atomic_write_text(
        latch_path,
        json.dumps({"source_id": source_id, "segment_index": segment_index, "reviewed": True}),
    )

    print(
        f"{source_id} segment {segment_index} exhausted its re-weave attempts -- routing to review_gate (one-time)",
        file=sys.stderr,
    )
    print(json.dumps({"review_brief": brief, "quarantine_status": "needs_review"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
