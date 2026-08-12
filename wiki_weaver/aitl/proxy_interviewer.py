"""wiki_weaver.aitl.proxy_interviewer -- the ProxyInterviewer.

Implements attractor's ``Interviewer`` protocol
(amplifier_module_loop_pipeline.interviewer) so it is a drop-in alternative
to ``AutoApproveInterviewer`` / ``ConsoleInterviewer`` at exactly the same
seam: the ``interviewer=`` parameter accepted by both
``amplifier_module_pipeline_runner.runner.run_pipeline`` (the "reusable
engine harness ... the seam a consumer with its own session/bundle
lifecycle plugs into") and, one level down,
``amplifier_module_loop_pipeline.handlers.human.HumanGateHandler``. No
change of any kind to the attractor engine or its own ``attractor`` CLI --
see ``wiki_weaver.aitl.run`` for the launcher that wires this in alongside
the existing modes.

FAILS LOUD (returns ``Answer(value=AnswerValue.SKIPPED)``) in every case
where it cannot construct a confident, grounded answer:
  - ``lens/persona.md`` missing, empty, or structurally incomplete
    (``wiki_weaver.aitl.persona`` -- checked BEFORE any backend call)
  - the backend gives up (persona doesn't cover this gate)
  - the backend raises (network/provider failure)
  - the backend's response doesn't parse, names an invalid choice, or
    answers with an empty/stub string (``wiki_weaver.aitl.backend
    ._parse_decision``)

``SKIPPED`` is not a special case invented here -- it is the SAME answer
value ``HumanGateHandler._check_special_answer`` already converts into a
FAIL ``Outcome`` for every other interviewer, halting the pipeline rather
than guessing. Reusing it means "the proxy refused" gets the exact same,
already-tested fail-loud treatment as "a human hit Ctrl-D at the console
prompt" -- no new engine behavior to trust.

Every outcome -- answered AND refused -- is recorded via
``wiki_weaver.aitl.audit`` before returning (docs/DESIGN.md Sec 7:
"Never let the proxy self-certify ... Proxy decisions are provisional and
logged").
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

from amplifier_module_loop_pipeline.interviewer import (
    Answer,
    AnswerValue,
    Option,
    QuestionType,
)

from wiki_weaver.aitl import audit
from wiki_weaver.aitl.backend import GateQuestion, ProxyBackend
from wiki_weaver.aitl.corrections import load_corrections
from wiki_weaver.aitl.persona import load_persona
from wiki_weaver.lib import WikiRoot, read_current_source_id

# The gates this proxy has a factual, gate-specific brief for today (see
# module docstring's "what I got wrong" -- do not generalize this to every
# gate; wiki-weaver's other 5 pipelines' gates are answered from persona +
# label alone, same as before this fix). collect_guidance is the [B] Guide
# follow-up to review_gate -- same defect class (a gate reached with zero
# grounding refuses rather than fabricates), same fix shape (a deterministic
# node writes a stamped, freshness-checked brief file moments earlier in the
# SAME pass; see wiki_weaver.ingest.guidance_brief). takeaways_gate is the
# PRE-write analogue -- same defect class, same fix shape, this time via
# wiki_weaver.ingest.takeaways_brief (see that module's docstring).
# curate_gate is the source-curation checkpoint (see wiki_weaver.ingest.curate_brief)
# -- same defect class, same fix shape, reached BEFORE detect_kind/segment_source ever
# run for a source. file_back_gate is ask.dot's own analogue (see
# wiki_weaver.ask.file_back_brief) -- same defect class, but its brief has no
# source-id-style freshness stamp (ask.dot has no per-source resume loop of its own;
# see that module's docstring for why presence + non-empty brief is the whole contract).
# synthesis_gate is pipeline/synthesize.dot's own analogue (see
# wiki_weaver.synthesize.synthesis_brief) -- same defect class, same fix shape,
# reached BEFORE select_gap ever picks a candidate to write a page for. Its brief
# is stamped with a candidate-set signature rather than a source_id (this gate has
# no per-source identity -- see that module's docstring), but is otherwise
# verified with the SAME presence/non-empty discipline as every gate above.
_REVIEW_GATE_STAGE = "review_gate"
_COLLECT_GUIDANCE_STAGE = "collect_guidance"
_TAKEAWAYS_GATE_STAGE = "takeaways_gate"
_CURATE_GATE_STAGE = "curate_gate"
_FILE_BACK_GATE_STAGE = "file_back_gate"
_SYNTHESIS_GATE_STAGE = "synthesis_gate"


def _is_review_gate_stage(stage: str) -> bool:
    """``stage`` is ``node.id``, optionally suffixed ``-N`` on re-entrant
    loop passes (``HumanGateHandler._get_stage_id``) -- e.g. a [B] Guide ->
    re-weave -> back-to-review_gate loop produces ``\"review_gate-2\"``,
    ``\"review_gate-3\"``, etc. Every one of those passes is still the same
    gate and needs the same freshly-written brief.
    """
    return stage == _REVIEW_GATE_STAGE or stage.startswith(f"{_REVIEW_GATE_STAGE}-")


def _is_collect_guidance_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_review_gate_stage`` above --
    collect_guidance can in principle be reached on more than one pass too."""
    return stage == _COLLECT_GUIDANCE_STAGE or stage.startswith(f"{_COLLECT_GUIDANCE_STAGE}-")


