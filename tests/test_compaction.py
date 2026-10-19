"""A model step whose history was compacted is recorded against the source.

Under the default worker, context-simple trims the agent's history when the prompt nears
its budget (200K advertised for Sonnet without enable_1m_context). The trim is silent: the
step still succeeds. V4 reads it back from the engine's own per-session log in the run
directory - consecutive request sizes (input_tokens + cache_write_tokens) only grow inside
one session unless history was removed. Across 157 Stage 1 brief/write sessions this count
matched the hosted context:compaction records exactly. Fixtures are trimmed copies of two
real Stage 1 writer sessions (4 compactions, none).
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from wiki_weaver.steps import count_compactions, node_compactions

FIX = Path(__file__).parent / "fixtures" / "engine-sessions"


def _start(p: Path) -> float:
    first = json.loads(p.read_text().splitlines()[0])
    return datetime.fromisoformat(first["timestamp"]).timestamp()


def test_counts_compactions_in_a_real_compacted_session() -> None:
    assert count_compactions(FIX / "compacted-4.jsonl") == 4


def test_clean_session_counts_zero() -> None:
    assert count_compactions(FIX / "clean.jsonl") == 0


def test_missing_or_garbled_log_counts_zero(tmp_path: Path) -> None:
    assert count_compactions(tmp_path / "nope.jsonl") == 0
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"event": "provider:response", "data": {}}\nnot json\n')
    assert count_compactions(bad) == 0


def _engine(run_dir: Path, node: str, sid: str, fixture: str) -> None:
    d = run_dir / "engine" / node / "sessions" / sid
    d.mkdir(parents=True)
    shutil.copy2(FIX / fixture, d / "events.jsonl")


def test_node_compactions_counts_only_this_sources_sessions(tmp_path: Path) -> None:
    run = tmp_path / "batch-01"
    _engine(run, "write", "engine-a", "compacted-4.jsonl")  # 2026-10-09T22:26
    _engine(run, "write", "engine-b", "clean.jsonl")  # 2026-10-10T00:33
    _engine(run, "brief", "engine-c", "clean.jsonl")
    _engine(run, "write", "not-an-engine-session", "clean.jsonl")
    (run / "engine/write/sessions/not-an-engine-session/events.jsonl").write_text(
        '{"event": "session:start", "data": {}}\n'
    )
    a = _start(FIX / "compacted-4.jsonl")
    b = _start(FIX / "clean.jsonl")
    # a source that started before both sessions owns both
    assert node_compactions(run, since=a - 1) == {"brief": 0, "write": 4}
    # a later source owns only the later sessions
    assert node_compactions(run, since=b - 1) == {"brief": 0, "write": 0}
    assert node_compactions(tmp_path / "no-engine", since=0) == {"brief": 0, "write": 0}


def _now_session(src: Path, dest: Path) -> None:
    """Copy a fixture session with its start moved to now, as if this source started it."""
    lines = src.read_text().splitlines()
    first = json.loads(lines[0])
    first["timestamp"] = datetime.now(UTC).isoformat()
    dest.parent.mkdir(parents=True)
    dest.write_text("\n".join([json.dumps(first), *lines[1:]]) + "\n")


def test_commit_records_compactions_against_the_source(corpus: Path, tmp_path: Path) -> None:
    from test_steps import ORCHARD, cite, fake_brief, page, step

    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    assert step(corpus, "page_select") == (0, "ok")
    _now_session(FIX / "compacted-4.jsonl", run / "engine/write/sessions/engine-w/events.jsonl")
    _now_session(FIX / "clean.jsonl", run / "engine/brief/sessions/engine-b/events.jsonl")
    sel = (corpus / ".wiki/work/selected.txt").read_text().split()
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / sel[0]).write_text(page("Orchard Sync", [ORCHARD], f"- Rollout Friday {q}\n"))
    (corpus / sel[1]).write_text(page("Orchard rollout", [ORCHARD], f"## Plan\n- Friday {q}\n"))
    assert step(corpus, "checks") == (0, "pass")
    assert step(corpus, "commit", str(run)) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["source"] == ORCHARD and row["compactions"] == {"brief": 0, "write": 4}
    changes = json.loads((run / "changes.jsonl").read_text().splitlines()[-1])
    assert changes["compactions"] == {"brief": 0, "write": 4}


def test_result_json_carries_compactions_per_source() -> None:
    from wiki_weaver import result as rs

    rows = [
        {
            "source": "a.md",
            "status": "converged",
            "converged": True,
            "compactions": {"brief": 0, "write": 2},
        }
    ]
    data = rs.build(run_id="r", status="final", rows=rows, errored=[])
    assert [
        {k: v for k, v in x.items() if k != "ingress_truncations"} for x in data["sources"]
    ] == [{"name": "a.md", "status": "converged", "compactions": {"brief": 0, "write": 2}}]


# The 128 KiB ingress cap -------------------------------------------------------------
# context-simple admits at most 128 KiB (UTF-8) of any one tool result and drops the rest
# behind a "[tool-result truncated at ingress ...]" marker. The marker is never logged (not in
# the run directory, and context-simple's own context:tool_result_ingress_truncated event is
# not in the hosted logs either), so V4 applies the cap's rule to the tool:post result the
# engine does log: the result as the model receives it (JSON for dict/list output) over
# 128 KiB means the model saw only its head.


def _read_session(tmp_path: Path, content_bytes: int, name: str = "s.jsonl") -> Path:
    lines = (FIX / "read-pages.jsonl").read_text().splitlines()
    post = json.loads(lines[1])
    post["data"]["result"]["output"]["content"] = "x" * content_bytes
    p = tmp_path / name
    p.write_text("\n".join([lines[0], json.dumps(post)]) + "\n")
    return p


def test_tool_result_over_128_kib_counts_as_cut(tmp_path: Path) -> None:
    from wiki_weaver.steps import count_ingress_truncations

    assert count_ingress_truncations(_read_session(tmp_path, 140_000)) == 1


def test_tool_result_under_128_kib_is_whole(tmp_path: Path) -> None:
    from wiki_weaver.steps import count_ingress_truncations

    assert count_ingress_truncations(_read_session(tmp_path, 120_000)) == 0
    assert count_ingress_truncations(FIX / "compacted-4.jsonl") == 0  # no tool:post kept
    assert count_ingress_truncations(tmp_path / "missing.jsonl") == 0


def test_commit_records_ingress_cuts_against_the_source(corpus: Path, tmp_path: Path) -> None:
    from test_steps import ORCHARD, cite, fake_brief, page, step

    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    assert step(corpus, "page_select") == (0, "ok")
    big = _read_session(tmp_path, 200_000, "big.jsonl")
    _now_session(big, run / "engine/write/sessions/engine-w/events.jsonl")
    _now_session(FIX / "clean.jsonl", run / "engine/brief/sessions/engine-b/events.jsonl")
    sel = (corpus / ".wiki/work/selected.txt").read_text().split()
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / sel[0]).write_text(page("Orchard Sync", [ORCHARD], f"- Rollout Friday {q}\n"))
    (corpus / sel[1]).write_text(page("Orchard rollout", [ORCHARD], f"## Plan\n- Friday {q}\n"))
    assert step(corpus, "checks") == (0, "pass")
    assert step(corpus, "commit", str(run)) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["ingress_truncations"] == {"brief": 0, "write": 1}
    assert row["compactions"] == {"brief": 0, "write": 0}


def test_result_json_carries_ingress_cuts_per_source() -> None:
    from wiki_weaver import result as rs

    rows = [
        {
            "source": "a.md",
            "status": "converged",
            "converged": True,
            "compactions": {"brief": 0, "write": 0},
            "ingress_truncations": {"brief": 0, "write": 2},
        }
    ]
    src = rs.build(run_id="r", status="final", rows=rows, errored=[])["sources"][0]
    assert src["ingress_truncations"] == {"brief": 0, "write": 2}
