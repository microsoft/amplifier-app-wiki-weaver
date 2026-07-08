# pyright: reportMissingImports=false
"""Bounded, gated overview re-weave (Item 2: overview.md as a synthesized map).

THE PROBLEM (verified on the real frozen corpus, not hypothesized):
``overview.md`` is meant to be a synthesized navigational map, but the
per-source ``ingest`` node (pipeline/synthesize.dot) touches it incrementally
on EVERY source cycle. At corpus scale this degrades into a per-source
narration log -- the frozen real corpus carries 399 "(source N)" openers
against ``grade_overview()``'s threshold of <=2 (eval/grade_wiki.py).

THE PROVEN MECHANISM (validated via a real prototype run against the actual
421-page index.md catalog, zero redesign needed): given the wiki's ACTUAL
index.md page catalog (NOT the degraded overview.md) plus explicit
constraints, a single LLM completion writes a genuinely thematic overview.md
from scratch that passes ``grade_overview()`` cleanly (0 source-narration
openers, real wikilinks organized under thematic ## headings). The defect was
never model capability -- it was the per-cycle INCREMENTAL accretion pattern
(each cycle patches an already-degrading overview.md). This module replaces
"patch the log" with "re-derive fresh from the clean catalog", gated by the
free deterministic grader so the LLM call only happens when actually needed.

COST-BOUNDED DESIGN:
  - ``grade_overview()`` is free and deterministic (zero LLM cost, zero
    network). It runs FIRST; if overview.md already passes, this module is a
    complete no-op.
  - Only on FAILURE does a re-weave LLM call happen -- exactly ONE call per
    invocation of the gate, wired ONCE PER FULL INGEST RUN (after the entire
    _inbox/ drain completes), never once per source. See wiki_weaver/lib.py's
    ``ingest()`` drain loop and wiki_weaver/engine_runner.py's ``run_ingest()``
    for the two wiring points (CLI drain path and engine-driven drain path).
  - Retries are bounded (``max_retries``, default 2). If the gate is still
    failing after the budget, this FAILS LOUD -- it never silently leaves a
    degraded overview.md and reports success.

MECHANISM REUSE: this follows the exact pattern already established by
``run_init`` / ``build_init_dot`` in engine_runner.py -- a single-node DOT
pipeline built as a Python string, run through the same
``engine_runner._run_pipeline`` proper-path runner (no new provider-calling
mechanism introduced). The agent is instructed to read index.md and write
overview.md directly via its filesystem tools, exactly as build_init_dot has
the schema-design agent write schema.md directly.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .engine_runner import MODEL, PROVIDER, _dot_escape_prompt, _run_coro, _run_pipeline
from .grading import GradeResult, grade_overview
from .lib import wiki_runs
from .model_resolver import resolve_model
from .policy import load_policy

__all__ = [
    "ReweaveGateResult",
    "build_reweave_dot",
    "reweave_overview",
    "reweave_overview_if_needed",
]


def build_reweave_dot(
    wiki_dir: Path,
    *,
    provider: str = PROVIDER,
    model: str = MODEL,
) -> str:
    """Build the single-node DOT pipeline that re-weaves overview.md.

    Mirrors ``build_ask_dot`` / ``build_init_dot`` in engine_runner.py: the
    prompt instructs the agent to read a specific file itself (index.md) and
    write its output directly to another file (overview.md) via its
    filesystem tools -- the proven, already-used pattern for one-shot
    file-producing LLM pipelines in this codebase.

    The prompt constraints below are the EXACT constraints proven in the
    prototype run against the real 421-page index.md catalog (0 source-
    narration openers, hundreds of real wikilinks, both grade_overview() hard
    gates OV1/OV2 passed cleanly) -- reused verbatim, adapted only to
    reference the concrete wiki_dir paths.
    """
    wiki_abs = str(wiki_dir.resolve())

    prompt = (
        f"Read {wiki_abs}/index.md (the page catalog -- organized into "
        "thematic sections such as ## Concepts, ## Synthesis, ## Entities, "
        "each page listed with a wikilink and brief description).\n"
        "\n"
        "From THAT catalog alone (do not invent pages not in it), write a "
        f"completely fresh {wiki_abs}/overview.md -- a from-scratch rewrite, "
        "not an edit of any existing degraded overview.\n"
        "\n"
        "Hard constraints:\n"
        '1. OV1 - narration ban: Do NOT write "(source N)" parentheticals, '
        '"A Nth thread" openers, or ANY per-source/per-article narration. '
        "You are not summarizing ingestion history.\n"
        "2. OV2 - link density: Include real [[wikilinks]] to actual pages "
        "from the index - organized under thematic ## headings you identify "
        "from the catalog's actual content (not source-ingestion order).\n"
        "3. Structure: group related pages under each theme with orienting "
        "prose describing KNOWLEDGE, not ingestion history. A reader must be "
        "able to navigate the entire wiki by theme from this one page.\n"
        "4. Use real page titles/wikilink targets exactly as they appear in "
        "the index.\n"
        "5. Aim for genuine coverage of the catalog's major themes.\n"
        "\n"
        f"Write the complete result directly to {wiki_abs}/overview.md using "
        "your filesystem tools (overwrite the existing file entirely). "
        "Output ONLY the raw markdown content of the new overview.md into "
        "that file -- do not wrap it in commentary, and do not write "
        "anything else."
    )

    prompt_dot = _dot_escape_prompt(prompt)
    model = resolve_model(provider, model)

    lines = [
        "digraph reweave_overview {",
        '    graph [goal="Synthesize a fresh, thematic overview.md from index.md", '
        'default_fidelity="compact"]',
        '    start [shape=Mdiamond, label="Start"]',
        "    reweave [",
        '        label="Re-weave overview",',
        f'        llm_provider="{provider}",',
        f'        llm_model="{model}",',
        f'        prompt="{prompt_dot}"',
        "    ]",
        '    done [shape=Msquare, label="Done"]',
        "    start -> reweave -> done",
        "}",
        "",
    ]
    return "\n".join(lines)


async def _run_reweave_pipeline(
    dot_source: str,
    logs_dir: Path,
    wiki_dir: Path,
) -> tuple[str, dict]:
    """Run the reweave DOT through the shared proper-path runner.

    Uses the plain ``_run_pipeline`` (not the read-only ``_run_ask_pipeline``
    variant) because this node MUST be able to write ``overview.md`` --
    identical wiring to ``run_init`` / ``run_inner``.
    """
    return await _run_pipeline(
        dot_source,
        logs_dir,
        wiki_dir,
        execute_prompt="Run the pipeline",
        wiki_dir=wiki_dir,
    )


def reweave_overview(wiki_dir: str | Path) -> None:
    """Synthesize a fresh overview.md from index.md via one LLM completion.

    This is the ONE-LLM-CALL primitive. Callers that want the free
    grade-first / bounded-retry behaviour should use
    ``reweave_overview_if_needed`` instead of calling this directly.

    Fails loud (raises ``RuntimeError``/``FileNotFoundError``) rather than
    silently leaving a missing or empty overview.md:
      - index.md must exist (nothing to synthesize from).
      - overview.md must exist and be non-empty after the pipeline runs.
    """
    wiki_dir = Path(wiki_dir).resolve()
    index_path = wiki_dir / "index.md"
    if not index_path.is_file():
        raise FileNotFoundError(
            f"reweave_overview: index.md not found in {wiki_dir} -- "
            "nothing to synthesize the overview from."
        )

    policy = load_policy(wiki_dir)
    dot_source = build_reweave_dot(
        wiki_dir,
        provider=policy.provider,
        model=policy.model_for("overview"),
    )

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / f"reweave-{timestamp}"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / "reweave.dot").write_text(dot_source, encoding="utf-8")

    # Runs on the shared ingest loop when driven from ingest()'s
    # shared_engine_loop() context (single-loop drain); otherwise a private
    # one-shot loop. Either way the load-once _BASE_BUNDLE stays loop-consistent.
    _run_coro(_run_reweave_pipeline(dot_source, logs_dir, wiki_dir))

    overview_path = wiki_dir / "overview.md"
    if (
        not overview_path.is_file()
        or not overview_path.read_text(encoding="utf-8").strip()
    ):
        raise RuntimeError(
            f"reweave_overview: overview.md was not written (or is empty) "
            f"after the re-weave pipeline ran. See logs: {logs_dir}"
        )


@dataclass
class ReweaveGateResult:
    """Outcome of a grade-then-maybe-reweave gate pass.

    ``attempts`` is the number of re-weave LLM calls actually made (0 when
    the initial grade already passed -- the free, common-case path).
    """

    initial_passed: bool
    attempts: int
    final_passed: bool
    initial_report: str
    final_report: str


def reweave_overview_if_needed(
    wiki_dir: str | Path,
    max_retries: int = 2,
    *,
    grade_fn: Callable[[Path], GradeResult] = grade_overview,
    reweave_fn: Callable[[Path], None] = reweave_overview,
) -> ReweaveGateResult:
    """Grade overview.md; re-weave (bounded retries) only if it fails.

    Zero LLM cost when the gate already passes -- ``grade_fn`` is
    deterministic and free, and no ``reweave_fn`` call is made.

    On FAILURE, calls ``reweave_fn`` up to ``max_retries`` times, re-grading
    after each attempt, stopping as soon as the gate passes. If still failing
    after the budget is exhausted, returns ``final_passed=False`` -- this is
    a fail-loud contract: callers must check ``final_passed`` and must NOT
    treat a non-passing result as success.

    ``grade_fn`` / ``reweave_fn`` are injectable (default to the real
    ``grade_overview`` / ``reweave_overview``) so callers -- and tests -- can
    substitute fakes without needing a real wiki, LLM, or network access.
    """
    wiki_dir = Path(wiki_dir).resolve()

    result = grade_fn(wiki_dir)
    if result.passed:
        report = result.report()
        return ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report=report,
            final_report=report,
        )

    initial_report = result.report()
    attempts = 0
    while attempts < max_retries:
        attempts += 1
        reweave_fn(wiki_dir)
        result = grade_fn(wiki_dir)
        if result.passed:
            break

    return ReweaveGateResult(
        initial_passed=False,
        attempts=attempts,
        final_passed=result.passed,
        initial_report=initial_report,
        final_report=result.report(),
    )
