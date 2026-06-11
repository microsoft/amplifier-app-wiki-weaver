# pyright: reportMissingImports=false
#!/usr/bin/env python3
"""Run the wiki-weaver INNER convergence pipeline through the attractor engine.

This is the only part of wiki-weaver that needs the engine. ``run_inner``
takes ONE source and ONE wiki dir, substitutes the inner DOT's ``$vars`` with
concrete absolute paths, and runs the pipeline via the canonical "Option B"
recipe (full Amplifier session with tools per node): load the attractor
profile, overlay the ``loop-pipeline`` orchestrator with our ``dot_source``,
prepare, create a session, register ``session.spawn`` so every node gets a
real sub-session with file tools, then ``session.execute``.

The OUTER corpus sweep is a plain Python loop in the CLI (see wiki_weaver.py).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Environment wiring (overridable via env vars; defaults match this machine).
# --------------------------------------------------------------------------

WIKI_WEAVER_ROOT = Path(__file__).resolve().parent.parent
PIPELINE_DIR = WIKI_WEAVER_ROOT / "pipeline"
INNER_DOT = PIPELINE_DIR / "wiki-weaver-inner.dot"
SCHEMA_PATH = PIPELINE_DIR / "SCHEMA.md"
VALIDATE_PY = PIPELINE_DIR / "validate_wiki.py"
RUBRIC_PATH = WIKI_WEAVER_ROOT / "eval" / "scenario-01-llm-wiki" / "rubric.md"

# Attractor engine module sources (made importable on demand). The
# loop-pipeline orchestrator and its unified-llm dependency live as source
# checkouts rather than installed wheels in this environment.
ATTRACTOR_MODULES = Path(
    os.environ.get(
        "WIKI_WEAVER_ATTRACTOR_MODULES",
        "/home/bkrabach/dev/modern-bundle-pipeline/amplifier-bundle-attractor/modules",
    )
)

# The attractor coding profile (local path avoids the network). Falls back to
# the canonical git URL if the local file is missing.
PROFILE_LOCAL = os.environ.get(
    "WIKI_WEAVER_PROFILE",
    "/home/bkrabach/dev/medium-tools-wiki/amplifier-bundle-attractor/"
    "profiles/attractor-profile-anthropic.yaml",
)
PROFILE_GIT = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
    "#subdirectory=profiles/attractor-profile-anthropic"
)

MODEL = os.environ.get("WIKI_WEAVER_MODEL", "claude-sonnet-4-5")
PROVIDER = os.environ.get("WIKI_WEAVER_PROVIDER", "anthropic")
NODE_PROFILE_NAME = "weaver"

# Node ids in the inner DOT that drive an LLM (need an explicit model). Tool
# nodes (validate) and routing nodes (check) do not.
LLM_NODE_IDS = ("ingest", "assess", "feedback")


def _ensure_engine_importable() -> None:
    """Put the engine module sources on sys.path (idempotent)."""
    for mod in ("loop-pipeline", "unified-llm-client"):
        p = str(ATTRACTOR_MODULES / mod)
        if p not in sys.path:
            sys.path.insert(0, p)


@dataclass
class InnerResult:
    """Outcome of one inner-pipeline run for a single source."""

    status: str
    converged: bool
    logs_dir: Path
    notes: str = ""
    failure_reason: str | None = None


# --------------------------------------------------------------------------
# DOT preparation: $var substitution + per-node model injection
# --------------------------------------------------------------------------


def build_dot(source_path: Path, wiki_dir: Path, max_cycles: int) -> str:
    """Read the inner DOT and substitute its required context variables with
    concrete ABSOLUTE paths, then inject an explicit llm model on each LLM node.
    """
    dot = INNER_DOT.read_text(encoding="utf-8")

    validate_cmd = f"{sys.executable} {VALIDATE_PY} {wiki_dir}"
    substitutions = {
        "$source_path": str(source_path),
        "$wiki_dir": str(wiki_dir),
        "$schema_path": str(SCHEMA_PATH),
        "$rubric_path": str(RUBRIC_PATH),
        "$validate_cmd": validate_cmd,
        "$max_cycles": str(max_cycles),
    }
    for var, value in substitutions.items():
        dot = dot.replace(var, value)

    # Inject explicit provider+model on each LLM node. The engine refuses to
    # run an LLM node without an explicit model (no silent default). We target
    # node ids by their line-anchored declaration so the [[wikilinks]] inside
    # prompt strings are never mistaken for attribute brackets.
    model_attrs = f'        llm_provider="{PROVIDER}", llm_model="{MODEL}",\n'
    for nid in LLM_NODE_IDS:
        opener = f"    {nid} [\n"
        if opener in dot and "llm_model" not in dot.split(opener, 1)[1][:200]:
            dot = dot.replace(opener, opener + model_attrs, 1)

    return dot


# --------------------------------------------------------------------------
# Option B recipe: spawn capability + run
# --------------------------------------------------------------------------


async def _load_profile() -> Any:
    from amplifier_foundation import load_bundle

    last_err: Exception | None = None
    for src in (PROFILE_LOCAL, PROFILE_GIT):
        try:
            return await load_bundle(src)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"Could not load attractor profile: {last_err}")


def _register_spawn(session: Any, prepared: Any, profile: Any) -> None:
    """Register session.spawn so each pipeline node runs as a full sub-session.

    Every node spawns a child cloned from the attractor profile: same provider
    + the same real tools (filesystem/bash/search), driven by the normal
    ``loop-agent`` orchestrator (NOT loop-pipeline, which would recurse).
    """
    from amplifier_foundation import Bundle

    instruction_parts: list[str] = []
    for ctx_path in (getattr(profile, "context", None) or {}).values():
        try:
            instruction_parts.append(Path(str(ctx_path)).read_text(encoding="utf-8"))
        except OSError:
            pass
    system_instruction = "\n\n".join(instruction_parts) or None

    child_session = {
        "orchestrator": profile.session["orchestrator"],
        "context": {"module": "context-simple"},
    }

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        child_bundle = Bundle(
            name=f"node-{agent_name}",
            version="1.0.0",
            session=child_session,
            providers=profile.providers,
            tools=profile.tools,
            hooks=getattr(profile, "hooks", []) or [],
            instruction=system_instruction,
        )
        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


async def _run_async(dot_source: str, logs_dir: Path, cwd: Path) -> InnerResult:
    import json

    from amplifier_foundation import Bundle

    profile = await _load_profile()
    overlay = Bundle(
        name="wiki-weaver-inner-run",
        session={
            "orchestrator": {
                "module": "loop-pipeline",
                "config": {
                    "dot_source": dot_source,
                    "logs_root": str(logs_dir),
                    "profiles": {PROVIDER: NODE_PROFILE_NAME},
                },
            },
            "context": {"module": "context-simple"},
        },
    )
    composed = profile.compose(overlay)
    prepared = await composed.prepare()
    session = await prepared.create_session(session_cwd=cwd)
    _register_spawn(session, prepared, profile)

    async with session:
        raw = await session.execute("Run the wiki-weaver inner pipeline")

    text = str(raw)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        data = {"status": "unknown", "notes": text}

    status = data.get("status", "unknown")
    return InnerResult(
        status=status,
        converged=status == "success",
        logs_dir=logs_dir,
        notes=str(data.get("notes", ""))[:2000],
        failure_reason=data.get("failure_reason"),
    )


def run_inner(
    source_path: str | Path,
    wiki_dir: str | Path,
    *,
    max_cycles: int = 3,
) -> InnerResult:
    """Run the inner convergence pipeline for ONE source through the engine.

    Args:
        source_path: Raw source article to integrate this run.
        wiki_dir: Persistent wiki directory (artifact survives loop_restart).
        max_cycles: Hard iteration hint passed to the pipeline.

    Returns:
        InnerResult with status, convergence flag, and the per-node log dir.
    """
    _ensure_engine_importable()

    source_path = Path(source_path).resolve()
    wiki_dir = Path(wiki_dir).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"source not found: {source_path}")
    if not wiki_dir.is_dir():
        raise FileNotFoundError(f"wiki dir not found: {wiki_dir}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logs_dir = wiki_dir / ".runs" / timestamp
    logs_dir.mkdir(parents=True, exist_ok=True)

    dot_source = build_dot(source_path, wiki_dir, max_cycles)
    (logs_dir / "inner.dot").write_text(dot_source, encoding="utf-8")

    return asyncio.run(_run_async(dot_source, logs_dir, wiki_dir))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Run the wiki-weaver inner pipeline.")
    ap.add_argument("source_path")
    ap.add_argument("wiki_dir")
    ap.add_argument("--max-cycles", type=int, default=3)
    args = ap.parse_args()

    result = run_inner(args.source_path, args.wiki_dir, max_cycles=args.max_cycles)
    print(f"status={result.status} converged={result.converged}")
    print(f"logs={result.logs_dir}")
    if result.notes:
        print(f"notes={result.notes}")
    raise SystemExit(0 if result.converged else 1)
