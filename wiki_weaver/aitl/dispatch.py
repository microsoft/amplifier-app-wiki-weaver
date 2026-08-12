"""wiki_weaver.aitl.dispatch -- per-site (per-gate) gate-mode dispatch.

WHY THIS LIVES HERE, NOT IN THE ENGINE (docs/KERNEL_PHILOSOPHY.md's
"mechanism, not policy"): ``HumanGateHandler`` accepts exactly ONE
``Interviewer`` for an entire pipeline run (the ``interviewer=`` parameter
``amplifier_module_pipeline_runner.runner.run_pipeline`` and
``HumanGateHandler.__init__`` both expose -- see ``wiki_weaver.aitl.run``'s
module docstring). That is the engine's MECHANISM: a single, swappable
answerer seam. WHICH answerer applies to WHICH gate in a given wiki's
pipeline is a wiki-weaver-level POLICY choice, so per-site selection is
built here, as a wrapper around that one seam, not as a change to
``HumanGateHandler`` itself.

``PerSiteInterviewer`` is a single ``Interviewer`` (satisfies the exact
same protocol ``AutoApproveInterviewer``/``ConsoleInterviewer``/
``ProxyInterviewer`` already do) that inspects
``Question.stage`` -- already carrying the node id, ``-N``-suffixed on
re-entrant loop passes (``HumanGateHandler._get_stage_id``) -- strips that
suffix (see ``base_stage_name``, generalizing the reasoning
``wiki_weaver.aitl.proxy_interviewer._is_review_gate_stage`` /
``_is_collect_guidance_stage`` already apply per-gate to ANY stage name),
and routes to whichever concrete ``Interviewer`` was built for that gate's
configured mode. Any stage with no explicit override falls back to the
run's global ``--gate-mode``.

A stage that resolves to ``gate-mode=fail`` (either because that is the
global default and no override applies, or because a per-site override
explicitly asked for it) cannot be represented by leaving
``interviewer=None`` for the WHOLE run any more -- some OTHER site might be
a real interviewer. ``GateModeFailError`` is raised instead, at the moment
that specific gate is reached -- the per-site equivalent of
``HumanGateHandler.execute``'s own "requires an Interviewer but none was
provided" guard, just deferred from "run start" to "this gate is reached"
since different gates in the same run may resolve differently.

``AuditingInterviewer`` is the second half of this module: task requirement
3 (attribute which MODE answered which gate in ``.ai/gate-decisions.jsonl``,
so an A/B across gate configurations can be attributed after the fact).
``ProxyInterviewer`` already writes its own, richer audit entry per
decision (grounding, persona digest, refusal reasons --
``wiki_weaver.aitl.audit``) and is marked ``_SELF_AUDITS = True``; every
OTHER interviewer (console, auto-approve) writes nothing today (see
``wiki_weaver.ingest.guidance_brief``'s module docstring: "a human reviewer
using the console interviewer writes nothing to this file at all"). Wrap
those in ``AuditingInterviewer`` so EVERY gate decision -- not just the
proxy's -- lands in the same durable, attributable trail, tagged with the
mode that produced it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from wiki_weaver.aitl import audit
from wiki_weaver.aitl.proxy_interviewer import to_gate_question

_REENTRY_SUFFIX_RE = re.compile(r"-\d+$")

VALID_GATE_MODES = ("fail", "auto-approve", "console", "proxy")


def base_stage_name(stage: str) -> str:
    """Strip a ``HumanGateHandler`` re-entrant loop suffix (``-2``, ``-3``,
    ...) off a stage id, so ``"review_gate-2"`` and ``"review_gate"``
    resolve to the same per-site override. Generalizes the same reasoning
    ``proxy_interviewer._is_review_gate_stage`` /
    ``_is_collect_guidance_stage`` already apply per-gate, to ANY stage
    name, for per-site mode dispatch.
    """
    return _REENTRY_SUFFIX_RE.sub("", stage)


class GateModeFailError(RuntimeError):
    """Raised when a gate resolves to gate-mode=fail -- the per-site
    equivalent of ``HumanGateHandler.execute``'s own "requires an
    Interviewer but none was provided" guard. Uncaught, this propagates out
    of the pipeline run exactly like that guard's ``ValueError`` does today
    -- a fail-loud stop, never a guess.
    """


class PerSiteInterviewer:
    """Dispatches each gate question to the ``Interviewer`` configured for
    that gate's stage (see module docstring), defaulting to the run's
    global ``--gate-mode`` for any stage with no explicit override.
    """

    def __init__(self, default: Any | None, overrides: dict[str, Any | None]) -> None:
        self._default = default
        self._overrides = overrides  # {base_stage_name: Interviewer | None}

    def _resolve(self, stage: str) -> Any:
        base = base_stage_name(stage)
        interviewer = self._overrides.get(base, self._default)
        if interviewer is None:
            raise GateModeFailError(
                f"gate {stage!r} resolved to gate-mode=fail (no Interviewer configured for "
                f"stage {base!r}) -- the pipeline must stop here rather than guess an answer"
            )
        return interviewer

    def ask(self, question: Any) -> Any:
        return self._resolve(question.stage).ask(question)

    async def async_ask(self, question: Any) -> Any:
        interviewer = self._resolve(question.stage)
        if hasattr(interviewer, "async_ask"):
            return await interviewer.async_ask(question)
        return interviewer.ask(question)

    def ask_multiple(self, questions: list[Any]) -> list[Any]:
        """L-18: delegate to ``ask()`` for each question (matches every
        other Interviewer implementation's convention)."""
        return [self.ask(q) for q in questions]

    def inform(self, message: str) -> None:
        """Broadcasts to the default interviewer only -- a one-way
        notification has no single gate to key an override off of."""
        if self._default is not None:
            self._default.inform(message)


def _describe_answer(gate_question: Any, answer: Any) -> dict[str, Any]:
    """Reduce an engine ``Answer`` to the fields ``audit.record_decision``
    needs, for gates whose own interviewer does not self-audit. Mirrors
    (deliberately more simply than) ``ProxyInterviewer``'s own
    decision/grounding shape -- this path never has a "grounding" citation
    to report (console/auto-approve are not reasoning from a persona), so
    that field is always ``None`` here.
    """
    from amplifier_module_loop_pipeline.interviewer import AnswerValue

    value = getattr(answer, "value", None)
    if isinstance(value, AnswerValue) and value == AnswerValue.SKIPPED:
        return {"decision": "refused", "reason": "interviewer returned SKIPPED (no answer given)"}
    if isinstance(value, AnswerValue) and value == AnswerValue.TIMEOUT:
        return {"decision": "timeout", "reason": "interviewer timed out waiting for an answer"}

    if gate_question.gate_type == "choice":
        choice_key = value
        choice_label = None
        selected = getattr(answer, "selected_option", None)
        if selected is not None:
            choice_key = selected.key
            choice_label = selected.label
        return {
            "decision": "answered",
            "reason": "",
            "choice_key": choice_key if isinstance(choice_key, str) else None,
            "choice_label": choice_label,
        }

    answer_text = getattr(answer, "text", "") or ""
    return {"decision": "answered", "reason": "", "answer_text": answer_text}


class AuditingInterviewer:
    """Wraps any non-self-auditing ``Interviewer`` so EVERY gate decision --
    not just the proxy's own rich record -- is attributed to the gate-mode
    that answered it in ``.ai/gate-decisions.jsonl`` (see module docstring).
    Best-effort: if ``wiki_root`` is unavailable (some pipelines run
    console/auto-approve with no ``--param wiki_root``), auditing is
    silently skipped -- there is nowhere to write it, and a run must never
    fail over an audit trail it cannot place.
    """

    def __init__(self, inner: Any, wiki_root: str | Path, mode: str) -> None:
        self._inner = inner
        self._wiki_root = wiki_root
        self._mode = mode

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
        if self._wiki_root is None:
            return  # nothing to write into -- best-effort only, never crash the run
        gate_question = to_gate_question(question)
        details = _describe_answer(gate_question, answer)
        choices = [label for _key, label in gate_question.choices] or None
        audit.record_decision(
            self._wiki_root,
            stage=gate_question.stage,
            gate_type=gate_question.gate_type,
            question=gate_question.text,
            choices=choices,
            mode=self._mode,
            **details,  # type: ignore[arg-type]
        )
