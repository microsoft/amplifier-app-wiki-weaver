"""Team Pulse-shaped jobs. Team Pulse sends only the pages relevant to the new source, the
ledger and the one new source in `_inbox/` - not `_sources/`. Such a job must converge
(the pages already quote earlier sources whose text is not in the job), its run record
must keep every check finding, and its progress must move per source. Model-free except
the last test, which repeats the staging job locally and skips without the engine and key.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from engine import needs_engine_and_key
from test_steps import ORCHARD, cite, fake_brief, page, step

from wiki_weaver import checks as ck
from wiki_weaver import cli
from wiki_weaver import ledger as lg
from wiki_weaver import result as rs

OLD = "Old Notes.md"  # s7 in the ledger; its text is not in the job
NEW = "New Source.md"  # s8, the one in flight
NEW_TEXT = "Wren Talbot: the new source says the rollout moved to Monday the tenth."
SOURCES = {8: (NEW, NEW_TEXT)}
KNOWN = {7: OLD, 8: NEW}
FM = f"---\ntitle: T\ntype: topics\nsources:\n  - {OLD}\n  - {NEW}\nlast_updated: 2031-03-02\n---\n"
KEPT = '- Old claim [s7: "the old notes said these words"]\n'
PRIOR = FM + KEPT
QUOTE = "The orchard sensor rollout ships on Friday"  # verbatim in the ORCHARD fixture


# Fix 1 - verify only the quotes this write wrote ----------------------------------------


def test_a_prior_quote_from_absent_ledger_source_passes():
    assert ck.check_citations("p.md", PRIOR, SOURCES, prior=PRIOR, known=KNOWN) == []


def test_b_new_quote_from_the_current_source_passes_beside_it():
    text = PRIOR + '- Moved [s8: "rollout moved to Monday the tenth"]\n'
    assert ck.check_citations("p.md", text, SOURCES, prior=PRIOR, known=KNOWN) == []


def test_c_new_quote_citing_a_ledger_known_absent_source_fails():
    text = PRIOR + '- More [s7: "five more words from old notes"]\n'
    errs = ck.check_citations("p.md", text, SOURCES, prior=PRIOR, known=KNOWN)
    assert len(errs) == 1 and "s7" in errs[0] and "not verifiable" in errs[0], errs
    assert ck.check_kind(errs[0]) == "citations"


def test_d_id_in_neither_ledger_nor_sources_fails_even_when_prior():
    text = PRIOR + '- Ghost [s9: "one two three four five"]\n'
    errs = ck.check_citations("p.md", text, SOURCES, prior=text, known=KNOWN)
    assert errs == ["p.md: cites unknown source [s9]"]


def test_e_changed_prior_quote_is_new_and_must_verify():
    text = FM + '- Old claim [s7: "the old notes said these WORDS"]\n'
    errs = ck.check_citations("p.md", text, SOURCES, prior=PRIOR, known=KNOWN)
    assert len(errs) == 1 and "not verifiable" in errs[0], errs
    # with that source's text present, the changed quote must be found verbatim in it
    present = {**SOURCES, 7: (OLD, "the old notes said these words and nothing else")}
    errs = ck.check_citations("p.md", text, present, prior=PRIOR, known=KNOWN)
    assert len(errs) == 1 and "not found verbatim" in errs[0], errs


def test_prior_quote_is_the_same_quote_whitespace_aside():
    text = FM + '- Old claim [s7: "the  old notes said\tthese words"]\n'
    assert ck.check_citations("p.md", text, SOURCES, prior=PRIOR, known=KNOWN) == []


def test_prior_quote_is_not_re_read_even_when_its_source_is_present():
    # one rule for every job shape: a prior quote stands on its ledger-known id; the full
    # audit of every quote against every source is `wiki-weaver lint`
    present = {**SOURCES, 7: (OLD, "text in which the old quote does not appear at all")}
    assert ck.check_citations("p.md", PRIOR, present, prior=PRIOR, known=KNOWN) == []
    assert ck.check_citations("p.md", PRIOR, present) != []  # lint's call: verified


def test_prior_quote_still_needs_its_source_listed_in_frontmatter():
    fm = f"---\ntitle: T\ntype: topics\nsources:\n  - {NEW}\nlast_updated: 2031-03-02\n---\n"
    errs = ck.check_citations("p.md", fm + KEPT, SOURCES, prior=PRIOR, known=KNOWN)
    assert errs == [f"p.md: cites s7 ({OLD}) but it is not in frontmatter sources"]


def test_without_a_prior_every_quote_verifies_as_before():
    # a new page, or `wiki-weaver lint`: no prior text, so nothing is taken on trust
    assert ck.check_citations("p.md", PRIOR, SOURCES) == ["p.md: cites unknown source [s7]"]
    assert ck.check_citations("p.md", PRIOR, SOURCES, known=KNOWN) != []


def test_known_sources_lists_every_ledger_id(corpus: Path):
    assert lg.known_sources(corpus) == {}
    lg.append_row(corpus, lg.make_row(corpus, source=OLD, file_hash="a" * 64, status="failed"))
    lg.append_row(corpus, lg.make_row(corpus, source=NEW, file_hash="b" * 64, status="converged"))
    assert lg.known_sources(corpus) == {1: OLD, 2: NEW}
    assert lg.source_versions(corpus) == {}  # neither file is on disk


def _tp_shaped(corpus: Path) -> None:
    """Make the fixture corpus Team Pulse-shaped: one source in _inbox/, a ledger naming a
    source whose file is not here, and a committed page quoting that source."""
    for f in (corpus / "_inbox").glob("*.md"):
        if f.name != ORCHARD:
            f.unlink()
    lg.append_row(
        corpus,
        lg.make_row(
            corpus, source=OLD, file_hash="a" * 64, status=lg.STATUS_CONVERGED, source_id=7
        ),
    )
    (corpus / "orchard-rollout.md").write_text(page("Orchard Rollout", [OLD], "## Plan\n" + KEPT))
    subprocess.run(["git", "add", "-A"], cwd=corpus, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "tp-shaped"],
        cwd=corpus,
        check=True,
    )


def _write_pages(corpus: Path, added: str) -> None:
    """The writer's output: the summary page, and the existing page with its old quote kept
    and ``added`` woven in."""
    cur = json.loads((corpus / ".wiki/work/current.json").read_text())
    summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
    (corpus / summary).write_text(page(summary, [cur["filename"]], cite(corpus, QUOTE) + "\n"))
    (corpus / "orchard-rollout.md").write_text(
        page("Orchard Rollout", [OLD, cur["filename"]], "## Plan\n" + KEPT + added)
    )


def test_steps_accept_the_prior_quote_and_verify_the_new_one(corpus: Path, tmp_path: Path):
    _tp_shaped(corpus)
    run = tmp_path / "run"
    assert step(corpus, "select", str(run), "0", "-") == (0, "source")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    assert step(corpus, "page_select") == (0, "ok")
    _write_pages(corpus, f"- Ships {cite(corpus, QUOTE)}\n")
    assert step(corpus, "checks") == (0, "pass")
    assert step(corpus, "commit", str(run)) == (0, "next")
    row = json.loads((corpus / ".wiki/.processed.jsonl").read_text().splitlines()[-1])
    assert row["status"] == "converged" and "orchard-rollout.md" in row["pages_touched"]
    text = (corpus / "orchard-rollout.md").read_text()
    assert KEPT in text and QUOTE in text


def test_steps_hold_keeps_every_finding_in_the_row(corpus: Path, tmp_path: Path):
    _tp_shaped(corpus)
    run = tmp_path / "run"
    step(corpus, "select", str(run), "0", "-")
    step(corpus, "assemble")
    fake_brief(corpus, ["orchard-rollout"])
    step(corpus, "page_select")
    bad = "".join(f'- Claim {i} [s7: "newly added words number {i} here"]\n' for i in range(6))
    _write_pages(corpus, bad)
    assert step(corpus, "checks") == (0, "rewrite")
    assert (corpus / ".wiki/work/findings.md").read_text().count("not verifiable") == 6
    assert step(corpus, "write_guard") == (0, "ok")
    assert step(corpus, "checks") == (0, "hold")
    assert step(corpus, "hold", str(run)) == (0, "next")
    row = json.loads((run / "changes.jsonl").read_text().splitlines()[-1])
    assert row["failure_kind"] == "checks_failed"
    # Fix 2: the row keeps the whole list; `reason` stays the short summary of it
    assert len(row["errors"]) == 6 and all("not verifiable" in e for e in row["errors"])
    assert row["reason"] == "checks failed twice: " + "; ".join(row["errors"][:3]) + " (+3 more)"
    # rollback: the page is byte-identical to its committed version
    committed = page("Orchard Rollout", [OLD], "## Plan\n" + KEPT)
    assert (corpus / "orchard-rollout.md").read_text() == committed


# Fix 2 - the run record keeps the full error list -------------------------------------


def test_result_failed_entries_carry_the_full_error_list_capped():
    many = [f"p.md: finding {i}" for i in range(60)]
    rows = [
        {"source": "a", "status": "failed", "reason": "checks failed twice: ...",
         "failure_kind": "checks_failed", "errors": many},
        {"source": "b", "status": "failed", "reason": "brief: model step failed \u2014 x",
         "failure_kind": "model_step_failed"},
        {"source": "c", "status": "converged"},
    ]  # fmt: skip
    d = rs.build(run_id="r", status="final", rows=rows, errored=[])
    a, b = d["failed"]
    assert rs.ERRORS_CAP == 50 and a["errors"] == many[:50]
    assert b["errors"] == [b["reason"]]  # a single cause: the list is that cause
    assert all(set(f) == {"source", "reason", "failure_kind", "errors"} for f in d["failed"])


# Fix 3 - progress per source, not per batch -----------------------------------------------


def test_refresh_rewrites_only_an_in_progress_record(tmp_path: Path):
    p = tmp_path / "result.json"
    extra = {"started": "2026-10-21T00:00:00Z", "model_calls": 3, "lens_page_types": [{"t": 1}]}
    errored = [{"kind": "engine", "reason": "x"}]
    rs.write(p, rs.build(run_id="r1", status="in_progress", rows=[], errored=errored, extra=extra))
    rows = [
        {"source": "a", "status": "converged"},
        {"source": "b", "status": "failed", "reason": "r", "failure_kind": "unknown", "errors": ["r"]},
    ]  # fmt: skip
    out = rs.refresh(p, rows)
    data = json.loads(p.read_text())
    assert out == data and data["status"] == "in_progress" and data["run_id"] == "r1"
    assert data["counts"] == {
        "converged": 1, "failed": 1, "blocked": 0, "errored": 1, "skipped": 0, "total": 3
    }  # fmt: skip
    assert [s["name"] for s in data["sources"]] == ["a", "b"]
    assert data["failed"][0]["errors"] == ["r"] and data["errored"] == errored
    assert all(data[k] == v for k, v in extra.items())  # the rest of the record is kept
    rs.write(p, rs.build(run_id="r1", status="final", rows=rows, errored=[]))
    before = p.read_text()
    assert rs.refresh(p, []) is None and p.read_text() == before  # a final record is never touched
    assert rs.refresh(tmp_path / "missing.json", rows) is None


def _engine(observed: list[dict]):
    """Stand-in for the dot-runner batch: the real glue steps with the model outputs
    written by hand, and after each source finishes the run's in-progress result.json is
    read - the way a progress panel polls it mid-batch."""

    def run(graph, corpus, params, logs_root, **kw):
        run_dir = params["run_dir"]
        while step(corpus, "select", run_dir, params["cap"], params["only"])[1] == "source":
            step(corpus, "assemble")
            cur = json.loads((corpus / ".wiki/work/current.json").read_text())
            fake_brief(corpus, ["orchard-rollout"])
            step(corpus, "page_select")
            summary = (corpus / ".wiki/work/selected.txt").read_text().split()[0]
            body = (cite(corpus, QUOTE) if cur["filename"] == ORCHARD else "") + "\n"
            for name, first in ((summary, ""), ("orchard-rollout.md", "## Plan\n")):
                p = corpus / name
                if p.exists():  # integrate, never replace: the loss guard checks this
                    p.write_text(p.read_text().rstrip("\n") + "\n" + body)
                else:
                    p.write_text(page(name, [cur["filename"]], first + body))
            verdict = step(corpus, "checks")[1]
            assert verdict == "pass", verdict
            step(corpus, "commit", run_dir)
            observed.append(json.loads((Path(run_dir).parent / "result.json").read_text()))
        return 0

    return run


def test_in_progress_result_moves_per_source(corpus: Path, monkeypatch: pytest.MonkeyPatch):
    for f in sorted((corpus / "_inbox").glob("*.md"))[3:]:
        f.unlink()
    assert len(list((corpus / "_inbox").glob("*.md"))) == 3
    observed: list[dict] = []
    monkeypatch.setenv("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(cli, "run_graph", _engine(observed))
    a = cli.build_parser().parse_args(["ingest", "--wiki", str(corpus)])
    assert a.func(a) == 0
    # read between sources, inside the batch: 1 -> 2 -> 3, each still in progress
    assert [o["counts"]["total"] for o in observed] == [1, 2, 3]
    assert [o["counts"]["converged"] for o in observed] == [1, 2, 3]
    assert all(o["status"] == "in_progress" for o in observed)
    assert [len(o["sources"]) for o in observed] == [1, 2, 3]
    assert all(o["sources"][-1]["status"] == "converged" for o in observed)
    final = json.loads(max((corpus / ".wiki/runs").glob("ingest-*/result.json")).read_text())
    assert final["status"] == "final" and final["counts"]["total"] == 3
    assert json.loads((corpus / ".wiki/last-run.json").read_text())["counts"]["total"] == 3


def test_per_source_result_is_written_atomically(corpus: Path, tmp_path: Path, monkeypatch):
    from wiki_weaver import steps

    monkeypatch.chdir(corpus)
    run = tmp_path / "ingest-x"
    batch = run / "batch-01"
    batch.mkdir(parents=True)
    rs.write(
        run / "result.json", rs.build(run_id="ingest-x", status="in_progress", rows=[], errored=[])
    )
    replaced: list[str] = []
    real = os.replace

    def spy(src, dst):
        replaced.append(Path(dst).name)
        return real(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    steps.record(batch, lg.make_row(Path("."), source="x.md", file_hash="h" * 64, status="failed"))
    assert "result.json" in replaced, replaced
    data = json.loads((run / "result.json").read_text())
    assert data["status"] == "in_progress" and data["counts"]["failed"] == 1
    assert data["sources"] == [
        {"name": "x.md", "status": "failed", "compactions": {}, "ingress_truncations": {}}
    ]


# End to end - the staging job, locally --------------------------------------------------

TP_SHAPED = Path(
    os.environ.get(
        "WW_TP_SHAPED_CORPUS", "~/Desktop/Development/08-CI-fable/phase2/tp-shaped/corpus"
    )
).expanduser()


@needs_engine_and_key
@pytest.mark.skipif(
    not TP_SHAPED.is_dir(), reason=f"tp-shaped corpus not at {TP_SHAPED} (WW_TP_SHAPED_CORPUS)"
)
def test_e2e_tp_shaped_job_converges(tmp_path: Path):
    """Three model calls: the one job that held on staging converges once prior quotes are
    taken on the ledger's word and only the new source's quotes are verified."""
    w = tmp_path / "tp"
    shutil.copytree(TP_SHAPED, w)
    before = {p.name: p.read_text() for p in w.glob("*.md")}
    cmd = [sys.executable, "-m", "wiki_weaver.cli"]
    r = subprocess.run(
        [*cmd, "init", "--plain", str(w)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stderr
    r = subprocess.run(
        [*cmd, "ingest", "--wiki", str(w)],
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    data = json.loads((w / ".wiki/last-run.json").read_text())
    assert r.returncode == 0, (r.stdout, r.stderr, data.get("failed"))
    assert data["verdict"] == "converged" and data["counts"]["total"] == 1
    assert data["counts"]["converged"] == 1 and data["failed"] == []
    after = {p.name: p.read_text() for p in w.glob("*.md")}
    assert any("[s4:" in t for t in after.values())  # a new, verified quote from the new source
    for name, old in before.items():  # every prior quote is still there
        for sid in ("s1", "s3"):
            assert after[name].count(f"[{sid}:") >= old.count(f"[{sid}:"), name
    assert not list((w / "_inbox").glob("*.md"))  # consumed, not held
    assert not list((w / ".wiki/failed").glob("*.md"))
