# pyright: reportMissingImports=false
"""Goal-gate verdict-contract tests (a gate must be satisfiable by construction).

THE PROBLEM THESE PIN (measured, both directions, 2026-08-01):

attractor's fail-closed goal-gate contract (EXTENSIONS.md 25) requires a node
carrying ``goal_gate=true`` to produce an EXPLICIT verdict. A plain-prose final
message is NOT explicit -- the engine returns RETRY rather than satisfying the
gate. ``synthesize.dot``'s ``ingest`` node carried ``goal_gate=true`` while its
prompt asked only for "a short one-paragraph summary", so EVERY ingest cycle
burned one guaranteed wasted retry re-doing already-correct work.

This was not a latent authoring bug -- it was an UPSTREAM BEHAVIOR CHANGE. Under
the canonical spec (4.5) plain prose fell back to SUCCESS, so the node was
correct when it shipped. attractor's 25 extension inverted that. This repo
tracks attractor @main unpinned (see AGENTS.md), so the contract can move again.
These tests are the pin that makes the next move fail loudly in CI instead of
silently doubling every ingest.

WHAT IS PINNED HERE:

G1 -- Every ``goal_gate=true`` node in every pipeline/*.dot must carry a verdict
      contract the engine actually accepts. Two are accepted (see ACCEPTED
      CONTRACTS below). Pure text scan of the .dot source: no engine import, no
      parser dependency, no LLM, no network -- so it runs everywhere and cannot
      itself be broken by an engine change.

G2 -- Behavioral proof of G1's premise against the real engine: prose does NOT
      satisfy a gate, and a tool node's exit code DOES. Skipped loudly (never
      silently) on a pre-25 engine, naming what was missing.

G3 -- REGRESSION: the "prose + trailing {"status": "success"} marker" approach
      is rejected as a contract even though it works in the happy path. The
      router extracts the LAST balanced {...} from the message, and this same
      ingest prompt instructs the model to write JSON removal records to
      .ai/removals.jsonl -- so a model that narrates a removal after the marker
      silently reintroduces the original defect. Measured below: it does.

ACCEPTED CONTRACTS (only these two -- both proven in G2/G3):

  1. TOOL NODE (shape=parallelogram). The exit code IS the verdict, explicit by
     construction. Immune to the text-parsing ladder entirely. This is the
     preferred form: attractor's own guidance is "an LLM node produces judgment
     as a file; a tool node makes every state transition and emits the verdict."

  2. PURE-JSON FINAL MESSAGE. The prompt must require the node's entire final
     message to be the JSON verdict and nothing else (engine ladder path 3).
     This is what ``assess`` uses.

DELIBERATELY NOT ACCEPTED:

  - ``report_outcome`` tool call. It is the engine's authoritative path 1, BUT
    ``assess``'s own prompt states "on this pipeline that tool call is never
    read" -- the conclusion of PR #41, which reverted PR #38 for exactly this.
    Whether 25-era engines now read it on this pipeline's invocation path is
    UNVERIFIED. Accepting it here would let a node claim a contract this repo
    has not proven end-to-end. If someone proves it with a real run, add it
    here and cite the run.

  - Embedded/trailing JSON in prose (ladder path 4). Works until it doesn't --
    G3 measures the failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "pipeline"

# ---------------------------------------------------------------------------
# Minimal .dot node-block scanner.
#
# Deliberately NOT the attractor parser: pipeline/ingest.dot does not parse
# without runtime params ($max_drain_iters), and this guard must cover every
# file unconditionally. The .dot text is the source of truth being asserted on.
# ---------------------------------------------------------------------------


def _node_blocks(dot_text: str) -> dict[str, str]:
    """Map node id -> attribute-block text, line-based.

    NOT a regex over the whole file: prompts legitimately contain `[` and `]`
    (``[[wikilink]]``, ``[N]`` citations), and a naive block regex silently runs
    a single-line declaration (``start [shape=Mdiamond, label="Start"]``) into
    the NEXT node's body -- which made `start` inherit ingest's goal_gate and
    reported a false positive. A node's attribute block ends at a line that is
    exactly `]`; a long single-line prompt never produces one.
    """
    blocks: dict[str, str] = {}
    lines = dot_text.splitlines()
    i = 0
    while i < len(lines):
        m = re.match(r"^\s{4}(?P<id>\w+)\s*\[(?P<rest>.*)$", lines[i])
        if not m:
            i += 1
            continue
        node_id, rest = m.group("id"), m.group("rest")
        if rest.rstrip().endswith("]"):  # single-line declaration
            blocks[node_id] = rest.rstrip()[:-1]
            i += 1
            continue
        body: list[str] = []
        i += 1
        while i < len(lines) and lines[i].strip() != "]":
            body.append(lines[i])
            i += 1
        i += 1  # consume the closing bracket line
        blocks[node_id] = "\n".join(body)
    return blocks


def _is_goal_gate(body: str) -> bool:
    # unquoted true and quoted "true" both coerce (engine: graph.py)
    return re.search(r'goal_gate\s*=\s*"?true"?', body) is not None


def _is_tool_node(body: str) -> bool:
    return re.search(r"shape\s*=\s*parallelogram", body) is not None


def _requires_pure_json_final_message(body: str) -> bool:
    """True when the prompt demands the ENTIRE final message be the JSON verdict.

    Matches assess's FINAL-MESSAGE CONTRACT shape: a 'final/last message' phrase
    co-located with an exclusivity phrase ('and NOTHING ELSE' / 'exactly that
    one-line JSON object'). A trailing marker appended AFTER prose does not
    match, and must not -- that is ladder path 4 (see G3).
    """
    prompt = body.replace("\\n", "\n")
    has_final_msg = re.search(
        r"(final|last)[- ]message|your\s+VERY\s+LAST\s+message", prompt, re.I
    )
    exclusive = re.search(
        r"NOTHING\s+ELSE|and\s+nothing\s+else|MUST\s+be\s+exactly\s+that\s+one-line\s+JSON",
        prompt,
        re.IGNORECASE,
    )
    return bool(has_final_msg and exclusive)


def _dot_files() -> list[Path]:
    files = sorted(PIPELINE_DIR.glob("*.dot"))
    assert files, f"no pipelines found under {PIPELINE_DIR}"
    return files


def _gate_nodes() -> list[tuple[Path, str, str]]:
    out: list[tuple[Path, str, str]] = []
    for f in _dot_files():
        for node_id, body in _node_blocks(f.read_text(encoding="utf-8")).items():
            if node_id != "graph" and _is_goal_gate(body):
                out.append((f, node_id, body))
    return out


# ---------------------------------------------------------------------------
# G1 -- the durable guard
# ---------------------------------------------------------------------------


def test_g1_at_least_one_goal_gate_exists():
    """Sanity: the scanner actually finds gates (guards against a silent no-op)."""
    gates = _gate_nodes()
    assert gates, (
        "No goal_gate=true nodes found in any pipeline/*.dot. Either the gates "
        "were removed or the node scanner regressed -- a guard that asserts "
        "nothing is worse than no guard."
    )


@pytest.mark.parametrize(
    "dot_file,node_id",
    [(f.name, n) for f, n, _ in _gate_nodes()],
)
def test_g1_every_goal_gate_has_an_accepted_verdict_contract(
    dot_file: str, node_id: str
):
    """Every goal_gate=true node must be satisfiable by construction."""
    body = next(b for f, n, b in _gate_nodes() if f.name == dot_file and n == node_id)

    if _is_tool_node(body):
        return  # contract 1: exit code is the verdict
    if _requires_pure_json_final_message(body):
        return  # contract 2: ladder path 3

    pytest.fail(
        f"{dot_file}:{node_id} has goal_gate=true but no accepted verdict "
        f"contract.\n\n"
        f"attractor's fail-closed contract (EXTENSIONS.md 25) returns RETRY for "
        f"a plain-prose final message, so this node burns a guaranteed wasted "
        f"cycle on EVERY run.\n\n"
        f"Fix with ONE of:\n"
        f"  (preferred) move the gate to a shape=parallelogram tool node whose "
        f"exit code is the verdict, and let this node go back to producing "
        f"judgment as a file; or\n"
        f"  require this node's ENTIRE final message be the JSON verdict and "
        f"nothing else (see assess's FINAL-MESSAGE CONTRACT).\n\n"
        f'A trailing {{"status": ...}} marker appended after prose is NOT '
        f"accepted -- see test_g3_trailing_marker_is_not_a_contract."
    )


def test_g1_ingest_gate_is_deterministic():
    """Pin the specific repair: ingest's verdict comes from a tool node.

    Fails on the pre-fix tree (gate on the LLM `ingest` node) and on the
    prompt-marker variant. This is the earn-your-place eval for the change.
    """
    text = (PIPELINE_DIR / "synthesize.dot").read_text(encoding="utf-8")
    blocks = _node_blocks(text)

    assert "ingest" in blocks, "synthesize.dot lost its ingest node"
    assert not _is_goal_gate(blocks["ingest"]), (
        "ingest (an LLM node) carries goal_gate=true again. Its verdict must "
        "come from a deterministic tool node, not from prose the model types."
    )

    assert "gate_ingest" in blocks, "synthesize.dot lost its gate_ingest tool node"
    gate = blocks["gate_ingest"]
    assert _is_tool_node(gate) and _is_goal_gate(gate), (
        "gate_ingest must be the goal_gate, as shape=parallelogram"
    )

    # Both outcomes routed explicitly: a FAILED tool node only traverses
    # edges matching outcome=fail, so a missing fail edge dead-ends the run.
    assert re.search(r"gate_ingest\s*->\s*\w+\s*\[[^\]]*outcome=success", text), (
        "gate_ingest has no outcome=success edge"
    )
    assert re.search(r"gate_ingest\s*->\s*\w+\s*\[[^\]]*outcome=fail", text), (
        "gate_ingest has no outcome=fail edge -- an empty ingest would "
        "dead-end at no_matching_edge instead of routing to repair"
    )

    # A stale manifest from a prior loop_restart must not satisfy the gate.
    assert "clear_manifest" in blocks, (
        "clear_manifest node missing: without it a stale touched-pages manifest "
        "from the previous cycle satisfies gate_ingest and the gate passes on "
        "work that never happened"
    )


# ---------------------------------------------------------------------------
# G2/G3 -- behavioral proof against the real engine
# ---------------------------------------------------------------------------


def _engine():
    """Return (_parse_outcome, Node) for a 25-era engine, or skip loudly."""
    try:
        from amplifier_module_loop_pipeline.backend import _parse_outcome
        from amplifier_module_loop_pipeline.graph import Node
        from amplifier_module_loop_pipeline.outcome import Outcome
    except ImportError as e:  # pragma: no cover
        pytest.skip(f"attractor loop-pipeline not importable: {e}")

    if "is_explicit" not in getattr(Outcome, "__dataclass_fields__", {}):
        pytest.skip(
            "installed attractor predates the EXTENSIONS.md 25 fail-closed "
            "goal-gate contract (Outcome has no is_explicit field). G1 still "
            "ran and is the durable guard; this behavioral proof needs a "
            "25-era engine."
        )
    return _parse_outcome, Node


def _gate_node(node_cls):
    n = node_cls(id="ingest", shape="box")
    n.attrs["goal_gate"] = True
    return n


PROSE_SUMMARY = (
    "I integrated source 1 into the wiki. I created concept-agents.md and "
    "updated index.md and overview.md."
)


def test_g2_prose_does_not_satisfy_a_goal_gate():
    """The reported defect, reproduced: prose -> RETRY, not SUCCESS."""
    parse, Node = _engine()
    outcome = parse(PROSE_SUMMARY, node=_gate_node(Node))
    assert not (outcome.is_success and outcome.is_explicit), (
        "A plain-prose final message satisfied a goal gate. Either the engine "
        "reverted to canonical spec 4.5 or the gate check changed -- if prose "
        "is genuinely accepted again, G1's rules can relax. Verify, don't assume."
    )


def test_g3_trailing_marker_is_not_a_contract():
    """PR #52's approach works, then silently breaks. Both measured here.

    The router takes the LAST balanced {...}. The ingest prompt separately
    instructs the model to write JSON removal records. When the model narrates
    one after the marker, the marker is no longer last and the gate fails --
    reintroducing the original defect, intermittently and invisibly.
    """
    parse, Node = _engine()

    happy = f'{PROSE_SUMMARY}\n{{"status": "success"}}'
    o_happy = parse(happy, node=_gate_node(Node))
    assert o_happy.is_success and o_happy.is_explicit, (
        "baseline broke: prose + trailing marker no longer parses at all"
    )

    collided = (
        f'{PROSE_SUMMARY}\n{{"status": "success"}}\n'
        'I also logged one removal: {"page": "x.md", "removed": "old claim", '
        '"action": "superseded", "reason": "newer data"}'
    )
    o_collided = parse(collided, node=_gate_node(Node))
    assert not (o_collided.is_success and o_collided.is_explicit), (
        "The brace-collision regression did not reproduce. If the router "
        "became string-aware or stopped taking the last block, re-measure "
        "before relaxing G1 -- this test is the reason path 4 is rejected."
    )


def test_g2_tool_node_exit_code_is_an_explicit_verdict():
    """The chosen fix's premise: a tool node's exit code satisfies a gate.

    Asserted against the engine's own handler rather than a live run: the
    end-to-end proof lives in the PR's run log.
    """
    _engine()  # skip-gate on pre-25 engines
    from amplifier_module_loop_pipeline.handlers import tool as tool_handler

    src = Path(tool_handler.__file__).read_text(encoding="utf-8")
    assert "is_explicit" in src, (
        "tool handler no longer marks outcomes explicit -- gate_ingest's "
        "contract depends on a tool exit code being an explicit verdict"
    )
