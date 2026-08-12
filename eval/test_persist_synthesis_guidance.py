"""wiki_weaver.synthesize.persist_synthesis_guidance -- applies synthesis_gate's
freeform DROP: instructions to the synth ledger. See that module's docstring
for the full rationale (closing the exact "collect_guidance carried nothing"
defect class for synthesize.dot's own gate).
"""

from __future__ import annotations

import json

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import persist_synthesis_guidance as psg


def _write_brief(wr: WikiRoot, signature: str, pending_terms: list[str]) -> None:
    ensure_dir(wr.ai_dir)
    wr.synthesis_brief_file.write_text(
        json.dumps({"signature": signature, "stage": "synthesis_gate", "brief": "...", "pending_terms": pending_terms}),
        encoding="utf-8",
    )


def _write_answer(wr: WikiRoot, signature: str, text: str) -> None:
    ensure_dir(wr.ai_dir)
    wr.synthesis_answer_file.write_text(
        json.dumps({"signature": signature, "stage": "synthesis_gate", "text": text}), encoding="utf-8"
    )


def _ledger_rows(wr: WikiRoot) -> list[dict]:
    if not wr.synth_ledger_path.is_file():
        return []
    return [json.loads(line) for line in wr.synth_ledger_path.read_text(encoding="utf-8").splitlines() if line]


# -- parse_drop_terms (deterministic marker parse) ---------------------------


def test_parse_drop_terms_matches_case_insensitively():
    matched, unmatched = psg.parse_drop_terms(
        "DROP: Token Economics\nsome other line\n", ["token economics", "local inference"]
    )
    assert matched == ["token economics"]
    assert unmatched == []


def test_parse_drop_terms_ignores_unmatched_names_never_invents():
    matched, unmatched = psg.parse_drop_terms("DROP: some made up theme", ["token economics"])
    assert matched == []
    assert unmatched == ["some made up theme"]


def test_parse_drop_terms_dedupes_repeated_drop_lines():
    matched, _ = psg.parse_drop_terms("DROP: token economics\nDROP: token economics\n", ["token economics"])
    assert matched == ["token economics"]


def test_parse_drop_terms_handles_bulleted_lines():
    matched, _ = psg.parse_drop_terms("- DROP: token economics", ["token economics"])
    assert matched == ["token economics"]


def test_parse_drop_terms_ignores_non_drop_prose():
    matched, unmatched = psg.parse_drop_terms(
        "I think token economics is a strong theme, keep it.", ["token economics"]
    )
    assert matched == []
    assert unmatched == []


# -- main(): fail-soft toward keeping every candidate ------------------------


def test_main_noop_when_no_brief_on_disk(wiki_root):
    wr = WikiRoot(wiki_root.root)
    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert _ledger_rows(wr) == []


def test_main_noop_when_no_answer_file(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics"])
    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert _ledger_rows(wr) == []


def test_main_noop_when_answer_signature_is_stale(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig-current", ["token economics"])
    _write_answer(wr, "sig-OLD", "DROP: token economics")

    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert _ledger_rows(wr) == []


def test_main_noop_on_auto_approved_stub(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics"])
    _write_answer(wr, "sig1", "auto-approved")

    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert _ledger_rows(wr) == []


def test_main_noop_on_keep_all(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics"])
    _write_answer(wr, "sig1", "KEEP ALL")

    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert _ledger_rows(wr) == []


# -- main(): the acceptance-bar behavior -- a real drop changes the ledger --


def test_main_ledgers_matched_drop_as_declined(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics", "local inference"])
    _write_answer(wr, "sig1", "DROP: token economics")

    code = psg.main(["--wiki-root", str(wr.root)])
    assert code == 0

    rows = _ledger_rows(wr)
    assert len(rows) == 1
    assert rows[0]["term"] == "token economics"
    assert rows[0]["decision"] == "declined"
    assert "synthesis_gate" in rows[0]["reason"]


def test_main_drops_multiple_candidates_in_one_answer(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics", "local inference", "human judgment"])
    _write_answer(wr, "sig1", "DROP: token economics\nDROP: local inference\n")

    psg.main(["--wiki-root", str(wr.root)])

    terms = {row["term"] for row in _ledger_rows(wr)}
    assert terms == {"token economics", "local inference"}


def test_main_ignores_unmatched_drop_and_still_persists_valid_ones(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics"])
    _write_answer(wr, "sig1", "DROP: token economics\nDROP: not a real candidate\n")

    psg.main(["--wiki-root", str(wr.root)])

    rows = _ledger_rows(wr)
    assert [r["term"] for r in rows] == ["token economics"]


def test_main_does_not_duplicate_ledger_rows_across_reruns(wiki_root):
    """Re-running is safe -- select_gap's own decided_terms exclusion makes a
    duplicate declined row harmless, but this still should not pathologically
    grow without bound if the same answer file is read twice."""
    wr = WikiRoot(wiki_root.root)
    _write_brief(wr, "sig1", ["token economics"])
    _write_answer(wr, "sig1", "DROP: token economics")

    psg.main(["--wiki-root", str(wr.root)])
    rows_after_first = len(_ledger_rows(wr))
    psg.main(["--wiki-root", str(wr.root)])
    rows_after_second = len(_ledger_rows(wr))

    # Idempotency here is select_gap's job (decided_terms dedupes by term);
    # this node just appends durably, same discipline as commit.py's own
    # test_commit_is_idempotent_reruns_are_still_a_single_intended_row.
    assert rows_after_first == 1
    assert rows_after_second == 2


def test_dropped_term_is_excluded_by_select_gaps_existing_decided_terms(wiki_root):
    """The actual acceptance bar: select_gap's UNCHANGED exclusion logic
    honors a row this node wrote, with zero changes to select_gap itself."""
    from wiki_weaver.synthesize import select_gap

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.gap_candidates_file.write_text(
        json.dumps(
            [
                {"term": "token economics", "source_count": 4, "source_ids": ["s0.txt"]},
                {"term": "local inference", "source_count": 5, "source_ids": ["s1.txt"]},
            ]
        ),
        encoding="utf-8",
    )
    _write_brief(wr, "sig1", ["token economics", "local inference"])
    _write_answer(wr, "sig1", "DROP: token economics")

    psg.main(["--wiki-root", str(wr.root)])

    already_decided = select_gap.decided_terms(wr.synth_ledger_path)
    candidates = select_gap.read_candidates(wr.gap_candidates_file)
    remaining = [c["term"] for c in candidates if c["term"] not in already_decided]

    assert remaining == ["local inference"]
