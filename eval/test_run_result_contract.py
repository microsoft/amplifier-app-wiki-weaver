# pyright: reportMissingImports=false
"""Integration tests for the run-result contract (result.json + exit codes).

THE CHANGE THIS PROVES: every ingest run -- lib.ingest()'s single-file and
drain paths, and engine_runner.run_ingest()'s tool path -- now ends with a
machine-readable ``<run>/result.json`` (verdict + counts + named gate blocks
+ engine errors), an honest one-line headline, and (for lib.ingest) an exit
code following the documented contract in wiki_weaver/run_result.py.

THE INVARIANT UNDER TEST (the incident fix): a run where sources were
attempted but 0 converged must NEVER report verdict "converged" and must
NEVER exit 0 -- previously an all-gate-blocked drain exited 0 and printed an
all-green summary for a week.

MOCKING STRATEGY (same conventions as eval/test_ingest_drain.py and
eval/test_gate_advisory_mode.py): run_inner()/run_pipeline() are mocked (no
real engine/LLM calls); the duplicate-page gate runs REAL over real files;
the overview re-weave gate is stubbed to pass (covered by test_reweave.py).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# lib.ingest() pulls in the attractor engine deps at call time. Skip cleanly
# in lightweight CI -- same convention as eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402
import wiki_weaver.grading as grading  # noqa: E402
import wiki_weaver.lib as lib  # noqa: E402
import wiki_weaver.retention as retention  # noqa: E402
import wiki_weaver.reweave as reweave  # noqa: E402
from wiki_weaver.lib import INBOX, SOURCES  # noqa: E402
from wiki_weaver.run_result import (  # noqa: E402
    EXIT_BLOCKED,
    EXIT_EMPTY,
    EXIT_ERRORED,
    EXIT_FAILED,
    EXIT_OK,
)


@pytest.fixture(autouse=True)
def _advisory_default_env(monkeypatch):
    """Advisory is the default mode; enforce tests set the hatch explicitly."""
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)


@pytest.fixture(autouse=True)
def _bypass_reweave_gate(monkeypatch):
    """Stub the overview re-weave gate (orthogonal; eval/test_reweave.py)."""
    monkeypatch.setattr(
        "wiki_weaver.reweave.reweave_overview_if_needed",
        lambda *_a, **_kw: reweave.ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="stub",
            final_report="stub",
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers (pattern copied from eval/test_gate_advisory_mode.py)
# ---------------------------------------------------------------------------


def _make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    return wiki


def _seed_inbox(inbox: Path, name: str) -> Path:
    p = inbox / name
    p.write_text(f"# {name}\n\nUnique body text for {name}.\n", encoding="utf-8")
    old = time.time() - 10  # past the 2-s drain debounce
    os.utime(p, (old, old))
    return p


def _fake_result(converged: bool = True, status: str = "success") -> SimpleNamespace:
    return SimpleNamespace(
        converged=converged,
        status=status if converged else "failed",
        failure_reason=None if converged else "did not converge",
        logs_dir=Path("/tmp/fake_logs"),
        notes="",
        advisories=[],
    )


def _load_result(wiki: Path) -> dict:
    """Load the single result.json this run wrote under .wiki/runs/."""
    paths = sorted((wiki / ".wiki" / "runs").glob("ingest-*/result.json"))
    assert len(paths) == 1, f"expected exactly one result.json, found: {paths}"
    return json.loads(paths[0].read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Drain-path scenarios (lib.ingest)
# ---------------------------------------------------------------------------


def test_all_converge_verdict_converged_exit_0(tmp_path: Path, capsys) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(),
    ):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK
    result = _load_result(wiki)
    assert result["verdict"] == "converged"
    assert result["counts"] == {
        "total": 2,
        "converged": 2,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    assert result["advisories"] == []
    assert result["blocked"] == []
    assert result["errored"] == []
    out = capsys.readouterr().out
    assert "ingest CONVERGED: 2/2 converged" in out
    assert "run result:" in out


def test_partial_verdict_partial_exit_0(tmp_path: Path, capsys) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    def mock_run(src, *a, **kw):
        return _fake_result(converged=(src.name == "a.md"))

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK, "partial (>=1 converged, no blocked/errored) exits 0"
    result = _load_result(wiki)
    assert result["verdict"] == "partial"
    assert result["counts"]["total"] == 2
    assert result["counts"]["converged"] == 1
    assert result["counts"]["failed"] == 1
    assert "ingest PARTIAL: 1/2 converged, 1 failed" in capsys.readouterr().out


def test_all_fail_THE_invariant(tmp_path: Path, capsys) -> None:
    """THE incident invariant: attempted > 0, converged == 0 -> verdict is
    NOT converged and the exit code is NOT 0."""
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(converged=False),
    ):
        rc = lib.ingest(wiki)

    result = _load_result(wiki)
    assert result["verdict"] != "converged", "0-converged run must not read as success"
    assert result["verdict"] == "failed"
    assert rc != 0, "0-converged run must never exit 0"
    assert rc == EXIT_FAILED
    assert result["counts"]["converged"] == 0
    assert result["counts"]["total"] == 2
    out = capsys.readouterr().out
    assert "ingest FAILED: 0/2 converged" in out


def test_engine_error_verdict_errored_exit_1(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")

    def mock_run(*a, **kw):
        raise RuntimeError("simulated engine failure")

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib.ingest(wiki)

    assert rc == EXIT_ERRORED
    result = _load_result(wiki)
    assert result["verdict"] == "errored"
    assert result["errored"], "errored record must carry the reason"
    assert "simulated engine failure" in result["errored"][0]["reason"]


def test_advisory_fired_does_not_downgrade_verdict(tmp_path: Path) -> None:
    """Advisory mode (the default): a duplicate-page hit is SURFACED in
    result.json's advisories but the verdict stays converged and exit 0."""
    wiki = _make_wiki(tmp_path)
    # The known false-positive pair the duplicate-page heuristic fires on.
    (wiki / "gpt-5.md").write_text("# GPT-5\n\nModel page.\n", encoding="utf-8")
    (wiki / "gpt-5-1.md").write_text("# GPT-5.1\n\nModel page.\n", encoding="utf-8")
    _seed_inbox(wiki / INBOX, "s1.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(),
    ):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK, "an advisory alone must never change the exit code"
    result = _load_result(wiki)
    assert result["verdict"] == "converged", "advisories do not downgrade the verdict"
    assert any("duplicate-page" in a for a in result["advisories"])
    assert result["blocked"] == []


