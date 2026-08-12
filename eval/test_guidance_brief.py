"""guidance_brief: the context collect_guidance needs to actually answer
instead of refusing -- the same weave brief review_gate showed, plus
(best-effort) the reviewer's own stated reason for choosing [B] Guide from
.ai/gate-decisions.jsonl. FAIL LOUD (never fabricate) when review_brief_file
itself is missing/unreadable/stale for the current source."""

from __future__ import annotations

import json

from wiki_weaver.ingest import guidance_brief
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt") -> WikiRoot:
    wiki_root.add_source(name)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def _write_review_brief(wr: WikiRoot, source_id: str, brief: str = "Source: s1.txt\nPages created (0): none") -> None:
    ensure_dir(wr.ai_dir)
    wr.review_brief_file.write_text(
        json.dumps({"source_id": source_id, "stage": "review_gate", "brief": brief}),
        encoding="utf-8",
    )


def _write_gate_decision(wr: WikiRoot, **fields) -> None:
    ensure_dir(wr.ai_dir)
    existing = wr.gate_decisions_file.read_text(encoding="utf-8") if wr.gate_decisions_file.is_file() else ""
    with open(wr.gate_decisions_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(fields) + "\n")
    _ = existing  # keep append semantics obvious in the test


def test_missing_current_source_fails_loud_no_fabricated_brief(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""
    assert "current_source" in err


def test_missing_review_brief_file_fails_loud_no_fabricated_guidance(wiki_root, capsys):
    """No review-brief.json at all (prepare_review_brief/quarantine_brief
    never ran, or ran before this fix landed) -- must fail rather than
    invent steering context. This is the exact defect the fix addresses:
    collect_guidance reached with zero grounding."""
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""
    assert "does not exist" in err
    assert not wr.guidance_brief_file.is_file()


def test_stale_review_brief_fails_loud(wiki_root, capsys):
    """review-brief.json exists but was written for a DIFFERENT source than
    current_source.txt names right now -- must refuse, never trust it."""
    wr = _seed_current_source(wiki_root, "s2-current.txt")
    _write_review_brief(wr, source_id="s1-previous.txt")

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    err = capsys.readouterr().err

    assert code == 1
    assert "stale" in err
    assert not wr.guidance_brief_file.is_file()


def test_fresh_review_brief_produces_guidance_brief_with_weave_context(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "new-page.md" in payload["guidance_brief"]


def test_guidance_brief_includes_reviewers_own_guide_reason_when_available(wiki_root, capsys):
    """THE enhancement (best-effort, additive): the reviewer's own stated
    reason for choosing [B] Guide, from the most recent gate-decisions.jsonl
    entry, is folded into the guidance brief."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")
    _write_gate_decision(
        wr,
        stage="review_gate",
        gate_type="choice",
        question="review_gate",
        choices=["[A] Accept", "[B] Guide", "[C] Skip"],
        decision="answered",
        reason="the wikilinks in new-page.md are broken, needs fixing before merge",
        grounding="Capability floor",
        choice_key="B",
        choice_label="[B] Guide",
    )

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "broken, needs fixing before merge" in payload["guidance_brief"]


def test_guidance_brief_omits_reason_section_when_gate_decisions_absent(wiki_root, capsys):
    """A human reviewer using the console interviewer never writes to
    gate-decisions.jsonl at all -- the guidance brief must still be useful
    (the weave-brief half) in that case, never fail loud on its absence."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "new-page.md" in payload["guidance_brief"]
    assert "Reviewer's stated reason" not in payload["guidance_brief"]


def test_guidance_brief_ignores_reason_from_a_non_guide_decision(wiki_root, capsys):
    """If the LAST gate-decisions.jsonl entry isn't an [B] Guide answer for
    review_gate (e.g. it's from an unrelated gate, or an Accept/Skip), the
    reason must not be attributed to this Guide choice."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")
    _write_gate_decision(
        wr,
        stage="review_gate",
        gate_type="choice",
        question="review_gate",
        choices=["[A] Accept", "[B] Guide", "[C] Skip"],
        decision="answered",
        reason="looks fine, accepting",
        grounding="Capability floor",
        choice_key="A",
        choice_label="[A] Accept",
    )

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "Reviewer's stated reason" not in payload["guidance_brief"]


def test_guidance_brief_reads_reason_from_reentrant_review_gate_suffix(wiki_root, capsys):
    """review_gate-2 (a re-entrant loop pass) is still the same gate --
    its Guide reason must still be picked up."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nsecond pass")
    _write_gate_decision(
        wr,
        stage="review_gate-2",
        gate_type="choice",
        question="review_gate",
        choices=["[A] Accept", "[B] Guide", "[C] Skip"],
        decision="answered",
        reason="still missing a citation",
        grounding="g",
        choice_key="B",
        choice_label="[B] Guide",
    )

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "still missing a citation" in payload["guidance_brief"]


def test_writes_guidance_brief_file_stamped_with_source_id(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt", brief="Source: s1.txt\nPages created (1): new-page.md")

    code = guidance_brief.main(["--wiki-root", str(wr.root)])
    stdout_payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert wr.guidance_brief_file.is_file()
    file_payload = json.loads(wr.guidance_brief_file.read_text(encoding="utf-8"))
    assert file_payload["source_id"] == "s1.txt"
    assert file_payload["stage"] == "collect_guidance"
    assert file_payload["brief"] == stdout_payload["guidance_brief"]


def test_output_is_exactly_one_json_line_on_success(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    _write_review_brief(wr, source_id="s1.txt")
    guidance_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
