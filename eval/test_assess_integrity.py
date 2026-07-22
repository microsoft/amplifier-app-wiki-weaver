# pyright: reportMissingImports=false
"""Assess-integrity regression tests (2026-07 production-failure triage).

THE INCIDENT: a 25-source, 10-hour production run quarantined 4 sources, and
ALL FOUR were the same engine artifact -- the assess node's child session hit
its 50-call tool-round ceiling doing OPEN-ENDED re-verification across a
48-page wiki and never rendered a verdict. The failures were INVISIBLE in
``.wiki/.processed.jsonl`` (the ledger recorded only the 21 successes).

These tests pin the three wiki-weaver-side fixes:

1. BOUNDED ASSESS SCOPE (root cost driver): ingest writes a touched-pages
   manifest (``.ai/touched-pages.txt``); assess verifies EXACTLY those pages,
   with a fail-open fallback when the manifest is missing. The verdict
   contract (flat bare-JSON final message, PR #41) is byte-for-byte intact.
2. LEDGER FAILURE RECORDS: quarantining a source to ``.wiki/failed/`` now
   appends a symmetrical ``status: "failed"`` ledger record (engine drain via
   ingest_fail.py; Python drain via lib.py), WITHOUT breaking the #44 resume
   rules: a failed row never marks a source processed (re-drop retry works),
   and result.json counts derived from the ledger count it as failed.
3. FAILURE-KIND TAGGING: ``no_verdict`` (assess never rendered a verdict --
   the incident class) vs ``judged_non_converged`` (assess voted refine and
   the cycle budget ran out) vs ``unknown``, via the assessment-file-mtime
   heuristic in ``wiki_weaver.lib.classify_failure_kind``.

(The iteration-budget knob itself -- ``max_tool_rounds_per_input: 50`` in
attractor-pipeline.yaml's agents block -- is NOT settable per-node from
wiki-weaver; that raise is an attractor-repo change, intentionally absent
here.)

All engine paths are mocked (no LLM calls); isolated tmp_path wikis only.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Engine-adjacent modules pull in the attractor deps; skip cleanly in a
# lightweight CI env -- same convention as eval/test_ingest_drain.py.
pytest.importorskip("wiki_weaver.engine_runner")

import wiki_weaver.engine_runner as er  # noqa: E402
import wiki_weaver.retention as retention  # noqa: E402
import wiki_weaver.reweave as reweave  # noqa: E402
from wiki_weaver.lib import (  # noqa: E402
    FAILURE_KIND_JUDGED,
    FAILURE_KIND_NO_VERDICT,
    FAILURE_KIND_UNKNOWN,
    _append_failure_ledger,
    _append_ledger,
    _processed_sources,
    _read_ledger,
    classify_failure_kind,
    ingest,
    touched_manifest_path,
    wiki_failed,
)

INBOX = "_inbox"
SOURCES = "_sources"

SYNTHESIZE_DOT = _REPO / "pipeline" / "synthesize.dot"


# ---------------------------------------------------------------------------
# Fixtures + helpers (patterns from test_ingest_drain.py / test_durable_drain.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _advisory_default_env(monkeypatch):
    """Gates are ADVISORY by default; keep the shell env out of it."""
    monkeypatch.delenv("WIKI_WEAVER_ENFORCE_GATES", raising=False)


@pytest.fixture(autouse=True)
def _bypass_reweave_gate(monkeypatch):
    """Stub the overview re-weave gate (orthogonal; eval/test_reweave.py)."""
    monkeypatch.setattr(
        "wiki_weaver.reweave.reweave_overview_if_needed",
        lambda *_a, **_kw: reweave.ReweaveGateResult(
            initial_passed=True,
            attempts=0,
            final_passed=True,
            initial_report="stub",
            final_report="stub",
        ),
    )


@pytest.fixture(autouse=True)
def _passing_retention_gate(monkeypatch):
    """Stub the claim-retention gate to PASS (orthogonal; own test files)."""
    monkeypatch.setattr(
        "wiki_weaver.retention.enforce_retention_gate",
        lambda *_a, **_kw: retention.RetentionGateDecision(
            action="proceed", message=""
        ),
    )


def _make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True)
    (wiki / ".wiki").mkdir()
    (wiki / INBOX).mkdir()
    (wiki / SOURCES).mkdir()
    (wiki / ".wiki" / ".processed.jsonl").touch()
    return wiki


def _seed_inbox(inbox: Path, name: str, content: str | None = None) -> Path:
    if content is None:
        content = f"# {name}\n\nUnique body text for {name}.\n"
    p = inbox / name
    p.write_text(content, encoding="utf-8")
    old = time.time() - 10  # past the 2-s drain debounce
    os.utime(p, (old, old))
    return p


def _fake_result(converged: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        converged=converged,
        status="success" if converged else "failed",
        failure_reason=None if converged else "did not converge",
        logs_dir=Path("/tmp/fake_logs"),
        notes="",
        advisories=[],
    )


def _final_result_json(wiki: Path) -> dict:
    """The single FINAL result.json under .wiki/runs/ (fails if != 1)."""
    finals = []
    for p in sorted((wiki / ".wiki" / "runs").glob("ingest-*/result.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("status") == "final":
            finals.append(data)
    assert len(finals) == 1, f"expected exactly one final result.json: {finals}"
    return finals[0]


def _node_prompt(dot_text: str, node_id: str) -> str:
    """Extract a node's prompt attribute value from raw DOT text."""
    start = dot_text.index(f"    {node_id} [")
    block = dot_text[start : dot_text.index("\n    ]", start)]
    pstart = block.index('prompt="') + len('prompt="')
    # prompt is the last attribute in these blocks; ends at the closing quote
    pend = block.rindex('"')
    return block[pstart:pend]


