"""wiki_weaver.aitl.backend -- the proxy's reasoning step.

WHY THIS IS NOT A pipeline `box` NODE (a deliberate, documented choice):
docs/DESIGN.md Sec 7's guardrails, taken from aiuser's documented failures,
require: "Compose the proxy without tools it shouldn't have ... Ask nicely
-> it cheats. We give the proxy no such tools. A capability restriction,
not a request."

Every `box` node in this codebase spawns a full coding agent with its own
bash/filesystem/search tools -- amplifier_module_pipeline_runner.runner's
own module docstring: "Every LLM (box) node spawns a full attractor-agent-*
coding agent (its own loop-agent orchestrator + filesystem/bash/search
tools)". There is no per-node knob in a `.dot` file to strip those tools
back off. Asking a full agent "please don't use your tools" is exactly the
"ask nicely" pattern the guardrail names as aiuser's failure.

So the reasoning step here is deliberately NOT an agent: it is a single,
tool-free chat completion (``unified_llm.generate()``, no ``tools=``
argument at all). This is a MECHANICAL capability restriction -- there is
no tool-calling loop for the model to exploit, because none is ever
constructed -- not a request the model could ignore.

``unified_llm`` is imported lazily (inside ``LLMProxyBackend.decide``), not
at module import time, so importing this module (and the pure, network-free
``_parse_decision``/``_extract_json_object`` functions below) never requires
it to be installed. It is present in this workspace's environment because
this whole package only runs inside the attractor/amplifier stack (see
wiki_weaver.aitl.run's module docstring).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-sonnet-4-5"

# What "give up" means to a human reading the audit trail later, restated
# here so both the prompt and _parse_decision agree on the contract.
_SYSTEM_PROMPT = """You are answering ONE question on behalf of a human, in \
their place, for an automated wiki-maintenance pipeline. You have been given \
a character document (the "persona") that the human wrote describing how \
they want gates like this one answered for THIS wiki -- their intent, what \
matters to them, what to prioritize, and what to refuse.

