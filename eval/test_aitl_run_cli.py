"""wiki_weaver.aitl.run -- CLI argument parsing and interviewer selection.

Does NOT exercise a live attractor pipeline run (no network, no API key,
no real LLM call): amplifier_module_pipeline_runner.runner.run_pipeline is
monkeypatched in the one test that drives main() end-to-end.
"""

from __future__ import annotations

import pytest

from wiki_weaver.aitl import run as aitl_run


class _FakeResult:
    def __init__(self, status: str = "success") -> None:
        self.status = status
        self.notes = ""
        self.logs_dir = "/tmp/fake-logs"


def test_default_gate_mode_is_fail():
    args = aitl_run.build_parser().parse_args(["pipeline/init.dot"])

    assert args.gate_mode == "fail"


def test_fail_mode_passes_no_interviewer():
    args = aitl_run.build_parser().parse_args(["pipeline/init.dot", "--gate-mode", "fail"])

    assert aitl_run._build_interviewer(args, wiki_root=None) is None


def test_auto_approve_mode_builds_auto_approve_interviewer():
    args = aitl_run.build_parser().parse_args(["pipeline/init.dot", "--gate-mode", "auto-approve"])

    interviewer = aitl_run._build_interviewer(args, wiki_root=None)

    from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

    assert isinstance(interviewer, AutoApproveInterviewer)


def test_console_mode_builds_console_interviewer():
    args = aitl_run.build_parser().parse_args(["pipeline/init.dot", "--gate-mode", "console"])

    interviewer = aitl_run._build_interviewer(args, wiki_root=None)

    from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

    assert isinstance(interviewer, ConsoleInterviewer)


def test_proxy_mode_requires_wiki_root():
    args = aitl_run.build_parser().parse_args(["pipeline/correct.dot", "--gate-mode", "proxy"])

    with pytest.raises(SystemExit):
        aitl_run._build_interviewer(args, wiki_root=None)


def test_proxy_mode_builds_proxy_interviewer(tmp_path):
    args = aitl_run.build_parser().parse_args(["pipeline/correct.dot", "--gate-mode", "proxy"])

    interviewer = aitl_run._build_interviewer(args, wiki_root=str(tmp_path))

    from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder
    from wiki_weaver.aitl.proxy_interviewer import ProxyInterviewer

    # Unconditionally wrapped in FreeformAnswerRecorder whenever wiki_root is
    # available -- see wiki_weaver.aitl.freeform_bridge's module docstring
    # for the verified platform defect this works around.
    assert isinstance(interviewer, FreeformAnswerRecorder)
    assert isinstance(interviewer._inner, ProxyInterviewer)  # type: ignore[attr-defined]
    assert interviewer._inner.wiki_root == tmp_path  # type: ignore[attr-defined]


def test_main_reports_missing_dot_file(tmp_path):
    missing = tmp_path / "does-not-exist.dot"

    code = aitl_run.main([str(missing)])

    assert code == 1


def test_main_wires_selected_interviewer_into_run_pipeline(tmp_path, monkeypatch):
    from amplifier_module_pipeline_runner import runner as real_runner

    dot_file = tmp_path / "sample.dot"
    dot_file.write_text(
        "digraph g { start [shape=Mdiamond]; exit [shape=Msquare]; start -> exit; }",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured.update(kwargs)
        captured["dot_source"] = dot_source
        return _FakeResult()

    monkeypatch.setattr(real_runner, "run_pipeline", fake_run_pipeline)

    code = aitl_run.main([str(dot_file), "--param", f"wiki_root={tmp_path}", "--gate-mode", "auto-approve"])

    assert code == 0
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["wiki_root"] == str(tmp_path)

    from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

    from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder

    # Unconditionally wrapped in FreeformAnswerRecorder whenever wiki_root is
    # available -- see wiki_weaver.aitl.freeform_bridge's module docstring.
    assert isinstance(captured["interviewer"], FreeformAnswerRecorder)
    assert isinstance(captured["interviewer"]._inner, AutoApproveInterviewer)


# -- per-site --gate-mode-for -------------------------------------------------


def test_default_gate_mode_for_is_empty():
    args = aitl_run.build_parser().parse_args(["pipeline/ingest.dot"])

    assert args.gate_mode_for == []


def test_gate_mode_for_parses_stage_equals_mode(tmp_path):
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode-for",
            "takeaways_gate=console",
            "--gate-mode-for",
            "review_gate=proxy",
        ]
    )

    overrides = aitl_run._parse_gate_mode_overrides(args.gate_mode_for)

    assert overrides == {"takeaways_gate": "console", "review_gate": "proxy"}


