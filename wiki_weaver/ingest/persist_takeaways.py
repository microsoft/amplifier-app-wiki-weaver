"""wiki_weaver.ingest.persist_takeaways -- write ``takeaways_gate``'s
freeform emphasis text to ``.ai/takeaways-guidance.md`` for ``weave`` to
read directly (the SAME file-based hand-off ``review-guidance.md`` already
proves for a post-write steer -- see ``pipeline/ingest.dot``'s weave
prompt).

READS FROM ``takeaways-answer.json``, NOT AN ENVIRONMENT VARIABLE (see
``wiki_weaver.aitl.freeform_bridge``'s module docstring for the full story):
a VERIFIED platform defect means environment variable names containing
periods -- e.g. the engine's own ``human.gate.text`` context key, upper-cased
verbatim by ``tool_env`` to ``HUMAN.GATE.TEXT`` -- do not reliably survive
``asyncio.create_subprocess_shell``'s ``env=`` passthrough on this platform.
Every ``Interviewer`` this project constructs writes its own freeform
``takeaways_gate`` answer directly to ``WikiRoot.takeaways_answer_file`` the
moment it answers (``FreeformAnswerRecorder``); this node reads that file
instead, verifying freshness against ``current_source_file`` -- the exact
same discipline every other stamped brief file in this codebase already
uses (``review_brief_file``/``guidance_brief_file``/``takeaways_brief_file``).

UNLIKE ``collect_guidance``'s ``persist_guidance.py``, the literal
``"auto-approved"`` stub
(``amplifier_module_loop_pipeline.interviewer.AutoApproveInterviewer``'s own
freeform answer) is NOT a fail-loud condition here. ``collect_guidance`` is
only ever reached after a reviewer DELIBERATELY chose ``[B] Guide`` --
auto-approve driving through that path would be nonsensical (nobody asked
to steer), so persist_guidance.py correctly refuses it. ``takeaways_gate``
is different: it is reached on EVERY source, and an unattended per-site
``--gate-mode-for takeaways_gate=auto-approve`` is a legitimate, INTENDED
choice (this task's own requirement: every gate independently supports
human/agent/unattended). Its honest answer is "no particular emphasis --
proceed normally," not a refusal. So a stub, blank, missing, or stale
answer file simply means NO guidance file is written at all -- ``weave``'s
prompt already treats an ABSENT ``takeaways-guidance.md`` as "no special
emphasis was requested," exactly like an ordinary pass has always looked
before this gate existed.

Always clears any stale file from a PRIOR pass first (this node runs fresh
every segment-processing pass -- see ``pipeline/ingest.dot``'s
``prepare_takeaways_brief -> takeaways_gate -> persist_takeaways -> weave``
sequence) so a stub/blank/stale answer can never leave a stale REAL answer
from a previous segment sitting around for weave to (wrongly) apply again.

F4 (GOAL-followups.md) -- RETRACT:/ABSORB: DIRECTIVES: this is the exact
gate a human twice asked, in plain freeform text, to remove a page that had
already leaked into the wiki ("Delete joe-njenga.md", repeated a source
later because it was still there -- see ``wiki_weaver.ingest.retract``'s
module docstring for the full case). Before this text is treated as
ordinary weave emphasis, it is scanned for AT MOST ONE
``RETRACT: <page.md>`` or ``ABSORB: <page.md> INTO: <target.md>`` line
(``wiki_weaver.ingest.retract.extract_retract_directive``). If found, the
page action is executed immediately (ledgered as ``"decision": "retract"``,
its own git commit) and only the REMAINING text (the directive line
stripped out) is persisted as takeaways guidance -- so an answer can both
retract a page AND steer this source's weave in the same breath. A
malformed directive (see that module's docstring for the exact rejected
shapes) is FAIL LOUD here too: this node exits 1 rather than silently
dropping the request, the same discipline every other freeform-capture
tool in this codebase already applies to a malformed/stub answer. Needed
ZERO ``pipeline/ingest.dot`` changes -- this node already runs
unconditionally after every ``takeaways_gate`` answer.

Usage:
    python3 -m wiki_weaver.ingest.persist_takeaways --wiki-root <path> --out .ai/takeaways-guidance.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.ingest.retract import RetractDirectiveError, extract_retract_directive, retract_page
from wiki_weaver.lib import WikiRoot, atomic_write_text, read_current_source_id

AUTO_APPROVED_STUB = "auto-approved"

# THE PROXY'S HONEST "nothing to steer" ANSWER for this gate (see
# wiki_weaver.aitl.backend's _TAKEAWAYS_GATE_NOTE and lens/persona.md's own
# takeaways-discussion guidance: "If the gist and candidates give you
# nothing to go on, say 'no specific emphasis' rather than inventing
# one."). LIVE FAILURE THIS FIXES: before that backend note existed, a
# well-formed brief with no strong signal made the proxy choose give_up
# (fail-loud, halts the pipeline) instead of answering per the persona's
# own instruction. Now that the proxy DOES answer with this literal
# phrase, it must be treated exactly like the auto-approved stub here --
# a real, honest "no guidance" outcome, not a literal steering
# instruction to hand weave. Case-insensitive, trailing "." tolerated, so
# "No specific emphasis." still matches.
NO_EMPHASIS_STUB = "no specific emphasis"


def _is_stub_answer(text: str) -> bool:
    """``text`` is a stand-in for "no guidance," not real steering --
    either the unattended auto-approve placeholder or the proxy's
    canonical no-signal phrase (see ``NO_EMPHASIS_STUB`` above). Both mean
    the same thing to ``weave``: proceed unsteered, exactly as if no
    takeaways-answer file existed at all."""
    normalized = text.strip().lower().rstrip(".")
    return normalized in (AUTO_APPROVED_STUB, NO_EMPHASIS_STUB)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.persist_takeaways")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--out", required=True)
    return parser


def _read_answer_text(wr: WikiRoot, current_source_id: str) -> str | None:
    """The real answer text from ``takeaways-answer.json``, or ``None`` if
    absent/unreadable/malformed/stale -- every one of those is treated as
    "no guidance was given" (see module docstring), never fail-loud: a
    blank/missing answer is a NORMAL, expected outcome for this gate."""
    path = wr.takeaways_answer_file
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    text = data.get("text")
    source_id = data.get("source_id")
    if not isinstance(text, str):
        return None
    if not isinstance(source_id, str) or source_id != current_source_id:
        return None  # stale (a previous source's answer) -- never trust it
    return text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    out_path = Path(args.out)
    # Never let a prior pass's real guidance survive, unconfirmed, into this
    # one -- see module docstring's staleness argument.
    out_path.unlink(missing_ok=True)

    try:
        current_source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    text = _read_answer_text(wr, current_source_id)
    if text is not None:
        text = text.strip()

    if not text or _is_stub_answer(text):
        print(
            "no takeaways guidance provided -- proceeding without special emphasis",
            file=sys.stderr,
        )
        return 0

    # F4: a RETRACT:/ABSORB: directive embedded in this answer is executed
    # NOW, before the rest is treated as ordinary weave emphasis -- see
    # module docstring and wiki_weaver.ingest.retract's module docstring.
    # FAIL LOUD on a malformed directive or a missing page/target: this
    # node exits 1 rather than silently dropping the request.
    try:
        directive, text = extract_retract_directive(text)
    except RetractDirectiveError as exc:
        print(f"malformed retract directive: {exc}", file=sys.stderr)
        return 1

    if directive is not None:
        try:
            record = retract_page(wr, directive, requested_during_source_id=current_source_id)
        except FileNotFoundError as exc:
            print(f"retract failed: {exc}", file=sys.stderr)
            return 1
        print(f"retracted per gate answer: {json.dumps(record)}", file=sys.stderr)

    if not text:
        print(
            "no takeaways guidance provided -- proceeding without special emphasis",
            file=sys.stderr,
        )
        return 0

    atomic_write_text(out_path, text)
    print(f"takeaways guidance persisted to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
