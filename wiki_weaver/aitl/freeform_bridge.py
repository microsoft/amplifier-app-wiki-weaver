"""wiki_weaver.aitl.freeform_bridge -- the real hand-off for takeaways_gate's
freeform answer, working around a VERIFIED platform defect in the engine's
own env-var convention.

THE DEFECT (found during live DTU verification, not theoretical): the engine
(``amplifier_module_loop_pipeline.handlers.human.HumanGateHandler
._execute_freeform``) puts a freeform answer into context under the key
``"human.gate.text"`` (a DOTTED key, hardcoded, not something wiki-weaver
controls). ``tool_env="human.gate.text,py"`` (the exact convention
``collect_guidance``'s ``persist_guidance.py`` already used, reused
verbatim for ``takeaways_gate``/``persist_takeaways.py``) upper-cases that
key VERBATIM to build the subprocess environment variable name:
``HUMAN.GATE.TEXT``. Reproduced directly, outside the pipeline entirely::

    env = dict(os.environ); env["HUMAN.GATE.TEXT"] = "hello world test"
    proc = await asyncio.create_subprocess_shell(
        "python3 -c \"import os; print(os.environ.get('HUMAN.GATE.TEXT'))\"",
        env=env, ...)
    # -> None. The identical value under "HUMAN_GATE_TEXT" (underscored)
    #    DOES survive. Only the DOTTED name is silently dropped somewhere
    #    between this process and the child's environment on this platform.

This means the dotted env-var hand-off was ALREADY unreliable for
``collect_guidance``/``persist_guidance`` before this change -- this module
does not touch that pre-existing path (out of scope for this task), but
does not repeat the same broken convention for the NEW gate either.

WHY NOT command-string substitution instead (``${human.gate.text}`` in
``tool_command``): ``persist_guidance.py``'s own docstring already states
the reason this is forbidden -- "the free-form text itself must never
transit a $substitution token" (arbitrary human/LLM-authored text
substituted into a shell command string is a shell-injection vector).
Neither existing channel (broken env var, unsafe substitution) is usable.

THE FIX: every ``Interviewer`` this project constructs (regardless of mode
-- console, auto-approve, proxy) is wrapped in ``FreeformAnswerRecorder``,
which writes ITS OWN freeform ``takeaways_gate`` answer directly to a
stamped JSON file (``WikiRoot.takeaways_answer_file``) the MOMENT it
answers -- a plain Python file write, no subprocess environment and no
shell substitution involved at all. ``wiki_weaver.ingest.persist_takeaways``
then reads that file (verifying freshness against ``current_source_file``,
the same discipline every other stamped brief file in this codebase
already uses) instead of an environment variable. This is the same
file-based hand-off pattern review_brief_file/guidance_brief_file/
takeaways_brief_file already prove -- just running in the other direction
(answerer -> tool, instead of tool -> answerer).

This wrapper is UNCONDITIONAL (applied for every mode, not just per-site
overrides) -- see ``wiki_weaver.aitl.run._build_single_interviewer`` -- so
takeaways_gate's answer reaches ``weave`` correctly whether the gate is
answered by a human, an unattended auto-approve, or the AITL proxy.

SECOND FREEFORM SITE (``synthesis_gate``, ``pipeline/synthesize.dot`` --
see ``wiki_weaver.synthesize.synthesis_brief``'s module docstring): the
identical platform defect applies to ANY freeform gate, not just
``takeaways_gate``. ``synthesis_gate`` has no per-source identity to stamp
its answer with (it is a pass-level checkpoint over a CANDIDATE SET, not a
single source) -- its answer file is stamped with the candidate-set
``signature`` ``wiki_weaver.synthesize.synthesis_brief`` computed instead of
a ``source_id``. See ``wiki_weaver.synthesize.persist_synthesis_guidance``
for the reader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from wiki_weaver.aitl.proxy_interviewer import to_gate_question
from wiki_weaver.lib import WikiRoot, atomic_write_text, read_current_source_id

_TAKEAWAYS_GATE_STAGE = "takeaways_gate"
_SYNTHESIS_GATE_STAGE = "synthesis_gate"


def _is_takeaways_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as
    ``proxy_interviewer._is_takeaways_gate_stage`` -- duplicated rather than
    imported to keep this module usable standalone (it wraps interviewers
    the ``aitl`` package does not otherwise touch, e.g. ``AutoApproveInterviewer``)."""
    return stage == _TAKEAWAYS_GATE_STAGE or stage.startswith(f"{_TAKEAWAYS_GATE_STAGE}-")


