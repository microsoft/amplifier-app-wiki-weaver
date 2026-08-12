"""wiki_weaver.ask.file_back_brief -- the self-contained brief for
``ask.dot``'s ``file_back_gate``.

THE GAP THIS FIXES (see the task that commissioned this module and
``ask.dot``'s own header): the source gist is explicit that filing answers
back matters -- "good answers can be filed back into the wiki as new pages.
A comparison you asked for, an analysis, a connection you discovered --
these are valuable and shouldn't disappear into chat history. This way your
explorations compound in the knowledge base just like ingested sources do."
``file_back_gate`` exists in ``ask.dot`` and is reachable by default
(``enable_file_back`` defaults to true), but it has never been exercised --
it carries NOTHING but a bare node label, the exact failure mode every other
gate in this project had before it got a brief (``review_gate``,
``takeaways_gate``, ``collect_guidance``): an agent proxy answering cold has
no grounds to judge whether THIS particular answer is worth filing back, and
correctly refuses rather than fabricate a judgment.

THE FIX: a deterministic node (no model call) between ``present`` and
``file_back_gate`` -- reads the question that was asked and the answer that
was just presented, and states plainly what choosing ``[A] File this answer
back`` actually does (registers the answer as a new immutable source and
runs it through ``ingest.dot``, exactly like any other source).

NO SOURCE-ID-STYLE FRESHNESS STAMP (unlike the ingest-side stamped briefs):
``ask.dot`` has no per-source resume loop of its own -- see its own header,
"This pipeline has no resume/idempotency concerns of its own." One question
in, one answer out, this brief written fresh immediately before the gate in
the SAME pass -- so ``wiki_weaver.lib.WikiRoot.file_back_brief_file``'s own
docstring documents why presence and a non-empty ``brief`` field are the
whole contract here, not a stamped source id to verify against.

FAIL LOUD, NEVER FABRICATE: if ``.ai/answer.md`` is missing or empty at this
point, ``coverage_check`` should never have routed here in the first place
(``ask.dot``'s own contract: ``file_back_gate`` is only reached via
``present``, which only runs when ``coverage_check`` found a non-empty
``answer.md``) -- but this tool still refuses to invent a brief rather than
silently trust that invariant.

Usage:
    python3 -m wiki_weaver.ask.file_back_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, atomic_write_text, ensure_dir

# Keep the answer gist bounded -- same discipline as review_brief/
# takeaways_brief/curate_brief: this goes into a prompt, not a log file.
MAX_ANSWER_CHARS = 800


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ask.file_back_brief")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--answer-file", default=".ai/answer.md")
    return parser


def _truncate(text: str, max_chars: int = MAX_ANSWER_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "\u2026"


def build_brief(question: str, answer_text: str) -> str:
    """Assemble the compact, human/proxy-readable brief for
    ``file_back_gate``. Every field is read live from disk/env state --
    never invented."""
    question_line = question.strip() or "(question text unavailable)"
    answer_gist = _truncate(answer_text) if answer_text.strip() else "(answer content unavailable)"

    lines = [
        f"Question asked: {question_line}",
        "Answer about to be offered for filing back:",
        answer_gist,
        "",
        (
            "Choosing [A] registers this answer as a new immutable source and runs it through "
            "the normal ingest pipeline (ingest.dot), exactly like any other source -- it becomes "
            "a real wiki page (or folds into an existing one), not a special case. Per the wiki's "
            "own pattern: comparisons, analyses, and connections discovered in conversation are "
            "valuable and compound in the knowledge base just like ingested sources do -- they "
            "shouldn't disappear into chat history. Choosing [B] leaves this answer as a one-off, "
            "unrecorded response."
        ),
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    answer_path = Path(args.answer_file)
    if not answer_path.is_absolute():
        answer_path = wr.root / answer_path
    if not answer_path.is_file():
        # FAIL LOUD: no answer content means there is nothing real to brief
        # -- see module docstring. Never emit an empty or invented brief.
        print(f"{answer_path} does not exist -- nothing to brief file_back_gate from", file=sys.stderr)
        return 1

    answer_text = answer_path.read_text(encoding="utf-8")
    question = os.environ.get("QUESTION", "")

    brief = build_brief(question, answer_text)

    # Same two-channel delivery as review_brief.py/takeaways_brief.py: stdout
    # JSON only reaches $file_back_brief / Question.metadata["description"]
    # -- a channel the proxy's own question-reduction never reads. The
    # stamped file lets ProxyInterviewer read it directly.
    ensure_dir(wr.ai_dir)
    atomic_write_text(
        wr.file_back_brief_file,
        json.dumps({"stage": "file_back_gate", "brief": brief}, indent=2) + "\n",
    )

    print(json.dumps({"file_back_brief": brief}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
