"""commit is the only irreversible node: archive, ledger, log, one git
commit. These tests cover idempotency and the crash-before-ledger-write
ordering invariant that makes the whole drain resumable."""

from __future__ import annotations

import json

import pytest

from wiki_weaver.ingest import commit, select_source
from wiki_weaver.lib import WikiRoot, ensure_dir


def _seed_in_flight_source(wiki_root, name: str = "s1.txt") -> WikiRoot:
    wiki_root.add_source(name)
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    if pytest.importorskip("shutil").which("git"):
        wiki_root.init_git()
        wiki_root.commit_all("seed")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def test_commit_accept_idempotent_single_ledger_row(wiki_root):
    wr = _seed_in_flight_source(wiki_root)

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "0",
    ]  # fmt: skip

    code1 = commit.main(argv)
    assert code1 == 0
    lines_after_first = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines_after_first) == 1

    # Re-run for the SAME source id -- must be a no-op, not a duplicate row.
    code2 = commit.main(argv)
    assert code2 == 0
    lines_after_second = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines_after_second) == 1
    assert lines_after_first == lines_after_second


def test_commit_accept_emits_ingested_count_json(wiki_root, capsys):
    wr = _seed_in_flight_source(wiki_root)
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "4",
    ]  # fmt: skip
    code = commit.main(argv)
    out = capsys.readouterr().out.strip()
    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload == {"ingested_count": 5}


def test_commit_accept_empty_string_ingested_count_falls_back_to_default(wiki_root, capsys):
    """Regression (bare-$var substitution defect): pipeline/ingest.dot passes
    --ingested-count via bare $ingested_count substitution. When
    ingested_count is absent from context (e.g. the very first accept in a
    run), the engine leaves the literal token in place and bash's
    unset-variable expansion resolves it to an empty string -- so this CLI
    receives --ingested-count "" (never a missing flag). It must apply the
    default (0), not crash with an int() ValueError."""
    wr = _seed_in_flight_source(wiki_root)
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "",
    ]  # fmt: skip
    code = commit.main(argv)
    out = capsys.readouterr().out.strip()
    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload == {"ingested_count": 1}


def test_commit_accept_re_run_does_not_double_increment(wiki_root, capsys):
    wr = _seed_in_flight_source(wiki_root)
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "4",
    ]  # fmt: skip
    commit.main(argv)
    capsys.readouterr()

    code = commit.main(argv)
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0
    assert payload == {"ingested_count": 4}  # unchanged: nothing new happened


def test_commit_ordering_failure_before_ledger_write_leaves_source_pending(wiki_root):
    """Simulates a crash between "the work" and the ledger append: as long
    as ledger.jsonl was never written, select_source must still consider
    the source pending on the next pass -- this is the whole resume
    doctrine (CLI-CONTRACT.md's ordering law)."""
    wiki_root.add_source("s1.txt")
    wiki_root.add_source("s2.txt")
    wr = WikiRoot(wiki_root.root)
    # No ledger.jsonl written at all -- as if the process died before commit
    # ever got to append it.
    pending = select_source.compute_pending(wr, "")
    assert pending == ["s1.txt", "s2.txt"]


