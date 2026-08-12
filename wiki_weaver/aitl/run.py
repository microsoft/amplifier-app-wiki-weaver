"""wiki_weaver.aitl.run -- selectable launcher: proxy | auto-approve | console | fail.

Wires an ``Interviewer`` into a pipeline run via
``amplifier_module_pipeline_runner.runner.run_pipeline``'s public
``interviewer=`` parameter -- the SAME low-level extension seam the
``attractor`` CLI itself uses (``amplifier_module_pipeline_runner.cli``).
This is a purely additive, opt-in entry point at the wiki-weaver layer:
**no change of any kind to the attractor engine or its own ``attractor``
CLI's ``--on-human-gate`` flag.** docs/KERNEL_PHILOSOPHY's "mechanism, not
policy" applies directly here -- the engine already exposes the mechanism
(``interviewer=`` / the ``Interviewer`` protocol); which interviewer to use
for a given wiki is a wiki-weaver-level POLICY choice, so it belongs here,
not in the engine.

Runtime dependency note: like every other ``wiki_weaver.*`` CLI module,
this is invoked as ``python3 -m wiki_weaver.aitl.run`` from WITHIN the same
Python environment that has the attractor/amplifier stack installed (the
same environment ``pipeline/*.dot`` are run from) -- see AGENTS.md's cache
management notes. ``amplifier_module_pipeline_runner`` /
``amplifier_module_loop_pipeline`` are therefore imported directly, lazily,
rather than declared in pyproject.toml's (deliberately empty) core
dependency list, matching how ``pipeline/*.dot`` tool nodes already assume
this same environment.

DEFAULT ``--gate-mode`` IS "fail" -- IDENTICAL to the ``attractor`` CLI's
own default -- so this launcher can never silently change behavior for any
caller that doesn't explicitly ask for ``proxy``/``auto-approve``/
``console``. No existing eval run invokes this module at all (they call
``attractor run`` directly), so this addition changes nothing for them
either way.

Usage:
    python3 -m wiki_weaver.aitl.run pipeline/init.dot \\
        --param wiki_root=/srv/wikis/acme \\
        --cwd /srv/wikis/acme \\
        --gate-mode proxy
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.aitl.run")
    parser.add_argument("dot_file", help="path to a .dot pipeline file")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="k=v",
        help="key=value param for $param expansion (repeatable) -- same convention as `attractor run --param`",
    )
    parser.add_argument("--cwd", default=None, help="working directory for the pipeline (default: cwd)")
    parser.add_argument("--logs-root", default=None, help="directory for run logs (default: a fresh tempdir)")
    parser.add_argument("--provider", default="anthropic", help="provider for box/agent nodes (default: anthropic)")
    parser.add_argument(
        "--gate-mode",
        choices=("fail", "auto-approve", "console", "proxy"),
        default="fail",
        help=(
            "DEFAULT mode for every human-gate (hexagon) node not named by --gate-mode-for. "
            "'fail' (default, matches the attractor CLI's own default): no interviewer is wired in -- "
            "the pipeline fails loud at its first gate. 'auto-approve': picks the first listed choice, "
            "non-interactive (same as `attractor run --on-human-gate auto-approve`). 'console': a real "
            "person answers interactively at this terminal. 'proxy': the AITL proxy answers from "
            "<wiki_root>/lens/persona.md, failing loud (never guessing) when the persona is missing or "
            "does not cover the gate."
        ),
    )
    parser.add_argument(
        "--gate-mode-for",
        action="append",
        default=[],
        metavar="STAGE=MODE",
        help=(
            "per-site override: answer gate STAGE (a node id, e.g. 'review_gate', 'collect_guidance', "
            "'file_back_gate', 'takeaways_gate') with MODE instead of the run's --gate-mode default. "
            "Repeatable -- every human/agent gate in a pipeline is independently selectable. MODE is one "
            "of fail|auto-approve|console|proxy, same choices as --gate-mode. A re-entrant loop pass "
            "('review_gate-2', etc. -- see amplifier_module_loop_pipeline.handlers.human"
            ".HumanGateHandler._get_stage_id) resolves against the SAME override as its base stage name."
        ),
    )
    parser.add_argument(
        "--model",
        default=None,
        help="model for --gate-mode proxy's reasoning step (default: wiki_weaver.aitl.backend.DEFAULT_MODEL)",
    )
    return parser


def _parse_params(raw: list[str]) -> dict[str, str]:
    from amplifier_module_pipeline_runner.params import parse_params

    return parse_params(raw)


def _parse_gate_mode_overrides(raw: list[str]) -> dict[str, str]:
    """Parse repeated ``--gate-mode-for STAGE=MODE`` entries into
    ``{stage: mode}``. Raises ``ValueError`` (caught in ``main()``,
    identical handling to ``_parse_params``) on a malformed entry or an
    unrecognized mode -- fail loud on a typo rather than silently ignoring
    an override the caller thought was in effect.
    """
    from wiki_weaver.aitl.dispatch import VALID_GATE_MODES

    overrides: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ValueError(f"--gate-mode-for must be STAGE=MODE, got {item!r}")
        stage, _, mode = item.partition("=")
        stage = stage.strip()
        mode = mode.strip()
        if not stage:
            raise ValueError(f"--gate-mode-for: empty stage name in {item!r}")
        if mode not in VALID_GATE_MODES:
            raise ValueError(f"--gate-mode-for {stage}: invalid mode {mode!r} (choices: {VALID_GATE_MODES})")
        overrides[stage] = mode
    return overrides


def _build_single_interviewer(
    mode: str, args: argparse.Namespace, wiki_root: str | None, *, audited: bool = False
) -> object | None:
    """Build the interviewer for ONE gate-mode value. Returns ``None`` for
    'fail' -- the per-mode building block both the single-mode path (no
    ``--gate-mode-for`` at all) and the per-site ``PerSiteInterviewer`` path
    share.

    ``audited``, when true, wraps a non-self-auditing interviewer (see
    ``wiki_weaver.aitl.dispatch.AuditingInterviewer``) so every decision it
    makes is attributed to ``mode`` in ``.ai/gate-decisions.jsonl``. This is
    OFF by default and only ever turned on for the per-site path (see
    ``_build_interviewer`` below) -- with a single global ``--gate-mode``
    and no overrides, there is only one mode for the whole run (already
    printed to stdout at run start), so wrapping would add indirection with
    no attribution question left to answer; existing callers therefore keep
    getting the EXACT interviewer type they always have. Per-site runs are
    where mixed modes in ONE run make per-gate attribution actually matter
    (task requirement 3) -- and where the corrupted-experiment attribution
    gap this feature fixes actually occurs.
    """
    if mode == "fail":
        return None

    if mode == "auto-approve":
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer: object = AutoApproveInterviewer()
    elif mode == "console":
        from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

        interviewer = ConsoleInterviewer()
    else:  # proxy
        if not wiki_root:
            print(
                "attractor-aitl: --gate-mode proxy requires --param wiki_root=<path> "
                "(the proxy reads <wiki_root>/lens/persona.md)",
                file=sys.stderr,
            )
            raise SystemExit(1)

        from wiki_weaver.aitl.backend import DEFAULT_MODEL, LLMProxyBackend
        from wiki_weaver.aitl.proxy_interviewer import ProxyInterviewer

        backend = LLMProxyBackend(model=args.model or DEFAULT_MODEL)
        interviewer = ProxyInterviewer(wiki_root=wiki_root, backend=backend)

    # Read BEFORE wrapping with FreeformAnswerRecorder below -- a verified
    # double-logging defect (measured: every proxy freeform gate produced a
    # rich row AND a bare duplicate row across real eval runs) traced to
    # exactly this ordering. FreeformAnswerRecorder does not forward
    # attribute access to the interviewer it wraps, so checking
    # `_SELF_AUDITS` on the ALREADY-WRAPPED object always sees the
    # wrapper's own (absent) attribute, never the real interviewer's --
    # meaning ProxyInterviewer's `_SELF_AUDITS = True` was silently lost
    # the moment FreeformAnswerRecorder wrapped it, and every per-site
    # (`--gate-mode-for`) proxy run got double-wrapped in AuditingInterviewer
    # on top of ProxyInterviewer's own richer self-recording -- one real
    # decision, two rows: ProxyInterviewer's rich one (reason/grounding) and
    # AuditingInterviewer's bare one (`reason=""`, no grounding). Capturing
    # this flag on the UNWRAPPED interviewer, before it is ever wrapped,
    # restores the attribution `_SELF_AUDITS` was always meant to carry.
    self_audits = getattr(interviewer, "_SELF_AUDITS", False)

    # UNCONDITIONAL, regardless of `audited`: a verified platform defect
    # means takeaways_gate's freeform answer cannot reliably reach
    # persist_takeaways via the engine's own tool_env/env-var convention
    # (see wiki_weaver.aitl.freeform_bridge's module docstring for the
    # full reproduction). Every interviewer this project builds -- for
    # every mode -- writes its own takeaways_gate answer directly to
    # .ai/takeaways-answer.json the moment it answers. This is NOT the
    # gate-mode-attribution auditing wrap (that stays conditional on
    # `audited`); this is what makes the gate's answer actually reach
    # weave at all.
    if wiki_root is not None:
        from wiki_weaver.aitl.freeform_bridge import FreeformAnswerRecorder

        interviewer = FreeformAnswerRecorder(interviewer, wiki_root)

    if not audited or self_audits or wiki_root is None:
        return interviewer

    from wiki_weaver.aitl.dispatch import AuditingInterviewer

    return AuditingInterviewer(interviewer, wiki_root, mode)


def _build_interviewer(args: argparse.Namespace, wiki_root: str | None) -> object | None:
    """Select the interviewer for this run.

    With no ``--gate-mode-for`` overrides, this is EXACTLY the single-mode
    behavior every existing caller already depends on: returns ``None`` for
    'fail' (no interviewer wired in; ``HumanGateHandler`` raises the first
    time a gate is actually reached), or the interviewer for ``--gate-mode``
    -- unwrapped, byte-for-byte the type it always was.

    With one or more overrides, returns a
    ``wiki_weaver.aitl.dispatch.PerSiteInterviewer`` that dispatches each
    gate to its own configured mode, falling back to ``--gate-mode`` for
    every stage not named -- every mode built for THIS path is audit-wrapped
    (task requirement 3), since a mixed-mode run is exactly where
    per-gate attribution matters.
    """
    overrides_raw = _parse_gate_mode_overrides(args.gate_mode_for)
    if not overrides_raw:
        return _build_single_interviewer(args.gate_mode, args, wiki_root)

    from wiki_weaver.aitl.dispatch import PerSiteInterviewer

    default = _build_single_interviewer(args.gate_mode, args, wiki_root, audited=True)
    overrides = {
        stage: _build_single_interviewer(mode, args, wiki_root, audited=True) for stage, mode in overrides_raw.items()
    }
    return PerSiteInterviewer(default=default, overrides=overrides)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    dot_path = Path(args.dot_file).expanduser()
    if not dot_path.is_file():
        print(f"attractor-aitl: DOT file not found: {dot_path}", file=sys.stderr)
        return 1
    dot_source = dot_path.read_text(encoding="utf-8")

    try:
        params = _parse_params(args.param)
    except ValueError as e:
        print(f"attractor-aitl: {e}", file=sys.stderr)
        return 1

    # THE FIX for a verified engine defect: a `folder`-shape node's
    # `dot_file` (e.g. ask.dot's file_back -> ingest.dot) resolves against
    # `graph.source_dir` when the ENGINE set one -- but neither
    # `amplifier_module_pipeline_runner.runner.run_pipeline` nor the
    # `attractor` CLI's own `cmd_run` ever set `graph.source_dir` for the
    # ROOT graph (only child graphs get one, derived from their OWN
    # resolved path -- see `PipelineHandler.execute`'s "(5) Set
    # child_graph.source_dir"). With no source_dir, `resolve_dot_path`
    # falls back to `context.target_dir` (== --cwd) -- so a relative
    # `dot_file` on the TOP-level pipeline always resolves against --cwd,
    # never against the .dot file's own directory. Confirmed by reading
    # amplifier_module_loop_pipeline.handlers.pipeline.resolve_dot_path and
    # amplifier_module_pipeline_runner.runner.{run_pipeline,drive_engine}
    # directly -- this is a defect in the vendored engine's public API
    # surface (`run_pipeline` only accepts DOT source text, never a
    # pre-parsed Graph, so there is no seam to inject a root source_dir
    # from outside it) -- not something fixable without editing
    # amplifier-bundle-attractor. The smallest correct fix on our side:
    # inject the pipeline file's own absolute directory as a bare `$var`
    # every relative sibling `dot_file` reference can prefix (see
    # pipeline/ask.dot's `file_back` node) -- `resolve_dot_path` returns an
    # expanded absolute path as-is (step 2), bypassing the broken
    # source_dir/cwd fallback entirely. Fail loud on a caller-supplied
    # collision rather than silently overriding it -- same discipline the
    # engine's own `context.target_dir` reserved key uses.
    if "dot_source_dir" in params:
        print(
            "attractor-aitl: --param dot_source_dir is reserved (derived automatically "
            "from the pipeline file's own location) and may not be set explicitly",
            file=sys.stderr,
        )
        return 1
    params["dot_source_dir"] = str(dot_path.resolve().parent)

    try:
        interviewer = _build_interviewer(args, params.get("wiki_root"))
    except ValueError as e:
        print(f"attractor-aitl: {e}", file=sys.stderr)
        return 1

    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()
    logs_root = Path(args.logs_root).expanduser() if args.logs_root else None

    from amplifier_module_pipeline_runner import runner

    print(f"attractor-aitl: running {dot_path} (cwd: {cwd}, gate-mode: {args.gate_mode})")

    try:
        result = asyncio.run(
            runner.run_pipeline(
                dot_source,
                params=params or None,
                cwd=cwd,
                logs_root=logs_root,
                provider=args.provider,
                interviewer=interviewer,
            )
        )
    except Exception as e:  # noqa: BLE001 -- fail loud with the real error, no fallback
        print(f"attractor-aitl: pipeline execution failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"attractor-aitl: status={result.status}")
    print(f"attractor-aitl: logs={result.logs_dir}")
    if result.notes:
        print("attractor-aitl: notes:")
        print(result.notes)

    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
