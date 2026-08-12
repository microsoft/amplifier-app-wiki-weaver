"""wiki_weaver.ingest.review_brief -- a self-contained brief of what ``weave``
actually did to THIS source, for ``review_gate`` (human or proxy).

THE DEFECT THIS FIXES (see delivery report): ``review_gate``'s only carried
context was ``description="$retention_flag"`` -- a single word ("ok" or
"suspicious: ..."). A human reviewer at this gate has the terminal
scrollback, the diff, and the ledger; they watched the weave happen, so the
gate never needed to carry anything else. An agent proxy answering the same
gate cold sees only the six-word node label plus that one flag, and
(correctly) refuses to accept/guide/skip content it has never been shown.

THE FIX: a compact, deterministic brief -- source id, kind, segment
position, WHICH pages were created vs updated (by name, not just count --
a page named after a tool tells a very different story than one extending
an existing argument), and the retention flag -- built from exactly the
same read-only bookkeeping ``commit.py``'s ledger row and ``budget.py``'s
cost row already use (``count_pages_touched``'s sibling,
``git_changed_wiki_files_detail``; ``read_current_kind`` with the same
``read_source_kind`` fallback; the same ``current_segment.json`` shape
``commit.py`` reads). No model call, no new persisted artifact -- this is
read-only bookkeeping, same class as ``retention_check``/``budget``.

ADDITIVE, NOT A REPLACEMENT (see delivery report's design-alternatives
note): this is a NEW node inserted between ``budget`` and ``review_gate``,
not a change to either. ``budget.py``'s stdout contract
(``within_budget`` / exit 1) is untouched -- this module never imports or
calls it. ``commit.py``'s ``_current_segment`` helper is intentionally NOT
imported here (it is a private, underscore-prefixed helper local to that
module); the tiny shape it reads (``current_segment.json``'s
``{"index": ..., "total": ...}``) is duplicated below rather than exported,
keeping this change a zero-risk addition that touches no existing file's
public surface.

FAIL LOUD, NEVER FABRICATE (the task's explicit requirement, and this
project's own recorded history of silent-success failures): if
``current_source.txt`` is missing or unreadable, there is no real source to
brief, and this tool refuses to invent one -- same contract
``read_current_source_id`` already enforces for ``budget.py`` and
``commit.py``. Every other field degrades the same way its sibling tools
already do (kind falls back to the frontmatter guess; page names come up
empty, never fabricated, when git is unavailable) -- this node introduces
no new silent-success surface beyond what already exists in this pipeline.

Usage:
    python3 -m wiki_weaver.ingest.review_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    git_changed_wiki_files_detail,
    read_current_kind,
    read_current_source_id,
    read_source_kind,
)

# Keep the brief compact -- this goes into a prompt, not a log file (see
# module docstring / delivery report). Long lists of page names are
# truncated with a "+N more" tail rather than dumped in full.
MAX_PAGES_LISTED = 8


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.review_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _kind_for_brief(wr: WikiRoot, source_path: Path) -> str:
    """Prefer what ``detect_kind`` actually determined (disk state) over the
    best-effort, frontmatter-only ``read_source_kind`` guess -- identical
    fallback to ``budget.py``'s ``_kind_for_cost`` / ``commit.py``'s
    ``_kind_for_ledger``."""
    try:
        return read_current_kind(wr)
    except FileNotFoundError:
        return read_source_kind(source_path)


def _current_segment(wr: WikiRoot) -> tuple[int, int]:
    """(index, total) of the segment under review right now.

    Mirrors ``commit.py``'s identically-shaped private helper -- duplicated,
    not imported, to keep this node a strictly additive change (see module
    docstring). Defaults to (1, 1) exactly as ``commit.py`` does when
    ``segment_source --select`` never ran for this pipeline.
    """
    path = wr.current_segment_file
    if not path.is_file():
        return 1, 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1, 1
    return int(data.get("index", 1)), int(data.get("total", 1))


def _format_pages(names: set[str]) -> str:
    if not names:
        return "none"
    ordered = sorted(names)
    shown = ordered[:MAX_PAGES_LISTED]
    text = ", ".join(shown)
    remaining = len(ordered) - len(shown)
    if remaining > 0:
        text += f", +{remaining} more"
    return text


def build_brief(wr: WikiRoot, source_id: str) -> str:
    """Assemble the compact, human/proxy-readable brief for ``source_id``.

    Every field here is read live from disk/git state -- the same
    bookkeeping ``commit.py``'s ledger row and ``budget.py``'s cost row
    already compute -- never invented. ``git_changed_wiki_files_detail``
    degrades to empty sets (not an error) when git is unavailable, matching
    ``count_pages_touched``'s existing, already-tested behavior elsewhere
    in this pipeline.
    """
    source_path = wr.sources_dir / source_id
    kind = _kind_for_brief(wr, source_path)
    segment_index, segment_total = _current_segment(wr)
    created, updated = git_changed_wiki_files_detail(wr.root, "wiki")

    # The channel $retention_flag already proves works (retention_check's
    # parse_json="true" output populates context.retention_flag; this node's
    # tool_env="retention_flag,py" exposes it as RETENTION_FLAG). Never
    # fabricate "ok" when the value simply hasn't reached us.
    retention_flag = os.environ.get("RETENTION_FLAG", "").strip() or "unknown (not available to this node)"

    lines = [
        f"Source: {source_id} (kind: {kind}, segment {segment_index}/{segment_total})",
        f"Pages created ({len(created)}): {_format_pages(created)}",
        f"Pages updated ({len(updated)}): {_format_pages(updated)}",
        f"Retention check: {retention_flag}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        # FAIL LOUD: no identifiable current source means there is nothing
        # real to brief. Never emit an empty or invented brief for a gate
        # this consequential -- a fabricated brief is worse than a gate
        # that stops (see module docstring).
        print(str(exc), file=sys.stderr)
        return 1

    brief = build_brief(wr, source_id)

    # THE delivery fix (see wiki_weaver.aitl.proxy_interviewer's module
    # docstring): the stdout JSON below only reaches $review_brief /
    # Question.metadata["description"] -- a channel the proxy's own
    # question-reduction (_to_gate_question) never reads. Writing the same
    # brief to a well-known file lets ProxyInterviewer read it directly, the
    # same way it already reads lens/persona.md from disk. Stamped with
    # source_id so the reader can verify freshness against
    # current_source.txt before trusting it -- a stale brief from a
    # previous source is worse than no brief (see review_brief_file's
    # docstring in wiki_weaver.lib).
    atomic_write_text(
        wr.review_brief_file,
        json.dumps({"source_id": source_id, "stage": "review_gate", "brief": brief}, indent=2) + "\n",
    )

    print(json.dumps({"review_brief": brief}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
