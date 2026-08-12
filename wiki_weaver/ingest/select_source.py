"""wiki_weaver.ingest.select_source -- THE resume gate.

``pending = sources-on-disk MINUS ledger.jsonl ids``, recomputed fresh on
every pass. This is what makes a run resumable: kill it, restart from
``start``, it recomputes pending and picks up where it stopped. See
pipeline/ingest.dot's header and CLI-CONTRACT.md.

STALE-WATERMARK GUARD (KNOWN_ISSUES.md #6's real root cause, corrected --
see that entry's follow-up): the filename-only resume gate above assumes a
``kind=stream`` source's identity always arrives under a NEW filename when
it grows (watermark.py's own docstring: "verified NOT stable... embeds a
growing export date range"). That assumption does NOT hold for every real
feed: the eval corpus's periodic chat exports were verified (see
``epoch-feed/E1..E7``) to reuse the exact SAME filename while the body
grows in place underneath it. Once ``sid`` is ledgered even once (at
whatever size the file happened to be that pass), ``ledgered_source_ids``
above makes it permanently invisible to this gate -- ``detect_kind`` /
``watermark`` / ``segment_source`` never run again for it, no matter how
large the file grows, and the durable watermark freezes at that first
small size forever. This was VERIFIED by direct reproduction: every
affected identity's stored ``watermark_chars`` matches, byte for byte, the
body length of whichever epoch snapshot first got it successfully
ledgered (see delivery report).

TWO CORRECTIONS TO THE ACCOUNT ABOVE (2026-08-09, verified by reproduction):

(a) This guard did NOT check what its own wording claims. The watermark store is
keyed by IDENTITY, but "grew past ITS OWN watermark" is a per-FILE claim, and the
two coincide only when the file being checked is the one that stamped the record.
Several files routinely share one identity (see watermark.py's header: three
files under a single chat-id), so an untouched file tripped the guard the moment
a SHORTER window of the same conversation stamped the record -- halting the whole
run on healthy files. Fixed below: growth is only meaningful against the record
this file itself wrote.

(b) The "no_segment" mechanism described next is only half the story. It holds
when the delta re-split yields FEWER segments than the old ledger. When it yields
MORE, you get a PARTIAL weave -- some new segments woven, the remainder silently
dropped, watermark still stamped at full length (reproduced: pass 1 total=2,
pass 2 delta_total=78, segments 3..12 woven, 391 of 400 new days woven, the rest
lost). Harder to catch than the documented no-op, because the output looks
plausible. The conclusion below is right; the stated mechanism is incomplete.

AND ONE ON SCOPE: generation-aware bookkeeping makes SAME-FILENAME growth safe.
It does not address what this tool is actually used for -- windows of a
conversation arriving over time, in arbitrary order, with overlap. A scalar
offset cannot represent a set of intervals: windows fed [d1-d2], [d5-d6],
[d3-d4] produced three FULL re-ingests, no delta ever computed, no gap reported.
See KNOWN_ISSUES.md #6's 2026-08-09 correction for the two measured unknowns --
turn text IS byte-stable across re-exports (7,932 turns, zero drift), gaps ARE
safe for the weaver, and overlap is NOT.


THE FIX HERE IS DETECTION, NOT SILENT RECOVERY: reopening the pending set
for a grown, already-ledgered filename was tried and PROVEN UNSAFE --
``segment_source``'s per-(source_id, segment_index) ledger check
(``ledgered_segment_indices``) is not generation-aware, so a fresh delta
split of the SAME filename collides with the OLD ledger row at the same
index and ``select_segment`` reports ``no_segment`` immediately, before
weave ever sees the new content -- yet ``advance_watermark_if_stream``
still runs and stamps the watermark at the file's new FULL length. That is
WORSE than the original bug: it durably records the growth as covered
while silently never weaving it. Recovering the lost content safely needs
generation-aware segment/ledger bookkeeping -- real, non-trivial work, out
of scope here (see delivery report's "what a full fix requires" section).
This guard instead makes the gap IMPOSSIBLE TO MISS: ``compute_pending``
now also checks every already-ledgered stream identity's CURRENT on-disk
body against its own durable watermark, using only state that already
exists (no new schema), and fails loud -- mirroring the existing
``ledger_corrupt`` sentinel below -- the moment growth is detected, rather
than continuing to silently drain other sources while this one quietly
falls behind.

Usage:
    python3 -m wiki_weaver.ingest.select_source --wiki-root <path> --restrict <ids-or-empty>
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from wiki_weaver.ingest.watermark import IdentityError, extract_stream_identity, load_watermarks
from wiki_weaver.lib import (
    LedgerCorruptError,
    WikiRoot,
    atomic_write_text,
    ensure_dir,
    ledgered_source_ids,
    split_header,
)


class StaleWatermarkGrowthError(Exception):
    """One or more already-ledgered ``kind=stream`` sources have grown, on
    disk, past their own durable watermark -- see this module's docstring
    (STALE-WATERMARK GUARD) for the full mechanism and why this fails loud
    instead of silently reopening or silently staying quiet."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.select_source")
    parser.add_argument("--wiki-root", required=True, help="filesystem root of the wiki")
    parser.add_argument(
        "--restrict",
        default="",
        help="comma-separated source ids to restrict the candidate set to (empty = all pending)",
    )
    return parser


