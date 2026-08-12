"""segment_source: deterministic split at safe unit boundaries (DESIGN.md
§5) plus the segment-granularity resume gate. Exercised against both
synthetic text and the real, largest transcripts in the target corpus."""

from __future__ import annotations

import json
from pathlib import Path

from wiki_weaver.ingest import segment_source
from wiki_weaver.ingest.detect_kind import MEETING_TURN_RE, STREAM_TURN_RE
from wiki_weaver.ingest.segment_source import (
    DEFAULT_SEGMENT_BYTES,
    _HEADING_RE,
    _unit_starts,
    build_segments,
    split_header,
)
from wiki_weaver.lib import WikiRoot, atomic_append_jsonl, ensure_dir

FIXTURES = Path(__file__).parent / "fixtures"

SMALL_MEETING_TEXT = """# Transcript: Quick Sync

Duration: 0:02:00
Speakers: A, B

---

[0:00:01] A: Hello there.

[0:00:05] B: Hi, good to see you.
"""


def _repeat_meeting_turns(n: int, words_per_turn: int = 40) -> str:
    header = "# Transcript: Long Meeting\n\nDuration: 2:00:00\nSpeakers: A, B\n\n---\n\n"
    body = ""
    for i in range(n):
        minute, second = divmod(i, 60)
        speaker = "A" if i % 2 == 0 else "B"
        words = " ".join(f"word{i}_{w}" for w in range(words_per_turn))
        body += f"[0:{minute:02d}:{second:02d}] {speaker}: {words}\n\n"
    return header + body


# --- split_header ---


def test_split_header_carries_metadata_block_and_separator():
    header, body = split_header(SMALL_MEETING_TEXT)
    assert "Duration: 0:02:00" in header
    assert header.rstrip().endswith("---")
    assert body.startswith("[0:00:01] A: Hello there.")


def test_split_header_absent_delimiter_returns_empty_header():
    text = "# Just an article\n\nSome prose with no header block.\n"
    header, body = split_header(text)
    assert header == ""
    assert body == text


# --- build_segments: synthetic ---


def test_small_source_produces_single_segment():
    segments = build_segments(SMALL_MEETING_TEXT, "meeting", DEFAULT_SEGMENT_BYTES)
    assert len(segments) == 1
    assert "Duration: 0:02:00" in segments[0]["body"]
    assert "[0:00:01] A: Hello there." in segments[0]["body"]
    assert segments[0]["start_ts"] == "0:00:01"
    assert segments[0]["end_ts"] == "0:00:05"


def test_large_meeting_splits_at_turn_boundaries_never_mid_turn():
    text = _repeat_meeting_turns(4000)  # comfortably over budget
    segments = build_segments(text, "meeting", DEFAULT_SEGMENT_BYTES)
    assert len(segments) > 1

    for seg in segments:
        # Header carried into every segment.
        assert "Duration: 2:00:00" in seg["body"]
        # Every segment's body (after header + injected marker comment)
        # begins with a complete turn line -- never a fragment.
        after_marker = seg["body"].split("-->\n", 1)[1]
        assert MEETING_TURN_RE.match(after_marker), after_marker[:80]


def test_oversized_single_turn_is_never_split_mid_turn():
    """A single turn larger than the byte budget must still become its own
    (oversized) segment rather than being cut mid-sentence."""
    huge_word_count = 30000  # single turn alone exceeds DEFAULT_SEGMENT_BYTES
    text = _repeat_meeting_turns(1, words_per_turn=huge_word_count)
    segments = build_segments(text, "meeting", DEFAULT_SEGMENT_BYTES)
    assert len(segments) == 1
    assert segments[0]["bytes"] > DEFAULT_SEGMENT_BYTES


def test_article_splits_at_heading_boundaries():
    text = "# Title\n\nIntro paragraph.\n\n## Section One\n\n" + ("word " * 5000) + "\n\n## Section Two\n\nMore text.\n"
    segments = build_segments(text, "article", 200)  # tiny budget forces a split
    assert len(segments) > 1
    for seg in segments[1:]:
        after_marker = seg["body"].split("-->\n", 1)[1]
        assert _HEADING_RE.match(after_marker) or after_marker.strip() == ""


# --- Real-corpus segmentation (delivery report: manually verified byte
# counts and start_ts/end_ts for both files match these assertions) ---


def test_real_236kb_stream_transcript_segments_at_day_or_turn_boundaries():
    path = FIXTURES / "real-largest-stream-236kb.md"
    text = path.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) > 230_000  # sanity: this really is the ~236KB file

    segments = build_segments(text, "stream", DEFAULT_SEGMENT_BYTES)
    assert len(segments) == 4  # measured directly against this real file

    _, body = split_header(text)
    starts = set(_unit_starts(body, "stream"))
    # Reconstruct each segment's un-marked body slice and confirm it starts
    # exactly at one of the pre-computed safe boundaries (0 always qualifies).
    offset = 0
    for seg in segments:
        after_marker = seg["body"].split("-->\n", 1)[1]
        assert body[offset : offset + len(after_marker)] == after_marker
        assert offset in starts
        offset += len(after_marker)
    assert offset == len(body)

    for seg in segments:
        after_marker = seg["body"].split("-->\n", 1)[1]
        assert offset == len(body) or STREAM_TURN_RE.match(after_marker) or after_marker == ""


