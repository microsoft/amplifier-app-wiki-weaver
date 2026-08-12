"""Tests for evals.invention_rate.

Focus areas (see evals/invention_rate.py's module docstring for the
incident this tool exists to prevent): arm-name heterogeneity must never
cause a run or an arm to silently vanish from the report, sub_key matching
must not pool across an experimental variable an arm name distinguishes
(e.g. wiki-N10 vs raw-N110), and the gate must apply its documented,
raw-relative rule rather than an absolute ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals import invention_rate as ir


def _write_run(root: Path, stem: str, key: dict, records: list[dict], *, grades_name: str | None = None) -> None:
    """Write a (key, grades) pair matching the on-disk convention.

    grades_name overrides the default "<stem>-grades.jsonl" naming, used to
    build the legacy "grades.jsonl" / "grading-key.json" pair.
    """
    key_name = "grading-key.json" if stem == "grading" else f"{stem}-key.json"
    (root / key_name).write_text(json.dumps(key), encoding="utf-8")

    grades_name = grades_name or (f"{stem}-grades.jsonl" if stem != "grading" else "grades.jsonl")
    lines = "\n".join(json.dumps(r) for r in records) + "\n"
    (root / grades_name).write_text(lines, encoding="utf-8")


def _grade(qid: str, a_invented: bool, b_invented: bool) -> dict:
    return {"id": qid, "A": {"invented": a_invented}, "B": {"invented": b_invented}}


def _simple_key(n: int, arm_a: str, arm_b: str) -> dict:
    return {f"q{i:02d}": {"A": arm_a, "B": arm_b} for i in range(1, n + 1)}


# ---------------------------------------------------------------------------
# classify_arm
# ---------------------------------------------------------------------------


def test_classify_arm_raw_variants():
    assert ir.classify_arm("raw") == (ir.RAW, "")
    assert ir.classify_arm("raw-N10") == (ir.RAW, "N10")
    assert ir.classify_arm("raw-N110") == (ir.RAW, "N110")


def test_classify_arm_wiki_variants():
    assert ir.classify_arm("wiki") == (ir.WIKI, "")
    assert ir.classify_arm("wiki-N10") == (ir.WIKI, "N10")
    assert ir.classify_arm("wiki-N110") == (ir.WIKI, "N110")


def test_classify_arm_known_pipeline_variants_are_wiki():
    # These are exactly the non-standard names the task calls out: hybrid,
    # dis, split, sib, idx, thread, dissplit.
    for name in ("hybrid", "dis", "split", "dissplit", "idx", "sib", "thread"):
        category, sub_key = ir.classify_arm(name)
        assert category == ir.WIKI, f"{name} should classify as wiki, got {category}"
        assert sub_key == ""


def test_classify_arm_unknown_name_is_unclassified_not_silently_wiki():
    """The core 'do not silently skip' guarantee: an arm name this tool has
    never seen must come back UNCLASSIFIED, not be assumed to be a wiki
    variant just because it isn't literally 'raw'."""
    assert ir.classify_arm("graphrag") == (ir.UNCLASSIFIED, "")
    assert ir.classify_arm("arm-c") == (ir.UNCLASSIFIED, "")
    assert ir.classify_arm("") == (ir.UNCLASSIFIED, "")


# ---------------------------------------------------------------------------
# discover_runs
# ---------------------------------------------------------------------------


def test_discover_runs_finds_standard_and_legacy_pairs(tmp_path):
    _write_run(
        tmp_path,
        "articles",
        _simple_key(2, "hybrid", "raw"),
        [_grade("q01", False, False), _grade("q02", False, False)],
    )
    _write_run(
        tmp_path, "grading", _simple_key(2, "wiki", "raw"), [_grade("q01", False, False), _grade("q02", False, False)]
    )

    runs = ir.discover_runs(tmp_path)
    names = {r["run"] for r in runs}
    assert names == {"articles", "grading"}
    for r in runs:
        assert r["key_path"] is not None
        assert r["key_path"].is_file()


