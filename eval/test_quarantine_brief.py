"""quarantine_brief: route an exhausted re-weave to review_gate ONCE before
automatic quarantine takes over. Termination proof lives here too -- see
test_second_exhaustion_for_same_source_auto_quarantines_no_second_review
and test_termination_bound_holds_across_many_repeated_exhaustions below.

Also covers the SEGMENT-SCOPED fix (docs/KNOWN_ISSUES.md #3): the one-shot
rescue latch is keyed by (source_id, segment_index), not source_id alone --
see test_later_segment_of_same_source_gets_its_own_rescue below."""

from __future__ import annotations

import json

from wiki_weaver.ingest import quarantine_brief
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt", segment: dict | None = None) -> WikiRoot:
    wiki_root.add_source(name)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    if segment is not None:
        wr.current_segment_file.write_text(json.dumps(segment), encoding="utf-8")
    return wr


def test_missing_current_source_fails_loud_no_fabricated_brief(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = quarantine_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""
    assert "current_source" in err


def test_first_exhaustion_writes_review_brief_and_routes_needs_review(wiki_root, capsys):
    """THE fix: the FIRST give_up for a source builds a review_gate brief
    (reusing review_brief.build_brief) and routes to review_gate -- never
    straight to commit_skip."""
    wr = _seed_current_source(wiki_root, "s1.txt")
    wr.reweave_attempts_file.write_text(
        json.dumps({"source_id": "s1.txt", "segment_index": 1, "attempts": 3}), encoding="utf-8"
    )
    wr.validation_report_file.write_text(
        "# Structural Validation Report\n\n1 issue(s) found:\n- broken link: [[missing-page]]\n",
        encoding="utf-8",
    )

    code = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["quarantine_status"] == "needs_review"
    assert "broken link" in payload["review_brief"]
    assert "3 time(s)" in payload["review_brief"]

    # review_gate's own brief file was written -- review_gate needs ZERO
    # changes to handle this path (same file shape prepare_review_brief
    # writes).
    assert wr.review_brief_file.is_file()
    file_payload = json.loads(wr.review_brief_file.read_text(encoding="utf-8"))
    assert file_payload["source_id"] == "s1.txt"
    assert file_payload["stage"] == "review_gate"
    assert file_payload["quarantined"] is True

    # The one-shot latch is now set for this (source, segment).
    latch = json.loads(wr.quarantine_state_file.read_text(encoding="utf-8"))
    assert latch == {"source_id": "s1.txt", "segment_index": 1, "reviewed": True}


def test_second_exhaustion_for_same_source_auto_quarantines_no_second_review(wiki_root, capsys):
    """THE termination guarantee, directly proven: once a source has been
    offered its one post-exhaustion review, a SECOND give_up for the SAME
    source (e.g. a guided re-weave that failed validation again) routes
    straight to commit_quarantine -- never back to review_gate again."""
    wr = _seed_current_source(wiki_root, "s1.txt")

    # First exhaustion: needs_review, latch set.
    code1 = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload1 = json.loads(capsys.readouterr().out.strip())
    assert code1 == 0
    assert payload1["quarantine_status"] == "needs_review"

    # Second exhaustion for the SAME source: already_reviewed, no new
    # review_brief_file overwrite needed, straight to auto-quarantine.
    code2 = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload2 = json.loads(capsys.readouterr().out.strip())
    assert code2 == 0
    assert payload2["quarantine_status"] == "already_reviewed"


def test_termination_bound_holds_across_many_repeated_exhaustions(wiki_root, capsys):
    """Stronger form of the termination proof: no matter how many times
    give_up fires for the SAME source afterward, it is ALWAYS
    already_reviewed -- never needs_review again, and never anything else.
    This is what makes an unbounded reviewer<->reweave cycle structurally
    impossible."""
    wr = _seed_current_source(wiki_root, "s1.txt")

    quarantine_brief.main(["--wiki-root", str(wr.root)])
    capsys.readouterr()

    for _ in range(10):
        code = quarantine_brief.main(["--wiki-root", str(wr.root)])
        payload = json.loads(capsys.readouterr().out.strip())
        assert code == 0
        assert payload["quarantine_status"] == "already_reviewed"


def test_stale_latch_from_previous_source_is_reset_not_reused(wiki_root, capsys):
    """A latch left over from a DIFFERENT source id must never suppress
    review for a genuinely new source -- identical discipline to
    reweave_bound.py's own stale-attempts handling."""
    wr = _seed_current_source(wiki_root, "s2.txt")
    wr.quarantine_state_file.write_text(json.dumps({"source_id": "s1-old.txt", "reviewed": True}), encoding="utf-8")

    code = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert payload["quarantine_status"] == "needs_review"


def test_later_segment_of_same_source_gets_its_own_rescue(wiki_root, capsys):
    """THE fix (docs/KNOWN_ISSUES.md #3): the one-shot rescue latch is keyed
    by (source_id, segment_index), not source_id alone. Before this fix,
    segment 1 spending the source's only rescue meant segment 2+ of the
    SAME source found the latch already burned and auto-quarantined with
    no review, ever -- confirmed against a real 73-source production run
    (segment 1: 0/73 quarantines; segment 2+: 14 quarantines vs 4 accepts).
    Each segment must get its own rescue, exactly like a freshly-selected
    source does."""
    wr = _seed_current_source(wiki_root, "s1.txt", segment={"index": 1, "total": 2})

    # Segment 1 exhausts and spends ITS rescue.
    code1 = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload1 = json.loads(capsys.readouterr().out.strip())
    assert code1 == 0
    assert payload1["quarantine_status"] == "needs_review"

    # Segment 2 of the SAME source now exhausts for the FIRST time -- it
    # must get its own rescue, not inherit segment 1's spent latch.
    wr.current_segment_file.write_text(json.dumps({"index": 2, "total": 2}), encoding="utf-8")
    code2 = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload2 = json.loads(capsys.readouterr().out.strip())
    assert code2 == 0
    assert payload2["quarantine_status"] == "needs_review"

    latch = json.loads(wr.quarantine_state_file.read_text(encoding="utf-8"))
    assert latch == {"source_id": "s1.txt", "segment_index": 2, "reviewed": True}

    # A SECOND exhaustion of segment 2 itself, however, is auto-quarantined --
    # the per-segment termination guarantee still holds.
    code3 = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload3 = json.loads(capsys.readouterr().out.strip())
    assert code3 == 0
    assert payload3["quarantine_status"] == "already_reviewed"


def test_missing_validation_report_is_reported_honestly_not_fabricated(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = quarantine_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "no validation report available" in payload["review_brief"]


def test_output_is_exactly_one_json_line_on_success(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    quarantine_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1
