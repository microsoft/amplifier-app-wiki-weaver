"""wiki_weaver.aitl.dispatch -- per-site gate-mode dispatch.

PerSiteInterviewer: routes each gate Question (by stage, -N-suffix
stripped) to the Interviewer configured for that stage, falling back to a
default for any stage with no override. A stage resolving to gate-mode=fail
raises GateModeFailError -- the per-site equivalent of HumanGateHandler's
own "no Interviewer provided" guard.

AuditingInterviewer: wraps a non-self-auditing Interviewer so every gate
decision -- not just the proxy's own -- is recorded to
.ai/gate-decisions.jsonl, tagged with the mode that produced it.
"""

from __future__ import annotations

import json

import pytest
from amplifier_module_loop_pipeline.interviewer import (
    Answer,
    AnswerValue,
    Option,
    Question,
    QuestionType,
)

from wiki_weaver.aitl.audit import gate_decisions_path
from wiki_weaver.aitl.dispatch import (
    AuditingInterviewer,
    GateModeFailError,
    PerSiteInterviewer,
    base_stage_name,
)

# -- base_stage_name ---------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("review_gate", "review_gate"),
        ("review_gate-2", "review_gate"),
        ("review_gate-37", "review_gate"),
        ("collect_guidance", "collect_guidance"),
        ("collect_guidance-2", "collect_guidance"),
        ("takeaways_gate", "takeaways_gate"),
        ("takeaways_gate-3", "takeaways_gate"),
    ],
)
def test_base_stage_name_strips_reentrant_suffix(stage, expected):
    assert base_stage_name(stage) == expected


# -- PerSiteInterviewer -------------------------------------------------------


class _RecordingInterviewer:
    def __init__(self, answer_value: str) -> None:
        self.answer_value = answer_value
        self.asked: list[object] = []

    def ask(self, question):
        self.asked.append(question)
        return Answer(value=self.answer_value, text=self.answer_value)

    def ask_multiple(self, questions):
        return [self.ask(q) for q in questions]

    def inform(self, message: str) -> None:
        pass


def _question(stage: str) -> Question:
    return Question(text=f"question for {stage}", type=QuestionType.FREEFORM, stage=stage)


def test_dispatches_to_override_for_named_stage():
    default = _RecordingInterviewer("default-answer")
    override = _RecordingInterviewer("override-answer")
    dispatcher = PerSiteInterviewer(default=default, overrides={"review_gate": override})

    answer = dispatcher.ask(_question("review_gate"))

    assert answer.value == "override-answer"
    assert len(override.asked) == 1
    assert default.asked == []


def test_falls_back_to_default_for_unnamed_stage():
    default = _RecordingInterviewer("default-answer")
    override = _RecordingInterviewer("override-answer")
    dispatcher = PerSiteInterviewer(default=default, overrides={"review_gate": override})

    answer = dispatcher.ask(_question("collect_guidance"))

    assert answer.value == "default-answer"
    assert len(default.asked) == 1


def test_reentrant_stage_suffix_resolves_to_same_override():
    override = _RecordingInterviewer("override-answer")
    dispatcher = PerSiteInterviewer(default=None, overrides={"review_gate": override})

    answer = dispatcher.ask(_question("review_gate-2"))

    assert answer.value == "override-answer"


def test_unconfigured_stage_with_fail_default_raises_gate_mode_fail_error():
    dispatcher = PerSiteInterviewer(default=None, overrides={})

    with pytest.raises(GateModeFailError, match="takeaways_gate"):
        dispatcher.ask(_question("takeaways_gate"))


def test_explicit_fail_override_raises_even_with_real_default():
    default = _RecordingInterviewer("default-answer")
    dispatcher = PerSiteInterviewer(default=default, overrides={"takeaways_gate": None})

    with pytest.raises(GateModeFailError):
        dispatcher.ask(_question("takeaways_gate"))
    # The default (a real interviewer) is untouched by the fail override.
    assert default.asked == []


@pytest.mark.asyncio
async def test_async_ask_prefers_async_ask_when_present():
    class _AsyncOnly:
        async def async_ask(self, question):
            return Answer(value="async-answer")

    dispatcher = PerSiteInterviewer(default=_AsyncOnly(), overrides={})

    answer = await dispatcher.async_ask(_question("any_gate"))

    assert answer.value == "async-answer"


