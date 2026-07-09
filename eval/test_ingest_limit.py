"""Drain-loop ``--limit`` tests (scheduled-ingestion-limit-addendum).

Covers the addendum's counting semantics [C3], off-by-one correctness [C4],
zero/validation [C5], defaults [C6], selection-order preservation [C9a], and
single-file no-op [C9c] -- calling ``wiki_weaver.lib.ingest()`` directly with
a mocked ``run_inner`` so no real LLM/runtime is needed.

See docs/designs/scheduled-ingestion-limit-addendum.md §8 for the test plan
these implement.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# These tests patch wiki_weaver.engine_runner at runtime, which imports the
# attractor engine deps. Skip cleanly in lightweight CI (no @main resolution)
# rather than erroring. Matches eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

from wiki_weaver.lib import INBOX, SOURCES, DrainReport  # noqa: E402
from wiki_weaver.lib import ingest as lib_ingest  # noqa: E402


@pytest.fixture(autouse=True)
def _bypass_reweave_gate(monkeypatch):
    """Stub the overview re-weave gate (wiki_weaver/reweave.py).

    These tests exercise the --limit drain-gate ORCHESTRATION with minimal
    synthetic wikis with no real index.md/overview.md content -- an
    orthogonal concern to the overview quality gate (covered by
    eval/test_reweave.py). Without this stub, grade_overview() would
    legitimately fail on these bare fixtures and attempt a real re-weave.
    """
    from wiki_weaver.reweave import ReweaveGateResult

    monkeypatch.setattr(
        "wiki_weaver.reweave.reweave_overview_if_needed",
        lambda *_a, **_kw: ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="stub: bypassed for --limit drain-gate test",
            final_report="stub: bypassed for --limit drain-gate test",
        ),
    )


# ---------------------------------------------------------------------------
# Shared helpers (mirrors eval/test_ingest_drain.py's conventions)
# ---------------------------------------------------------------------------


def _make_wiki(tmp_path: Path) -> Path:
    """Minimal wiki scaffold that satisfies ingest()'s prerequisite checks."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    return wiki


def _seed_inbox(inbox: Path, name: str, content: str | None = None) -> Path:
    """Write *name* to *inbox* with a backdated mtime (clears the 2-s debounce)."""
    if content is None:
        content = f"# {name}\n\nUnique body text for {name}.\n"
    p = inbox / name
    p.write_text(content, encoding="utf-8")
    old = time.time() - 10
    os.utime(p, (old, old))
    return p


def _seed_binary(inbox: Path, name: str) -> Path:
    """Write a NUL-containing binary file (fails _looks_like_text)."""
    p = inbox / name
    p.write_bytes(b"\x00\x01\x02BINARY\x00")
    old = time.time() - 10
    os.utime(p, (old, old))
    return p


def _seed_duplicate(wiki: Path, inbox: Path, name: str) -> Path:
    """Write a file and pre-register it as already-ingested in the registry."""
    content = f"# Already ingested {name}\n\nBody for {name}.\n"
    p = _seed_inbox(inbox, name, content)
    file_hash = hashlib.sha256(p.read_bytes()).hexdigest()
    registry_path = wiki / ".wiki" / ".sources.json"
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    else:
        registry = {"version": 1, "next_id": 1, "sources": []}
    next_id = registry["next_id"]
    registry["sources"].append(
        {
            "id": next_id,
            "filename": name,
            "hash": file_hash,
            "ingested": True,
            "first_seen": "2026-01-01T00:00:00",
        }
    )
    registry["next_id"] = next_id + 1
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    return p


def _fake_result(converged: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        converged=converged,
        status="success" if converged else "failed",
        failure_reason=None if converged else "did not converge",
        logs_dir=Path("/tmp/fake_logs"),
    )


def _run_inner_counter(order: list[str] | None = None):
    """A mock run_inner that records call order and always converges."""
    calls: list[str] = []

    def _mock(src, wiki_dir, max_cycles, source_id):
        calls.append(src.name)
        if order is not None:
            order.append(src.name)
        return _fake_result(True)

    return calls, _mock


# ---------------------------------------------------------------------------
# Counting semantics [C3]
# ---------------------------------------------------------------------------


def test_limit_counts_only_real_ingests(tmp_path: Path) -> None:
    """Cheap dispositions (dup/binary) never consume the --limit budget."""
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX

    # Alphabetically-first: cheap dispositions, all disposed regardless of cap.
    _seed_binary(inbox, "a_bin1.bin")
    _seed_binary(inbox, "a_bin2.bin")
    _seed_duplicate(wiki, inbox, "a_dup1.md")
    _seed_duplicate(wiki, inbox, "a_dup2.md")
    # Alphabetically-last: the 3 real-eligible sources.
    _seed_inbox(inbox, "b_new1.md")
    _seed_inbox(inbox, "b_new2.md")
    _seed_inbox(inbox, "b_new3.md")

    calls, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=2, report=report)

    assert calls == ["b_new1.md", "b_new2.md"], f"unexpected run_inner calls: {calls}"
    assert report.hit_limit is True

    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == ["b_new3.md"], f"expected only b_new3.md left; got {remaining}"

    # All cheap dispositions were disposed regardless of the cap.
    assert not (inbox / "a_bin1.bin").exists()
    assert not (inbox / "a_bin2.bin").exists()
    assert not (inbox / "a_dup1.md").exists()
    assert not (inbox / "a_dup2.md").exists()


