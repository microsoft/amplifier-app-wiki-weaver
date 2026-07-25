# pyright: reportMissingImports=false
"""Citation-density floor (grounding-dilution) tests.

THE PROBLEM THESE PIN (measured on a real production corpus via git-history
audit of 121 sync commits): citation-marker density fell 25-35% in EVERY repo
with 4 syncs (e.g. 4.9 -> 3.2 markers/KB) -- appended/rewritten prose was
outpacing its grounding markers and no gate noticed. The claim-retention
judge measures whether OLD claims survived; nothing measured whether NEW or
rewritten prose carries grounding at the same standard.

WHAT IS PINNED HERE:

D1 -- detect_grounding_dilution(): deterministic per-touched-page marker
      density (``[src: ...]`` + ``[YYYY-MM-DD ...]`` per KB of body text)
      vs the page's own pre-ingest density. Flags a >25% relative drop
      (flag-configurable) OR a fall below the absolute 1.0 markers/KB floor
      (configurable). Documented exemptions: tiny pages (<1KB after-body),
      index.md/overview.md, deleted pages, zero-marker before-pages.

D2 -- the check is wired into run_retention_checks(): a dilution flag lands
      in advisory_signals (gate name "grounding-dilution", message contains
      "grounding dilution: <page> density X -> Y") and preserves the
      pre-ingest snapshot for diff/restore. ADVISORY-ONLY, never blocks.

D3 -- pipeline/synthesize.dot's retention-contract section now requires new
      or rewritten prose to carry grounding markers at the same standard as
      existing content (one sentence, verified through the real dot parser).

MOCKING STRATEGY (same conventions as eval/test_retention_contract.py):
detect_grounding_dilution is never mocked (it is the deterministic unit under
test); only check_retention() is faked for the run_retention_checks()
integration test so no LLM judge is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import wiki_weaver.retention as retention  # noqa: E402
from wiki_weaver.retention import (  # noqa: E402
    DEFAULT_DENSITY_DROP_THRESHOLD,
    DEFAULT_DENSITY_FLOOR,
    detect_grounding_dilution,
    run_retention_checks,
)

SYNTHESIZE_DOT = _REPO / "pipeline" / "synthesize.dot"

_FM = "---\ntitle: Page\ntype: concept\nsources: [1]\nlast_updated: 2026-07-01\n---\n\n"
_LINE = (
    "This is a grounded operational fact about the system, padded out to a "
    "useful length so KB math is stable."
)


def _page(
    lines: int,
    markers: int,
    marker: str = "[src: repo/notes.md]",
    variant: str = "",
) -> str:
    """Frontmatter + *lines* body lines, the first *markers* of which carry a
    grounding marker. ~110 bytes/line => 20 lines is comfortably >1KB."""
    out = []
    for i in range(lines):
        suffix = f" {marker}" if i < markers else ""
        out.append(f"{_LINE} ({i}{variant}){suffix}")
    return _FM + "\n".join(out) + "\n"


def _snap_and_wiki(tmp_path: Path) -> tuple[Path, Path]:
    before = tmp_path / "before"
    wiki = tmp_path / "wiki"
    before.mkdir()
    wiki.mkdir()
    return before, wiki


# ---------------------------------------------------------------------------
# D1 -- the deterministic detector
# ---------------------------------------------------------------------------


def test_density_drop_over_threshold_flagged(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(_page(20, 10), encoding="utf-8")  # ~4.5/KB
    (wiki / "page.md").write_text(_page(20, 2, variant="x"), encoding="utf-8")

    flags = detect_grounding_dilution(before, wiki)

    assert len(flags) == 1
    f = flags[0]
    assert f.page == "page.md"
    assert f.after_density < f.before_density * (1 - DEFAULT_DENSITY_DROP_THRESHOLD)
    desc = f.describe()
    assert desc.startswith("grounding dilution: page.md density ")
    assert "markers/KB" in desc


def test_stable_density_silent(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(_page(20, 10), encoding="utf-8")
    # Body changed (touched) but marker density essentially unchanged.
    (wiki / "page.md").write_text(_page(20, 10, variant="x"), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_improved_density_silent(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(_page(20, 5), encoding="utf-8")
    (wiki / "page.md").write_text(_page(20, 12, variant="x"), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_fell_below_absolute_floor_flagged_even_without_big_drop(
    tmp_path: Path,
) -> None:
    """Isolate the floor rule: disable the relative-drop rule via an
    impossible threshold; a page that goes from at/above the floor to below
    it must still be flagged."""
    before, wiki = _snap_and_wiki(tmp_path)
    # 3 markers over ~2.2KB ≈ 1.3/KB (above floor); 1 marker ≈ 0.45/KB (below).
    (before / "page.md").write_text(_page(20, 3), encoding="utf-8")
    (wiki / "page.md").write_text(_page(20, 1, variant="x"), encoding="utf-8")

    flags = detect_grounding_dilution(
        before, wiki, drop_threshold=0.99, floor=DEFAULT_DENSITY_FLOOR
    )

    assert [f.page for f in flags] == ["page.md"]
    assert flags[0].before_density >= DEFAULT_DENSITY_FLOOR
    assert flags[0].after_density < DEFAULT_DENSITY_FLOOR


def test_page_always_below_floor_is_not_reflagged(tmp_path: Path) -> None:
    """The floor rule fires on FALLING below the floor -- a page that has
    always been below it must not generate a fresh advisory every run."""
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(_page(20, 1), encoding="utf-8")  # ~0.45/KB
    (wiki / "page.md").write_text(_page(20, 1, variant="x"), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki, drop_threshold=0.99, floor=1.0) == []


def test_tiny_pages_skipped(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "tiny.md").write_text(
        _FM + "One fact. [src: a] Another. [src: b]\n", encoding="utf-8"
    )
    (wiki / "tiny.md").write_text(
        _FM + "Rewritten with no markers at all.\n", encoding="utf-8"
    )

    assert detect_grounding_dilution(before, wiki) == []


def test_index_and_overview_exempt(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    for name in ("index.md", "overview.md"):
        (before / name).write_text(_page(20, 10), encoding="utf-8")
        (wiki / name).write_text(_page(20, 0, variant="x"), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_deleted_page_skipped(tmp_path: Path) -> None:
    """Deletion is shrinkage/judge territory -- no density flag on top."""
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "gone.md").write_text(_page(20, 10), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_zero_before_markers_silent(tmp_path: Path) -> None:
    """A page that never had markers cannot be diluted."""
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(_page(20, 0), encoding="utf-8")
    (wiki / "page.md").write_text(_page(40, 0, variant="x"), encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_untouched_body_silent(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    text = _page(20, 10)
    (before / "page.md").write_text(text, encoding="utf-8")
    (wiki / "page.md").write_text(text, encoding="utf-8")

    assert detect_grounding_dilution(before, wiki) == []


def test_dated_provenance_markers_counted(tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    (before / "page.md").write_text(
        _page(20, 10, marker="[2026-07-01 sync]"), encoding="utf-8"
    )
    (wiki / "page.md").write_text(
        _page(20, 1, marker="[2026-07-01 sync]", variant="x"), encoding="utf-8"
    )

    flags = detect_grounding_dilution(before, wiki)
    assert [f.page for f in flags] == ["page.md"]


def test_env_var_overrides_threshold_and_floor(monkeypatch, tmp_path: Path) -> None:
    before, wiki = _snap_and_wiki(tmp_path)
    # ~35% marker drop: flagged at the 0.25 default, silent at 0.60.
    (before / "page.md").write_text(_page(20, 10), encoding="utf-8")
    (wiki / "page.md").write_text(_page(20, 6, variant="x"), encoding="utf-8")

    assert len(detect_grounding_dilution(before, wiki)) == 1

    monkeypatch.setenv("WIKI_WEAVER_DENSITY_DROP_THRESHOLD", "0.60")
    monkeypatch.setenv("WIKI_WEAVER_DENSITY_FLOOR", "0.0")
    assert detect_grounding_dilution(before, wiki) == []

    # Malformed values fail soft to the defaults.
    monkeypatch.setenv("WIKI_WEAVER_DENSITY_DROP_THRESHOLD", "not-a-number")
    monkeypatch.setenv("WIKI_WEAVER_DENSITY_FLOOR", "")
    assert retention.density_drop_threshold() == DEFAULT_DENSITY_DROP_THRESHOLD
    assert retention.density_floor() == DEFAULT_DENSITY_FLOOR


# ---------------------------------------------------------------------------
# D2 -- wiring: run_retention_checks() surfaces the advisory + preserves snapshot
# ---------------------------------------------------------------------------


def _clean_judge_pass():
    return retention.RetentionGateResult(pages=[])


def test_run_retention_checks_surfaces_dilution_advisory_and_preserves_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "page.md").write_text(_page(20, 10), encoding="utf-8")
    (wiki / "page.md").write_text(_page(20, 2, variant="x"), encoding="utf-8")

    monkeypatch.setattr(
        retention, "check_retention", lambda *a, **k: _clean_judge_pass()
    )

    outcome = run_retention_checks(wiki, snapshot, "source.md")

    assert outcome.decision.action == "proceed"
    assert [f.page for f in outcome.dilution] == ["page.md"]
    gate_names = [g for g, _ in outcome.advisory_signals]
    assert "grounding-dilution" in gate_names
    msg = next(m for g, m in outcome.advisory_signals if g == "grounding-dilution")
    assert "ADVISORY" in msg and "never blocks" in msg
    assert "grounding dilution: page.md density" in msg
    assert "markers/KB" in msg
    # A dilution signal preserves the snapshot for human diff/restore.
    assert outcome.snapshot_preserved_to is not None
    assert outcome.snapshot_preserved_to.is_dir()
    assert not snapshot.exists(), "preserve moves (not copies) the snapshot"
    assert "snapshot preserved" in msg


def test_run_retention_checks_clean_pass_has_no_dilution_signal(
    monkeypatch, tmp_path: Path
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "page.md").write_text(_page(20, 10), encoding="utf-8")
    (wiki / "page.md").write_text(_page(20, 10, variant="x"), encoding="utf-8")

    monkeypatch.setattr(
        retention, "check_retention", lambda *a, **k: _clean_judge_pass()
    )

    outcome = run_retention_checks(wiki, snapshot, "source.md")

    assert outcome.dilution == []
    assert all(g != "grounding-dilution" for g, _ in outcome.advisory_signals)
    assert outcome.snapshot_preserved_to is None
    assert not snapshot.exists(), "a clean pass deletes the snapshot"


# ---------------------------------------------------------------------------
# D3 -- the ingest prompt requires grounding on new/rewritten prose
# ---------------------------------------------------------------------------


def _ingest_prompt() -> str:
    dot_parser = pytest.importorskip("amplifier_module_loop_pipeline.dot_parser")
    graph = dot_parser.parse_dot(SYNTHESIZE_DOT.read_text(encoding="utf-8"))
    return graph.nodes["ingest"].prompt


def test_ingest_prompt_requires_grounding_markers_on_new_prose() -> None:
    p = _ingest_prompt()
    assert "grounding markers" in p
    assert "same standard and density" in p
    assert "dilute the page's grounding" in p
    # Lives inside the retention-contract section, aligned with its wording.
    assert p.index("RETENTION CONTRACT") < p.index("grounding markers")
    assert p.index("grounding markers") < p.index("CITE-OR-DON'T-CLAIM")