def test_gate_mode_for_rejects_missing_equals():
    with pytest.raises(ValueError, match="STAGE=MODE"):
        aitl_run._parse_gate_mode_overrides(["takeaways_gate"])


def test_gate_mode_for_rejects_invalid_mode():
    with pytest.raises(ValueError, match="invalid mode"):
        aitl_run._parse_gate_mode_overrides(["takeaways_gate=telepathy"])


def test_gate_mode_for_rejects_empty_stage_name():
    with pytest.raises(ValueError, match="empty stage name"):
        aitl_run._parse_gate_mode_overrides(["=console"])


def test_no_overrides_returns_the_exact_single_mode_interviewer(tmp_path):
    """Backward-compat guarantee: with zero --gate-mode-for entries,
    _build_interviewer must return EXACTLY what it did before per-site
    support existed -- no PerSiteInterviewer wrapper at all."""
    args = aitl_run.build_parser().parse_args(["pipeline/ingest.dot", "--gate-mode", "auto-approve"])

    interviewer = aitl_run._build_interviewer(args, wiki_root=None)

    from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

    assert isinstance(interviewer, AutoApproveInterviewer)


def test_overrides_build_a_per_site_interviewer(tmp_path):
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode",
            "auto-approve",
            "--gate-mode-for",
            "takeaways_gate=console",
        ]
    )

    interviewer = aitl_run._build_interviewer(args, wiki_root=str(tmp_path))

    from wiki_weaver.aitl.dispatch import PerSiteInterviewer

    assert isinstance(interviewer, PerSiteInterviewer)


def test_per_site_proxy_override_still_requires_wiki_root():
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode",
            "auto-approve",
            "--gate-mode-for",
            "review_gate=proxy",
        ]
    )

    with pytest.raises(SystemExit):
        aitl_run._build_interviewer(args, wiki_root=None)


def test_per_site_fail_override_resolves_to_gate_mode_fail_error(tmp_path):
    """A per-site fail override must actually stop that gate (and only that
    gate) at answer time -- see wiki_weaver.aitl.dispatch.GateModeFailError."""
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode",
            "auto-approve",
            "--gate-mode-for",
            "takeaways_gate=fail",
        ]
    )

    interviewer = aitl_run._build_interviewer(args, wiki_root=str(tmp_path))

    from amplifier_module_loop_pipeline.interviewer import Question, QuestionType

    from wiki_weaver.aitl.dispatch import GateModeFailError

    with pytest.raises(GateModeFailError):
        interviewer.ask(Question(text="q", type=QuestionType.FREEFORM, stage="takeaways_gate"))

    # A DIFFERENT gate, not overridden, still uses the global default fine.
    answer = interviewer.ask(Question(text="q", type=QuestionType.FREEFORM, stage="collect_guidance"))
    assert answer.value == "auto-approved"


def test_main_reports_gate_mode_for_parse_errors(tmp_path):
    dot_file = tmp_path / "sample.dot"
    dot_file.write_text(
        "digraph g { start [shape=Mdiamond]; exit [shape=Msquare]; start -> exit; }",
        encoding="utf-8",
    )

    code = aitl_run.main([str(dot_file), "--gate-mode-for", "bad-entry-no-equals"])

    assert code == 1


def test_per_site_proxy_is_not_double_wrapped_in_auditing_interviewer(tmp_path):
    """Regression (D1 -- measured double-logging defect): a self-auditing
    interviewer (ProxyInterviewer, `_SELF_AUDITS = True`) must NOT also be
    wrapped in AuditingInterviewer when built via the audited (per-site
    `--gate-mode-for`) path, even though it is unconditionally wrapped in
    FreeformAnswerRecorder first. FreeformAnswerRecorder does not forward
    attribute access to the interviewer it wraps, so checking `_SELF_AUDITS`
    on the wrapper (instead of the bare interviewer, captured BEFORE
    wrapping) always saw the wrapper's own absent attribute -- silently
    losing the self-audit signal and causing AuditingInterviewer to wrap on
    top, producing a second, poorer (`reason=""`, no grounding) row for
    every decision ProxyInterviewer already recorded richly itself."""
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode",
            "proxy",
            "--gate-mode-for",
            "takeaways_gate=proxy",
        ]
    )

    interviewer = aitl_run._build_single_interviewer("proxy", args, str(tmp_path), audited=True)

    from wiki_weaver.aitl.dispatch import AuditingInterviewer
    from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder
    from wiki_weaver.aitl.proxy_interviewer import ProxyInterviewer

    assert not isinstance(interviewer, AuditingInterviewer)
    assert isinstance(interviewer, FreeformAnswerRecorder)
    assert isinstance(interviewer._inner, ProxyInterviewer)  # type: ignore[attr-defined]


