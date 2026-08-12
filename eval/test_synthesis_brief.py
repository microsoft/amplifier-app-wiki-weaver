"""wiki_weaver.synthesize.synthesis_brief -- the self-contained PRE-write
brief for synthesis_gate. See that module's docstring for the full
rationale (the "think about what it all means" gap this closes).
"""

from __future__ import annotations

import json

from wiki_weaver.lib import LedgerCorruptError, WikiRoot, atomic_append_jsonl, ensure_dir
from wiki_weaver.synthesize import synthesis_brief


def _write_candidates(wr: WikiRoot, candidates: list[dict]) -> None:
    ensure_dir(wr.ai_dir)
    wr.gap_candidates_file.write_text(json.dumps(candidates), encoding="utf-8")


def _seed_sources(wiki_root, count: int) -> None:
    for i in range(count):
        wiki_root.add_source(f"s{i}.txt")


def test_pending_candidates_excludes_already_decided_terms(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_candidates(
        wr,
        [
            {"term": "token economics", "source_count": 4, "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"]},
            {
                "term": "local inference",
                "source_count": 5,
                "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt", "s4.txt"],
            },
        ],
    )
    atomic_append_jsonl(wr.synth_ledger_path, {"term": "token economics", "decision": "declined"})

    pending = synthesis_brief.pending_candidates(wr)

    assert [c["term"] for c in pending] == ["local inference"]


def test_pending_candidates_raises_on_corrupt_ledger(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _write_candidates(wr, [{"term": "token economics", "source_count": 4, "source_ids": ["s0.txt"]}])
    ensure_dir(wr.ai_dir)
    wr.synth_ledger_path.write_text("{bad json\n", encoding="utf-8")

    try:
        synthesis_brief.pending_candidates(wr)
        raise AssertionError("expected LedgerCorruptError")
    except LedgerCorruptError:
        pass


def test_compute_signature_is_stable_and_order_independent():
    a = [{"term": "token economics", "source_count": 4}, {"term": "local inference", "source_count": 5}]
    b = [{"term": "local inference", "source_count": 5}, {"term": "token economics", "source_count": 4}]
    assert synthesis_brief.compute_signature(a) == synthesis_brief.compute_signature(b)


def test_compute_signature_changes_when_pending_set_changes():
    a = [{"term": "token economics", "source_count": 4}]
    b = [{"term": "token economics", "source_count": 5}]
    c = [{"term": "token economics", "source_count": 4}, {"term": "local inference", "source_count": 5}]
    sigs = {
        synthesis_brief.compute_signature(a),
        synthesis_brief.compute_signature(b),
        synthesis_brief.compute_signature(c),
    }
    assert len(sigs) == 3


def test_build_brief_lists_term_claim_source_count_and_fraction():
    pending = [
        {
            "term": "token economics",
            "source_count": 4,
            "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"],
            "claim": "Token cost dominates architecture choices.",
        }
    ]
    brief = synthesis_brief.build_brief(pending, total_sources=8)

    assert '"token economics"' in brief
    assert "4 source(s)" in brief
    assert "50%" in brief
    assert "Token cost dominates architecture choices." in brief
    assert "DROP:" in brief
    assert "KEEP ALL" in brief


def test_build_brief_notes_uncounted_candidates_beyond_display_cap():
    pending = [{"term": f"theme-{i}", "source_count": 4, "source_ids": ["s0.txt"]} for i in range(30)]
    brief = synthesis_brief.build_brief(pending, total_sources=10)
    assert "5 more candidate(s) not shown" in brief


def test_main_routes_no_pending_when_candidates_file_absent(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = synthesis_brief.main(["--wiki-root", str(wr.root)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pending_status"] == "no_pending"
    assert not wr.synthesis_brief_file.is_file()


def test_main_routes_no_pending_when_every_candidate_already_decided(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    _write_candidates(wr, [{"term": "token economics", "source_count": 4, "source_ids": ["s0.txt"]}])
    atomic_append_jsonl(wr.synth_ledger_path, {"term": "token economics", "decision": "paged"})

    code = synthesis_brief.main(["--wiki-root", str(wr.root)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pending_status"] == "no_pending"


def test_main_routes_no_pending_on_corrupt_ledger_rather_than_crashing(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    _write_candidates(wr, [{"term": "token economics", "source_count": 4, "source_ids": ["s0.txt"]}])
    ensure_dir(wr.ai_dir)
    wr.synth_ledger_path.write_text("{bad json\n", encoding="utf-8")

    code = synthesis_brief.main(["--wiki-root", str(wr.root)])
    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["pending_status"] == "no_pending"


def test_main_writes_stamped_brief_and_routes_has_pending(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    _seed_sources(wiki_root, 8)
    _write_candidates(
        wr,
        [
            {
                "term": "token economics",
                "source_count": 4,
                "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"],
                "claim": "Cost dominates.",
            }
        ],
    )

    code = synthesis_brief.main(["--wiki-root", str(wr.root)])
    assert code == 0

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["pending_status"] == "has_pending"
    assert "token economics" in payload["synthesis_brief"]

    stamped = json.loads(wr.synthesis_brief_file.read_text(encoding="utf-8"))
    assert stamped["stage"] == "synthesis_gate"
    assert stamped["pending_terms"] == ["token economics"]
    assert isinstance(stamped["signature"], str) and stamped["signature"]
    assert stamped["brief"] == payload["synthesis_brief"]