def _is_takeaways_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_review_gate_stage`` above --
    takeaways_gate is reached once per segment-processing pass, and a
    reweave loop never revisits it, but the same defensive suffix check
    costs nothing and keeps this consistent with its two siblings."""
    return stage == _TAKEAWAYS_GATE_STAGE or stage.startswith(f"{_TAKEAWAYS_GATE_STAGE}-")


def _is_curate_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_review_gate_stage`` above --
    curate_gate is reached once per freshly-selected source, and no loop
    ever revisits it, but the same defensive suffix check costs nothing."""
    return stage == _CURATE_GATE_STAGE or stage.startswith(f"{_CURATE_GATE_STAGE}-")


def _is_synthesis_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_review_gate_stage`` above --
    synthesis_gate is reached at most once per synthesize.dot invocation
    (between rank_gap_candidates and select_gap -- see
    wiki_weaver.synthesize.synthesis_brief's module docstring), but the same
    defensive suffix check costs nothing and keeps this consistent with its
    siblings."""
    return stage == _SYNTHESIS_GATE_STAGE or stage.startswith(f"{_SYNTHESIS_GATE_STAGE}-")


def _is_file_back_gate_stage(stage: str) -> bool:
    """Same re-entrant-suffix reasoning as ``_is_review_gate_stage`` above --
    file_back_gate is reached at most once per ask.dot invocation, but the
    same defensive suffix check costs nothing and keeps this consistent
    with its siblings."""
    return stage == _FILE_BACK_GATE_STAGE or stage.startswith(f"{_FILE_BACK_GATE_STAGE}-")


def _load_brief_file(path: Path, current_source_id: str, *, kind: str) -> tuple[str | None, str | None]:
    """Read and validate a stamped ``{source_id, stage, brief}`` JSON brief
    file for the CURRENT source.

    Returns ``(brief_text, None)`` on success, or ``(None, reason)`` when the
    brief is missing, unreadable, malformed, or stale (written for a source
    other than the one ``current_source.txt`` names right now). Every
    failure reason is a REFUSAL condition for the caller -- a brief that
    cannot be verified current is worse than no brief at all (a confident
    wrong decision instead of an honest refusal); never fall back to
    answering from the bare node label. Shared by both ``review_gate``'s
    ``review-brief.json`` and ``collect_guidance``'s ``guidance-brief.json``
    -- same file shape, same verification, different path.
    """
    if not path.is_file():
        return None, f"{path} does not exist -- no {kind} to judge this gate from"

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

    if brief_source_id != current_source_id:
        return None, (
            f"{path} is stale: brief was written for source {brief_source_id!r}, "
            f"but the current source is {current_source_id!r}"
        )

    return brief_text, None


def _load_review_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``review-brief.json`` for the CURRENT source. See
    ``_load_brief_file`` for the shared verification contract."""
    wr = WikiRoot(wiki_root)
    try:
        current_source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        return None, f"cannot verify brief freshness -- no current source: {exc}"
    return _load_brief_file(wr.review_brief_file, current_source_id, kind="weave brief")


def _load_guidance_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``guidance-brief.json`` for the CURRENT source --
    the ``collect_guidance`` analogue of ``_load_review_brief`` above. See
    ``_load_brief_file`` for the shared verification contract and
    ``wiki_weaver.ingest.guidance_brief`` for what writes this file."""
    wr = WikiRoot(wiki_root)
    try:
        current_source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        return None, f"cannot verify brief freshness -- no current source: {exc}"
    return _load_brief_file(wr.guidance_brief_file, current_source_id, kind="guidance brief")


def _load_takeaways_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``takeaways-brief.json`` for the CURRENT source --
    the PRE-write analogue of ``_load_review_brief`` above. See
    ``_load_brief_file`` for the shared verification contract and
    ``wiki_weaver.ingest.takeaways_brief`` for what writes this file."""
    wr = WikiRoot(wiki_root)
    try:
        current_source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        return None, f"cannot verify brief freshness -- no current source: {exc}"
    return _load_brief_file(wr.takeaways_brief_file, current_source_id, kind="takeaways brief")


def _load_curate_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``curate-brief.json`` for the CURRENT source --
    the source-curation analogue of ``_load_review_brief`` above. See
    ``_load_brief_file`` for the shared verification contract and
    ``wiki_weaver.ingest.curate_brief`` for what writes this file."""
    wr = WikiRoot(wiki_root)
    try:
        current_source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        return None, f"cannot verify brief freshness -- no current source: {exc}"
    return _load_brief_file(wr.curate_brief_file, current_source_id, kind="curation brief")


def _load_synthesis_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``synthesis-brief.json`` for ``synthesize.dot``'s
    ``synthesis_gate`` -- like ``_load_file_back_brief`` below, this has NO
    ``source_id``-style freshness check (this gate has no per-source resume
    loop; ``wiki_weaver.synthesize.synthesis_brief`` writes this file
    immediately before ``synthesis_gate`` is ever reached, in the same
    linear pass -- see that module's docstring). Presence + a non-empty
    ``brief`` field is the whole contract here."""
    wr = WikiRoot(wiki_root)
    path = wr.synthesis_brief_file
    if not path.is_file():
        return None, f"{path} does not exist -- no candidate list to judge this gate from"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} could not be read/parsed: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} did not contain a JSON object"
    brief_text = data.get("brief")
    if not isinstance(brief_text, str) or not brief_text.strip():
        return None, f"{path} has no usable 'brief' text"
    return brief_text, None


