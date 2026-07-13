# pyright: reportMissingImports=false
"""Claim-retention backstop gate (Phase 1) -- wired into wiki_weaver/lib.py's ingest().

THE PROBLEM (confirmed on real incident fixtures, not hypothesized): wiki-weaver's
synthesis prompt genuinely tries to preserve prior content ("FUSE claims, don't
just append"), but nothing at runtime checks whether prior content actually
SURVIVED a re-write. ``wiki_weaver.grading.grade_claim_retention`` (relocated
there from ``eval/grade_claim_retention.py`` -- see that module's docstring)
already classifies exactly this: RETAINED / SUPERSEDED / MOVED / SILENTLY_LOST.
It existed for over a release cycle but was eval-only -- never invoked during a
real ingest. This module wires it into the actual ingest path.

HONEST FRAMING -- read before touching this file
--------------------------------------------------
This gate is an INDEPENDENT, LLM-JUDGE-BACKED RE-CHECK backed by a fail-open /
fail-closed escalation policy. It is explicitly NOT deterministic and must
NEVER be described as "deterministic" or "cannot be bypassed" anywhere in this
codebase (comments, docstrings, log messages, docs). ``grade_claim_retention()``
always requires a real LLM judge call to produce a verdict -- there is no
offline/mechanical mode, unlike ``grade_overview()`` in ``wiki_weaver/grading.py``.
A PASS from this gate is strong probabilistic evidence that no grounded claim
was silently dropped -- it is not proof. Overclaiming this as deterministic
could cause a downstream consumer to skip building the human-review safeguards
a probabilistic system like this one actually needs.

WHAT THIS GATE DOES
--------------------
1. ``snapshot_pages()`` -- copy the wiki's current root-level ``*.md`` pages
   (the BEFORE state) to a temp location before a source is synthesized.
2. ``check_retention()`` -- after synthesis converges, diff each before-page
   against its after-wiki counterpart; SKIP pages whose body text (frontmatter
   excluded -- see the hash-scope comment below) is unchanged; grade
   changed/deleted pages with ``grade_claim_retention`` against a FOCUSED
   after-context (the page + its 1-hop wikilink neighbors), not the whole
   corpus, to stay under the grader's context-length cap.
3. ``enforce_retention_gate()`` -- the caller-facing orchestration wiki_weaver/lib.py
   uses: runs the check, applies the persistent consecutive-grader-failure
   counter with escalation, and always cleans up the snapshot.

SCOPE (Phase 1 only)
--------------------
In scope: the gate above, wired into ``ingest()``'s single-file AND drain paths.
Explicitly OUT of scope for this module (sequenced later): a ``.dot``
self-healing graph node (Phase 2), a CLI ``check-retention`` subcommand
(Phase 3), an agent-tool-module wrapper (no consumer identified yet).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .grading import RetentionResult, grade_claim_retention
from .index import (
    PageNotFound,
    _body,
    _extract_links,
    _slug,
    build_indexes,
    query_graph_neighbors,
)
from .lib import wiki_retention_state

__all__ = [
    "PageRetentionOutcome",
    "RetentionGateResult",
    "RetentionGateDecision",
    "DEFAULT_ESCALATION_THRESHOLD",
    "snapshot_pages",
    "check_retention",
    "enforce_retention_gate",
    "load_failure_counter",
    "record_grader_error",
    "record_grader_success",
]

# Default consecutive-grader-failure escalation threshold. Configurable per
# call via enforce_retention_gate(..., escalation_threshold=N) -- a CLI/policy
# knob for this is Phase 3 scope, not built here.
DEFAULT_ESCALATION_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Hash-scope decision: BODY ONLY, frontmatter stripped -- deliberate, not a
# default. Read this before changing it.
# ---------------------------------------------------------------------------
#
# The incident fixtures (eval/fixtures/incident_2026_07/) show the bug's own
# signature: the `last_updated` frontmatter field ROLLS BACKWARD on a page
# that also lost real body content (e.g. design-and-promotion.md before=
# 2026-07-07, after=2026-06-29; byo-agent-ecosystem-recon.md before=2026-07-10,
# after=2026-07-06). That is direct evidence that frontmatter fields -- even
# ones that look like change-tracking metadata -- are NOT a trustworthy signal
# for "did the body change": whatever wrote the after-page here recorded an
# EARLIER date than the before-page, despite the file being touched. Hashing
# full file bytes (frontmatter included) would happen to still detect a change
# on these specific fixtures (since the timestamp differs too), but that is
# incidental, not reliable -- a future case where only `sources:` or another
# frontmatter field ticks with the body byte-for-byte identical would trigger
# a wasted LLM grading pass, and a case where frontmatter is copied verbatim
# while the body silently regresses would still need catching by body content,
# never by trusting metadata.
#
# This also matches the codebase's OWN established convention: index.py's
# `_extract_links()` operates on `_body(text)` specifically to exclude
# frontmatter noise from content-shape analysis (see index.py's module
# docstring: "Frontmatter parser is a lightweight inline implementation").
# The SKIP-if-unchanged optimization here follows the same principle: what
# matters for claim retention lives in the BODY (grounded claims are prose,
# never frontmatter keys), so the change signal must come from the body only.
#
# DECISION: hash `_body(text)` (frontmatter stripped, via index.py's own
# helper -- not re-implemented here), not the full file bytes.


def _body_hash(path: Path) -> str | None:
    """Return sha256 of *path*'s body text (frontmatter excluded), or None if unreadable/missing."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return hashlib.sha256(_body(text).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# snapshot_pages -- BEFORE-state capture
