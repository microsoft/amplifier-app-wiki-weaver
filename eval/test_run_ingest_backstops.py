# pyright: reportMissingImports=false
"""Regression tests: the run_ingest() (agent-tool-module) path gets the same
claim-retention, duplicate-page, and overview-reweave backstops as
wiki_weaver/lib.py's ingest() CLI/scheduled-ingestion path.

THE GAP THIS PROVES CLOSED: before this fix, wiki_weaver.engine_runner.
run_ingest() -- the function backing the ``wiki_weaver_ingest`` agent tool
(modules/tool-wiki-weaver) -- called run_pipeline() and returned its raw
engine status untouched. An engine run that CONVERGED while silently
dropping prior content, or that left a merge-fragment duplicate page behind
(the "appended instead of fused" failure signature), was reported back to
the calling agent as a plain success -- identical to the CLI-path gap
wiki_weaver/retention.py and wiki_weaver/grading.py's no_duplicate_pages()
already closed for wiki_weaver/lib.py's ingest(). These tests prove
run_ingest() now downgrades exactly those two scenarios to a loud,
actionable failure -- proven via the run_ingest() path SPECIFICALLY, not
just the already-tested lib.ingest() path (see eval/test_claim_retention_backstop.py
and eval/test_grade_synthesis.py for that coverage).

MOCKING STRATEGY:
  - run_pipeline() is mocked (no real engine/LLM call) -- same pattern as
    eval/test_spawn_timeout.py's run_ingest coverage.
  - enforce_retention_gate() is mocked with an injected fake decision -- same
    "test the wiring, not the LLM judge" pattern as
    eval/test_claim_retention_backstop.py's fail-open/fail-closed tests.
  - no_duplicate_pages() is NOT mocked in the duplicate-page test -- it is
    purely deterministic (a glob + regex scan), so that test lets the REAL
    function scan REAL files written to tmp_path, giving genuine end-to-end
    evidence for that half of the gate.
  - reweave_overview_if_needed() is mocked to an immediate pass/fail so each
    test isolates exactly one gate.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# run_ingest() pulls in the attractor engine deps (amplifier_module_pipeline_runner,
# unified_llm). Skip cleanly in lightweight CI (no @main resolution) -- same
# convention as eval/test_spawn_timeout.py and eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402
import wiki_weaver.grading as grading  # noqa: E402
import wiki_weaver.retention as retention  # noqa: E402
import wiki_weaver.reweave as reweave  # noqa: E402


@pytest.fixture(autouse=True)
def _advisory_default_env(monkeypatch):
    """Both gates are ADVISORY by default (never block); the blocking tests
    below set WIKI_WEAVER_ENFORCE_GATES=1 explicitly (the escape hatch --
    see wiki_weaver.grading.gates_enforced()). Clear it here so the advisory
    tests exercise the true default regardless of the developer's shell env."""
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)


def _fake_pipeline_result(status: str = "success") -> Any:
    from amplifier_module_pipeline_runner import PipelineResult

    return PipelineResult(
        status=status,
        notes="ok",
        logs_dir=Path("/tmp"),
        raw="{}",
        failure_reason=None,
    )


def _passing_reweave_result() -> "reweave.ReweaveGateResult":
    return reweave.ReweaveGateResult(
        initial_passed=True,
        attempts=0,
        final_passed=True,
        initial_report="",
        final_report="",
    )


def _passing_retention_decision() -> "retention.RetentionGateDecision":
    return retention.RetentionGateDecision(
        action="proceed", message="claim-retention gate: PASS"
    )


# ---------------------------------------------------------------------------
# Test 1 -- confirmed content loss (claim-retention backstop)
# ---------------------------------------------------------------------------