def _load_file_back_brief(wiki_root: str | Path) -> tuple[str | None, str | None]:
    """Read and validate ``file-back-brief.json`` for ``ask.dot``'s
    ``file_back_gate``. Unlike every ingest-side brief above, this has NO
    source-id-style freshness check -- ``ask.dot`` has no per-source resume
    loop of its own (see ``wiki_weaver.ask.file_back_brief``'s module
    docstring for why presence + a non-empty ``brief`` field is the whole
    contract here)."""
    wr = WikiRoot(wiki_root)
    path = wr.file_back_brief_file
    if not path.is_file():
        return None, f"{path} does not exist -- no answer/question brief to judge this gate from"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} could not be read/parsed: {exc}"
    if not isinstance(data, dict):
        return None, f"{path} did not contain a JSON object"
    brief_text = data.get("brief")
    if not isinstance(brief_text, str) or not brief_text.strip():
        return None, f"{path} has no usable 'brief' text"
    return brief_text, None


def to_gate_question(question: object) -> GateQuestion:
    """Reduce attractor's real ``Question`` to our minimal ``GateQuestion``.

    ``QuestionType.FREEFORM`` -> ``gate_type="freeform"``. Everything else
    (``MULTIPLE_CHOICE`` / ``CONFIRMATION`` / ``YES_NO``) is treated as a
    choice gate. None of wiki-weaver's 6 pipelines produce ``CONFIRMATION``
    or ``YES_NO`` today -- every hexagon gate is either ``mode="freeform"``
    or has labeled outgoing edges, and ``HumanGateHandler`` always renders
    the latter as ``MULTIPLE_CHOICE`` with real ``options``. The yes/no
    synthesis below exists defensively so a future gate of that shape is
    handled explicitly rather than silently mishandled.

    Public (not underscore-prefixed): also used by
    ``wiki_weaver.aitl.dispatch.AuditingInterviewer`` to reduce a Question
    for non-proxy interviewers' audit entries -- same package, one shared
    reduction, never duplicated (unlike the cross-package precedent in
    ``wiki_weaver.ingest.guidance_brief``, which duplicates rather than
    imports FROM ``aitl`` to avoid a new package dependency direction; this
    is aitl-internal reuse, not that).
    """
    stage = getattr(question, "stage", "") or ""
    text = getattr(question, "text", "") or ""

    if getattr(question, "type", None) == QuestionType.FREEFORM:
        return GateQuestion(text=text, gate_type="freeform", stage=stage)

    options = list(getattr(question, "options", []) or [])
    if not options:
        options = [Option(key="yes", label="Yes"), Option(key="no", label="No")]
    choices = tuple((opt.key, opt.label) for opt in options)
    return GateQuestion(text=text, gate_type="choice", stage=stage, choices=choices)