def _is_synthesis_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_takeaways_gate_stage``
    above, applied to the second freeform site this bridge covers."""
    return stage == _SYNTHESIS_GATE_STAGE or stage.startswith(f"{_SYNTHESIS_GATE_STAGE}-")


def _read_synthesis_signature(wr: WikiRoot) -> str | None:
    """The candidate-set ``signature``
    ``wiki_weaver.synthesize.synthesis_brief`` stamped ``synthesis-brief.json``
    with for THIS pass -- the ``synthesis_gate`` analogue of
    ``read_current_source_id`` above. Best-effort: ``None`` on any
    missing/malformed brief (nothing durable to stamp the answer with),
    never a crash."""
    path = wr.synthesis_brief_file
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    signature = data.get("signature")
    return signature if isinstance(signature, str) and signature else None


class FreeformAnswerRecorder:
    """Wraps ANY ``Interviewer`` so a freeform ``takeaways_gate`` or
    ``synthesis_gate`` answer is written directly to its own stamped answer
    file the moment it is given -- see module docstring for the platform
    defect this works around. Transparent pass-through for every other
    gate/stage.

    Best-effort: if there is nothing durable to stamp the answer with
    (``takeaways_gate``: no current source; ``synthesis_gate``: no
    synthesis brief), the write is skipped -- this must never crash the
    pipeline over a recording side-channel.
    """

    def __init__(self, inner: Any, wiki_root: str | Path) -> None:
        self._inner = inner
        self._wiki_root = wiki_root

    async def async_ask(self, question: Any) -> Any:
        if hasattr(self._inner, "async_ask"):
            answer = await self._inner.async_ask(question)
        else:
            answer = self._inner.ask(question)
        self._record(question, answer)
        return answer

    def ask(self, question: Any) -> Any:
        answer = self._inner.ask(question)
        self._record(question, answer)
        return answer

    def ask_multiple(self, questions: list[Any]) -> list[Any]:
        return [self.ask(q) for q in questions]

    def inform(self, message: str) -> None:
        self._inner.inform(message)

    def _record(self, question: Any, answer: Any) -> None:
        from amplifier_module_loop_pipeline.interviewer import AnswerValue

        gate_question = to_gate_question(question)
        if gate_question.gate_type != "freeform":
            return

        wr = WikiRoot(self._wiki_root)
        if _is_takeaways_gate_stage(gate_question.stage):
            stage_name, out_file, key_field = _TAKEAWAYS_GATE_STAGE, wr.takeaways_answer_file, "source_id"
        elif _is_synthesis_gate_stage(gate_question.stage):
            stage_name, out_file, key_field = _SYNTHESIS_GATE_STAGE, wr.synthesis_answer_file, "signature"
        else:
            return

        # A refused/skipped/timed-out answer carries no real text -- Answer's
        # own default `text=""` means a bare `isinstance(text, str)` check is
        # NOT enough to detect this (it would write an empty-string record).
        value = getattr(answer, "value", None)
        if isinstance(value, AnswerValue):
            return

        text = getattr(answer, "text", None)
        if not isinstance(text, str):
            return

        if stage_name == _TAKEAWAYS_GATE_STAGE:
            try:
                key_value: str | None = read_current_source_id(wr)
            except FileNotFoundError:
                key_value = None
        else:
            key_value = _read_synthesis_signature(wr)

        if key_value is None:
            return  # nothing durable to stamp this answer with -- skip, never crash

        atomic_write_text(
            out_file,
            json.dumps({key_field: key_value, "stage": stage_name, "text": text}, indent=2, ensure_ascii=False) + "\n",
        )