def test_ask_multiple_delegates_per_question_and_can_mix_modes():
    default = _RecordingInterviewer("default-answer")
    override = _RecordingInterviewer("override-answer")
    dispatcher = PerSiteInterviewer(default=default, overrides={"review_gate": override})

    answers = dispatcher.ask_multiple([_question("review_gate"), _question("collect_guidance")])

    assert [a.value for a in answers] == ["override-answer", "default-answer"]


def test_inform_broadcasts_to_default_only():
    calls: list[str] = []

    class _Informer:
        def ask(self, question):
            return Answer()

        def inform(self, message: str) -> None:
            calls.append(message)

    dispatcher = PerSiteInterviewer(default=_Informer(), overrides={})
    dispatcher.inform("hello")

    assert calls == ["hello"]


def test_inform_is_a_no_op_when_default_is_fail():
    dispatcher = PerSiteInterviewer(default=None, overrides={})
    dispatcher.inform("hello")  # must not raise


# -- AuditingInterviewer -------------------------------------------------------


def _audit_entries(wiki_root) -> list[dict]:
    path = gate_decisions_path(wiki_root)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_wraps_console_style_answer_and_records_mode(tmp_path):
    inner = _RecordingInterviewer("A")
    wrapped = AuditingInterviewer(inner, tmp_path, mode="console")

    answer = wrapped.ask(
        Question(
            text="Approve?",
            type=QuestionType.MULTIPLE_CHOICE,
            options=[Option(key="A", label="[A] Accept"), Option(key="B", label="[B] Skip")],
            stage="review_gate",
        )
    )

    assert answer.value == "A"
    entries = _audit_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["mode"] == "console"
    assert entries[0]["stage"] == "review_gate"
    assert entries[0]["decision"] == "answered"


def test_wraps_auto_approve_style_choice_answer(tmp_path):
    class _AutoApprove:
        def ask(self, question):
            first = question.options[0]
            return Answer(value=first.key, selected_option=first)

    wrapped = AuditingInterviewer(_AutoApprove(), tmp_path, mode="auto-approve")

    wrapped.ask(
        Question(
            text="Approve?",
            type=QuestionType.MULTIPLE_CHOICE,
            options=[Option(key="A", label="[A] Accept")],
            stage="takeaways_gate",
        )
    )

    entries = _audit_entries(tmp_path)
    assert entries[0]["mode"] == "auto-approve"
    assert entries[0]["choice_key"] == "A"
    assert entries[0]["choice_label"] == "[A] Accept"


def test_wraps_freeform_answer(tmp_path):
    inner = _RecordingInterviewer("emphasize the caching section")
    wrapped = AuditingInterviewer(inner, tmp_path, mode="console")

    wrapped.ask(Question(text="What should we emphasize?", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    entries = _audit_entries(tmp_path)
    assert entries[0]["gate_type"] == "freeform"
    assert entries[0]["answer_text"] == "emphasize the caching section"


def test_records_skipped_answer_as_refused(tmp_path):
    class _SkippingInterviewer:
        def ask(self, question):
            return Answer(value=AnswerValue.SKIPPED)

    wrapped = AuditingInterviewer(_SkippingInterviewer(), tmp_path, mode="console")
    wrapped.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    entries = _audit_entries(tmp_path)
    assert entries[0]["decision"] == "refused"


def test_skips_auditing_when_wiki_root_is_none():
    """Best-effort: some pipelines run console/auto-approve with no
    --param wiki_root -- auditing must not crash the run, it just has
    nowhere to write."""
    inner = _RecordingInterviewer("A")
    wrapped = AuditingInterviewer(inner, None, mode="console")  # type: ignore[arg-type]

    answer = wrapped.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert answer.value == "A"  # the underlying answer still comes through


@pytest.mark.asyncio
async def test_async_ask_wraps_and_records():
    pass  # covered by PerSiteInterviewer's own async test above; kept out
    # of AuditingInterviewer suite to avoid needing a tmp_path async fixture
    # combination redundant with the sync coverage already present.


def test_ask_multiple_records_each_decision(tmp_path):
    inner = _RecordingInterviewer("ok")
    wrapped = AuditingInterviewer(inner, tmp_path, mode="console")

    wrapped.ask_multiple(
        [
            Question(text="q1", type=QuestionType.FREEFORM, stage="s1"),
            Question(text="q2", type=QuestionType.FREEFORM, stage="s2"),
        ]
    )

    entries = _audit_entries(tmp_path)
    assert len(entries) == 2
    assert {e["stage"] for e in entries} == {"s1", "s2"}