def test_discover_runs_surfaces_grades_file_with_no_key_instead_of_dropping(tmp_path):
    """A *-grades.jsonl with no sibling *-key.json must still appear in the
    run list (key_path=None) so it is a visible anomaly, not a silent gap."""
    (tmp_path / "orphan-grades.jsonl").write_text(json.dumps(_grade("q01", False, False)) + "\n", encoding="utf-8")

    runs = ir.discover_runs(tmp_path)
    assert len(runs) == 1
    assert runs[0]["run"] == "orphan"
    assert runs[0]["key_path"] is None

    stats = ir.compute_run_stats(runs[0]["run"], runs[0]["grades_path"], runs[0]["key_path"])
    assert "error" in stats
    assert stats["arms"] == {}


# ---------------------------------------------------------------------------
# compute_run_stats: basic counting
# ---------------------------------------------------------------------------


def test_compute_run_stats_basic_counts(tmp_path):
    key = _simple_key(4, "hybrid", "raw")
    records = [
        _grade("q01", False, True),  # hybrid=False, raw=True
        _grade("q02", False, False),
        _grade("q03", True, False),
        _grade("q04", False, True),
    ]
    _write_run(tmp_path, "toy", key, records)

    stats = ir.compute_run_stats("toy", tmp_path / "toy-grades.jsonl", tmp_path / "toy-key.json")
    assert stats["n_questions"] == 4
    assert stats["unmatched_ids"] == 0
    assert stats["missing_invented"] == 0
    assert stats["arms"]["hybrid"] == {"n": 4, "invented": 1, "rate": 0.25}
    assert stats["arms"]["raw"] == {"n": 4, "invented": 2, "rate": 0.5}
    assert stats["unclassified_arms"] == {}
    assert stats["by_subkey"][""][ir.WIKI]["n"] == 4
    assert stats["by_subkey"][""][ir.RAW]["n"] == 4


def test_compute_run_stats_unmatched_id_is_counted_not_crashed(tmp_path):
    key = _simple_key(2, "hybrid", "raw")
    records = [_grade("q01", False, False), _grade("q99", True, True)]  # q99 not in key
    _write_run(tmp_path, "toy", key, records)

    stats = ir.compute_run_stats("toy", tmp_path / "toy-grades.jsonl", tmp_path / "toy-key.json")
    assert stats["n_questions"] == 2
    assert stats["unmatched_ids"] == 1
    assert stats["arms"]["hybrid"]["n"] == 1
    assert stats["arms"]["raw"]["n"] == 1


def test_compute_run_stats_missing_invented_field_is_counted_not_crashed(tmp_path):
    key = _simple_key(1, "hybrid", "raw")
    _write_run(tmp_path, "toy", key, [{"id": "q01", "A": {"other_field": 1}, "B": {"invented": True}}])

    stats = ir.compute_run_stats("toy", tmp_path / "toy-grades.jsonl", tmp_path / "toy-key.json")
    assert stats["missing_invented"] == 1
    assert "hybrid" not in stats["arms"]  # A side never got a countable invented value
    assert stats["arms"]["raw"] == {"n": 1, "invented": 1, "rate": 1.0}


# ---------------------------------------------------------------------------
# Arm-name heterogeneity: the specific behavior the task requires
# ---------------------------------------------------------------------------