def test_enforce_blocked_verdict_blocked_gate_named(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Enforce mode: the block surfaces as verdict 'blocked' (exit 4) with the
    GATE NAMED in result.json.blocked[] and in the headline -- never a generic
    'module/hook failed to load' style error line (the mislabel fix)."""
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki = _make_wiki(tmp_path)
    (wiki / "gpt-5.md").write_text("# GPT-5\n\nModel page.\n", encoding="utf-8")
    (wiki / "gpt-5-1.md").write_text("# GPT-5.1\n\nModel page.\n", encoding="utf-8")
    _seed_inbox(wiki / INBOX, "s1.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(),
    ):
        rc = lib.ingest(wiki)

    assert rc == EXIT_BLOCKED
    result = _load_result(wiki)
    assert result["verdict"] == "blocked"
    assert result["blocked"], "enforce block must produce a structured record"
    record = result["blocked"][0]
    assert record["gate"] == "duplicate-page"
    assert record["scope"] == "wiki"
    assert record["offending_items"], "the offending pages must be listed"
    out = capsys.readouterr().out
    assert "ingest BLOCKED (enforce):" in out
    assert "duplicate-page" in out


def test_empty_inbox_verdict_empty_exit_3(tmp_path: Path, capsys) -> None:
    wiki = _make_wiki(tmp_path)

    rc = lib.ingest(wiki)  # inbox exists but is empty; engine never touched

    assert rc == EXIT_EMPTY
    result = _load_result(wiki)
    assert result["verdict"] == "empty"
    assert result["counts"]["total"] == 0
    assert "ingest EMPTY" in capsys.readouterr().out


def test_result_write_failure_does_not_break_the_run(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """FAIL-SOFT: a result.json write failure must not change the run's
    outcome or exit code -- only warn. Breaks ONLY run_result's own JSON
    serialization (its module-local ``json`` binding), so the run itself is
    untouched and the real fail-soft catch inside write_result_json() is
    exercised end-to-end."""
    import wiki_weaver.run_result as run_result

    class _BrokenJson:
        @staticmethod
        def dumps(*_a, **_kw):
            raise OSError("simulated serialization/write failure")

    monkeypatch.setattr(run_result, "json", _BrokenJson)

    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(),
    ):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK, "an observability write failure must not change the outcome"
    assert not list((wiki / ".wiki" / "runs").glob("ingest-*/result.json"))
    out = capsys.readouterr().out
    assert "WARNING" in out and "result.json" in out
    assert "ingest CONVERGED: 1/1 converged" in out, "headline still prints"


# ---------------------------------------------------------------------------
# Single-file path scenarios (lib.ingest --source)
# ---------------------------------------------------------------------------


def test_single_file_enforce_retention_block_names_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki = _make_wiki(tmp_path)
    (wiki / "page.md").write_text("# Page\n\nOriginal claim.\n", encoding="utf-8")
    src = _seed_inbox(wiki / INBOX, "unrelated.md")

    # Same faking convention as eval/test_gate_advisory_mode.py: only
    # check_retention() is faked; the REAL enforce_retention_gate()
    # orchestration runs unmodified.
    monkeypatch.setattr(
        retention,
        "check_retention",
        lambda before_dir, after_wiki, judge_fn=None: retention.RetentionGateResult(
            pages=[
                retention.PageRetentionOutcome(
                    page="page.md",
                    status="confirmed_loss",
                    silently_lost=[{"claim_quote": "Original claim."}],
                )
            ]
        ),
    )

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(),
    ):
        rc = lib.ingest(wiki, source=src)

    assert rc == EXIT_BLOCKED
    result = _load_result(wiki)
    assert result["verdict"] == "blocked"
    assert result["blocked"][0]["gate"] == "claim-retention"
    assert result["blocked"][0]["scope"] == "source"


def test_single_file_failed_source_exits_5(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "a.md")

    with patch(
        "wiki_weaver.engine_runner.run_inner",
        side_effect=lambda *a, **kw: _fake_result(converged=False),
    ):
        rc = lib.ingest(wiki, source=src)

    assert rc == EXIT_FAILED
    result = _load_result(wiki)
    assert result["verdict"] == "failed"


# ---------------------------------------------------------------------------
# Tool path (engine_runner.run_ingest)
# ---------------------------------------------------------------------------


def _fake_pipeline_result(status: str = "success"):
    from amplifier_module_pipeline_runner import PipelineResult

    return PipelineResult(
        status=status,
        notes="ok",
        logs_dir=Path("/tmp"),
        raw="{}",
        failure_reason=None,
    )


def _isolate_run_ingest_gates(monkeypatch) -> None:
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snap, **kw: retention.RetentionGateDecision(
            action="proceed", message=""
        ),
    )
    monkeypatch.setattr(grading, "no_duplicate_pages", lambda wiki: [])
    monkeypatch.setattr(
        reweave,
        "reweave_overview_if_needed",
        lambda wiki_dir: reweave.ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="",
            final_report="",
        ),
    )


def test_run_ingest_writes_result_json(monkeypatch, tmp_path: Path, capsys) -> None:
    """The tool/agent path also ends with a result.json in ITS logs dir --
    counts derived from the ledger/_failed deltas the engine wrote."""
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    inbox = wiki_dir / INBOX
    inbox.mkdir()
    _seed_inbox(inbox, "a.md")
    _isolate_run_ingest_gates(monkeypatch)

    async def fake_run_pipeline(dot_source: str, **kwargs):
        # Simulate the engine archiving the one source: a ledger line appears.
        ledger = wiki_dir / ".wiki" / ".processed.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"source": "a.md", "converged": True}) + "\n")
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)

    result = er.run_ingest(wiki_dir)

    path = result.logs_dir / "result.json"
    assert path.is_file(), "run_ingest must write result.json into its logs dir"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["verdict"] == "converged"
    assert payload["counts"]["converged"] == 1
    out = capsys.readouterr().out
    assert "ingest CONVERGED: 1/1 converged" in out
    assert "run result:" in out
    # The run-events sink never loaded here (run_pipeline mocked) -- the
    # fail-soft WARNING must be loud, not silent.
    assert "hook-run-events" in out


def test_run_ingest_enforce_block_named_in_result(monkeypatch, tmp_path: Path) -> None:
    """Enforce-mode gate block on the tool path: result.json.blocked[] names
    the gate (mislabel fix for the run_ingest wiring site)."""
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    inbox = wiki_dir / INBOX
    inbox.mkdir()
    _seed_inbox(inbox, "a.md")
    _isolate_run_ingest_gates(monkeypatch)
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snap, **kw: retention.RetentionGateDecision(
            action="block_confirmed_loss",
            message="claim-retention gate: SILENTLY_LOST claim(s) detected",
        ),
    )

    async def fake_run_pipeline(dot_source: str, **kwargs):
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)

    result = er.run_ingest(wiki_dir)

    assert result.converged is False
    payload = json.loads((result.logs_dir / "result.json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "blocked"
    assert payload["blocked"][0]["gate"] == "claim-retention"
    assert payload["blocked"][0]["scope"] == "run"


def test_run_ingest_engine_exception_still_writes_result(
    monkeypatch, tmp_path: Path
) -> None:
    """An engine crash re-raises unchanged BUT result.json (verdict errored)
    is written first so headless callers get a machine-readable outcome."""
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    (wiki_dir / INBOX).mkdir()
    _isolate_run_ingest_gates(monkeypatch)

    async def fake_run_pipeline(dot_source: str, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)

    with pytest.raises(RuntimeError, match="engine exploded"):
        er.run_ingest(wiki_dir)

    paths = sorted((wiki_dir / ".wiki" / "runs").glob("ingest-*/result.json"))
    assert len(paths) == 1
    payload = json.loads(paths[0].read_text(encoding="utf-8"))
    assert payload["verdict"] == "errored"
    assert "engine exploded" in payload["errored"][0]["reason"]
