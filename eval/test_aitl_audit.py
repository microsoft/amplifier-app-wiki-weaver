"""wiki_weaver.aitl.audit -- append-only, durable gate-decision audit trail.

pipeline/CLI-CONTRACT.md's wiki_weaver.review entry names .ai/gate-
decisions.jsonl as the file it will read; this proves the format that
consumer will see, independent of that (not-yet-built) command.
"""

from __future__ import annotations

import json

from wiki_weaver.aitl.audit import gate_decisions_path, persona_digest, record_decision


def test_record_decision_appends_jsonl(tmp_path):
    record_decision(
        tmp_path,
        stage="ask-1",
        gate_type="freeform",
        question="What is this wiki for?",
        choices=None,
        decision="answered",
        reason="matches identity section",
        grounding="Identity: this wiki tracks runbooks",
        answer_text="Runbooks and on-call knowledge.",
        persona_text="## Identity\nfoo",
        mode="proxy",
    )

    path = gate_decisions_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["stage"] == "ask-1"
    assert entry["gate_type"] == "freeform"
    assert entry["question"] == "What is this wiki for?"
    assert entry["decision"] == "answered"
    assert entry["reason"] == "matches identity section"
    assert entry["grounding"] == "Identity: this wiki tracks runbooks"
    assert entry["answer_text"] == "Runbooks and on-call knowledge."
    assert entry["choice_key"] is None
    assert entry["persona_digest"] == persona_digest("## Identity\nfoo")
    assert entry["timestamp"]
    assert entry["mode"] == "proxy"


def test_mode_defaults_to_null_for_backward_compatible_callers(tmp_path):
    """Callers that predate per-site gate-mode selection never pass
    ``mode=`` -- the field must default to ``None``, never crash."""
    record_decision(
        tmp_path, stage="a", gate_type="freeform", question="q", choices=None, decision="answered", reason="r"
    )

    entry = json.loads(gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert entry["mode"] is None


def test_mode_records_console_and_auto_approve_values(tmp_path):
    record_decision(
        tmp_path,
        stage="a",
        gate_type="freeform",
        question="q",
        choices=None,
        decision="answered",
        reason="r",
        mode="console",
    )
    record_decision(
        tmp_path,
        stage="b",
        gate_type="choice",
        question="q2",
        choices=["[A] X"],
        decision="answered",
        reason="r",
        mode="auto-approve",
    )

    lines = gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["mode"] == "console"
    assert json.loads(lines[1])["mode"] == "auto-approve"


def test_record_decision_accumulates_across_calls(tmp_path):
    record_decision(
        tmp_path, stage="a", gate_type="freeform", question="q1", choices=None, decision="answered", reason="r1"
    )
    record_decision(
        tmp_path,
        stage="b",
        gate_type="choice",
        question="q2",
        choices=["[A] X", "[B] Y"],
        decision="give_up",
        reason="r2",
    )

    lines = gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["stage"] == "a"
    assert json.loads(lines[1])["stage"] == "b"
    assert json.loads(lines[1])["choices"] == ["[A] X", "[B] Y"]


def test_missing_persona_text_yields_null_digest(tmp_path):
    record_decision(
        tmp_path, stage="a", gate_type="freeform", question="q", choices=None, decision="refused", reason="no persona"
    )

    entry = json.loads(gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert entry["persona_digest"] is None


def test_persona_digest_is_stable_and_content_sensitive():
    a = persona_digest("hello")
    b = persona_digest("hello")
    c = persona_digest("world")

    assert a == b
    assert a != c


# -- corrections_digest: the traceability the feedback-loop proof requires --
#
# Two runs of the SAME gate must be distinguishable in gate-decisions.jsonl
# by whether a standing correction was in effect -- corrections_digest is
# that signal, mirroring persona_digest's existing shape exactly.


def test_missing_corrections_text_yields_null_digest(tmp_path):
    record_decision(
        tmp_path, stage="a", gate_type="freeform", question="q", choices=None, decision="answered", reason="r"
    )

    entry = json.loads(gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert entry["corrections_digest"] is None


def test_present_corrections_text_yields_non_null_digest_distinct_from_persona(tmp_path):
    record_decision(
        tmp_path,
        stage="a",
        gate_type="choice",
        question="q",
        choices=["[A] Approve"],
        decision="answered",
        reason="r",
        persona_text="## Identity\nfoo",
        corrections_text="Alice, not Bob, led the migration.",
    )

    entry = json.loads(gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert entry["corrections_digest"] is not None
    assert entry["corrections_digest"] != entry["persona_digest"]


def test_same_correction_text_yields_same_digest_across_two_runs(tmp_path):
    """The core traceability claim: run the SAME gate twice, once with a
    correction present and once without -- the digest field lets a reader
    of gate-decisions.jsonl tell them apart deterministically."""
    record_decision(
        tmp_path,
        stage="scope_gate",
        gate_type="choice",
        question="Approve reprocess?",
        choices=["[A] Approve", "[B] Skip"],
        decision="answered",
        reason="no correction on file",
        choice_key="A",
        mode="proxy",
    )
    record_decision(
        tmp_path,
        stage="scope_gate",
        gate_type="choice",
        question="Approve reprocess?",
        choices=["[A] Approve", "[B] Skip"],
        decision="answered",
        reason="correction on file changes this",
        choice_key="B",
        corrections_text="Alice, not Bob, led the migration.",
        mode="proxy",
    )

    lines = gate_decisions_path(tmp_path).read_text(encoding="utf-8").splitlines()
    before = json.loads(lines[0])
    after = json.loads(lines[1])
    assert before["corrections_digest"] is None
    assert after["corrections_digest"] is not None
    assert before["choice_key"] != after["choice_key"]
