"""wiki_weaver.synthesize.commit -- ledger the decision, one git commit.

The only node that makes a gap's outcome irreversible -- mirrors
``wiki_weaver.ingest.commit``'s accept/skip shape (``--decision paged``
here plays the role ``accept`` plays there; ``declined`` plays ``skip``'s
role), same ordering law (CLI-CONTRACT.md / REWRITE-GUIDE.md \u00a72.3: state is
written AFTER the work completes, never before) and the same idempotency
guarantee (re-running for an already-ledgered term is a no-op).

``--decision paged``: the page written by ``write_gap_page`` (and passed
structural validation) is kept; ledgers the term as ``paged`` with the page
filename, commits wiki + ledger together.

``--decision declined``: EITHER ``answer_gap`` found no real cross-source
support (coverage_check routed straight here, no page was ever written), OR
``retry_bound`` gave up after repeated structural failures (a page MAY
exist on disk from the last attempt -- revert it, same discipline as
``ingest.commit``'s skip path: never merge content that was not accepted).
Either way, the term is ledgered ``declined`` with a ``reason`` and never
proposed again by ``select_gap``.

Usage:
    python3 -m wiki_weaver.synthesize.commit --wiki-root <path> --decision paged|declined [--reason <text>]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_append_jsonl,
    commit_if_staged,
    git_available,
    git_clean_and_checkout,
)
from wiki_weaver.synthesize.write_gap_page import slugify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.commit")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--decision", required=True, choices=("paged", "declined"))
    parser.add_argument(
        "--reason",
        default="",
        help="override the auto-detected decline reason (default: inferred from which ephemeral file is present)",
    )
    return parser


def _infer_decline_reason(wr: WikiRoot) -> str:
    """``commit_declined`` is reached from TWO different edges (see
    pipeline/synthesize.dot): ``gap_coverage_check``'s ``declined`` sentinel
    (``answer_gap`` found no real cross-source support -- ``gap_declined_file``
    exists) or ``retry_bound``'s ``give_up`` sentinel (structural validation
    never converged -- no decline file, a page may exist on disk instead).
    Both land on this SAME node/tool_command, so the reason is inferred from
    which artifact is actually present rather than needing two node
    instances or a second context key."""
    if wr.gap_declined_file.is_file():
        text = wr.gap_declined_file.read_text(encoding="utf-8").strip()
        return f"answer_gap declined: {text[:200]}" if text else "answer_gap declined coverage as too thin"
    return "structural validation did not converge within the retry bound"


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def _read_current_gap(wr: WikiRoot) -> dict:
    path = wr.current_gap_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- select_gap must run first")
    return json.loads(path.read_text(encoding="utf-8"))


def _cleanup_ephemeral(wr: WikiRoot) -> None:
    """Never let a stale answer/decline/context file leak into the next
    gap's pass -- the mirror of ingest.commit's guidance cleanup."""
    for path in (wr.gap_answer_file, wr.gap_declined_file, wr.gap_context_file):
        if path.is_file():
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        gap = _read_current_gap(wr)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    term = gap["term"]
    slug = slugify(term)

    if args.decision == "declined":
        # Belt-and-suspenders: a page may exist on disk from a retry_bound
        # give-up (structural validation never converged) -- revert it,
        # never merge unaccepted content. A no-op if write_gap_page never
        # ran (the coverage_check=declined path).
        if git_available(wr.root):
            page_path = wr.wiki_dir / f"{slug}.md"
            if page_path.exists():
                git_clean_and_checkout(wr.root, str(page_path.relative_to(wr.root)))

            # write_gap_page.link_gap_page (the orphan fix) may have already
            # added a [[slug|...]] wikilink from index.md to the page just
            # reverted above -- revert index.md's uncommitted edit too, or
            # a dangling link to a now-nonexistent page ships as a NEW
            # broken-link defect (find_structural_issues would catch it,
            # but not until some LATER validate call -- this pipeline's own
            # give-up path never runs validate again before committing the
            # decline). A no-op when write_gap_page never ran or never
            # needed to add the link (term already linked from a prior
            # pass).
            index_path = wr.wiki_dir / "index.md"
            if index_path.exists():
                git_clean_and_checkout(wr.root, str(index_path.relative_to(wr.root)))

        record = {
            "term": term,
            "decision": "declined",
            "reason": args.reason or _infer_decline_reason(wr),
            "source_count": gap.get("source_count"),
            "timestamp": _timestamp(),
        }
        atomic_append_jsonl(wr.synth_ledger_path, record)
        if git_available(wr.root):
            commit_if_staged(wr.root, f"wiki-weaver synthesize: decline {term!r}")
        _cleanup_ephemeral(wr)
        print(f"declined: {term!r} ({record['reason']})", file=sys.stderr)
        return 0

    record = {
        "term": term,
        "decision": "paged",
        "page": f"{slug}.md",
        "source_count": gap.get("source_count"),
        "source_ids": gap.get("source_ids"),
        "timestamp": _timestamp(),
    }
    atomic_append_jsonl(wr.synth_ledger_path, record)
    if git_available(wr.root):
        commit_if_staged(wr.root, f"wiki-weaver synthesize: page {term!r} ({slug}.md)")
    _cleanup_ephemeral(wr)
    print(f"paged: {term!r} -> wiki/{slug}.md", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
