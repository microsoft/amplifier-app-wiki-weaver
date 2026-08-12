"""reweave_bound: bounded retry for THIS (source, segment) pair,
identity-gated against a stale attempts file from a previous source OR a
previous segment of the same source (see module docstring's SEGMENT-SCOPED
section, docs/KNOWN_ISSUES.md #3)."""

from __future__ import annotations

import json

from wiki_weaver.ingest import reweave_bound
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed(wiki_root, source_id: str = "s1.txt", segment: dict | None = None) -> WikiRoot:
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(source_id, encoding="utf-8")
    if segment is not None:
        wr.current_segment_file.write_text(json.dumps(segment), encoding="utf-8")
    return wr


def test_retry_then_give_up_at_max(wiki_root, capsys):
    wr = _seed(wiki_root)

    code1 = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert code1 == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"

    code2 = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert code2 == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"

    code3 = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert code3 == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "give_up"

    state = json.loads(wr.reweave_attempts_file.read_text(encoding="utf-8"))
    assert state == {"source_id": "s1.txt", "segment_index": 1, "attempts": 3}


def test_empty_string_max_falls_back_to_default(wiki_root, capsys):
    """Regression (bare-$var substitution defect): pipeline/ingest.dot passes
    --max via bare $max_reweave substitution. When max_reweave is absent from
    context, the engine leaves the literal token in place and bash's
    unset-variable expansion resolves it to an empty string -- so this CLI
    receives --max "" (never a missing flag). It must apply the default (2),
    not crash with an int() ValueError."""
    wr = _seed(wiki_root)
    code = reweave_bound.main(["--wiki-root", str(wr.root), "--max", ""])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"
    state = json.loads(wr.reweave_attempts_file.read_text(encoding="utf-8"))
    assert state == {"source_id": "s1.txt", "segment_index": 1, "attempts": 1}


def test_stale_attempts_from_previous_source_is_reset_not_hard_failed(wiki_root, capsys):
    wr = _seed(wiki_root, "s1.txt")
    wr.reweave_attempts_file.parent.mkdir(parents=True, exist_ok=True)
    wr.reweave_attempts_file.write_text(json.dumps({"source_id": "OLD-SOURCE", "attempts": 2}), encoding="utf-8")

    code = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert code == 0
    # Fresh source -> first attempt, not treated as attempt 3 of the old one.
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"
    state = json.loads(wr.reweave_attempts_file.read_text(encoding="utf-8"))
    assert state == {"source_id": "s1.txt", "segment_index": 1, "attempts": 1}


def test_stale_attempts_from_previous_segment_of_same_source_is_reset_not_carried_over(wiki_root, capsys):
    """THE fix (docs/KNOWN_ISSUES.md #3): a counter already exhausted by an
    EARLIER segment of the SAME source must not carry over to a LATER
    segment -- each segment is its own unit of work (DESIGN.md \u00a75) and gets
    its own full retry budget, exactly like a freshly-selected source does.

    Before this fix, segment 2 inheriting segment 1's exhausted count meant
    a single failed validate on segment 2 immediately exceeded --max --
    confirmed against a real 73-source production run (segment 1: 0/73
    quarantines; segment 2+: 14 quarantines vs 4 accepts)."""
    wr = _seed(wiki_root, "s1.txt", segment={"index": 1, "total": 2})
    # Segment 1 already exhausted (attempts == max).
    wr.reweave_attempts_file.write_text(
        json.dumps({"source_id": "s1.txt", "segment_index": 1, "attempts": 2}), encoding="utf-8"
    )

    # Now segment 2 of the SAME source runs reweave_bound for the first time.
    wr.current_segment_file.write_text(json.dumps({"index": 2, "total": 2}), encoding="utf-8")
    code = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert code == 0
    # Fresh segment -> first attempt, NOT treated as attempt 3 of segment 1's count.
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"
    state = json.loads(wr.reweave_attempts_file.read_text(encoding="utf-8"))
    assert state == {"source_id": "s1.txt", "segment_index": 2, "attempts": 1}


def test_unsegmented_caller_defaults_to_segment_1_unchanged_behavior(wiki_root, capsys):
    """A caller that never ran segment_source --select (evals/arms/ingest-b.dot,
    ingest-c.dot, or a fresh checkout) has no current_segment_file -- must
    behave exactly as before this fix (segment index defaults to 1, stable
    across repeated calls)."""
    wr = _seed(wiki_root)  # no segment file written
    reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    capsys.readouterr()
    reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    capsys.readouterr()
    code = reweave_bound.main(["--wiki-root", str(wr.root), "--max", "2"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "give_up"
    state = json.loads(wr.reweave_attempts_file.read_text(encoding="utf-8"))
    assert state == {"source_id": "s1.txt", "segment_index": 1, "attempts": 3}
    assert code == 0
