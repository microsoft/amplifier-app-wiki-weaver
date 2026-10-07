"""B-lean item 1: correctness, test-first from the second review's repros. No model calls."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from test_steps import ORCHARD, cite, fake_brief, page, step

ROOT = Path(__file__).resolve().parent.parent
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
SUMMARY = "source-2031-03-02-orchard-sync.md"


def _head(corpus: Path, path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"HEAD:{path}"], cwd=corpus, capture_output=True, text=True, check=False
    )
    return r.stdout if r.returncode == 0 else None


def _commit(corpus: Path, msg: str) -> None:
    subprocess.run([*GIT, "add", "-A"], cwd=corpus, check=True)
    subprocess.run([*GIT, "commit", "-qm", msg], cwd=corpus, check=True)


def _recover(corpus: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", "recover"],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
    )


def _ingest(corpus: Path, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "x")}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "wiki_weaver.cli", "ingest", "--wiki", str(corpus)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


# ------------------------------------------------------------------ a. hold with no writer


def test_brief_failure_hold_reverts_nothing_of_the_owners(corpus: Path, tmp_path: Path):
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    # the owner's own uncommitted work, present while the brief runs
    lens_edit = (corpus / "lens.md").read_text() + "\n## Owner note\nmine\n"
    (corpus / "lens.md").write_text(lens_edit)
    (corpus / "draft-notes.md").write_text("an untracked draft")
    # the brief fails: it writes no slugs, page_select fails, the graph routes to hold
    assert step(corpus, "page_select")[0] != 0
    assert step(corpus, "hold", str(tmp_path / "r")) == (0, "next")
    assert (corpus / "lens.md").read_text() == lens_edit
    assert (corpus / "draft-notes.md").read_text() == "an untracked draft"
    assert (corpus / ".wiki/failed" / ORCHARD).exists()


def test_hold_reverts_owned_paths_and_scope_named_paths_only(corpus: Path, tmp_path: Path):
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    assert step(corpus, "page_select") == (0, "ok")
    (corpus / SUMMARY).write_text(
        page("O", [ORCHARD], cite(corpus, "words not in it at all") + "\n")
    )
    (corpus / "stray.md").write_text("the writer's out-of-scope page")
    assert step(corpus, "checks") == (0, "rewrite")
    assert step(corpus, "checks") == (0, "hold")
    (corpus / "later-draft.md").write_text("written after the checks ran")
    assert step(corpus, "hold", str(tmp_path / "r")) == (0, "next")
    assert not (corpus / SUMMARY).exists()  # owned
    assert not (corpus / "stray.md").exists()  # named by the scope check
    assert (corpus / "later-draft.md").exists()  # neither: untouched


# ------------------------------------------------------------------ b. journal trust


def test_committed_journal_reverts_nothing(corpus: Path, tmp_path: Path, monkeypatch):
    """A journal left behind after its commit landed must not make recovery revert a
    later owner edit to one of its paths."""
    from wiki_weaver import steps

    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    step(corpus, "page_select")
    q = cite(corpus, "The orchard sensor rollout ships on Friday")
    (corpus / SUMMARY).write_text(page("O", [ORCHARD], q + "\n"))
    assert step(corpus, "checks") == (0, "pass")
    monkeypatch.chdir(corpus)

    def die(*a, **k):
        raise RuntimeError("process died after the commit")

    monkeypatch.setattr(steps, "_finish", die)
    with pytest.raises(RuntimeError):
        steps.step_commit(str(tmp_path / "r"))
    monkeypatch.undo()
    assert _head(corpus, SUMMARY) is not None  # the commit landed
    owner = page("O", [ORCHARD], q + "\nThe owner added this line by hand.\n")
    (corpus / SUMMARY).write_text(owner)
    r = _recover(corpus)
    assert r.returncode == 0, r.stderr
    assert (corpus / SUMMARY).read_text() == owner
    assert not (corpus / ".wiki/work/journal.json").exists()
    assert not (corpus / "_inbox" / ORCHARD).exists()  # committed copy matches; dropped


def test_scratch_without_a_journal_stops_the_run_and_touches_nothing(corpus: Path):
    for f in (corpus / "_inbox").glob("*.md"):
        f.unlink()
    (corpus / ".wiki/work").mkdir(parents=True, exist_ok=True)
    (corpus / ".wiki/work/current.json").write_text(json.dumps({"filename": ORCHARD}))
    (corpus / SUMMARY).write_text("partial output")
    r = _ingest(corpus)
    assert r.returncode == 1, (r.returncode, r.stderr)
    assert ".wiki/work" in r.stderr
    assert (corpus / SUMMARY).read_text() == "partial output"
    assert _head(corpus, SUMMARY) is None


@pytest.mark.parametrize(
    "bad",
    [
        {
            "op": "source",
            "source": ORCHARD,
            "dest": f"_sources/{ORCHARD}",
            "paths": ["../victim.md"],
        },
        {"op": "source", "source": ORCHARD, "dest": f"_sources/{ORCHARD}", "paths": ["/etc/hosts"]},
        {"op": "source", "source": ORCHARD, "dest": "../elsewhere.md", "paths": []},
        {"op": "source", "source": "../x.md", "dest": None, "paths": []},
        {"op": "source", "source": ORCHARD, "dest": None, "paths": "lens.md"},
    ],
)
def test_an_invalid_journal_is_rejected(corpus: Path, bad: dict):
    (corpus.parent / "victim.md").write_text("outside")
    (corpus / ".wiki/work").mkdir(parents=True, exist_ok=True)
    (corpus / ".wiki/work/journal.json").write_text(json.dumps(bad))
    r = _recover(corpus)
    assert r.returncode != 0
    assert "journal" in r.stderr
    assert (corpus.parent / "victim.md").read_text() == "outside"


def test_a_differing_inbox_copy_survives_a_leftover_journal(corpus: Path, tmp_path: Path):
    (corpus / "_sources").mkdir(exist_ok=True)
    (corpus / "_sources" / ORCHARD).write_text("the committed version")
    _commit(corpus, "src")
    (corpus / "_inbox" / ORCHARD).write_text("an edited version, dropped again")
    (corpus / ".wiki/work").mkdir(parents=True, exist_ok=True)
    (corpus / ".wiki/work/journal.json").write_text(
        json.dumps(
            {
                "op": "commit",
                "source": ORCHARD,
                "dest": f"_sources/{ORCHARD}",
                "paths": [f"_sources/{ORCHARD}"],
                "committed": True,
            }
        )
    )
    assert _recover(corpus).returncode == 0
    assert (corpus / "_inbox" / ORCHARD).read_text() == "an edited version, dropped again"


# ------------------------------------------------------------------ c. contained reads in ingest


def test_a_selected_slug_that_escapes_the_corpus_is_never_read(corpus: Path, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("SECRET PAGE")
    (corpus / "evil.md").symlink_to(outside)
    (corpus / "index.md").unlink(missing_ok=True)
    (corpus / "index.md").symlink_to(outside)
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, ["evil"])
    assert step(corpus, "page_select") == (0, "ok")
    assert "SECRET" not in (corpus / ".wiki/work/pages.md").read_text()
    assert "evil.md" not in (corpus / ".wiki/work/selected.txt").read_text()
    assert not (corpus / ".wiki/work/before/evil.md").exists()


def test_lens_and_corrections_outside_the_corpus_are_never_read(corpus: Path, tmp_path: Path):
    outside = tmp_path / "outside.md"
    outside.write_text("SECRET LENS")
    (corpus / "lens/corrections/evil.md").symlink_to(outside)
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    assert step(corpus, "assemble") == (0, "ok")
    assert "SECRET" not in (corpus / ".wiki/work/context.md").read_text()
    (corpus / "lens.md").unlink()
    (corpus / "lens.md").symlink_to(outside)
    rc, _ = step(corpus, "assemble")
    assert rc != 0
    assert "SECRET" not in (corpus / ".wiki/work/context.md").read_text()


# ------------------------------------------------------------------ d. size guard


def test_oversized_writer_context_is_held_without_a_model_call(corpus: Path, tmp_path: Path):
    text = (ROOT / "pipeline" / "ingest.dot").read_text()
    assert re.search(r"page_select\s*->\s*hold\s*\[[^\]]*last_line=oversized", text)
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, [])
    r = subprocess.run(
        [sys.executable, "-m", "wiki_weaver.steps", "page_select"],
        cwd=corpus,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "WIKI_WEAVER_MAX_WRITER_CHARS": "100"},
    )
    assert r.stdout.strip().splitlines()[-1] == "oversized"
    assert step(corpus, "hold", str(tmp_path / "r")) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert re.fullmatch(r"oversized-context: \d+ chars", row["reason"]), row["reason"]
    assert row["model_calls"] == 1  # the brief only; the writer never ran


# ------------------------------------------------------------------ e. snapshot staging


def test_snapshot_commit_never_stages_scratch(corpus: Path):
    from wiki_weaver.cli import commit_pending

    gi = corpus / ".gitignore"
    gi.write_text("\n".join(x for x in gi.read_text().splitlines() if x != ".wiki/work/") + "\n")
    _commit(corpus, "gitignore without work")
    (corpus / ".wiki/work").mkdir(parents=True, exist_ok=True)
    (corpus / ".wiki/work/scratch.md").write_text("scratch")
    (corpus / "owner.md").write_text("owner")
    commit_pending(corpus, "snapshot")
    assert _head(corpus, "owner.md") == "owner"
    assert _head(corpus, ".wiki/work/scratch.md") is None


# ------------------------------------------------------------------ B-lean 3: the (as of) date


def test_pages_md_gives_each_page_its_latest_source_date(corpus: Path, tmp_path: Path):
    assert step(corpus, "select", str(tmp_path / "r"), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    assert step(corpus, "page_select") == (0, "ok")
    pages = (corpus / ".wiki/work/pages.md").read_text()
    assert (
        "## orchard-rollout.md (new page - does not exist yet; Current state as of 2031-03-02"
        in pages
    )
    assert "Current state as of" not in pages.split("## source-")[1].split("\n")[0]
    assert "today:" not in (corpus / ".wiki/work/context.md").read_text()