Ground rules, non-negotiable:
1. Answer ONLY if the persona document gives you a real, specific basis for \
this exact question. Do not answer from general knowledge, politeness, or a \
guess at what seems reasonable -- answer from what the persona actually says.
2. If the persona does not address this topic, reserves this kind of decision \
for a real human (see any "Capability floor" / "Refusal script" section), or \
you are not confident, respond with action="give_up". This is a normal, \
expected, SAFE outcome -- never invent an answer instead.
3. When you DO answer, you must cite the specific part of the persona that \
justifies it in the "grounding" field. An answer with no real grounding will \
be rejected downstream regardless of what you put in "action".
4. Respond with EXACTLY ONE JSON object and nothing else (no prose before or \
after, no markdown fence). Schema:
   {"action": "answer" | "give_up",
    "reason": "<short, one or two sentences>",
    "grounding": "<specific persona sentence/section this is grounded in, \
or null if giving up>",
    "answer_text": "<your freeform answer, or null if this is a choice gate \
or you are giving up>",
    "choice_key": "<the option key you are choosing, or null if this is a \
freeform gate or you are giving up>"}
"""


# Shared by every stage-scoped give_up-override below (takeaways_gate,
# synthesis_gate, curate_gate as of this writing) -- see
# ``_STAGE_OVERRIDE_NOTES`` further down for why a third near-identical
# gate (curate_gate) triggered pulling this shape out into one predicate
# instead of copy-pasting a third pair of stage-constant + predicate.
def _stage_matches(stage: str, base_stage: str) -> bool:
    """True if ``stage`` IS ``base_stage``, or a re-entrant ``base_stage-N``
    suffix (``HumanGateHandler._get_stage_id`` appends ``-2``, ``-3``, ...
    when a loop revisits the same node -- e.g. a [B] Guide -> re-weave ->
    back-to-review_gate loop produces ``"review_gate-2"``, ``"review_gate-3"``,
    etc).

    Same re-entrant-suffix duplication as
    ``proxy_interviewer._is_takeaways_gate_stage`` et al. -- this module sits
    BELOW that one in the dependency direction (proxy_interviewer imports
    ``GateQuestion`` FROM here), so the tiny stage-prefix check is
    duplicated rather than imported, same reasoning
    ``wiki_weaver.ingest.guidance_brief``'s own docstring gives for its
    cross-package duplication. WITHIN this module, though, this one
    predicate is shared by every ``_is_*_gate_stage`` helper below.
    """
    return stage == base_stage or stage.startswith(f"{base_stage}-")


# takeaways_gate is reached on EVERY source (unlike collect_guidance, which
# is only reached after a reviewer deliberately chose to steer -- see
# wiki_weaver.ingest.persist_guidance's module docstring for why THAT gate
# correctly treats a no-signal answer as refuse-worthy: nobody asked to
# steer, so answering "nothing to add" would be nonsensical there).
# LIVE FAILURE THIS FIXES (see delivery report): the persona's own
# takeaways-discussion section already instructs "if the gist and
# candidates give you nothing to go on, say 'no specific emphasis' rather
# than inventing one" -- but ground rule 2 above (not confident ->
# give_up) swallowed that instruction: a model with nothing to steer read
# as exactly the "not confident" case rule 2 tells it to give up on. The
# proxy chose give_up on a well-formed, non-boilerplate brief, and
# give_up is fail-loud (ProxyInterviewer returns SKIPPED), so the whole
# pipeline halted on a source that had NOTHING wrong with it -- there was
# simply no strong emphasis to report, which is a normal, expected
# outcome for this one gate.
#
# A SECOND, LATER LIVE FAILURE at this SAME gate -- the FOURTH occurrence
# of this defect class overall (see _CURATE_GATE_NOTE below for the
# third): six eval arms died 2-3/20 sources in, every one on the same
# shape of message -- "proxy gave up on gate 'takeaways_gate'" on a source
# the model judged off-topic for the wiki (Chinese-AI-model business/
# capacity news landing in an AI-engineering-blog wiki), followed by
# "Error at takeaways_gate (no_matching_edge)": this gate has no give_up
# edge in pipeline/ingest.dot, so the give_up didn't just skip the
# source -- it crashed the run. The model's off-topic JUDGMENT was
# correct; the CONCLUSION it drew from that judgment was out of scope for
# THIS gate. curate_gate (below) -- the ONE gate that could actually
# decline a source -- was retired from this pipeline after measuring
# -1.9pp (see SITE-EVALUATION.md); nothing downstream of it declines a
# source anymore, so takeaways_gate has never had the authority to keep a
# source out, and give_up here doesn't even achieve that -- it only
# crashes the run; the source is still sitting there next time. Two
# independent, unrelated changes made this failure MORE likely without
# creating it: the "structure across the WHOLE source" brief line added
# below, and a hardened direction file -- both simply hand the model MORE
# evidence a source is off-topic, so it refuses more confidently on
# exactly the sources this gate was never asked to gatekeep.
#
# THE FIX: being off-topic is itself useful information for the STEER
# (reject the catalog's predicted merge targets for this source, don't
# force an entity-level tie to a page it doesn't belong on, write the
# source's own page and stop there) -- never grounds to give_up. Still
# scoped to takeaways_gate ONLY, same reasoning as the fix above: a
# missing/stale brief refuses upstream before this backend is ever
# called, so an off-topic judgment reaching here can only be a real
# judgment about a well-formed brief, never a broken one.
#
# THE FIX is scoped to takeaways_gate ONLY (stage-checked below), not
# generalized to every freeform gate: by the time this backend is called
# for takeaways_gate, ProxyInterviewer has ALREADY verified the brief is
# present, fresh, and non-empty (a missing/stale brief refuses before
# ever reaching here -- see proxy_interviewer.py's
# ``_is_takeaways_gate_stage`` branch). So "the brief doesn't suggest a
# strong emphasis" (or "this source looks off-topic") can only mean
# "genuinely nothing to steer" / "genuinely off-topic," never "the brief
# itself is broken" -- that second condition already fails loud,
# upstream, before a backend call is ever made. Missing/stale briefs are
# UNCHANGED by this fix.
_TAKEAWAYS_GATE_STAGE = "takeaways_gate"


def _is_takeaways_gate_stage(stage: str) -> bool:
    return _stage_matches(stage, _TAKEAWAYS_GATE_STAGE)


_TAKEAWAYS_GATE_NOTE = (
    "=== NOTE FOR THIS GATE (takeaways_gate) ===\n"
    "This gate's brief is guaranteed present, fresh, and non-empty for this source -- a\n"
    "missing or stale brief already refused before your turn, so ground rule 2's \"not\n"
    "confident -> give_up\" is NOT the right response to a brief that simply doesn't point\n"
    "at a strong emphasis. That is a normal, expected result here, not a reason to give up:\n"
    'if the persona\'s own takeaways-discussion guidance tells you to answer "no specific emphasis" in that situation, do exactly that -- action="answer", answer_text="no specific emphasis", grounding citing that persona instruction. Reserve give_up for\n'
    "this gate for when the persona does not address takeaways steering at all.\n"
    "\n"
    "THE BRIEF NOW CARRIES TWO SEPARATE SIGNALS ABOUT THIS SOURCE, READ THEM AS SUCH:\n"
    '  1. "What the OPENING looks like it\'s about" -- a deterministic gist of ONLY the\n'
    "     source's opening substantive prose. The opening is often representative, but not\n"
    "     always: it can be adoption-stat filler, scene-setting, or a hook that has nothing to\n"
    "     do with the source's actual contribution.\n"
    '  2. "Structure across the WHOLE source" -- every section heading and every bold\n'
    "     lead-in phrase found ANYWHERE in the source, in reading order. This line reflects\n"
    "     the entire source, not just its opening, and is frequently where the source's real,\n"
    "     specific argument actually lives (e.g. a numbered stage/section list, or a bolded\n"
    "     phrase the author used to flag their own key point mid-article).\n"
    "These two lines can disagree. When they do, DO NOT default to the opening just because it\n"
    "comes first -- the opening has no special authority here. Weigh the structure line as at\n"
    "least as informative as the opening gist, and when the structure line names something\n"
    "concrete and specific (a mechanism, a technique, a named risk or decision) that the opening\n"
    "never mentions, treat THAT as the emphasis worth flagging, not the opening's framing.\n"
    "\n"
    "A THIRD situation this gate must not give_up on: the gist and/or structure signals above\n"
    "may lead you to judge that this source is genuinely OFF-TOPIC for the wiki -- about a\n"
    "different subject than the wiki's established focus, with no real connection to what the\n"
    "corpus otherwise covers. This gate does NOT decide whether a source belongs (that gate,\n"
    "curate_gate, was retired from this pipeline -- nothing declines a source anymore, so\n"
    "whatever reaches this gate is already going to be woven in regardless of what you answer\n"
    "here). Judging a source off-topic is USEFUL INFORMATION FOR YOUR STEER, not a reason to\n"
    'give_up: action="answer", and use that judgment as the emphasis itself. Say plainly what\n'
    "the source actually is about and how that differs from the wiki's established focus;\n"
    "instruct the weave to REJECT the catalog's predicted merge targets for this source rather\n"
    "than force a connection to an existing page it doesn't belong on; and direct that the\n"
    "source get its own source-level page and stop there, without inventing an entity-level tie\n"
    "to unrelated existing pages just because the catalog predicted one. If this corpus later\n"
    "accumulates other sources on the same off-topic subject, that may earn its own concept\n"
    "page in time -- one source alone is not yet an argument for that. Reserve give_up for this\n"
    "\n"
    'A FOURTH signal may appear in the brief: a line starting "NEAR-DUPLICATE SIGNAL". This is\n'
    "a DETERMINISTIC, computed comparison (no model involved) against every source already\n"
    "ingested into this wiki, naming any prior source this one closely resembles and how similar\n"
    "(e.g. republished wire copy, a syndicated repost, the same piece crossposted under a\n"
    "different byline/date). It appears ONLY when a genuine near-duplicate was found -- its\n"
    "absence means the check ran and found nothing, not that the check didn't run. This is a\n"
    "computed RESEMBLANCE, not a decision: it does not tell you what to do with it, and it does\n"
    "not mean the source should be skipped or declined (nothing declines a source at this gate --\n"
    "see the off-topic case above). Use it to steer the weave AWAY FROM the specific mistake it\n"
    "exists to catch: do not let the flagged source count as a SECOND, independent source\n"
    "corroborating whatever the prior source already established -- a republished or syndicated\n"
    "copy is still just the ONE underlying source appearing twice, and citing it as though two\n"
    "authors independently converged on the same point would be citation padding, manufacturing\n"
    "the exact cross-source-convergence signal this wiki exists to detect. If the source genuinely\n"
    "adds something the prior one didn't (a correction, new data, additional context), instruct\n"
    "the weave to note that specifically rather than treat the whole source as fresh corroboration.\n"
    "This is still just information for your steer, not a reason to give_up.\n"
    "=== END NOTE ===\n\n"
)

_SYNTHESIS_GATE_STAGE = "synthesis_gate"


def _is_synthesis_gate_stage(stage: str) -> bool:
    return _stage_matches(stage, _SYNTHESIS_GATE_STAGE)


_SYNTHESIS_GATE_NOTE = (
    "=== NOTE FOR THIS GATE (synthesis_gate) ===\n"
    "This gate's brief lists every candidate cross-source theme that survived detection,\n"
    "attribution, and threshold filtering for this pass -- a missing/unreadable brief already\n"
    "refused before your turn (this should not happen), so ground rule 2's \"not confident ->\n"
    'give_up" is NOT the right response to a brief that simply gives no strong reason to drop\n'
    'anything. That is the normal, expected case: respond action="answer",\n'
    'answer_text="KEEP ALL", grounding citing the persona\'s default toward keeping candidates,\n'
    "unless you can name a real, affirmative reason a SPECIFIC listed candidate should not get a\n"
    "page (too thin a claim, boilerplate-sounding, clearly redundant with an existing page) -- in\n"
    'that case answer with one line per such candidate, in the exact form "DROP: <term>",\n'
    "copying the term text EXACTLY as printed in the brief. Reserve give_up for this gate for\n"
    "when the brief itself is missing or unreadable.\n"
    "=== END NOTE ===\n\n"
)

# curate_gate is the THIRD occurrence of this exact defect class (see
# _TAKEAWAYS_GATE_NOTE above for the first, and delivery report for the
# live incident this one fixes: two eval runs halted at 2/20 sources with
# "curate_gate: give_up" -> "refused: interviewer returned SKIPPED", on a
# source the persona's own source-curation section already covers -- "If
# the brief leaves you genuinely unable to judge, choose Accept. In this
# gate the safe action is to let the source through, not to refuse." --
# but ground rule 2 ("not confident -> give_up") swallowed that
# instruction exactly as it swallowed takeaways_gate's, halting the whole
# pipeline on a source that had nothing WRONG with it, just a judgment
# call the persona already resolved.
#
# THE FIX is scoped to curate_gate ONLY, same reasoning as
# _TAKEAWAYS_GATE_NOTE above: by the time this backend is called for
# curate_gate, ProxyInterviewer has ALREADY verified the brief is present,
# fresh, and non-empty (see proxy_interviewer.py's
# ``_is_curate_gate_stage`` branch) -- a missing/stale brief refuses
# before ever reaching here, UNCHANGED by this fix. So "hard to judge"
# here can only mean "genuinely a close call," never "the brief itself is
# broken."
#
# Accept is the deliberately asymmetric default for THIS gate specifically
# (not a general "when uncertain, say yes" rule -- see
# _is_curate_gate_stage's docstring on why this is stage-scoped): a
# wrongly-declined source is signal lost permanently -- the pipeline never
# offers it again -- while a wrongly-accepted source only spends compute,
# and gets a second chance to be judged thin/redundant downstream at
# weave and synthesis (synthesis_gate's own "KEEP ALL" default above is
# the same asymmetry one stage later). give_up remains available for a
# curate_gate whose persona genuinely never addresses source curation at
# all -- that is still a real refusal condition, not eliminated by this
# note.
_CURATE_GATE_STAGE = "curate_gate"


def _is_curate_gate_stage(stage: str) -> bool:
    return _stage_matches(stage, _CURATE_GATE_STAGE)


_CURATE_GATE_NOTE = (
    "=== NOTE FOR THIS GATE (curate_gate) ===\n"
    "This gate's brief is guaranteed present, fresh, and non-empty for this source -- a\n"
    "missing or stale brief already refused before your turn, so ground rule 2's \"not\n"
    'confident -> give_up" is NOT the right response to a source that is simply hard to\n'
    "judge. Uncertainty is asymmetric at THIS gate specifically: a wrongly-declined source is\n"
    "signal lost permanently (it is never offered to this pipeline again), while a\n"
    "wrongly-accepted source only costs compute and gets a second chance to be judged thin,\n"
    "redundant, or off-topic downstream during weave and synthesis. If the persona's own\n"
    "source-curation guidance tells you to default toward Accept -- including for the case\n"
    'where it says something like "genuinely unable to judge, choose Accept" -- do exactly\n'
    'that: action="answer", choice_key for the Accept option, grounding citing that persona\n'
    "instruction. Reserve give_up for this gate for when the persona does not address source\n"
    "curation at all.\n"
    "=== END NOTE ===\n\n"
)

# The shared mechanism every gate-scoped override above plugs into. This is
# a TABLE walked in a loop, not a fourth copy-pasted if/elif branch in
# _build_prompt -- see the module docstring's "Third occurrence" framing
# and _stage_matches's docstring: two prior gates (takeaways_gate,
# synthesis_gate) with an identical shape, plus curate_gate needing the
# same treatment here and now, plus a plausible future fourth, crossed the
# threshold where copy-pasting a third near-identical predicate+branch
# stops paying for itself. Each entry's CONTENT still differs per gate
# (freeform "no specific emphasis" vs freeform "KEEP ALL" vs choice-gate
# "Accept") -- only the boilerplate of matching-and-selecting is shared.
# Adding a future gate here is one tuple entry, not a new branch.
_STAGE_OVERRIDE_NOTES: tuple[tuple[Callable[[str], bool], str], ...] = (
    (_is_takeaways_gate_stage, _TAKEAWAYS_GATE_NOTE),
    (_is_synthesis_gate_stage, _SYNTHESIS_GATE_NOTE),
    (_is_curate_gate_stage, _CURATE_GATE_NOTE),
)


def _stage_override_note(stage: str) -> str:
    """The give_up-override note for ``stage``, or ``\"\"`` for every gate
    not in ``_STAGE_OVERRIDE_NOTES`` (the vast majority -- e.g. review_gate,
    collect_guidance, file_back_gate keep ground rule 2 exactly as written,
    give_up included, because for THOSE gates "not confident" really does
    mean "stuck," not "the persona already told you what to do")."""
    for matches, note in _STAGE_OVERRIDE_NOTES:
        if matches(stage):
            return note
    return ""


@dataclass(frozen=True)
class GateQuestion:
    """Attractor's real ``Question`` (amplifier_module_loop_pipeline
    .interviewer), reduced to exactly what a proxy decision needs. See
    wiki_weaver.aitl.proxy_interviewer for the mapping from the engine's
    actual type.
    """

    text: str
    gate_type: str  # "freeform" | "choice"
    stage: str = ""
    choices: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (key, label)
    brief: str | None = None  # a gate-specific factual brief, e.g. review_gate's weave brief
    # (wiki_weaver.aitl.proxy_interviewer._load_review_brief); None for every
    # other gate today -- see that module's docstring for why this is not
    # generalized beyond review_gate.
    corrections: str | None = None  # standing lens/corrections/ text, EVERY gate --
    # see wiki_weaver.aitl.corrections's module docstring. Unlike `brief`
    # above, this is populated for every stage (not gate-specific) and its
    # absence (None) is a normal, common state (no correction has ever been
    # filed), never a refusal condition.

    @property
    def choice_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _label in self.choices)


@dataclass(frozen=True)
class ProxyDecision:
    """What a backend returns for one gate.

    ``action="give_up"`` is a first-class, EXPECTED outcome (docs/DESIGN.md
    Sec 7: "give_up is a first-class verdict. Distinguishes 'we're done'
    from 'we're stuck.'") -- never treat it as an error path.
    """

    action: str  # "answer" | "give_up"
    reason: str
    answer_text: str | None = None
    choice_key: str | None = None
    grounding: str | None = None


@runtime_checkable
class ProxyBackend(Protocol):
    async def decide(self, question: GateQuestion, persona_text: str) -> ProxyDecision: ...


def _build_prompt(question: GateQuestion, persona_text: str) -> str:
    if question.gate_type == "choice":
        options = "\n".join(f"  - key={key!r}: {label}" for key, label in question.choices)
        question_block = f"This is a MULTIPLE-CHOICE gate. Options:\n{options}\n\nQuestion: {question.text}"
    else:
        question_block = f"This is a FREEFORM gate -- answer in your own words.\n\nQuestion: {question.text}"

    # question.corrections carries standing, durable corrections from
    # lens/corrections/ (wiki_weaver.aitl.corrections) -- populated for
    # EVERY gate, unlike `brief` below. See that module's docstring for why
    # these outrank ad hoc judgment (the same precedence
    # docs/DESIGN.md \u00a74 gives `weave`) and why they are read from this
    # directory rather than folded into the persona document. Omitted
    # entirely (never an empty block) when there is nothing to load -- no
    # correction having ever been filed is a normal, common state.
    corrections_block = ""
    if question.corrections:
        corrections_block = (
            "=== STANDING CORRECTIONS (lens/corrections/ -- durable, previously "
            "filed by a human or a prior correction pass; these outrank ad hoc "
            "judgment whenever they bear on this gate) ===\n"
            f"{question.corrections}\n"
            "=== END STANDING CORRECTIONS ===\n\n"
        )

    # question.brief carries gate-specific factual context the persona alone
    # cannot supply -- e.g. review_gate's weave brief (what the pipeline
    # actually did for this source). None for every gate that doesn't set
    # one; the block is simply omitted rather than emitted empty.
    brief_block = ""
    if question.brief:
        brief_block = (
            "=== BRIEF (what actually happened for this gate -- ground your "
            "judgment in this, not a guess) ===\n"
            f"{question.brief}\n"
            "=== END BRIEF ===\n\n"
        )

    # Stage-scoped give_up override -- resolves the give_up-vs-"this is a
    # normal outcome for THIS gate" tension against ground rule 2 above.
    # See _STAGE_OVERRIDE_NOTES for the shared mechanism and why it is a
    # table, not an if/elif chain.
    stage_note = _stage_override_note(question.stage)

    return (
        "=== PERSONA (the character document -- your ONLY basis for answering) ===\n"
        f"{persona_text}\n"
        "=== END PERSONA ===\n\n"
        f"{corrections_block}"
        f"{brief_block}"
        f"{stage_note}"
        f"{question_block}\n"
    )


def _extract_json_object(raw: str) -> dict | None:
    """Defensively pull one JSON object out of a model response.

    Handles the bare-JSON case (the instructed format), a fenced ```json
    block (models do this anyway despite instructions), and -- as a last
    resort -- the first ``{...}`` substring. Never raises; returns ``None``
    on anything that doesn't parse, which the caller turns into
    ``action="give_up"`` (never a guess).
    """
    text = raw.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None

    return None


def _parse_decision(raw_text: str, question: GateQuestion) -> ProxyDecision:
    """Parse and validate a backend response -- the model cannot certify its
    own output's shape (same discipline as wiki_weaver.ingest.validate_plan
    and every other box-output validator in this codebase). Any defect here
    -- malformed JSON, an unknown action, an invalid choice, an empty or
    stub answer, or a missing grounding citation -- becomes ``give_up``,
    never a best-effort guess.
    """
    obj = _extract_json_object(raw_text)
    if not isinstance(obj, dict):
        return ProxyDecision(action="give_up", reason="backend response was not parseable JSON")

    action = obj.get("action")
    reason = str(obj.get("reason") or "").strip() or "(no reason given)"
    grounding_raw = obj.get("grounding")
    grounding = str(grounding_raw).strip() if grounding_raw else None

    if action == "give_up":
        return ProxyDecision(action="give_up", reason=reason, grounding=grounding)

    if action != "answer":
        return ProxyDecision(action="give_up", reason=f"backend returned unknown action {action!r}")

    if not grounding:
        # No citation into the persona -> nothing for a human to verify
        # later -> refuse rather than accept an ungrounded answer. This is
        # the auditability requirement enforced at the parse boundary, not
        # just documented as a hope.
        return ProxyDecision(action="give_up", reason="backend answered without citing grounding in the persona")

    if question.gate_type == "choice":
        choice_key = obj.get("choice_key")
        if not isinstance(choice_key, str) or choice_key not in question.choice_keys:
            return ProxyDecision(action="give_up", reason=f"backend chose an invalid option {choice_key!r}")
        return ProxyDecision(action="answer", reason=reason, choice_key=choice_key, grounding=grounding)

    answer_raw = obj.get("answer_text")
    answer_text = str(answer_raw).strip() if answer_raw else ""
    if not answer_text or answer_text == "auto-approved":
        return ProxyDecision(action="give_up", reason="backend returned an empty or stub answer")
    return ProxyDecision(action="answer", reason=reason, answer_text=answer_text, grounding=grounding)


class LLMProxyBackend:
    """Production ``ProxyBackend``: one tool-free ``unified_llm.generate()``
    call per gate. See module docstring for why this deliberately avoids
    the pipeline's own box/agent machinery.
    """

    def __init__(self, model: str = DEFAULT_MODEL, client: object | None = None) -> None:
        self._model = model
        self._client = client  # optional unified_llm.Client override (dependency injection)

    async def decide(self, question: GateQuestion, persona_text: str) -> ProxyDecision:
        try:
            from unified_llm import generate
        except ImportError as exc:
            return ProxyDecision(
                action="give_up",
                reason=f"unified_llm is not importable in this environment: {exc}",
            )

        prompt = _build_prompt(question, persona_text)
        try:
            result = await generate(
                model=self._model,
                prompt=prompt,
                system=_SYSTEM_PROMPT,
                max_tokens=600,
                client=self._client,  # type: ignore[arg-type]
            )
        except Exception as exc:  # noqa: BLE001 -- any backend failure is a give_up, never a guess
            return ProxyDecision(action="give_up", reason=f"backend call failed: {type(exc).__name__}: {exc}")

        return _parse_decision(result.text, question)
