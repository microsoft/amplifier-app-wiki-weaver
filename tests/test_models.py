"""Every model step resolves to an explicit provider and model through the engine's own parser.

Stage 1 ran with `.large_context` on the writer: the engine's class grammar is
`[.][a-z0-9-]+`, so the rule never matched and the write node silently fell back to the
worker's default model. This test runs the graphs through the installed engine's
`parse_dot` + `parse_stylesheet` + `apply_stylesheet` (the same calls `apply_transforms`
makes) so a rule that does not match fails here, not in production.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PIPELINE = Path(__file__).parent.parent / "pipeline"
GRAPHS = ["init.dot", "ingest.dot", "ask.dot"]

_RESOLVE = r"""
import json, sys
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.stylesheet import apply_stylesheet, parse_stylesheet

out = {}
for path in sys.argv[1:]:
    g = parse_dot(open(path).read())
    apply_stylesheet(g, parse_stylesheet(g.model_stylesheet or ""))
    out[path] = {
        n.id: {"provider": n.llm_provider, "model": n.llm_model}
        for n in g.nodes.values()
        if n.shape == "box"
    }
print(json.dumps(out))
"""


def _engine_python() -> str | None:
    exe = shutil.which("dot-runner")
    if not exe:
        return None
    first = Path(exe).read_text(errors="replace").splitlines()[0]
    return first[2:].strip() if first.startswith("#!") else None


def _resolved() -> dict[str, dict[str, dict[str, str | None]]]:
    py = _engine_python()
    if not py:
        pytest.skip("dot-runner not installed")
    paths = [str(PIPELINE / g) for g in GRAPHS]
    r = subprocess.run([py, "-c", _RESOLVE, *paths], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_every_box_node_resolves_provider_and_model() -> None:
    resolved = _resolved()
    unresolved = [
        f"{Path(p).name}:{node}"
        for p, nodes in resolved.items()
        for node, m in nodes.items()
        if not m["provider"] or not m["model"]
    ]
    assert all(resolved.values()), "a graph has no box nodes - parse went wrong"
    assert not unresolved, f"model steps with no stylesheet match: {unresolved}"


def test_writer_runs_on_sonnet() -> None:
    write = _resolved()[str(PIPELINE / "ingest.dot")]["write"]
    assert write == {"provider": "anthropic", "model": "sonnet"}
