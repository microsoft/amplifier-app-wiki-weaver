"""A held source says why the model step failed; run-level errors say what kind they are.

Before: a timeout, a bad key, rate limiting, an overload and a refused connection all
recorded `model_step_failed` with the same text; the cause sat in the node's status.json,
which never leaves the container in Resolve. Fixtures: auth-401, connection and timeout are
real engine status files (forced on smoke copies, 2026-10-16); rate-429, overloaded-529 and
server-500 reuse the real 401 file with the provider's documented error bodies.
"""

from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from wiki_weaver import ledger as lg
from wiki_weaver import result as rs
from wiki_weaver.steps import model_failure

FIX = Path(__file__).parent / "fixtures" / "node-status"
PKG = Path(__file__).parent.parent / "wiki_weaver"


def _engine(tmp_path: Path, node: str, fixture: str) -> Path:
    d = tmp_path / "batch-01" / "engine" / node
    d.mkdir(parents=True)
    shutil.copy2(FIX / fixture, d / "status.json")
    return tmp_path / "batch-01"


@pytest.mark.parametrize(
    ("fixture", "node", "kind", "cause"),
    [
        ("timeout.json", "brief", "model_timeout", "timeout after 900s"),
        ("auth-401.json", "brief", "model_auth_error", "HTTP 401 authentication"),
        ("rate-429.json", "write", "model_provider_error", "HTTP 429 rate limited"),
        ("overloaded-529.json", "write", "model_provider_error", "HTTP 529 overloaded"),
        ("server-500.json", "write", "model_provider_error", "HTTP 500 server error"),
        ("connection.json", "brief", "model_connection_error", "connection error"),
    ],
)
def test_status_file_cause_becomes_kind_and_reason(
    tmp_path: Path, fixture: str, node: str, kind: str, cause: str
) -> None:
    run = _engine(tmp_path, node, fixture)
    assert model_failure(run, node) == (kind, f"{node}: model step failed \u2014 {cause}")


def test_no_usable_reason_falls_back_to_model_step_failed(tmp_path: Path) -> None:
    run = _engine(tmp_path, "write", "no-reason.json")
    assert model_failure(run, "write") == ("model_step_failed", "write: model step failed")
    assert model_failure(tmp_path / "nowhere", "brief") == (
        "model_step_failed",
        "brief: model step failed",
    )


def test_unrecognised_reason_is_kept_in_the_text(tmp_path: Path) -> None:
    run = _engine(tmp_path, "write", "no-reason.json")
    p = run / "engine/write/status.json"
    d = json.loads(p.read_text())
    d["failure_reason"] = "something new went wrong"
    p.write_text(json.dumps(d))
    kind, reason = model_failure(run, "write")
    assert kind == "model_step_failed" and reason.endswith("something new went wrong")


def test_kinds_are_one_enumeration() -> None:
    assert {k.value for k in lg.FailureKind} == lg.FAILURE_KINDS
    assert {
        "model_timeout",
        "model_auth_error",
        "model_provider_error",
        "model_connection_error",
        "model_step_failed",
    } <= lg.FAILURE_KINDS


def test_make_row_rejects_a_kind_outside_the_enumeration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="failure_kind"):
        lg.make_row(tmp_path, source="a.md", file_hash="h", status="failed", failure_kind="nope")


def _kind_values() -> list[tuple[str, ast.expr]]:
    out = []
    for py in PKG.glob("*.py"):
        for node in ast.walk(ast.parse(py.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "failure_kind":
                out.append((py.name, node.value))
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values, strict=True):
                    if isinstance(k, ast.Constant) and k.value == "failure_kind":
                        out.append((py.name, v))
    return out


def test_every_failure_kind_written_in_code_is_a_member() -> None:
    found = _kind_values()
    assert found, "no failure_kind writes found - the scan is broken"
    for where, v in found:
        if isinstance(v, ast.Constant):
            assert v.value in lg.FAILURE_KINDS | {""}, f"{where}: literal {v.value!r}"
        elif isinstance(v, ast.Attribute):
            assert getattr(lg, v.attr) in lg.FAILURE_KINDS, f"{where}: {ast.unparse(v)}"
        # names and calls are checked at runtime: make_row refuses non-members


def test_errored_entries_carry_a_kind() -> None:
    assert rs.ERRORED_KINDS == {"preflight", "recovery", "usage", "engine", "interrupted"}
    for where, v in _errored_appends():
        assert isinstance(v, ast.Dict), f"{where}: errored entry is not a dict literal"
        keys = {k.value for k in v.keys if isinstance(k, ast.Constant)}
        assert "kind" in keys, f"{where}: errored entry has no kind"
        kind = next(
            val
            for k, val in zip(v.keys, v.values, strict=True)
            if isinstance(k, ast.Constant) and k.value == "kind"
        )
        assert isinstance(kind, ast.Constant) and kind.value in rs.ERRORED_KINDS, where


def _errored_appends() -> list[tuple[str, ast.expr]]:
    out = []
    src = (PKG / "cli.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "errored"
            and node.func.attr in ("append", "extend")
        ):
            out.append((f"cli.py:{node.lineno}", node.args[0]))
        if isinstance(node, ast.AugAssign) and getattr(node.target, "id", "") == "errored":
            elt = node.value.elt if isinstance(node.value, ast.ListComp) else node.value
            out.append((f"cli.py:{node.lineno}", elt))
    assert out, "no errored writes found - the scan is broken"
    return out
