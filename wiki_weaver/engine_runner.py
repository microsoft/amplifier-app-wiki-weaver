# pyright: reportMissingImports=false
#!/usr/bin/env python3
"""Run the wiki-weaver INNER convergence pipeline through the attractor engine.

Delegates the generic engine-driving mechanics (compose the attractor-pipeline
base bundle, create the session, wire ``session.spawn``, drive the engine
directly) to the shared ``amplifier_module_pipeline_runner.run_pipeline``
library (see the ``pipeline-runner`` module in amplifier-bundle-attractor).
This module keeps only the wiki-weaver PRODUCT: building each ``.dot``
pipeline (with concrete paths/prompts substituted in), and the two
wiki-weaver-specific spawn constraints applied via ``run_pipeline``'s
``child_constraint`` seam:

  - ``_fs_child_constraint``  -- denies the ledger/_sources/ paths (Fix 1)
  - ``_ask_child_constraint`` -- read-only wiki access for the ask pipeline

Every ``run_pipeline`` call also gets the context-intelligence hook overlay
(``_ci_overlay()``) via the ``extra_overlays`` seam, and
``spawn_timeout=SPAWN_TIMEOUT_SECONDS`` so a stalled child agent still fails
loud instead of hanging (see the comment on ``SPAWN_TIMEOUT_SECONDS`` below).

The OUTER corpus sweep is a plain Python loop in the CLI (see wiki_weaver/lib.py).
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from amplifier_module_pipeline_runner import run_pipeline

from ._assets import pipeline_dir
from .lib import wiki_ledger, wiki_policy_dir, wiki_registry, wiki_runs, wiki_sources
from .model_resolver import resolve_model
from .policy import WikiPolicy, load_policy

# --------------------------------------------------------------------------
# Static asset locations (this repo).
# --------------------------------------------------------------------------

WIKI_WEAVER_ROOT = Path(__file__).resolve().parent.parent
# Resolve the pipeline-asset directory for the active install layout (real wheel
# ships them at site-packages/wiki_weaver_pipeline/; dev tree at repo-root
# pipeline/). See wiki_weaver._assets.pipeline_dir for the dual-path logic.
PIPELINE_DIR = pipeline_dir()
INNER_DOT = PIPELINE_DIR / "synthesize.dot"
# ingest.dot: the parent DAG that invokes synthesize.dot as a folder sub-pipeline.
INGEST_DOT = PIPELINE_DIR / "ingest.dot"
# ask.dot: the single-node ask pipeline (static DOT with $var tokens).
ASK_DOT = PIPELINE_DIR / "ask.dot"
# init.dot: the single-node LLM schema-design pipeline (static DOT with $var tokens).
INIT_DOT = PIPELINE_DIR / "init.dot"
# ingest_setup.py: the tool node that picks the next inbox source + assigns a
# stable id before the synthesize.dot folder sub-pipeline runs.
INGEST_SETUP_PY = Path(__file__).resolve().parent / "ingest_setup.py"
# ingest_archive.py: the archive tool node (post-convergence process-state write).
INGEST_ARCHIVE_PY = Path(__file__).resolve().parent / "ingest_archive.py"
# ingest_fail.py: the fail-route tool node (non-convergence; moves to _failed/).
INGEST_FAIL_PY = Path(__file__).resolve().parent / "ingest_fail.py"
SCHEMA_PATH = PIPELINE_DIR / "SCHEMA.md"
VALIDATE_PY = PIPELINE_DIR / "validate_wiki.py"
NORMALIZE_PY = PIPELINE_DIR / "normalize_links.py"
FOOTNOTES_PY = PIPELINE_DIR / "footnotes.py"
# normalize_unicode.py: repairs stray \uXXXX JSON-escape artifacts in prose
# (defense-in-depth bridge for amplifier-support #306; see module docstring).
NORMALIZE_UNICODE_PY = PIPELINE_DIR / "normalize_unicode.py"
# RESERVED FOR EVAL GRADING ONLY. The scenario rubric grades the WHOLE finished
# corpus wiki (all sources, the A/B test). It is the WRONG bar for the inner
# per-source loop: a single freshly-ingested article can never satisfy
# whole-corpus completeness, so assess would vote `refine` forever. Do NOT point
# the pipeline `assess` node at this -- it uses CONVERGENCE_RUBRIC_PATH below.
RUBRIC_PATH = WIKI_WEAVER_ROOT / "eval" / "scenario-01-llm-wiki" / "rubric.md"
# The pipeline `assess` gate's PER-SOURCE convergence rubric. Judges "is THIS
# one source well integrated?" -- the correct, achievable bar for the inner loop.
CONVERGENCE_RUBRIC_PATH = PIPELINE_DIR / "CONVERGENCE_RUBRIC.md"

# The attractor-pipeline bundle: composes the loop-pipeline orchestrator,
# context-simple, the anthropic provider, filesystem/bash/search tools, and the
# per-provider child agents the engine spawns. Local checkout preferred; the
# bundle's ``attractor:`` namespace resolves to the cached microsoft repo via
# the user registry. Falls back to the canonical git URL.
# Set WIKI_WEAVER_ATTRACTOR_PIPELINE to point at a local checkout of the
# attractor-pipeline bundle (e.g. the bundles/attractor-pipeline.yaml inside a
# local clone of amplifier-bundle-attractor). When the env var is absent,
# run_pipeline's own local-sibling-then-git-URL fallback applies (see the
# bridge below).
ATTRACTOR_PIPELINE_LOCAL = os.environ.get("WIKI_WEAVER_ATTRACTOR_PIPELINE")

# Bridge to pipeline-runner's OWN local-bundle override env var. run_pipeline
# (amplifier_module_pipeline_runner.runner) resolves its base bundle via a
# local sibling path or the ``ATTRACTOR_PIPELINE_BUNDLE`` env var -- a
# DIFFERENT name than wiki-weaver's own ``WIKI_WEAVER_ATTRACTOR_PIPELINE``.
# Forward it here (setdefault -- never override an explicit
# ATTRACTOR_PIPELINE_BUNDLE the caller already set) so existing wiki-weaver
# dev workflows that point WIKI_WEAVER_ATTRACTOR_PIPELINE at a local checkout
# keep working unchanged. doctor() still reports on ATTRACTOR_PIPELINE_LOCAL
# directly (see lib.py).
if ATTRACTOR_PIPELINE_LOCAL:
    os.environ.setdefault("ATTRACTOR_PIPELINE_BUNDLE", ATTRACTOR_PIPELINE_LOCAL)

# Context-intelligence hook module source (already installed in the amplifier
# venv; prepare() resolves it from cache).
CI_HOOK_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main"
    "#subdirectory=modules/hook-context-intelligence"
)

SETTINGS_PATH = Path(
    os.environ.get(
        "AMPLIFIER_SETTINGS", str(Path.home() / ".amplifier" / "settings.yaml")
    )
)

# Provider the inner LLM nodes route to (maps to attractor-agent-anthropic via
# the bundle's orchestrator ``profiles`` table).
PROVIDER = os.environ.get("WIKI_WEAVER_PROVIDER", "anthropic")
# Explicit model for the LLM nodes. The attractor child agents intentionally
# carry NO default_model ("no silent defaults"), so the spawning node must name
# one. The engine forwards it as a provider_preference to the child session.
MODEL = os.environ.get("WIKI_WEAVER_MODEL", "sonnet")
# Optional per-node reasoning_effort (recognized stylesheet property). Unset =>
# omitted entirely, so default behaviour (e.g. Wave 1 anthropic) is unchanged.
REASONING_EFFORT = os.environ.get("WIKI_WEAVER_REASONING_EFFORT")

# LLM-driven node ids in the DOT (need an explicit llm_provider so the engine
# routes them to a child agent). Tool nodes (validate) do not.
LLM_NODE_IDS = ("ingest", "assess", "feedback")

# Wall-clock ceiling for a single child-agent spawn (one pipeline node's full
# execution: the LLM call(s) plus any tool-call rounds it makes internally).
#
# WHY THIS EXISTS: ``prepared.spawn()`` (called from ``spawn_capability``
# below) has no timeout of its own. The underlying unified-llm-client Anthropic
# adapter never sets an explicit ``timeout`` on the SDK client either, so a
# single LLM completion silently inherits the Anthropic Python SDK's default
# ``Timeout(connect=5.0, read=600, write=600, pool=600)`` -- a 10-minute cap
# nobody configured and nothing in wiki-weaver or loop-agent surfaces. Worse,
# a node like "ingest" makes MANY sequential LLM+tool-call rounds in one spawn
# (observed: 250+ tool calls touching many existing pages), so the aggregate
# spawn duration is not bounded by any single call's timeout at all.
#
# Investigation (see docs/investigations -- or PR description) confirmed via a
# 20+ minute uninterrupted live reproduction that legitimate per-node spawns
# regularly take several minutes (observed up to ~6 minutes for the "assess"
# grading node across multiple refine cycles) with zero errors -- i.e. this is
# NOT an unbounded hang, it is an invisible, unconfigured wait that is
# indistinguishable from a hang to an operator watching CPU/log activity over
# a shorter window. Bounding it here makes a stalled spawn fail loud (a clear,
# actionable TimeoutError) instead of blocking the shared event loop forever.
#
# Default is generous headroom above every legitimately observed duration
# (multiples of the ~6-minute worst case seen live) so normal slow-but-working
# runs are never falsely killed. Override via WIKI_WEAVER_SPAWN_TIMEOUT for
# unusually large corpora / slower models.
SPAWN_TIMEOUT_SECONDS = float(os.environ.get("WIKI_WEAVER_SPAWN_TIMEOUT", "1800"))


@dataclass
class InnerResult:
    """Outcome of one inner-pipeline run for a single source."""

    status: str
    converged: bool
    logs_dir: Path
    notes: str = ""
    failure_reason: str | None = None


# --------------------------------------------------------------------------
# settings loader: CI hook config from overrides.hook-context-intelligence
# --------------------------------------------------------------------------


def load_ci_config() -> dict[str, Any]:
    """Read the context-intelligence hook config from the user's settings.

    Reads ``overrides.hook-context-intelligence.config`` from
    ~/.amplifier/settings.yaml and returns a config dict using the PRIMARY
    ``destinations`` shape expected by the hook's current contract.

    The hook's ``LoggingHandler`` is always-on: it writes per-session
    ``events.jsonl`` + ``metadata.json`` locally regardless of config.
    An empty return (``{}``) means local-only logging — the normal default.

    ``destinations`` shape (remote fan-out, optional):
    ::

        {
            "destinations": {
                "<name>": {
                    "url": "https://...",
                    "api_key": "<key>",       # optional
                    "include": ["**"],         # glob filter, optional
                }
            }
        }

    Three resolution paths:

    1. ``cfg`` already has a ``destinations`` dict → pass through, expanding
       ``${VAR}`` in every destination's ``url`` and ``api_key``.
    2. ``cfg`` has the simple legacy scalars ``context_intelligence_server_url``
       (+ optionally ``context_intelligence_api_key``) → translate into the
       primary ``destinations`` shape. Only synthesises the remote destination
       when *both* url **and** api_key are non-empty after ``${VAR}`` expansion;
       otherwise returns local-only ``{}``.
    3. Nothing configured → return ``{}`` (local-only, the normal default).
    """
    try:
        import yaml  # pyright: ignore[reportMissingModuleSource]
    except Exception:  # noqa: BLE001
        return {}
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    overrides = (data.get("overrides") or {}).get("hook-context-intelligence") or {}
    cfg = overrides.get("config") or {}

    # --- Path 1: caller already supplied the destinations shape ---------------
    if isinstance(cfg.get("destinations"), dict):
        destinations: dict[str, Any] = {}
        for name, dest in cfg["destinations"].items():
            if not isinstance(dest, dict):
                continue
            expanded: dict[str, Any] = dict(dest)
            if isinstance(expanded.get("url"), str):
                expanded["url"] = os.path.expandvars(expanded["url"])
            if isinstance(expanded.get("api_key"), str):
                expanded["api_key"] = os.path.expandvars(expanded["api_key"])
            destinations[name] = expanded
        return {"destinations": destinations} if destinations else {}

    # --- Path 2: legacy convenience scalars → translate to destinations -------
    raw_url = str(cfg.get("context_intelligence_server_url") or "").strip()
    raw_key = str(cfg.get("context_intelligence_api_key") or "").strip()
    url = os.path.expandvars(raw_url) if raw_url else ""
    key = os.path.expandvars(raw_key) if raw_key else ""
    if url and key:
        return {
            "destinations": {
                "default": {
                    "url": url,
                    "api_key": key,
                    "include": ["**"],
                }
            }
        }

    # --- Path 3: nothing configured → local-only (the normal default) --------
    return {}


# --------------------------------------------------------------------------
# DOT preparation: $var substitution + per-node provider injection
# --------------------------------------------------------------------------


def _substitute_models(dot_text: str, policy: WikiPolicy) -> str:
    """Apply per-node llm_provider / llm_model substitutions to a synthesize DOT text.

    Loops over LLM_NODE_IDS, resolves the concrete model id for each node via
    the policy, and substitutes the ``llm_provider`` and ``llm_model`` attributes
    in-place.  Returns the modified DOT text.

    Factored out of ``build_dot`` so that ``run_ingest`` can materialise a fully
    resolved ``synthesize.dot`` into the run directory — making the tool-module
    path honour ``WIKI_WEAVER_MODEL`` exactly as the CLI path already did.
    """
    for node_id in LLM_NODE_IDS:
        node_opener = f"    {node_id} [\n"
        idx = dot_text.find(node_opener)
        if idx == -1:
            continue
        node_close = dot_text.find("\n    ]", idx + len(node_opener))
        if node_close == -1:
            continue
        block = dot_text[idx:node_close]
        spec = policy.model_for(node_id)
        concrete = resolve_model(policy.provider, spec)
        block = re.sub(
            r'llm_provider="[^"]*"', f'llm_provider="{policy.provider}"', block
        )
        block = re.sub(r'llm_model="[^"]*"', f'llm_model="{concrete}"', block)
        dot_text = dot_text[:idx] + block + dot_text[node_close:]
    return dot_text


def build_dot(
    source_path: Path,
    wiki_dir: Path,
    policy: WikiPolicy,
    source_id: int | str = "",
) -> str:
    """Read the inner DOT, substitute its required context variables with
    concrete ABSOLUTE paths.

    ``llm_provider`` and ``llm_model`` are declared directly in synthesize.dot
    (making the DOT self-contained so it works both as a direct pipeline and as
    a folder sub-pipeline without requiring build_dot injection).

    ``policy`` — the resolved WikiPolicy for this wiki (from load_policy).
    Default policy (no project policy/ dir) produces byte-identical output
    to the pre-Phase-D code that used module-level constants.

    ``source_id`` is the stable id the CLI assigned to this source BEFORE
    ingest (Fix 3). It is injected as ``$source_id`` so the ingest node uses
    the authoritative id instead of guessing one per run.
    """
    import sys

    # Inner DOT source: project override when present, else engine default.
    dot = policy.inner_dot_path.read_text(encoding="utf-8")

    # The validator writes its structured PASS/FAIL result to this known file on
    # EVERY run (--out). The feedback + refine-ingest nodes are told to READ it,
    # so the deterministic failures are plumbed into the remediation path
    # (PIPELINE_DESIGN.md §4). Dotted context keys are silently dropped in
    # box-node prompts, so a file is the reliable hand-off channel.
    validation_report = wiki_dir / ".ai" / "validation.md"
    validate_cmd = (
        f"{sys.executable} {VALIDATE_PY} {wiki_dir} --out {validation_report}"
    )
    # Append --config when the wiki supplies a project validator config so that
    # the in-pipeline validate node uses the same constants as cmd_lint.
    if policy.validator_config_path is not None:
        validate_cmd += f" --config {policy.validator_config_path}"

    normalize_cmd = f"{sys.executable} {NORMALIZE_PY} {wiki_dir}"
    footnotes_cmd = f"{sys.executable} {FOOTNOTES_PY} {wiki_dir}"
    normalize_unicode_cmd = f"{sys.executable} {NORMALIZE_UNICODE_PY} {wiki_dir}"

    substitutions = {
        "$source_path": str(source_path),
        "$wiki_dir": str(wiki_dir),
        "$validation_report": str(validation_report),
        # Schema / rubric: project override or engine default (byte-identical
        # for default wikis because policy defaults == the original constants).
        "$schema_path": str(policy.schema_path),
        # assess uses the PER-SOURCE convergence rubric, NOT the whole-corpus
        # eval rubric (which would vote `refine` forever on a single article).
        "$convergence_rubric": str(policy.convergence_rubric_path),
        # $rubric_path retained for any eval-grading reuse; no longer referenced
        # by the inner pipeline's assess node (kept reserved for the eval grader).
        "$rubric_path": str(RUBRIC_PATH),
        "$normalize_cmd": normalize_cmd,
        "$footnotes_cmd": footnotes_cmd,
        "$normalize_unicode_cmd": normalize_unicode_cmd,
        "$validate_cmd": validate_cmd,
        "$max_cycles": str(policy.max_cycles),
        "$source_id": str(source_id),
    }
    for var, value in substitutions.items():
        dot = dot.replace(var, value)

    # Apply per-node provider / model overrides from policy.
    #
    # synthesize.dot bakes in defaults (llm_provider="anthropic",
    # llm_model="claude-sonnet-4-6") as self-contained fallbacks so the DOT can
    # be loaded directly by the engine without Python injection.  We always
    # override them here with resolved values so that:
    #   - family tokens ("sonnet", "haiku") resolve to the newest served id, and
    #   - per-stage overrides from wiki.config.yaml take effect.
    dot = _substitute_models(dot, policy)

    return dot


# --------------------------------------------------------------------------
# spawn capability
# --------------------------------------------------------------------------
#
# Agent resolution (per-node config -> a self-contained child Bundle),
# recursion-avoidance, and Layer-1 (system prompt) delivery are now owned
# entirely by amplifier_module_pipeline_runner.run_pipeline's own
# make_spawn_fn / _resolve_agent_bundle (see that module's docstrings).
#
# LAYER-1 DECISION (do not re-introduce context.include processing here):
# wiki-weaver has no tuned Layer-1 of its own -- its authoring instructions
# live in each .dot node's ``prompt=`` text (additive), and its agents are
# the bare attractor-pipeline agents (no per-agent context/system_prompt
# override). run_pipeline's agent resolution deliberately does NOT process
# an agent's ``context.include`` block as Layer-1 -- it relies on
# loop-agent's provider-default selection (``context/system-<provider>.md``),
# which is fail-loud on an empty Layer-1. A successful spawn is therefore
# proof the real provider system prompt was read.
#
# What THIS module still owns: the two wiki-weaver-specific constraints
# applied to a spawned child via run_pipeline's ``child_constraint`` seam
# (filesystem isolation for ingest agents; read-only scoping for the ask
# agent) -- see _fs_child_constraint / _ask_child_constraint below.


# --------------------------------------------------------------------------
# Fix 1 -- per-node filesystem isolation
# --------------------------------------------------------------------------
#
# Process state (the .wiki/.processed.jsonl ledger and the _sources/ directory) is the
# deterministic CLI's EXCLUSIVE responsibility. A spawned LLM node must be
# PHYSICALLY UNABLE to write it. The tool-filesystem module honours a
# ``denied_write_paths`` config (DENY wins over ALLOW in is_path_allowed), so we
# inject the ledger + archive paths into every spawned child agent's filesystem
# tool config. This blocks the write_file / edit_file tools at the source.
#
# HONEST LIMITATION: tool-bash has no path-level sandbox (only command
# allow/deny lists), so a determined agent could still shell its way around the
# filesystem deny-list. That escape hatch is covered by the deterministic GUARD
# in cli/wiki_weaver.py (Fix 1b), which fails loud on any agent-written ledger
# line / archive move the CLI did not perform. Prevention here; safety net there.


def _denied_process_paths(wiki_dir: Path) -> list[str]:
    """The two process-state paths a spawned node must never write."""
    wiki_dir = Path(wiki_dir).resolve()
    return [str(wiki_ledger(wiki_dir)), str(wiki_sources(wiki_dir))]


def _constrain_agent_fs(child_bundle: Any, wiki_dir: Path) -> None:
    """Inject ``denied_write_paths`` for the ledger + archive into the child
    bundle's tool-filesystem config, in place. Idempotent.
    """
    denied = _denied_process_paths(wiki_dir)
    tools = getattr(child_bundle, "tools", None)
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("module") != "tool-filesystem":
            continue
        cfg = tool.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
            tool["config"] = cfg
        existing = cfg.get("denied_write_paths") or []
        merged = list(dict.fromkeys([*existing, *denied]))
        cfg["denied_write_paths"] = merged


def _fs_child_constraint(wiki_dir: Path) -> Callable[[Any], Any]:
    """Build a ``run_pipeline(child_constraint=...)`` callable for Fix 1.

    Wraps ``_constrain_agent_fs`` (which mutates the child bundle in place
    and returns ``None``) into the ``Callable[[Bundle], Bundle]`` shape
    ``run_pipeline``'s ``child_constraint`` seam expects. Applied to every
    spawned child of an ingest/init pipeline so it physically cannot write
    the ledger or _sources/ (DENY beats ALLOW in the filesystem tool).
    """

    def _constrain(child_bundle: Any) -> Any:
        _constrain_agent_fs(child_bundle, wiki_dir)
        return child_bundle

    return _constrain


# --------------------------------------------------------------------------
# Ask pipeline: single-shot wiki reading + cited answer
# --------------------------------------------------------------------------
#
# MECHANISM (structural, not instructional): each spawned child agent has
#   - tool-bash + tool-web-* REMOVED from its tools list (structural exclusion)
#   - tool-filesystem denied_write_paths=["/"] (deny all writes)
#   - tool-filesystem root_path + allowed_read_paths = wiki_dir (constrain reads)
# This forces the agent to ground answers in wiki content — it structurally
# cannot write files, shell out, or fetch from the web.


@dataclass
class AskResult:
    """Outcome of a wiki-ask operation."""

    answer: str
    pages_used: list[str]
    refused: bool
    raw: str
    logs_dir: Path


def _dot_escape_prompt(s: str) -> str:
    """Escape a Python string for use as a DOT double-quoted attribute value.

    DOT escape conventions used by the loop-pipeline engine:
      \\  ->  \\\\  (backslashes first)
      "   ->  \\"   (embedded double quotes)
      newline -> \\n  (literal \\n in DOT file; engine reconstructs newline)
    """
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    return s


_ASK_ESCAPE_MODULES: frozenset[str] = frozenset(
    {"tool-bash", "tool-web-fetch", "tool-search-web", "tool-web", "tool-web-search"}
)


def _constrain_ask_agent(child_bundle: Any, wiki_dir: Path, answer_file: Path) -> None:
    """Scope the child agent to read-only wiki access (THE MECHANISM).

    Structural steps:
    1. Remove tool-bash / tool-web-* from the tools list — the agent CANNOT
       shell or fetch from the web. Works when tools are still in dict form
       (pre-prepare bundle). No-op if already resolved to module objects.
    2. Set tool-filesystem ``denied_write_paths=[wiki_dir]`` — deny writes to
       all wiki content (index, pages, .sources.json). The agent CAN write the
       answer to ``answer_file`` (a temp path outside wiki_dir) so the full
       response survives past the pipeline's notes truncation.
    3. Set ``root_path`` + ``allowed_read_paths`` on tool-filesystem to
       ``wiki_dir`` — constrains reads to wiki dir (best-effort; honoured if
       the module supports these config keys, silently ignored otherwise).

    HONEST LIMITATION: bash removal relies on dict-form tools. The write-deny
    constraint is the structural backstop regardless of tool form.
    """
    tools = getattr(child_bundle, "tools", None)
    if not isinstance(tools, list):
        return

    # Step 1: remove bash/web tools (structural).
    removals = [
        i
        for i, t in enumerate(tools)
        if isinstance(t, dict) and t.get("module", "") in _ASK_ESCAPE_MODULES
    ]
    for i in reversed(removals):
        tools.pop(i)

    # Step 2+3: scope filesystem tool.
    wiki_dir_s = str(wiki_dir)
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if tool.get("module") != "tool-filesystem":
            continue
        cfg = tool.get("config")
        if not isinstance(cfg, dict):
            cfg = {}
            tool["config"] = cfg
        # Deny writes to wiki content (protects all wiki pages and metadata).
        # answer_file is outside wiki_dir so the agent can still write it.
        cfg["denied_write_paths"] = [wiki_dir_s]
        # Best-effort read scoping — silently ignored if unsupported.
        cfg["root_path"] = wiki_dir_s
        cfg["allowed_read_paths"] = [wiki_dir_s]


def _ask_child_constraint(wiki_dir: Path, answer_file: Path) -> Callable[[Any], Any]:
    """Build a ``run_pipeline(child_constraint=...)`` callable for the ask pipeline.

    Wraps ``_constrain_ask_agent`` (mutates in place, returns ``None``) into
    the ``Callable[[Bundle], Bundle]`` shape ``run_pipeline``'s
    ``child_constraint`` seam expects. Applied to the single spawned "answer"
    child so it structurally cannot modify wiki content, shell, or fetch from
    the web -- it can read within ``wiki_dir`` and write the answer to
    ``answer_file`` (a temp path outside wiki_dir).
    """

    def _constrain(child_bundle: Any) -> Any:
        _constrain_ask_agent(child_bundle, wiki_dir, answer_file)
        return child_bundle

    return _constrain


def _parse_ask_output(text: str) -> tuple[str, list[str], bool]:
    """Extract (answer, pages_used, refused) from raw pipeline output.

    The loop-pipeline wraps the agent's response as:
      {"status": "success", "notes": "Plain text response: <agent text>", ...}
    or, if the agent output JSON directly:
      {"answer": "...", "pages_used": [...], "refused": false}

    Steps:
    1. Parse as JSON — if it has "notes" (pipeline wrapper), unwrap and recurse;
       if it has "answer" (direct ask result), return it.
    2. Search for the last ```json...``` fenced block.
    3. Search for the last JSON object containing an "answer" key.
    4. Fall back to the full text.
    """
    import json as _json

    # Step 1: try parsing as JSON (handles pipeline wrapper and direct answers).
    try:
        data = _json.loads(text)
        if isinstance(data, dict):
            if "answer" in data:
                # Direct ask-result JSON from the agent.
                return (
                    str(data["answer"]),
                    [str(p) for p in (data.get("pages_used") or [])],
                    bool(data.get("refused", False)),
                )
            if "notes" in data:
                # Loop-pipeline wrapper: agent response is in "notes".
                notes = str(data["notes"])
                # Strip "Plain text response: " prefix added by the pipeline.
                _PREFIX = "Plain text response: "
                if notes.startswith(_PREFIX):
                    notes = notes[len(_PREFIX) :]
                if notes:
                    return _parse_ask_output(notes)
    except (ValueError, TypeError):
        pass

    # Step 2: last ```json...``` fenced block.
    fenced = list(re.finditer(r"```json\s*(.*?)```", text, re.DOTALL))
    if fenced:
        json_str = fenced[-1].group(1).strip()
        try:
            data = _json.loads(json_str)
            if isinstance(data, dict) and "answer" in data:
                return (
                    str(data["answer"]),
                    [str(p) for p in (data.get("pages_used") or [])],
                    bool(data.get("refused", False)),
                )
        except (ValueError, TypeError):
            pass

    # Step 3: last JSON object containing an "answer" key.
    candidates = list(re.finditer(r'\{"answer".*?\}', text, re.DOTALL))
    if candidates:
        json_str = candidates[-1].group(0)
        try:
            data = _json.loads(json_str)
            if isinstance(data, dict) and "answer" in data:
                return (
                    str(data["answer"]),
                    [str(p) for p in (data.get("pages_used") or [])],
                    bool(data.get("refused", False)),
                )
        except (ValueError, TypeError):
            pass

    # Step 4: return the full text trimmed.
    return text.strip(), [], False


def build_ask_dot(
    wiki_dir: Path,
    question: str,
    answer_file: Path,
    *,
    provider: str = PROVIDER,
    model: str = MODEL,
) -> str:
    """Build the single-node DOT pipeline for a wiki ask query.

    The DOT has one LLM node ("answer") that is instructed to:
      - Read index.md + overview.md to route to relevant pages
      - Follow [[wikilinks]] 1-2 hops
      - Synthesize a cited answer from wiki content
      - Write the answer JSON to answer_file
      - FAIL LOUD if the wiki does not cover the question

    ``provider`` / ``model`` — resolved from load_policy(wiki_dir) by the
    caller so the wiki's model-tier knob (``models.ask`` / ``models.default``)
    applies to retrieval too.  Defaults are the module-level constants;
    byte-identical for default wikis.

    The agent writes its full answer to ``answer_file`` to bypass the
    loop-pipeline's notes-field truncation. The spawned agent's tools are
    constrained by _ask_child_constraint (wiki writes denied, bash/web removed).
    """
    wiki_abs = str(wiki_dir.resolve())
    sources_json = str(wiki_registry(wiki_dir))
    answer_file_s = str(answer_file)

    # Build the agent instruction (real Python newlines; _dot_escape_prompt
    # encodes them as \\n for the DOT attribute).
    prompt = (
        "You are a wiki reader. Answer the question ONLY from the compiled wiki.\n"
        "\n"
        f"WIKI DIRECTORY: {wiki_abs}\n"
        f"QUESTION: {question}\n"
        "\n"
        "PROCEDURE — follow in order:\n"
        f"1. Read {wiki_abs}/index.md to find pages relevant to the question.\n"
        f"2. Read {wiki_abs}/overview.md to understand the wiki scope.\n"
        "3. Read the 2-3 most relevant pages from the index.\n"
        "4. Follow [[wikilinks]] in those pages (up to 2 hops) for related content.\n"
        f"5. Read {sources_json} to resolve source IDs to author+URL for citations.\n"
        "\n"
        "ANSWER RULES:\n"
        "  COVERED: Write a direct cited answer. Name every wiki page used.\n"
        "  Cite as [Author, URL] from .sources.json; cite by page name if no\n"
        "  author/URL is available.\n"
        f"  NOT COVERED: Say EXACTLY: \"The wiki does not cover '{question}'."
        ' Pages consulted: [list pages you read]."\n'
        "  Do NOT use training knowledge. Do NOT refuse silently. FAIL LOUD.\n"
        "  PARTIAL: State clearly what IS and what IS NOT covered.\n"
        "  Ground EVERY claim in wiki content you actually read. No fabrication.\n"
        "\n"
        "REQUIRED OUTPUT STEP (mandatory — do this last, after your reasoning):\n"
        f"Write a JSON file to exactly this path: {answer_file_s}\n"
        "The file content must be a single JSON object:\n"
        '{"answer": "<full answer text>", "pages_used": ["page.md"], "refused": false}\n'
        "For refused: set refused=true, list examined pages in the answer field.\n"
        "The file MUST be written before you finish — this is how your answer is captured."
    )

    prompt_dot = _dot_escape_prompt(prompt)

    # Resolve family token to a concrete served model id before injecting into DOT.
    model = resolve_model(provider, model)

    # Build DOT using plain string concatenation for lines with literal { }
    # to avoid Python f-string brace conflicts.
    lines = [
        "digraph ask_wiki {",
        '    graph [goal="Answer question from compiled wiki", default_fidelity="compact"]',
        '    start [shape=Mdiamond, label="Start"]',
        "    answer [",
        '        label="Read wiki and answer",',
        f'        llm_provider="{provider}",',
        f'        llm_model="{model}",',
        f'        prompt="{prompt_dot}"',
        "    ]",
        '    done [shape=Msquare, label="Done"]',
        "    start -> answer -> done",
        "}",
        "",
    ]
    return "\n".join(lines)


def build_ask_dot_from_file(
    wiki_dir: Path,
    question: str,
    answer_file: Path,
    *,
    provider: str = PROVIDER,
    model: str = MODEL,
) -> str:
    """Build the ask DOT pipeline by reading pipeline/ask.dot and substituting tokens.

    Mirrors build_ask_dot() but reads the static ASK_DOT template rather than
    building the DOT as a Python string.  The two functions are byte-identical for
    the same inputs.

    Token substitution:
      $wiki_dir    -> str(wiki_dir.resolve())
      $sources_json -> str(wiki_registry(wiki_dir))  # <wiki>/.wiki/.sources.json
      $answer_file -> str(answer_file)
      $question    -> _dot_escape_prompt(question)   (DOT-escaping for the prompt context)

    The template bakes llm_provider="anthropic" / llm_model="claude-sonnet-4-6".
    If policy differs, those values are replaced with the supplied provider/model.
    """
    wiki_abs = str(wiki_dir.resolve())
    sources_json = str(wiki_registry(wiki_dir))
    answer_file_s = str(answer_file)

    dot = ASK_DOT.read_text(encoding="utf-8")

    # Substitute path tokens — plain Linux paths, no DOT-escaping needed.
    dot = dot.replace("$wiki_dir", wiki_abs)
    dot = dot.replace("$sources_json", sources_json)
    dot = dot.replace("$answer_file", answer_file_s)
    # Question is user-supplied and may contain DOT-special chars; escape before injecting.
    dot = dot.replace("$question", _dot_escape_prompt(question))

    # Resolve family token to a concrete served model id before substitution.
    model = resolve_model(provider, model)

    # Apply provider/model override — replace the baked defaults unconditionally so
    # the call is always correct regardless of env-var PROVIDER/MODEL values.
    dot = dot.replace('llm_provider="anthropic"', f'llm_provider="{provider}"')
    dot = dot.replace('llm_model="claude-sonnet-4-6"', f'llm_model="{model}"')

    return dot


def run_ask(
    wiki_dir: str | Path,
    question: str,
) -> AskResult:
    """Answer a question by reading the compiled wiki (no embeddings/RAG).

    Navigates index.md + [[wikilinks]] to find grounded content, synthesizes
    a cited answer, and explicitly refuses with a loud "the wiki does not cover X"
    if the topic is absent.

    The spawned agent session is CI-instrumented (inherits the hook from the
    composed bundle) so cost/token/artifact events are captured in
    ``<wiki_dir>/.wiki/runs/ask-<ts>/`` alongside the ask.dot and session logs.

    Tool scoping (THE MECHANISM — structural, not instructional):
      - tool-bash and web tools REMOVED from the spawned agent's tools list
      - tool-filesystem: denied_write_paths=[wiki_dir] (protect wiki content)
        + root_path + allowed_read_paths=wiki_dir (constrain reads)
    The agent cannot modify wiki content or escape to the web. It CAN write
    its full answer to a temp file (answer_file) outside wiki_dir so the
    response survives the loop-pipeline's notes-field truncation.
    """
    wiki_dir = Path(wiki_dir).resolve()
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")

    import uuid

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / f"ask-{timestamp}-{uuid.uuid4().hex[:8]}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # The answer_file lives outside wiki_dir so the denied_write_paths
    # constraint does not block the agent from writing its answer.
    answer_file = Path(f"/tmp/wiki_ask_{timestamp}_{uuid.uuid4().hex[:8]}.json")

    # Resolve provider/model from the wiki's policy so the model-tier knob
    # (models.ask / models.default) applies to retrieval too.
    _ask_policy = load_policy(wiki_dir)
    dot_source = build_ask_dot_from_file(
        wiki_dir,
        question,
        answer_file,
        provider=_ask_policy.provider,
        model=_ask_policy.model_for("ask"),
    )
    (logs_dir / "ask.dot").write_text(dot_source, encoding="utf-8")

    result = _run_coro(
        run_pipeline(
            dot_source,
            cwd=wiki_dir,
            logs_root=logs_dir,
            provider=_ask_policy.provider,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            # THE MECHANISM: constrain the spawned "answer" child to
            # read-only wiki access (wiki writes denied, bash/web removed).
            child_constraint=_ask_child_constraint(wiki_dir, answer_file),
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )
    raw_text = result.raw

    # Primary: read from answer_file (full response; bypasses notes truncation).
    if answer_file.exists():
        try:
            file_json = answer_file.read_text(encoding="utf-8")
            answer, pages_used, refused = _parse_ask_output(file_json)
            # Copy to logs dir for posterity, then clean up.
            (logs_dir / "ask_answer.json").write_text(file_json, encoding="utf-8")
            answer_file.unlink(missing_ok=True)
            return AskResult(
                answer=answer,
                pages_used=pages_used,
                refused=refused,
                raw=file_json[:5000],
                logs_dir=logs_dir,
            )
        except (OSError, ValueError):
            pass

    # Fallback: extract from (truncated) pipeline result notes.
    answer, pages_used, refused = _parse_ask_output(raw_text)
    return AskResult(
        answer=answer,
        pages_used=pages_used,
        refused=refused,
        raw=raw_text[:5000],
        logs_dir=logs_dir,
    )


# --------------------------------------------------------------------------
# Context-intelligence hook overlay
# --------------------------------------------------------------------------


def _ci_overlay() -> Any:
    """Build the context-intelligence hook overlay Bundle.

    Passed to every ``run_pipeline`` call via ``extra_overlays`` so it is
    composed onto the session (and inherited by every spawned child) exactly
    as ``_build_prepared`` used to compose it directly. The hook's
    ``LoggingHandler`` is always-on (writes per-session events.jsonl locally
    regardless of config), so this overlay is unconditional -- an empty
    ``load_ci_config()`` result ({} -- no remote destinations) still yields a
    valid overlay that enables local-only logging, matching prior behavior.
    """
    from amplifier_foundation import Bundle

    ci_cfg = load_ci_config()
    return Bundle(
        name="wiki-weaver-ci",
        version="1.0.0",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": CI_HOOK_SOURCE,
                "config": ci_cfg,
            }
        ],
    )


# --------------------------------------------------------------------------
# Single shared event loop for one ingest drive
# --------------------------------------------------------------------------

# Single shared event loop for one ingest DRIVE (Option A). While a runner is
# installed here, every engine pipeline run (run_inner per source, the overview
# re-weave) reuses ONE loop, so the load-once _BASE_BUNDLE provider client stays
# bound to a LIVE loop for the whole drain. Without this, each per-source
# asyncio.run() opened and CLOSED its own loop, and source N+1 reused the
# closed-loop client -> "RuntimeError: Event loop is closed" (fatal on 3.13).
_ACTIVE_RUNNER: asyncio.Runner | None = None


def _run_coro(coro: Any) -> Any:
    """Drive a coroutine to completion on the right loop.

    Reuses the shared ingest loop when ``shared_engine_loop()`` is active
    (single-loop drain); otherwise falls back to a private one-shot
    ``asyncio.run()`` loop (single-file ingest, ask/lint/init, the tool-module
    ``run_ingest`` path, and ``__main__``). Callers are unchanged sync code.
    """
    if _ACTIVE_RUNNER is not None:
        return _ACTIVE_RUNNER.run(coro)
    return asyncio.run(coro)


@contextmanager
def shared_engine_loop() -> Iterator[asyncio.Runner]:
    """Install ONE asyncio event loop for an entire ingest drive.

    Every ``_run_coro()`` call made inside this context shares the runner's
    loop, so the cached ``_BASE_BUNDLE`` provider client is created and reused
    on a single live loop for the whole drain (sources + the final re-weave).
    The loop is closed exactly once, when the context exits.

    Re-entrant: a nested call reuses the already-installed runner rather than
    opening a second loop.
    """
    global _ACTIVE_RUNNER
    if _ACTIVE_RUNNER is not None:
        # Already inside a shared loop -> reuse it; don't nest runners.
        yield _ACTIVE_RUNNER
        return
    runner = asyncio.Runner()
    _ACTIVE_RUNNER = runner
    try:
        yield runner
    finally:
        _ACTIVE_RUNNER = None
        runner.close()


# --------------------------------------------------------------------------
# Public entrypoints
# --------------------------------------------------------------------------
#
# Each entrypoint below builds its .dot pipeline (product logic, unchanged)
# and calls run_pipeline (amplifier_module_pipeline_runner) to drive it. Every
# call uniformly gets: extra_overlays=[_ci_overlay()] (parity with the old
# _build_prepared, which always composed the CI hook), profiles=None (the
# runner's DEFAULT_PROFILES already maps anthropic/openai/gemini -> the
# matching attractor-agent-* child, same routing wiki-weaver relied on), and
# spawn_timeout=SPAWN_TIMEOUT_SECONDS (parity with the old _spawn_with_timeout,
# now enforced inside the runner's own make_spawn_fn).


def run_inner(
    source_path: str | Path,
    wiki_dir: str | Path,
    *,
    max_cycles: int | None = None,
    source_id: int | str = "",
) -> InnerResult:
    """Run the inner convergence pipeline for ONE source through the engine.

    ``max_cycles`` — when set, overrides the wiki's wiki.config.yaml value
    (CLI flag beats config file).  When None, the policy resolves from config
    (default 3 if not configured).

    ``source_id`` is the stable id the CLI assigned to this source (Fix 3); it
    is threaded into the ingest node as ``$source_id``.
    """
    source_path = Path(source_path).resolve()
    wiki_dir = Path(wiki_dir).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source not found: {source_path}")
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")

    policy = load_policy(wiki_dir, cli_max_cycles=max_cycles)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / timestamp
    logs_dir.mkdir(parents=True, exist_ok=True)

    dot_source = build_dot(source_path, wiki_dir, policy, source_id=source_id)
    (logs_dir / "inner.dot").write_text(dot_source, encoding="utf-8")

    result = _run_coro(
        run_pipeline(
            dot_source,
            cwd=wiki_dir,
            logs_root=logs_dir,
            provider=policy.provider,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            child_constraint=_fs_child_constraint(wiki_dir),
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )
    status = result.status or "unknown"
    return InnerResult(
        status=status,
        converged=status == "success",
        logs_dir=logs_dir,
        notes=result.notes[:2000],
        # NOTE: PipelineResult does not carry the engine's Outcome.failure_reason
        # (only status/notes) -- see the migration report for this known gap.
        failure_reason=None,
    )


def run_ingest(
    wiki_dir: str | Path,
    max_cycles: int | None = None,
) -> InnerResult:
    """Run the full inbox DRAIN loop via ingest.dot.

    ``ingest.dot`` orchestrates a full drain of _inbox/ in a single engine run:
      1. A setup tool node (ingest_setup.py) that picks the next inbox source,
         assigns a stable source id, and publishes context keys as JSON
         (including has_source, archive_cmd, fail_cmd, and synthesize keys).
         Returns has_source=false when the inbox is empty -- the normal
         drain-complete signal.
      2. A folder sub-pipeline (synthesize.dot) that integrates the source,
         inheriting all context keys from step 1.
      3. An archive tool node (ingest_archive.py) on convergence -- moves the
         source to _sources/, appends the ledger, marks ingested in .wiki/.sources.json.
      4. A fail_handler tool node (ingest_fail.py) on non-convergence -- moves
         the source to _failed/ so the inbox keeps shrinking.
      5. loop_restart back to setup until has_source=false.

    Safety bound: ``max_drain_iters`` is computed as max(20, inbox_count * 5)
    and substituted into ingest.dot's ``default_max_retry`` graph attribute.
    If the engine hits the bound it fails loudly -- a bug would spin forever
    otherwise.  For a 7-source inbox the bound is 35; well clear of normal
    operation but finite.

    ``max_cycles`` is unused (the policy is read by ingest_setup.py and
    passed as a context key into synthesize.dot). Retained for API symmetry
    with run_inner.
    """
    import sys

    wiki_dir = Path(wiki_dir).resolve()
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")

    # Compute a generous-but-finite loop safety bound based on the current
    # inbox size.  Each drain cycle (one source) uses one loop_restart, so
    # a 7-source inbox needs 7 iterations; the 5x multiplier gives headroom
    # for retries and partial-failure recovery while keeping it finite.
    # The bound is substituted into ingest.dot as default_max_retry.
    inbox = wiki_dir / "_inbox"
    if inbox.is_dir():
        inbox_count = sum(
            1 for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")
        )
    else:
        inbox_count = 0
    max_drain_iters = max(20, inbox_count * 5)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / f"ingest-{timestamp}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Substitute compile-time tokens in ingest.dot (paths and bounds that
    # must be concrete values, not expanded from engine context).
    dot_template = INGEST_DOT.read_text(encoding="utf-8")
    setup_cmd = (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(INGEST_SETUP_PY))}"
        f" {shlex.quote(str(wiki_dir))} {shlex.quote(str(wiki_dir))}"
    )

    # Materialise a fully-resolved synthesize.dot in the run directory so that
    # the engine executes with the WIKI_WEAVER_MODEL / wiki.config.yaml model,
    # not the hardcoded defaults baked into the package file.  This closes the
    # gap where the tool-module path (run_ingest) silently ignored the resolved
    # model while the CLI path (run_inner / build_dot) honoured it.
    policy = load_policy(wiki_dir)
    inner_text = INNER_DOT.read_text(encoding="utf-8")
    inner_text = _substitute_models(inner_text, policy)
    resolved_synthesize_dot = logs_dir / "synthesize.dot"
    resolved_synthesize_dot.write_text(inner_text, encoding="utf-8")
    synthesize_dot_abs = str(resolved_synthesize_dot)

    dot_source = dot_template.replace("$setup_cmd", setup_cmd)
    dot_source = dot_source.replace("$synthesize_dot", synthesize_dot_abs)
    dot_source = dot_source.replace("$max_drain_iters", str(max_drain_iters))

    (logs_dir / "ingest.dot").write_text(dot_source, encoding="utf-8")

    result = _run_coro(
        run_pipeline(
            dot_source,
            cwd=wiki_dir,
            logs_root=logs_dir,
            provider=policy.provider,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            child_constraint=_fs_child_constraint(wiki_dir),
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )
    status = result.status or "unknown"
    return InnerResult(
        status=status,
        converged=status == "success",
        logs_dir=logs_dir,
        notes=result.notes[:2000],
        # See run_inner's note: PipelineResult carries no failure_reason.
        failure_reason=None,
    )


THIN_SLICE_DOT = """\
digraph engine_smoke {{
    graph [goal="Prove the PreparedBundle path spawns a tool-capable child session"]

    start [shape=Mdiamond, label="Start"]
    write [
        label="Write proof file",
        llm_provider="{provider}",
        prompt="Write the exact text 'engine-ok' (no quotes, no trailing newline) to the file {out_path} using your filesystem tools. Then reply with the single word DONE."
    ]
    done [shape=Msquare, label="Done"]

    start -> write -> done
}}
"""


def run_thin_slice(
    out_path: str | Path, cwd: str | Path | None = None
) -> dict[str, Any]:
    """THIN SLICE: a trivial one-box DOT that writes 'engine-ok' to ``out_path``
    through the full proper PreparedBundle path. Make-or-break proof.

    Returns a small dict with the raw engine text and the resolved out_path.
    """
    out_path = Path(out_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cwd = Path(cwd).resolve() if cwd else out_path.parent

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = out_path.parent / ".thin-runs" / timestamp
    logs_dir.mkdir(parents=True, exist_ok=True)

    dot_source = THIN_SLICE_DOT.format(provider=PROVIDER, out_path=str(out_path))
    (logs_dir / "thin.dot").write_text(dot_source, encoding="utf-8")

    result = _run_coro(
        run_pipeline(
            dot_source,
            cwd=cwd,
            logs_root=logs_dir,
            provider=PROVIDER,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )
    return {
        "out_path": str(out_path),
        "exists": out_path.is_file(),
        "content": out_path.read_text(encoding="utf-8") if out_path.is_file() else None,
        "logs_dir": str(logs_dir),
        "raw": result.raw[:2000],
    }


# --------------------------------------------------------------------------
# Lint pipeline: single-shot structural validation
# --------------------------------------------------------------------------
#
# MECHANISM: a deterministic tool-only pipeline (no LLM) that runs the same
# validate_wiki.py the in-pipeline `validate` node uses. Keeps `wiki-weaver lint`
# and the pipeline's structural gate structurally aligned — one validator, two
# entry points. Uses --out to write a result file so run_lint can recover the
# validator's pass/fail verdict from outside the engine.

# lint.dot: the single-node lint pipeline (static DOT with $var token).
LINT_DOT = PIPELINE_DIR / "lint.dot"


def build_lint_dot(wiki_dir: Path, lint_result_file: Path) -> str:
    """Build the lint DOT pipeline as a Python string (reference builder).

    Token substitution mirrors build_lint_dot_from_file:
      $validate_cmd -> python validate_wiki.py <wiki_abs> [--config <cfg>] --out <lint_result_file>

    ``wiki_dir`` is resolved to an absolute path so the DOT is self-contained.
    If ``<wiki_dir>/.wiki/policy/validator.yaml`` exists, ``--config`` is appended
    (identical behaviour to lib.lint).

    Byte-identical to build_lint_dot_from_file for the same inputs.
    """
    import sys

    wiki_abs = str(wiki_dir.resolve())
    validator_cfg = wiki_policy_dir(wiki_dir) / "validator.yaml"
    validate_cmd = f"{sys.executable} {VALIDATE_PY} {wiki_abs}"
    if validator_cfg.is_file():
        validate_cmd += f" --config {validator_cfg}"
    validate_cmd += f" --out {lint_result_file}"

    lines = [
        "digraph lint_wiki {",
        '    graph [goal="Validate wiki structure"]',
        '    start [shape=Mdiamond, label="Start"]',
        "    lint [",
        "        shape=parallelogram,",
        '        label="Validate Structure",',
        f'        tool_command="{validate_cmd}"',
        "    ]",
        '    done [shape=Msquare, label="Done"]',
        "    start -> lint",
        '    lint -> done [label="pass", condition="outcome=success"]',
        '    lint -> done [label="fail", condition="outcome=fail"]',
        "}",
        "",
    ]
    return "\n".join(lines)


def build_lint_dot_from_file(wiki_dir: Path, lint_result_file: Path) -> str:
    """Build the lint DOT pipeline by reading pipeline/lint.dot and substituting tokens.

    Token substitution:
      $validate_cmd -> python validate_wiki.py <wiki_abs> [--config <cfg>] --out <lint_result_file>

    Byte-identical to build_lint_dot for the same inputs.
    """
    import sys

    wiki_abs = str(wiki_dir.resolve())
    validator_cfg = wiki_policy_dir(wiki_dir) / "validator.yaml"
    validate_cmd = f"{sys.executable} {VALIDATE_PY} {wiki_abs}"
    if validator_cfg.is_file():
        validate_cmd += f" --config {validator_cfg}"
    validate_cmd += f" --out {lint_result_file}"

    dot = LINT_DOT.read_text(encoding="utf-8")
    dot = dot.replace("$validate_cmd", validate_cmd)
    return dot


def run_lint(wiki_dir: str | Path) -> int:
    """Run the structural validator as an attractor pipeline.

    Mirrors lib.lint() but routes through the attractor engine. Returns the
    validator's exit code: 0 on pass, 1 on fail. Prints the validator report
    to stdout (identical output to lib.lint).

    The wiki must already exist — lint runs on a built wiki whose ``.wiki/runs/``
    directory is writable (no bootstrapping issue, unlike init).

    The validator result is written to a tmp file via ``--out`` and read back
    so run_lint can recover the pass/fail verdict from outside the engine.
    """
    import sys

    wiki_dir = Path(wiki_dir).resolve()
    if not wiki_dir.is_dir():
        print(f"FAIL: wiki dir not found: {wiki_dir}", file=sys.stderr)
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / f"lint-{timestamp}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Result file: validate_wiki.py writes its output here via --out.
    # Using a /tmp path (not wiki/.ai/) avoids collision on concurrent runs
    # and sidesteps any denied_write_paths constraints.
    lint_result_file = Path(f"/tmp/wiki_lint_{timestamp}.md")

    dot_source = build_lint_dot_from_file(wiki_dir, lint_result_file)
    (logs_dir / "lint.dot").write_text(dot_source, encoding="utf-8")

    _run_coro(
        run_pipeline(
            dot_source,
            cwd=wiki_dir,
            logs_root=logs_dir,
            provider=PROVIDER,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )

    # Primary: read from the result file (written by validate_wiki.py --out).
    # Print it so the user sees the same PASS/FAIL output as lib.lint.
    # The report starts with "Wiki: ..." and the PASS/FAIL verdict appears
    # after the per-check lines — check for the marker line, not startswith.
    if lint_result_file.exists():
        result = lint_result_file.read_text(encoding="utf-8")
        sys.stdout.write(result)
        lint_result_file.unlink(missing_ok=True)
        return 0 if "\nPASS \u2014 structurally valid" in result else 1

    # Fallback: result file not written (unexpected). Fail loud — if we cannot
    # confirm PASS we must not silently return 0.
    print(
        "FAIL: validator result file not written — see logs in "
        f"{logs_dir} for details.",
        file=sys.stderr,
    )
    return 1


# --------------------------------------------------------------------------
# Init pipeline: single-shot LLM schema design
# --------------------------------------------------------------------------
#
# MECHANISM: one LLM node that reads the wiki purpose + source sample, adapts
# the generic SCHEMA.md for the domain, and writes <wiki>/.wiki/policy/schema.md.
# That path is already the first override point in policy.py's schema_path
# resolution — so the first ingest after `init --purpose` automatically runs
# with the domain-fit schema, no other changes needed.
#
# POST-CHECK: after the engine returns, run_init asserts the file exists,
# is non-empty, and looks like a schema (contains "type:" and "index"/"overview").
# Fail loud — never silently fall back to the generic default.

# The raw prompt template (real Python newlines and quotes; substituted then
# DOT-escaped before embedding in the DOT string attribute).
_INIT_PROMPT_TEMPLATE = (
    "You are designing the SCHEMA for a domain-specific, LLM-maintained wiki \u2014 the\n"
    "configuration file that makes an LLM a disciplined wiki maintainer for THIS domain\n"
    '(the Karpathy "LLM wiki" pattern). You design STRUCTURE, not content. Do NOT invent\n'
    "facts about the domain.\n"
    "\n"
    "PURPOSE OF THIS WIKI (its intended use and the outcomes it must serve):\n"
    "$purpose\n"
    "\n"
    "SAMPLE OF REAL SOURCE MATERIAL that will be ingested (may be empty \u2014 if empty, design\n"
    "from the purpose alone):\n"
    "$source_sample\n"
    "\n"
    "GENERIC DEFAULT SCHEMA (the domain-agnostic baseline \u2014 adapt it: keep what works,\n"
    "change what this domain and these outcomes actually need):\n"
    "$default_schema\n"
    "\n"
    "Design a schema TAILORED to this wiki's purpose and desired outcomes. Write the\n"
    "complete schema to `$wiki_dir/.wiki/policy/schema.md`. It MUST define:\n"
    "1. PAGE TYPES \u2014 a domain-fit `type:` taxonomy chosen to serve the stated outcomes.\n"
    "   Do NOT reflexively keep concept/entity/comparison/synthesis if this domain wants\n"
    "   different page types (e.g. a team-decisions wiki may want decision/workstream/owner\n"
    "   pages; a tool-landscape wiki may want tool/technique/comparison pages).\n"
    "2. FRONTMATTER CONTRACT \u2014 required fields (MUST include title, type, sources) plus any\n"
    "   domain-useful optional fields that serve the outcomes (e.g. decision_date, owner,\n"
    "   status, confidence).\n"
    '3. CONVENTIONS \u2014 [[wikilink]] linking, how to record contradictions / "open tensions",\n'
    "   no-orphan and no-dangling-link rules.\n"
    "4. NAV PAGES \u2014 KEEP `index.md` and `overview.md` as the required navigation pages (do\n"
    "   NOT rename them). Describe what each should contain for THIS domain.\n"
    "5. INGEST & QUERY GUIDANCE \u2014 how a new source should be integrated, and how the wiki\n"
    "   should be queried, both oriented to the desired outcomes.\n"
    "\n"
    "Make it specific: a reader should see what THIS wiki does better than a generic one.\n"
    "Output ONLY the schema file content to the path above."
)


def build_init_dot(
    wiki_dir: Path,
    purpose: str,
    source_sample: str,
    *,
    provider: str = PROVIDER,
    model: str = MODEL,
) -> str:
    """Build the init DOT pipeline as a Python string (reference builder).

    Constructs the raw prompt (with real Python newlines/quotes), substitutes
    the four token values, then DOT-escapes the whole prompt for embedding in
    the DOT attribute string.

    Byte-identical to build_init_dot_from_file for the same inputs.
    """
    wiki_abs = str(wiki_dir.resolve())
    default_schema = SCHEMA_PATH.read_text(encoding="utf-8")

    # Substitute all tokens into the raw prompt BEFORE DOT-escaping.
    # _dot_escape_prompt distributes over concatenation, so substituting
    # values first and then escaping the whole prompt is byte-identical to
    # escaping each value separately and substituting into the pre-escaped
    # template (which is what build_init_dot_from_file does).
    prompt = _INIT_PROMPT_TEMPLATE
    prompt = prompt.replace("$wiki_dir", wiki_abs)
    prompt = prompt.replace("$purpose", purpose)
    prompt = prompt.replace("$source_sample", source_sample)
    prompt = prompt.replace("$default_schema", default_schema)

    prompt_dot = _dot_escape_prompt(prompt)

    # Resolve family token to a concrete served model id before injecting into DOT.
    model = resolve_model(provider, model)

    lines = [
        "digraph init_wiki {",
        '    graph [goal="Design a domain-adaptive schema for a new wiki"]',
        '    start [shape=Mdiamond, label="Start"]',
        "    design_schema [",
        '        label="Design Domain Schema",',
        f'        llm_provider="{provider}",',
        f'        llm_model="{model}",',
        f'        prompt="{prompt_dot}"',
        "    ]",
        '    done [shape=Msquare, label="Done"]',
        "    start -> design_schema -> done",
        "}",
        "",
    ]
    return "\n".join(lines)


def build_init_dot_from_file(
    wiki_dir: Path,
    purpose: str,
    source_sample: str,
    *,
    provider: str = PROVIDER,
    model: str = MODEL,
) -> str:
    """Build the init DOT pipeline by reading pipeline/init.dot and substituting tokens.

    Token substitution:
      $wiki_dir       -> str(wiki_dir.resolve())               (path; no DOT-escaping)
      $purpose        -> _dot_escape_prompt(purpose)           (user-supplied text)
      $source_sample  -> _dot_escape_prompt(source_sample)     (file content)
      $default_schema -> _dot_escape_prompt(SCHEMA.md text)    (built-in schema)

    The template bakes llm_provider="anthropic" / llm_model="claude-sonnet-4-6".
    If policy differs, those values are replaced with the supplied provider/model.

    Byte-identical to build_init_dot for the same inputs.
    """
    wiki_abs = str(wiki_dir.resolve())
    default_schema = SCHEMA_PATH.read_text(encoding="utf-8")

    dot = INIT_DOT.read_text(encoding="utf-8")

    # Substitute path token — plain path, no DOT-escaping needed.
    dot = dot.replace("$wiki_dir", wiki_abs)
    # User/content tokens: DOT-escape before substituting into the already-
    # DOT-escaped template (preserves byte-identity with build_init_dot).
    dot = dot.replace("$purpose", _dot_escape_prompt(purpose))
    dot = dot.replace("$source_sample", _dot_escape_prompt(source_sample))
    dot = dot.replace("$default_schema", _dot_escape_prompt(default_schema))

    # Resolve family token to a concrete served model id before substitution.
    model = resolve_model(provider, model)

    # Apply provider/model override — replace the baked defaults unconditionally.
    dot = dot.replace('llm_provider="anthropic"', f'llm_provider="{provider}"')
    dot = dot.replace('llm_model="claude-sonnet-4-6"', f'llm_model="{model}"')

    return dot


def _sample_inbox(inbox: Path, max_chars: int = 6000) -> str:
    """Return a sample of inbox source content (first ~max_chars across first few files).

    Used by run_init to give the LLM a taste of what real sources look like,
    so the schema can be tailored to actual content shape (not just the stated purpose).
    """
    parts: list[str] = []
    total = 0
    for p in sorted(inbox.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        chunk = text[: max_chars - total]
        parts.append(f"--- {p.name} ---\n{chunk}")
        total += len(chunk)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


def run_init(
    wiki_dir: str | Path,
    *,
    purpose: str | None = None,
    sample_inbox: bool = True,
    plain: bool = False,
) -> int:
    """Scaffold a wiki and optionally design a domain-fit schema via LLM.

    Contract C (per parent conversation design decision):
      - Always calls lib.init(wiki_dir) first (deterministic scaffold: dirs, stubs,
        ledger). This also ensures <wiki>/.wiki/runs/ is writable for the engine.
      - plain=True  -> stop after scaffold (generic default schema, no LLM). Free.
      - No signal (purpose is None AND inbox is empty/absent) -> same as plain.
      - Otherwise   -> run init.dot (one LLM node that writes .wiki/policy/schema.md).

    POST-CHECK after the LLM run (fail loud, never silent fallback):
      Asserts <wiki>/.wiki/policy/schema.md was written, is non-empty, and contains a
      "type:" taxonomy reference plus "index" and "overview" nav-page mentions.
    """
    import sys

    from .lib import init as _lib_init

    wiki_dir = Path(wiki_dir).expanduser().resolve()

    # Step 1: Always scaffold first (deterministic; creates dirs + stubs + ledger;
    # also creates wiki_dir so the engine can create .wiki/runs/ inside it).
    rc = _lib_init(wiki_dir)
    if rc != 0:
        return rc

    # Step 2: Decide mode.
    if plain:
        print("  schema: generic default (--plain mode, no LLM schema design)")
        return 0

    source_sample = ""
    if sample_inbox:
        inbox = wiki_dir / "_inbox"
        if inbox.is_dir():
            source_sample = _sample_inbox(inbox)

    # No signal: no purpose + no inbox sources → generic default, no LLM.
    if purpose is None and not source_sample:
        print(
            "  schema: generic default"
            " (no --purpose or inbox sources; use --purpose for a domain-fit schema)"
        )
        return 0

    # Step 3: LLM mode — design a domain-fit schema.
    purpose_str = purpose or ""

    # Create policy/ dir before the engine runs so the LLM can write schema.md
    # without needing to mkdir itself (belt-and-suspenders).
    wiki_policy_dir(wiki_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_runs(wiki_dir) / f"init-{timestamp}"
    logs_dir.mkdir(parents=True, exist_ok=True)

    _init_policy = load_policy(wiki_dir)
    dot_source = build_init_dot_from_file(
        wiki_dir,
        purpose_str,
        source_sample,
        provider=_init_policy.provider,
        model=_init_policy.model_for("init"),
    )
    (logs_dir / "init.dot").write_text(dot_source, encoding="utf-8")

    _run_coro(
        run_pipeline(
            dot_source,
            cwd=wiki_dir,
            logs_root=logs_dir,
            provider=_init_policy.provider,
            profiles=None,
            extra_overlays=[_ci_overlay()],
            child_constraint=_fs_child_constraint(wiki_dir),
            spawn_timeout=SPAWN_TIMEOUT_SECONDS,
        )
    )

    # Step 4: Post-check — fail loud if schema.md wasn't written or looks malformed.
    schema_file = wiki_policy_dir(wiki_dir) / "schema.md"
    if not schema_file.is_file():
        print(
            f"FAIL: schema design completed but {schema_file} was not written.\n"
            f"  See engine logs: {logs_dir}",
            file=sys.stderr,
        )
        return 1

    content = schema_file.read_text(encoding="utf-8")
    if not content.strip():
        print(
            f"FAIL: {schema_file} was written but is empty. See logs: {logs_dir}",
            file=sys.stderr,
        )
        return 1

    # Sanity: domain-fit schema must define a type: taxonomy and mention both nav pages.
    has_type = "type:" in content
    has_nav = "index" in content.lower() and "overview" in content.lower()
    if not (has_type and has_nav):
        print(
            f"FAIL: {schema_file} looks malformed — must contain a 'type:' taxonomy "
            f"and mention both 'index' and 'overview' nav pages.\n"
            f"  See logs: {logs_dir}",
            file=sys.stderr,
        )
        return 1

    print(f"  schema: {schema_file} (domain-fit, LLM-designed)")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run the wiki-weaver inner pipeline.")
    sub = ap.add_subparsers(dest="cmd")

    p_inner = sub.add_parser("inner", help="run the inner pipeline for one source")
    p_inner.add_argument("source_path")
    p_inner.add_argument("wiki_dir")
    p_inner.add_argument("--max-cycles", type=int, default=3)

    p_thin = sub.add_parser("thin", help="run the thin-slice proof")
    p_thin.add_argument("out_path")

    args = ap.parse_args()

    if args.cmd == "thin":
        res = run_thin_slice(args.out_path)
        print(res)
        raise SystemExit(0 if res["exists"] and res["content"] == "engine-ok" else 1)

    # default: inner
    result = run_inner(args.source_path, args.wiki_dir, max_cycles=args.max_cycles)
    print(f"status={result.status} converged={result.converged}")
    print(f"logs={result.logs_dir}")
    if result.notes:
        print(f"notes={result.notes}")
    raise SystemExit(0 if result.converged else 1)