# ---------------------------------------------------------------------------
# Item 2 -- touched-pages manifest + bounded assess scope
# ---------------------------------------------------------------------------


def test_synthesize_dot_parses_and_validates() -> None:
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    graph = parse_dot(SYNTHESIZE_DOT.read_text(encoding="utf-8"))
    validate_or_raise(graph)


def test_ingest_prompt_instructs_manifest_write() -> None:
    dot = SYNTHESIZE_DOT.read_text(encoding="utf-8")
    prompt = _node_prompt(dot, "ingest")
    assert "TOUCHED-PAGES MANIFEST" in prompt
    assert "$touched_manifest" in prompt
    # One page path per line, overwritten each cycle, written LAST.
    assert "one wiki-relative page path per line" in prompt
    assert "OVERWRITE" in prompt


def test_assess_prompt_bounded_scope_with_fail_open_fallback() -> None:
    dot = SYNTHESIZE_DOT.read_text(encoding="utf-8")
    prompt = _node_prompt(dot, "assess")
    # Bounded work-list: manifest pages + THIS source's grounding, NOT the wiki.
    assert "VERIFICATION SCOPE" in prompt
    assert "$touched_manifest" in prompt
    assert "do NOT re-verify the whole wiki" in prompt
    assert "Do NOT open pages outside the manifest" in prompt
    # Fail-open fallback when the manifest is missing.
    assert "FALLBACK (fail-open)" in prompt
    assert "manifest missing -- full-scope fallback" in prompt


def _dot_unescape(s: str) -> str:
    """Reverse of engine_runner._dot_escape_prompt (for contract assertions)."""
    s = s.replace("\\\\", "\x00")
    s = s.replace("\\n", "\n").replace('\\"', '"')
    return s.replace("\x00", "\\")


