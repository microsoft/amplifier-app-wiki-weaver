# pyright: reportMissingImports=false
"""Index/overview consistency-check tests (entry points lag content).

THE PROBLEM THESE PIN (measured on a real production corpus via git-history
audit of 121 sync commits): one repo's index.md was missing 6 of its 26 pages
for 16 days across 2 syncs, and its overview.md still asserted the corpus
"spans ... to 2026-07-08" after 11 pages were updated on 07-24. The ingest
prompt says "Maintain index.md" but nothing at runtime verified it happened.

WHAT IS PINNED HERE:

C1 -- index completeness: every top-level content page must appear in
      index.md. MISSING pages get a deterministic MECHANICAL repair (appended
      under '## Recently added (auto-indexed)', no LLM) plus an advisory so
      the next re-weave organizes them; membership is alias-aware.

C2 -- dead index entries (wikilinks pointing at no page) are advisory-only:
      surfaced loudly, NEVER silently deleted.

C3 -- overview staleness: an explicit "spans ... to YYYY-MM-DD" claim older
      than the newest content-page modification is an advisory AND counts as
      a failure (OV3) in the overview re-weave gate's default grader, so the
      existing bounded LLM re-weave pass refreshes the overview.

C4 -- the fresh-wiki skip path (no index.md) stays a silent no-op, and the
      recently-added ``ReweaveGateResult.skipped`` behavior is regression-
      pinned against the new composed default grader.

SAFETY: everything here is deterministic -- no LLM, no network. The re-weave
gate tests inject a fake reweave_fn (same convention as eval/test_reweave.py).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from wiki_weaver.consistency import (  # noqa: E402
    AUTO_INDEX_HEADING,
    check_index_consistency,
    check_overview_staleness,
    run_consistency_checks,
)

TODAY = date.today().isoformat()


def _fm(title: str, aliases: list[str] | None = None) -> str:
    alias_line = ""
    if aliases:
        alias_line = f"aliases: [{', '.join(aliases)}]\n"
    return (
        f"---\ntitle: {title}\ntype: concept\nsources: [1]\n"
        f"{alias_line}last_updated: 2026-07-01\n---\n\n"
    )


def _page(wiki: Path, name: str, title: str, aliases: list[str] | None = None) -> Path:
    p = wiki / name
    p.write_text(
        _fm(title, aliases) + f"# {title}\n\nA real grounded sentence. [1]\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# C1 -- index completeness: mechanical repair + advisory
# ---------------------------------------------------------------------------


def test_missing_pages_mechanically_appended_and_advisory(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    _page(wiki, "beta.md", "Beta Concept")
    _page(wiki, "gamma.md", "Gamma")
    (wiki / "index.md").write_text(
        "# Index\n\n## Concepts\n\n- [[alpha]] - Alpha\n", encoding="utf-8"
    )

    outcome = run_consistency_checks(wiki)

    assert outcome.skipped is False
    assert outcome.index.missing == ["beta.md", "gamma.md"]
    assert outcome.index.repaired is True
    gate_names = [g for g, _ in outcome.advisory_signals]
    assert "index-consistency" in gate_names
    adv = next(m for g, m in outcome.advisory_signals if "missing" in m)
    assert "beta.md" in adv and "gamma.md" in adv
    assert "ADVISORY" in adv

    index_text = (wiki / "index.md").read_text(encoding="utf-8")
    assert AUTO_INDEX_HEADING in index_text
    assert "[[beta]]" in index_text
    assert "[[gamma]]" in index_text
    # Title carried into the bullet when it differs from the stem.
    assert "Beta Concept" in index_text
    # Pre-existing catalog content untouched.
    assert "## Concepts" in index_text and "- [[alpha]] - Alpha" in index_text

    # The repair is effective AND idempotent: a second pass finds nothing.
    again = check_index_consistency(wiki)
    assert again.missing == []
    assert again.repaired is False


def test_repair_reuses_existing_auto_section_no_duplicate_heading(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    _page(wiki, "beta.md", "Beta")
    (wiki / "index.md").write_text(
        f"# Index\n\n- [[alpha]]\n\n{AUTO_INDEX_HEADING}\n\n- [[alpha]]\n",
        encoding="utf-8",
    )

    result = check_index_consistency(wiki)

    assert result.missing == ["beta.md"]
    index_text = (wiki / "index.md").read_text(encoding="utf-8")
    assert index_text.count(AUTO_INDEX_HEADING) == 1, (
        "a prior run's auto-index section must be reused, never duplicated"
    )
    assert "[[beta]]" in index_text


def test_consistent_index_is_silent_and_untouched(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    _page(wiki, "beta.md", "Beta")
    original = "# Index\n\n- [[alpha]]\n- [[beta]]\n- [[overview]]\n"
    (wiki / "index.md").write_text(original, encoding="utf-8")
    (wiki / "overview.md").write_text("# Overview\n", encoding="utf-8")

    outcome = run_consistency_checks(wiki)

    assert outcome.advisory_signals == []
    assert (wiki / "index.md").read_text(encoding="utf-8") == original


def test_alias_linked_page_is_not_missing_and_alias_is_not_dead(
    tmp_path: Path,
) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "kubernetes.md", "Kubernetes", aliases=["k8s"])
    (wiki / "index.md").write_text("# Index\n\n- [[k8s]]\n", encoding="utf-8")

    result = check_index_consistency(wiki)

    assert result.missing == []
    assert result.dead_entries == []


# ---------------------------------------------------------------------------
# C2 -- dead entries: advisory only, never deleted
# ---------------------------------------------------------------------------


def test_dead_entries_advisory_only_lines_never_deleted(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    original = "# Index\n\n- [[alpha]]\n- [[ghost-page]]\n"
    (wiki / "index.md").write_text(original, encoding="utf-8")

    outcome = run_consistency_checks(wiki)

    assert outcome.index.dead_entries == ["ghost-page"]
    adv = next(m for g, m in outcome.advisory_signals if "dead" in m)
    assert "ghost-page" in adv
    assert "nothing deleted" in adv
    # The dead line is STILL THERE -- surfaced, not silently removed.
    assert (wiki / "index.md").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# C3 -- overview coverage-date staleness
# ---------------------------------------------------------------------------


def _stale_overview(wiki: Path, claim: str = "2026-07-08") -> None:
    (wiki / "overview.md").write_text(
        "# Overview\n\n"
        f"The corpus spans early experiments to {claim}.\n\n"
        "## Themes\n\n- [[alpha]]\n- [[beta]]\n- [[gamma]]\n- [[delta]]\n- [[epsilon]]\n",
        encoding="utf-8",
    )


def test_stale_coverage_claim_flagged(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# Index\n\n- [[alpha]]\n", encoding="utf-8")
    _page(wiki, "alpha.md", "Alpha")  # mtime = now, newer than the claim
    _stale_overview(wiki, claim="2026-07-08")

    staleness = check_overview_staleness(wiki)
    assert staleness.stale is True
    assert staleness.claimed_date == "2026-07-08"
    assert staleness.newest_page_date == TODAY
    assert "2026-07-08" in staleness.message

    outcome = run_consistency_checks(wiki)
    adv = next(m for g, m in outcome.advisory_signals if g == "overview-staleness")
    assert "2026-07-08" in adv
    assert "OV3" in adv


def test_current_coverage_claim_not_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    _stale_overview(wiki, claim=TODAY)

    assert check_overview_staleness(wiki).stale is False


def test_no_coverage_claim_not_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    (wiki / "overview.md").write_text(
        "# Overview\n\nA thematic map with no date claims.\n", encoding="utf-8"
    )

    assert check_overview_staleness(wiki).stale is False


def test_multiple_claims_use_newest_end_date(tmp_path: Path) -> None:
    """An intentionally-historical sentence next to a current one must not
    false-alarm: only when ALL claims lag the newest page is it stale."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    (wiki / "overview.md").write_text(
        "# Overview\n\n"
        "The early phase spans January to 2026-02-01.\n"
        f"Overall the corpus spans that origin through {TODAY}.\n",
        encoding="utf-8",
    )

    assert check_overview_staleness(wiki).stale is False


