# pyright: reportMissingImports=false
"""Durability tests: incremental result.json + crash-safe drain resume.

THE CHANGES THIS PROVES (P0 items from the post-incident improvement plan):

1. **Incremental result.json** -- the DRAIN path rewrites
   ``<run>/result.json`` ATOMICALLY (tmp + os.replace) after EVERY source
   disposition, with ``status: "in_progress"`` and running counts, flipping
   to ``status: "final"`` at end-of-run. A crash mid-drain leaves a
   machine-readable snapshot of everything finished so far.

2. **Crash-safe resume** -- re-running the drain on the same wiki dir (no
   flag) adopts a crashed run's ``in_progress`` snapshot, skips
   ledger-completed sources, reprocesses the orphaned in-flight source
   (which never left ``_inbox/``), and finalizes a COMBINED result.json.
   Adopted snapshots flip to ``status: "superseded"`` so they can never be
   double-adopted. Ledger always wins over a stray inbox copy.

MOCKING STRATEGY (same conventions as eval/test_run_result_contract.py):
run_inner() is mocked (no real engine/LLM calls); the duplicate-page gate
runs REAL over real files; the overview re-weave gate is stubbed to pass.
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

import wiki_weaver.lib as lib  # noqa: E402
import wiki_weaver.reweave as reweave  # noqa: E402
import wiki_weaver.run_result as run_result  # noqa: E402
from wiki_weaver.lib import INBOX, SOURCES  # noqa: E402
from wiki_weaver.run_result import (  # noqa: E402
    EXIT_EMPTY,
    EXIT_OK,
    STATUS_FINAL,
    STATUS_IN_PROGRESS,
    STATUS_SUPERSEDED,
    build_result,
    find_interrupted_runs,
    merge_source_records,
    write_result_json,
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
# Shared helpers (pattern copied from eval/test_run_result_contract.py)
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


def _all_results(wiki: Path) -> list[tuple[Path, dict]]:
    """All (run_dir, result) pairs under .wiki/runs/, oldest-first."""
    paths = sorted((wiki / ".wiki" / "runs").glob("ingest-*/result.json"))
    return [(p.parent, json.loads(p.read_text(encoding="utf-8"))) for p in paths]


def _load_single_result(wiki: Path) -> dict:
    results = _all_results(wiki)
    assert len(results) == 1, f"expected exactly one result.json, found: {results}"
    return results[0][1]


# ---------------------------------------------------------------------------
# (a) Incremental in_progress snapshots during a multi-source drain
# ---------------------------------------------------------------------------


def test_in_progress_snapshot_after_every_source(tmp_path: Path) -> None:
    """Mid-drain inspection: while source N is being processed, result.json
    already holds an in_progress snapshot of sources 1..N-1's dispositions."""
    wiki = _make_wiki(tmp_path)
    for name in ("a.md", "b.md", "c.md"):
        _seed_inbox(wiki / INBOX, name)

    observed: list[dict | None] = []  # snapshot seen at the START of each source

    def mock_run(src, *a, **kw):
        results = _all_results(wiki)
        observed.append(results[0][1] if results else None)
        return _fake_result()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK
    assert len(observed) == 3

    # Source 1 (a.md): nothing completed yet -> no snapshot required.
    assert observed[0] is None

    # Source 2 (b.md): a.md's completion is already durable.
    snap1 = observed[1]
    assert snap1 is not None
    assert snap1["status"] == STATUS_IN_PROGRESS
    assert snap1["counts"]["converged"] == 1
    assert snap1["counts"]["total"] == 1
    assert snap1["sources"] == [{"name": "a.md", "status": "converged"}]
    # Mid-run verdict = normal verdict rules applied to counts-so-far.
    assert snap1["verdict"] == "converged"

    # Source 3 (c.md): both prior completions durable.
    snap2 = observed[2]
    assert snap2 is not None
    assert snap2["status"] == STATUS_IN_PROGRESS
    assert snap2["counts"]["converged"] == 2
    assert snap2["counts"]["total"] == 2

    # End-of-run write flips to final with full counts.
    final = _load_single_result(wiki)
    assert final["status"] == STATUS_FINAL
    assert final["counts"]["converged"] == 3
    assert final["counts"]["total"] == 3