# ---------------------------------------------------------------------------


def snapshot_pages(wiki: str | Path, dest: str | Path) -> None:
    """Copy the wiki's current root-level ``*.md`` pages into *dest* (plain file copy).

    Both ``grade_claim_retention`` (via ``check_retention``) and the existing
    eval graders (``grade_overview``, ``grade_synthesis``) only ever scan
    root ``*.md`` files -- confirmed against ``wiki_weaver/grading.py`` and
    ``eval/grade_wiki.py``. This snapshot mirrors exactly that scope: nothing
    under ``.wiki/``, ``_sources/``, ``_inbox/``, etc.
    """
    wiki = Path(wiki)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for md in wiki.glob("*.md"):
        shutil.copy2(md, dest / md.name)


# ---------------------------------------------------------------------------
# check_retention -- the per-page diff + focused-context grading pass
# ---------------------------------------------------------------------------


@dataclass
class PageRetentionOutcome:
    """Per-page result of a single check_retention() pass."""

    page: str  # filename, e.g. "beacon.md"
    status: str  # "skipped_unchanged" | "passed" | "confirmed_loss" | "errored"
    silently_lost: list[dict] = field(default_factory=list)
    error: str | None = None
    focused_context_pages: list[str] = field(default_factory=list)


@dataclass
class RetentionGateResult:
    """Aggregate outcome of check_retention() across every before-page.

    Distinguishes three mutually-exclusive states, in priority order:
      passed             -- every page skipped-unchanged or graded RETAINED/
                             SUPERSEDED/MOVED only. No loss, no grader errors.
      has_confirmed_loss -- at least one page produced a definitive
                             SILENTLY_LOST verdict. This is real, actionable
                             evidence of content loss -- it takes priority
                             over any co-occurring "errored" page, because the
                             grader DID successfully run and DID find loss.
      errored            -- no confirmed loss, but at least one page's grader
                             call could not produce a verdict (unavailable
                             judge, malformed response, etc.). This is NOT
                             evidence of loss -- it means the independent
                             re-check could not run, which is a distinct
                             failure mode requiring the fail-open/fail-closed
                             escalation policy (see enforce_retention_gate()).
    """

    pages: list[PageRetentionOutcome]

    @property
    def has_confirmed_loss(self) -> bool:
        return any(p.status == "confirmed_loss" for p in self.pages)

    @property
    def errored(self) -> bool:
        return not self.has_confirmed_loss and any(
            p.status == "errored" for p in self.pages
        )

    @property
    def passed(self) -> bool:
        return not self.has_confirmed_loss and not self.errored

    @property
    def lost_claims(self) -> list[tuple[str, dict]]:
        """[(page_name, claim_dict), ...] for every SILENTLY_LOST claim across all pages."""
        out: list[tuple[str, dict]] = []
        for p in self.pages:
            for c in p.silently_lost:
                out.append((p.page, c))
        return out

    def report(self) -> str:
        verdict = (
            "CONFIRMED_LOSS"
            if self.has_confirmed_loss
            else "ERRORED"
            if self.errored
            else "PASS"
        )
        lines = [f"[{verdict}] claim-retention-gate"]
        for p in self.pages:
            lines.append(f"  {p.status:<18} {p.page}")
            if p.error:
                lines.append(f"      error: {p.error}")
            for c in p.silently_lost:
                lines.append(f"      LOST: {c.get('claim_quote', '')[:120]}")
        return "\n".join(lines)


