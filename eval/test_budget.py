"""budget: exit code IS the routing signal, and cost.jsonl must be durable
before the process exits on EITHER branch."""

from __future__ import annotations

import json
import time

from wiki_weaver.ingest import budget
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed(wiki_root, start_offset_s: float) -> WikiRoot:
    wiki_root.add_source("s1.txt", "some source content\n")
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    wr.source_start_file.write_text(str(int(time.time() - start_offset_s)), encoding="utf-8")
    return wr


def test_within_budget_exits_zero_and_prints_sentinel(wiki_root, monkeypatch, capsys):
    monkeypatch.delenv("PER_SOURCE_TIME_CEILING", raising=False)
    wr = _seed(wiki_root, start_offset_s=1)

    code = budget.main(["--wiki-root", str(wr.root), "--append", "cost.jsonl"])
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert out.splitlines()[-1] == "within_budget"
    lines = wr.cost_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["source_id"] == "s1.txt"


def test_over_budget_exits_one_but_still_writes_cost_jsonl(wiki_root, monkeypatch, capsys):
    monkeypatch.setenv("PER_SOURCE_TIME_CEILING", "1")
    wr = _seed(wiki_root, start_offset_s=5)  # 5s elapsed > 1s ceiling

    code = budget.main(["--wiki-root", str(wr.root), "--append", "cost.jsonl"])

    assert code == 1
    lines = wr.cost_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["source_id"] == "s1.txt"
    assert record["duration_s"] >= 5


def test_missing_ceiling_env_uses_default_900s(wiki_root, monkeypatch, capsys):
    # Explicitly unset -- per contract, must NOT rely on shell ${:-default};
    # our own default (900s) must apply.
    monkeypatch.delenv("PER_SOURCE_TIME_CEILING", raising=False)
    wr = _seed(wiki_root, start_offset_s=2)
    code = budget.main(["--wiki-root", str(wr.root), "--append", "cost.jsonl"])
    assert code == 0


def test_cost_record_has_required_fields(wiki_root, monkeypatch):
    monkeypatch.delenv("PER_SOURCE_TIME_CEILING", raising=False)
    wr = _seed(wiki_root, start_offset_s=1)
    budget.main(["--wiki-root", str(wr.root), "--append", "cost.jsonl"])
    record = json.loads(wr.cost_path.read_text(encoding="utf-8").splitlines()[0])
    for field in (
        "source_id",
        "kind",
        "bytes",
        "duration_s",
        "wiki_pages",
        "pages_touched",
        "tokens_in",
        "tokens_out",
    ):
        assert field in record
