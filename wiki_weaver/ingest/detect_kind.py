"""wiki_weaver.ingest.detect_kind -- closes DESIGN.md §5's gap: ``kind`` used
to be a label with no behavior behind it. This is the behavior.

Code decides whenever it genuinely can (LANGUAGE_PHILOSOPHY §1, and this
project's own measured A/B/C eval: deterministic routing beat an LLM planner
on every metric -- 10 pages vs 5, 41 links vs 13, 113s/page vs 372s/page).
Only when the signals are truly ambiguous does this print ``kind_unknown``,
routing to a cheap ``classify_kind`` LLM box (see pipeline/ingest.dot) --
and it NEVER silently defaults to ``article`` on ambiguity. That silent
default is the exact failure mode DESIGN.md §5 exists to fix: a 236KB
source mis-detected as an ordinary one-shot article would never get
segmented and would be ingested as one giant context.

Signals used, in priority order (see delivery report for the real-corpus
investigation that produced this order):

  0. Explicit ``kind:`` marker (leading line or frontmatter) -- read_explicit_kind().
  1. ``Lookback:``/``Downloaded:`` header fields -- the structural signature
     of a continuously-exported chat/channel (DESIGN.md §5's ``stream``:
     "append-only (chat, channel)"). Takes priority over the (overloaded)
     ``Chat type:`` value: a "Chat type: Meeting" thread in THIS header
     family is a persistent meeting-chat thread that is downloaded
     repeatedly with a lookback window -- an append-only stream, not a
     one-shot recording -- even though it reuses the word "Meeting".
  2. ``Duration:`` + ``Speakers:`` header fields together -- the one-shot
     recording signature (DESIGN.md §5's ``meeting``).
  3. Turn-format density in the body, when the header alone is incomplete
     (observed in the real corpus: some transcripts have no ``Chat type:``
     line at all). Two DISTINCT turn shapes are recognized structurally
     (never by digit-count alone, which overlaps):
       meeting turn:  ``[H:MM:SS] Name: text``  or  ``[MM:SS] Name: text``
       stream  turn:  ``[HH:MM] **Name**`` (bold speaker, day-blocked)
  4. Zero turn markers of either shape found at all -- a POSITIVE signal for
     ``article`` (one-shot prose), not a default: DESIGN.md §5 defines
     article as "one-shot, immutable", and the absence of any dialogue
     structure is itself evidence for that, not an absence of evidence.
  5. Anything else -- ``kind_unknown``. Confidence is always ``low`` here;
     every other branch above is ``high`` (or explicitly demoted for a
     turn-density-only match with no header corroboration -- see MIN_TURNS).

Usage:
    python3 -m wiki_weaver.ingest.detect_kind --wiki-root <path>
    python3 -m wiki_weaver.ingest.detect_kind --wiki-root <path> --from-verdict <path>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from wiki_weaver.lib import (
    VALID_KINDS,
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    read_current_source_id,
    read_explicit_kind,
)

# Header-field signals. Only the header block matters (well within the
# first 2KB in every real sample seen), but matching over the whole text is
# just as correct and cheap (DESIGN.md §14: byte-level ops are 3-5 orders of
# magnitude below one LLM pass even at hundreds of KB) -- no reason to add a
# read-window special case.
_DURATION_RE = re.compile(r"^Duration:\s*\S", re.MULTILINE)
_SPEAKERS_FIELD_RE = re.compile(r"^Speakers:\s*\S", re.MULTILINE)
_LOOKBACK_RE = re.compile(r"^Lookback:\s*\S", re.MULTILINE)
_DOWNLOADED_RE = re.compile(r"^Downloaded:\s*\S", re.MULTILINE)
_CHAT_TYPE_RE = re.compile(r"^Chat type:\s*(.+)$", re.MULTILINE)

# Turn-shape signals -- structurally distinct (by trailing token shape, not
# digit count, which overlaps: "[23:26]" alone matches both a 1-2-group
# H:MM/MM:SS pattern):
#   meeting: "[0:00:04] Priya Raghunathan: text" or "[00:03] Hana Sasaki: text"
#   stream:  "[23:26] **Devon Achebe**" (bold speaker, Teams group-chat export)
MEETING_TURN_RE = re.compile(r"^\[\d{1,2}(?::\d{2}){1,2}\]\s+[^\[\*\n]+?:\s", re.MULTILINE)
STREAM_TURN_RE = re.compile(r"^\[\d{2}:\d{2}\]\s+\*\*[^*\n]+\*\*", re.MULTILINE)
DAY_HEADER_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}", re.MULTILINE)

# Below this many turn-shaped lines, body structure alone is not enough to
# be confident -- could be a stray bracketed timestamp in prose. Chosen
# generously low (real transcripts have hundreds of turns; false positives
# from prose are essentially always 0-2 incidental matches).
MIN_TURNS_FOR_CONFIDENCE = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.detect_kind")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--from-verdict",
        default=None,
        help="path to classify_kind's one-word LLM verdict file -- validates it against "
        "the closed vocabulary instead of running signal detection (AP-2: never route on "
        "raw LLM token output)",
    )
    return parser


def detect_from_signals(text: str) -> dict:
    """Pure function: source text in, detection result out. No I/O, so it is
    directly unit-testable against real transcript content."""
    signals: list[str] = []

    duration = bool(_DURATION_RE.search(text))
    speakers_field = bool(_SPEAKERS_FIELD_RE.search(text))
    lookback = bool(_LOOKBACK_RE.search(text))
    downloaded = bool(_DOWNLOADED_RE.search(text))
    chat_type_m = _CHAT_TYPE_RE.search(text)
    chat_type = chat_type_m.group(1).strip() if chat_type_m else None

    meeting_turns = len(MEETING_TURN_RE.findall(text))
    stream_turns = len(STREAM_TURN_RE.findall(text))
    day_headers = len(DAY_HEADER_RE.findall(text))

    if duration:
        signals.append("duration_field")
    if speakers_field:
        signals.append("speakers_field")
    if lookback:
        signals.append("lookback_field")
    if downloaded:
        signals.append("downloaded_field")
    if chat_type:
        signals.append(f"chat_type={chat_type}")
    if meeting_turns:
        signals.append(f"meeting_turn_lines={meeting_turns}")
    if stream_turns:
        signals.append(f"stream_turn_lines={stream_turns}")
    if day_headers:
        signals.append(f"day_header_lines={day_headers}")

    # 1. Continuously-exported channel signature outranks the (overloaded)
    # "Chat type:" value -- see module docstring, signal 1.
    if lookback or downloaded:
        return {"kind": "stream", "confidence": "high", "signals": signals, "turns": meeting_turns + stream_turns}

    # 2. One-shot recording signature.
    if duration and speakers_field:
        return {"kind": "meeting", "confidence": "high", "signals": signals, "turns": meeting_turns}

    # 3. Turn-format density fallback, when the header alone is incomplete.
    if stream_turns >= MIN_TURNS_FOR_CONFIDENCE and stream_turns >= meeting_turns:
        confidence = "high" if day_headers else "low"
        return {"kind": "stream", "confidence": confidence, "signals": signals, "turns": stream_turns}
    if meeting_turns >= MIN_TURNS_FOR_CONFIDENCE:
        confidence = "high" if (duration or speakers_field) else "low"
        return {"kind": "meeting", "confidence": confidence, "signals": signals, "turns": meeting_turns}

    # 4. No dialogue-turn markers at all -- a POSITIVE signal for "article",
    # not a default. See module docstring, signal 4.
    if meeting_turns == 0 and stream_turns == 0:
        signals.append("no_turn_markers_found")
        return {"kind": "article", "confidence": "high", "signals": signals, "turns": 0}

    # 5. Genuinely ambiguous -- do not guess.
    signals.append("ambiguous_turn_density")
    return {"kind": "kind_unknown", "confidence": "low", "signals": signals, "turns": meeting_turns + stream_turns}


def detect_kind_for_source(source_path: Path) -> dict:
    """Full detection for one source file: explicit marker first, then
    signal-based heuristics. Returns the same shape emitted on stdout."""
    explicit = read_explicit_kind(source_path)
    if explicit in VALID_KINDS:
        return {"kind": explicit, "confidence": "high", "signals": ["explicit_kind_marker"], "turns": None}

    text = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
    result = detect_from_signals(text)
    if explicit is not None:
        # Declared but not one of the closed vocabulary -- don't silently
        # coerce it or silently ignore it; record the discrepancy and fall
        # through to the heuristic result computed above.
        result["signals"] = [f"explicit_kind_marker_invalid={explicit}", *result["signals"]]
    return result


def _write_current_kind(wr: WikiRoot, payload: dict) -> None:
    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.current_kind_file, json.dumps(payload, indent=2) + "\n")


def _clear_stale_source_content_override(wr: WikiRoot) -> None:
    """DESIGN.md §5 stream watermarking: ``watermark`` writes
    ``current_source_content_file`` only for a ``kind=stream`` source (the
    bounded delta/full/reset content ``segment_source`` must read INSTEAD OF
    the raw file). ``detect_kind`` runs first, unconditionally, for EVERY
    freshly (re)selected source -- exactly like it already does for
    ``current_kind_file`` (see this module's own docstring: "written fresh
    ... every time a source is (re)selected") -- so this is the natural,
    single place to delete any copy left by a PRIOR stream source. Without
    this, a non-stream source selected right after a stream source would
    silently have ``segment_source`` read the OLD stream's delta instead of
    its own raw file.
    """
    stale = wr.current_source_content_file
    if stale.is_file():
        stale.unlink()


def _run_detect(wr: WikiRoot) -> int:
    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _clear_stale_source_content_override(wr)

    source_path = wr.sources_dir / source_id
    result = detect_kind_for_source(source_path)
    payload = {
        "kind": result["kind"],
        "confidence": result["confidence"],
        "signals": result["signals"],
        "bytes": source_path.stat().st_size if source_path.is_file() else 0,
        "turns": result["turns"],
    }
    _write_current_kind(wr, payload)

    print(
        f"detected kind={payload['kind']} confidence={payload['confidence']} "
        f"for {source_id} ({payload['bytes']} bytes, signals={payload['signals']})",
        file=sys.stderr,
    )
    print(json.dumps({"kind": payload["kind"]}))
    return 0


def _run_from_verdict(wr: WikiRoot, verdict_path: Path) -> int:
    """AP-2 gate (DESIGN.md §12.1): classify_kind (the LLM box) never routes
    the graph directly -- this reads what it wrote and validates it against
    the closed vocabulary. A missing file or anything outside
    {article, meeting, stream, repo} routes to ``kind_invalid`` (-> exit in
    the graph) rather than silently coercing to a guess."""
    if not verdict_path.is_file():
        print(f"{verdict_path} does not exist -- classify_kind must run first", file=sys.stderr)
        print(json.dumps({"kind": "kind_invalid"}))
        return 0

    raw = verdict_path.read_text(encoding="utf-8").strip().lower()
    if raw not in VALID_KINDS:
        print(f"{verdict_path} contains {raw!r}, not one of {VALID_KINDS} -- refusing to guess", file=sys.stderr)
        print(json.dumps({"kind": "kind_invalid"}))
        return 0

    payload = {
        "kind": raw,
        "confidence": "high",
        "signals": ["llm_classified"],
        "bytes": None,
        "turns": None,
    }
    try:
        source_id = read_current_source_id(wr)
        source_path = wr.sources_dir / source_id
        payload["bytes"] = source_path.stat().st_size if source_path.is_file() else 0
    except FileNotFoundError:
        pass  # current_kind.json is still useful without the byte count

    _write_current_kind(wr, payload)
    print(f"classify_kind resolved kind_unknown -> {raw}", file=sys.stderr)
    print(json.dumps({"kind": raw}))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    if args.from_verdict:
        return _run_from_verdict(wr, Path(args.from_verdict))
    return _run_detect(wr)


if __name__ == "__main__":
    raise SystemExit(main())
