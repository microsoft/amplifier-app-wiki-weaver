"""Regression (D2 -- curate_gate measured harmful): pipeline/ingest.dot's
default routing no longer includes the source-curation checkpoint, and the
retired mechanism is preserved, wired and runnable, as
evals/arms/ingest-curate.dot.

MEASUREMENT (3-run comparative eval, takeaways+direction baseline, calibrated
metric):

    BASELINE   50.4%   [38, 50, 63]
    +CURATE    48.6%   [41, 47, 58]
    DELTA      -1.9pp

curate_gate declined 0/2/2 sources across the three runs -- a declined
source is permanently lost signal, and downstream weave/synthesis already
filter weak material, so the checkpoint bought nothing at real cost. Every
other site measured in this project was neutral-to-positive; this was the
only one measured negative.

JUDGMENT CALL (excise vs. leave reachable via --gate-mode-for): excised.
`--gate-mode-for` selects WHICH INTERVIEWER answers a gate that is already
reachable by a graph edge -- it cannot restore an edge that doesn't exist,
so "leave it reachable only via an override flag" was never actually
available once drain_bound routes straight to detect_kind (this project's
own requirement). Leaving prepare_curate_brief/curate_gate declared-but-
unreachable in the live graph would repeat a mistake this project has
already made twice (canon.dot, correct.dot both validated for months while
completely unexercised -- see pipeline/CLI-CONTRACT.md's "Known
pre-existing gap"). The mechanism is preserved instead as a genuinely
runnable standalone arm, the same pattern already used for
evals/arms/ingest-b.dot / ingest-c.dot.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.transforms import apply_transforms
from amplifier_module_loop_pipeline.validation import validate_or_raise

REPO_ROOT = Path(__file__).parent.parent
INGEST_DOT = REPO_ROOT / "pipeline" / "ingest.dot"
INGEST_CURATE_DOT = REPO_ROOT / "evals" / "arms" / "ingest-curate.dot"


def _load_graph(path: Path):
    src = path.read_text(encoding="utf-8")
    graph = parse_dot(src)
    graph = apply_transforms(graph, PipelineContext())
    validate_or_raise(graph)
    return graph


# -- pipeline/ingest.dot: curate_gate is GONE --------------------------------


def test_ingest_dot_has_no_curate_nodes():
    graph = _load_graph(INGEST_DOT)
    node_ids = set(graph.nodes)

    assert "curate_gate" not in node_ids
    assert "prepare_curate_brief" not in node_ids
    assert "commit_decline" not in node_ids


def test_ingest_dot_drain_bound_routes_directly_to_detect_kind():
    graph = _load_graph(INGEST_DOT)

    continue_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "drain_bound" and edge.condition == "context.drain_status=continue"
    }

    assert continue_targets == {"detect_kind"}


def test_ingest_dot_still_validates_and_has_no_orphaned_curate_references():
    """Full engine validation (parse_dot -> apply_transforms ->
    validate_or_raise) must still pass after excising the three nodes and
    their five edges -- proves no dangling edge references a removed node."""
    graph = _load_graph(INGEST_DOT)
    assert len(graph.nodes) > 0
    assert len(graph.edges) > 0

    for edge in graph.edges:
        assert edge.from_node in graph.nodes, f"edge from removed node: {edge.from_node}"
        assert edge.to_node in graph.nodes, f"edge to removed node: {edge.to_node}"


# -- evals/arms/ingest-curate.dot: the preserved, still-runnable arm --------


def test_ingest_curate_arm_exists_and_validates():
    graph = _load_graph(INGEST_CURATE_DOT)
    assert graph.name == "wiki_weaver_v2_ingest_curate"


def test_ingest_curate_arm_still_has_curate_gate_wired_in():
    graph = _load_graph(INGEST_CURATE_DOT)
    node_ids = set(graph.nodes)

    assert "curate_gate" in node_ids
    assert "prepare_curate_brief" in node_ids
    assert "commit_decline" in node_ids

    continue_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "drain_bound" and edge.condition == "context.drain_status=continue"
    }
    assert continue_targets == {"prepare_curate_brief"}

    accept_targets = {
        edge.to_node for edge in graph.edges if edge.from_node == "curate_gate" and edge.label.startswith("[A]")
    }
    decline_targets = {
        edge.to_node for edge in graph.edges if edge.from_node == "curate_gate" and edge.label.startswith("[B]")
    }
    assert accept_targets == {"detect_kind"}
    assert decline_targets == {"commit_decline"}


def test_ingest_curate_arm_declined_ledger_value_used():
    """The `declined` ledger decision (distinct from skip/quarantine) is
    still wired into commit_decline's tool_command in the preserved arm."""
    graph = _load_graph(INGEST_CURATE_DOT)
    commit_decline = graph.nodes["commit_decline"]
    assert "--decision declined" in commit_decline.attrs.get("tool_command", "")
