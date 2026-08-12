"""lint.write_report: formats the final report from --structural (always
present) and --findings (present only when the structural gate let analyze
run). Never edits the wiki -- writes only under --out."""

from __future__ import annotations

from wiki_weaver.lint import write_report
from wiki_weaver.lib import WikiRoot


def test_write_report_with_both_inputs_present(wiki_root):
    wr = WikiRoot(wiki_root.root)
    structural_path = wr.root / ".ai" / "structural-report.md"
    structural_path.parent.mkdir(parents=True, exist_ok=True)
    structural_path.write_text("No structural issues found.\n", encoding="utf-8")
    findings_path = wr.root / ".ai" / "lint-findings.md"
    findings_path.write_text("No contradictions found.\n", encoding="utf-8")

    code = write_report.main(
        [
            "--wiki-root", str(wr.root),
            "--out", "reports",
            "--structural", str(structural_path),
            "--findings", str(findings_path),
        ]
    )  # fmt: skip

    assert code == 0
    reports_dir = wr.root / "reports"
    files = list(reports_dir.glob("lint-*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "No structural issues found." in content
    assert "No contradictions found." in content


def test_write_report_with_findings_absent_handles_gracefully(wiki_root):
    """structural gate routed straight to write_report -- --findings was
    never passed on the command line at all."""
    wr = WikiRoot(wiki_root.root)
    structural_path = wr.root / ".ai" / "structural-report.md"
    structural_path.parent.mkdir(parents=True, exist_ok=True)
    structural_path.write_text("3 issue(s) found:\n- broken link: x.md\n", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--out", "reports",
        "--structural", str(structural_path),
    ]  # fmt: skip
    code = write_report.main(argv)

    assert code == 0
    reports_dir = wr.root / "reports"
    files = list(reports_dir.glob("lint-*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "3 issue(s) found" in content
    assert "structural gate did not allow it to run" in content


def test_write_report_defaults_out_to_reports_relative_to_wiki_root(wiki_root):
    wr = WikiRoot(wiki_root.root)
    structural_path = wr.root / ".ai" / "structural-report.md"
    structural_path.parent.mkdir(parents=True, exist_ok=True)
    structural_path.write_text("ok\n", encoding="utf-8")

    argv = ["--wiki-root", str(wr.root), "--structural", str(structural_path)]
    code = write_report.main(argv)

    assert code == 0
    assert list((wr.root / "reports").glob("lint-*.md"))


def test_write_report_empty_string_out_falls_back_to_reports_default(wiki_root):
    """Regression (bare-$var substitution defect): pipeline/lint.dot passes
    --out via bare $report_dir substitution. When report_dir is absent from
    context, the engine leaves the literal token in place and bash's
    unset-variable expansion resolves it to an empty string -- so this CLI
    receives --out "" (never a missing flag). It must fall back to the
    DEFAULT_OUT default, not write into --wiki-root itself."""
    wr = WikiRoot(wiki_root.root)
    structural_path = wr.root / ".ai" / "structural-report.md"
    structural_path.parent.mkdir(parents=True, exist_ok=True)
    structural_path.write_text("ok\n", encoding="utf-8")

    argv = ["--wiki-root", str(wr.root), "--out", "", "--structural", str(structural_path)]
    code = write_report.main(argv)

    assert code == 0
    assert list((wr.root / "reports").glob("lint-*.md"))
