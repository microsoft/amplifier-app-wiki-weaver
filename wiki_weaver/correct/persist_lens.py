"""wiki_weaver.correct.persist_lens -- THE load-bearing step: write a
correction into ``lens/corrections/`` so a future ``ingest`` cannot
silently reintroduce it, then commit wiki + lens together.

WHY THIS IS THE STEP THAT MATTERS (see ``pipeline/correct.dot``'s header
and ``docs/DESIGN.md`` \u00a78): ``re_derive`` (the box node immediately
upstream) only rewrites TODAY's wiki pages. If nothing durable is written
here, the next ``ingest`` that re-weaves a source repeating the original
(wrong) framing silently re-breaks the exact thing this correction just
fixed -- ``weave``'s own prompt (``pipeline/ingest.dot``) already says
"standing corrections in lens/corrections/ must not be silently
reintroduced," which only works if something is actually THERE to not
reintroduce.

TWO EFFECTS FROM ONE CORRECTION (the feedback-loop this module closes the
second half of):
  1. **Fixes the immediate problem** -- already done, by ``re_derive``,
     before this node ever runs.
  2. **Persists something durable** that (a) ``weave`` re-reads on every
     future ingest pass (unchanged, pre-existing behavior -- this module
     only has to land the file in the directory ``weave`` already reads),
     and (b) the AITL proxy now ALSO reads on every future gate decision,
     via ``wiki_weaver.aitl.corrections`` -- see that module's docstring
     for why the proxy needed a NEW read path here (it previously read
     only ``lens/persona.md``) and why corrections are read from THIS
     directory rather than folded into the persona document.

SCOPE (the task's required "this page is wrong" vs "stop doing this class
of thing" distinction -- see ``build_correction_content`` below): decided
deterministically from ``locate_pages``'s own output moments earlier in the
SAME pass, never guessed from the correction's wording.

IDEMPOTENCY: the correction id is a content hash of the claim text (see
``correction_id``) -- re-running ``correct.dot`` for the exact same
correction (e.g. a resumed/retried pass) checks for
``lens/corrections/<id>.md`` before writing, so no duplicate correction is
ever recorded. The subsequent commit is guarded by
``git diff --staged --quiet`` (``commit_if_staged``) -- a re-run that
changes nothing stages nothing and commits nothing (no phantom commit).

Usage:
    python3 -m wiki_weaver.correct.persist_lens --wiki-root <path> \\
        --claim-file .ai/correction-text.md --pages-file .ai/affected-pages.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, commit_if_staged, ensure_dir, git_init_if_absent

AUTO_APPROVED_STUB = "auto-approved"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.correct.persist_lens")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--claim-file", required=True)
    parser.add_argument("--pages-file", required=True)
    return parser


def correction_id(claim_text: str) -> str:
    """Deterministic id for this correction -- a content hash of the claim
    text (whitespace-trimmed, so re-runs that differ only in trailing
    newline are still recognized as the same correction). Every re-run for
    the SAME claim maps to the same ``lens/corrections/<id>.md``, which is
    what makes the idempotency check below possible."""
    return hashlib.sha256(claim_text.strip().encode("utf-8")).hexdigest()[:16]


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_pages(pages_path: Path) -> list[str]:
    if not pages_path.is_file():
        return []
    try:
        data = json.loads(pages_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pages = data.get("pages", []) if isinstance(data, dict) else []
    return [str(p) for p in pages] if isinstance(pages, list) else []


def build_correction_content(claim_text: str, correction_id_: str, pages: list[str], created: str) -> str:
    """Render ``lens/corrections/<id>.md``.

    ``scope`` is decided from ``pages`` -- ``locate_pages``'s own output,
    computed moments earlier in the same ``correct.dot`` pass by searching
    the CURRENT wiki for the corrected claim:
      - ``pages`` non-empty -> ``scope: page`` -- this correction is tied
        to specific, currently-identifiable pages (listed in frontmatter).
        Narrow: a future source about something unrelated to these pages
        does not need to weigh this correction as heavily.
      - ``pages`` empty -> ``scope: general`` -- nothing on the wiki right
        now asserts anything this claim's own vocabulary matches, so the
        correction is recorded as a standing, general instruction rather
        than a page-specific fix.

    Both are written to the SAME file shape, in the SAME directory, and are
    read UNFILTERED by every downstream consumer today (``weave``'s prompt
    reads the whole directory on every pass; ``wiki_weaver.aitl.corrections``
    does the same for the AITL proxy) -- ``scope``/``pages`` are recorded
    for a human (or a future ``wiki-weaver review``) auditing
    ``lens/corrections/``, not to filter what gets read. See
    ``wiki_weaver.aitl.corrections``'s module docstring for the full
    rationale.
    """
    scope = "page" if pages else "general"
    frontmatter = [
        "---",
        f"id: {correction_id_}",
        f"created: {created}",
        f"scope: {scope}",
        f"pages: [{', '.join(pages)}]",
        "---",
        "",
    ]
    return "\n".join(frontmatter) + claim_text.strip() + "\n"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))
    claim_path = Path(args.claim_file)

    if not claim_path.is_file():
        print(f"refusing to persist correction: {claim_path} does not exist", file=sys.stderr)
        return 1
    claim_text = claim_path.read_text(encoding="utf-8")
    if not claim_text.strip():
        print(f"refusing to persist correction: {claim_path} is empty", file=sys.stderr)
        return 1
    if claim_text.strip() == AUTO_APPROVED_STUB:
        # Defense in depth: capture.py already rejects this stub before the
        # claim file is ever written, but persist_lens is the step that
        # actually commits to lens/corrections/ -- the highest-precedence
        # layer this project has alongside lens/canon/ -- never trust a
        # single upstream check alone for a write this consequential.
        print(
            f"refusing to persist correction: {claim_path} contains the literal "
            "auto-approve stub, not a real correction",
            file=sys.stderr,
        )
        return 1

    pages = _read_pages(Path(args.pages_file))
    cid = correction_id(claim_text)
    scope = "page" if pages else "general"
    correction_path = wr.corrections_dir / f"{cid}.md"

    if correction_path.is_file():
        print(f"correction {cid} already recorded at {correction_path} -- idempotent no-op", file=sys.stderr)
    else:
        ensure_dir(wr.corrections_dir)
        content = build_correction_content(claim_text, cid, pages, _timestamp())
        atomic_write_text(correction_path, content)
        print(f"correction {cid} persisted to {correction_path} (scope={scope})", file=sys.stderr)

    git_init_if_absent(wr.root)
    commit_if_staged(wr.root, f"wiki-weaver: correction {cid} ({scope} scope)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
