"""wiki_weaver.ingest.segment_source -- deterministic segmentation for
oversized sources (DESIGN.md §5: "SEGMENT deterministically; each segment is
a unit of work").

Splits ONLY at safe unit boundaries for the source's detected ``kind`` --
NEVER mid-turn, mid-day, or mid-paragraph -- and carries the header block
into every segment so each is self-describing on its own. A source under
budget still goes through this exact code path and produces exactly one
segment (DESIGN.md §5 / the task's explicit instruction: "one uniform code
path, no special case").

Two modes, mirroring select_source's split of "compute pending" from
"choose + write breadcrumb", but at segment granularity:

  python3 -m wiki_weaver.ingest.segment_source --wiki-root <path> [--max-bytes N]
      THE split step. Deterministically re-derived every time this source is
      (re)selected -- cheap (DESIGN.md §14) and avoids trusting any stale
      on-disk manifest across a crash/resume. Writes ``.ai/segments.json``
      and the segment files under ``.ai/segments/<source>/``.
      stdout: segmented (>1) | single (1) | segment_failed

  python3 -m wiki_weaver.ingest.segment_source --wiki-root <path> --select
      THE segment-level resume gate: pending = {1..total} MINUS ledgered
      segment indices for this source, recomputed fresh from ledger.jsonl on
      every pass (identical resume doctrine to select_source, one level
      down). Writes ``.ai/current_segment.json`` and
      ``.ai/current-segment-content.md`` (the ONE file retrieve_slice/weave
      read, whether this source was segmented or not).
      stdout: has_segment | no_segment
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from wiki_weaver.ingest.detect_kind import MEETING_TURN_RE, STREAM_TURN_RE
from wiki_weaver.ingest.watermark import advance_watermark_if_stream
from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    int_or_default,
    ledgered_segment_indices,
    read_current_kind,
    read_current_source_id,
    split_header,
)

# THE byte budget. Justified against the real corpus this feature targets
# (110 Teams transcripts, ~/dev/future-brainstorm/archive/transcripts/,
# measured directly -- see delivery report): overall mean 65.9KB, median
# 49KB, max 241.5KB (~236KB). 80,000 bytes is chosen so that:
#   - the common case (mean/median well under budget) stays a SINGLE
#     segment -- no multi-cycle overhead for an ordinary-length meeting,
#     matching kind=article's "ingest whole" simplicity for the typical case.
#   - the measured worst case (241.5KB) splits into ceil(241500/80000) = 4
#     bounded segments rather than one giant context.
#   - 80,000 bytes is roughly 20K tokens (English text averages ~4
#     bytes/token) -- a conservative fraction of any current LLM context
#     window once the retrieve_slice wiki-page slice and prompt overhead are
#     added alongside it.
DEFAULT_SEGMENT_BYTES = 80_000

# Mirrors MEETING_TURN_RE/STREAM_TURN_RE's shapes (detect_kind.py) with a
# capturing group, purely for start_ts/end_ts reporting -- kept separate
# from the boundary regexes themselves so "how we split" and "what we
# report" can vary independently.
_MEETING_TS_RE = re.compile(r"^\[([^\]]+)\]\s+[^\[\*\n]+?:\s", re.MULTILINE)
_STREAM_TS_RE = re.compile(r"^\[([^\]]+)\]\s+\*\*[^*\n]+\*\*", re.MULTILINE)
_DAY_HEADER_RE = re.compile(r"^## \d{4}-\d{2}-\d{2}", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s", re.MULTILINE)
_PARAGRAPH_BREAK_RE = re.compile(r"\n\s*\n")

_UNSAFE_FILENAME_RE = re.compile(r'[<>:"|?*\\/]')


def _sanitize_id(source_id: str) -> str:
    """Filesystem-safe directory name for a source id (IMPLEMENTATION_PHILOSOPHY:
    sanitize Windows-prohibited characters in any user/external-data-derived
    filename component)."""
    return _UNSAFE_FILENAME_RE.sub("-", source_id).strip("-") or "source"


def _unit_starts(body: str, kind: str) -> list[int]:
    """Character offsets where each safe-to-split-before unit begins.
    Offset 0 is always included. Splitting is only ever allowed at one of
    these offsets -- never mid-turn, mid-day, or mid-paragraph."""
    if kind == "meeting":
        starts = [m.start() for m in MEETING_TURN_RE.finditer(body)]
    elif kind == "stream":
        # Day-header boundaries are the PRIMARY safe boundary for a chat/
        # channel export -- keeps each segment to whole calendar days.
        # Falls back to per-turn boundaries only when the export has no (or
        # only one) day-header, e.g. a single-day export.
        starts = [m.start() for m in _DAY_HEADER_RE.finditer(body)]
        if len(starts) <= 1:
            starts = [m.start() for m in STREAM_TURN_RE.finditer(body)]
    else:
        # article, repo, or anything else with no turn/day concept: prefer
        # markdown heading boundaries; fall back to paragraph breaks so a
        # heading-less document still never splits mid-paragraph.
        starts = [m.start() for m in _HEADING_RE.finditer(body)]
        if len(starts) <= 1:
            starts = [m.end() for m in _PARAGRAPH_BREAK_RE.finditer(body)]

    if not starts or starts[0] != 0:
        starts = [0, *starts]
    return sorted(set(s for s in starts if 0 <= s <= len(body)))


def _pack_segments(body: str, starts: list[int], max_bytes: int) -> list[tuple[int, int]]:
    """Greedily fill segments up to ``max_bytes``, cutting ONLY at a unit
    boundary. A single unit that alone exceeds ``max_bytes`` still becomes
    its own (oversized) segment rather than being split mid-unit -- the
    "never mid-turn" safety guarantee is a harder constraint than the byte
    budget, which is a target, not a hard cap."""
    if len(body) == 0:
        return [(0, 0)]
    bounds = [*starts, len(body)]
    segments: list[tuple[int, int]] = []
    seg_start = bounds[0]
    for i in range(len(bounds) - 1):
        unit_start, unit_end = bounds[i], bounds[i + 1]
        candidate_bytes = len(body[seg_start:unit_end].encode("utf-8"))
        if candidate_bytes > max_bytes and unit_start > seg_start:
            segments.append((seg_start, unit_start))
            seg_start = unit_start
    segments.append((seg_start, len(body)))
    return segments


def _extract_ts(segment_body: str, kind: str) -> tuple[str | None, str | None]:
    if kind == "meeting":
        matches = list(_MEETING_TS_RE.finditer(segment_body))
    elif kind == "stream":
        matches = list(_STREAM_TS_RE.finditer(segment_body))
    else:
        return None, None
    if not matches:
        return None, None
    return matches[0].group(1), matches[-1].group(1)


def build_segments(text: str, kind: str, max_bytes: int) -> list[dict]:
    """Pure function: source text + kind in, list of segment dicts out
    (``{"body": full segment text incl. header, "bytes", "start_ts", "end_ts"}``).
    No I/O -- directly unit-testable against real transcript content."""
    header, body = split_header(text)
    starts = _unit_starts(body, kind)
    ranges = _pack_segments(body, starts, max_bytes)
    total = len(ranges)

    segments: list[dict] = []
    for i, (start, end) in enumerate(ranges, start=1):
        chunk = body[start:end]
        start_ts, end_ts = _extract_ts(chunk, kind)
        marker = f"<!-- wiki-weaver segment {i}/{total} (chars {start}-{end} of {len(body)}) -->\n"
        full = header + marker + chunk
        segments.append(
            {
                "body": full,
                "bytes": len(full.encode("utf-8")),
                "start_ts": start_ts,
                "end_ts": end_ts,
            }
        )
    return segments


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.segment_source")
    parser.add_argument("--wiki-root", required=True)
    # int_or_default: pipeline/ingest.dot passes this via the bare $segment_max_bytes
    # substitution form -- an absent context key reaches this CLI as "" (empty
    # string), which int_or_default resolves to DEFAULT_SEGMENT_BYTES (the ONE
    # place this default lives; see lib.int_or_default's docstring).
    parser.add_argument("--max-bytes", type=int_or_default(DEFAULT_SEGMENT_BYTES), default=DEFAULT_SEGMENT_BYTES)
    parser.add_argument(
        "--select",
        action="store_true",
        default=False,
        help="segment-level resume gate: pick the next pending segment instead of (re-)splitting",
    )
    return parser


def _run_split(wr: WikiRoot, max_bytes: int) -> int:
    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        kind = read_current_kind(wr)
    except FileNotFoundError as exc:
        print(f"{exc} -- cannot segment without a detected kind", file=sys.stderr)
        print("segment_failed")
        return 0

    source_path = wr.sources_dir / source_id
    if not source_path.is_file():
        print(f"{source_path} does not exist -- source was removed after selection", file=sys.stderr)
        print("segment_failed")
        return 0

    # DESIGN.md §5 stream watermarking: for a kind=stream source in delta/
    # full/watermark_reset mode, `watermark` writes the bounded content THIS
    # pass must segment to current_source_content_file -- read that INSTEAD
    # OF the raw (possibly ~95%-duplicate) file when present. Every other
    # kind never has this file (detect_kind deletes any stale copy on fresh
    # selection), so this is a no-op fallback to the pre-watermark behavior
    # for meeting/article/repo.
    content_source = wr.current_source_content_file if wr.current_source_content_file.is_file() else source_path
    try:
        text = content_source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"could not read {content_source}: {exc}", file=sys.stderr)
        print("segment_failed")
        return 0

    segments = build_segments(text, kind, max_bytes)

    safe_id = _sanitize_id(source_id)
    seg_dir = wr.segments_dir / safe_id
    ensure_dir(seg_dir)
    manifest_entries: list[dict] = []
    for i, seg in enumerate(segments, start=1):
        rel_path = f".ai/segments/{safe_id}/segment-{i:04d}.md"
        atomic_write_text(wr.root / rel_path, seg["body"])
        manifest_entries.append(
            {
                "index": i,
                "path": rel_path,
                "bytes": seg["bytes"],
                "start_ts": seg["start_ts"],
                "end_ts": seg["end_ts"],
            }
        )

    manifest = {
        "source_id": source_id,
        "kind": kind,
        "segments": manifest_entries,
        "total": len(manifest_entries),
    }
    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.segments_manifest_file, json.dumps(manifest, indent=2) + "\n")

    total = len(manifest_entries)
    print(f"segmented {source_id} (kind={kind}) into {total} segment(s), max_bytes={max_bytes}", file=sys.stderr)
    print("segmented" if total > 1 else "single")
    return 0


def _run_select(wr: WikiRoot) -> int:
    if not wr.segments_manifest_file.is_file():
        print(f"{wr.segments_manifest_file} does not exist -- segment_source must run first", file=sys.stderr)
        return 1

    manifest = json.loads(wr.segments_manifest_file.read_text(encoding="utf-8"))
    source_id = manifest["source_id"]
    total = manifest["total"]
    entries = {entry["index"]: entry for entry in manifest["segments"]}

    done = ledgered_segment_indices(wr.ledger_path, source_id)
    pending = sorted(set(range(1, total + 1)) - done)

    if not pending:
        # THE durable checkpoint for DESIGN.md §5 stream watermarking: this
        # fires exactly once, exactly when every segment 1..total of THIS
        # pass has a ledger decision -- i.e. the delta/full content this
        # source's watermark step bounded is now durably committed. Advancing
        # any EARLIER (e.g. right when the delta was computed, before any
        # segment was ledgered) would violate the "do the work -> make it
        # durable -> then record it done" ordering law: a crash partway
        # through a multi-segment delta would silently lose the unfinished
        # segments on resume (the next run would see "no new content" and
        # skip them forever). No-op for every non-stream kind.
        advance_watermark_if_stream(wr, source_id, manifest.get("kind", ""))
        print(f"all {total} segment(s) of {source_id} already ledgered", file=sys.stderr)
        print("no_segment")
        return 0

    index = pending[0]
    entry = entries[index]
    content_path = wr.root / entry["path"]
    content = content_path.read_text(encoding="utf-8")

    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.current_segment_content_file, content)
    atomic_write_text(
        wr.current_segment_file,
        json.dumps(
            {
                "index": index,
                "total": total,
                "path": entry["path"],
                "start_ts": entry["start_ts"],
                "end_ts": entry["end_ts"],
            },
            indent=2,
        )
        + "\n",
    )

    print(f"segment {index}/{total} selected for {source_id} ({len(pending)} segment(s) pending)", file=sys.stderr)
    print("has_segment")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    if args.select:
        return _run_select(wr)
    return _run_split(wr, args.max_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
