"""wiki_weaver.aitl.backend -- decision parsing is deterministic and
network-free (the LLM call itself is never exercised here; only the
response-parsing/validation boundary, which is where "never invent an
answer" is actually enforced).
"""

from __future__ import annotations

import json

from wiki_weaver.aitl.backend import (
    GateQuestion,
    _build_prompt,
    _extract_json_object,
    _is_curate_gate_stage,
    _is_takeaways_gate_stage,
    _parse_decision,
)

FREEFORM_Q = GateQuestion(text="Describe the correction.", gate_type="freeform", stage="intake")
CHOICE_Q = GateQuestion(
    text="Approve reprocess?",
    gate_type="choice",
    stage="scope_gate",
    choices=(("A", "[A] Approve"), ("B", "[B] Skip")),
)


def _resp(obj: dict) -> str:
    return json.dumps(obj)


def test_answer_freeform_requires_grounding():
    raw = _resp({"action": "answer", "answer_text": "Alice led it.", "reason": "matches persona"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"
    assert "grounding" in decision.reason


def test_answer_freeform_with_grounding_succeeds():
    raw = _resp(
        {
            "action": "answer",
            "answer_text": "Alice led it.",
            "reason": "matches persona",
            "grounding": "Identity section names Alice as lead",
        }
    )

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "answer"
    assert decision.answer_text == "Alice led it."
    assert decision.grounding == "Identity section names Alice as lead"


def test_give_up_action_is_passed_through():
    raw = _resp({"action": "give_up", "reason": "persona says nothing about this"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"
    assert decision.reason == "persona says nothing about this"


def test_malformed_json_is_give_up():
    decision = _parse_decision("not json at all {{{", FREEFORM_Q)

    assert decision.action == "give_up"


def test_non_object_json_is_give_up():
    decision = _parse_decision("[1, 2, 3]", FREEFORM_Q)

    assert decision.action == "give_up"


def test_stub_answer_text_is_give_up():
    raw = _resp({"action": "answer", "answer_text": "auto-approved", "reason": "x", "grounding": "y"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"


def test_empty_answer_text_is_give_up():
    raw = _resp({"action": "answer", "answer_text": "   ", "reason": "x", "grounding": "y"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"


def test_choice_gate_valid_key_succeeds():
    raw = _resp({"action": "answer", "choice_key": "A", "reason": "x", "grounding": "y"})

    decision = _parse_decision(raw, CHOICE_Q)

    assert decision.action == "answer"
    assert decision.choice_key == "A"


def test_choice_gate_invalid_key_is_give_up():
    raw = _resp({"action": "answer", "choice_key": "Z", "reason": "x", "grounding": "y"})

    decision = _parse_decision(raw, CHOICE_Q)

    assert decision.action == "give_up"


def test_choice_gate_missing_key_is_give_up():
    raw = _resp({"action": "answer", "reason": "x", "grounding": "y"})

    decision = _parse_decision(raw, CHOICE_Q)

    assert decision.action == "give_up"


def test_unknown_action_is_give_up():
    raw = _resp({"action": "maybe", "reason": "x"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"


def test_missing_action_is_give_up():
    raw = _resp({"reason": "x"})

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"


def test_fenced_json_is_parsed():
    raw = "```json\n" + _resp({"action": "give_up", "reason": "fenced"}) + "\n```"

    decision = _parse_decision(raw, FREEFORM_Q)

    assert decision.action == "give_up"
    assert decision.reason == "fenced"


def test_extract_json_object_finds_embedded_object_as_last_resort():
    raw = "Sure, here you go:\n" + json.dumps({"action": "give_up", "reason": "embedded"}) + "\nHope that helps!"

    obj = _extract_json_object(raw)

    assert obj is not None
    assert obj["action"] == "give_up"


def test_extract_json_object_returns_none_for_garbage():
    assert _extract_json_object("no json here at all") is None


def test_build_prompt_omits_brief_block_when_absent():
    """Every gate but review_gate today -- GateQuestion.brief defaults to
    None, and the prompt must not grow a stray empty '=== BRIEF ===' block
    for them."""
    prompt = _build_prompt(FREEFORM_Q, "persona text")

    assert "BRIEF" not in prompt


def test_build_prompt_includes_brief_block_when_present():
    """review_gate's whole point: the weave brief must actually reach the
    prompt the model sees, verbatim, not just live on the GateQuestion."""
    q = GateQuestion(
        text="review_gate",
        gate_type="choice",
        stage="review_gate",
        choices=(("A", "[A] Accept"), ("B", "[B] Guide"), ("C", "[C] Skip")),
        brief="Source: s1.txt (kind: article, segment 1/1)\nPages created (1): onnx-runtime.md",
    )

    prompt = _build_prompt(q, "persona text")

    assert "BRIEF" in prompt
    assert "Pages created (1): onnx-runtime.md" in prompt
    # Brief must appear before the question itself -- the model reads
    # persona -> brief -> question, in that order.
    assert prompt.index("Pages created") < prompt.index("Question:")


# -- takeaways_gate: no-emphasis-vs-give_up note (see module docstring's
# LIVE FAILURE) -- the stage-scoped note that resolves the give_up-vs-
# "no specific emphasis" conflict, WITHOUT touching any other gate.


def test_is_takeaways_gate_stage_matches_bare_and_reentrant_suffix():
    assert _is_takeaways_gate_stage("takeaways_gate")
    assert _is_takeaways_gate_stage("takeaways_gate-2")
    assert not _is_takeaways_gate_stage("takeaways_gate_other")
    assert not _is_takeaways_gate_stage("review_gate")
    assert not _is_takeaways_gate_stage("collect_guidance")


def test_build_prompt_includes_no_emphasis_note_for_takeaways_gate():
    q = GateQuestion(
        text="Say what to emphasize.",
        gate_type="freeform",
        stage="takeaways_gate",
        brief="Source: s1.txt\nWhat it looks like it's about: real content",
    )

    prompt = _build_prompt(q, "persona text")

    assert "no specific emphasis" in prompt.lower()
    assert "NOTE FOR THIS GATE" in prompt
    # The note must land AFTER the brief and BEFORE the question -- the
    # model reads persona -> brief -> this note -> question, in that order.
    assert prompt.index("NOTE FOR THIS GATE") > prompt.index("real content")
    assert prompt.index("NOTE FOR THIS GATE") < prompt.index("Question:")


def test_build_prompt_omits_no_emphasis_note_for_other_gates():
    """The give_up-vs-answer resolution is takeaways_gate-ONLY -- e.g.
    collect_guidance must keep give_up as the correct response to "nothing
    to steer" (see persist_guidance.py's docstring: answering there when
    nobody asked to steer is nonsensical), so it must never see this note.
    """
    q = GateQuestion(text="Describe what weave should do differently.", gate_type="freeform", stage="collect_guidance")

    prompt = _build_prompt(q, "persona text")

    assert "NOTE FOR THIS GATE" not in prompt
    assert "no specific emphasis" not in prompt.lower()


def test_build_prompt_includes_no_emphasis_note_on_reentrant_takeaways_stage():
    q = GateQuestion(text="Say what to emphasize.", gate_type="freeform", stage="takeaways_gate-2")

    prompt = _build_prompt(q, "persona text")

    assert "NOTE FOR THIS GATE" in prompt


# -- takeaways_gate: off-topic-source-is-a-steer-not-a-refusal (see module
# docstring's "SECOND, LATER LIVE FAILURE" -- fourth occurrence of the
# defect class overall) -- six eval arms died 2-3/20 sources in because the
# proxy chose give_up on a source it correctly judged off-topic, and
# takeaways_gate has no give_up edge in pipeline/ingest.dot, so the run
# crashed rather than merely skipping a step.


def test_build_prompt_includes_off_topic_steer_note_for_takeaways_gate():
    q = GateQuestion(
        text="Say what to emphasize.",
        gate_type="freeform",
        stage="takeaways_gate",
        brief=(
            "Source: s003.txt\nWhat the OPENING looks like it's about: GPU capacity and market dynamics for Kimi K3"
        ),
    )

    prompt = _build_prompt(q, "persona text")

    assert "OFF-TOPIC" in prompt
    assert "REJECT" in prompt
    assert "predicted merge targets" in prompt
    assert "NOTE FOR THIS GATE" in prompt
    # Same ordering discipline as the no-emphasis note: brief -> note -> question.
    assert prompt.index("OFF-TOPIC") > prompt.index("Kimi K3")
    assert prompt.index("OFF-TOPIC") < prompt.index("Question:")


def test_off_topic_steer_answer_parses_as_a_real_answer_not_give_up():
    """Positive proof the parsing layer never blocks this: a well-formed,
    grounded steer answer that rejects the predicted merge targets and asks
    for a source-level page only is a normal ``action="answer"``, never
    swallowed by any parse-layer guard -- the LIVE bug this fixes is a MODEL
    decision (give_up chosen over a steer), not a parsing defect."""
    q = GateQuestion(text="Say what to emphasize.", gate_type="freeform", stage="takeaways_gate")
    raw = _resp(
        {
            "action": "answer",
            "answer_text": (
                "Reject the predicted merge targets -- this is capacity/business news about a "
                "model launch, not an argument about engineering practice. Do NOT force a "
                "connection to unrelated existing pages. Write the source page and stop there."
            ),
            "reason": "source is genuinely off-topic for this wiki",
            "grounding": "takeaways_gate note: off-topic judgment is useful information for the steer, not a reason to give_up",
        }
    )

    decision = _parse_decision(raw, q)

    assert decision.action == "answer"
    assert "Reject the predicted merge targets" in (decision.answer_text or "")


# -- standing corrections (lens/corrections/, EVERY gate) -------------------
#
# THE feedback-loop fix: unlike `brief` (review_gate/collect_guidance/
# takeaways_gate only), `corrections` is populated for every gate stage --
# see wiki_weaver.aitl.corrections's module docstring for why.


def test_build_prompt_omits_corrections_block_when_absent():
    """No correction has ever been filed for this wiki -- the common case;
    the prompt must not grow a stray empty corrections block for it."""
    prompt = _build_prompt(FREEFORM_Q, "persona text")

    assert "STANDING CORRECTIONS" not in prompt


def test_build_prompt_includes_corrections_block_when_present():
    q = GateQuestion(
        text="Approve reprocess?",
        gate_type="choice",
        stage="scope_gate",
        choices=(("A", "[A] Approve"), ("B", "[B] Skip")),
        corrections="Alice, not Bob, led the migration effort.",
    )

    prompt = _build_prompt(q, "persona text")

    assert "STANDING CORRECTIONS" in prompt
    assert "Alice, not Bob, led the migration effort." in prompt
    # Corrections must land before the question -- durable standing facts
    # inform the decision, not an afterthought appended after it.
    assert prompt.index("Alice, not Bob") < prompt.index("Question:")


def test_build_prompt_includes_both_corrections_and_brief_in_persona_corrections_brief_order():
    q = GateQuestion(
        text="review_gate",
        gate_type="choice",
        stage="review_gate",
        choices=(("A", "[A] Accept"), ("B", "[B] Guide"), ("C", "[C] Skip")),
        brief="Source: s1.txt\nPages updated (1): migration-history.md",
        corrections="Alice, not Bob, led the migration effort.",
    )

    prompt = _build_prompt(q, "persona text")

    assert prompt.index("=== PERSONA") < prompt.index("STANDING CORRECTIONS")
    assert prompt.index("STANDING CORRECTIONS") < prompt.index("=== BRIEF")
    assert prompt.index("=== BRIEF") < prompt.index("Question:")


# -- curate_gate: the give_up-vs-Accept-under-uncertainty note (see module
# docstring's "Third occurrence" LIVE FAILURE) -- the stage-scoped note that
# resolves the give_up-vs-"safe action is Accept" conflict, WITHOUT touching
# any other gate. Mirrors the takeaways_gate tests above -- same mechanism,
# now proven against a THIRD gate.


def test_is_curate_gate_stage_matches_bare_and_reentrant_suffix():
    assert _is_curate_gate_stage("curate_gate")
    assert _is_curate_gate_stage("curate_gate-2")
    assert not _is_curate_gate_stage("curate_gate_other")
    assert not _is_curate_gate_stage("review_gate")
    assert not _is_curate_gate_stage("takeaways_gate")


CURATE_CHOICE_Q = GateQuestion(
    text="curate_gate",
    gate_type="choice",
    stage="curate_gate",
    choices=(("A", "[A] Accept"), ("B", "[B] Decline")),
    brief="Candidate source: s003.txt\nWhat it appears to be about: GPU capacity/demand for a Chinese AI model",
)


def test_build_prompt_includes_accept_note_for_curate_gate():
    prompt = _build_prompt(CURATE_CHOICE_Q, "persona text")

    assert "accept" in prompt.lower()
    assert "NOTE FOR THIS GATE" in prompt
    # The note must land AFTER the brief and BEFORE the question -- same
    # persona -> brief -> note -> question ordering as every other gate.
    assert prompt.index("NOTE FOR THIS GATE") > prompt.index("GPU capacity")
    assert prompt.index("NOTE FOR THIS GATE") < prompt.index("Question:")


def test_build_prompt_omits_accept_note_for_other_gates():
    """The give_up-override is curate_gate-ONLY (among choice gates) -- e.g.
    review_gate must keep ground rule 2 exactly as written; a genuinely
    unjudgeable weave brief there is still a real refusal condition, not
    silently converted to an Accept."""
    q = GateQuestion(
        text="review_gate",
        gate_type="choice",
        stage="review_gate",
        choices=(("A", "[A] Accept"), ("B", "[B] Guide"), ("C", "[C] Skip")),
        brief="Source: s1.txt\nPages created (1): onnx-runtime.md",
    )

    prompt = _build_prompt(q, "persona text")

    assert "NOTE FOR THIS GATE" not in prompt


def test_build_prompt_includes_accept_note_on_reentrant_curate_stage():
    q = GateQuestion(
        text="curate_gate",
        gate_type="choice",
        stage="curate_gate-2",
        choices=(("A", "[A] Accept"), ("B", "[B] Decline")),
    )

    prompt = _build_prompt(q, "persona text")

    assert "NOTE FOR THIS GATE" in prompt


def test_curate_gate_accept_choice_answer_parses_as_a_real_answer_not_give_up():
    """Positive proof the parsing layer never blocks this: a well-formed
    choice_key='A' answer, grounded in the persona's own Accept-under-
    uncertainty instruction, is a normal ``action="answer"``, not swallowed
    by any parse-layer guard -- the LIVE bug this fixes is a MODEL decision
    (give_up chosen over Accept), not a parsing defect, so this test proves
    the parsing side was never the obstacle."""
    raw = _resp(
        {
            "action": "answer",
            "choice_key": "A",
            "reason": "brief leaves this genuinely hard to judge; persona defaults to Accept",
            "grounding": "Source curation: 'If the brief leaves you genuinely unable to judge, choose Accept.'",
        }
    )

    decision = _parse_decision(raw, CURATE_CHOICE_Q)

    assert decision.action == "answer"
    assert decision.choice_key == "A"


def test_no_specific_emphasis_answer_parses_as_a_real_answer_not_give_up():
    """Positive proof the parsing layer never blocks this: a well-formed
    answer_text of exactly the persona's own canonical phrase is a normal
    ``action="answer"``, not swallowed by the stub/empty-answer guard (see
    _parse_decision's ``auto-approved``/empty-string check above)."""
    q = GateQuestion(text="Say what to emphasize.", gate_type="freeform", stage="takeaways_gate")
    raw = _resp(
        {
            "action": "answer",
            "answer_text": "no specific emphasis",
            "reason": "brief gives nothing to steer",
            "grounding": "Pre-weave takeaways discussion: say 'no specific emphasis' rather than inventing one",
        }
    )

    decision = _parse_decision(raw, q)

    assert decision.action == "answer"
    assert decision.answer_text == "no specific emphasis"
