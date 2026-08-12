"""Regression (D-synth -- synthesis_gate measured net-neutral):
pipeline/synthesize.dot's default routing no longer includes the
corpus-wide pre-write checkpoint, and the retired mechanism is preserved,
wired and runnable, as evals/arms/synthesize-gate.dot.

MEASUREMENT (paired n=6, same base wiki, gate off vs on, calibrated metric):

    base      ctl %   gate %   delta
    td1       48.6%    50.0%    +1.4
    td2       59.4%    61.3%    +1.9
    td3       37.9%    37.9%     0.0
    t1        36.7%    36.7%     0.0
    t2        36.7%    37.9%    +1.3
    t3        38.1%    37.5%    -0.6

    PAIRED MEAN +0.7pp   wins 3/6

The pre-committed bar for this read was >= 4/6 wins; it missed (two ties, one
loss). An earlier n=3 read (+1.1pp, 2/3 wins) was the optimistic half of a
wider distribution, not a stable effect. synthesis_gate is not broken -- it
demonstrably drops a real candidate before any page is written, proven live
-- it simply does not move quality enough to justify the extra pass.

JUDGMENT CALL (excise vs. leave reachable via --gate-mode-for): excised, the
identical judgment call already made for curate_gate
(test_ingest_curate_gate_retirement.py). `--gate-mode-for` selects WHICH
INTERVIEWER answers a gate that is already reachable by a graph edge -- it
cannot restore an edge that doesn't exist, so "leave it reachable only via
an override flag" was never actually available once rank_gap_candidates
routes straight to select_gap (this task's own requirement). Leaving
prepare_synthesis_brief/synthesis_gate/persist_synthesis_guidance
declared-but-unreachable in the live graph would repeat a mistake this
project has already made twice (canon.dot, correct.dot both validated for
months while completely unexercised -- see pipeline/CLI-CONTRACT.md's
"Known pre-existing gap"). The mechanism is preserved instead as a
genuinely runnable standalone arm, the same pattern already used for
evals/arms/ingest-curate.dot / ingest-b.dot / ingest-c.dot.
"""

from __future__ import annotations

from pathlib import Path

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.transforms import apply_transforms
from amplifier_module_loop_pipeline.validation import validate_or_raise

REPO_ROOT = Path(__file__).parent.parent
SYNTHESIZE_DOT = REPO_ROOT / "pipeline" / "synthesize.dot"
SYNTHESIZE_GATE_DOT = REPO_ROOT / "evals" / "arms" / "synthesize-gate.dot"


def _load_graph(path: Path):
    src = path.read_text(encoding="utf-8")
    graph = parse_dot(src)
    graph = apply_transforms(graph, PipelineContext())
    validate_or_raise(graph)
    return graph


# -- pipeline/synthesize.dot: synthesis_gate is GONE -------------------------


def test_synthesize_dot_has_no_synthesis_gate_nodes():
    graph = _load_graph(SYNTHESIZE_DOT)
    node_ids = set(graph.nodes)

    assert "synthesis_gate" not in node_ids
    assert "prepare_synthesis_brief" not in node_ids
    assert "persist_synthesis_guidance" not in node_ids


def test_synthesize_dot_rank_gap_candidates_routes_directly_to_select_gap():
    graph = _load_graph(SYNTHESIZE_DOT)

    candidates_ok_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "rank_gap_candidates" and edge.condition == "context.tool.last_line=candidates_ok"
    }

    assert candidates_ok_targets == {"select_gap"}


def test_synthesize_dot_still_validates_and_has_no_orphaned_gate_references():
    """Full engine validation (parse_dot -> apply_transforms ->
    validate_or_raise) must still pass after excising the three nodes and
    their four edges -- proves no dangling edge references a removed node."""
    graph = _load_graph(SYNTHESIZE_DOT)
    assert len(graph.nodes) > 0
    assert len(graph.edges) > 0

    for edge in graph.edges:
        assert edge.from_node in graph.nodes, f"edge from removed node: {edge.from_node}"
        assert edge.to_node in graph.nodes, f"edge to removed node: {edge.to_node}"


def test_synthesize_dot_near_duplicate_and_citation_gates_untouched():
    """rank_gap_candidates (Gate A, near-duplicate dedupe) and check_citations
    (Gate B, citation verification) are unrelated to this retirement and
    must remain wired exactly as before."""
    graph = _load_graph(SYNTHESIZE_DOT)
    node_ids = set(graph.nodes)

    assert "rank_gap_candidates" in node_ids
    assert "check_citations" in node_ids

    citations_ok_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "check_citations" and edge.condition == "context.tool.last_line=citations_ok"
    }
    citations_bad_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "check_citations" and edge.condition == "context.tool.last_line=citations_bad"
    }
    assert citations_ok_targets == {"validate"}
    assert citations_bad_targets == {"retry_bound"}


# -- evals/arms/synthesize-gate.dot: the preserved, still-runnable arm ------


def test_synthesize_gate_arm_exists_and_validates():
    graph = _load_graph(SYNTHESIZE_GATE_DOT)
    assert graph.name == "wiki_weaver_v2_synthesize_gate"


def test_synthesize_gate_arm_still_has_synthesis_gate_wired_in():
    graph = _load_graph(SYNTHESIZE_GATE_DOT)
    node_ids = set(graph.nodes)

    assert "synthesis_gate" in node_ids
    assert "prepare_synthesis_brief" in node_ids
    assert "persist_synthesis_guidance" in node_ids

    candidates_ok_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "rank_gap_candidates" and edge.condition == "context.tool.last_line=candidates_ok"
    }
    has_pending_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "prepare_synthesis_brief" and edge.condition == "context.pending_status=has_pending"
    }
    no_pending_targets = {
        edge.to_node
        for edge in graph.edges
        if edge.from_node == "prepare_synthesis_brief" and edge.condition == "context.pending_status=no_pending"
    }

    assert candidates_ok_targets == {"prepare_synthesis_brief"}
    assert has_pending_targets == {"synthesis_gate"}
    assert no_pending_targets == {"select_gap"}


def test_synthesize_gate_arm_persists_guidance_before_select_gap():
    """synthesis_gate -> persist_synthesis_guidance -> select_gap must still
    be wired end to end in the preserved arm (whatever
    persist_synthesis_guidance writes to the synth-ledger keeps working
    here, exactly as it did before retirement)."""
    graph = _load_graph(SYNTHESIZE_GATE_DOT)

    gate_targets = {edge.to_node for edge in graph.edges if edge.from_node == "synthesis_gate"}
    guidance_targets = {edge.to_node for edge in graph.edges if edge.from_node == "persist_synthesis_guidance"}

    assert gate_targets == {"persist_synthesis_guidance"}
    assert guidance_targets == {"select_gap"}


def test_synthesize_gate_arm_uses_the_same_persist_synthesis_guidance_tool():
    """The preserved arm calls the SAME module the default pipeline used to
    call -- no forked/duplicated logic for the retired mechanism."""
    graph = _load_graph(SYNTHESIZE_GATE_DOT)
    node = graph.nodes["persist_synthesis_guidance"]
    assert "wiki_weaver.synthesize.persist_synthesis_guidance" in node.attrs.get("tool_command", "")
