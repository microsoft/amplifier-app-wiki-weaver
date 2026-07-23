# pyright: reportMissingImports=false
"""Retention-contract tests (R1-R3 from the llm-wiki origins audit).

THE PROBLEM THESE PIN: weekly ingests into a living wiki showed net content
REMOVAL. The ingest prompt licensed sectional re-writes ("RE-READ ... and
RE-WRITE them") with NO retention contract, so unchanged-but-unmentioned prior
content survived only at the model's discretion; the claim-retention gate
measured topic erasure (not shrinkage), was advisory-by-default, and DELETED
its pre-ingest snapshot on every exit path so nothing could ever be restored.

WHAT IS PINNED HERE:

R1 -- pipeline/synthesize.dot's ingest prompt now carries an explicit
      RETENTION CONTRACT (RETAINED / SUPERSEDED-with-visible-trace / MOVED --
      the same taxonomy the grader uses), states that absence of mention is
      NOT evidence of staleness, routes dated history to '## History', and
      KEEPS the rewrite instruction (integration stays the goal) subordinated
      to the contract.

R2 -- the ingest agent self-declares removals to .ai/removals.jsonl; a
      non-empty manifest surfaces as a run-level ADVISORY (result.json +
      DrainReport.advisories) so removals are always LOUD and reviewable.

R3 -- a free deterministic shrinkage heuristic (body-line delta > 20% OR a
      lost '## ' heading) surfaces as an advisory independent of the LLM
      judge; and the pre-ingest snapshot is PRESERVED to .wiki/snapshots/
      (pruned to the newest 10) whenever ANY retention signal fired --
      deleted only on a clean pass.

MOCKING STRATEGY (same conventions as eval/test_gate_advisory_mode.py):
run_inner() is mocked for the integration tests (no engine/LLM); only
check_retention() is faked for judge outcomes -- the REAL
enforce_retention_gate() / run_retention_checks() orchestration runs
unmodified; detect_shrinkage / read_removals_manifest / preserve_snapshot are
never mocked (they are the deterministic units under test).
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

import wiki_weaver.retention as retention  # noqa: E402
from wiki_weaver.lib import (  # noqa: E402
    INBOX,
    SOURCES,
    removals_manifest_path,
    wiki_snapshots,
)
from wiki_weaver.retention import (  # noqa: E402
    detect_shrinkage,
    enforce_retention_gate,
    preserve_snapshot,
    read_removals_manifest,
    run_retention_checks,
    snapshot_pages,
)

SYNTHESIZE_DOT = _REPO / "pipeline" / "synthesize.dot"
SCHEMA_MD = _REPO / "pipeline" / "SCHEMA.md"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _page(
    body_lines: int, headings: list[str] | None = None, title: str = "Page"
) -> str:
    """A page with frontmatter + exactly *body_lines* non-empty body lines
    (heading lines count toward the total)."""
    headings = headings or []
    lines = [f"# {title}"]
    for h in headings:
        lines.append(f"## {h}")
    while len(lines) < body_lines:
        lines.append(f"Grounded claim number {len(lines)} with a real fact.")
    assert len(lines) == body_lines, "helper misuse: headings exceed body_lines"
    fm = f"---\ntitle: {title}\ntype: concept\nsources: [1]\nlast_updated: 2026-07-01\n---\n"
    return fm + "\n" + "\n".join(lines) + "\n"


def _clean_pass():
    return retention.RetentionGateResult(pages=[])


def _confirmed_loss():
    return retention.RetentionGateResult(
        pages=[
            retention.PageRetentionOutcome(
                page="page.md",
                status="confirmed_loss",
                silently_lost=[{"claim_quote": "Original grounded claim."}],
            )
        ]
    )


# ---------------------------------------------------------------------------
# R1 -- the prompt contract (dot-parse + content assertions)
# ---------------------------------------------------------------------------


def _ingest_prompt() -> str:
    dot_parser = pytest.importorskip("amplifier_module_loop_pipeline.dot_parser")
    graph = dot_parser.parse_dot(SYNTHESIZE_DOT.read_text(encoding="utf-8"))
    return graph.nodes["ingest"].prompt


def test_synthesize_dot_still_parses() -> None:
    dot_parser = pytest.importorskip("amplifier_module_loop_pipeline.dot_parser")
    graph = dot_parser.parse_dot(SYNTHESIZE_DOT.read_text(encoding="utf-8"))
    assert "ingest" in graph.nodes


def test_ingest_prompt_contains_retention_contract() -> None:
    p = _ingest_prompt()
    assert "RETENTION CONTRACT" in p
    # The three permitted end-states, by name.
    for term in ("RETAINED", "SUPERSEDED", "MOVED"):
        assert term in p, f"contract must name the {term} state"
    # Supersession must leave a visible trace.
    assert "visible trace" in p
    # Silence in the new source is not a deletion license.
    assert "NOT evidence of staleness" in p
    assert (
        "never remove or condense prior content merely because this source does not repeat it"
        in p
    )
    # Dated history has a sanctioned home instead of silent eviction.
    assert "## History" in p
    assert "never silently drop it" in p


def test_ingest_prompt_keeps_rewrite_instruction_subordinated_to_contract() -> None:
    """Append-only is NOT the goal -- integration is. The rewrite instruction
    must survive, and the contract must govern it (appear after it, referring
    back to 'every re-write above')."""
    p = _ingest_prompt()
    assert "RE-WRITE them" in p, "the fusion re-write instruction must remain"
    assert "do NOT merely append" in p, "anti-append fusion goal must remain"
    assert p.index("RE-WRITE them") < p.index("RETENTION CONTRACT"), (
        "the contract must be stated as governing the re-write instruction above it"
    )
    assert "governs every re-write" in p


def test_ingest_prompt_terminology_matches_grader_taxonomy() -> None:
    """The writer's contract and the retention grader must agree on terms --
    previously the grader looked for traces nobody instructed the writer to
    leave (SUPERSEDED existed only in grading.py's taxonomy)."""
    from wiki_weaver.grading import _RETENTION_RUBRIC

    p = _ingest_prompt()
    for term in ("RETAINED", "SUPERSEDED", "MOVED"):
        assert term in p, f"{term} missing from the writer prompt"
        assert term in _RETENTION_RUBRIC, f"{term} missing from the grader rubric"


def test_ingest_prompt_requires_removal_manifest() -> None:
    p = _ingest_prompt()
    assert ".ai/removals.jsonl" in p
    assert '"action": "superseded|condensed|moved"' in p
    assert "Append only" in p
    assert '"page"' in p and '"removed"' in p and '"reason"' in p
    # Must be declared scratch state so the tamper check framing stays honest.
    assert "not tampering" in p


def test_schema_documents_retention_and_history_convention() -> None:
    text = SCHEMA_MD.read_text(encoding="utf-8")
    assert "## Content retention during re-writes" in text
    assert "`## History`" in text
    for term in ("RETAINED", "SUPERSEDED", "MOVED"):
        assert term in text


# ---------------------------------------------------------------------------
# R3a -- deterministic shrinkage heuristic
# ---------------------------------------------------------------------------


def _snap_and_wiki(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "before"
    wiki = tmp_path / "wiki"
    before.mkdir()
    wiki.mkdir()
    return before, wiki


def test_shrinkage_flags_page_shrunk_over_threshold(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "a.md").write_text(_page(20), encoding="utf-8")
    (wiki / "a.md").write_text(_page(15), encoding="utf-8")  # -25%
    flags = detect_shrinkage(before, wiki)
    assert [f.page for f in flags] == ["a.md"]
    assert flags[0].before_lines == 20 and flags[0].after_lines == 15


def test_shrinkage_ignores_growth_and_exact_threshold(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "grew.md").write_text(_page(10), encoding="utf-8")
    (wiki / "grew.md").write_text(_page(14), encoding="utf-8")
    # Exactly 20% is NOT "more than 20%" -- boundary must not flag.
    (before / "edge.md").write_text(_page(10), encoding="utf-8")
    (wiki / "edge.md").write_text(_page(8), encoding="utf-8")
    assert detect_shrinkage(before, wiki) == []


def test_shrinkage_flags_lost_h2_heading_even_without_line_shrink(
    tmp_path: Path,
) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "a.md").write_text(
        _page(12, headings=["Open tensions", "July 13 planning"]), encoding="utf-8"
    )
    (wiki / "a.md").write_text(_page(12, headings=["Open tensions"]), encoding="utf-8")
    flags = detect_shrinkage(before, wiki)
    assert len(flags) == 1
    assert flags[0].lost_headings == ["July 13 planning"]


def test_shrinkage_flags_deleted_page(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "gone.md").write_text(_page(8, headings=["History"]), encoding="utf-8")
    flags = detect_shrinkage(before, wiki)
    assert len(flags) == 1
    assert flags[0].after_lines == 0
    assert flags[0].lost_headings == ["History"]


def test_shrinkage_ignores_unchanged_pages(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    text = _page(30, headings=["Open tensions"])
    (before / "same.md").write_text(text, encoding="utf-8")
    (wiki / "same.md").write_text(text, encoding="utf-8")
    assert detect_shrinkage(before, wiki) == []


def test_shrinkage_frontmatter_changes_do_not_count(tmp_path: Path) -> None:
    """Body-only scope: frontmatter churn must not affect line counts."""
    before, wiki = _snap_and_wiki(tmp_path)
    body = _page(10)
    (before / "a.md").write_text(body, encoding="utf-8")
    (wiki / "a.md").write_text(
        body.replace("last_updated: 2026-07-01", "last_updated: 2026-07-20"),
        encoding="utf-8",
    )
    assert detect_shrinkage(before, wiki) == []


# ---------------------------------------------------------------------------
# R2 -- removal-manifest reader
# ---------------------------------------------------------------------------


def test_read_removals_manifest_missing_file(tmp_path: Path) -> None:
    assert read_removals_manifest(tmp_path) == []


def test_read_removals_manifest_parses_and_skips_malformed(tmp_path: Path) -> None:
    manifest = removals_manifest_path(tmp_path)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "page": "team-pulse.md",
                        "removed": "July 13 planning section",
                        "action": "condensed",
                        "reason": "folded into ## History",
                    }
                ),
                "",  # blank line tolerated
                "{not json",  # malformed line skipped, not fatal
                json.dumps(["not", "a", "dict"]),  # non-dict JSON skipped
                json.dumps(
                    {
                        "page": "program-structure.md",
                        "removed": "old milestone dates",
                        "action": "superseded",
                        "reason": "replaced by new schedule [7]",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )
    entries = read_removals_manifest(tmp_path)
    assert len(entries) == 2
    assert entries[0]["page"] == "team-pulse.md"
    assert entries[1]["action"] == "superseded"


# ---------------------------------------------------------------------------
# R3b -- snapshot preservation + pruning
# ---------------------------------------------------------------------------


def test_preserve_snapshot_moves_and_copies_manifest(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "page.md").write_text(_page(10), encoding="utf-8")
    manifest = removals_manifest_path(wiki)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"page": "page.md", "removed": "x", "action": "condensed", "reason": "r"}\n',
        encoding="utf-8",
    )

    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)
    dest = preserve_snapshot(wiki, snap, "weekly-report.md")

    assert dest is not None
    assert not snap.exists(), "snapshot must be MOVED, not copied"
    assert dest.parent == wiki_snapshots(wiki)
    assert dest.name.startswith("weekly-report-")
    assert (dest / "page.md").is_file(), (
        "preserved snapshot must contain the before-pages"
    )
    assert (dest / "removals.jsonl").is_file(), (
        "the removal declaration must travel with the snapshot it explains"
    )