def test_in_progress_snapshot_covers_failed_dispositions(tmp_path: Path) -> None:
    """The checkpoint fires for failure dispositions too (not just converged)."""
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    observed: list[dict | None] = []

    def mock_run(src, *a, **kw):
        results = _all_results(wiki)
        observed.append(results[0][1] if results else None)
        return _fake_result(converged=False)

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        lib.ingest(wiki)

    snap = observed[1]
    assert snap is not None
    assert snap["status"] == STATUS_IN_PROGRESS
    assert snap["counts"]["failed"] == 1
    assert snap["verdict"] == "failed"


def test_single_file_and_empty_paths_gain_final_status(tmp_path: Path) -> None:
    """Non-drain paths keep their end-only write but carry status: final."""
    wiki = _make_wiki(tmp_path)

    # Empty-inbox drain tick.
    rc = lib.ingest(wiki)
    assert rc == EXIT_EMPTY
    result = _load_single_result(wiki)
    assert result["status"] == STATUS_FINAL
    assert result["verdict"] == "empty"


# ---------------------------------------------------------------------------
# (b) Atomicity of write_result_json (tmp + os.replace)
# ---------------------------------------------------------------------------


def test_write_result_json_atomic_no_tmp_residue(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    result = build_result(
        "r1",
        {
            "total": 0,
            "converged": 0,
            "failed": 0,
            "blocked": 0,
            "errored": 0,
            "skipped": 0,
        },
    )
    path = write_result_json(run_dir, result)
    assert path is not None
    assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "r1"
    assert not (run_dir / ".result.json.tmp").exists(), "tmp file must not linger"


def test_write_result_json_never_leaves_torn_json(tmp_path: Path, capsys) -> None:
    """A failure between tmp-write and replace leaves the PREVIOUS complete
    snapshot intact -- a reader can never observe partial JSON."""
    run_dir = tmp_path / "run"
    counts = {
        "total": 1,
        "converged": 1,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    first = build_result("r1", counts)
    assert write_result_json(run_dir, first) is not None

    # Second write: os.replace blows up AFTER the tmp file was written.
    with patch.object(run_result.os, "replace", side_effect=OSError("disk gone")):
        out = write_result_json(run_dir, build_result("r2", counts))

    assert out is None
    # The visible file is still the FIRST complete, parseable snapshot.
    data = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
    assert data["run_id"] == "r1"
    assert "could not write result.json" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (c) KILL-MID-DRAIN: crash after source 1, before source 2 -> resume
# ---------------------------------------------------------------------------


def test_kill_mid_drain_then_resume_combined(tmp_path: Path, capsys) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    # --- Run 1: a.md converges; the process "dies" starting b.md. ---------- #
    # KeyboardInterrupt models SIGKILL-like death: it is NOT caught by the
    # drain's `except Exception` engine-error handling, so no disposition is
    # recorded for b.md and no end-of-run finalization happens.
    calls_run1: list[str] = []

    def mock_run_crash(src, *a, **kw):
        calls_run1.append(src.name)
        if src.name == "b.md":
            raise KeyboardInterrupt
        return _fake_result()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run_crash):
        with pytest.raises(KeyboardInterrupt):
            lib.ingest(wiki)

    assert calls_run1 == ["a.md", "b.md"]

    # Post-crash disk state: a.md completed durably; b.md is the orphaned
    # in-flight source and NEVER left _inbox/ (claim = completion, not start).
    assert (wiki / SOURCES / "a.md").is_file()
    assert (wiki / INBOX / "b.md").is_file()
    assert "a.md" in lib._processed_sources(wiki)
    assert "b.md" not in lib._processed_sources(wiki)

    crashed = _all_results(wiki)
    assert len(crashed) == 1
    crashed_dir, crashed_result = crashed[0]
    assert crashed_result["status"] == STATUS_IN_PROGRESS
    assert crashed_result["counts"]["converged"] == 1
    assert crashed_result["sources"] == [{"name": "a.md", "status": "converged"}]

    # --- Run 2: plain re-invocation IS the resume (no flag). --------------- #
    calls_run2: list[str] = []

    def mock_run_resume(src, *a, **kw):
        calls_run2.append(src.name)
        return _fake_result()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run_resume):
        rc = lib.ingest(wiki)

    assert rc == EXIT_OK
    # a.md NOT reprocessed (ledger/archive); orphan b.md recovered + processed.
    assert calls_run2 == ["b.md"]
    assert (wiki / SOURCES / "b.md").is_file()
    assert "resuming after interrupted drain" in capsys.readouterr().out

    results = _all_results(wiki)
    assert len(results) == 2
    # Crashed snapshot adopted exactly once: flipped to superseded.
    _, old = next(r for r in results if r[0] == crashed_dir)
    assert old["status"] == STATUS_SUPERSEDED
    assert old["resumed_by"]
    # New run finalized the COMBINED state: counts include the pre-crash a.md.
    new_dir, new = next(r for r in results if r[0] != crashed_dir)
    assert old["resumed_by"] == new["run_id"] == new_dir.name
    assert new["status"] == STATUS_FINAL
    assert new["verdict"] == "converged"
    assert new["counts"] == {
        "total": 2,
        "converged": 2,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    assert {s["name"] for s in new["sources"]} == {"a.md", "b.md"}

    # --- Run 3: no double-adoption. ---------------------------------------- #
    # The superseded snapshot must never be adopted again; with an empty
    # inbox this is a plain nothing-to-do tick.
    rc3 = lib.ingest(wiki)
    assert rc3 == EXIT_EMPTY
    final3 = _all_results(wiki)[-1][1]
    assert final3["verdict"] == "empty"
    assert final3["counts"]["total"] == 0


def test_crash_after_all_sources_empty_inbox_resume_finalizes(tmp_path: Path) -> None:
    """Crash AFTER every source completed (e.g. during the final re-weave):
    the resume finds an empty inbox but must finalize the adopted state, not
    forget it behind a fresh 'empty' verdict."""
    wiki = _make_wiki(tmp_path)
    runs = wiki / ".wiki" / "runs"
    crashed_dir = runs / "ingest-20260101-000000-000000"
    write_result_json(
        crashed_dir,
        build_result(
            "ingest-20260101-000000-000000",
            {
                "total": 2,
                "converged": 2,
                "failed": 0,
                "blocked": 0,
                "errored": 0,
                "skipped": 0,
            },
            sources=[
                {"name": "a.md", "status": "converged"},
                {"name": "b.md", "status": "converged"},
            ],
            status=STATUS_IN_PROGRESS,
        ),
    )

    rc = lib.ingest(wiki)  # empty inbox + crashed snapshot

    assert rc == EXIT_OK, "pre-crash completions must produce the combined verdict"
    results = _all_results(wiki)
    assert len(results) == 2
    old = next(r[1] for r in results if r[0] == crashed_dir)
    assert old["status"] == STATUS_SUPERSEDED
    new = next(r[1] for r in results if r[0] != crashed_dir)
    assert new["status"] == STATUS_FINAL
    assert new["verdict"] == "converged"
    assert new["counts"]["converged"] == 2


# ---------------------------------------------------------------------------
# (d) Ledger wins over a stray inbox copy
# ---------------------------------------------------------------------------


def test_ledger_wins_when_source_in_both_ledger_and_inbox(tmp_path: Path) -> None:
    """A source present in BOTH the ledger and _inbox/ is never re-completed:
    the drain skips it (ledger wins) and the resume merge keeps the real
    pre-crash disposition over the 'skipped' re-encounter."""
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")

    # Crashed snapshot says a.md converged...
    runs = wiki / ".wiki" / "runs"
    crashed_dir = runs / "ingest-20260101-000000-000000"
    write_result_json(
        crashed_dir,
        build_result(
            "ingest-20260101-000000-000000",
            {
                "total": 1,
                "converged": 1,
                "failed": 0,
                "blocked": 0,
                "errored": 0,
                "skipped": 0,
            },
            sources=[{"name": "a.md", "status": "converged"}],
            status=STATUS_IN_PROGRESS,
        ),
    )
    # ...and the ledger agrees (it is the durable source of truth) -- but the
    # file ALSO (somehow) still sits in _inbox/.
    lib._append_ledger(
        wiki,
        {"source": "a.md", "status": "success", "converged": True},
    )

    calls: list[str] = []

    def mock_run(src, *a, **kw):
        calls.append(src.name)
        return _fake_result()

    with patch("wiki_weaver.engine_runner.run_inner", side_effect=mock_run):
        rc = lib.ingest(wiki)

    assert calls == [], "ledger-completed source must never re-enter synthesis"
    assert not (wiki / INBOX / "a.md").exists(), "stray inbox copy cleared"
    assert rc == EXIT_OK
    new = _all_results(wiki)[-1][1]
    # Merge rule: 'skipped' never overwrites the real pre-crash disposition.
    assert new["sources"] == [{"name": "a.md", "status": "converged"}]
    assert new["counts"]["converged"] == 1
    assert new["counts"]["skipped"] == 0


def test_merge_source_records_rules() -> None:
    base = [
        {"name": "a.md", "status": "converged"},
        {"name": "b.md", "status": "not-converged"},
    ]
    updates = [
        {"name": "a.md", "status": "skipped"},  # ledger wins: kept converged
        {"name": "b.md", "status": "converged"},  # real re-attempt: replaces
        {"name": "c.md", "status": "converged"},  # new source: appended
    ]
    merged = merge_source_records(base, updates)
    assert merged == [
        {"name": "a.md", "status": "converged"},
        {"name": "b.md", "status": "converged"},
        {"name": "c.md", "status": "converged"},
    ]


def test_find_interrupted_runs_filters(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    counts = {
        "total": 0,
        "converged": 0,
        "failed": 0,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    write_result_json(
        runs / "ingest-1", build_result("ingest-1", counts, status=STATUS_IN_PROGRESS)
    )
    write_result_json(runs / "ingest-2", build_result("ingest-2", counts))  # final
    write_result_json(
        runs / "ingest-3", build_result("ingest-3", counts, status=STATUS_SUPERSEDED)
    )
    (runs / "ingest-4").mkdir()
    (runs / "ingest-4" / "result.json").write_text(
        "{ torn", encoding="utf-8"
    )  # legacy torn write
    # Pre-#43 legacy run dir with no result.json at all.
    (runs / "ingest-5").mkdir()

    found = find_interrupted_runs(runs)
    assert [d.name for d, _ in found] == ["ingest-1"]

    # exclude_run_id guards against self-adoption.
    assert find_interrupted_runs(runs, exclude_run_id="ingest-1") == []
    # Missing runs dir is fine.
    assert find_interrupted_runs(tmp_path / "nope") == []


# ---------------------------------------------------------------------------
# (e) Fail-soft: checkpoint write failures never break the run
# ---------------------------------------------------------------------------


def test_checkpoint_write_failure_is_fail_soft(tmp_path: Path, capsys) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")

    # Fail ONLY result.json writes: run_result's `os` is the shared global
    # module, so an unconditional patch would also break other atomic
    # writers (e.g. the source registry) -- a different scenario entirely.
    real_replace = os.replace

    def flaky_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("result.json"):
            raise OSError("read-only fs")
        return real_replace(src, dst, *args, **kwargs)

    with (
        patch(
            "wiki_weaver.engine_runner.run_inner",
            side_effect=lambda *a, **kw: _fake_result(),
        ),
        patch.object(run_result.os, "replace", side_effect=flaky_replace),
    ):
        rc = lib.ingest(wiki)

    # Every result.json write (checkpoints AND final) failed, yet the drain
    # completed all sources and the exit code reflects the real outcome.
    assert rc == EXIT_OK
    assert (wiki / SOURCES / "a.md").is_file()
    assert (wiki / SOURCES / "b.md").is_file()
    assert _all_results(wiki) == []
    assert "could not write result.json" in capsys.readouterr().out