def test_commit_skip_reverts_edits_and_ledgers_source(wiki_root):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")
    wiki_root.add_source("s1.txt")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    # Simulate weave's edits: modify a tracked page and add a new untracked one.
    (wr.wiki_dir / "index.md").write_text("mutated by weave", encoding="utf-8")
    (wr.wiki_dir / "new-page.md").write_text("brand new page", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "skip",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0

    # Reverted: index.md back to its committed content, new-page.md gone.
    assert "mutated" not in (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert not (wr.wiki_dir / "new-page.md").exists()

    ledger_lines = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    record = json.loads(ledger_lines[0])
    assert record["source_id"] == "s1.txt"
    assert record["decision"] == "skip"


def test_commit_skip_cleans_up_stray_root_level_pages(wiki_root):
    """The write-path bug's second-order leak (see commit.py's revert-scope
    fix): a skipped source's stray content page that landed at wiki_root
    instead of wiki/ must be cleaned up too, not just reverted edits under
    wiki/ -- otherwise a skipped source still leaves debris behind."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")
    wiki_root.add_source("s1.txt")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    # Simulate the write-path bug: weave's file tools were rooted at
    # wiki_root, and a bare-filename write landed directly there instead of
    # under wiki/.
    (wr.root / "leaked-page.md").write_text("brand new page, wrong directory", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "skip",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0

    assert not (wr.root / "leaked-page.md").exists()


def test_commit_accept_ledger_row_has_created_updated_split_and_run_summary(wiki_root):
    """touches_per_source (DESIGN.md's headline metric): accept's ledger row
    must carry the created/updated split, and log.md must carry a run-to-date
    summary -- both computed from durable state (git + the ledger itself),
    not tracked in-process, so they are always consistent with what's on disk."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wr = _seed_in_flight_source(wiki_root, "s1.txt")

    # Simulate weave's edits: modify the tracked index page, add a new one.
    (wr.wiki_dir / "index.md").write_text("---\ntitle: Index\ntype: index\n---\n\nmore.\n", encoding="utf-8")
    (wr.wiki_dir / "new-page.md").write_text("# New Page\n", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "accept",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
        "--ingested-count", "0",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0

    record = json.loads(wr.ledger_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["pages_touched"] == 2
    assert record["pages_created"] == 1
    assert record["pages_updated"] == 1

    log_text = wr.log_path.read_text(encoding="utf-8")
    assert "2 page(s) touched (1 created, 1 updated)" in log_text
    assert "Run so far: 1 source(s) accepted" in log_text
    assert "mean 2.0 page(s)/source touched (1 created, 1 updated total)" in log_text


def test_commit_accept_run_summary_accumulates_across_sources(wiki_root):
    """A second accepted source's run summary reflects BOTH sources'
    contributions, not just its own -- the mean is over ledger.jsonl to date."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")

    wr = _seed_in_flight_source(wiki_root, "s1.txt")
    (wr.wiki_dir / "page-a.md").write_text("# Page A\n", encoding="utf-8")
    commit.main(
        [
            "--wiki-root",
            str(wr.root),
            "--decision",
            "accept",
            "--append-ledger",
            "ledger.jsonl",
            "--append-log",
            "log.md",
            "--ingested-count",
            "0",
        ]
    )

    wiki_root.add_source("s2.txt")
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s2.txt", encoding="utf-8")
    (wr.wiki_dir / "page-b.md").write_text("# Page B\n", encoding="utf-8")
    (wr.wiki_dir / "page-c.md").write_text("# Page C\n", encoding="utf-8")
    commit.main(
        [
            "--wiki-root",
            str(wr.root),
            "--decision",
            "accept",
            "--append-ledger",
            "ledger.jsonl",
            "--append-log",
            "log.md",
            "--ingested-count",
            "1",
        ]
    )

    lines = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    second = json.loads(lines[1])
    assert second["pages_created"] == 2

    log_text = wr.log_path.read_text(encoding="utf-8")
    # 1 page (s1) + 2 pages (s2) touched across 2 accepted sources -> mean 1.5
    assert "Run so far: 2 source(s) accepted, mean 1.5 page(s)/source touched (3 created, 0 updated total)" in log_text


def test_commit_skip_cleans_up_stale_review_guidance(wiki_root):
    wr = _seed_in_flight_source(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.review_guidance_file.write_text("steer this way", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "skip",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    commit.main(argv)
    assert not wr.review_guidance_file.exists()


# -- THE ledger-distinction fix: quarantine vs skip -------------------------
#
# A reviewer's own deliberate [C] Skip and an AUTOMATIC quarantine (a source
# that exhausted its re-weave attempts AND had already been offered its one
# post-exhaustion review) used to be recorded identically as "skip" in
# ledger.jsonl -- indistinguishable without reading commit contents. These
# tests prove the two are now ledgered distinctly.


def test_commit_quarantine_reverts_edits_and_ledgers_as_quarantine_not_skip(wiki_root):
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")
    wiki_root.add_source("s1.txt")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    # Simulate the exhausted weave's edits, same as commit_skip's test.
    (wr.wiki_dir / "index.md").write_text("mutated by weave", encoding="utf-8")
    (wr.wiki_dir / "new-page.md").write_text("brand new page", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "quarantine",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0

    # Reverted, exactly like a deliberate skip.
    assert "mutated" not in (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
    assert not (wr.wiki_dir / "new-page.md").exists()

    ledger_lines = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    record = json.loads(ledger_lines[0])
    assert record["source_id"] == "s1.txt"
    # THE distinction: never "skip" -- a reader of ledger.jsonl can now tell
    # a reviewer's own choice apart from an automatic outcome.
    assert record["decision"] == "quarantine"
    assert record["decision"] != "skip"


def test_commit_quarantine_log_line_distinguishes_from_deliberate_skip(wiki_root):
    wr = _seed_in_flight_source(wiki_root, "s1.txt")
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "quarantine",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    commit.main(argv)

    log_text = wr.log_path.read_text(encoding="utf-8")
    assert "quarantine | s1.txt" in log_text
    assert "AUTO-QUARANTINED" in log_text
    assert "skip |" not in log_text


def test_commit_quarantine_idempotent_single_ledger_row(wiki_root):
    wr = _seed_in_flight_source(wiki_root, "s1.txt")
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "quarantine",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip

    code1 = commit.main(argv)
    assert code1 == 0
    assert len(wr.ledger_path.read_text(encoding="utf-8").splitlines()) == 1

    code2 = commit.main(argv)
    assert code2 == 0
    assert len(wr.ledger_path.read_text(encoding="utf-8").splitlines()) == 1


def test_commit_quarantine_cleans_up_stale_review_guidance(wiki_root):
    wr = _seed_in_flight_source(wiki_root)
    ensure_dir(wr.ai_dir)
    wr.review_guidance_file.write_text("steer this way", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "quarantine",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    commit.main(argv)
    assert not wr.review_guidance_file.exists()


# -- THE curate-gate ledger distinction: declined vs skip -------------------
#
# curate_gate's [B] Decline fires BEFORE detect_kind/segment_source/weave ever
# run for a source -- there is no weave output to revert. Sharing "skip"'s
# label would make a pre-write decline indistinguishable from a post-write
# skip without reading commit contents -- the same defect "quarantine"
# already fixed for the quarantine/skip pair.


def test_commit_declined_ledgers_distinctly_from_skip_and_quarantine(wiki_root):
    wiki_root.add_source("s1.txt")
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "declined",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0

    ledger_lines = wr.ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(ledger_lines) == 1
    record = json.loads(ledger_lines[0])
    assert record["source_id"] == "s1.txt"
    assert record["decision"] == "declined"
    assert record["decision"] not in ("skip", "quarantine")


def test_commit_declined_log_line_distinguishes_from_skip_and_quarantine(wiki_root):
    wr = _seed_in_flight_source(wiki_root, "s1.txt")
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "declined",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    commit.main(argv)

    log_text = wr.log_path.read_text(encoding="utf-8")
    assert "declined | s1.txt" in log_text
    assert "DECLINED at curate_gate" in log_text
    assert "AUTO-QUARANTINED" not in log_text
    assert "skip |" not in log_text


def test_commit_declined_is_idempotent_single_ledger_row(wiki_root):
    wr = _seed_in_flight_source(wiki_root, "s1.txt")
    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "declined",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip

    code1 = commit.main(argv)
    assert code1 == 0
    assert len(wr.ledger_path.read_text(encoding="utf-8").splitlines()) == 1

    code2 = commit.main(argv)
    assert code2 == 0
    assert len(wr.ledger_path.read_text(encoding="utf-8").splitlines()) == 1


def test_commit_declined_is_harmless_no_op_when_nothing_was_ever_written(wiki_root):
    """No weave ever ran for a declined source -- the revert step must be a
    harmless no-op, never an error, when there is nothing to revert."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.init_git()
    wiki_root.add_page("index.md", "---\ntitle: Index\ntype: index\n---\n\n# Index\n")
    wiki_root.commit_all("seed")
    wiki_root.add_source("s1.txt")

    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    argv = [
        "--wiki-root", str(wr.root),
        "--decision", "declined",
        "--append-ledger", "ledger.jsonl",
        "--append-log", "log.md",
    ]  # fmt: skip
    code = commit.main(argv)
    assert code == 0
    # index.md untouched -- there was nothing to revert.
    assert "# Index" in (wr.wiki_dir / "index.md").read_text(encoding="utf-8")