def test_unknown_arm_name_is_never_silently_dropped(tmp_path):
    """A run using a genuinely novel arm naming scheme must still show up,
    in full, in the per-arm report -- classified as unclassified rather than
    disappearing. This is the exact failure the task describes: 'my own
    first pass at this silently skipped every non-standard run'."""
    key = {
        "q01": {"A": "raw", "B": "graphrag"},
        "q02": {"A": "graphrag", "B": "raw"},
    }
    records = [_grade("q01", False, True), _grade("q02", True, False)]
    _write_run(tmp_path, "newmethod", key, records)

    stats = ir.compute_run_stats("newmethod", tmp_path / "newmethod-grades.jsonl", tmp_path / "newmethod-key.json")

    # The unrecognized arm is still fully present in the per-arm table.
    assert "graphrag" in stats["arms"]
    assert stats["arms"]["graphrag"] == {"n": 2, "invented": 2, "rate": 1.0}
    # ...and visibly flagged as unclassified, with a count.
    assert stats["unclassified_arms"] == {"graphrag": 2}
    # ...and excluded from the wiki/raw pooled comparison (never silently
    # merged into either bucket).
    assert ir.WIKI not in stats["by_subkey"].get("", {}) or "graphrag" not in str(stats["by_subkey"])
    pooled_raw = stats["by_subkey"][""][ir.RAW]
    assert pooled_raw["n"] == 2  # only the raw arm, not graphrag


def test_run_naming_heterogeneity_across_a_full_directory(tmp_path):
    """Simulates the real heterogeneity described in the task: hybrid/raw,
    a bespoke single-word arm (sib-style), and wiki-N10/wiki-N110/raw-N10/
    raw-N110 (growth-style), all in one discovery pass. None may be
    dropped, and growth-style sub_keys must not be pooled together."""
    _write_run(
        tmp_path,
        "articles",
        _simple_key(2, "hybrid", "raw"),
        [_grade("q01", False, True), _grade("q02", False, False)],
    )
    _write_run(
        tmp_path,
        "sib",
        _simple_key(2, "sib", "raw"),
        [_grade("q01", True, False), _grade("q02", True, False)],
    )
    growth_key = {
        "q01": {"A": "wiki-N10", "B": "raw-N10"},
        "q02": {"A": "raw-N10", "B": "wiki-N10"},
        "q03": {"A": "wiki-N110", "B": "raw-N110"},
        "q04": {"A": "raw-N110", "B": "wiki-N110"},
    }
    _write_run(
        tmp_path,
        "growth",
        growth_key,
        [
            _grade("q01", True, False),
            _grade("q02", False, True),
            _grade("q03", False, False),
            _grade("q04", False, True),
        ],
    )

    all_stats = ir.compute_all_runs(tmp_path)
    by_run = {s["run"]: s for s in all_stats}
    assert set(by_run) == {"articles", "sib", "growth"}

    # Nothing silently dropped: every arm from every run appears.
    assert "hybrid" in by_run["articles"]["arms"]
    assert "sib" in by_run["sib"]["arms"]
    assert set(by_run["growth"]["arms"]) == {"wiki-N10", "raw-N10", "wiki-N110", "raw-N110"}

    # growth's sub_keys stay separate -- N10 not pooled with N110.
    growth_subkeys = by_run["growth"]["by_subkey"]
    assert set(growth_subkeys) == {"N10", "N110"}
    assert growth_subkeys["N10"][ir.WIKI]["n"] == 2
    assert growth_subkeys["N10"][ir.RAW]["n"] == 2
    assert growth_subkeys["N110"][ir.WIKI]["n"] == 2

    overall = ir.weighted_overall(all_stats)
    all_runs = overall["all_runs"]
    # 2 (articles hybrid) + 2 (sib) + 2+2 (growth wiki-N10/N110) = 8
    assert all_runs[ir.WIKI]["n"] == 8
    # 2 (articles raw) + 2 (sib raw) + 2+2 (growth raw-N10/N110) = 8
    assert all_runs[ir.RAW]["n"] == 8
    assert all_runs["unclassified"]["n"] == 0
    assert set(all_runs["runs_included"]) == {"articles", "sib", "growth"}


# ---------------------------------------------------------------------------
# weighted_overall
# ---------------------------------------------------------------------------