def test_missing_overview_not_stale(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _page(wiki, "alpha.md", "Alpha")
    assert check_overview_staleness(wiki).stale is False


# ---------------------------------------------------------------------------
# C4 -- fresh-wiki skip path
# ---------------------------------------------------------------------------


def test_fresh_wiki_without_index_is_a_silent_noop(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    # Even with stray content, no index.md means no front door to check.
    _page(wiki, "alpha.md", "Alpha")

    outcome = run_consistency_checks(wiki)

    assert outcome.skipped is True
    assert outcome.index.skipped is True
    assert outcome.advisory_signals == []
    assert not (wiki / "index.md").exists(), "the skip path must not create files"


# ---------------------------------------------------------------------------
# C3 wiring -- the staleness signal trips the overview re-weave gate (OV3)
# ---------------------------------------------------------------------------
# These import wiki_weaver.reweave, which pulls the attractor engine deps at
# module load time -- skip cleanly in lightweight CI, same convention as
# eval/test_reweave.py.


def _reweave_module():
    pytest.importorskip("wiki_weaver.engine_runner")
    import wiki_weaver.reweave as reweave

    return reweave


def _passing_but_stale_wiki(tmp_path: Path) -> Path:
    """A wiki whose overview passes OV1/OV2 cleanly but carries a stale
    coverage-date claim -- ONLY the new OV3 signal can trip the gate."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text(
        "# Index\n\n- [[alpha]]\n- [[beta]]\n- [[gamma]]\n- [[delta]]\n- [[epsilon]]\n",
        encoding="utf-8",
    )
    for name in ("alpha", "beta", "gamma", "delta", "epsilon"):
        _page(wiki, f"{name}.md", name.title())
    _stale_overview(wiki, claim="2026-07-08")
    return wiki


def test_grade_overview_with_consistency_fails_only_on_staleness(
    tmp_path: Path,
) -> None:
    reweave = _reweave_module()
    from wiki_weaver.grading import grade_overview

    wiki = _passing_but_stale_wiki(tmp_path)

    assert grade_overview(wiki).passed is True, (
        "fixture must pass the structural gates so OV3 is isolated"
    )
    composed = reweave.grade_overview_with_consistency(wiki)
    assert composed.passed is False
    assert any("OV3" in f for f in composed.failures)


def test_stale_overview_triggers_reweave_gate_and_fresh_overview_passes(
    tmp_path: Path,
) -> None:
    """End-to-end gate wiring with the REAL default grade_fn: a stale claim
    makes the initial grade fail, one (fake) re-weave writes a fresh overview
    without the claim, and re-grading passes."""
    reweave = _reweave_module()
    wiki = _passing_but_stale_wiki(tmp_path)

    calls: list[Path] = []

    def fake_reweave(wiki_dir: Path) -> None:
        calls.append(wiki_dir)
        (wiki / "overview.md").write_text(
            "# Overview\n\n## Themes\n\n"
            "- [[alpha]]\n- [[beta]]\n- [[gamma]]\n- [[delta]]\n- [[epsilon]]\n",
            encoding="utf-8",
        )

    result = reweave.reweave_overview_if_needed(wiki, reweave_fn=fake_reweave)

    assert result.initial_passed is False
    assert "OV3" in result.initial_report
    assert len(calls) == 1
    assert result.final_passed is True
    assert result.skipped is False


def test_current_overview_does_not_trigger_reweave_gate(tmp_path: Path) -> None:
    reweave = _reweave_module()
    wiki = _passing_but_stale_wiki(tmp_path)
    _stale_overview(wiki, claim=TODAY)  # same structure, current claim

    def exploding_reweave(wiki_dir: Path) -> None:
        raise AssertionError("no re-weave call expected on a current overview")

    result = reweave.reweave_overview_if_needed(wiki, reweave_fn=exploding_reweave)
    assert result.initial_passed is True
    assert result.attempts == 0


def test_fresh_wiki_skip_result_unchanged_with_composed_default_grader(
    tmp_path: Path,
) -> None:
    """Regression-pin: the recently-added ``skipped`` behavior (no index.md ->
    warned no-op, never a failure) must survive the default grade_fn change
    to grade_overview_with_consistency."""
    reweave = _reweave_module()
    wiki = tmp_path / "wiki"
    wiki.mkdir()  # no index.md, no overview.md -- grade fails, gate must skip

    result = reweave.reweave_overview_if_needed(wiki)

    assert result.skipped is True
    assert result.attempts == 0
    assert result.final_passed is False
    assert "skipped: no index.md" in result.final_report
