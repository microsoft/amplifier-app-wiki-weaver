"""Amplifier tool module: wiki-weaver commands as mountable tools.

Registers 4 tools — one per wiki-weaver user command — that an AmplifierSession
agent can invoke. Each tool is a thin wrapper over the importable
``wiki_weaver.engine_runner.run_*`` functions:

    tool.execute(input_data)
      → await asyncio.to_thread(run_<cmd>, ...)   (run_* are SYNCHRONOUS)
      → returns ToolResult(success=..., output=<answer/status/report>)

WHY asyncio.to_thread (the one non-obvious wrinkle):
    wiki-weaver's run_init / run_ingest / run_ask / run_lint are *synchronous*
    wrappers that each call ``asyncio.run(...)`` internally to drive the
    attractor engine. A tool's ``execute()`` is itself awaited inside the host
    session's running event loop, and ``asyncio.run()`` cannot be called from a
    running loop. Running each sync function in a worker thread (to_thread) gives
    it a fresh thread with no active loop, so its internal ``asyncio.run`` works.
    (This differs from attractor-wiki's module, whose ``run_pipeline`` is already
    async and is awaited directly.)

All real work lives in ``wiki_weaver`` (the bundle's root package, installed
editable by Bundle.prepare() before this module activates). This module adds NO
logic beyond mapping tool arguments to run_* arguments and shaping the result.

The Iron Law (creating-amplifier-modules skill): mount() MUST call
coordinator.mount() for each tool, or protocol_compliance validation fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
from typing import Any

from amplifier_core import ToolResult

# wiki_weaver is the bundle's root package (installed editable in the same venv
# by Bundle.prepare()/activate_bundle_package before modules activate).
from wiki_weaver.engine_runner import run_ask, run_ingest, run_init, run_lint

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool classes — one per command. Each maps arguments → real run_* call.
# ---------------------------------------------------------------------------


class WikiWeaverInitTool:
    """Scaffold a wiki and LLM-design a domain-fit schema from its purpose."""

    @property
    def name(self) -> str:
        return "wiki_weaver_init"

    @property
    def description(self) -> str:
        return (
            "Initialize a new wiki-weaver wiki (the Karpathy LLM-wiki pattern). "
            "Always scaffolds the deterministic backbone (dirs, stubs, ledger), then — "
            "unless plain=true — runs one LLM node that designs a domain-fit schema "
            "(page types, frontmatter contract, conventions) from the stated purpose and "
            "writes it to <wiki_dir>/policy/schema.md. Pass plain=true to stop after the "
            "deterministic scaffold (no LLM, free). LLM mode requires a configured provider."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the (new or empty) wiki repository root.",
                },
                "purpose": {
                    "type": "string",
                    "description": (
                        "One-paragraph statement of what this wiki is for and the outcomes "
                        "it must serve. Drives the LLM schema design. Omit to use the generic "
                        "default schema."
                    ),
                },
                "plain": {
                    "type": "boolean",
                    "description": (
                        "If true, stop after the deterministic scaffold (generic default "
                        "schema, no LLM call). Default false."
                    ),
                },
            },
            "required": ["wiki_dir"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        wiki_dir = input_data["wiki_dir"]
        purpose = input_data.get("purpose")
        plain = bool(input_data.get("plain", False))

        def _call() -> tuple[int, str]:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_init(wiki_dir, purpose=purpose, plain=plain)
            return rc, buf.getvalue()

        rc, report = await asyncio.to_thread(_call)
        return ToolResult(
            success=rc == 0,
            output=(report.strip() or f"init exit code {rc}")[:8000],
        )


class WikiWeaverIngestTool:
    """Drain the wiki's _inbox/ into the compiled wiki (LLM-heavy convergence loop)."""

    @property
    def name(self) -> str:
        return "wiki_weaver_ingest"

    @property
    def description(self) -> str:
        return (
            "Ingest source documents into the wiki by draining its _inbox/ folder: for each "
            "source the convergence pipeline mines it, writes/updates cross-referenced pages, "
            "reconciles duplicates/orphans, and verifies the result, looping until each source "
            "is well integrated. LONG-RUNNING and LLM-heavy (minutes per source). Place sources "
            "in <wiki_dir>/_inbox/ first (init must have been run). Returns the drain status and "
            "convergence notes."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki repository root (must contain _inbox/).",
                },
                "max_cycles": {
                    "type": "integer",
                    "description": (
                        "Optional cap on per-source refinement cycles (CLI flag beats the wiki's "
                        "config). Omit to use the wiki's configured value (default 3)."
                    ),
                },
            },
            "required": ["wiki_dir"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        wiki_dir = input_data["wiki_dir"]
        max_cycles = input_data.get("max_cycles")

        result = await asyncio.to_thread(run_ingest, wiki_dir, max_cycles)

        parts = [f"status={result.status}", f"converged={result.converged}"]
        if result.failure_reason:
            parts.append(f"failure_reason={result.failure_reason}")
        if result.notes:
            parts.append(f"notes={result.notes}")
        parts.append(f"logs_dir={result.logs_dir}")
        return ToolResult(success=result.converged, output="\n".join(parts)[:8000])


class WikiWeaverAskTool:
    """Read-only, index-first Q&A against the compiled wiki (cited answer, no RAG)."""

    @property
    def name(self) -> str:
        return "wiki_weaver_ask"

    @property
    def description(self) -> str:
        return (
            "Answer a question by READING the compiled wiki (no embeddings/RAG): navigates "
            "index.md + [[wikilinks]] to grounded pages, synthesizes a cited answer, and "
            "explicitly refuses ('the wiki does not cover X') when the topic is absent. "
            "READ-ONLY — the spawned agent is structurally barred from writing wiki content, "
            "shelling out, or fetching from the web. Returns the cited answer and the pages used."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the compiled wiki repository root.",
                },
                "question": {
                    "type": "string",
                    "description": "The question to answer against the wiki.",
                },
            },
            "required": ["wiki_dir", "question"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        wiki_dir = input_data["wiki_dir"]
        question = input_data["question"]

        # run_ask signature is (wiki_dir, question) — wiki_dir first.
        result = await asyncio.to_thread(run_ask, wiki_dir, question)

        answer = result.answer.strip() if result.answer else ""
        if result.pages_used:
            answer += "\n\nPages used: " + ", ".join(result.pages_used)
        # A refusal is a valid, honest outcome (topic absent) — surface it as the
        # output but mark unsuccessful so callers see the topic wasn't covered.
        return ToolResult(
            success=not result.refused,
            output=(answer or result.raw or "(no answer returned)")[:8000],
        )


class WikiWeaverLintTool:
    """Deterministic structural validation of the wiki (no LLM)."""

    @property
    def name(self) -> str:
        return "wiki_weaver_lint"

    @property
    def description(self) -> str:
        return (
            "Run deterministic structural validation on the wiki (no LLM): checks frontmatter, "
            "the type taxonomy, link integrity, orphans, and other schema rules via the same "
            "validator the ingest pipeline uses. Returns the full validator report and a PASS/FAIL "
            "verdict. READ-ONLY — does not modify the wiki."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki repository root.",
                },
            },
            "required": ["wiki_dir"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        wiki_dir = input_data["wiki_dir"]

        def _call() -> tuple[int, str]:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = run_lint(wiki_dir)
            return rc, buf.getvalue()

        rc, report = await asyncio.to_thread(_call)
        return ToolResult(
            success=rc == 0,
            output=(report.strip() or f"lint exit code {rc}")[:8000],
        )


# ---------------------------------------------------------------------------
# mount() — THE required entry point. Iron Law: must call coordinator.mount()
# for every tool, or protocol_compliance validation fails.
# ---------------------------------------------------------------------------

_TOOLS = [
    WikiWeaverInitTool(),
    WikiWeaverIngestTool(),
    WikiWeaverAskTool(),
    WikiWeaverLintTool(),
]


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount all 4 wiki-weaver tools into the coordinator.

    Satisfies the Iron Law: calls coordinator.mount() for each tool.
    """
    for tool in _TOOLS:
        await coordinator.mount("tools", tool, name=tool.name)
        logger.debug("tool-wiki-weaver: mounted '%s'", tool.name)

    names = [t.name for t in _TOOLS]
    logger.info("tool-wiki-weaver: mounted %d tools: %s", len(names), names)
    return {
        "name": "tool-wiki-weaver",
        "version": "0.1.0",
        "provides": names,
    }
