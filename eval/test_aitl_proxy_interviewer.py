"""ProxyInterviewer -- fails loud (never invents) when the persona is
missing/incomplete, when the backend gives up, or when the backend errors;
answers in-character and records an audit trail otherwise.

No network, no real LLM call anywhere in this file -- FakeBackend/
ErrorBackend stand in for wiki_weaver.aitl.backend.LLMProxyBackend.
"""

from __future__ import annotations

import json

from amplifier_module_loop_pipeline.interviewer import AnswerValue, Option, Question, QuestionType

from wiki_weaver.aitl.audit import gate_decisions_path
from wiki_weaver.aitl.backend import GateQuestion, ProxyDecision
from wiki_weaver.aitl.proxy_interviewer import ProxyInterviewer
from wiki_weaver.lib import WikiRoot, ensure_dir

_VALID_PERSONA = (
    "## Identity\nStands in for the team.\n\n"
    "## Capability floor\nMay decide routine placement.\n\n"
    "## Hard constraints\nNever invent facts.\n\n"
    "## Refusal script\nDecline if ungrounded.\n\n"
    '## What "done" means\nMust cite this document.\n'
)


class FakeBackend:
    """Stands in for LLMProxyBackend -- returns a fixed decision, records
    every call it received so tests can assert the deterministic pre-check
    ran (or didn't run) the backend."""

    def __init__(self, decision: ProxyDecision) -> None:
        self.decision = decision
        self.calls: list[GateQuestion] = []

    async def decide(self, question: GateQuestion, persona_text: str) -> ProxyDecision:
        self.calls.append(question)
        return self.decision


class ErrorBackend:
    async def decide(self, question: GateQuestion, persona_text: str) -> ProxyDecision:
        raise RuntimeError("network exploded")


def _install_persona(wiki_root, text: str = _VALID_PERSONA) -> None:
    lens = wiki_root / "lens"
    lens.mkdir(parents=True, exist_ok=True)
    (lens / "persona.md").write_text(text, encoding="utf-8")


def _freeform_question(stage: str = "intake") -> Question:
    return Question(text="Describe the correction.", type=QuestionType.FREEFORM, stage=stage)


def _choice_question(stage: str = "scope_gate") -> Question:
    return Question(
        text="Approve reprocess?",
        type=QuestionType.MULTIPLE_CHOICE,
        options=[Option(key="A", label="[A] Approve"), Option(key="B", label="[B] Skip")],
        stage=stage,
    )


