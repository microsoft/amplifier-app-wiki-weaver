"""wiki_weaver.ingest.guidance_brief -- the context ``collect_guidance``
needs to actually answer instead of refusing.

THE DEFECT THIS FIXES (see delivery report): ``collect_guidance`` carried
nothing but a six-word node label -- the EXACT vacuum ``review_gate`` had
before ``prepare_review_brief`` fixed it (see that module's docstring). In
a live 20-source run, both times the proxy chose ``[B] Guide``, it then hit
``collect_guidance`` with zero grounding and (correctly, per the persona's
own constraints -- fabricating steering text with nothing to ground it in
would violate the persona's own refusal script) refused, crashing the
pipeline with ``no_matching_edge`` and throwing away the reviewer's
decision to steer instead of skip. Zero of two [B] Guide choices ever
produced a steered re-weave.

THE FIX: reuse ``review_brief.build_brief`` -- the SAME self-contained
description of what ``weave`` just did that ``review_gate`` already showed
when the reviewer chose ``[B] Guide`` a moment earlier -- plus, best
effort, the reviewer's OWN stated reason for choosing Guide (the freshest,
most relevant possible signal: ``wiki_weaver.aitl.audit``'s
``.ai/gate-decisions.jsonl``, written moments earlier by
``ProxyInterviewer._record`` when the proxy answers ``review_gate``). The
reason is ADDITIVE, never required -- a human reviewer using the console
interviewer never writes to ``gate-decisions.jsonl`` at all, and this
brief must still be useful (the weave-brief half) in that case.

FAIL LOUD, NEVER FABRICATE, identical discipline to ``review_brief.py``: if
``review_brief_file`` itself is missing/unreadable/stale for the CURRENT
source, there is nothing real to steer from -- refuse to invent one. In the
normal pipeline this can only happen if ``prepare_review_brief`` /
``quarantine_brief`` failed to run moments earlier in this SAME pass -- a
real bug elsewhere, not an ordinary condition, exactly like
``prepare_review_brief``'s own ``current_source_id`` guard.

Duplicates (rather than imports) the tiny brief-file read/verify shape
``wiki_weaver.aitl.proxy_interviewer._load_review_brief`` also implements --
that function lives in the ``aitl`` package, which this ``ingest``-package
tool must not depend on (see ``review_brief.py``'s own precedent: it
duplicates ``commit.py``'s ``_current_segment`` helper rather than import
it, to keep additions zero-risk and package-isolated).

Usage:
    python3 -m wiki_weaver.ingest.guidance_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir, read_current_source_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.guidance_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _load_review_brief_text(wr: WikiRoot, source_id: str) -> tuple[str | None, str | None]:
    """(brief_text, None) on success, (None, reason) on any failure.

    Same contract as ``proxy_interviewer._load_review_brief`` -- a brief
    that cannot be verified current is worse than no brief at all, never
    fall back to guessing.
    """
    path = wr.review_brief_file
    if not path.is_file():
        return None, f"{path} does not exist -- no weave brief to steer from"

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} could not be read/parsed: {exc}"

    if not isinstance(data, dict):
        return None, f"{path} did not contain a JSON object"

    brief_text = data.get("brief")
    brief_source_id = data.get("source_id")
    if not isinstance(brief_text, str) or not brief_text.strip():
        return None, f"{path} has no usable 'brief' text"
    if not isinstance(brief_source_id, str) or not brief_source_id:
        return None, f"{path} has no 'source_id' to verify freshness against"
    if brief_source_id != source_id:
        return None, (
            f"{path} is stale: brief was written for source {brief_source_id!r}, "
            f"but the current source is {source_id!r}"
        )

    return brief_text, None


def _last_guide_reason(wr: WikiRoot) -> str | None:
    """Best-effort: the reviewer's OWN stated reason for choosing [B] Guide,
    from the most recent ``gate-decisions.jsonl`` entry.

    Never required, never fail-loud on absence -- a human answering via the
    console interviewer writes nothing to this file at all, and the
    weave-brief half of the guidance brief must stand on its own regardless.
    Only trusts the LAST line: this tool always runs immediately after
    review_gate answered [B] Guide in the SAME pass, so no other gate
    decision can have been recorded in between.
    """
    path = wr.gate_decisions_file
    if not path.is_file():
        return None
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        last = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(last, dict):
        return None

    stage = str(last.get("stage") or "")
    if not (stage == "review_gate" or stage.startswith("review_gate-")):
        return None
    if last.get("decision") != "answered" or last.get("choice_key") != "B":
        return None

    reason = last.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip()


def build_guidance_brief(wr: WikiRoot, review_brief_text: str) -> str:
    """The weave brief, plus the reviewer's own stated reason for
    steering when one is available -- see module docstring."""
    lines = [review_brief_text]
    reason = _last_guide_reason(wr)
    if reason:
        lines.append("")
        lines.append("Reviewer's stated reason for choosing [B] Guide:")
        lines.append(reason)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    review_brief_text, error = _load_review_brief_text(wr, source_id)
    if error or review_brief_text is None:
        # FAIL LOUD: no real weave brief to steer from -- never fabricate a
        # guidance brief without it (same discipline as review_brief.py).
        print(error, file=sys.stderr)
        return 1

    guidance_brief = build_guidance_brief(wr, review_brief_text)

    ensure_dir(wr.ai_dir)
    atomic_write_text(
        wr.guidance_brief_file,
        json.dumps({"source_id": source_id, "stage": "collect_guidance", "brief": guidance_brief}, indent=2) + "\n",
    )

    print(json.dumps({"guidance_brief": guidance_brief}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