def test_weighted_overall_pools_counts_not_rates(tmp_path):
    # Run 1: n=10, wiki invents 2 (20%). Run 2: n=100, wiki invents 5 (5%).
    # A naive average-of-rates would give 12.5%; pooled-by-count gives 7/110.
    _write_run(
        tmp_path,
        "small",
        _simple_key(10, "hybrid", "raw"),
        [_grade(f"q{i:02d}", i <= 2, False) for i in range(1, 11)],
    )
    big_key = {f"q{i:03d}": {"A": "hybrid", "B": "raw"} for i in range(1, 101)}
    big_records = [_grade(f"q{i:03d}", i <= 5, False) for i in range(1, 101)]
    _write_run(tmp_path, "big", big_key, big_records)

    all_stats = ir.compute_all_runs(tmp_path)
    overall = ir.weighted_overall(all_stats)["all_runs"]
    assert overall[ir.WIKI]["n"] == 110
    assert overall[ir.WIKI]["invented"] == 7
    assert abs(overall[ir.WIKI]["rate"] - 7 / 110) < 1e-9


def test_weighted_overall_excludes_key_error_runs_visibly(tmp_path):
    (tmp_path / "orphan-grades.jsonl").write_text(json.dumps(_grade("q01", False, False)) + "\n", encoding="utf-8")
    all_stats = ir.compute_all_runs(tmp_path)
    overall = ir.weighted_overall(all_stats)["all_runs"]
    assert overall["runs_with_key_error"] == ["orphan"]
    assert overall[ir.WIKI]["n"] == 0
    assert overall[ir.RAW]["n"] == 0


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------


def test_gate_pass_when_raw_zero_and_wiki_zero():
    result = ir.gate_subrun({"n": 10, "invented": 0, "rate": 0.0}, {"n": 10, "invented": 0, "rate": 0.0})
    assert result["status"] == ir.PASS


def test_gate_fail_when_raw_zero_and_wiki_nonzero():
    """No baseline slack to trade against: any invention when raw has none
    is a strict regression versus reading the sources directly."""
    result = ir.gate_subrun({"n": 10, "invented": 1, "rate": 0.1}, {"n": 10, "invented": 0, "rate": 0.0})
    assert result["status"] == ir.FAIL


def test_gate_pass_within_tolerance():
    # wiki 30% vs raw 20%: 0.3 <= 0.2 * 1.5 (0.30) -> pass (boundary).
    result = ir.gate_subrun({"n": 10, "invented": 3, "rate": 0.3}, {"n": 10, "invented": 2, "rate": 0.2})
    assert result["status"] == ir.PASS


def test_gate_fail_beyond_tolerance():
    # wiki 40% vs raw 20%: 0.4 > 0.2 * 1.5 (0.30) -> fail. Matches the
    # growth-run N10 case in the real data (FINDINGS.md Β§5b).
    result = ir.gate_subrun({"n": 10, "invented": 4, "rate": 0.4}, {"n": 10, "invented": 2, "rate": 0.2})
    assert result["status"] == ir.FAIL


def test_gate_run_not_applicable_when_no_raw_arm(tmp_path):
    """thread-key.json's real shape: two wiki-reading arms compared to each
    other, no raw arm at all. The gate must say N/A, never coerce a
    wiki-vs-wiki comparison into a pass."""
    key = {
        "q01": {"A": "thread", "B": "hybrid"},
        "q02": {"A": "hybrid", "B": "thread"},
    }
    _write_run(tmp_path, "thread", key, [_grade("q01", False, False), _grade("q02", False, False)])
    stats = ir.compute_run_stats("thread", tmp_path / "thread-grades.jsonl", tmp_path / "thread-key.json")

    results = ir.gate_run(stats)
    assert len(results) == 1
    assert results[0]["status"] == ir.NOT_APPLICABLE


