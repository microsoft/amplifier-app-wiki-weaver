"""Table-driven check: every subcommand's stdout matches CLI-CONTRACT.md's
sentinel table EXACTLY. This is the routing contract (context.tool.last_line
or parse_json) -- test it literally, per the task's own instruction."""

from __future__ import annotations

import json

from wiki_weaver.ask import load_index, prepare_file_back
from wiki_weaver.ingest import (
    commit,
    drain_bound,
    persist_guidance,
    reweave_bound,
    select_source,
    validate,
)
from wiki_weaver.init import check_bound, read_verdict
from wiki_weaver.lib import WikiRoot, ensure_dir
from wiki_weaver.lint import structural_validate


def test_select_source_sentinels_are_exactly_one_of_three(wiki_root, capsys):
    # no_source
    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "no_source"

    # has_source
    wiki_root.add_source("s1.txt")
    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "has_source"

    # ledger_corrupt
    (wiki_root.root / "ledger.jsonl").write_text("{bad json\n", encoding="utf-8")
    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "ledger_corrupt"


def test_drain_bound_sentinel_is_one_json_line_with_required_keys(capsys):
    drain_bound.main(["--count", "0", "--max-sources", "5"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert set(payload) == {"drain_count", "drain_status"}
    assert payload["drain_status"] in ("continue", "budget_exhausted")


def test_validate_has_no_stdout_sentinel_only_exit_code(wiki_root, capsys):
    code = validate.main(["--wiki-root", str(wiki_root.root), "--out", ".ai/validation-report.md"])
    assert code in (0, 1)
    assert capsys.readouterr().out == ""


def test_persist_guidance_has_no_required_stdout_sentinel(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HUMAN.GATE.TEXT", "steer this way")
    persist_guidance.main(["--out", str(tmp_path / "guidance.md")])
    assert capsys.readouterr().out == ""


def test_reweave_bound_sentinel_is_retry_or_give_up(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    reweave_bound.main(["--wiki-root", str(wr.root), "--max", "1"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "retry"

    reweave_bound.main(["--wiki-root", str(wr.root), "--max", "1"])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "give_up"


def test_commit_accept_sentinel_is_one_json_line_with_ingested_count(wiki_root, capsys):
    wiki_root.add_source("s1.txt")
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "0",
    ]  # fmt: skip
    commit.main(argv)
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    payload = json.loads(out)
    assert set(payload) == {"ingested_count"}


def test_commit_skip_has_no_required_stdout_sentinel(wiki_root, capsys):
    wiki_root.add_source("s1.txt")
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "skip",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# init.dot, ask.dot, lint.dot sentinels
# ---------------------------------------------------------------------------


def test_read_verdict_sentinel_is_enough_or_not_enough(tmp_path, capsys):
    path = tmp_path / "init-verdict.txt"

    read_verdict.main(["--verdict-file", str(path)])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "not_enough"

    path.write_text("enough", encoding="utf-8")
    read_verdict.main(["--verdict-file", str(path)])
    assert capsys.readouterr().out.strip().splitlines()[-1] == "enough"


def test_check_bound_sentinel_is_one_json_line_with_required_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    check_bound.main(["--count", "0", "--max-rounds", "5"])
    payload = json.loads(capsys.readouterr().out.strip())
    assert set(payload) == {"interview_rounds", "bound_status"}
    assert payload["bound_status"] in ("ok", "hit")


def test_ask_load_index_has_no_required_stdout_sentinel(wiki_root, monkeypatch, capsys):
    monkeypatch.setenv("QUESTION", "anything")
    wr = WikiRoot(wiki_root.root)
    load_index.main(["--wiki-root", str(wr.root), "--out", ".ai/candidate-pages.json"])
    # Informational only, per CLI-CONTRACT.md's sentinel summary table.
    assert capsys.readouterr().out != ""  # informational stdout is fine, just not routed on


def test_ask_prepare_file_back_sentinel_is_one_json_line_with_restrict_to_sources(wiki_root, capsys, tmp_path):
    wr = WikiRoot(wiki_root.root)
    answer_path = tmp_path / "answer.md"
    answer_path.write_text("An answer.\n", encoding="utf-8")

    prepare_file_back.main(["--wiki-root", str(wr.root), "--answer-file", str(answer_path)])
    out = capsys.readouterr().out.strip()
    assert len(out.splitlines()) == 1
    payload = json.loads(out)
    assert set(payload) == {"restrict_to_sources"}


def test_lint_structural_validate_sentinel_is_structural_ok_or_bad(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = structural_validate.main(["--wiki-root", str(wr.root), "--out", ".ai/structural-report.md"])
    assert code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "structural_ok"
