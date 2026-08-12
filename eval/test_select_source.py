"""select_source is THE resume gate: pending = sources-on-disk MINUS ledger
ids, recomputed fresh every pass, in deterministic order."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wiki_weaver.ingest import select_source
from wiki_weaver.ingest.watermark import advance_watermark_if_stream, extract_stream_identity, load_watermarks
from wiki_weaver.lib import WikiRoot, atomic_append_jsonl, split_header

FIXTURES = Path(__file__).parent / "fixtures"
REAL_STREAM = FIXTURES / "real-largest-stream-236kb.md"


def _run(wiki_root, restrict: str = "") -> int:
    argv = ["--wiki-root", str(wiki_root.root), "--restrict", restrict]
    return select_source.main(argv)


def test_resume_after_partial_ledger(wiki_root, capsys):
    for name in ("s1.txt", "s2.txt", "s3.txt", "s4.txt", "s5.txt"):
        wiki_root.add_source(name)

    ledger_path = wiki_root.root / "ledger.jsonl"
    with ledger_path.open("w", encoding="utf-8") as handle:
        for name in ("s1.txt", "s2.txt", "s3.txt"):
            handle.write(json.dumps({"source_id": name, "decision": "accept"}) + "\n")

    wr = WikiRoot(wiki_root.root)
    pending = select_source.compute_pending(wr, "")
    assert pending == ["s4.txt", "s5.txt"]

    code = _run(wiki_root)
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip().splitlines()[-1] == "has_source"
    assert wr.current_source_file.read_text(encoding="utf-8") == "s4.txt"
    assert wr.source_start_file.is_file()


def test_no_source_when_all_ledgered(wiki_root, capsys):
    wiki_root.add_source("s1.txt")
    ledger_path = wiki_root.root / "ledger.jsonl"
    ledger_path.write_text(json.dumps({"source_id": "s1.txt"}) + "\n", encoding="utf-8")

    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip().splitlines()[-1] == "no_source"


def test_no_source_on_empty_sources_dir(wiki_root, capsys):
    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip().splitlines()[-1] == "no_source"


def test_ledger_corrupt_is_fail_loud_not_silent_no_source(wiki_root, capsys):
    wiki_root.add_source("s1.txt")
    ledger_path = wiki_root.root / "ledger.jsonl"
    ledger_path.write_text("not json\n", encoding="utf-8")

    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    out = capsys.readouterr()
    assert code == 0  # routes via last_line, not exit code -- see report
    assert out.out.strip().splitlines()[-1] == "ledger_corrupt"
    # And it must NOT have written current_source.txt as if a source were chosen.
    wr = WikiRoot(wiki_root.root)
    assert not wr.current_source_file.is_file()


def test_restrict_narrows_candidate_set(wiki_root, capsys):
    for name in ("s1.txt", "s2.txt", "s3.txt"):
        wiki_root.add_source(name)

    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", "s3.txt"])
    out = capsys.readouterr()
    assert code == 0
    assert out.out.strip().splitlines()[-1] == "has_source"
    wr = WikiRoot(wiki_root.root)
    assert wr.current_source_file.read_text(encoding="utf-8") == "s3.txt"


# ---------------------------------------------------------------------------
# STALE-WATERMARK GUARD: an already-ledgered kind=stream source whose file
# grows in place under the SAME filename (verified real-corpus defect --
# see KNOWN_ISSUES.md #6's corrected root cause). Reproduced here against
# the real 236KB stream fixture, exactly like it happened in the wild: an
# early snapshot is ledgered, then the file is overwritten in place (never
# given a new filename) with the full, larger real content.
# ---------------------------------------------------------------------------


def _ledger_one_segment_accept(ledger_path, source_id: str) -> None:
    atomic_append_jsonl(
        ledger_path, {"source_id": source_id, "decision": "accept", "segment_index": 1, "segment_total": 1}
    )


def test_finds_no_growth_when_nothing_has_grown(wiki_root):
    """The healthy case: an already-ledgered stream source whose on-disk
    body has NOT grown past its watermark must not be flagged."""
    real_text = REAL_STREAM.read_text(encoding="utf-8")
    header, body = split_header(real_text)
    CUT = 126_457
    early_text = header + body[:CUT]

    wiki_root.add_source("chat.md", early_text)
    wr = WikiRoot(wiki_root.root)
    advance_watermark_if_stream(wr, "chat.md", "stream")
    _ledger_one_segment_accept(wr.ledger_path, "chat.md")

    from wiki_weaver.lib import ledgered_source_ids

    all_ids = select_source._list_source_ids(wr)
    ledgered = ledgered_source_ids(wr.ledger_path)
    assert select_source.find_stale_watermark_growth(wr, all_ids, ledgered) == []
    # And the resume gate behaves exactly as before: fully drained.
    assert select_source.compute_pending(wr, "") == []


def test_finds_growth_when_same_filename_grows_past_watermark(wiki_root):
    """THE reproduction: same filename, ledgered once at a small size, then
    overwritten in place with the full (much larger) real transcript --
    exactly what epoch-feed/E1..E7 did to the real affected corpus sources.
    Proves detection triggers on the REAL fixture, not a synthetic stand-in."""
    real_text = REAL_STREAM.read_text(encoding="utf-8")
    header, body = split_header(real_text)
    CUT = 126_457
    early_text = header + body[:CUT]

    wiki_root.add_source("chat.md", early_text)
    wr = WikiRoot(wiki_root.root)
    advance_watermark_if_stream(wr, "chat.md", "stream")
    _ledger_one_segment_accept(wr.ledger_path, "chat.md")

    identity = extract_stream_identity(early_text)
    store = load_watermarks(wr.watermarks_path)
    assert store[identity]["watermark_chars"] == CUT

    # The feed overwrites the SAME path with the full, larger real file --
    # never a new filename (the assumption select_source's ledger-by-
    # filename gate silently depends on).
    wiki_root.add_source("chat.md", real_text)

    from wiki_weaver.lib import ledgered_source_ids

    all_ids = select_source._list_source_ids(wr)
    ledgered = ledgered_source_ids(wr.ledger_path)
    findings = select_source.find_stale_watermark_growth(wr, all_ids, ledgered)

    assert findings == [("chat.md", identity, CUT, len(body))]

    # THE guard: compute_pending (and therefore the CLI) fails loud instead
    # of silently draining the rest of the run while this source falls
    # behind.
    with pytest.raises(select_source.StaleWatermarkGrowthError):
        select_source.compute_pending(wr, "")


def test_growth_guard_ignores_files_that_did_not_set_the_watermark(wiki_root):
    """REGRESSION: the watermark is per-IDENTITY but this guard's claim is
    per-FILE. Several files routinely share one chat identity (the intended
    usage feeds a conversation as overlapping windows over time). When a
    SHORTER window stamps the record last, every longer sibling of the same
    identity looked "grown" though nothing on disk had changed -- halting the
    whole run on healthy files.

    Growth is only meaningful against the record the file itself wrote.
    """
    header = "# Chat: T\n\nChat type: Group\nChat ID: 19:shared@thread.v2\nMessages: 3\n\n---\n\n"

    def day(d):
        return f"## 2026-05-{d:02d}\n\n[10:00] **A**\n> message {d}\n\n"

    long_text = header + "".join(day(d) for d in range(1, 9))
    short_text = header + day(1)

    wiki_root.add_source("A-long.md", long_text)
    wiki_root.add_source("B-short.md", short_text)
    wr = WikiRoot(wiki_root.root)

    # Both ledgered; the SHORT window stamped the shared identity last.
    _ledger_one_segment_accept(wr.ledger_path, "A-long.md")
    _ledger_one_segment_accept(wr.ledger_path, "B-short.md")
    advance_watermark_if_stream(wr, "B-short.md", "stream")

    identity = extract_stream_identity(short_text)
    store = load_watermarks(wr.watermarks_path)
    assert store[identity]["source_id"] == "B-short.md"

    from wiki_weaver.lib import ledgered_source_ids

    all_ids = select_source._list_source_ids(wr)
    ledgered = ledgered_source_ids(wr.ledger_path)

    # A-long.md is longer than the record, but it did not write the record.
    assert select_source.find_stale_watermark_growth(wr, all_ids, ledgered) == []
    assert select_source.compute_pending(wr, "") == []  # no spurious halt


def test_cli_prints_growth_detected_sentinel_and_writes_no_current_source(wiki_root, capsys):
    real_text = REAL_STREAM.read_text(encoding="utf-8")
    header, body = split_header(real_text)
    CUT = 126_457
    early_text = header + body[:CUT]

    wiki_root.add_source("chat.md", early_text)
    wr = WikiRoot(wiki_root.root)
    advance_watermark_if_stream(wr, "chat.md", "stream")
    _ledger_one_segment_accept(wr.ledger_path, "chat.md")
    wiki_root.add_source("chat.md", real_text)

    code = select_source.main(["--wiki-root", str(wiki_root.root), "--restrict", ""])
    out = capsys.readouterr()
    assert code == 0  # routes via last_line, not a process crash -- matches ledger_corrupt's convention
    assert out.out.strip().splitlines()[-1] == "growth_detected"
    assert "chat.md" in out.err
    assert not wr.current_source_file.is_file()  # never selected a source when failing loud


def test_growth_guard_ignores_non_stream_ledgered_sources(wiki_root):
    """A one-shot (meeting/article) ledgered source has no watermark entry
    at all -- the guard must not misfire for it even if it happens to carry
    a Call ID field (meeting transcripts do)."""
    meeting_text = (
        "# Transcript: Standup\n\nDuration: 0:10:00\nSpeakers: A, B\nCall ID: abc-123\n\n---\n\n"
        "[0:00:01] A: hi\n[0:00:02] B: hi\n"
    )
    wiki_root.add_source("meeting.md", meeting_text)
    wr = WikiRoot(wiki_root.root)
    _ledger_one_segment_accept(wr.ledger_path, "meeting.md")
    # No watermark store at all -- advance_watermark_if_stream never runs
    # for kind=meeting (see watermark.py), so the store stays empty.

    assert select_source.compute_pending(wr, "") == []  # no growth, no exception


def test_growth_guard_ignores_sources_with_no_stable_identity(wiki_root):
    """A ledgered source with no Chat ID / Call ID field at all (identity
    extraction fails) must be skipped, not raise."""
    text = "# Some chat\n\n---\n\n[09:00] **A**\n> hi\n"
    wiki_root.add_source("no-id.md", text)
    wr = WikiRoot(wiki_root.root)
    _ledger_one_segment_accept(wr.ledger_path, "no-id.md")

    assert select_source.compute_pending(wr, "") == []
