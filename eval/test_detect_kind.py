"""detect_kind: code-first article|meeting|stream|repo detection (DESIGN.md
§5). Never silently defaults to article on ambiguity -- that silent default
is the exact failure mode this feature exists to fix."""

from __future__ import annotations

import json
from pathlib import Path

from wiki_weaver.ingest import detect_kind
from wiki_weaver.lib import WikiRoot, ensure_dir

FIXTURES = Path(__file__).parent / "fixtures"

MEETING_TEXT = """# Transcript: Evals Sync

Source: https://example.com/recording
Duration: 1:08:47
Speakers: Priya Raghunathan, Tomas Lindqvist, Adaeze Nwosu
Date: 5/11/2026, 3:50:42 PM
Chat type: Meeting
Call ID: 0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0
Attendees: Adaeze Nwosu, Priya Raghunathan, Tomas Lindqvist

---

[0:00:04] Priya Raghunathan: thinking I will, I'll talk at you guys for a bit.

[0:00:07] Tomas Lindqvist: Yeah.

[0:00:10] Priya Raghunathan: but feel free to interrupt at any point.

[0:00:11] Tomas Lindqvist: Okay.
"""

# Real corpus finding: some transcripts have NO "Chat type:" line at all,
# but still carry Duration:/Speakers: and MM:SS-only (no hour) turns.
MEETING_TEXT_NO_CHAT_TYPE_MMSS = """# Transcript: 11 Hana & Milo-recording.mp4

Source: https://example.com/recording
Duration: 0:03:14
Speakers: Hana Sasaki, Milo Bergstrom

---

[00:03] Hana Sasaki: Chat and meetings on the project.

[00:14] Milo Bergstrom: Yeah. Yep, one of the tabs.

[00:20] Hana Sasaki: Right, exactly.
"""

# Real corpus finding: a continuously-downloaded Teams GROUP chat export --
# day-blocked, bold-speaker turns, no Duration:/Speakers: fields at all.
STREAM_TEXT = """# Chat: Amplifier Resolve Team

Chat type: Group
Chat ID: 19:1122334455667788990aabbccddeeff0@thread.v2
Downloaded: 2026-06-20
Lookback: 30 (since 2026-05-21)
Messages: 882

---

## 2026-05-21

[23:26] **Devon Achebe**
> proposed PRs to address the issues.

## 2026-05-22

[13:15] **Ingrid Halvorsen**
> Looking now.

[13:18] **Ingrid Halvorsen**
> Second message same day.
"""

# Real corpus finding: "Chat type: Meeting" ALSO appears in the Lookback:/
# Downloaded: header family -- a persistent meeting-CHAT thread (continuously
# re-downloaded), not a one-shot recording. Lookback:/Downloaded: must win.
PERSISTENT_MEETING_CHAT_TEXT = """# Chat: Amplifier team chat

Chat type: Meeting
Chat ID: 19:meeting_abc@thread.v2
Downloaded: 2026-06-08
Lookback: since-last-download (since 2026-06-08)
Messages: 0

---

_(no messages in this window)_
"""

ARTICLE_TEXT = """# Retrieval-Augmented Generation

RAG combines retrieval with generation to ground model outputs in real
documents. It reduces hallucination for knowledge-intensive tasks.

## History

Introduced in a 2020 paper, RAG has since become a standard pattern.
"""

# Genuinely ambiguous: ONE real turn-shaped line (below MIN_TURNS_FOR_CONFIDENCE,
# so rule 3 does not fire) but not zero either (so rule 4's "no turns at all"
# positive-article-signal does not fire) -- and no header corroboration.
AMBIGUOUS_TEXT = """# Notes

Some meandering notes taken during the day.

[10:15] Someone: said something once, in passing.

More prose after that -- no more turns like this one.
"""


def test_meeting_signature_duration_and_speakers():
    result = detect_kind.detect_from_signals(MEETING_TEXT)
    assert result["kind"] == "meeting"
    assert result["confidence"] == "high"
    assert "duration_field" in result["signals"]
    assert "speakers_field" in result["signals"]


def test_meeting_without_chat_type_line_and_mmss_turns():
    """Real corpus: some transcripts have no 'Chat type:' line at all."""
    result = detect_kind.detect_from_signals(MEETING_TEXT_NO_CHAT_TYPE_MMSS)
    assert result["kind"] == "meeting"
    assert result["confidence"] == "high"


def test_stream_signature_lookback_and_downloaded():
    result = detect_kind.detect_from_signals(STREAM_TEXT)
    assert result["kind"] == "stream"
    assert result["confidence"] == "high"
    assert "lookback_field" in result["signals"]
    assert "downloaded_field" in result["signals"]


def test_persistent_meeting_chat_thread_is_stream_not_meeting():
    """The overloaded 'Chat type: Meeting' value must NOT win over the
    structural Lookback:/Downloaded: signature -- this is a persistent,
    continuously-downloaded chat thread (append-only), not a one-shot
    recording, even though it reuses the word 'Meeting'."""
    result = detect_kind.detect_from_signals(PERSISTENT_MEETING_CHAT_TEXT)
    assert result["kind"] == "stream"
    assert result["confidence"] == "high"


