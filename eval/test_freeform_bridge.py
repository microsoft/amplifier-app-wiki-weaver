"""wiki_weaver.aitl.freeform_bridge -- FreeformAnswerRecorder.

Wraps ANY Interviewer so a freeform takeaways_gate answer is written
directly to WikiRoot.takeaways_answer_file the moment it is given --
working around a verified platform defect (dotted env var names do not
reliably survive subprocess dispatch on this platform, so the engine's own
tool_env convention cannot carry a freeform answer to a downstream tool
node reliably). Transparent pass-through for every other gate/stage.
"""

from __future__ import annotations

import json

import pytest
from amplifier_module_loop_pipeline.interviewer import Answer, AnswerValue, Option, Question, QuestionType

from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder
from wiki_weaver.lib import WikiRoot, ensure_dir


class _FixedInterviewer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.asked: list[object] = []

    def ask(self, question):
        self.asked.append(question)
        return Answer(value=self.text, text=self.text)


def _seed_current_source(wiki_root, name: str = "s1.txt") -> WikiRoot:
    wiki_root.add_source(name)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def _read_answer_file(wr: WikiRoot) -> dict:
    return json.loads(wr.takeaways_answer_file.read_text(encoding="utf-8"))


def test_writes_answer_file_for_takeaways_gate_freeform_question(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("Emphasize the caching section."), wr.root)

    answer = recorder.ask(Question(text="What to emphasize?", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert answer.value == "Emphasize the caching section."
    payload = _read_answer_file(wr)
    assert payload["source_id"] == "s1.txt"
    assert payload["stage"] == "takeaways_gate"
    assert payload["text"] == "Emphasize the caching section."


def test_reentrant_stage_suffix_still_writes_answer_file(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("steer this way"), wr.root)

    recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate-2"))

    payload = _read_answer_file(wr)
    assert payload["text"] == "steer this way"


def test_does_not_write_for_non_takeaways_gate_freeform_question(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("some steering text"), wr.root)

    recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="collect_guidance"))

    assert not wr.takeaways_answer_file.is_file()


def test_does_not_write_for_choice_question_even_at_takeaways_gate_stage(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")

    class _ChoiceInterviewer:
        def ask(self, question):
            opt = question.options[0]
            return Answer(value=opt.key, selected_option=opt)

    recorder = FreeformAnswerRecorder(_ChoiceInterviewer(), wr.root)
    recorder.ask(
        Question(
            text="q",
            type=QuestionType.MULTIPLE_CHOICE,
            options=[Option(key="A", label="[A] Accept")],
            stage="takeaways_gate",
        )
    )

    assert not wr.takeaways_answer_file.is_file()


def test_skips_write_when_no_current_source(wiki_root):
    """Best-effort: nothing durable to stamp the answer with -- must not crash."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    recorder = FreeformAnswerRecorder(_FixedInterviewer("emphasis text"), wr.root)

    answer = recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert answer.value == "emphasis text"  # answer still comes through
    assert not wr.takeaways_answer_file.is_file()


def test_skips_write_when_answer_is_skipped(wiki_root):
    """A refused/skipped answer has no .text to write -- must not crash or
    write a bogus entry."""
    wr = _seed_current_source(wiki_root, "s1.txt")

    class _RefusingInterviewer:
        def ask(self, question):
            return Answer(value=AnswerValue.SKIPPED)

    recorder = FreeformAnswerRecorder(_RefusingInterviewer(), wr.root)
    answer = recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert answer.value == AnswerValue.SKIPPED
    assert not wr.takeaways_answer_file.is_file()


def test_overwrites_stale_answer_from_a_previous_segment(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    ensure_dir(wr.ai_dir)
    wr.takeaways_answer_file.write_text(
        json.dumps({"source_id": "s1.txt", "stage": "takeaways_gate", "text": "OLD emphasis"}), encoding="utf-8"
    )
    recorder = FreeformAnswerRecorder(_FixedInterviewer("NEW emphasis"), wr.root)

    recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert _read_answer_file(wr)["text"] == "NEW emphasis"


@pytest.mark.asyncio
async def test_async_ask_prefers_async_ask_when_present(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")

    class _AsyncOnly:
        async def async_ask(self, question):
            return Answer(value="async emphasis", text="async emphasis")

    recorder = FreeformAnswerRecorder(_AsyncOnly(), wr.root)
    answer = await recorder.async_ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    assert answer.value == "async emphasis"
    assert _read_answer_file(wr)["text"] == "async emphasis"


def test_ask_multiple_delegates_to_ask_for_each(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("emphasis"), wr.root)

    answers = recorder.ask_multiple(
        [
            Question(text="q1", type=QuestionType.FREEFORM, stage="takeaways_gate"),
            Question(text="q2", type=QuestionType.FREEFORM, stage="collect_guidance"),
        ]
    )

    assert len(answers) == 2
    assert _read_answer_file(wr)["text"] == "emphasis"


def _seed_synthesis_brief(wiki_root, signature: str = "sig1") -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.synthesis_brief_file.write_text(
        json.dumps({"signature": signature, "stage": "synthesis_gate", "brief": "...", "pending_terms": ["x"]}),
        encoding="utf-8",
    )
    return wr


def _read_synthesis_answer_file(wr: WikiRoot) -> dict:
    return json.loads(wr.synthesis_answer_file.read_text(encoding="utf-8"))


def test_writes_answer_file_for_synthesis_gate_freeform_question(wiki_root):
    wr = _seed_synthesis_brief(wiki_root, "sig1")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("DROP: token economics"), wr.root)

    answer = recorder.ask(Question(text="Drop anything?", type=QuestionType.FREEFORM, stage="synthesis_gate"))

    assert answer.value == "DROP: token economics"
    payload = _read_synthesis_answer_file(wr)
    assert payload["signature"] == "sig1"
    assert payload["stage"] == "synthesis_gate"
    assert payload["text"] == "DROP: token economics"


def test_synthesis_gate_answer_does_not_touch_takeaways_answer_file(wiki_root):
    wr = _seed_synthesis_brief(wiki_root, "sig1")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("KEEP ALL"), wr.root)

    recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="synthesis_gate"))

    assert not wr.takeaways_answer_file.is_file()


def test_reentrant_synthesis_gate_suffix_still_writes_answer_file(wiki_root):
    wr = _seed_synthesis_brief(wiki_root, "sig1")
    recorder = FreeformAnswerRecorder(_FixedInterviewer("DROP: x"), wr.root)

    recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="synthesis_gate-2"))

    assert _read_synthesis_answer_file(wr)["text"] == "DROP: x"


def test_skips_write_for_synthesis_gate_when_no_brief_on_disk(wiki_root):
    """Best-effort: nothing durable to stamp the answer with -- must not crash."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    recorder = FreeformAnswerRecorder(_FixedInterviewer("DROP: x"), wr.root)

    answer = recorder.ask(Question(text="q", type=QuestionType.FREEFORM, stage="synthesis_gate"))

    assert answer.value == "DROP: x"  # answer still comes through
    assert not wr.synthesis_answer_file.is_file()


def test_inform_delegates_to_inner():
    calls: list[str] = []

    class _Informer:
        def ask(self, question):
            return Answer()

        def inform(self, message: str) -> None:
            calls.append(message)

    FreeformAnswerRecorder(_Informer(), "/tmp/does-not-matter").inform("hello")
    assert calls == ["hello"]