class ProxyInterviewer:
    """Answers human-gate questions from ``lens/persona.md``, in character,
    failing loud (never inventing) when it cannot ground an answer.
    """

    # Marks this interviewer as already writing its OWN (richer) audit
    # entry per decision -- see wiki_weaver.aitl.dispatch.AuditingInterviewer,
    # which checks this attribute to avoid double-logging a gate answered
    # by --gate-mode=proxy.
    _SELF_AUDITS = True

    def __init__(self, wiki_root: str | Path, backend: ProxyBackend) -> None:
        self.wiki_root = Path(wiki_root)
        self._backend = backend

    # -- Interviewer protocol (amplifier_module_loop_pipeline.interviewer) --

    def ask(self, question: object) -> Answer:
        """Synchronous entry point -- convenient for direct/test callers.
        The engine itself prefers ``async_ask`` (see below) specifically to
        avoid the sync/async bridge deadlock; this wrapper only runs its
        own event loop when none is already running.
        """
        return asyncio.run(self.async_ask(question))

    async def async_ask(self, question: object) -> Answer:
        """Preferred by ``HumanGateHandler._dispatch_ask`` when present --
        avoids the sync/async bridge deadlock the engine's own docstring
        warns about (no ``nest_asyncio`` in the worker container)."""
        gate_question = to_gate_question(question)
        persona = load_persona(self.wiki_root)

        if not persona.ok:
            self._record(gate_question, decision="refused", reason=persona.reason, persona_text=None)
            self._log(f"refusing gate {gate_question.stage!r}: {persona.reason}")
            return Answer(value=AnswerValue.SKIPPED)

        # THE feedback-loop close: standing corrections (lens/corrections/)
        # loaded fresh from disk for EVERY gate -- see
        # wiki_weaver.aitl.corrections's module docstring for why this
        # exists and why it is fail-SOFT (never a refusal condition). No
        # correction ever having been filed is the common case and must not
        # change behavior for any gate that predates this feature.
        corrections = load_corrections(self.wiki_root)
        if corrections.text:
            gate_question = replace(gate_question, corrections=corrections.text)
        # Threaded into every _record() call below so gate-decisions.jsonl
        # shows, per decision, whether standing corrections were in effect
        # (via corrections_digest -- see audit.py) -- the traceability the
        # task's own acceptance bar requires: two runs of the SAME gate must
        # be distinguishable by whether a correction was present.
        corrections_for_audit = corrections.text or None

        # review_gate is the one gate this proxy cannot judge from persona +
        # label alone -- it needs the weave brief (what the pipeline
        # actually did for THIS source). Missing/stale is a REFUSAL, not a
        # fall-through to answering cold (see module docstring's "Absence").
        if _is_review_gate_stage(gate_question.stage):
            brief_text, brief_error = _load_review_brief(self.wiki_root)
            if brief_error:
                self._record(
                    gate_question,
                    decision="refused",
                    reason=brief_error,
                    persona_text=persona.text,
                    corrections_text=corrections_for_audit,
                )
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        # collect_guidance is the [B] Guide follow-up -- same defect class as
        # review_gate above (a gate reached with zero grounding refuses
        # rather than fabricates steering text). Missing/stale is a
        # REFUSAL here too, never a fall-through to inventing steering text
        # cold (see wiki_weaver.ingest.guidance_brief's module docstring).
        elif _is_collect_guidance_stage(gate_question.stage):
            brief_text, brief_error = _load_guidance_brief(self.wiki_root)
            if brief_error:
                self._record(
                    gate_question,
                    decision="refused",
                    reason=brief_error,
                    persona_text=persona.text,
                    corrections_text=corrections_for_audit,
                )
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        # takeaways_gate is the PRE-write discussion site (see
        # wiki_weaver.ingest.takeaways_brief's module docstring) -- same
        # defect class as review_gate/collect_guidance above (a gate
        # reached with zero grounding refuses rather than fabricates
        # emphasis). Missing/stale is a REFUSAL here too, never a
        # fall-through to inventing emphasis cold.
        elif _is_takeaways_gate_stage(gate_question.stage):
            brief_text, brief_error = _load_takeaways_brief(self.wiki_root)
            if brief_error:
                self._record(gate_question, decision="refused", reason=brief_error, persona_text=persona.text)
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        # curate_gate is the source-curation checkpoint (see
        # wiki_weaver.ingest.curate_brief's module docstring) -- same defect
        # class as review_gate/collect_guidance/takeaways_gate above.
        # Missing/stale is a REFUSAL here too, never a fall-through to
        # guessing whether a source belongs in this wiki cold.
        elif _is_curate_gate_stage(gate_question.stage):
            brief_text, brief_error = _load_curate_brief(self.wiki_root)
            if brief_error:
                self._record(
                    gate_question,
                    decision="refused",
                    reason=brief_error,
                    persona_text=persona.text,
                    corrections_text=corrections_for_audit,
                )
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        # synthesis_gate is pipeline/synthesize.dot's own pass-level analogue
        # (see wiki_weaver.synthesize.synthesis_brief's module docstring) --
        # same defect class as every gate above. Missing/unreadable is a
        # REFUSAL here too, never a fall-through to guessing which candidate
        # themes deserve a page cold.
        elif _is_synthesis_gate_stage(gate_question.stage):
            brief_text, brief_error = _load_synthesis_brief(self.wiki_root)
            if brief_error:
                self._record(
                    gate_question,
                    decision="refused",
                    reason=brief_error,
                    persona_text=persona.text,
                    corrections_text=corrections_for_audit,
                )
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        # file_back_gate is ask.dot's own analogue (see
        # wiki_weaver.ask.file_back_brief's module docstring) -- same defect
        # class, no source-id-style freshness check (ask.dot has no
        # per-source resume loop of its own).
        elif _is_file_back_gate_stage(gate_question.stage):
            brief_text, brief_error = _load_file_back_brief(self.wiki_root)
            if brief_error:
                self._record(
                    gate_question,
                    decision="refused",
                    reason=brief_error,
                    persona_text=persona.text,
                    corrections_text=corrections_for_audit,
                )
                self._log(f"refusing gate {gate_question.stage!r}: {brief_error}")
                return Answer(value=AnswerValue.SKIPPED)
            gate_question = replace(gate_question, brief=brief_text)

        try:
            decision = await self._backend.decide(gate_question, persona.text)
        except Exception as exc:  # noqa: BLE001 -- any backend failure is fail-loud, never a guess
            reason = f"backend raised {type(exc).__name__}: {exc}"
            self._record(
                gate_question,
                decision="error",
                reason=reason,
                persona_text=persona.text,
                corrections_text=corrections_for_audit,
            )
            self._log(f"gate {gate_question.stage!r} errored: {reason}")
            return Answer(value=AnswerValue.SKIPPED)

        if decision.action != "answer":
            self._record(
                gate_question,
                decision="give_up",
                reason=decision.reason,
                grounding=decision.grounding,
                persona_text=persona.text,
                corrections_text=corrections_for_audit,
            )
            self._log(f"proxy gave up on gate {gate_question.stage!r}: {decision.reason}")
            return Answer(value=AnswerValue.SKIPPED)

        if gate_question.gate_type == "choice":
            label = dict(gate_question.choices).get(decision.choice_key or "")
            self._record(
                gate_question,
                decision="answered",
                reason=decision.reason,
                grounding=decision.grounding,
                choice_key=decision.choice_key,
                choice_label=label,
                persona_text=persona.text,
                corrections_text=corrections_for_audit,
            )
            self._log(f"proxy answered gate {gate_question.stage!r}: chose {label!r}")
            return Answer(
                value=decision.choice_key or "",
                selected_option=Option(key=decision.choice_key or "", label=label) if label else None,
                text=label or decision.choice_key or "",
            )

        self._record(
            gate_question,
            decision="answered",
            reason=decision.reason,
            grounding=decision.grounding,
            answer_text=decision.answer_text,
            persona_text=persona.text,
            corrections_text=corrections_for_audit,
        )
        self._log(f"proxy answered gate {gate_question.stage!r} (freeform)")
        return Answer(value=decision.answer_text or "", text=decision.answer_text or "")

    def ask_multiple(self, questions: list[object]) -> list[Answer]:
        """L-18: delegate to ``ask()`` for each question (matches every
        other Interviewer implementation's convention)."""
        return [self.ask(q) for q in questions]

    def inform(self, message: str) -> None:
        self._log(f"informed: {message}")

    # -- internals ------------------------------------------------------------

    def _record(self, gq: GateQuestion, **kwargs: object) -> None:
        choices = [label for _key, label in gq.choices] or None
        audit.record_decision(
            self.wiki_root,
            stage=gq.stage,
            gate_type=gq.gate_type,
            question=gq.text,
            mode="proxy",
            choices=choices,
            **kwargs,  # type: ignore[arg-type]
        )

    @staticmethod
    def _log(message: str) -> None:
        print(f"[aitl-proxy] {message}", file=sys.stderr)
