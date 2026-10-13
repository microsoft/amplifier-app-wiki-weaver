"""Purity acceptance (R1): no model call anywhere outside a box node.

(a) no module in wiki_weaver/ imports an LLM SDK or an HTTP client
(b) every box node in pipeline/*.dot has an inline prompt=
(c) no shell (parallelogram) node's command invokes a model
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from dotparse import nodes

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "wiki_weaver"
GRAPHS = sorted((ROOT / "pipeline").glob("*.dot"))

BANNED_IMPORTS = {
    "anthropic",
    "openai",
    "httpx",
    "requests",
    "aiohttp",
    "urllib3",
    "litellm",
    "cohere",
    "mistralai",
    "groq",
    "ollama",
    "langchain",
    "langchain_core",
    "llama_index",
    "boto3",
    "google.generativeai",
    "google.genai",
    "vertexai",
    "transformers",
    "unified_llm",
    "amplifier_unified_llm_client",
    "amplifier_core",
    "amplifier_foundation",
    "amplifier_module_pipeline_runner",
    "amplifier_module_loop_pipeline",
    "amplifier_agent_lib",
}
# parameters travel by environment (tool_env), never interpolated into shell text
STEP_CMD = re.compile(r'^"\$PY" -m wiki_weaver\.steps [a-z_]+$')
ECHO_EXIT = re.compile(r"^echo '[^']*' >&2; exit 1$")
MODEL_WORDS = re.compile(
    r"dot-runner|amplifier|claude|anthropic|openai|gpt|gemini|llm|ollama|curl|wget|http",
    re.IGNORECASE,
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def test_a_no_llm_sdk_imports():
    offenders = []
    for py in PKG.rglob("*.py"):
        for mod in _imports(py):
            if any(mod == b or mod.startswith(b + ".") for b in BANNED_IMPORTS):
                offenders.append(f"{py.relative_to(ROOT)}: {mod}")
    assert not offenders, offenders


def test_three_graphs_exist():
    assert {g.name for g in GRAPHS} == {"ingest.dot", "init.dot", "ask.dot"}


def test_b_every_box_node_has_inline_prompt():
    missing, boxes = [], 0
    for g in GRAPHS:
        for nid, attrs in nodes(g).items():
            if attrs.get("shape") == "box":
                boxes += 1
                if not attrs.get("prompt", "").strip():
                    missing.append(f"{g.name}:{nid}")
    assert boxes == 6, (
        f"expected 6 box nodes (brief, write, index, draft, pick, answer); got {boxes}"
    )
    assert not missing, missing


def test_b2_no_other_inference_shapes():
    # tripleoctagon with a prompt and house/folder nodes can also reach a model
    for g in GRAPHS:
        for nid, attrs in nodes(g).items():
            assert attrs.get("shape") not in ("tripleoctagon", "house", "folder"), f"{g.name}:{nid}"
            assert "type" not in attrs and "node_type" not in attrs, f"{g.name}:{nid}"


def test_c_no_shell_node_invokes_a_model():
    from wiki_weaver.steps import STEPS

    bad = []
    for g in GRAPHS:
        for nid, attrs in nodes(g).items():
            if attrs.get("shape") != "parallelogram":
                continue
            cmd = attrs.get("tool_command", "")
            if STEP_CMD.match(cmd):
                if cmd.split()[3] not in STEPS:
                    bad.append(f"{g.name}:{nid}: unknown step in {cmd!r}")
            elif not ECHO_EXIT.match(cmd):
                bad.append(f"{g.name}:{nid}: unexpected command {cmd!r}")
            if MODEL_WORDS.search(cmd):
                bad.append(f"{g.name}:{nid}: model-looking token in {cmd!r}")
    assert not bad, bad


def test_dot_runner_lint_passes_on_all_graphs():
    import shutil
    import subprocess

    import pytest

    exe = shutil.which("dot-runner")
    if not exe:
        pytest.skip("dot-runner not installed")
    for g in GRAPHS:
        r = subprocess.run([exe, "lint", str(g)], capture_output=True, text=True, check=False)
        assert r.returncode == 0, (g.name, r.stdout, r.stderr)
        assert "ERROR" not in r.stdout, (g.name, r.stdout)