def test_gate_run_splits_by_subkey_not_pooled(tmp_path):
    """The growth run must gate N10 and N110 independently. Pooling them
    would hide exactly the kind of scale-dependent divergence FINDINGS.md
    Β§5b warns about."""
    growth_key = {
        "q01": {"A": "wiki-N10", "B": "raw-N10"},
        "q02": {"A": "raw-N10", "B": "wiki-N10"},
        "q03": {"A": "wiki-N110", "B": "raw-N110"},
        "q04": {"A": "raw-N110", "B": "wiki-N110"},
    }
    # N10: wiki invents both (2/2=100%), raw invents none (0/2=0%) -> FAIL.
    # N110: wiki invents none (0/2=0%), raw invents none (0/2=0%) -> PASS.
    records = [
        _grade("q01", True, False),
        _grade("q02", False, True),
        _grade("q03", False, False),
        _grade("q04", False, False),
    ]
    _write_run(tmp_path, "growth", growth_key, records)
    stats = ir.compute_run_stats("growth", tmp_path / "growth-grades.jsonl", tmp_path / "growth-key.json")

    results = {r["sub_key"]: r for r in ir.gate_run(stats)}
    assert set(results) == {"N10", "N110"}
    assert results["N10"]["status"] == ir.FAIL
    assert results["N110"]["status"] == ir.PASS


def test_gate_run_reports_key_error_as_not_applicable(tmp_path):
    (tmp_path / "orphan-grades.jsonl").write_text(json.dumps(_grade("q01", False, False)) + "\n", encoding="utf-8")
    stats = ir.compute_run_stats("orphan", tmp_path / "orphan-grades.jsonl", None)
    results = ir.gate_run(stats)
    assert results[0]["status"] == ir.NOT_APPLICABLE


def test_gate_all_flags_failure_across_runs(tmp_path):
    # One run that passes, one that fails.
    _write_run(
        tmp_path, "articles", _simple_key(2, "hybrid", "raw"), [_grade("q01", False, True), _grade("q02", False, False)]
    )
    _write_run(tmp_path, "idx", _simple_key(2, "idx", "raw"), [_grade("q01", True, False), _grade("q02", True, False)])

    all_stats = ir.compute_all_runs(tmp_path)
    results = ir.gate_all(all_stats)
    statuses = {r["run"]: r["status"] for r in results}
    assert statuses["articles"] == ir.PASS
    assert statuses["idx"] == ir.FAIL


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


def test_cli_default_report_runs_clean(tmp_path, capsys):
    _write_run(
        tmp_path,
        "articles",
        _simple_key(2, "hybrid", "raw"),
        [_grade("q01", False, False), _grade("q02", False, False)],
    )
    code = ir.main([str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "articles" in out
    assert "weighted overall" in out


def test_cli_json_report(tmp_path, capsys):
    _write_run(
        tmp_path,
        "articles",
        _simple_key(2, "hybrid", "raw"),
        [_grade("q01", False, False), _grade("q02", False, False)],
    )
    code = ir.main([str(tmp_path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["runs"][0]["run"] == "articles"
    assert "weighted_overall" in payload


def test_cli_gate_all_exit_code_reflects_failure(tmp_path, capsys):
    _write_run(tmp_path, "idx", _simple_key(2, "idx", "raw"), [_grade("q01", True, False), _grade("q02", True, False)])
    code = ir.main([str(tmp_path), "--gate", "all"])
    assert code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out.upper()


def test_cli_gate_single_run_not_applicable_does_not_fail_exit_code(tmp_path, capsys):
    key = {"q01": {"A": "thread", "B": "hybrid"}}
    _write_run(tmp_path, "thread", key, [_grade("q01", False, False)])
    code = ir.main([str(tmp_path), "--gate", "thread"])
    assert code == 0
    out = capsys.readouterr().out
    assert "NOT_APPLICABLE" in out.upper()


def test_cli_gate_unknown_run_name_reports_and_fails(tmp_path, capsys):
    _write_run(tmp_path, "articles", _simple_key(1, "hybrid", "raw"), [_grade("q01", False, False)])
    code = ir.main([str(tmp_path), "--gate", "nonexistent"])
    assert code == 1
    out = capsys.readouterr().out
    assert "unknown run" in out
