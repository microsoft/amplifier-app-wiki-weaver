"""wiki_weaver.ingest.watermark -- DESIGN.md §5's stream-growth fix.

The centerpiece test (test_real_truncated_stream_then_full_emits_only_delta)
constructs the exact real-world growth scenario against the real, largest
stream transcript in the target corpus: truncate it at a real day boundary
to simulate an earlier export, ingest it (watermark advances), then present
the FULL real file (as if re-downloaded with 19 more days of messages) and
prove only the delta -- not the whole ~236KB file -- gets emitted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wiki_weaver.ingest import detect_kind, segment_source
from wiki_weaver.ingest.watermark import (
    DEFAULT_OVERLAP_BYTES,
    DEFAULT_OVERLAP_TURNS,
    IdentityError,
    NEW_CONTENT_MARKER,
    OVERLAP_MARKER,
    advance_watermark_if_stream,
    compute_delta,
    extract_stream_identity,
    load_watermarks,
)
from wiki_weaver.lib import WikiRoot, atomic_append_jsonl, ensure_dir, split_header

FIXTURES = Path(__file__).parent / "fixtures"
REAL_STREAM = FIXTURES / "real-largest-stream-236kb.md"

SMALL_STREAM_TEXT = """# Chat: Test Channel

Chat type: Group
Chat ID: 19:test-channel-id@thread.v2
Downloaded: 2026-06-01
Lookback: 30 (since 2026-05-01)
Messages: 4

---

## 2026-05-01

[09:00] **Alice**
> Hello there.

[09:05] **Bob**
> Hi Alice, good to see you.
"""


def _select(wr: WikiRoot, source_id: str, kind: str = "stream") -> None:
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(source_id, encoding="utf-8")
    wr.current_kind_file.write_text(json.dumps({"kind": kind}), encoding="utf-8")


# --- extract_stream_identity ---


def test_identity_prefers_chat_id():
    identity = extract_stream_identity(SMALL_STREAM_TEXT)
    assert identity == "chat-id:19:test-channel-id@thread.v2"


def test_identity_falls_back_to_call_id():
    text = "# Meeting-ish chat\n\nCall ID: abc-123-def\n\n---\n\nsome body\n"
    assert extract_stream_identity(text) == "call-id:abc-123-def"


def test_identity_raises_when_no_stable_field_present():
    text = "# Some chat with no id fields\n\n---\n\n[09:00] **A**\n> hi\n"
    with pytest.raises(IdentityError):
        extract_stream_identity(text)


def test_identity_is_stable_across_the_three_real_header_fields_that_drift():
    """Verified against the real corpus: Downloaded:/Lookback:/Messages: all
    change on every re-export of the SAME Chat ID (3 real snapshots of
    19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2 differ on all three) --
    Chat ID itself must be, and is, the one field that doesn't."""
    export_1 = (
        "# Chat: Amplifier Agent Workstream\n\nChat type: Group\n"
        "Chat ID: 19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2\n"
        "Downloaded: 2026-06-03\nLookback: 30 (since 2026-05-04)\nMessages: 74\n\n---\n\nbody\n"
    )
    export_2 = (
        "# Chat: Amplifier as Agent Workstream\n\nChat type: Group\n"  # title differs too
        "Chat ID: 19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2\n"
        "Downloaded: 2026-06-20\nLookback: 30 (since 2026-05-21)\nMessages: 100\n\n---\n\nbody\n"
    )
    assert extract_stream_identity(export_1) == extract_stream_identity(export_2)


# --- compute_delta: first sight ---


def test_first_sight_of_identity_is_full_mode(wiki_root, capsys):
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    _select(wr, "chat1.md")

    result = compute_delta(wr, "chat1.md")
    assert result["mode"] == "full"
    assert result["watermark"] == 0
    assert result["overlap_bytes"] == 0
    assert result["new_bytes"] > 0

    content = wr.current_source_content_file.read_text(encoding="utf-8")
    assert NEW_CONTENT_MARKER in content
    assert OVERLAP_MARKER not in content
    assert "Alice" in content and "Bob" in content
    # No watermark persisted yet -- compute_delta never mutates the store
    # (ordering law: advance only happens once the pass is durably committed).
    assert load_watermarks(wr.watermarks_path) == {}


# --- compute_delta: no_delta ---