def test_assess_verdict_contract_unchanged() -> None:
    """The flat bare-JSON final-message contract (PR #41) is intact verbatim."""
    dot = SYNTHESIZE_DOT.read_text(encoding="utf-8")
    prompt = _dot_unescape(_node_prompt(dot, "assess"))
    for required in (
        "Your FINAL message MUST end with exactly one FLAT JSON object",
        '{"status": "success", "preferred_label": "converged"}',
        '{"status": "success", "preferred_label": "refine"}',
        "FLATNESS RULES",
        "FINAL-MESSAGE CONTRACT",
        "Do NOT report your verdict via the report_outcome tool",
    ):
        assert required in prompt, f"verdict-contract text missing: {required!r}"


def test_build_dot_substitutes_touched_manifest(tmp_path: Path) -> None:
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    from wiki_weaver.policy import load_policy

    wiki = _make_wiki(tmp_path)
    src = tmp_path / "s.md"
    src.write_text("# s\n\nbody\n", encoding="utf-8")

    dot = er.build_dot(src, wiki, load_policy(wiki), source_id=7)
    assert "$touched_manifest" not in dot
    assert str(touched_manifest_path(wiki)) in dot
    validate_or_raise(parse_dot(dot))


def test_ingest_setup_emits_manifest_key_and_clears_stale(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    import wiki_weaver.ingest_setup as ingest_setup

    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    # Stale manifest from a PREVIOUS source must be deleted by setup.
    manifest = touched_manifest_path(wiki)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("stale-page.md\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["ingest_setup.py", str(wiki), str(wiki)])
    assert ingest_setup.main() == 0

    out = json.loads(capsys.readouterr().out)
    assert out["has_source"] == "true"
    assert out["touched_manifest"] == str(manifest)
    assert not manifest.exists(), "stale manifest must be deleted per-source"
    # fail_cmd now carries source_id + started_at (classification inputs).
    fail_parts = out["fail_cmd"].split()
    assert fail_parts[-2] == out["source_id"]
    float(fail_parts[-1])  # started_at parses as a float


def test_run_inner_clears_stale_manifest(tmp_path: Path, monkeypatch) -> None:
    wiki = _make_wiki(tmp_path)
    src = tmp_path / "s.md"
    src.write_text("# s\n\nbody\n", encoding="utf-8")
    manifest = touched_manifest_path(wiki)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("stale-page.md\n", encoding="utf-8")

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        from amplifier_module_pipeline_runner import PipelineResult

        return PipelineResult(
            status="success", notes="", logs_dir=Path("/tmp"), raw="{}"
        )

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    result = er.run_inner(src, wiki, source_id=1)
    assert result.converged
    assert not manifest.exists(), "run_inner must delete a stale manifest"


# ---------------------------------------------------------------------------
# Item 4 -- failure-kind classification (unit)
# ---------------------------------------------------------------------------


def test_classify_failure_kind_matrix(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    started = time.time()

    # No reference time -> undecidable.
    assert classify_failure_kind(wiki, None) == FAILURE_KIND_UNKNOWN
    # No assessment file -> assess never rendered a verdict.
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_NO_VERDICT

    assessment = wiki / ".ai" / "assessment.md"
    assessment.parent.mkdir(parents=True, exist_ok=True)
    assessment.write_text("C1..C5 scores", encoding="utf-8")

    # Stale assessment (older than this source's synthesis) -> no_verdict.
    old = started - 100
    os.utime(assessment, (old, old))
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_NO_VERDICT

    # Assessment written DURING this synthesis -> a verdict-rendering assess ran.
    fresh = started + 5
    os.utime(assessment, (fresh, fresh))
    assert classify_failure_kind(wiki, started) == FAILURE_KIND_JUDGED


# ---------------------------------------------------------------------------
# Item 3 -- engine-drain fail_handler (ingest_fail.py) ledger records
# ---------------------------------------------------------------------------


def _run_ingest_fail(wiki: Path, src: Path, *extra: str) -> int:
    import wiki_weaver.ingest_fail as ingest_fail

    argv = ["ingest_fail.py", str(wiki), str(src), *extra]
    with patch.object(sys, "argv", argv):
        return ingest_fail.main()


def test_ingest_fail_writes_ledger_failure_record(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    run_dir = wiki / ".wiki" / "runs" / "ingest-20260722-000000"
    run_dir.mkdir(parents=True)
    src = _seed_inbox(wiki / INBOX, "bad.md")
    started = f"{time.time():.3f}"

    assert _run_ingest_fail(wiki, src, "7", started) == 0

    assert not src.exists()
    assert (wiki_failed(wiki) / "bad.md").is_file()
    rows = _read_ledger(wiki)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "bad.md"
    assert row["source_id"] == 7
    assert row["status"] == "failed"
    assert row["converged"] is False
    assert row["hash"], "content hash must be recorded (symmetry with success)"
    assert row["failed_to"].endswith("bad.md")
    assert row["reason"]
    # No assessment file existed -> the incident class: no verdict rendered.
    assert row["failure_kind"] == FAILURE_KIND_NO_VERDICT
    assert row["logs_dir"].endswith("ingest-20260722-000000")


def test_ingest_fail_tags_judged_when_assessment_is_fresh(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "bad.md")
    started = time.time()
    assessment = wiki / ".ai" / "assessment.md"
    assessment.parent.mkdir(parents=True, exist_ok=True)
    assessment.write_text("scores", encoding="utf-8")
    fresh = started + 5
    os.utime(assessment, (fresh, fresh))

    assert _run_ingest_fail(wiki, src, "7", f"{started:.3f}") == 0
    assert _read_ledger(wiki)[0]["failure_kind"] == FAILURE_KIND_JUDGED


def test_ingest_fail_two_arg_backward_compat(tmp_path: Path) -> None:
    """An older 2-arg fail_cmd still quarantines + records (kind unknown)."""
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "bad.md")

    assert _run_ingest_fail(wiki, src) == 0
    assert (wiki_failed(wiki) / "bad.md").is_file()
    row = _read_ledger(wiki)[0]
    assert row["status"] == "failed"
    assert row["source_id"] == ""
    assert row["failure_kind"] == FAILURE_KIND_UNKNOWN


def test_ingest_fail_idempotent_retry_writes_no_duplicate_record(
    tmp_path: Path,
) -> None:
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "bad.md")

    assert _run_ingest_fail(wiki, src, "7", f"{time.time():.3f}") == 0
    assert len(_read_ledger(wiki)) == 1
    # Retry after the source is already quarantined: exit 0, NO second record.
    assert _run_ingest_fail(wiki, src, "7", f"{time.time():.3f}") == 0
    assert len(_read_ledger(wiki)) == 1


# ---------------------------------------------------------------------------
# Item 3 -- resume/ledger compatibility (#44 "ledger-wins" rules)
# ---------------------------------------------------------------------------


def test_processed_sources_excludes_failed_rows(tmp_path: Path) -> None:
    wiki = _make_wiki(tmp_path)
    _append_ledger(
        wiki,
        {"source": "good.md", "converged": True, "status": "success"},
    )
    _append_failure_ledger(
        wiki,
        source="bad.md",
        source_id=2,
        file_hash="abc",
        failed_to="x",
        reason="did not converge",
        failure_kind=FAILURE_KIND_NO_VERDICT,
    )
    processed = _processed_sources(wiki)
    assert "good.md" in processed
    assert "bad.md" not in processed, (
        "a FAILED ledger row must never mark a source processed -- "
        "the documented retry path is re-dropping it into _inbox/"
    )


def test_failed_source_redropped_into_inbox_is_retried(tmp_path: Path) -> None:
    """The retry path survives the new failure records: a source with a
    prior FAILED ledger row, re-dropped into _inbox/, is re-attempted (not
    skipped as already-processed) and can converge."""
    wiki = _make_wiki(tmp_path)
    src = _seed_inbox(wiki / INBOX, "retry.md")
    _append_failure_ledger(
        wiki,
        source="retry.md",
        source_id=1,
        file_hash="deadbeef",
        failed_to=str(wiki_failed(wiki) / "retry.md"),
        reason="did not converge",
        failure_kind=FAILURE_KIND_NO_VERDICT,
    )

    calls: list[str] = []

    def fake_run_inner(source_path, wiki_dir, **kwargs):
        calls.append(Path(source_path).name)
        return _fake_result(converged=True)

    with patch.object(er, "run_inner", fake_run_inner):
        rc = ingest(wiki)

    assert calls == ["retry.md"], "the previously-failed source must be retried"
    assert rc == 0
    assert (wiki / SOURCES / "retry.md").is_file()
    assert not src.exists()
    rows = _read_ledger(wiki)
    assert [r["status"] for r in rows] == ["failed", "success"]


def test_drain_not_converged_records_failure_and_result_failed(
    tmp_path: Path,
) -> None:
    """Python drain path: a non-converged source is quarantined AND recorded
    (ledger failure row + result.json failed[] + counts), with the
    failure kind classified from the assessment-file heuristic."""
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "nope.md")

    def fake_run_inner(source_path, wiki_dir, **kwargs):
        # Simulate an assess that DID render refine verdicts: write a fresh
        # assessment during this source's synthesis window.
        assessment = Path(wiki_dir) / ".ai" / "assessment.md"
        assessment.parent.mkdir(parents=True, exist_ok=True)
        assessment.write_text("C1=3 refine", encoding="utf-8")
        return _fake_result(converged=False)

    with patch.object(er, "run_inner", fake_run_inner):
        rc = ingest(wiki)

    assert rc == 5  # attempted > 0, converged == 0 -> verdict failed
    assert (wiki_failed(wiki) / "nope.md").is_file()

    row = _read_ledger(wiki)[0]
    assert row["status"] == "failed"
    assert row["converged"] is False
    assert row["failure_kind"] == FAILURE_KIND_JUDGED
    assert "did not converge" in row["reason"]

    result = _final_result_json(wiki)
    assert result["verdict"] == "failed"
    assert result["counts"]["failed"] == 1
    assert result["counts"]["converged"] == 0
    assert result["failed"] == [
        {
            "source": "nope.md",
            "reason": row["reason"],
            "failure_kind": FAILURE_KIND_JUDGED,
        }
    ]


def test_drain_not_converged_without_assessment_tags_no_verdict(
    tmp_path: Path,
) -> None:
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "nope.md")

    with patch.object(er, "run_inner", lambda *a, **kw: _fake_result(converged=False)):
        ingest(wiki)

    assert _read_ledger(wiki)[0]["failure_kind"] == FAILURE_KIND_NO_VERDICT


