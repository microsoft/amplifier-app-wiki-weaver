"""Amplifier tool module: wiki-weaver commands as mountable tools.

Registers 9 tools in two groups:

GROUP 1 — Pipeline commands (4 existing tools)
    wiki_weaver_init / ingest / ask / lint

    These wrap the importable ``wiki_weaver.engine_runner.run_*`` functions.
    Each uses ``asyncio.to_thread`` because run_* call ``asyncio.run()``
    internally — they cannot be called from a running event loop.

GROUP 2 — Index query tools (5 new tools, INCREMENT 1)
    wiki_backlinks / wiki_graph_neighbors / wiki_tags /
    wiki_properties / wiki_resolve_citation

    These wrap ``wiki_weaver.index.query_*`` — pure synchronous filesystem
    reads that do NOT call asyncio.run() internally.  They are called
    directly from the async execute() method (no asyncio.to_thread needed).
    All five require ``build_indexes(wiki_dir)`` to have been run first.

All tools share the Iron Law (creating-amplifier-modules skill):
    mount() MUST call coordinator.mount() for each tool, or
    protocol_compliance validation fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import logging
from typing import Any

from amplifier_core import ToolResult

# wiki_weaver is the bundle's root package (installed editable in the same venv
# by Bundle.prepare()/activate_bundle_package before modules activate).
from wiki_weaver.engine_runner import run_ask, run_ingest, run_init, run_lint
from wiki_weaver.index import (
    CitationNotFound,
    CycleDetectedError,
    PageNotFound,
    SchemaVersionError,
    WikiIndexError,
    query_backlinks,
    query_graph_neighbors,
    query_properties,
    query_resolve_citation,
    query_tags,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GROUP 1: Pipeline command tool classes (unchanged from v0.1.0)
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
        # Run-level gate advisories (duplicate-page / claim-retention): fired
        # but did NOT block. Surfaced here so an advisory-fired run is never
        # indistinguishable from a clean one to the calling agent.
        for advisory in result.advisories:
            parts.append(f"advisory={advisory}")
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
# GROUP 2: Index query tool classes (INCREMENT 1 — 5 new tools)
# ---------------------------------------------------------------------------
# All five share these conventions:
#
#   - execute() is synchronous internally (pure filesystem reads).
#     query_* functions do not call asyncio.run(), so we call them directly.
#   - On WikiIndexError subclasses: ToolResult(success=False, output=<message>)
#   - On FileNotFoundError (index not built): ToolResult(success=False, hint to user)
#   - Return envelope: { ...result..., "stale": bool, "built": "<iso8601>" }
#     serialised as JSON in ToolResult.output.
# ---------------------------------------------------------------------------


def _index_error_result(exc: Exception) -> ToolResult:
    """Convert a wiki-index error to a ToolResult(success=False).

    Specific error subclasses produce more actionable messages than the generic
    WikiIndexError base-class string, so we pattern-match in preference order.
    """
    if isinstance(exc, SchemaVersionError):
        msg = (
            f"Index schema mismatch on {exc.index_name!r}: "
            f"found version {exc.found}, expected {exc.expected}. "
            "Re-run build_indexes to regenerate."
        )
    elif isinstance(exc, CycleDetectedError):
        msg = f"Alias cycle detected: {' -> '.join(exc.chain)}"
    elif isinstance(exc, PageNotFound):
        msg = f"Page not found in index: {exc.page!r}"
    elif isinstance(exc, CitationNotFound):
        msg = f"Citation {exc.n} out of range on page {exc.page!r}"
    elif isinstance(exc, FileNotFoundError):
        msg = f"Index file not found — run build_indexes first. ({exc})"
    else:
        msg = str(exc)
    return ToolResult(success=False, output=msg[:4000])


class WikiBacklinksTool:
    """Return pages that link to a given wiki page (requires built indexes)."""

    @property
    def name(self) -> str:
        return "wiki_backlinks"

    @property
    def description(self) -> str:
        return (
            "Return all pages that contain a [[wikilink]] pointing at the given page. "
            "Requires wiki_dir to have a built index (run build_indexes first). "
            "Returns stale=true when corpus has changed since the last index build — "
            "data is still returned; callers should surface the stale warning."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki corpus directory.",
                },
                "page": {
                    "type": "string",
                    "description": "Slug (or filename stem) of the page to look up.",
                },
            },
            "required": ["wiki_dir", "page"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            result = query_backlinks(input_data["wiki_dir"], input_data["page"])
            return ToolResult(success=True, output=json.dumps(result)[:8000])
        except (WikiIndexError, FileNotFoundError) as exc:
            return _index_error_result(exc)


class WikiGraphNeighborsTool:
    """Return immediate outbound and inbound link neighbours of a wiki page."""

    @property
    def name(self) -> str:
        return "wiki_graph_neighbors"

    @property
    def description(self) -> str:
        return (
            "Return the immediate link graph neighbourhood of a page: "
            "out=pages this page links to, in=pages that link to this page. "
            "Immediate neighbours only — no depth parameter. "
            "Requires a built index. Returns stale flag when corpus has changed."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki corpus directory.",
                },
                "page": {
                    "type": "string",
                    "description": "Slug (or filename stem) of the page.",
                },
            },
            "required": ["wiki_dir", "page"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            result = query_graph_neighbors(input_data["wiki_dir"], input_data["page"])
            return ToolResult(success=True, output=json.dumps(result)[:8000])
        except (WikiIndexError, FileNotFoundError) as exc:
            return _index_error_result(exc)


class WikiTagsTool:
    """List pages for a tag, or summarise all tags when no tag is given."""

    @property
    def name(self) -> str:
        return "wiki_tags"

    @property
    def description(self) -> str:
        return (
            "When tag is provided: return pages carrying that tag. "
            "When tag is omitted: return a tag→count summary for the whole corpus. "
            "Requires a built index. Returns stale flag when corpus has changed."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki corpus directory.",
                },
                "tag": {
                    "type": "string",
                    "description": (
                        "Tag to look up. Omit to get the full tag→count summary."
                    ),
                },
            },
            "required": ["wiki_dir"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            result = query_tags(input_data["wiki_dir"], input_data.get("tag"))
            return ToolResult(success=True, output=json.dumps(result)[:8000])
        except (WikiIndexError, FileNotFoundError) as exc:
            return _index_error_result(exc)


class WikiPropertiesTool:
    """Return all frontmatter properties (type, tags, aliases, …) for a page."""

    @property
    def name(self) -> str:
        return "wiki_properties"

    @property
    def description(self) -> str:
        return (
            "Return the full set of frontmatter key-value pairs for a page "
            "(type, tags, aliases, sources, last_updated, …). "
            "Requires a built index. Returns stale flag when corpus has changed."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki corpus directory.",
                },
                "page": {
                    "type": "string",
                    "description": "Slug (or filename stem) of the page.",
                },
            },
            "required": ["wiki_dir", "page"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            result = query_properties(input_data["wiki_dir"], input_data["page"])
            return ToolResult(success=True, output=json.dumps(result)[:8000])
        except (WikiIndexError, FileNotFoundError) as exc:
            return _index_error_result(exc)


class WikiResolveCitationTool:
    """Map a page citation ordinal (1-based) to its source record."""

    @property
    def name(self) -> str:
        return "wiki_resolve_citation"

    @property
    def description(self) -> str:
        return (
            "Resolve citation ordinal n (1-based) on a page to a source record "
            "from .sources.json.  The page's frontmatter 'sources' field is a list "
            "of source IDs; ordinal n indexes into that list. "
            "Returns: source {id, slug, path, title, url?} + stale + built. "
            "Raises CitationNotFound when n is out of range; PageNotFound when the "
            "slug does not exist."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "wiki_dir": {
                    "type": "string",
                    "description": "Absolute path to the wiki corpus directory.",
                },
                "page": {
                    "type": "string",
                    "description": "Slug (or filename stem) of the citing page.",
                },
                "n": {
                    "type": "integer",
                    "description": "1-based citation ordinal (position in the page's sources list).",
                    "minimum": 1,
                },
            },
            "required": ["wiki_dir", "page", "n"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        try:
            result = query_resolve_citation(
                input_data["wiki_dir"],
                input_data["page"],
                int(input_data["n"]),
            )
            return ToolResult(success=True, output=json.dumps(result)[:8000])
        except (WikiIndexError, FileNotFoundError) as exc:
            return _index_error_result(exc)


# ---------------------------------------------------------------------------
# Tool registry + mount() — the required entry point.
# Iron Law: must call coordinator.mount() for every tool.
# ---------------------------------------------------------------------------

_TOOLS = [
    # GROUP 1 — pipeline commands (existing)
    WikiWeaverInitTool(),
    WikiWeaverIngestTool(),
    WikiWeaverAskTool(),
    WikiWeaverLintTool(),
    # GROUP 2 — index query tools (INCREMENT 1)
    WikiBacklinksTool(),
    WikiGraphNeighborsTool(),
    WikiTagsTool(),
    WikiPropertiesTool(),
    WikiResolveCitationTool(),
]


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Mount all 9 wiki-weaver tools into the coordinator.

    Satisfies the Iron Law: calls coordinator.mount() for each tool.
    """
    for tool in _TOOLS:
        await coordinator.mount("tools", tool, name=tool.name)
        logger.debug("tool-wiki-weaver: mounted '%s'", tool.name)

    names = [t.name for t in _TOOLS]
    logger.info("tool-wiki-weaver: mounted %d tools: %s", len(names), names)
    return {
        "name": "tool-wiki-weaver",
        "version": "0.2.0",
        "provides": names,
    }