def test_no_delta_when_body_unchanged(wiki_root):
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    _select(wr, "chat1.md")

    identity = extract_stream_identity(SMALL_STREAM_TEXT)
    _, body = split_header(SMALL_STREAM_TEXT)
    from wiki_weaver.ingest.watermark import _fingerprint, save_watermarks

    save_watermarks(
        wr.watermarks_path,
        {
            identity: {
                "source_id": "chat1.md",
                "watermark_chars": len(body),
                "watermark_bytes": len(body.encode("utf-8")),
                "fingerprint": _fingerprint(body),
            }
        },
    )

    result = compute_delta(wr, "chat1.md")
    assert result["mode"] == "no_delta"
    assert result["new_bytes"] == 0
    assert not wr.current_source_content_file.is_file()  # never written for no_delta


# --- compute_delta: watermark_reset ---


def test_watermark_reset_when_prefix_diverges(wiki_root):
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    _select(wr, "chat1.md")

    identity = extract_stream_identity(SMALL_STREAM_TEXT)
    from wiki_weaver.ingest.watermark import _fingerprint, save_watermarks

    # A stored fingerprint that does NOT match this body's actual prefix --
    # simulates edited/truncated history rather than pure append.
    save_watermarks(
        wr.watermarks_path,
        {
            identity: {
                "source_id": "chat1.md",
                "watermark_chars": 10,
                "watermark_bytes": 10,
                "fingerprint": _fingerprint("SOMETHING ELSE ENTIRELY"),
            }
        },
    )

    result = compute_delta(wr, "chat1.md")
    assert result["mode"] == "watermark_reset"
    # Falls back to full ingest -- the WHOLE body, not a truncated delta.
    _, body = split_header(SMALL_STREAM_TEXT)
    assert result["new_bytes"] == len(body.encode("utf-8"))
    content = wr.current_source_content_file.read_text(encoding="utf-8")
    assert "Alice" in content and "Bob" in content


def test_watermark_reset_when_content_shrank(wiki_root):
    """A shorter body than the recorded watermark is unambiguous divergence
    (history truncated/edited) -- must not silently delta."""
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    _select(wr, "chat1.md")

    identity = extract_stream_identity(SMALL_STREAM_TEXT)
    from wiki_weaver.ingest.watermark import save_watermarks

    save_watermarks(
        wr.watermarks_path,
        {
            identity: {
                "source_id": "chat1.md",
                "watermark_chars": 999_999,
                "watermark_bytes": 999_999,
                "fingerprint": "irrelevant",
            }
        },
    )

    result = compute_delta(wr, "chat1.md")
    assert result["mode"] == "watermark_reset"


# --- compute_delta: identity_failed ---


def test_identity_failed_when_no_stable_field(wiki_root):
    text = "# Chat with no id\n\n---\n\n[09:00] **A**\n> hi\n[09:05] **B**\n> hi again\n[09:10] **A**\n> bye\n"
    wiki_root.add_source("chat1.md", text)
    wr = WikiRoot(wiki_root.root)
    _select(wr, "chat1.md")

    with pytest.raises(IdentityError):
        compute_delta(wr, "chat1.md")


def test_identity_failed_when_source_missing(wiki_root):
    wr = WikiRoot(wiki_root.root)
    _select(wr, "ghost.md")
    with pytest.raises(IdentityError):
        compute_delta(wr, "ghost.md")


# --- advance_watermark_if_stream ---