def test_run_ingest_catches_confirmed_content_loss(monkeypatch, tmp_path: Path) -> None:
    """With the WIKI_WEAVER_ENFORCE_GATES=1 escape hatch set, a converged
    engine run that the retention gate confirms lost a claim must come back
    from run_ingest() as converged=False, not a false success -- the OLD
    (pre-advisory) blocking behavior, now opt-in.
    """
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    (wiki_dir / "concept.md").write_text(
        "# Concept\n\nOriginal grounded claim.\n", encoding="utf-8"
    )

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        # Simulate the engine converging while silently rewriting the page --
        # exactly the incident-fixture failure signature this backstop exists
        # to catch (see eval/fixtures/incident_2026_07/).
        (wiki_dir / "concept.md").write_text(
            "# Concept\n\nRewritten -- original claim is gone.\n", encoding="utf-8"
        )
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)

    calls: dict = {}

    def fake_enforce_retention_gate(wiki, snapshot_dir, **kwargs):
        calls["wiki"] = Path(wiki).resolve()
        calls["snapshot_dir"] = Path(snapshot_dir)
        return retention.RetentionGateDecision(
            action="block_confirmed_loss",
            message=(
                "claim-retention gate: SILENTLY_LOST claim(s) detected in "
                'the re-write -- concept.md: "Original grounded claim."'
            ),
        )

    monkeypatch.setattr(
        retention, "enforce_retention_gate", fake_enforce_retention_gate
    )
    # Isolate: no duplicate pages, overview gate passes -- ONLY the retention
    # backstop is under test here.
    monkeypatch.setattr(grading, "no_duplicate_pages", lambda wiki: [])
    monkeypatch.setattr(
        reweave,
        "reweave_overview_if_needed",
        lambda wiki_dir: _passing_reweave_result(),
    )

    result = er.run_ingest(wiki_dir)

    assert result.converged is False, (
        "run_ingest() must NOT report success when the claim-retention gate "
        "confirms silent content loss"
    )
    assert "SILENTLY_LOST" in (result.failure_reason or "")
    assert calls.get("wiki") == wiki_dir
    assert calls["snapshot_dir"].name.startswith(".retention-snap-ingest-"), (
        "run_ingest() must snapshot pages BEFORE the drain and pass that "
        "snapshot dir to enforce_retention_gate(), matching lib.py's pattern"
    )


# ---------------------------------------------------------------------------
# Test 2 -- merge-fragment duplicate page (REAL, unmocked deterministic check)
# ---------------------------------------------------------------------------


def test_run_ingest_catches_duplicate_page(monkeypatch, tmp_path: Path) -> None:
    """With the WIKI_WEAVER_ENFORCE_GATES=1 escape hatch set, a converged
    engine run that leaves a merge-fragment duplicate page behind
    (concept-2.md alongside concept.md) must come back from run_ingest() as
    converged=False -- proven with the REAL, unmocked no_duplicate_pages()
    deterministic scan. This is the OLD (pre-advisory) blocking behavior,
    now opt-in.
    """
    monkeypatch.setenv("WIKI_WEAVER_ENFORCE_GATES", "1")
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    (wiki_dir / "concept.md").write_text("# Concept\n", encoding="utf-8")

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        # Simulate a bad synthesis run: a fragment was appended instead of
        # fused into the existing page -- the exact signature
        # no_duplicate_pages() exists to catch.
        (wiki_dir / "concept-2.md").write_text(
            "# Concept (new source)\n", encoding="utf-8"
        )
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snapshot_dir, **kwargs: _passing_retention_decision(),
    )
    monkeypatch.setattr(
        reweave,
        "reweave_overview_if_needed",
        lambda wiki_dir: _passing_reweave_result(),
    )

    result = er.run_ingest(wiki_dir)

    assert result.converged is False, (
        "run_ingest() must NOT report success when a merge-fragment "
        "duplicate page is detected post-synthesis"
    )
    assert "duplicate-page gate" in (result.failure_reason or "")
    assert "concept-2.md" in (result.failure_reason or "")

    # Sanity: the real, unmocked deterministic function agrees.
    assert grading.no_duplicate_pages(wiki_dir) == ["concept-2.md"]


# ---------------------------------------------------------------------------
# Test 3 -- overview re-weave wiring (corrects the reweave.py docstring claim)
# ---------------------------------------------------------------------------


