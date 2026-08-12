"""Table-driven check: every wiki_weaver.synthesize subcommand's stdout
matches the routing contract literally (context.tool.last_line or
parse_json), same discipline as tests/test_sentinels.py."""

from __future__ import annotations

import json

import pytest

from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.synthesize import commit, iteration_bound, retry_bound, select_gap, write_gap_page


def _write_gap_candidates(wr: WikiRoot, candidates: list[dict]) -> None:
    """Seed select_gap's input directly -- .ai/gap-candidates.json is what
    rank_gap_candidates writes upstream (see pipeline/synthesize.dot);
    select_gap itself no longer computes candidates, so its tests seed this
    file rather than building an n-gram-friendly sources/ fixture."""
    ensure_dir(wr.ai_dir)
    wr.gap_candidates_file.write_text(json.dumps(candidates), encoding="utf-8")


def test_select_gap_sentinel_is_one_of_three(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)

    # no_gap: no candidates at all (rank_gap_candidates wrote an empty list --
    # the legitimate "scan_arguments found nothing" outcome)
    _write_gap_candidates(wr, [])
    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "no_gap"

    # has_gap: rank_gap_candidates already ranked and threshold-filtered
    # this candidate -- select_gap just picks the first undecided one.
    _write_gap_candidates(
        wr,
        [{"term": "token economics", "source_count": 4, "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"]}],
    )
    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_gap"
    assert wr.current_gap_file.is_file()
    gap = json.loads(wr.current_gap_file.read_text(encoding="utf-8"))
    assert gap["term"] == "token economics"

    # ledger_corrupt: malformed synth-ledger.jsonl fails loud
    wr.synth_ledger_path.write_text("{bad json\n", encoding="utf-8")
    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "ledger_corrupt"


def test_select_gap_never_reselects_a_decided_term(wiki_root, capsys):
    """THE resume-gate guarantee: once ledgered (paged or declined), a term
    is never proposed again -- even though it is still present in
    gap-candidates.json."""
    wr = WikiRoot(wiki_root.root)
    _write_gap_candidates(
        wr,
        [{"term": "token economics", "source_count": 4, "source_ids": ["s0.txt", "s1.txt", "s2.txt", "s3.txt"]}],
    )

    from wiki_weaver.lib import atomic_append_jsonl

    atomic_append_jsonl(wr.synth_ledger_path, {"term": "token economics", "decision": "declined"})

    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "no_gap"


def test_select_gap_no_gap_when_candidates_file_absent(wiki_root, capsys):
    """select_gap is only reached after rank_gap_candidates routes
    candidates_ok (see pipeline/synthesize.dot), but an absent candidates
    file must still resolve to the safe, honest no_gap reading rather than
    crashing the resume gate."""
    wr = WikiRoot(wiki_root.root)
    code = select_gap.main(["--wiki-root", str(wr.root)])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "no_gap"


def test_iteration_bound_sentinel_is_one_json_line_with_required_keys(capsys):
    iteration_bound.main(["--count", "0", "--max-iterations", "5"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert set(payload) == {"iteration_count", "iteration_status"}
    assert payload["iteration_status"] in ("continue", "budget_exhausted")

    iteration_bound.main(["--count", "5", "--max-iterations", "5"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["iteration_status"] == "budget_exhausted"


def test_retry_bound_sentinel_is_retry_or_give_up(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics"}), encoding="utf-8")

    retry_bound.main(["--wiki-root", str(wr.root), "--max", "1"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"

    retry_bound.main(["--wiki-root", str(wr.root), "--max", "1"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "give_up"


def test_commit_paged_appends_ledger_row_and_has_no_required_stdout_sentinel(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(
        json.dumps({"term": "token economics", "source_count": 4, "source_ids": ["s0.txt", "s1.txt"]}),
        encoding="utf-8",
    )
    ensure_dir(wr.wiki_dir)
    (wr.wiki_dir / "token-economics.md").write_text(
        '---\ntitle: "Token Economics"\ntype: theme\nsources:\n  - s0.txt\n---\n\nBody.\n', encoding="utf-8"
    )

    code = commit.main(["--wiki-root", str(wr.root), "--decision", "paged"])
    assert code == 0

    rows = [json.loads(line) for line in wr.synth_ledger_path.read_text(encoding="utf-8").splitlines() if line]
    assert rows[-1]["term"] == "token economics"
    assert rows[-1]["decision"] == "paged"
    assert rows[-1]["page"] == "token-economics.md"


def test_commit_declined_appends_ledger_row_with_inferred_reason(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_count": 4}), encoding="utf-8")
    wr.gap_declined_file.write_text("sources only share vocabulary, not a claim\n", encoding="utf-8")

    code = commit.main(["--wiki-root", str(wr.root), "--decision", "declined"])
    assert code == 0

    rows = [json.loads(line) for line in wr.synth_ledger_path.read_text(encoding="utf-8").splitlines() if line]
    assert rows[-1]["term"] == "token economics"
    assert rows[-1]["decision"] == "declined"
    assert "sources only share vocabulary" in rows[-1]["reason"]


def test_commit_declined_reverts_index_link_added_by_write_gap_page(wiki_root, git_available):
    """The give_up path (retry_bound exhausted) reaches commit_declined
    AFTER write_gap_page already linked the term's page from index.md (the
    orphan fix) -- reverting the abandoned page without also reverting that
    link would leave a dangling [[slug]] wikilink in index.md pointing at a
    page that no longer exists: a NEW broken-link defect shipped straight
    into the committed wiki."""
    if not git_available:
        pytest.skip("git not available")
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_page(
        "index.md",
        "---\ntitle: Index\ntype: index\n---\n\n# Index\n\n- [[overview|Overview]]\n",
    )
    wiki_root.init_git()
    wiki_root.commit_all("seed")

    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(
        json.dumps({"term": "token economics", "source_count": 4, "source_ids": ["s0.txt"]}), encoding="utf-8"
    )
    wr.gap_answer_file.write_text("Draft that never structurally converged.\n", encoding="utf-8")

    write_gap_page.main(["--wiki-root", str(wr.root)])
    index_text = (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert "[[token-economics|" in index_text  # sanity: the link was actually added

    code = commit.main(["--wiki-root", str(wr.root), "--decision", "declined"])
    assert code == 0

    assert not (wr.wiki_dir / "token-economics.md").exists()
    index_text_after = (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert "[[token-economics|" not in index_text_after
    assert "## Synthesized Themes" not in index_text_after


def test_commit_is_idempotent_reruns_are_still_a_single_intended_row(wiki_root):
    """Re-running commit for the same already-ledgered term must not crash
    and must not corrupt the ledger -- select_gap's exclusion is what
    actually prevents re-processing; commit itself just appends durably."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_gap_file.write_text(json.dumps({"term": "token economics", "source_count": 4}), encoding="utf-8")
    wr.gap_declined_file.write_text("thin\n", encoding="utf-8")

    commit.main(["--wiki-root", str(wr.root), "--decision", "declined"])
    rows_after_first = wr.synth_ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(rows_after_first) == 1