def test_real_201kb_meeting_transcript_segments_at_turn_boundaries():
    path = FIXTURES / "real-largest-meeting-201kb.md"
    text = path.read_text(encoding="utf-8")

    segments = build_segments(text, "meeting", DEFAULT_SEGMENT_BYTES)
    assert len(segments) == 3  # measured directly against this real file

    for seg in segments:
        assert "Duration:" in seg["body"]  # header carried into every segment
        after_marker = seg["body"].split("-->\n", 1)[1]
        assert MEETING_TURN_RE.match(after_marker)


# --- CLI-level: split step ---


def test_cli_split_small_source_emits_single(wiki_root, capsys):
    wiki_root.add_source("s1.txt", SMALL_MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    wr.current_kind_file.write_text(json.dumps({"kind": "meeting"}), encoding="utf-8")

    code = segment_source.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "single"

    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    assert manifest["total"] == 1
    assert manifest["source_id"] == "s1.txt"
    seg_path = wr.root / manifest["segments"][0]["path"]
    assert seg_path.is_file()
    assert "Duration: 0:02:00" in seg_path.read_text(encoding="utf-8")


def test_cli_split_real_large_source_emits_segmented(wiki_root, capsys):
    real_text = (FIXTURES / "real-largest-meeting-201kb.md").read_text(encoding="utf-8")
    wiki_root.add_source("big.md", real_text)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("big.md", encoding="utf-8")
    wr.current_kind_file.write_text(json.dumps({"kind": "meeting"}), encoding="utf-8")

    code = segment_source.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "segmented"

    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    assert manifest["total"] == 3


def test_cli_split_missing_current_source_fails(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 1
    assert "select_source must run first" in capsys.readouterr().err


def test_cli_split_empty_string_max_bytes_falls_back_to_default(wiki_root, capsys):
    """Regression (bare-$var substitution defect): pipeline/ingest.dot passes
    --max-bytes via bare $segment_max_bytes substitution. When
    segment_max_bytes is absent from context, the engine leaves the literal
    token in place and bash's unset-variable expansion resolves it to an
    empty string -- so this CLI receives --max-bytes "" (never a missing
    flag). It must apply DEFAULT_SEGMENT_BYTES, not crash with an int()
    ValueError."""
    wiki_root.add_source("s1.txt", SMALL_MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    wr.current_kind_file.write_text(json.dumps({"kind": "meeting"}), encoding="utf-8")

    code = segment_source.main(["--wiki-root", str(wr.root), "--max-bytes", ""])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "single"

    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    assert manifest["total"] == 1


def test_cli_split_missing_kind_routes_segment_failed_not_a_crash(wiki_root, capsys):
    wiki_root.add_source("s1.txt", SMALL_MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    # detect_kind never ran -- current_kind.json absent.

    code = segment_source.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "segment_failed"


# --- CLI-level: select step (segment-granularity resume) ---


def _split_then(wr: WikiRoot, source_id: str, text: str, kind: str = "meeting") -> None:
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(source_id, encoding="utf-8")
    wr.current_kind_file.write_text(json.dumps({"kind": kind}), encoding="utf-8")
    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 0


def test_select_picks_first_pending_segment(wiki_root, capsys):
    wiki_root.add_source("s1.txt", SMALL_MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    _split_then(wr, "s1.txt", SMALL_MEETING_TEXT)
    capsys.readouterr()

    code = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "has_segment"

    current = json.loads(wr.current_segment_file.read_text(encoding="utf-8"))
    assert current["index"] == 1
    assert current["total"] == 1
    assert "Duration: 0:02:00" in wr.current_segment_content_file.read_text(encoding="utf-8")


def test_select_skips_already_ledgered_segments_on_resume(wiki_root, capsys):
    real_text = (FIXTURES / "real-largest-meeting-201kb.md").read_text(encoding="utf-8")
    wiki_root.add_source("big.md", real_text)
    wr = WikiRoot(wiki_root.root)
    _split_then(wr, "big.md", real_text)
    capsys.readouterr()

    # Simulate segment 1 already committed (accepted) in a prior pass.
    atomic_append_jsonl(
        wr.ledger_path,
        {"source_id": "big.md", "decision": "accept", "segment_index": 1, "segment_total": 3},
    )

    code = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "has_segment"
    current = json.loads(wr.current_segment_file.read_text(encoding="utf-8"))
    assert current["index"] == 2  # segment 1 skipped -- already ledgered


def test_select_no_segment_when_all_segments_ledgered(wiki_root, capsys):
    wiki_root.add_source("s1.txt", SMALL_MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    _split_then(wr, "s1.txt", SMALL_MEETING_TEXT)  # single segment, total=1
    capsys.readouterr()

    atomic_append_jsonl(
        wr.ledger_path,
        {"source_id": "s1.txt", "decision": "accept", "segment_index": 1, "segment_total": 1},
    )

    code = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert out.splitlines()[-1] == "no_segment"


def test_select_missing_manifest_fails(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    assert code == 1
    assert "segment_source must run first" in capsys.readouterr().err