def test_no_turn_markers_is_a_positive_article_signal_not_a_default():
    result = detect_kind.detect_from_signals(ARTICLE_TEXT)
    assert result["kind"] == "article"
    assert result["confidence"] == "high"
    assert "no_turn_markers_found" in result["signals"]


def test_ambiguous_text_returns_kind_unknown_never_silently_defaults():
    result = detect_kind.detect_from_signals(AMBIGUOUS_TEXT)
    assert result["kind"] == "kind_unknown"
    assert result["confidence"] == "low"


def test_explicit_kind_line_wins_over_everything_via_file(tmp_path):
    path = tmp_path / "s1.txt"
    path.write_text("kind: repo\n\n" + MEETING_TEXT, encoding="utf-8")
    result = detect_kind.detect_kind_for_source(path)
    assert result["kind"] == "repo"
    assert result["confidence"] == "high"
    assert result["signals"] == ["explicit_kind_marker"]


def test_explicit_invalid_kind_marker_falls_through_to_heuristics(tmp_path):
    path = tmp_path / "s1.txt"
    path.write_text("kind: podcast\n\n" + MEETING_TEXT, encoding="utf-8")
    result = detect_kind.detect_kind_for_source(path)
    assert result["kind"] == "meeting"  # heuristics still correctly resolve it
    assert any("explicit_kind_marker_invalid=podcast" in s for s in result["signals"])


# --- Real-corpus regression (delivery report: 110 real transcripts, 0
# kind_unknown, matches this exact split) ---


def test_real_largest_stream_file_detected_as_stream():
    """The task's framing called this a '236KB meeting transcript'; the real
    corpus file at this size is actually a Teams GROUP chat export (kind=
    stream), not a one-shot meeting recording -- see delivery report."""
    path = FIXTURES / "real-largest-stream-236kb.md"
    result = detect_kind.detect_kind_for_source(path)
    assert result["kind"] == "stream"
    assert result["confidence"] == "high"


def test_real_largest_meeting_file_detected_as_meeting():
    path = FIXTURES / "real-largest-meeting-201kb.md"
    result = detect_kind.detect_kind_for_source(path)
    assert result["kind"] == "meeting"
    assert result["confidence"] == "high"


# --- CLI-level tests ---


def test_main_writes_current_kind_file_and_stdout_json(wiki_root, capsys):
    wiki_root.add_source("s1.txt", MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")

    code = detect_kind.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()

    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload == {"kind": "meeting"}

    on_disk = json.loads(wr.current_kind_file.read_text(encoding="utf-8"))
    assert on_disk["kind"] == "meeting"
    assert on_disk["confidence"] == "high"
    assert on_disk["bytes"] == len(MEETING_TEXT.encode("utf-8"))


def test_missing_current_source_fails_with_message(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = detect_kind.main(["--wiki-root", str(wr.root)])
    assert code == 1
    assert "select_source must run first" in capsys.readouterr().err


def test_stdout_has_only_json_line(wiki_root, capsys):
    wiki_root.add_source("s1.txt", MEETING_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    detect_kind.main(["--wiki-root", str(wr.root)])
    captured = capsys.readouterr()
    assert len(captured.out.strip().splitlines()) == 1
    json.loads(captured.out.strip())  # must parse as exactly one JSON object


# --- --from-verdict (AP-2 gate) ---


def test_from_verdict_valid_kind_resolves_and_persists(wiki_root, capsys):
    wiki_root.add_source("s1.txt", AMBIGUOUS_TEXT)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("s1.txt", encoding="utf-8")
    wr.llm_kind_verdict_file.write_text("meeting\n", encoding="utf-8")

    code = detect_kind.main(["--wiki-root", str(wr.root), "--from-verdict", str(wr.llm_kind_verdict_file)])
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert json.loads(out.splitlines()[-1]) == {"kind": "meeting"}
    on_disk = json.loads(wr.current_kind_file.read_text(encoding="utf-8"))
    assert on_disk["kind"] == "meeting"
    assert on_disk["signals"] == ["llm_classified"]


def test_from_verdict_freeform_text_is_kind_invalid_not_a_guess(wiki_root, capsys):
    """AP-2 (DESIGN.md §12.1): a free-form LLM answer must never silently
    fall through any edge -- it must be caught here, deterministically."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.llm_kind_verdict_file.write_text("Sure, I think this is a meeting!\n", encoding="utf-8")

    code = detect_kind.main(["--wiki-root", str(wr.root), "--from-verdict", str(wr.llm_kind_verdict_file)])
    out = capsys.readouterr().out.strip()

    assert code == 0
    assert json.loads(out.splitlines()[-1]) == {"kind": "kind_invalid"}


def test_from_verdict_missing_file_is_kind_invalid(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    code = detect_kind.main(["--wiki-root", str(wr.root), "--from-verdict", str(wr.ai_dir / "nope.txt")])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert json.loads(out.splitlines()[-1]) == {"kind": "kind_invalid"}