def test_per_site_console_is_still_wrapped_in_auditing_interviewer(tmp_path):
    """Companion to the double-logging regression above: a NON-self-auditing
    interviewer (ConsoleInterviewer has no `_SELF_AUDITS`) must still get
    the AuditingInterviewer wrap on the audited path -- console/auto-approve
    decisions have no other durable record in .ai/gate-decisions.jsonl."""
    args = aitl_run.build_parser().parse_args(
        [
            "pipeline/ingest.dot",
            "--gate-mode",
            "console",
            "--gate-mode-for",
            "review_gate=console",
        ]
    )

    interviewer = aitl_run._build_single_interviewer("console", args, str(tmp_path), audited=True)

    from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

    from wiki_weaver.aitl.dispatch import AuditingInterviewer
    from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder

    assert isinstance(interviewer, AuditingInterviewer)
    assert isinstance(interviewer._inner, FreeformAnswerRecorder)  # type: ignore[attr-defined]
    assert isinstance(interviewer._inner._inner, ConsoleInterviewer)  # type: ignore[attr-defined]


# -- dot_source_dir injection (D3) -------------------------------------------


def test_main_injects_dot_source_dir_param(tmp_path, monkeypatch):
    """Regression (D3 -- measured defect): a `folder`-shape node's relative
    `dot_file` (e.g. ask.dot's file_back -> ingest.dot) resolves against
    --cwd, never this pipeline file's own directory, because neither
    `run_pipeline` nor the `attractor` CLI ever set the root graph's
    `source_dir` (confirmed by reading
    amplifier_module_loop_pipeline.handlers.pipeline.resolve_dot_path and
    amplifier_module_pipeline_runner.runner). `wiki_weaver.aitl.run` must
    inject `dot_source_dir` = the pipeline file's own resolved parent
    directory as a `--param` so a bare `$dot_source_dir/child.dot` resolves
    correctly regardless of --cwd."""
    from amplifier_module_pipeline_runner import runner as real_runner

    subdir = tmp_path / "some" / "pipeline" / "dir"
    subdir.mkdir(parents=True)
    dot_file = subdir / "sample.dot"
    dot_file.write_text(
        "digraph g { start [shape=Mdiamond]; exit [shape=Msquare]; start -> exit; }",
        encoding="utf-8",
    )

    captured: dict[str, object] = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(real_runner, "run_pipeline", fake_run_pipeline)

    other_cwd = tmp_path / "somewhere" / "else"
    other_cwd.mkdir(parents=True)

    code = aitl_run.main([str(dot_file), "--cwd", str(other_cwd), "--gate-mode", "auto-approve"])

    assert code == 0
    params = captured["params"]
    assert isinstance(params, dict)
    assert params["dot_source_dir"] == str(subdir.resolve())


def test_main_rejects_explicit_dot_source_dir_param(tmp_path):
    """dot_source_dir is derived automatically from the pipeline file's own
    location -- a caller-supplied value would silently be overridden (or
    silently win over the derived one), either of which is confusing.
    Fail loud instead, matching the discipline the engine's own
    `context.target_dir` reserved key uses."""
    dot_file = tmp_path / "sample.dot"
    dot_file.write_text(
        "digraph g { start [shape=Mdiamond]; exit [shape=Msquare]; start -> exit; }",
        encoding="utf-8",
    )

    code = aitl_run.main([str(dot_file), "--param", "dot_source_dir=/somewhere"])

    assert code == 1


def test_main_returns_nonzero_on_pipeline_failure_status(tmp_path, monkeypatch):
    from amplifier_module_pipeline_runner import runner as real_runner

    dot_file = tmp_path / "sample.dot"
    dot_file.write_text(
        "digraph g { start [shape=Mdiamond]; exit [shape=Msquare]; start -> exit; }",
        encoding="utf-8",
    )

    async def fake_run_pipeline(dot_source, **kwargs):
        return _FakeResult(status="fail")

    monkeypatch.setattr(real_runner, "run_pipeline", fake_run_pipeline)

    code = aitl_run.main([str(dot_file), "--gate-mode", "auto-approve"])

    assert code == 1
