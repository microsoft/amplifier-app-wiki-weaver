"""wiki_weaver.synthesize.persist_synthesis_guidance -- apply synthesis_gate's
freeform ``DROP:`` instructions to the synth ledger, making them durably
change what ``select_gap`` proposes next.

THE FIX THIS CLOSES (see the task that commissioned ``synthesis_brief.py``'s
module docstring, and ``collect_guidance``'s own cautionary precedent in
``pipeline/ingest.dot``'s header: "collect_guidance used to carry NOTHING
but its six-word node label ... a gate reached with zero grounding refuses
rather than fabricates"): a gate whose answer nothing acts on is exactly the
``collect_guidance`` defect this codebase has already been bitten by once --
"it looked wired for months and was not." ``synthesis_gate`` must not repeat
that mistake: whatever the answerer names for dropping is ledgered here as
``declined`` -- the IDENTICAL schema ``wiki_weaver.synthesize.commit`` writes
for a normal declined candidate -- so ``select_gap.decided_terms`` (its own,
unmodified, already-tested exclusion logic) simply never proposes that term
again, in THIS pass or any future one. No change to ``select_gap`` itself is
needed; this node's whole job is producing ledger rows that node already
knows how to honor.

DETERMINISTIC PARSE, NEVER SEMANTIC RE-INTERPRETATION (AP-2, DESIGN.md
Sec 12.1: no routing/ledger decision in this pipeline is made from an LLM's
prose taken at face value): the freeform answer is scanned for lines of the
literal form ``DROP: <term>`` (case-insensitive prefix, optional leading
bullet/dash) -- the EXACT term text ``synthesis_brief`` printed, matched
case-insensitively against the pending set stamped in ``synthesis-brief.json``.
A name that does not match any pending term is logged and IGNORED, never
invented as a new ledger entry -- the same "never invent a source_id"
discipline ``attribute_sources``/``rank_candidates`` are already held to in
this package.

FAIL-SOFT TOWARD KEEPING EVERY CANDIDATE (same discipline
``persist_takeaways`` already established for ``takeaways_gate``): a missing,
stale, stub ("auto-approved"), or "KEEP ALL"-shaped answer is a NORMAL,
expected outcome for an unattended run -- never a fail-loud condition, and
never treated as a drop. This gate is opt-in scrutiny, not a mandatory
narrowing of the candidate list.

Usage:
    python3 -m wiki_weaver.synthesize.persist_synthesis_guidance --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_append_jsonl

AUTO_APPROVED_STUB = "auto-approved"

# Canonical "nothing to drop" phrases -- case-insensitive, trailing "." tolerated
# (mirrors persist_takeaways.NO_EMPHASIS_STUB's exact normalization).
_KEEP_ALL_STUBS = frozenset({"keep all", "none", "no changes", "no drops"})

_DROP_PREFIX = "drop:"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.persist_synthesis_guidance")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC"


def _read_brief(wr: WikiRoot) -> tuple[str | None, list[str]]:
    """``(signature, pending_terms)`` from ``synthesis-brief.json``, or
    ``(None, [])`` if absent/malformed. Without a real brief this node has
    nothing to verify an answer's freshness against, so it treats every
    answer as unverifiable -- never drop anything on an unverifiable brief."""
    path = wr.synthesis_brief_file
    if not path.is_file():
        return None, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, []
    if not isinstance(data, dict):
        return None, []
    signature = data.get("signature")
    pending_terms = data.get("pending_terms")
    if not isinstance(signature, str) or not signature:
        return None, []
    if not isinstance(pending_terms, list):
        return None, []
    return signature, [str(t) for t in pending_terms]


def _read_answer_text(wr: WikiRoot, expected_signature: str) -> str | None:
    """The real answer text from ``synthesis-answer.json``, or ``None`` if
    absent/unreadable/malformed/stale (signature mismatch) -- every one of
    those is treated as "nothing to drop" (see module docstring), never
    fail-loud: an unattended run legitimately never drops anything."""
    path = wr.synthesis_answer_file
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    signature = data.get("signature")
    if not isinstance(text, str):
        return None
    if not isinstance(signature, str) or signature != expected_signature:
        return None  # stale -- a previous pass's answer, never trust it
    return text


def _is_stub_answer(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".")
    return normalized == AUTO_APPROVED_STUB or normalized in _KEEP_ALL_STUBS


def parse_drop_terms(answer_text: str, pending_terms: list[str]) -> tuple[list[str], list[str]]:
    """Deterministic, structured-marker parse -- never a semantic
    re-interpretation of free prose to make a ledger decision (see module
    docstring). Returns ``(matched_terms, unmatched_raw_names)`` in the
    order first seen; ``matched_terms`` never contains a duplicate. An
    unmatched name is reported to the caller for a warning log, never
    ledgered -- the same "never invent a source_id" guard
    ``rank_candidates``/``attribute_record`` already apply elsewhere in this
    package."""
    by_lower = {t.lower(): t for t in pending_terms}
    matched: list[str] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    for raw_line in answer_text.splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if not line.lower().startswith(_DROP_PREFIX):
            continue
        name = line[len(_DROP_PREFIX) :].strip().strip('"').strip()
        if not name:
            continue
        term = by_lower.get(name.lower())
        if term is None:
            unmatched.append(name)
            continue
        if term not in seen:
            seen.add(term)
            matched.append(term)
    return matched, unmatched


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    signature, pending_terms = _read_brief(wr)
    if signature is None:
        print("no synthesis brief on disk -- nothing to apply", file=sys.stderr)
        return 0

    text = _read_answer_text(wr, signature)
    if text is None:
        print(
            "no synthesis guidance answer (missing, stale, or unattended) -- keeping every candidate",
            file=sys.stderr,
        )
        return 0

    text = text.strip()
    if not text or _is_stub_answer(text):
        print("synthesis_gate: keep all -- no candidates dropped", file=sys.stderr)
        return 0

    matched, unmatched = parse_drop_terms(text, pending_terms)
    for name in unmatched:
        print(f"synthesis_gate: ignoring DROP for {name!r} -- not a pending candidate term", file=sys.stderr)

    if not matched:
        print("synthesis_gate: no valid DROP: lines found -- keeping every candidate", file=sys.stderr)
        return 0

    for term in matched:
        atomic_append_jsonl(
            wr.synth_ledger_path,
            {
                "term": term,
                "decision": "declined",
                "reason": "dropped at synthesis_gate before any page was written",
                "timestamp": _timestamp(),
            },
        )
        print(f"synthesis_gate: dropped {term!r} -- ledgered as declined, never re-proposed", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