def test_resume_adopts_failed_records_and_does_not_requeue(tmp_path: Path) -> None:
    """#44 resume compatibility with failed ledger entries: a crashed drain
    left (a) a failed ledger row, (b) the source quarantined in .wiki/failed/
    (NOT in _inbox/), and (c) an in_progress snapshot carrying the failure.
    The resume must adopt the records, count the source as FAILED (never
    converged), and must NOT re-queue it."""
    from wiki_weaver.run_result import STATUS_IN_PROGRESS, build_result

    wiki = _make_wiki(tmp_path)
    # (a) failed ledger row; (b) quarantined file, inbox stays empty.
    _append_failure_ledger(
        wiki,
        source="x.md",
        source_id=3,
        file_hash="cafe",
        failed_to=str(wiki_failed(wiki) / "x.md"),
        reason="did not converge",
        failure_kind=FAILURE_KIND_NO_VERDICT,
    )
    wiki_failed(wiki).mkdir(parents=True, exist_ok=True)
    (wiki_failed(wiki) / "x.md").write_text("quarantined", encoding="utf-8")
    # (c) crashed run's in_progress snapshot.
    crash_dir = wiki / ".wiki" / "runs" / "ingest-20260722-000001"
    crash_dir.mkdir(parents=True)
    failed_rec = {
        "source": "x.md",
        "reason": "did not converge",
        "failure_kind": FAILURE_KIND_NO_VERDICT,
    }
    (crash_dir / "result.json").write_text(
        json.dumps(
            build_result(
                "ingest-20260722-000001",
                {
                    "total": 1,
                    "converged": 0,
                    "failed": 1,
                    "blocked": 0,
                    "errored": 0,
                    "skipped": 0,
                },
                failed=[failed_rec],
                sources=[{"name": "x.md", "status": "not-converged"}],
                status=STATUS_IN_PROGRESS,
            )
        ),
        encoding="utf-8",
    )

    def _explode(*_a, **_kw):  # nothing may be (re)attempted
        raise AssertionError("run_inner must not be called on resume-only drain")

    with patch.object(er, "run_inner", _explode):
        rc = ingest(wiki)

    assert rc == 5  # combined state: 1 attempted, 0 converged -> failed
    result = _final_result_json(wiki)
    assert result["counts"] == {
        "total": 1,
        "converged": 0,
        "failed": 1,
        "blocked": 0,
        "errored": 0,
        "skipped": 0,
    }
    assert result["failed"] == [failed_rec]
    assert result["sources"] == [{"name": "x.md", "status": "not-converged"}]
    # The crashed snapshot was superseded (never double-adopted).
    crashed = json.loads((crash_dir / "result.json").read_text(encoding="utf-8"))
    assert crashed["status"] == "superseded"
    # And the quarantined source was NOT re-queued.
    assert (wiki_failed(wiki) / "x.md").is_file()
    assert not any((wiki / INBOX).iterdir())