def test_run_ingest_propagates_reweave_failure(monkeypatch, tmp_path: Path) -> None:
    """run_ingest() must call reweave_overview_if_needed() once after the
    drain and propagate a non-passing result as a loud failure -- this is
    the wiring reweave.py's docstring falsely claimed already existed.
    """
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    # A non-empty _inbox/ so run_ingest() computes inbox_count > 0 -- matching
    # wiki_weaver/lib.py's drain path, which only reaches the reweave gate
    # after a real drain attempt (never on a bare/empty-inbox wiki).
    (wiki_dir / "_inbox").mkdir()
    (wiki_dir / "_inbox" / "source.md").write_text("a source\n", encoding="utf-8")

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snapshot_dir, **kwargs: _passing_retention_decision(),
    )
    monkeypatch.setattr(grading, "no_duplicate_pages", lambda wiki: [])

    reweave_calls: list[Path] = []

    def fake_reweave_overview_if_needed(wiki_dir_arg):
        reweave_calls.append(Path(wiki_dir_arg))
        return reweave.ReweaveGateResult(
            initial_passed=False,
            attempts=2,
            final_passed=False,
            initial_report="[FAIL] overview-quality",
            final_report="[FAIL] overview-quality: still degraded after retries",
        )

    monkeypatch.setattr(
        reweave, "reweave_overview_if_needed", fake_reweave_overview_if_needed
    )

    result = er.run_ingest(wiki_dir)

    assert reweave_calls == [wiki_dir], (
        "run_ingest() must call reweave_overview_if_needed(wiki_dir) exactly "
        "once, after the drain -- matching wiki_weaver/lib.py's placement"
    )
    assert result.converged is False
    assert "re-weave" in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# Tests 4+5 -- ADVISORY mode (the default): both gates detect + surface at
# run level (InnerResult.advisories) but do NOT block convergence.
# ---------------------------------------------------------------------------


def test_run_ingest_retention_block_is_advisory_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    """DEFAULT (no WIKI_WEAVER_ENFORCE_GATES): a retention-gate block verdict
    must NOT flip converged=False. It must surface as a run-level advisory in
    InnerResult.advisories, making the run distinguishable from a clean one.
    """
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snapshot_dir, **kwargs: retention.RetentionGateDecision(
            action="block_confirmed_loss",
            message=(
                "claim-retention gate: SILENTLY_LOST claim(s) detected in "
                'the re-write -- concept.md: "Original grounded claim."'
            ),
        ),
    )
    monkeypatch.setattr(grading, "no_duplicate_pages", lambda wiki: [])
    monkeypatch.setattr(
        reweave,
        "reweave_overview_if_needed",
        lambda wiki_dir: _passing_reweave_result(),
    )

    result = er.run_ingest(wiki_dir)

    assert result.converged is True, (
        "advisory mode (the default) must NOT block on a retention verdict"
    )
    assert result.failure_reason is None
    assert result.advisories, (
        "the advisory must surface at run level so an advisory-fired run is "
        "distinguishable from a clean one"
    )
    assert any(
        "claim-retention" in a and "SILENTLY_LOST" in a for a in result.advisories
    )
    assert any("did NOT block" in a for a in result.advisories)


def test_run_ingest_duplicate_page_is_advisory_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    """DEFAULT (no WIKI_WEAVER_ENFORCE_GATES): a duplicate-page hit (REAL,
    unmocked scan over a coexisting version-page pair: gpt-5.md + gpt-5-1.md)
    must NOT flip converged=False -- it surfaces as a WIKI-STRUCTURAL
    run-level advisory instead.
    """
    wiki_dir = (tmp_path / "wiki").resolve()
    wiki_dir.mkdir()
    (wiki_dir / "gpt-5.md").write_text("# GPT-5\n", encoding="utf-8")
    (wiki_dir / "gpt-5-1.md").write_text("# GPT-5.1\n", encoding="utf-8")

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        return _fake_pipeline_result(status="success")

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        retention,
        "enforce_retention_gate",
        lambda wiki, snapshot_dir, **kwargs: _passing_retention_decision(),
    )
    monkeypatch.setattr(
        reweave,
        "reweave_overview_if_needed",
        lambda wiki_dir: _passing_reweave_result(),
    )

    result = er.run_ingest(wiki_dir)

    assert result.converged is True, (
        "advisory mode (the default) must NOT block on a duplicate-page hit"
    )
    assert result.failure_reason is None
    # Wiki-structural framing, naming the offending pages.
    assert any(
        "duplicate-page" in a and "wiki contains" in a and "gpt-5-1.md" in a
        for a in result.advisories
    )

    # Sanity: the real, unmocked deterministic scan did fire.
    assert grading.no_duplicate_pages(wiki_dir) == ["gpt-5-1.md"]