def test_all_duplicates_report_complete_not_capped(tmp_path: Path) -> None:
    """A tick full of duplicates disposes them all cheaply and reports complete."""
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX

    _seed_duplicate(wiki, inbox, "dup1.md")
    _seed_duplicate(wiki, inbox, "dup2.md")
    _seed_duplicate(wiki, inbox, "dup3.md")

    calls, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib_ingest(wiki, limit=1, report=report)

    assert calls == [], "run_inner must not be called for an all-duplicate inbox"
    assert report.hit_limit is False, (
        "a tick full of duplicates must report complete, not capped"
    )
    assert rc == 0
    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == []


# ---------------------------------------------------------------------------
# Off-by-one [C4]
# ---------------------------------------------------------------------------


def test_exactly_N_eligible_reports_complete(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")
    _seed_inbox(inbox, "b.md")
    _seed_inbox(inbox, "c.md")

    calls, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=3, report=report)

    assert len(calls) == 3
    assert report.hit_limit is False
    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == []


def test_N_plus_one_eligible_reports_capped(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")
    _seed_inbox(inbox, "b.md")
    _seed_inbox(inbox, "c.md")
    _seed_inbox(inbox, "d.md")

    calls, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=3, report=report)

    assert len(calls) == 3
    assert report.hit_limit is True
    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == ["d.md"]


# ---------------------------------------------------------------------------
# Validation & zero [C5]
# ---------------------------------------------------------------------------


def test_limit_zero_processes_zero_real_sources(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")
    _seed_inbox(inbox, "b.md")
    _seed_inbox(inbox, "c.md")

    calls, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=0, report=report)

    assert calls == []
    assert report.hit_limit is True
    remaining = sorted(p.name for p in inbox.glob("*") if p.is_file())
    assert remaining == ["a.md", "b.md", "c.md"]


def test_limit_zero_emits_loud_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")

    _, mock_run = _run_inner_counter()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=0)

    out = capsys.readouterr().out
    assert "LIMIT REACHED" in out


def test_limit_none_on_same_inbox_processes_all(tmp_path: Path) -> None:
    """Contrast case for limit=0: limit=None processes everything."""
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")
    _seed_inbox(inbox, "b.md")
    _seed_inbox(inbox, "c.md")

    calls, mock_run = _run_inner_counter()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=None)

    assert len(calls) == 3
    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == []


# ---------------------------------------------------------------------------
# Defaults [C6]
# ---------------------------------------------------------------------------


def test_manual_ingest_default_unlimited(tmp_path: Path) -> None:
    """ingest() with no `limit` argument processes an inbox of N > 10 sources."""
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    n = 15
    for i in range(n):
        _seed_inbox(inbox, f"src{i:02d}.md")

    calls, mock_run = _run_inner_counter()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib_ingest(wiki)  # no limit passed -> unlimited

    assert len(calls) == n
    assert rc == 0
    remaining = [p.name for p in inbox.glob("*") if p.is_file()]
    assert remaining == []


# ---------------------------------------------------------------------------
# Loud signal [C8]
# ---------------------------------------------------------------------------


def test_cap_hit_emits_loud_warn_line(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    _seed_inbox(inbox, "a.md")
    _seed_inbox(inbox, "b.md")

    _, mock_run = _run_inner_counter()
    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=1, report=report)

    out = capsys.readouterr().out
    assert "LIMIT REACHED" in out
    assert report.hit_limit is True


# ---------------------------------------------------------------------------
# Selection order preserved [C9a]
# ---------------------------------------------------------------------------


def test_cap_processes_alphabetically_first_N(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    inbox = wiki / INBOX
    for name in ["z1.md", "z2.md", "a1.md", "a2.md", "a3.md"]:
        _seed_inbox(inbox, name)

    order: list[str] = []
    calls, mock_run = _run_inner_counter(order)
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib_ingest(wiki, limit=2)

    assert order == ["a1.md", "a2.md"], f"expected alphabetical a1,a2; got {order}"
    remaining = sorted(p.name for p in inbox.glob("*") if p.is_file())
    assert remaining == ["a3.md", "z1.md", "z2.md"]


# ---------------------------------------------------------------------------
# Single-file mode [C9c]
# ---------------------------------------------------------------------------


def test_source_mode_ignores_limit(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    # Source file lives outside the wiki (typical single-file invocation).
    source_file = tmp_path / "external.md"
    source_file.write_text("# External Source\n\nBody text.\n", encoding="utf-8")

    calls, mock_run = _run_inner_counter()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib_ingest(wiki, source=str(source_file), limit=0)

    assert rc == 0
    assert len(calls) == 1, "single-file mode always processes exactly the one file"
    assert source_file.exists(), "--source file must remain at its original location"