def _audit_entries(wiki_root) -> list[dict]:
    path = gate_decisions_path(wiki_root)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_missing_persona_fails_loud_without_calling_backend(tmp_path):
    backend = FakeBackend(ProxyDecision(action="answer", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_freeform_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []  # the deterministic pre-check must run BEFORE any backend call
    entries = _audit_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_structurally_incomplete_persona_fails_loud(tmp_path):
    """The 'does not COVER the gate' case at the persona-structure layer --
    an incomplete character document is refused before the backend is ever
    asked to reason from it."""
    _install_persona(tmp_path, text="## Identity\nOnly one section.\n")
    backend = FakeBackend(ProxyDecision(action="answer", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_freeform_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    assert _audit_entries(tmp_path)[0]["decision"] == "refused"


def test_backend_give_up_fails_loud_and_is_audited(tmp_path):
    """The 'persona exists but does not cover THIS gate' case -- only the
    backend (a real judgment call) can decide this, per-question."""
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="give_up", reason="persona does not cover this"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_freeform_question(stage="intake-1"))

    assert answer.value == AnswerValue.SKIPPED
    assert len(backend.calls) == 1
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "give_up"
    assert entries[0]["reason"] == "persona does not cover this"


def test_backend_error_fails_loud(tmp_path):
    _install_persona(tmp_path)
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=ErrorBackend())

    answer = interviewer.ask(_freeform_question())

    assert answer.value == AnswerValue.SKIPPED
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "error"
    assert "network exploded" in entries[0]["reason"]


def test_answers_freeform_gate_in_character(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(
        ProxyDecision(
            action="answer",
            answer_text="Alice led the migration.",
            reason="matches identity",
            grounding="Identity section",
        )
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_freeform_question(stage="intake-2"))

    assert answer.value == "Alice led the migration."
    assert answer.text == "Alice led the migration."
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["answer_text"] == "Alice led the migration."
    assert entries[0]["grounding"] == "Identity section"
    assert entries[0]["persona_digest"] is not None
    assert entries[0]["mode"] == "proxy"


def test_self_audits_marker_is_set():
    """wiki_weaver.aitl.dispatch.AuditingInterviewer checks this attribute
    to avoid double-logging a gate answered by --gate-mode=proxy."""
    assert ProxyInterviewer._SELF_AUDITS is True


def test_answers_choice_gate_with_valid_option(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(
        ProxyDecision(action="answer", choice_key="B", reason="scope too broad", grounding="Capability floor")
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_choice_question())

    assert answer.value == "B"
    assert answer.selected_option is not None
    assert answer.selected_option.label == "[B] Skip"
    entries = _audit_entries(tmp_path)
    assert entries[0]["choice_key"] == "B"
    assert entries[0]["choice_label"] == "[B] Skip"


def test_ask_multiple_delegates_to_ask_and_records_each(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="ok", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answers = interviewer.ask_multiple([_freeform_question("s1"), _freeform_question("s2")])

    assert len(answers) == 2
    assert len(backend.calls) == 2
    assert len(_audit_entries(tmp_path)) == 2


def test_inform_does_not_raise(tmp_path):
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=FakeBackend(ProxyDecision(action="give_up", reason="")))

    interviewer.inform("just letting you know")  # must not raise


# -- review_gate: the weave-brief delivery fix ------------------------------
#
# review_gate is the one stage where this proxy needs more than persona +
# label -- it needs review_brief_file (wiki_weaver.ingest.review_brief's
# output), verified fresh against current_source.txt. Every other stage
# (freeform/choice alike) is untouched by this -- see the tests above, none
# of which write a review-brief.json and all of which still pass.


def _review_gate_question() -> Question:
    return Question(
        text="review_gate",
        type=QuestionType.MULTIPLE_CHOICE,
        options=[
            Option(key="A", label="[A] Accept"),
            Option(key="B", label="[B] Guide"),
            Option(key="C", label="[C] Skip"),
        ],
        stage="review_gate",
    )


def _seed_current_source(wiki_root, source_id: str = "s1.txt") -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(source_id, encoding="utf-8")


def _write_review_brief(wiki_root, source_id: str, brief: str = "Source: s1.txt\nPages created (0): none") -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.review_brief_file.write_text(
        json.dumps({"source_id": source_id, "stage": "review_gate", "brief": brief}),
        encoding="utf-8",
    )


def test_review_gate_missing_brief_file_refuses_without_calling_backend(tmp_path):
    """No review-brief.json at all (prepare_review_brief never ran, or ran
    before this fix landed) -- the proxy must refuse, not fall through to
    judging from the bare six-word label (the exact bug this fixes)."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_review_gate_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []  # refused BEFORE any backend call, same as the persona pre-check
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_review_gate_stale_brief_refuses_without_calling_backend(tmp_path):
    """review-brief.json exists but was written for a DIFFERENT source than
    current_source.txt names right now -- a stale brief read at the wrong
    gate is worse than no brief (a confident wrong decision instead of an
    honest refusal); must refuse, never trust it."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s2-the-current-source.txt")
    _write_review_brief(tmp_path, source_id="s1-a-previous-source.txt")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_review_gate_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "stale" in entries[0]["reason"]


def test_review_gate_fresh_brief_reaches_backend_and_answers(tmp_path):
    """THE fix, positively verified: a fresh, matching brief is attached to
    the GateQuestion the backend actually receives (not dropped on the
    floor), and the proxy returns a real choice grounded in it."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_review_brief(tmp_path, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")
    backend = FakeBackend(
        ProxyDecision(action="answer", choice_key="A", reason="new page looks right", grounding="Capability floor")
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_review_gate_question())

    assert answer.value == "A"
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "new-page.md" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["choice_key"] == "A"


def test_review_gate_reentrant_stage_suffix_still_reads_brief(tmp_path):
    """A [B] Guide -> re-weave -> back-to-review_gate loop produces
    'review_gate-2' (HumanGateHandler._get_stage_id) -- still the same gate,
    still needs the (freshly rewritten) brief."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_review_brief(tmp_path, source_id="s1.txt", brief="Source: s1.txt\nsecond pass")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)
    question = _review_gate_question()
    question.stage = "review_gate-2"

    answer = interviewer.ask(question)

    assert answer.value == "A"
    assert backend.calls[0].brief == "Source: s1.txt\nsecond pass"


def test_non_review_gate_stage_unaffected_by_missing_brief_file(tmp_path):
    """A choice gate that ISN'T review_gate must behave exactly as before
    this fix -- no brief file check at all, backend called with brief=None."""
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="B", reason="scope too broad", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_choice_question())  # stage="scope_gate"

    assert answer.value == "B"
    assert backend.calls[0].brief is None


# -- collect_guidance: the [B] Guide steering-context delivery fix ----------
#
# THE DEFECT (see delivery report): collect_guidance carried NOTHING -- the
# same vacuum review_gate had before the weave-brief fix above. In a live
# run, both times the proxy chose [B] Guide it then hit collect_guidance
# cold and (correctly) refused, crashing the pipeline. guidance_brief.py
# now writes guidance-brief.json moments earlier in the same pass; these
# tests verify the proxy reads and trusts (or refuses) it exactly like
# review_gate's own weave brief.


def _guidance_question() -> Question:
    return Question(
        text="Describe what weave should do differently.", type=QuestionType.FREEFORM, stage="collect_guidance"
    )


def _write_guidance_brief(wiki_root, source_id: str, brief: str = "Source: s1.txt\nPages created (0): none") -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.guidance_brief_file.write_text(
        json.dumps({"source_id": source_id, "stage": "collect_guidance", "brief": brief}),
        encoding="utf-8",
    )


def test_collect_guidance_missing_brief_file_refuses_without_calling_backend(tmp_path):
    """No guidance-brief.json at all -- the proxy must refuse, not fall
    through to fabricating steering text from the bare node label (the
    exact defect this fixes: 0-for-2 live Guide attempts crashed the
    pipeline instead of refusing gracefully via this path)."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="should never be used", reason="r"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_guidance_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_collect_guidance_stale_brief_refuses_without_calling_backend(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s2-current.txt")
    _write_guidance_brief(tmp_path, source_id="s1-previous.txt")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="should never be used", reason="r"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_guidance_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "stale" in entries[0]["reason"]


def test_collect_guidance_fresh_brief_reaches_backend_and_answers(tmp_path):
    """THE fix, positively verified: a fresh, matching guidance brief is
    attached to the GateQuestion the backend actually receives, and the
    proxy produces REAL steering text grounded in it -- this is the
    acceptance evidence for the fix (a real answer, not a refusal)."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_guidance_brief(
        tmp_path,
        source_id="s1.txt",
        brief="Source: s1.txt\nPages created (1): onnx-runtime.md\nvalidation issue: broken link",
    )
    backend = FakeBackend(
        ProxyDecision(
            action="answer",
            answer_text="Fix the broken wikilink in onnx-runtime.md before re-weaving; keep everything else.",
            reason="grounded in the brief's validation issue",
            grounding="Capability floor",
        )
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_guidance_question())

    assert answer.value == "Fix the broken wikilink in onnx-runtime.md before re-weaving; keep everything else."
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "onnx-runtime.md" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert (
        entries[0]["answer_text"]
        == "Fix the broken wikilink in onnx-runtime.md before re-weaving; keep everything else."
    )


# -- takeaways_gate: refuse-on-missing-brief vs no-emphasis-is-an-answer ---
#
# THE TWO DISTINCT OUTCOMES THE TASK BRIEF REQUIRES STAY SEPARATE:
#   1. A missing or stale takeaways-brief.json is a BROKEN CONTRACT -> the
#      proxy refuses (SKIPPED), exactly like review_gate/collect_guidance
#      above -- UNCHANGED and NOT weakened by the fix below.
#   2. A present, well-formed brief that simply doesn't warrant a steer is
#      a LEGITIMATE outcome -> the backend answers "no specific emphasis"
#      (a real ``action="answer"``, per backend.py's takeaways_gate-only
#      note) and the weave proceeds unsteered -- this must NOT collapse
#      into a give_up/refusal (the live defect this fixes: every
#      evaluation run died at zero sources ingested because it did).


def _takeaways_question(stage: str = "takeaways_gate") -> Question:
    return Question(
        text="Say what to emphasize before this source is woven.",
        type=QuestionType.FREEFORM,
        stage=stage,
    )


def _write_takeaways_brief(
    wiki_root, source_id: str, brief: str = "Source: s1.txt\nWhat it's about: real content"
) -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.takeaways_brief_file.write_text(
        json.dumps({"source_id": source_id, "stage": "takeaways_gate", "brief": brief}),
        encoding="utf-8",
    )


def test_takeaways_gate_missing_brief_file_refuses_without_calling_backend(tmp_path):
    """No takeaways-brief.json at all -- a broken contract, refused BEFORE
    any backend call, never a fall-through to judging from the bare
    question text (same discipline as review_gate/collect_guidance)."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="should never be used", reason="r"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_takeaways_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_takeaways_gate_stale_brief_refuses_without_calling_backend(tmp_path):
    """A brief written for a DIFFERENT (previous) source is worse than no
    brief -- refuse, never trust it, exactly like review_gate/
    collect_guidance's identical staleness contract."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s2-current.txt")
    _write_takeaways_brief(tmp_path, source_id="s1-previous.txt")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="should never be used", reason="r"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_takeaways_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "stale" in entries[0]["reason"]


def test_takeaways_gate_no_specific_emphasis_is_answered_not_refused(tmp_path):
    """THE FIX, positively verified: a fresh, well-formed brief that gives
    the backend nothing to steer on results in a real ANSWER ("no specific
    emphasis"), not a give_up/refusal -- the weave proceeds unsteered
    instead of the whole pipeline halting on a source with nothing wrong."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_takeaways_brief(tmp_path, source_id="s1.txt")
    backend = FakeBackend(
        ProxyDecision(
            action="answer",
            answer_text="no specific emphasis",
            reason="brief gives nothing concrete to steer",
            grounding="Pre-weave takeaways discussion: say 'no specific emphasis' rather than inventing one",
        )
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_takeaways_question())

    assert answer.value == "no specific emphasis"
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "real content" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["answer_text"] == "no specific emphasis"


def test_takeaways_gate_off_topic_source_is_answered_not_refused(tmp_path):
    """THE FIX, positively verified (fourth occurrence of this defect class --
    see backend.py's module docstring "SECOND, LATER LIVE FAILURE"): a fresh,
    well-formed brief for a source the backend judges OFF-TOPIC for the wiki
    still results in a real ANSWER (a steer that rejects the predicted merge
    targets and asks for a source-level page only), not a give_up/refusal.
    The live defect this fixes crashed six eval arms at 2-3/20 sources
    ('Error at takeaways_gate (no_matching_edge)') because takeaways_gate has
    no give_up edge in pipeline/ingest.dot -- a give_up here does not just
    skip the step, it kills the run."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s003.txt")
    _write_takeaways_brief(
        tmp_path,
        source_id="s003.txt",
        brief=(
            "Source: s003.txt\n"
            "What the OPENING looks like it's about: GPU capacity and market dynamics for Kimi K3, "
            "DeepSeek V4, and Qwen 3.8 -- Chinese AI model releases and business news, not engineering practice."
        ),
    )
    steer_text = (
        "Reject the predicted merge targets -- all three are wrong. This is capacity and business "
        "news about a model launch, not an argument about engineering practice. Do NOT force a "
        "connection to an unrelated existing page. Write the source page and stop there."
    )
    backend = FakeBackend(
        ProxyDecision(
            action="answer",
            answer_text=steer_text,
            reason="source is genuinely off-topic for this wiki",
            grounding=(
                "takeaways_gate note: off-topic judgment is useful information for the steer, not a reason to give_up"
            ),
        )
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_takeaways_question())

    assert answer.value == steer_text
    assert answer.value != AnswerValue.SKIPPED
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "Kimi K3" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["answer_text"] == steer_text


def test_takeaways_gate_backend_still_gives_up_when_persona_truly_silent(tmp_path):
    """give_up remains available for this gate -- the fix narrows WHEN a
    model should choose it, it doesn't remove the option. A backend that
    genuinely gives up (e.g. persona doesn't address takeaways steering at
    all) still fails loud, exactly as before."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_takeaways_brief(tmp_path, source_id="s1.txt")
    backend = FakeBackend(ProxyDecision(action="give_up", reason="persona has no takeaways guidance at all"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_takeaways_question())

    assert answer.value == AnswerValue.SKIPPED
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "give_up"


def test_takeaways_gate_reentrant_stage_suffix_still_reads_brief(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_takeaways_brief(tmp_path, source_id="s1.txt", brief="Source: s1.txt\nsecond pass")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="no specific emphasis", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)
    question = _takeaways_question(stage="takeaways_gate-2")

    answer = interviewer.ask(question)

    assert answer.value == "no specific emphasis"
    assert backend.calls[0].brief == "Source: s1.txt\nsecond pass"


def test_collect_guidance_reentrant_stage_suffix_still_reads_brief(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_guidance_brief(tmp_path, source_id="s1.txt", brief="Source: s1.txt\nsecond pass")
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="steer this way", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)
    question = _guidance_question()
    question.stage = "collect_guidance-2"

    answer = interviewer.ask(question)

    assert answer.value == "steer this way"
    assert backend.calls[0].brief == "Source: s1.txt\nsecond pass"


# -- curate_gate: the source-curation weave-brief delivery fix -------------
#
# curate_gate is the source-curation checkpoint (see
# wiki_weaver.ingest.curate_brief's module docstring) -- same defect class as
# review_gate/collect_guidance/takeaways_gate above: reached with zero
# grounding, this proxy must refuse rather than guess whether a source
# belongs in the wiki.


def _curate_question(stage: str = "curate_gate") -> Question:
    return Question(
        text="curate_gate",
        type=QuestionType.MULTIPLE_CHOICE,
        options=[
            Option(key="A", label="[A] Accept"),
            Option(key="B", label="[B] Decline"),
        ],
        stage=stage,
    )


def _write_curate_brief(wiki_root, source_id: str, brief: str = "Candidate source: s1.txt\nWhat it's about: X") -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.curate_brief_file.write_text(
        json.dumps({"source_id": source_id, "stage": "curate_gate", "brief": brief}),
        encoding="utf-8",
    )


def test_curate_gate_missing_brief_file_refuses_without_calling_backend(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_curate_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_curate_gate_stale_brief_refuses_without_calling_backend(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s2-current.txt")
    _write_curate_brief(tmp_path, source_id="s1-previous.txt")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_curate_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "stale" in entries[0]["reason"]


def test_curate_gate_fresh_brief_reaches_backend_and_answers(tmp_path):
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_curate_brief(tmp_path, source_id="s1.txt", brief="Candidate source: s1.txt\nWhat this wiki covers: X")
    backend = FakeBackend(
        ProxyDecision(action="answer", choice_key="A", reason="fits the wiki's scope", grounding="Capability floor")
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_curate_question())

    assert answer.value == "A"
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "s1.txt" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["choice_key"] == "A"


# -- file_back_gate: ask.dot's own brief-delivery fix -----------------------
#
# file_back_gate has been reachable by default since ask.dot was built, but
# was never once exercised -- same defect class as the ingest-side gates
# above. Unlike those, this brief has NO source-id-style freshness check
# (ask.dot has no per-source resume loop of its own).


def _file_back_question(stage: str = "file_back_gate") -> Question:
    return Question(
        text="file_back_gate",
        type=QuestionType.MULTIPLE_CHOICE,
        options=[
            Option(key="A", label="[A] File this answer back into the wiki"),
            Option(key="B", label="[B] No, just answered"),
        ],
        stage=stage,
    )


def _write_file_back_brief(wiki_root, brief: str = "Question asked: who owns X?\nAnswer: Jane Doe") -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.file_back_brief_file.write_text(
        json.dumps({"stage": "file_back_gate", "brief": brief}),
        encoding="utf-8",
    )


def test_file_back_gate_missing_brief_file_refuses_without_calling_backend(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_file_back_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_file_back_gate_fresh_brief_reaches_backend_and_answers(tmp_path):
    """No current-source breadcrumb is seeded at all here (ask.dot has no
    per-source concept) -- the brief must still reach the backend purely on
    presence, with no source-id freshness check required."""
    _install_persona(tmp_path)
    _write_file_back_brief(tmp_path, brief="Question asked: who owns the runbook?\nAnswer: Jane Doe owns it.")
    backend = FakeBackend(
        ProxyDecision(action="answer", choice_key="A", reason="valuable connection", grounding="Capability floor")
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_file_back_question())

    assert answer.value == "A"
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "Jane Doe owns it" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["choice_key"] == "A"


# -- synthesis_gate: synthesize.dot's own pass-level brief-delivery fix ----
#
# synthesis_gate is the "think about what it all means" checkpoint (see
# wiki_weaver.synthesize.synthesis_brief's module docstring) -- same defect
# class as every gate above. Like file_back_gate, this brief has NO
# source-id-style freshness check (this gate has no per-source resume loop;
# it is a pass-level checkpoint over a candidate SET).


def _synthesis_question(stage: str = "synthesis_gate") -> Question:
    return Question(text="synthesis_gate", type=QuestionType.FREEFORM, stage=stage)


def _write_synthesis_brief(
    wiki_root, brief: str = 'Corpus-wide synthesis pass: 1 candidate theme(s)...\n1. "token economics" -- 4 source(s)'
) -> None:
    wr = WikiRoot(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.synthesis_brief_file.write_text(
        json.dumps(
            {"signature": "sig1", "stage": "synthesis_gate", "brief": brief, "pending_terms": ["token economics"]}
        ),
        encoding="utf-8",
    )


def test_synthesis_gate_missing_brief_file_refuses_without_calling_backend(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="KEEP ALL", reason="should never be used"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_synthesis_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"
    assert "does not exist" in entries[0]["reason"]


def test_synthesis_gate_fresh_brief_reaches_backend_and_answers(tmp_path):
    """No current-source breadcrumb is seeded here either (this gate has no
    per-source concept) -- the brief must reach the backend purely on
    presence, same as file_back_gate."""
    _install_persona(tmp_path)
    _write_synthesis_brief(
        tmp_path, brief='1. "token economics" -- 4 source(s) (50% of corpus)\n   Claim: cost dominates.'
    )
    backend = FakeBackend(
        ProxyDecision(
            action="answer",
            answer_text="DROP: token economics",
            reason="too thin a claim",
            grounding="Capability floor",
        )
    )
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_synthesis_question())

    assert answer.value == "DROP: token economics"
    assert len(backend.calls) == 1
    received: GateQuestion = backend.calls[0]
    assert received.brief is not None
    assert "token economics" in received.brief
    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "answered"
    assert entries[0]["answer_text"] == "DROP: token economics"


# -- standing corrections (lens/corrections/): the feedback-loop fix --------
#
# THE GAP THIS CLOSES (see wiki_weaver.aitl.corrections's module docstring):
# a correction filed via wiki_weaver.correct.persist_lens had no path to
# inform any FUTURE proxy gate decision -- the proxy only ever read
# lens/persona.md. These tests verify corrections are loaded for EVERY
# gate (not gate-specific like `brief`), are fail-SOFT (never a refusal
# condition), and actually reach the backend.


def _write_correction(wiki_root, filename: str = "abc123.md", text: str = "Alice, not Bob, led the migration.") -> None:
    corrections_dir = WikiRoot(wiki_root).corrections_dir
    corrections_dir.mkdir(parents=True, exist_ok=True)
    (corrections_dir / filename).write_text(text, encoding="utf-8")


def test_no_corrections_present_is_not_a_refusal_and_backend_sees_none(tmp_path):
    """The common case: no correction has ever been filed. Must behave
    identically to every existing test above (none of which write
    lens/corrections/) -- corrections=None reaches the backend."""
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_choice_question())

    assert answer.value == "A"
    assert backend.calls[0].corrections is None


def test_standing_correction_reaches_the_backend_on_any_gate(tmp_path):
    """A correction present in lens/corrections/ is attached to the
    GateQuestion the backend actually receives -- for an ordinary choice
    gate, not just review_gate/collect_guidance/takeaways_gate (those are
    the only stages `brief` is gate-specific to; `corrections` is not
    gate-specific)."""
    _install_persona(tmp_path)
    _write_correction(tmp_path, text="Alice, not Bob, led the migration effort.")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="B", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_choice_question())

    assert answer.value == "B"
    assert backend.calls[0].corrections is not None
    assert "Alice, not Bob, led the migration effort." in backend.calls[0].corrections


def test_corrections_digest_recorded_in_audit_when_present(tmp_path):
    _install_persona(tmp_path)
    _write_correction(tmp_path, text="Alice, not Bob, led the migration effort.")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    interviewer.ask(_choice_question())

    entries = _audit_entries(tmp_path)
    assert entries[0]["corrections_digest"] is not None


def test_corrections_digest_null_when_absent(tmp_path):
    _install_persona(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="A", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    interviewer.ask(_choice_question())

    entries = _audit_entries(tmp_path)
    assert entries[0]["corrections_digest"] is None


def test_missing_persona_refuses_before_corrections_are_even_loaded(tmp_path):
    """Persona is still checked FIRST -- corrections never mask a missing
    persona (the deterministic pre-check ordering is unchanged)."""
    _write_correction(tmp_path)
    backend = FakeBackend(ProxyDecision(action="answer", answer_text="should never be used", reason="r"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_freeform_question())

    assert answer.value == AnswerValue.SKIPPED
    assert backend.calls == []


def test_review_gate_still_reads_brief_when_corrections_also_present(tmp_path):
    """corrections and brief are independent, additive fields -- a
    review_gate decision with BOTH a weave brief and a standing correction
    must carry both to the backend, not just one."""
    _install_persona(tmp_path)
    _seed_current_source(tmp_path, "s1.txt")
    _write_review_brief(tmp_path, source_id="s1.txt", brief="Source: s1.txt\nPages updated (1): migration-history.md")
    _write_correction(tmp_path, text="Alice, not Bob, led the migration effort.")
    backend = FakeBackend(ProxyDecision(action="answer", choice_key="B", reason="r", grounding="g"))
    interviewer = ProxyInterviewer(wiki_root=tmp_path, backend=backend)

    answer = interviewer.ask(_review_gate_question())

    assert answer.value == "B"
    received = backend.calls[0]
    assert received.brief is not None and "migration-history.md" in received.brief
    assert received.corrections is not None and "Alice, not Bob" in received.corrections