def _one_hop_neighbor_slugs(
    after_wiki: Path, before_page: Path, after_page_exists: bool
) -> set[str]:
    """Return the 1-hop wikilink-neighbor slugs of before_page's after-wiki counterpart.

    Uses the shipped index.py query layer (build_indexes/query_graph_neighbors)
    -- the established, deterministic wikilink graph -- not a re-implementation.

    If the page was deleted outright (no after-wiki counterpart), it has no
    entry in the after-wiki's link index to query neighbors FROM. Fall back to
    the page's OWN before-side outbound wikilinks as the best available signal
    for "where might this content have moved to" (serves the MOVED-detection
    case). Reuses index.py's own link-extraction/slug helpers -- no new
    parsing logic invented here.
    """
    slug = _slug(before_page.stem)
    if after_page_exists:
        try:
            neighbors = query_graph_neighbors(after_wiki, slug)
            return set(neighbors["out"]) | set(neighbors["in"])
        except PageNotFound:
            pass  # fall through to the before-side fallback below

    try:
        before_text = before_page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        before_text = ""
    return set(_extract_links(before_text))


def _focused_context_pages(after_wiki: Path, before_page: Path) -> list[Path]:
    """Return the after-wiki page files forming the FOCUSED context for before_page:
    its same-named after-page (if it still exists) plus its 1-hop wikilink neighbors.

    Deliberately NOT the whole corpus -- grade_claim_retention truncates its
    after-wiki text at 12k chars; feeding an entire large corpus would
    silently truncate away the very evidence needed to classify
    RETAINED/SUPERSEDED/MOVED correctly, defeating the grader's own
    trace-or-justify design.
    """
    after_page = after_wiki / before_page.name
    after_page_exists = after_page.is_file()
    neighbor_slugs = _one_hop_neighbor_slugs(after_wiki, before_page, after_page_exists)

    pages: list[Path] = []
    if after_page_exists:
        pages.append(after_page)
    for other in sorted(after_wiki.glob("*.md")):
        if other == after_page:
            continue
        if _slug(other.stem) in neighbor_slugs:
            pages.append(other)
    return pages