def test_advance_is_noop_for_non_stream_kind(wiki_root):
    wiki_root.add_source("a.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    advance_watermark_if_stream(wr, "a.md", "meeting")
    assert load_watermarks(wr.watermarks_path) == {}


def test_advance_persists_full_body_length_as_new_watermark(wiki_root):
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    wr = WikiRoot(wiki_root.root)
    advance_watermark_if_stream(wr, "chat1.md", "stream")

    identity = extract_stream_identity(SMALL_STREAM_TEXT)
    store = load_watermarks(wr.watermarks_path)
    _, body = split_header(SMALL_STREAM_TEXT)
    assert store[identity]["watermark_chars"] == len(body)
    assert store[identity]["watermark_bytes"] == len(body.encode("utf-8"))


# --- THE real-corpus growth test ---


def test_real_236kb_stream_transcript_truncate_then_full_emits_only_delta(wiki_root):
    """DONE MEANS: watermark proven on a real stream file with a real growth
    simulation. Cuts the real 236KB transcript at an ACTUAL day boundary
    (start of '## 2026-06-10', verified offset) to build an 'earlier' export,
    fully ingests it (advancing the watermark), then presents the complete
    real file under a DIFFERENT filename -- exactly how the real Teams
    export naming convention behaves -- and proves only the ~114KB delta
    beyond that day boundary is emitted, never the whole ~236KB file."""
    real_text = REAL_STREAM.read_text(encoding="utf-8")
    header, body = split_header(real_text)
    assert len(body.encode("utf-8")) > 230_000  # sanity: this really is the big one

    CUT = 126_457  # char offset: start of "## 2026-06-10" -- verified against the real file
    assert body[CUT : CUT + 13] == "## 2026-06-10"
    early_text = header + body[:CUT]

    wr = WikiRoot(wiki_root.root)
    wiki_root.add_source("chat-early.md", early_text)

    # --- Pass 1: first sight of this identity (early snapshot) ---
    _select(wr, "chat-early.md")
    result_1 = compute_delta(wr, "chat-early.md")
    assert result_1["mode"] == "full"
    assert result_1["new_bytes"] == len(body[:CUT].encode("utf-8"))

    # Simulate the pass fully completing (segment -> ledger -> the durable
    # checkpoint that advances the watermark): a real caller does this via
    # segment_source, exercised end-to-end in the next test; here we call
    # the advance step directly since compute_delta itself never persists.
    advance_watermark_if_stream(wr, "chat-early.md", "stream")

    identity = extract_stream_identity(early_text)
    store = load_watermarks(wr.watermarks_path)
    assert store[identity]["watermark_chars"] == CUT

    # --- Pass 2: the FULL real file arrives under a NEW filename (the real
    # Teams re-export naming pattern: the date range in the filename grows). ---
    wiki_root.add_source("chat-2026-05-21_to_2026-06-20-full.md", real_text)
    _select(wr, "chat-2026-05-21_to_2026-06-20-full.md")

    result_2 = compute_delta(wr, "chat-2026-05-21_to_2026-06-20-full.md")
    assert result_2["mode"] == "delta"
    assert result_2["watermark"] == len(body[:CUT].encode("utf-8"))

    expected_new_bytes = len(body[CUT:].encode("utf-8"))
    assert result_2["new_bytes"] == expected_new_bytes
    # THE proof: the delta is a small fraction of the whole file, not the
    # whole ~236KB file re-emitted.
    assert result_2["new_bytes"] < len(body.encode("utf-8")) * 0.5

    emitted = wr.current_source_content_file.read_text(encoding="utf-8")
    assert NEW_CONTENT_MARKER in emitted
    # The emitted content must NOT contain the early days (2026-05-21 etc.)
    # outside of the overlap window -- only recent context + new material.
    assert "## 2026-05-21" not in emitted
    # And it MUST contain the new day (06-10) and beyond.
    assert "## 2026-06-10" in emitted
    assert "## 2026-06-20" in emitted
    # Overlap window present and clearly marked, bounded (not the whole
    # early history re-included).
    assert OVERLAP_MARKER in emitted
    overlap_section = emitted.split(OVERLAP_MARKER, 1)[1].split(NEW_CONTENT_MARKER, 1)[0]
    assert len(overlap_section.encode("utf-8")) < DEFAULT_OVERLAP_BYTES + 4000  # a few turns, not the whole prefix


def test_real_236kb_stream_transcript_full_pipeline_delta_then_no_delta(wiki_root):
    """End-to-end through detect_kind + watermark + segment_source
    (content-override read) + select_segment + a simulated commit, proving:
    (1) the delta gets segmented (still large enough to need >1 segment),
    (2) once ledgered, the watermark advances via segment_source's own
    no_segment checkpoint, and (3) representing the SAME full content again
    correctly yields no_delta (nothing new -- skip, no further LLM work)."""
    real_text = REAL_STREAM.read_text(encoding="utf-8")
    header, body = split_header(real_text)
    CUT = 126_457
    early_text = header + body[:CUT]

    wr = WikiRoot(wiki_root.root)
    wiki_root.add_source("chat-early.md", early_text)

    # Pass 1: ingest the early snapshot end-to-end.
    _select(wr, "chat-early.md")
    detect_kind.main(["--wiki-root", str(wr.root)])
    assert json.loads(wr.current_kind_file.read_text(encoding="utf-8"))["kind"] == "stream"

    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 0
    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    total_1 = manifest["total"]
    assert total_1 >= 1

    # Ledger every segment of pass 1, then run --select once more so
    # segment_source's own no_segment branch fires and advances the watermark.
    for idx in range(1, total_1 + 1):
        atomic_append_jsonl(
            wr.ledger_path,
            {"source_id": "chat-early.md", "decision": "accept", "segment_index": idx, "segment_total": total_1},
        )
    out = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    assert out == 0

    identity = extract_stream_identity(early_text)
    store = load_watermarks(wr.watermarks_path)
    assert store[identity]["watermark_chars"] == CUT

    # Pass 2: the full real file, new filename -- detect_kind clears the
    # stale content-override file, watermark computes the delta, and
    # segment_source must segment THAT (not the whole file).
    wiki_root.add_source("chat-full.md", real_text)
    _select(wr, "chat-full.md")
    detect_kind.main(["--wiki-root", str(wr.root)])

    from wiki_weaver.ingest.watermark import compute_delta as _compute_delta

    result = _compute_delta(wr, "chat-full.md")
    assert result["mode"] == "delta"

    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 0
    manifest_2 = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    # The delta segments must total roughly the delta size, NOT the whole
    # 236KB file -- i.e. dramatically fewer bytes than re-segmenting the raw file.
    delta_total_bytes = sum(e["bytes"] for e in manifest_2["segments"])
    assert delta_total_bytes < len(real_text.encode("utf-8")) * 0.6

    total_2 = manifest_2["total"]
    for idx in range(1, total_2 + 1):
        atomic_append_jsonl(
            wr.ledger_path,
            {"source_id": "chat-full.md", "decision": "accept", "segment_index": idx, "segment_total": total_2},
        )
    out2 = segment_source.main(["--wiki-root", str(wr.root), "--select"])
    assert out2 == 0

    store_2 = load_watermarks(wr.watermarks_path)
    assert store_2[identity]["watermark_chars"] == len(body)

    # Pass 3: the SAME full content re-presented (e.g. a re-download with no
    # new messages yet) -- must be no_delta, no LLM call needed.
    wiki_root.add_source("chat-full-again.md", real_text)
    _select(wr, "chat-full-again.md")
    detect_kind.main(["--wiki-root", str(wr.root)])
    result_3 = _compute_delta(wr, "chat-full-again.md")
    assert result_3["mode"] == "no_delta"


# --- detect_kind's stale-file cleanup ---


def test_detect_kind_clears_stale_source_content_override(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_content_file.write_text("stale delta from a PRIOR stream source", encoding="utf-8")

    wiki_root.add_source("article.md", "# Just an article\n\nSome prose, no turns at all here.\n")
    wr.current_source_file.write_text("article.md", encoding="utf-8")

    detect_kind.main(["--wiki-root", str(wr.root)])
    assert not wr.current_source_content_file.is_file()


# --- segment_source prefers the content-override file when present ---


def test_segment_source_reads_content_override_not_raw_file(wiki_root):
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    _select(wr, "chat1.md")

    override_text = (
        "# Chat: Test Channel\n\n---\n\n[10:00] **Zoe**\n> This is the override content, not the raw file.\n"
    )
    ensure_dir(wr.ai_dir)
    wr.current_source_content_file.write_text(override_text, encoding="utf-8")

    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 0
    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    seg_path = wr.root / manifest["segments"][0]["path"]
    seg_text = seg_path.read_text(encoding="utf-8")
    assert "Zoe" in seg_text
    assert "Alice" not in seg_text  # raw file's content must NOT leak in


def test_segment_source_falls_back_to_raw_file_when_no_override(wiki_root):
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    _select(wr, "chat1.md")
    assert not wr.current_source_content_file.is_file()

    code = segment_source.main(["--wiki-root", str(wr.root)])
    assert code == 0
    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    seg_path = wr.root / manifest["segments"][0]["path"]
    assert "Alice" in seg_path.read_text(encoding="utf-8")


# --- CLI-level ---


def test_cli_prints_full_mode_json_on_first_sight(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    wiki_root.add_source("chat1.md", SMALL_STREAM_TEXT)
    _select(wr, "chat1.md")

    from wiki_weaver.ingest import watermark

    code = watermark.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload["mode"] == "full"


def test_cli_prints_identity_failed_json_and_exits_zero(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    text = "# No id chat\n\n---\n\n[09:00] **A**\n> hi\n[09:05] **B**\n> hi\n[09:10] **A**\n> bye\n"
    wiki_root.add_source("chat1.md", text)
    _select(wr, "chat1.md")

    from wiki_weaver.ingest import watermark

    code = watermark.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out.strip()
    assert code == 0
    payload = json.loads(out.splitlines()[-1])
    assert payload["mode"] == "identity_failed"


def test_cli_missing_current_source_fails(wiki_root, capsys):
    from wiki_weaver.ingest import watermark

    wr = WikiRoot(wiki_root.root)
    code = watermark.main(["--wiki-root", str(wr.root)])
    assert code == 1
    assert "select_source must run first" in capsys.readouterr().err


def test_default_overlap_turns_constant_matches_design_doc():
    # DESIGN.md §5: "stream = 10 turns ... of preceding context"
    assert DEFAULT_OVERLAP_TURNS == 10