def _list_source_ids(wr: WikiRoot) -> list[str]:
    if not wr.sources_dir.is_dir():
        return []
    return sorted(p.name for p in wr.sources_dir.iterdir() if p.is_file())


def find_stale_watermark_growth(
    wr: WikiRoot, all_ids: list[str], ledgered: set[str]
) -> list[tuple[str, str, int, int]]:
    """Already-ledgered filenames whose CURRENT on-disk body is longer than
    the durable watermark recorded for their stream identity. Returns
    ``(source_id, identity, watermark_chars, current_body_chars)`` tuples,
    sorted by source_id for deterministic output. Empty list is the normal,
    healthy case -- costs one cheap regex header-scan per already-ledgered
    file still present on disk, no LLM, no new state (DESIGN.md \u00a714: byte-
    level ops are negligible next to a single LLM call)."""
    store = load_watermarks(wr.watermarks_path)
    if not store:
        return []

    findings: list[tuple[str, str, int, int]] = []
    for sid in all_ids:
        if sid not in ledgered:
            continue
        path = wr.sources_dir / sid
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue  # source vanished after ledgering -- not this guard's concern
        try:
            identity = extract_stream_identity(text)
        except IdentityError:
            continue  # never a stream identity -- one-shot kinds correctly stay done forever
        record = store.get(identity)
        if record is None:
            continue  # this identity was never watermarked (not kind=stream at ingest time)
        # The watermark is per-IDENTITY, but this guard's claim is per-FILE: "this
        # file grew past ITS OWN durable watermark". Those coincide only when this
        # file is the one that set the record. Several files routinely share one
        # identity (watermark.py's own header documents 3 files under a single
        # chat-id), so comparing every file against whichever one happened to
        # stamp the record last flags files that were never touched -- and halts
        # the whole run. Reproduced: file A (2165 chars) ledgered, then a SHORTER
        # window B of the same identity stamps the record at 227; A is then
        # reported as "grown from 227 to 2165" though nothing on disk changed.
        # Growth is only meaningful against the record this file itself wrote.
        if record.get("source_id") != sid:
            continue
        _header, body = split_header(text)
        watermark_chars = int(record["watermark_chars"])
        if len(body) > watermark_chars:
            findings.append((sid, identity, watermark_chars, len(body)))
    return sorted(findings)


def compute_pending(wr: WikiRoot, restrict: str) -> list[str]:
    """Deterministic pending-source computation -- the resume gate itself.

    Raises ``StaleWatermarkGrowthError`` (never silently proceeds) when an
    already-ledgered stream source has grown past its watermark -- see the
    module docstring's STALE-WATERMARK GUARD.
    """
    all_ids = _list_source_ids(wr)
    ledgered = ledgered_source_ids(wr.ledger_path)  # raises LedgerCorruptError

    stale = find_stale_watermark_growth(wr, all_ids, ledgered)
    if stale:
        details = "; ".join(
            f"{sid} ({identity}): watermarked at {watermark_chars} chars, now {current_chars} chars on disk"
            for sid, identity, watermark_chars, current_chars in stale
        )
        raise StaleWatermarkGrowthError(
            f"{len(stale)} already-ledgered stream source(s) have grown past their durable watermark -- {details}"
        )

    pending = [sid for sid in all_ids if sid not in ledgered]

    restrict_ids = [s.strip() for s in restrict.split(",") if s.strip()]
    if restrict_ids:
        restrict_set = set(restrict_ids)
        pending = [sid for sid in pending if sid in restrict_set]
    return pending


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        pending = compute_pending(wr, args.restrict)
    except LedgerCorruptError as exc:
        print(f"ledger corrupt, failing loud rather than treating as empty: {exc}", file=sys.stderr)
        print("ledger_corrupt")
        return 0
    except StaleWatermarkGrowthError as exc:
        print(f"stale watermark growth detected, failing loud rather than silently draining: {exc}", file=sys.stderr)
        print("growth_detected")
        return 0

    if not pending:
        print("no pending sources", file=sys.stderr)
        print("no_source")
        return 0

    chosen = pending[0]
    ensure_dir(wr.ai_dir)
    atomic_write_text(wr.current_source_file, chosen)
    atomic_write_text(wr.source_start_file, str(int(time.time())))
    print(f"selected source: {chosen} ({len(pending)} pending)", file=sys.stderr)
    print("has_source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