# ---------------------------------------------------------------------------
# Item 3 -- run_ingest (engine drain) result.json counting from the ledger
# ---------------------------------------------------------------------------


def test_run_ingest_counts_failure_rows_as_failed_not_converged(
    monkeypatch, tmp_path: Path
) -> None:
    """The engine-drain result.json derives its counts from parsed ledger
    rows: a failure row appended by ingest_fail.py must count as FAILED and
    surface in failed[], never inflate the converged count (which a blind
    line-count delta would have done)."""
    wiki = _make_wiki(tmp_path)
    _seed_inbox(wiki / INBOX, "a.md")
    _seed_inbox(wiki / INBOX, "b.md")
    # Pre-existing history must not leak into this run's counts.
    _append_ledger(wiki, {"source": "old.md", "converged": True, "status": "success"})

    async def fake_run_pipeline(dot_source: str, **kwargs: Any) -> Any:
        # Simulate what ingest.dot's archive + fail_handler nodes do:
        # one converged source (success ledger row), one quarantined source
        # (failure ledger row + file moved to .wiki/failed/).
        from amplifier_module_pipeline_runner import PipelineResult

        _append_ledger(wiki, {"source": "a.md", "converged": True, "status": "success"})
        _append_failure_ledger(
            wiki,
            source="b.md",
            source_id=2,
            file_hash="beef",
            failed_to=str(wiki_failed(wiki) / "b.md"),
            reason="did not converge",
            failure_kind=FAILURE_KIND_NO_VERDICT,
        )
        wiki_failed(wiki).mkdir(parents=True, exist_ok=True)
        (wiki / INBOX / "a.md").unlink()
        (wiki / INBOX / "b.md").rename(wiki_failed(wiki) / "b.md")
        return PipelineResult(
            status="success", notes="", logs_dir=Path("/tmp"), raw="{}"
        )

    monkeypatch.setattr(er, "run_pipeline", fake_run_pipeline)
    er.run_ingest(wiki)

    results = sorted((wiki / ".wiki" / "runs").glob("ingest-*/result.json"))
    assert len(results) == 1
    result = json.loads(results[0].read_text(encoding="utf-8"))
    assert result["counts"]["converged"] == 1
    assert result["counts"]["failed"] == 1
    assert result["counts"]["total"] == 2
    assert result["verdict"] == "partial"
    assert result["failed"] == [
        {
            "source": "b.md",
            "reason": "did not converge",
            "failure_kind": FAILURE_KIND_NO_VERDICT,
        }
    ]