def test_preserve_snapshot_prunes_to_keep_newest(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "page.md").write_text(_page(5), encoding="utf-8")

    preserved: list[Path] = []
    for i in range(12):
        snap = tmp_path / f"snap-{i}"
        snapshot_pages(wiki, snap)
        dest = preserve_snapshot(wiki, snap, f"source-{i}.md")
        assert dest is not None
        # Deterministic ordering: force strictly increasing mtimes.
        stamp = time.time() - (12 - i) * 60
        os.utime(dest, (stamp, stamp))
        preserved.append(dest)

    remaining = {d.name for d in wiki_snapshots(wiki).iterdir() if d.is_dir()}
    assert len(remaining) == 10, "growth must be bounded at the 10 newest"
    assert preserved[0].name not in remaining, "oldest must be pruned"
    assert preserved[1].name not in remaining, "second-oldest must be pruned"
    assert {p.name for p in preserved[2:]} == remaining


def test_preserve_snapshot_missing_dir_is_fail_soft(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    assert preserve_snapshot(wiki, tmp_path / "nope", "s.md") is None


# ---------------------------------------------------------------------------
# enforce_retention_gate -- cleanup_snapshot ownership transfer
# ---------------------------------------------------------------------------


def test_enforce_gate_cleanup_false_leaves_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """cleanup_snapshot=False must leave the snapshot in place on BOTH the
    verdict path and the grader-unavailable path -- previously even a
    fail-CLOSED block deleted it, so nothing could ever be restored."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "page.md").write_text(_page(5), encoding="utf-8")

    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())
    snap = tmp_path / "snap-verdict"
    snapshot_pages(wiki, snap)
    enforce_retention_gate(wiki, snap, cleanup_snapshot=False)
    assert snap.is_dir(), "caller-owned snapshot must survive a verdict"

    def _boom(*_a, **_k):
        raise RuntimeError("judge outage")

    monkeypatch.setattr(retention, "check_retention", _boom)
    snap2 = tmp_path / "snap-raise"
    snapshot_pages(wiki, snap2)
    enforce_retention_gate(wiki, snap2, cleanup_snapshot=False)
    assert snap2.is_dir(), "caller-owned snapshot must survive a grader error"


# ---------------------------------------------------------------------------
# run_retention_checks -- the R1-R3 orchestration
# ---------------------------------------------------------------------------


def _wiki_with_page(tmp_path: Path, body_lines: int = 20) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "page.md").write_text(_page(body_lines), encoding="utf-8")
    return wiki


def test_checks_clean_pass_deletes_snapshot_no_advisories(
    tmp_path: Path, monkeypatch
) -> None:
    wiki = _wiki_with_page(tmp_path)
    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())
    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)

    outcome = run_retention_checks(wiki, snap, "source.md")

    assert outcome.decision.action == "proceed"
    assert outcome.advisory_signals == []
    assert outcome.snapshot_preserved_to is None
    assert not snap.exists(), "clean pass must delete the snapshot (bounded growth)"
    root = wiki_snapshots(wiki)
    assert not root.exists() or not any(root.iterdir())


def test_checks_shrinkage_fires_advisory_and_preserves(
    tmp_path: Path, monkeypatch
) -> None:
    wiki = _wiki_with_page(tmp_path, body_lines=20)
    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())
    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)
    # Post-snapshot "ingest" shrinks the page by 50%.
    (wiki / "page.md").write_text(_page(10), encoding="utf-8")

    outcome = run_retention_checks(wiki, snap, "source.md")

    gates = [g for g, _ in outcome.advisory_signals]
    assert gates == ["page-shrinkage"]
    msg = outcome.advisory_signals[0][1]
    assert "page.md" in msg and "[source source.md]" in msg
    assert outcome.snapshot_preserved_to is not None
    assert outcome.snapshot_preserved_to.is_dir()
    assert str(outcome.snapshot_preserved_to) in msg, (
        "advisory must name the preserved snapshot so a headless operator can find it"
    )
    assert not snap.exists(), "snapshot must be MOVED to the preserved location"


def test_checks_removals_fire_advisory_and_preserve_with_manifest_copy(
    tmp_path: Path, monkeypatch
) -> None:
    wiki = _wiki_with_page(tmp_path)
    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())
    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)
    manifest = removals_manifest_path(wiki)
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        '{"page": "page.md", "removed": "old ops history", "action": "condensed", "reason": "folded into ## History"}\n',
        encoding="utf-8",
    )

    outcome = run_retention_checks(wiki, snap, "source.md")

    gates = [g for g, _ in outcome.advisory_signals]
    assert gates == ["removal-manifest"]
    assert "1 removed/condensed claim(s)" in outcome.advisory_signals[0][1]
    assert outcome.snapshot_preserved_to is not None
    assert (outcome.snapshot_preserved_to / "removals.jsonl").is_file()


def test_checks_confirmed_loss_preserves_snapshot(tmp_path: Path, monkeypatch) -> None:
    """Judge-detected loss preserves the snapshot even with no other signal --
    this is the restoration path that never existed before."""
    wiki = _wiki_with_page(tmp_path)
    monkeypatch.setattr(
        retention, "check_retention", lambda *_a, **_k: _confirmed_loss()
    )
    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)

    outcome = run_retention_checks(wiki, snap, "source.md")

    assert outcome.decision.action == "block_confirmed_loss"
    assert outcome.snapshot_preserved_to is not None
    assert (outcome.snapshot_preserved_to / "page.md").is_file()
    assert not snap.exists()


def test_checks_grader_fail_open_alone_deletes_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """Grader unavailability (below escalation) is NOT a retention signal --
    a clean deterministic picture means the snapshot is still deleted."""
    wiki = _wiki_with_page(tmp_path)

    def _boom(*_a, **_k):
        raise RuntimeError("judge outage")

    monkeypatch.setattr(retention, "check_retention", _boom)
    snap = tmp_path / "snap"
    snapshot_pages(wiki, snap)

    outcome = run_retention_checks(wiki, snap, "source.md", escalation_threshold=3)

    assert outcome.decision.action == "proceed"
    assert outcome.snapshot_preserved_to is None
    assert not snap.exists()


# ---------------------------------------------------------------------------
# Integration -- lib.ingest single-file path surfaces the new advisories in
# result.json / DrainReport and preserves/deletes the snapshot accordingly
# ---------------------------------------------------------------------------

pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.lib as lib  # noqa: E402
from wiki_weaver.lib import DrainReport  # noqa: E402


@pytest.fixture(autouse=True)
def _advisory_default_env(monkeypatch):
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)


@pytest.fixture(autouse=True)
def _bypass_reweave_gate(monkeypatch):
    from wiki_weaver.reweave import ReweaveGateResult

    monkeypatch.setattr(
        "wiki_weaver.reweave.reweave_overview_if_needed",
        lambda *_a, **_kw: ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="stub: bypassed for retention-contract test",
            final_report="stub: bypassed for retention-contract test",
        ),
    )


def _make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    (wiki / "existing.md").write_text(
        _page(20, headings=["Open tensions"]), encoding="utf-8"
    )
    return wiki


def _seed_inbox(inbox: Path, name: str) -> Path:
    p = inbox / name
    p.write_text(f"# {name}\n\nUnique body text for {name}.\n", encoding="utf-8")
    old = time.time() - 10
    os.utime(p, (old, old))
    return p


def _fake_result() -> SimpleNamespace:
    return SimpleNamespace(
        converged=True,
        status="success",
        failure_reason=None,
        logs_dir=Path("/tmp/fake_logs"),
    )


def _read_run_result(wiki: Path) -> dict:
    results = sorted((wiki / ".wiki" / "runs").glob("ingest-*/result.json"))
    assert results, "every ingest run must write a result.json"
    return json.loads(results[-1].read_text(encoding="utf-8"))


def test_ingest_surfaces_shrinkage_and_removals_advisories(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "weekly.md")
    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())

    def _destructive_run_inner(_src, wiki_dir, **_kw):
        # Simulate the failure class: the "ingest" shrinks an existing page by
        # 50% AND self-declares one removal.
        Path(wiki_dir, "existing.md").write_text(_page(10), encoding="utf-8")
        manifest = removals_manifest_path(Path(wiki_dir))
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            '{"page": "existing.md", "removed": "old ops history", "action": "condensed", "reason": "folded into ## History"}\n',
            encoding="utf-8",
        )
        return _fake_result()

    report = DrainReport()
    with patch(
        "wiki_weaver.engine_runner.run_inner", side_effect=_destructive_run_inner
    ):
        rc = lib.ingest(wiki, source=src, report=report)

    out = capsys.readouterr().out
    assert rc == 0, "both new signals are ADVISORY-ONLY -- the run must proceed"
    assert (wiki / SOURCES / "weekly.md").exists(), "source must still archive"

    # Loud at run level, machine-readable in DrainReport + result.json.
    assert "GATE ADVISORY [page-shrinkage]" in out
    assert "GATE ADVISORY [removal-manifest]" in out
    assert any("page-shrinkage" in a for a in report.advisories)
    assert any("removal-manifest" in a for a in report.advisories)
    result = _read_run_result(wiki)
    assert any("page-shrinkage" in a for a in result["advisories"])
    assert any("removal-manifest" in a for a in result["advisories"])

    # Snapshot preserved for diff/restore (advisory fired), not deleted.
    preserved = [d for d in wiki_snapshots(wiki).iterdir() if d.is_dir()]
    assert len(preserved) == 1
    assert (preserved[0] / "existing.md").is_file()
    assert (preserved[0] / "removals.jsonl").is_file()
    # No stray working snapshot left behind under runs/.
    assert not list((wiki / ".wiki" / "runs").glob(".retention-snap-*"))


def test_ingest_clean_pass_no_advisories_snapshot_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "harmless.md")
    monkeypatch.setattr(retention, "check_retention", lambda *_a, **_k: _clean_pass())

    def _additive_run_inner(_src, wiki_dir, **_kw):
        # Purely additive ingest: existing page grows, nothing declared removed.
        Path(wiki_dir, "existing.md").write_text(
            _page(24, headings=["Open tensions"]), encoding="utf-8"
        )
        return _fake_result()

    report = DrainReport()
    with patch("wiki_weaver.engine_runner.run_inner", side_effect=_additive_run_inner):
        rc = lib.ingest(wiki, source=src, report=report)

    assert rc == 0
    assert report.advisories == []
    result = _read_run_result(wiki)
    assert result["advisories"] == []
    root = wiki_snapshots(wiki)
    assert not root.exists() or not any(root.iterdir()), (
        "a clean pass must not accumulate preserved snapshots"
    )
    assert not list((wiki / ".wiki" / "runs").glob(".retention-snap-*"))
