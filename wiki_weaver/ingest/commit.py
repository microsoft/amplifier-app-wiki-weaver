"""wiki_weaver.ingest.commit -- archive the source, append ledger + log,
one git commit. The only node that makes ingesting a source irreversible.

Ordering law (CLI-CONTRACT.md, pipeline/ingest.dot's "IDEMPOTENCY RULES"):
state is written AFTER the work completes, never before -- an early ledger
write marks incomplete work done, invisibly and permanently. Both decisions
are idempotent: re-running for an already-ledgered source is a no-op, not
a duplicate row.

FLAGGED DESIGN NOTE (see delivery report): CLI-CONTRACT.md's prose says
accept "archives the source." pipeline/ingest.dot's own header says
``sources/`` is "COMMITTED. Immutable raw material. Never written to by
this pipeline." Treated "archives" as meaning "durably recorded as done via
the ledger," not a filesystem move -- honoring the stronger, explicit
immutability invariant rather than the vaguer prose word.

Usage:
    python3 -m wiki_weaver.ingest.commit --wiki-root <path> \
        --decision accept|skip --append-ledger ledger.jsonl \
        --append-log log.md [--ingested-count <int>]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    already_ledgered,
    atomic_append_jsonl,
    atomic_write_text,
    commit_if_staged,
    count_pages_touched_detail,
    ensure_dir,
    find_stray_root_pages,
    git_available,
    git_clean_and_checkout,
    int_or_default,
    read_current_kind,
    read_current_source_id,
    read_source_kind,
    resolve_path,
    summarize_ledger_touches,
)

# THE single source of truth for this default. pipeline/ingest.dot (and
# evals/arms/ingest-b.dot, ingest-c.dot) pass --ingested-count via the bare
# $ingested_count substitution form (never ${ingested_count:-0} -- see
# lib.int_or_default's docstring for why); when the context key is absent
# the CLI receives an empty string, which int_or_default resolves to
# DEFAULT_INGESTED_COUNT below.
DEFAULT_INGESTED_COUNT = 0


def _current_segment(wr: WikiRoot) -> tuple[int, int]:
    """(index, total) of the segment being committed right now. Defaults to
    (1, 1) -- the pre-segmentation whole-source shape -- when
    ``segment_source --select`` never ran (e.g. evals/arms/ingest-b.dot,
    ingest-c.dot, or any caller that hasn't wired in detect_kind/segment_source),
    so this is fully backward compatible."""
    path = wr.current_segment_file
    if not path.is_file():
        return 1, 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1, 1
    return int(data.get("index", 1)), int(data.get("total", 1))


def _kind_for_ledger(wr: WikiRoot, source_id: str) -> str:
    """Prefer what detect_kind actually determined (disk state) over the
    best-effort, frontmatter-only ``read_source_kind`` guess -- this is the
    accuracy DESIGN.md §5 asks for: the ledger label should reflect reality,
    not just echo a marker most real sources never carry."""
    try:
        return read_current_kind(wr)
    except FileNotFoundError:
        return read_source_kind(wr.sources_dir / source_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.commit")
    parser.add_argument("--wiki-root", required=True)
    # "quarantine" (see quarantine_brief.py's module docstring): a source
    # auto-quarantined AFTER already being offered its one post-exhaustion
    # review at review_gate -- ledgered distinctly from "skip" so the
    # ledger can tell a reviewer's deliberate [C] Skip apart from an
    # automatic quarantine the reviewer never had a chance to weigh in on
    # a second time (the defect this whole fix addresses: both used to be
    # recorded identically as "skip").
    # "declined" (see curate_brief.py's module docstring / curate_gate in
    # pipeline/ingest.dot): the answerer's OWN deliberate [B] Decline at
    # curate_gate, BEFORE any detect_kind/segment_source/weave work ever
    # ran for this source -- ledgered distinctly from "skip" (which reverts
    # a real weave's edits) because there is nothing to revert here: no
    # write ever happened. Sharing "skip"'s label would make a reviewer's
    # post-write skip indistinguishable from a pre-write decline without
    # reading commit contents -- the exact defect "quarantine" already
    # fixed for the quarantine/skip pair.
    parser.add_argument("--decision", required=True, choices=("accept", "skip", "quarantine", "declined"))
    parser.add_argument("--append-ledger", default="ledger.jsonl")
    parser.add_argument("--append-log", default="log.md")
    parser.add_argument("--ingested-count", type=int_or_default(DEFAULT_INGESTED_COUNT), default=DEFAULT_INGESTED_COUNT)
    return parser


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def _log_date() -> str:
    """Date component of the gist's log.md heading format."""
    return time.strftime("%Y-%m-%d", time.gmtime())


def _append_log(log_path: Path, line: str) -> None:
    existing = log_path.read_text(encoding="utf-8") if log_path.is_file() else ""
    ensure_dir(log_path.parent)
    atomic_write_text(log_path, existing + line)


def _cleanup_guidance(wr: WikiRoot) -> None:
    # Belt-and-suspenders: never let a stale guidance file leak into the
    # next source, on either decision path.
    guidance = wr.review_guidance_file
    if guidance.is_file():
        guidance.unlink()


def _do_skip(
    wr: WikiRoot,
    ledger_path: Path,
    log_path: Path,
    source_id: str,
    already_done: bool,
    segment_index: int,
    segment_total: int,
    decision: str = "skip",
) -> None:
    """Revert weave's edits and ledger the source as NOT merged.

    ``decision`` is one of ``"skip"`` (a reviewer's own deliberate [C] Skip
    choice at review_gate), ``"quarantine"`` (an AUTOMATIC quarantine -- the
    source exhausted its re-weave attempts and had already been offered its
    one post-exhaustion review; see quarantine_brief.py's module docstring),
    or ``"declined"`` (the answerer's own deliberate [B] Decline at
    curate_gate, BEFORE weave ever ran for this source -- see
    curate_brief.py's module docstring). All three share identical
    revert/ledger/commit mechanics -- the revert is a no-op for "declined"
    (nothing was ever written), harmless to run unconditionally -- the only
    difference is the ledger label and log line, so a reader of
    ledger.jsonl can tell a reviewer's own post-write choice apart from an
    automatic post-write outcome apart from a pre-write decline (the defect
    this parameter fixes: all of these used to be, or would otherwise be,
    recorded identically).
    """
    if already_done:
        print(
            f"{source_id} segment {segment_index}/{segment_total} already ledgered; {decision} is a no-op",
            file=sys.stderr,
        )
        return

    # Revert weave's working-tree edits FIRST -- never merge skipped content.
    if git_available(wr.root):
        git_clean_and_checkout(wr.root, "wiki")
        # The write-path bug's second-order leak (see validate.py's
        # WRITE-PATH LEAK GUARD): weave's file tools are rooted at
        # wiki_root, so a bare filename can land directly at wiki_root
        # instead of wiki/. The revert above only ever looked at "wiki" --
        # any stray content page a skipped source left at wiki_root itself
        # was never cleaned up. Scan (before reverting) and revert each one
        # by name, exactly like the "wiki" revert above, so a skipped
        # source never leaves debris behind regardless of where it landed.
        for stray in find_stray_root_pages(wr.root):
            git_clean_and_checkout(wr.root, stray.name)

    record = {
        "source_id": source_id,
        "decision": decision,
        "segment_index": segment_index,
        "segment_total": segment_total,
        "timestamp": _timestamp(),
    }
    atomic_append_jsonl(ledger_path, record)
    if decision == "skip":
        log_note = "Logged only, not merged."
    elif decision == "quarantine":
        log_note = (
            "AUTO-QUARANTINED: exhausted re-weave attempts, already offered one post-exhaustion "
            "review at review_gate. Not merged."
        )
    else:  # "declined"
        log_note = "DECLINED at curate_gate: judged not to belong in this wiki. No weave ever ran; not merged."
    _append_log(
        log_path,
        f"## [{_log_date()}] {decision} | {source_id} (segment {segment_index}/{segment_total})\n\n"
        f"{log_note} ({_timestamp()})\n\n",
    )

    if git_available(wr.root):
        commit_if_staged(wr.root, f"wiki-weaver: {decision} {source_id} segment {segment_index}/{segment_total}")


def _do_accept(
    wr: WikiRoot,
    ledger_path: Path,
    log_path: Path,
    source_id: str,
    already_done: bool,
    ingested_count_arg: int,
    segment_index: int,
    segment_total: int,
) -> int:
    if already_done:
        print(
            f"{source_id} segment {segment_index}/{segment_total} already ledgered; accept is a no-op", file=sys.stderr
        )
        return ingested_count_arg

    touched = count_pages_touched_detail(wr.root, wr.wiki_dir)
    pages_touched, pages_created, pages_updated = touched["total"], touched["created"], touched["updated"]
    record = {
        "source_id": source_id,
        "decision": "accept",
        "kind": _kind_for_ledger(wr, source_id),
        "pages_touched": pages_touched,
        "pages_created": pages_created,
        "pages_updated": pages_updated,
        "segment_index": segment_index,
        "segment_total": segment_total,
        "timestamp": _timestamp(),
    }
    atomic_append_jsonl(ledger_path, record)

    # THE touches_per_source metric (DESIGN.md's headline number -- see
    # pipeline/ingest.dot's weave prompt): recomputed from the ledger, which
    # now includes the row just appended, so this is always a true run-to-date
    # figure. Appended to log.md, not a separate artifact, so it is visible
    # exactly where a human already reads run output.
    summary = summarize_ledger_touches(ledger_path)
    _append_log(
        log_path,
        f"## [{_log_date()}] ingest | {source_id} (segment {segment_index}/{segment_total})\n\n"
        f"{pages_touched} page(s) touched ({pages_created} created, {pages_updated} updated). "
        f"({_timestamp()})\n\n"
        f"Run so far: {summary['sources']} source(s) accepted, "
        f"mean {summary['mean_touched']} page(s)/source touched "
        f"({summary['total_created']} created, {summary['total_updated']} updated total).\n\n",
    )

    if git_available(wr.root):
        commit_if_staged(wr.root, f"wiki-weaver: ingest {source_id} segment {segment_index}/{segment_total}")

    return ingested_count_arg + 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    ledger_path = resolve_path(wr, args.append_ledger)
    log_path = resolve_path(wr, args.append_log)

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    segment_index, segment_total = _current_segment(wr)
    already_done = already_ledgered(ledger_path, source_id, segment_index)

    if args.decision in ("skip", "quarantine", "declined"):
        _do_skip(
            wr, ledger_path, log_path, source_id, already_done, segment_index, segment_total, decision=args.decision
        )
        _cleanup_guidance(wr)
        return 0

    ingested_count = _do_accept(
        wr, ledger_path, log_path, source_id, already_done, args.ingested_count, segment_index, segment_total
    )
    _cleanup_guidance(wr)
    print(json.dumps({"ingested_count": ingested_count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
