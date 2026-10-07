"""Phase A, re-verification round: one failing test per remaining problem, written from the
reviewer's repro before the fix. No model calls."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_steps import ORCHARD, cite, fake_brief, page, step

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
SUMMARY = "source-2031-03-02-orchard-sync.md"


def _to_write(corpus: Path, slugs: list[str], run: Path) -> None:
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, slugs)
    assert step(corpus, "page_select") == (0, "ok")


def _head(corpus: Path, path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=corpus, capture_output=True, text=True, check=False
    )
    return r.stdout if r.returncode == 0 else None


def _commit(corpus: Path, msg: str) -> None:
    subprocess.run([*GIT, "add", "-A"], cwd=corpus, check=True)
    subprocess.run([*GIT, "commit", "-qm", msg], cwd=corpus, check=True)


def _env() -> dict:
    return {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}


def _ww(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=_env(),
    )


# ------------------------------------------------------------------ 1. owner data


def test_recovery_leaves_unrelated_owner_changes_alone(corpus: Path, tmp_path: Path):
    (corpus / "orchard-rollout.md").write_text(page("R", [], "## Plan\nkept\n"))
    _commit(corpus, "page")
    _to_write(corpus, ["orchard-rollout"], tmp_path / "r")
    # the in-flight writer's output
    (corpus / SUMMARY).write_text("half a summary")
    (corpus / "orchard-rollout.md").write_text("half a page")
    # the owner's own, unrelated, uncommitted work
    (corpus / "lens.md").write_text((corpus / "lens.md").read_text() + "\n## Owner note\nmine\n")
    (corpus / "draft-notes.md").write_text("an untracked draft")
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", "recover"],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.stdout.strip().endswith("recovered"), r.stderr
    assert "## Owner note" in (corpus / "lens.md").read_text()
    assert (corpus / "draft-notes.md").read_text() == "an untracked draft"
    assert not (corpus / SUMMARY).exists()
    assert (corpus / "orchard-rollout.md").read_text() == _head(corpus, "orchard-rollout.md")
    assert "lens.md" in r.stderr and "draft-notes.md" in r.stderr  # reported, not touched


def test_hold_reverts_what_the_scope_check_named_even_an_owner_edit(corpus: Path, tmp_path: Path):
    """B-lean rule (replaces the pre/post snapshot): hold reverts the journal's owned paths
    and the paths the scope check named. An owner edit that is dirty while a writer runs is
    indistinguishable from writer output, is named, and is reverted. The run's opening
    snapshot commit makes this window exist only for edits made mid-run."""
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    (corpus / "lens.md").write_text((corpus / "lens.md").read_text() + "\n## Owner note\nmine\n")
    fake_brief(corpus, [])
    assert step(corpus, "page_select") == (0, "ok")
    (corpus / SUMMARY).write_text(
        page("O", [ORCHARD], cite(corpus, "words not in the source at all") + "\n")
    )
    assert step(corpus, "checks") == (0, "rewrite")
    assert "outside the selected pages: lens.md" in (corpus / ".wiki/work/findings.md").read_text()
    assert step(corpus, "checks") == (0, "hold")
    assert step(corpus, "hold", str(tmp_path / "r")) == (0, "next")
    assert "## Owner note" not in (corpus / "lens.md").read_text()
    assert (corpus / ".wiki/failed" / ORCHARD).exists()


# ------------------------------------------------------------------ 2. init


def test_init_does_not_commit_an_interrupted_writers_output(corpus: Path, tmp_path: Path):
    _to_write(corpus, [], tmp_path / "r")
    (corpus / SUMMARY).write_text("half a summary")  # the run dies here
    r = _ww("init", str(corpus), "--plain")
    assert r.returncode == 0, r.stderr
    assert _head(corpus, SUMMARY) is None
    assert not (corpus / SUMMARY).exists()


# ------------------------------------------------------------------ 3. index phase


def test_interrupted_index_phase_is_restored_before_any_snapshot(corpus: Path, tmp_path: Path):
    (corpus / "index.md").write_text("old index\n")
    (corpus / "log.md").write_text("old log\n")
    _commit(corpus, "index")
    run = tmp_path / "b"
    run.mkdir()
    (run / "changes.jsonl").write_text(
        json.dumps({"source": "x.md", "status": "converged", "converged": True, "reason": ""})
        + "\n"
    )
    assert step(corpus, "index_prep", str(run)) == (0, "index")
    (corpus / "index.md").write_text("half a new index\n")  # the index model dies here
    (corpus / "log.md").write_text("old log\nhalf an entry\n")
    for f in (corpus / "_inbox").glob("*.md"):
        f.unlink()
    r = _ww("ingest", "--wiki", str(corpus))
    assert r.returncode == 3, r.stderr
    assert _head(corpus, "index.md") == "old index\n"
    assert _head(corpus, "log.md") == "old log\n"
    assert (corpus / "index.md").read_text() == "old index\n"
    assert (corpus / "log.md").read_text() == "old log\n"


# ------------------------------------------------------------------ 4. ask containment


def test_ask_inputs_never_follow_symlinks_out_of_the_corpus(corpus: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "index.md").write_text("SECRET INDEX\n")
    (outside / "READING.md").write_text("SECRET READING\n")
    (corpus / "index.md").unlink(missing_ok=True)
    (corpus / "READING.md").unlink()
    (corpus / "index.md").symlink_to(outside / "index.md")
    (corpus / "READING.md").symlink_to(outside / "READING.md")
    d = corpus / ".wiki/ask/q"
    d.mkdir(parents=True)
    (d / "question.txt").write_text("what?\n")
    assert step(corpus, "ask_assemble", str(d)) == (0, "ok")
    ctx = (d / "context.md").read_text()
    assert "SECRET" not in ctx


# ------------------------------------------------------------------ crash windows at the final unlink


def _die_on_inbox_unlink(monkeypatch):
    real = Path.unlink

    def die(self, *a, **k):
        if self.parent.name == "_inbox":
            raise RuntimeError("process died")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", die)
    return real


def test_crash_after_hold_commit_before_inbox_removal(corpus: Path, tmp_path: Path, monkeypatch):
    from wiki_weaver import steps

    _to_write(corpus, [], tmp_path / "r")
    step(corpus, "checks")
    step(corpus, "checks")
    monkeypatch.chdir(corpus)
    real = _die_on_inbox_unlink(monkeypatch)
    with pytest.raises(RuntimeError):
        steps.step_hold(str(tmp_path / "r"))
    monkeypatch.setattr(Path, "unlink", real)
    assert (corpus / "_inbox" / ORCHARD).exists() and _head(corpus, f".wiki/failed/{ORCHARD}")
    assert step(corpus, "recover")[0] == 0
    places = [(corpus / "_inbox" / ORCHARD).exists(), (corpus / ".wiki/failed" / ORCHARD).exists()]
    assert places == [False, True]


def test_crash_after_skip_commit_before_inbox_removal(corpus: Path, tmp_path: Path, monkeypatch):
    from wiki_weaver import steps

    empty = "2030-01-01 empty.md"
    (corpus / "_inbox" / empty).write_text("")
    monkeypatch.chdir(corpus)
    real = _die_on_inbox_unlink(monkeypatch)
    with pytest.raises(RuntimeError):
        steps.step_select(str(tmp_path / "r"), "0", "-")
    monkeypatch.setattr(Path, "unlink", real)
    assert (corpus / "_inbox" / empty).exists() and _head(corpus, f".wiki/skipped/{empty}") == ""
    assert step(corpus, "recover")[0] == 0
    places = [(corpus / "_inbox" / empty).exists(), (corpus / ".wiki/skipped" / empty).exists()]
    assert places == [False, True]


def test_a_redropped_held_source_is_not_dropped_by_recovery(corpus: Path, tmp_path: Path):
    """RepoWeaver's retry path: a held file moved back into _inbox/ must survive recovery
    even though an identical copy is committed in .wiki/failed/."""
    _to_write(corpus, [], tmp_path / "r")
    step(corpus, "checks")
    step(corpus, "checks")
    step(corpus, "hold", str(tmp_path / "r"))
    (corpus / "_inbox" / ORCHARD).write_bytes((corpus / ".wiki/failed" / ORCHARD).read_bytes())
    token = step(corpus, "recover")
    assert (corpus / "_inbox" / ORCHARD).exists()  # the file first, then the routing token
    assert token == (0, "clean")
