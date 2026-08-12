"""persist_takeaways: reads takeaways-answer.json (stamped, freshness-checked
against current_source.txt) instead of an environment variable -- a
verified platform defect means dotted env var names (tool_env would produce
HUMAN.GATE.TEXT from the engine's own "human.gate.text" context key) do not
reliably survive subprocess dispatch on this platform (see
wiki_weaver.aitl.freeform_bridge's module docstring for the full story).

UNLIKE persist_guidance.py, the literal auto-approve stub (and a blank
answer) is NOT a fail-loud condition here -- an unattended per-site
gate-mode=auto-approve for takeaways_gate is a legitimate "no particular
emphasis" answer, not a refusal. Always clears any stale file from a prior
segment-processing pass first."""

from __future__ import annotations

import json

from wiki_weaver.ingest import persist_takeaways
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt") -> WikiRoot:
    wiki_root.add_source(name)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def _write_answer(wr: WikiRoot, source_id: str, text: str) -> None:
    ensure_dir(wr.ai_dir)
    wr.takeaways_answer_file.write_text(
        json.dumps({"source_id": source_id, "stage": "takeaways_gate", "text": text}),
        encoding="utf-8",
    )


def test_writes_takeaways_guidance_verbatim(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "Emphasize the caching tradeoffs section.")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "Emphasize the caching tradeoffs section."


def test_auto_approved_stub_writes_no_guidance_file(wiki_root, capsys):
    """UNLIKE collect_guidance's persist_guidance.py: an unattended
    auto-approve answer here is a legitimate 'no particular emphasis'
    outcome, not a refusal -- so no file is written at all, and exit is 0."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "auto-approved")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()
    assert "no takeaways guidance" in capsys.readouterr().err


def test_no_specific_emphasis_answer_writes_no_guidance_file(wiki_root, capsys):
    """THE FIX (see backend.py's _TAKEAWAYS_GATE_NOTE / lens/persona.md's
    own takeaways-discussion guidance): the proxy's honest "nothing to
    steer" answer for this gate is the literal phrase "no specific
    emphasis" -- treated exactly like the auto-approved stub, never
    persisted as though it were a real steering instruction to hand
    weave. This is the "no-emphasis-answer" path: an ANSWERED gate (see
    ProxyInterviewer, which records this as decision="answered", not
    "give_up"/"refused") that still correctly proceeds unsteered."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "no specific emphasis")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()
    assert "no takeaways guidance" in capsys.readouterr().err


def test_no_specific_emphasis_answer_matches_case_insensitively_with_trailing_period(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "No Specific Emphasis.")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


def test_real_emphasis_that_merely_mentions_no_specific_emphasis_phrase_still_persists(wiki_root):
    """The stub match is on the WHOLE normalized answer, not a substring --
    a real steer that happens to reference the phrase (unlikely, but the
    contract must be precise) is still persisted verbatim."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "no specific emphasis needed beyond the caching section, emphasize that.")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert (
        out_path.read_text(encoding="utf-8")
        == "no specific emphasis needed beyond the caching section, emphasize that."
    )


def test_blank_answer_writes_no_guidance_file(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "   ")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


def test_missing_answer_file_writes_no_guidance_file(wiki_root):
    """No takeaways-answer.json at all (e.g. every interviewer refused, or
    the question wasn't freeform) -- treated as 'no guidance', never
    fail-loud: this is a normal, expected outcome for this gate."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


def test_stale_answer_file_ignored(wiki_root):
    """An answer written for a DIFFERENT (previous) source must never be
    applied to the current one -- same freshness discipline as
    review_brief_file/guidance_brief_file/takeaways_brief_file."""
    wr = _seed_current_source(wiki_root, "s2-current.txt")
    _write_answer(wr, "s1-previous.txt", "Stale emphasis from a previous source.")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


def test_malformed_answer_file_ignored(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    ensure_dir(wr.ai_dir)
    wr.takeaways_answer_file.write_text("not json", encoding="utf-8")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


def test_missing_current_source_fails_loud(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 1
    assert "current_source" in capsys.readouterr().err


def test_clears_stale_file_from_a_prior_pass_before_writing_real_guidance(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt")
    out_path = wr.ai_dir / "takeaways-guidance.md"
    out_path.write_text("STALE guidance from a previous segment", encoding="utf-8")
    _write_answer(wr, "s1.txt", "Fresh emphasis for this segment.")

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert out_path.read_text(encoding="utf-8") == "Fresh emphasis for this segment."


def test_clears_stale_file_from_a_prior_pass_when_this_pass_has_no_guidance(wiki_root):
    """The staleness case that actually matters: a REAL answer from a
    previous segment must never survive into a pass where the answerer
    said nothing -- weave must not silently re-apply old emphasis."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    out_path = wr.ai_dir / "takeaways-guidance.md"
    out_path.write_text("STALE guidance from a previous segment", encoding="utf-8")
    _write_answer(wr, "s1.txt", "auto-approved")

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not out_path.exists()


# ---------------------------------------------------------------------------
# F4 (GOAL-followups.md): a RETRACT:/ABSORB: directive embedded in the
# takeaways_gate answer -- the exact gate the human's two real "Delete
# joe-njenga.md" requests were made at. Needed ZERO pipeline/ingest.dot
# changes -- this node already runs unconditionally after every answer.
# ---------------------------------------------------------------------------


def test_retract_directive_removes_page_and_writes_no_guidance_file(wiki_root):
    wr = _seed_current_source(wiki_root, "017-source.txt")
    wr.wiki_dir.joinpath("joe-njenga.md").write_text("# Joe Njenga\n", encoding="utf-8")
    _write_answer(wr, "017-source.txt", "RETRACT: joe-njenga.md")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not (wr.wiki_dir / "joe-njenga.md").exists()
    ledger_record = json.loads(wr.ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert ledger_record["decision"] == "retract"
    assert ledger_record["requested_during_source_id"] == "017-source.txt"
    # No leftover guidance text -- the whole answer WAS the directive.
    assert not out_path.exists()


def test_retract_directive_alongside_real_guidance_persists_both(wiki_root):
    wr = _seed_current_source(wiki_root, "018-source.txt")
    wr.wiki_dir.joinpath("joe-njenga.md").write_text("# Joe Njenga\n", encoding="utf-8")
    _write_answer(
        wr,
        "018-source.txt",
        "RETRACT: joe-njenga.md\nEmphasize the retry-budget section for this source.",
    )
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 0
    assert not (wr.wiki_dir / "joe-njenga.md").exists()
    assert "retry-budget" in out_path.read_text(encoding="utf-8")


def test_malformed_retract_directive_fails_loud_and_writes_nothing(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    wr.wiki_dir.joinpath("joe-njenga.md").write_text("# Joe Njenga\n", encoding="utf-8")
    # ABSORB with no INTO: clause -- malformed.
    _write_answer(wr, "s1.txt", "ABSORB: joe-njenga.md")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 1
    assert "malformed retract directive" in capsys.readouterr().err
    assert not out_path.exists()
    assert (wr.wiki_dir / "joe-njenga.md").exists()  # untouched -- never mutated on a rejected directive


def test_retract_directive_for_missing_page_fails_loud(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_answer(wr, "s1.txt", "RETRACT: does-not-exist.md")
    out_path = wr.ai_dir / "takeaways-guidance.md"

    code = persist_takeaways.main(["--wiki-root", str(wr.root), "--out", str(out_path)])

    assert code == 1
    assert "retract failed" in capsys.readouterr().err
    assert not wr.ledger_path.exists()