def _grade_one_page(
    before_page: Path,
    after_wiki: Path,
    judge_fn,
) -> PageRetentionOutcome:
    context_pages = _focused_context_pages(after_wiki, before_page)
    before_text = before_page.read_text(encoding="utf-8", errors="replace")

    if context_pages:
        with tempfile.TemporaryDirectory(prefix="retention-focus-") as tmp:
            tmp_dir = Path(tmp)
            for p in context_pages:
                shutil.copy2(p, tmp_dir / p.name)
            result: RetentionResult = grade_claim_retention(
                before_text, tmp_dir, judge_fn=judge_fn
            )
    else:
        # No focused context resolvable at all (page deleted, zero linked
        # neighbors found either side) -- grade_claim_retention requires at
        # least one after-wiki .md file to grade against. Fall back to the
        # full after_wiki rather than silently skipping a deleted page; this
        # is not the common case (a real synthesis run leaves at least
        # overview.md/index.md) but must not go unchecked.
        result = grade_claim_retention(before_text, after_wiki, judge_fn=judge_fn)

    context_names = [p.name for p in context_pages]
    if result.error is not None:
        return PageRetentionOutcome(
            page=before_page.name,
            status="errored",
            error=result.error,
            focused_context_pages=context_names,
        )
    if result.silently_lost:
        return PageRetentionOutcome(
            page=before_page.name,
            status="confirmed_loss",
            silently_lost=result.silently_lost,
            focused_context_pages=context_names,
        )
    return PageRetentionOutcome(
        page=before_page.name, status="passed", focused_context_pages=context_names
    )


def check_retention(
    before_dir: str | Path,
    after_wiki: str | Path,
    judge_fn=None,
) -> RetentionGateResult:
    """Grade claim retention for every page that changed between before_dir and after_wiki.

    Pages whose BODY text (frontmatter excluded) is byte-identical are SKIPPED
    -- see the hash-scope comment above _body_hash() for why body-only, not
    full-file bytes. Changed or deleted pages are graded against a FOCUSED
    after-context (the page + its 1-hop wikilink neighbors), never the whole
    corpus -- see _focused_context_pages().

    HONEST FRAMING: every non-skipped page invokes grade_claim_retention(),
    which is an independent, LLM-judge-backed re-check -- not a deterministic
    gate. See this module's docstring.
    """
    before_dir = Path(before_dir)
    after_wiki = Path(after_wiki)

    # Refresh the after-wiki's index (cheap, deterministic, no LLM/network)
    # so query_graph_neighbors reflects the CURRENT after-state.
    build_indexes(after_wiki)

    outcomes: list[PageRetentionOutcome] = []
    for before_page in sorted(before_dir.glob("*.md")):
        after_page = after_wiki / before_page.name
        before_hash = _body_hash(before_page)
        after_hash = _body_hash(after_page)

        if after_hash is not None and before_hash == after_hash:
            outcomes.append(
                PageRetentionOutcome(page=before_page.name, status="skipped_unchanged")
            )
            continue

        outcomes.append(_grade_one_page(before_page, after_wiki, judge_fn))

    return RetentionGateResult(pages=outcomes)


# ---------------------------------------------------------------------------
# Persistent consecutive-grader-failure counter (fail-open/fail-closed escalation)
# ---------------------------------------------------------------------------


def load_failure_counter(wiki: str | Path) -> int:
    """Read the persisted consecutive-grader-failure counter (0 if absent/corrupt)."""
    path = wiki_retention_state(Path(wiki))
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get("consecutive_grader_errors", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def _save_failure_counter(wiki: Path, count: int) -> None:
    path = wiki_retention_state(wiki)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"consecutive_grader_errors": count}), encoding="utf-8")
    tmp.replace(path)


def record_grader_error(
    wiki: str | Path, threshold: int = DEFAULT_ESCALATION_THRESHOLD
) -> tuple[int, bool]:
    """Increment the persisted counter; return (new_count, escalated).

    escalated=True means new_count >= threshold -- the caller must fail
    CLOSED (block the commit) instead of the default fail-OPEN (allow the
    commit through with a loud WARN).
    """
    wiki = Path(wiki)
    count = load_failure_counter(wiki) + 1
    _save_failure_counter(wiki, count)
    return count, count >= threshold


