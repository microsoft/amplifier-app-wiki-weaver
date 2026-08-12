"""wiki_weaver.ingest.watermark -- DESIGN.md §5's actual fix for the live
stream-source defect.

THE BUG: a ``kind=stream`` source (a Teams group-chat export -- day-blocked
``[HH:MM] **Speaker**`` turns) gains a handful of new turns on its next
export. Because it is a NEW export, it typically arrives under a NEW
filename too (verified against the real corpus,
``~/dev/future-brainstorm/archive/transcripts/``: e.g.
``chat-2026-05-21_to_2026-06-20-Amplifier-Resolve-Team.md`` encodes a
growing end-date in its own name). ``select_source``'s resume gate keys
ledger membership on filename (``source_id``), so a new filename is a new
pending source -- and without this module, the ENTIRE file (~95% duplicate
of what was already ingested) would be segmented and woven again. This is
the v1 defect DESIGN.md §5 exists to fix, still live in v2 until now.

THE FIX: track a durable watermark per stream IDENTITY (not per filename,
not per content hash -- see ``extract_stream_identity`` below for why), and
on re-ingest emit only the content beyond that watermark, plus a small
overlap window so the writer doesn't lose seam context (DESIGN.md §5:
"stream = 10 turns or 2000 tokens of preceding context, not re-cited").

IDENTITY -- why it cannot be the content hash, the filename, or the title:

  - Content hash: by definition changes every time new turns are appended.
    Hashing an append-only stream to identify it is identifying it by the
    one property GUARANTEED to change on every legitimate re-export. This is
    the exact v1 mistake (DESIGN.md §5's opening paragraph).
  - Filename: verified NOT stable across re-exports in the real corpus --
    the filename literally embeds the growing date range
    (``..._to_2026-06-20...`` -> ``..._to_2026-07-20...`` next month).
  - Title (`# Chat: <title>`): human-editable, and this project's corpus
    already has two DIFFERENT titles for the SAME Chat ID
    (``Amplifier-Agent-Workstream`` vs ``Amplifier-as-Agent-Workstream``,
    both ``Chat ID: 19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2``) --
    verified, not hypothetical. Titles are not a safe identity key either.
  - ``Chat ID:`` / ``Call ID:`` header field: verified stable. Every one of
    the 27 real stream-kind files in the target corpus carries a
    ``Chat ID:`` field, and it is IDENTICAL across every re-export of the
    same channel found in the corpus (3 files share
    ``19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2``; the titles and every
    other header field differ). This is the "Call-ID-like header field" the
    task calls for, and it is the only signal in the corpus that actually
    satisfies "survives content growth."

So identity = the ``Chat ID:`` (preferred) or ``Call ID:`` header field.
When NEITHER is present (e.g. a stream detected purely by turn-shape density
with no header at all -- detect_kind.py's low-confidence fallback branch),
this module refuses to guess: it fails loud (``identity_failed``) rather
than silently falling back to a content hash, which would silently
reproduce the exact bug this module exists to fix.

WATERMARK COMPARISON IS ON THE BODY, NEVER THE HEADER: verified against the
real corpus (three re-exports of Chat ID
``19:0a1b2c3d4e5f60718293a4b5c6d7e8f9@thread.v2``) that ``Downloaded:``,
``Lookback:``, and ``Messages:`` all change on every re-export even when
the actual conversation is a pure superset of the prior export. The header
is NOT append-only stable -- only the body (everything after the leading
``---`` block) is. All watermark/fingerprint comparisons below operate on
``split_header(text)[1]`` (the body), never the raw file text.

ORDERING LAW (REWRITE-GUIDE.md §2.2/2.6, "do the work -> make it durable ->
THEN record it done"): this module's ``compute_delta`` NEVER persists the
new watermark position -- it only decides what THIS pass should segment and
writes it to ``current_source_content_file``. The watermark only advances
via ``advance_watermark_if_stream``, called from
``segment_source._run_select``'s ``no_segment`` branch -- the existing,
already-idempotent checkpoint that fires exactly once, exactly when every
segment of this pass has a durable ledger decision. Advancing any earlier
would mean a crash partway through a multi-segment delta silently loses the
unfinished segments on resume (the next run would see "no new content" and
skip them forever).

Sentinels (JSON, one line, ``parse_json="true"`` -- see pipeline/ingest.dot):
    full            first sight of this identity -- whole body is new.
    delta           new content beyond the watermark -- emits delta + overlap.
    no_delta        nothing new -- caller must route to a clean skip, no LLM call.
    watermark_reset content diverged BEFORE the watermark (history edited/
                    truncated) -- never silently delta; falls back to full
                    ingest and says so via this distinct sentinel.
    identity_failed (not one of the 4 above, added for the same reason
                    segment_source has ``segment_failed``): the source file
                    is missing/unreadable, or no stable identity field is
                    present. Fail loud -> the graph routes to exit, never a
                    guess.

Usage:
    python3 -m wiki_weaver.ingest.watermark --wiki-root <path>
        [--overlap-turns N] [--overlap-bytes N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path

from wiki_weaver.ingest.detect_kind import STREAM_TURN_RE
from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    read_current_source_id,
    split_header,
)


class IdentityError(Exception):
    """The current source cannot be watermarked: missing/unreadable file, or
    no stable identity field present. Callers must fail loud on this, never
    fall back to a content hash (see module docstring)."""


# ---------------------------------------------------------------------------
# Identity extraction
# ---------------------------------------------------------------------------

_CHAT_ID_RE = re.compile(r"^Chat ID:\s*(\S.*)$", re.MULTILINE)
_CALL_ID_RE = re.compile(r"^Call ID:\s*(\S.*)$", re.MULTILINE)


def extract_stream_identity(text: str) -> str:
    """The stable identity for a ``kind=stream`` source: ``Chat ID:`` first
    (present on all 27 real stream files investigated), ``Call ID:`` second
    (the meeting-style analogue, for a persistent meeting-chat thread that
    carries one instead -- see detect_kind.py's docstring on the "Chat type:
    Meeting" overload). Raises ``IdentityError`` -- never guesses, never
    hashes content -- when neither is present."""
    m = _CHAT_ID_RE.search(text)
    if m:
        return f"chat-id:{m.group(1).strip()}"
    m = _CALL_ID_RE.search(text)
    if m:
        return f"call-id:{m.group(1).strip()}"
    raise IdentityError(
        "no stable identity field found (looked for 'Chat ID:' / 'Call ID:' header "
        "fields) -- refusing to derive identity from the filename (verified unstable "
        "in the real corpus: it embeds a growing export date range) or a content hash "
        "(unstable by definition for an append-only stream) -- see module docstring"
    )


# ---------------------------------------------------------------------------
# Overlap policy (DESIGN.md §5: "10 turns or 2000 tokens of preceding
# context, not re-cited" -- starting values, tunable by eval)
# ---------------------------------------------------------------------------

DEFAULT_OVERLAP_TURNS = 10
# ~2000 tokens at PIPELINE-PHILOSOPHY §3's own conversion factor ("English
# text averages ~4 bytes/token") -- used only when the overlap window
# contains no recognizable turn boundary (see _overlap_start).
DEFAULT_OVERLAP_BYTES = 8_000

NEW_CONTENT_MARKER = "<!-- wiki-weaver watermark: NEW content begins here (beyond the prior watermark) -->\n"
OVERLAP_MARKER = (
    "<!-- wiki-weaver watermark: overlap context begins here -- prior "
    "material for continuity, DO NOT re-cite as new (DESIGN.md \u00a75) -->\n"
)


def _fingerprint(prefix_text: str) -> str:
    return hashlib.sha256(prefix_text.encode("utf-8")).hexdigest()


def _overlap_start(body: str, watermark_chars: int, overlap_turns: int, overlap_bytes: int) -> int:
    """Char offset where the emitted overlap window should begin: the start
    of the ``overlap_turns``-th-from-last stream turn at/before the
    watermark, when any turn boundaries exist in the prefix. Falls back to a
    byte-ish window (mirrors segment_source._unit_starts's own turn/fallback
    pattern) when the prefix has no recognizable turn shape -- e.g. a
    day-header-only export."""
    if overlap_turns <= 0 and overlap_bytes <= 0:
        return watermark_chars
    prefix = body[:watermark_chars]
    turn_starts = [m.start() for m in STREAM_TURN_RE.finditer(prefix)]
    if turn_starts:
        keep = turn_starts[-overlap_turns:] if overlap_turns > 0 else []
        return keep[0] if keep else watermark_chars
    return max(0, watermark_chars - overlap_bytes)


# ---------------------------------------------------------------------------
# Watermark store -- a single atomically-rewritten JSON dict keyed by
# identity, mirroring the existing wiki_index_file precedent (lib.py).
# ---------------------------------------------------------------------------


def load_watermarks(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_watermarks(path: Path, data: dict) -> None:
    ensure_dir(path.parent)
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------------------
# THE compute step -- read-only w.r.t. the watermark store (see ordering-law
# note in the module docstring); writes current_source_content_file for
# every mode except no_delta.
# ---------------------------------------------------------------------------


def _read_source_text(wr: WikiRoot, source_id: str) -> str:
    source_path = wr.sources_dir / source_id
    if not source_path.is_file():
        raise IdentityError(f"{source_path} does not exist -- source was removed after selection")
    try:
        return source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IdentityError(f"could not read {source_path}: {exc}") from exc


def compute_delta(
    wr: WikiRoot,
    source_id: str,
    overlap_turns: int = DEFAULT_OVERLAP_TURNS,
    overlap_bytes: int = DEFAULT_OVERLAP_BYTES,
) -> dict:
    """Pure-ish (does I/O to read the source + watermark store, and writes
    ``current_source_content_file``, but never mutates the watermark store
    itself -- see ordering-law note above). Returns the summary dict this
    module's CLI mirrors onto stdout as one JSON line."""
    text = _read_source_text(wr, source_id)
    identity = extract_stream_identity(text)
    _header, body = split_header(text)

    store = load_watermarks(wr.watermarks_path)
    record = store.get(identity)

    if record is None:
        _write_content(wr, _header, body[0:0], body)
        return {
            "mode": "full",
            "identity": identity,
            "watermark": 0,
            "new_bytes": len(body.encode("utf-8")),
            "overlap_bytes": 0,
        }

    watermark_chars = int(record["watermark_chars"])
    watermark_bytes = int(record.get("watermark_bytes", len(body[:watermark_chars].encode("utf-8"))))
    prior_fingerprint = record["fingerprint"]

    diverged = len(body) < watermark_chars or _fingerprint(body[:watermark_chars]) != prior_fingerprint
    if diverged:
        _write_content(wr, _header, body[0:0], body)
        return {
            "mode": "watermark_reset",
            "identity": identity,
            "watermark": watermark_bytes,
            "new_bytes": len(body.encode("utf-8")),
            "overlap_bytes": 0,
        }

    if len(body) == watermark_chars:
        return {
            "mode": "no_delta",
            "identity": identity,
            "watermark": watermark_bytes,
            "new_bytes": 0,
            "overlap_bytes": 0,
        }

    overlap_start = _overlap_start(body, watermark_chars, overlap_turns, overlap_bytes)
    overlap_text = body[overlap_start:watermark_chars]
    new_text = body[watermark_chars:]
    _write_content(wr, _header, overlap_text, new_text)
    return {
        "mode": "delta",
        "identity": identity,
        "watermark": watermark_bytes,
        "new_bytes": len(new_text.encode("utf-8")),
        "overlap_bytes": len(overlap_text.encode("utf-8")),
    }


def _write_content(wr: WikiRoot, header: str, overlap_text: str, new_text: str) -> None:
    parts = [header]
    if overlap_text:
        parts.append(OVERLAP_MARKER)
        parts.append(overlap_text)
    parts.append(NEW_CONTENT_MARKER)
    parts.append(new_text)
    atomic_write_text(wr.current_source_content_file, "".join(parts))


# ---------------------------------------------------------------------------
# THE advance step -- called by segment_source._run_select's no_segment
# branch, once this pass's segments are ALL durably ledgered. Self-
# sufficient: re-derives identity from the (immutable) raw source file, so
# no state needs to be threaded through from the compute step above.
# ---------------------------------------------------------------------------


def advance_watermark_if_stream(wr: WikiRoot, source_id: str, kind: str) -> None:
    """No-op for every kind other than ``stream``. For a stream source,
    advances the persisted watermark to this source file's current full
    body length -- correct regardless of whether this pass ingested
    ``full``/``delta``/``watermark_reset`` content, because by the time this
    is called every segment of whatever WAS emitted this pass is durably
    ledgered, and the append-only invariant means "durably committed up to
    here" now equals "the whole current file" (delta/full/reset all bound
    THIS pass's segmentation to exactly the current file's content, never
    less)."""
    if kind != "stream":
        return
    try:
        text = _read_source_text(wr, source_id)
        identity = extract_stream_identity(text)
    except IdentityError as exc:
        # Identity extraction already succeeded once earlier in this same
        # pass (or watermark's compute step would have failed loud before
        # segment_source ever ran) -- reaching this branch means the source
        # file vanished mid-pipeline. Log loudly rather than crash a
        # finalize step for content that already committed successfully.
        print(f"watermark advance skipped for {source_id}: {exc}", file=sys.stderr)
        return

    _header, body = split_header(text)
    store = load_watermarks(wr.watermarks_path)
    store[identity] = {
        "source_id": source_id,
        "watermark_chars": len(body),
        "watermark_bytes": len(body.encode("utf-8")),
        "fingerprint": _fingerprint(body),
        "updated": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()) + " UTC",
    }
    save_watermarks(wr.watermarks_path, store)
    print(
        f"watermark advanced: {identity} -> {store[identity]['watermark_bytes']} bytes ({source_id})", file=sys.stderr
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.watermark")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument("--overlap-turns", type=int, default=DEFAULT_OVERLAP_TURNS)
    parser.add_argument("--overlap-bytes", type=int, default=DEFAULT_OVERLAP_BYTES)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        result = compute_delta(wr, source_id, args.overlap_turns, args.overlap_bytes)
    except IdentityError as exc:
        print(f"{exc} (source={source_id})", file=sys.stderr)
        print(json.dumps({"mode": "identity_failed"}))
        return 0

    print(
        f"watermark: source={source_id} mode={result['mode']} watermark={result['watermark']} "
        f"new_bytes={result['new_bytes']} overlap_bytes={result['overlap_bytes']}",
        file=sys.stderr,
    )
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
