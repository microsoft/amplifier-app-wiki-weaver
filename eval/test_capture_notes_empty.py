"""Regression test for the empty-gate defect found in the live init.dot run.

MEASURED 2026-07-26: under `--on-human-gate auto-approve`, the freeform gate
returned an EMPTY string, NOT the literal "auto-approved" stub the councils
found in interviewer.py. The stub-only guard never fired; 5 rounds were
appended containing 0 bytes while each round paid for an LLM `evaluate` call.
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run(env_text: str | None, out: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=SRC)
    if env_text is not None:
        env["HUMAN.GATE.TEXT"] = env_text
    else:
        env.pop("HUMAN.GATE.TEXT", None)
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.init.capture_notes", "--out", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )


def test_empty_gate_text_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    r = _run("", out)
    assert r.returncode != 0, "empty gate text must fail loud"
    assert not out.exists(), "nothing may be written on refusal"


def test_whitespace_only_gate_text_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    r = _run("   \n\t  \n", out)
    assert r.returncode != 0
    assert not out.exists()


def test_missing_env_var_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    r = _run(None, out)
    assert r.returncode != 0
    assert not out.exists()


def test_stub_string_still_refused(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    r = _run("auto-approved", out)
    assert r.returncode != 0
    assert not out.exists()


def test_real_answer_is_captured(tmp_path: Path) -> None:
    out = tmp_path / "notes.md"
    r = _run("I want a wiki about distributed systems papers.", out)
    assert r.returncode == 0, r.stderr
    assert "distributed systems papers" in out.read_text()