def record_grader_success(wiki: str | Path) -> None:
    """Reset the persisted counter to zero.

    Called whenever the grader itself successfully ran and produced a real
    verdict -- whether that verdict was PASS or CONFIRMED_LOSS. Only grader
    UNAVAILABILITY (exception, malformed response) increments the counter;
    finding real loss is the grader working correctly, not a failure of it.
    """
    _save_failure_counter(Path(wiki), 0)


# ---------------------------------------------------------------------------
# enforce_retention_gate -- the caller-facing orchestration wiki_weaver/lib.py uses
# ---------------------------------------------------------------------------


@dataclass
class RetentionGateDecision:
    """What wiki_weaver/lib.py's ingest() must do after a converged synthesis run."""

    action: str  # "proceed" | "block_confirmed_loss" | "block_escalated_errors"
    message: str  # human-readable, fail-loud-ready message


def _grader_error_decision(
    wiki: Path, threshold: int, reason: str
) -> RetentionGateDecision:
    count, escalated = record_grader_error(wiki, threshold)
    if escalated:
        return RetentionGateDecision(
            action="block_escalated_errors",
            message=(
                f"ALERT: claim-retention gate has failed {count} consecutive "
                f"time(s) (escalation threshold {threshold}) -- {reason}. "
                "Escalating from fail-OPEN to fail-CLOSED: this source will NOT "
                "be archived until the independent re-check can run successfully "
                "again (counter resets on the next successful grader run)."
            ),
        )
    return RetentionGateDecision(
        action="proceed",
        message=(
            f"WARN: claim-retention gate could not run ({count}/{threshold} "
            f"consecutive failures) -- {reason}. Failing OPEN (allowing this "
            "source to be archived) -- this is NOT evidence the re-write is "
            "clean, only that the independent LLM re-check could not run this "
            "time."
        ),
    )


def enforce_retention_gate(
    wiki: str | Path,
    snapshot_dir: str | Path,
    *,
    escalation_threshold: int = DEFAULT_ESCALATION_THRESHOLD,
    judge_fn=None,
) -> RetentionGateDecision:
    """Run the claim-retention backstop after a converged synthesis run.

    ``snapshot_dir`` must be a BEFORE-page snapshot written by
    ``snapshot_pages()`` prior to the synthesis run. Always removes
    ``snapshot_dir`` before returning, on both the happy path and any
    internal error (mirrors the cleanup discipline in
    ``wiki_weaver/ingest_tamper_check.py``, which unlinks its own before-run
    snapshot on every exit path -- clean or malformed).

    HONEST FRAMING: this is an independent, LLM-judge-backed re-check backed
    by a fail-open/fail-closed escalation policy -- not a deterministic gate.
    See this module's docstring.
    """
    wiki = Path(wiki)
    snapshot_dir = Path(snapshot_dir)
    try:
        result = check_retention(snapshot_dir, wiki, judge_fn=judge_fn)
    except Exception as exc:  # noqa: BLE001 -- any failure here is "grader unavailable", not evidence of loss
        return _grader_error_decision(
            wiki, escalation_threshold, f"{type(exc).__name__}: {exc}"
        )
    finally:
        shutil.rmtree(snapshot_dir, ignore_errors=True)

    if result.errored:
        errored_reasons = "; ".join(
            f"{p.page}: {p.error}" for p in result.pages if p.status == "errored"
        )
        return _grader_error_decision(wiki, escalation_threshold, errored_reasons)

    # Grader ran successfully (whether PASS or CONFIRMED_LOSS) -- reset the
    # counter. See record_grader_success()'s docstring.
    record_grader_success(wiki)

    if result.has_confirmed_loss:
        lost_desc = "; ".join(
            f'{page}: "{c.get("claim_quote", "")[:120]}"'
            for page, c in result.lost_claims
        )
        return RetentionGateDecision(
            action="block_confirmed_loss",
            message=(
                "claim-retention gate: SILENTLY_LOST claim(s) detected in the "
                f"re-write -- {lost_desc}"
            ),
        )

    return RetentionGateDecision(action="proceed", message="claim-retention gate: PASS")
