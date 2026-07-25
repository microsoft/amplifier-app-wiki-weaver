# pyright: reportMissingImports=false
"""Deterministic post-ingest index/overview consistency check (entry points lag content).

THE PROBLEM (verified on a real production corpus via git-history audit of 121
sync commits, not hypothesized): content pages compound every sync, but the
wiki's FRONT DOOR does not keep up. One repo's ``index.md`` was missing 6 of
its 26 pages for 16 days ACROSS 2 SYNCS, and its ``overview.md`` still asserted
the corpus "spans ... to 2026-07-08" after 11 pages were updated on 07-24. The
ingest prompt asks the agent to "Maintain index.md" (pipeline/synthesize.dot),
but nothing at runtime verifies it actually did -- an unindexed page is
invisible to every reader who enters through the front door.

WHAT THIS MODULE DOES (all deterministic -- zero LLM, zero network):

1. ``check_index_consistency()`` -- every top-level content page on disk must
   appear in ``index.md`` (by wikilink slug, alias-aware), and every index
   wikilink must point at an existing page.
     - MISSING pages get a MECHANICAL REPAIR: appended under a clearly-marked
       ``## Recently added (auto-indexed)`` section (no LLM), plus an advisory
       so the next LLM re-weave organizes them properly. A mechanically
       findable page today beats a beautifully organized one next week.
     - DEAD entries are advisory-only -- lines are NEVER silently deleted
       (a dead entry may be a typo whose intended target a human can fix).

2. ``check_overview_staleness()`` -- if ``overview.md`` carries an explicit
   coverage-date claim ("spans ... to YYYY-MM-DD") older than the newest
   content-page modification, that claim is actively misleading. Surfaced as
   an advisory here, AND wired into the overview re-weave gate as an extra
   failure signal (see ``wiki_weaver.reweave.grade_overview_with_consistency``)
   so the existing LLM re-weave pass refreshes the overview.

3. ``run_consistency_checks()`` -- the caller-facing orchestration, run once
   per full ingest at BOTH wiring sites where the overview re-weave gate runs
   (wiki_weaver/lib.py's drain and wiki_weaver/engine_runner.py's
   run_ingest()), BEFORE the re-weave so a repaired index.md feeds it.
   Advisories flow through the existing advisories channel (result.json /
   DrainReport.advisories), exactly like the retention + shrinkage checks.

FRESH-WIKI SKIP: a wiki with no ``index.md`` (first-time sync that converged
zero sources) has no front door to check -- the whole pass is a silent no-op,
mirroring ``reweave_overview_if_needed``'s skip case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .index import _body, _extract_links, _parse_frontmatter, _slug

__all__ = [
    "AUTO_INDEX_HEADING",
    "SPECIAL_PAGES",
    "IndexConsistencyResult",
    "OverviewStaleness",
    "ConsistencyOutcome",
    "check_index_consistency",
    "check_overview_staleness",
    "run_consistency_checks",
]

# The mechanical-repair section heading. Clearly marked as machine-appended so
# the next LLM re-weave knows these entries need proper thematic organization.
AUTO_INDEX_HEADING = "## Recently added (auto-indexed)"

# Structural entry-point pages -- never treated as "content pages" that must
# themselves be cataloged in index.md.
SPECIAL_PAGES = frozenset({"index.md", "overview.md"})

# Explicit coverage-date claim in overview prose, e.g.
#   "The corpus spans early experiments to 2026-07-08."
# Single-line by construction ([^\n]) -- a coverage claim is one sentence.
_COVERAGE_CLAIM_RE = re.compile(
    r"\bspans\b[^\n]*?\b(?:to|through)\s+(\d{4}-\d{2}-\d{2})",
    re.IGNORECASE,
)


def _content_pages(wiki: Path) -> list[Path]:
    """Top-level content pages: root ``*.md`` minus the structural entry points.

    Same root-glob scope every other deterministic check in this codebase uses
    (grading.no_duplicate_pages, retention.snapshot_pages): nothing under
    ``.wiki/``, ``_sources/``, ``_inbox/``, etc.
    """
    return sorted(p for p in wiki.glob("*.md") if p.name not in SPECIAL_PAGES)


def _read_text_or_empty(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class IndexConsistencyResult:
    """Outcome of one index-completeness pass.

    ``missing`` is the content-page filenames that were absent from index.md
    (and, when ``repaired`` is True, have now been mechanically appended).
    ``dead_entries`` is index wikilink slugs resolving to no existing page --
    surfaced only, never deleted.
    ``skipped`` is the fresh-wiki case (no index.md to check).
    """

    missing: list[str] = field(default_factory=list)
    dead_entries: list[str] = field(default_factory=list)
    repaired: bool = False
    skipped: bool = False


def check_index_consistency(
    wiki: str | Path, *, repair: bool = True
) -> IndexConsistencyResult:
    """Verify index.md catalogs every content page; mechanically repair gaps.

    Membership is alias-aware in BOTH directions, reusing index.py's own slug
    and frontmatter helpers (single source of truth -- no new link semantics
    invented here):
      - a page counts as indexed when index.md links to its slug OR to any
        alias its frontmatter declares;
      - an index link is dead only when it matches no page slug AND no
        declared alias of any page.

    Repair (``repair=True``, the default) appends each missing page as a
    ``- [[stem]]`` bullet under ``AUTO_INDEX_HEADING`` -- inserted directly
    after the heading when a prior run already created it (never a duplicate
    heading), created at end-of-file otherwise. Dead entries are NOT touched.
    """
    wiki = Path(wiki)
    index_path = wiki / "index.md"
    if not index_path.is_file():
        return IndexConsistencyResult(skipped=True)

    index_text = _read_text_or_empty(index_path)
    link_slugs = set(_extract_links(index_text))

    pages = _content_pages(wiki)
    # slug -> page, plus alias -> page (mirrors index.py build_indexes' alias
    # collection; scalar-or-list handling identical).
    accepted: dict[str, str] = {}  # slug or alias -> page filename
    titles: dict[str, str] = {}  # page filename -> frontmatter title
    for p in pages:
        text = _read_text_or_empty(p)
        fm = _parse_frontmatter(text)
        accepted[_slug(p.stem)] = p.name
        raw_aliases = fm.get("aliases", [])
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        for alias in raw_aliases:
            accepted[_slug(str(alias))] = p.name
        title = fm.get("title")
        if isinstance(title, str) and title.strip():
            titles[p.name] = title.strip()

    # Special pages are legitimate link targets too (index may link overview).
    for name in SPECIAL_PAGES:
        if (wiki / name).is_file():
            accepted[_slug(Path(name).stem)] = name

    indexed_pages = {accepted[s] for s in link_slugs if s in accepted}
    missing = [p.name for p in pages if p.name not in indexed_pages]
    dead_entries = sorted(s for s in link_slugs if s not in accepted)

    repaired = False
    if missing and repair:
        bullets = []
        for name in missing:
            stem = Path(name).stem
            title = titles.get(name, "")
            if title and _slug(title) != _slug(stem):
                bullets.append(f"- [[{stem}]] -- {title}")
            else:
                bullets.append(f"- [[{stem}]]")
        bullet_block = "\n".join(bullets)

        if AUTO_INDEX_HEADING in index_text:
            # Prior run already created the section: insert the new bullets
            # directly after the heading line (no duplicate heading).
            head_pos = index_text.index(AUTO_INDEX_HEADING)
            line_end = index_text.find("\n", head_pos)
            if line_end == -1:
                index_text = index_text + "\n" + bullet_block + "\n"
            else:
                index_text = (
                    index_text[: line_end + 1]
                    + "\n"
                    + bullet_block
                    + "\n"
                    + index_text[line_end + 1 :]
                )
        else:
            if index_text and not index_text.endswith("\n"):
                index_text += "\n"
            index_text += (
                f"\n{AUTO_INDEX_HEADING}\n\n"
                "<!-- Appended mechanically by the index-consistency check; "
                "the next re-weave should organize these under proper "
                "thematic sections. -->\n\n"
                f"{bullet_block}\n"
            )
        index_path.write_text(index_text, encoding="utf-8")
        repaired = True

    return IndexConsistencyResult(
        missing=missing, dead_entries=dead_entries, repaired=repaired
    )


@dataclass
class OverviewStaleness:
    """Outcome of one overview coverage-date staleness pass.

    ``stale`` is True when overview.md carries an explicit "spans ... to
    YYYY-MM-DD" claim whose (newest) claimed end date is OLDER than the newest
    content-page modification date. ``message`` is advisory/gate-ready prose.
    """

    stale: bool = False
    claimed_date: str | None = None
    newest_page_date: str | None = None
    message: str = ""


def check_overview_staleness(wiki: str | Path) -> OverviewStaleness:
    """Flag overview.md coverage-date claims older than the newest page change.

    Deterministic and free: regex scan of overview.md's body for
    "spans ... to/through YYYY-MM-DD" claims, compared (at date granularity)
    against the newest content-page mtime. When several claims exist, the
    NEWEST claimed end date is used -- only a claim that lags ALL of the
    wiki's recent activity is flagged (conservative, no false alarms from an
    intentionally-historical sentence alongside a current one).

    No overview.md, no claim, or no content pages -> not stale (this check
    only ever judges an explicit, checkable assertion -- it never guesses).
    """
    wiki = Path(wiki)
    overview_text = _read_text_or_empty(wiki / "overview.md")
    if not overview_text:
        return OverviewStaleness()

    claims = _COVERAGE_CLAIM_RE.findall(_body(overview_text))
    valid_claims: list[str] = []
    for c in claims:
        try:
            date.fromisoformat(c)
        except ValueError:
            continue
        valid_claims.append(c)
    if not valid_claims:
        return OverviewStaleness()

    pages = _content_pages(wiki)
    if not pages:
        return OverviewStaleness()
    newest = max(
        datetime.fromtimestamp(p.stat().st_mtime).date().isoformat() for p in pages
    )
    claimed = max(valid_claims)  # ISO dates compare lexicographically

    if claimed >= newest:
        return OverviewStaleness(claimed_date=claimed, newest_page_date=newest)

    return OverviewStaleness(
        stale=True,
        claimed_date=claimed,
        newest_page_date=newest,
        message=(
            f"overview.md claims the corpus spans to {claimed}, but the newest "
            f"content-page modification is {newest} -- the coverage claim is "
            "stale and misleads readers about how current the wiki is"
        ),
    )


@dataclass
class ConsistencyOutcome:
    """Everything a caller needs after the full post-ingest consistency pass.

    ``advisory_signals`` are ``(gate_name, message)`` pairs ready for the
    advisories channel (same shape as retention.RetentionChecksOutcome's).
    All signals are ADVISORY-ONLY by design -- they never block, even under
    WIKI_WEAVER_ENFORCE_GATES=1 (the index repair is already applied
    mechanically; the staleness signal routes to the overview re-weave gate
    instead, which owns its own bounded-retry/fail-loud contract).
    ``skipped`` is the fresh-wiki case (no index.md).
    """

    advisory_signals: list[tuple[str, str]] = field(default_factory=list)
    index: IndexConsistencyResult = field(default_factory=IndexConsistencyResult)
    staleness: OverviewStaleness = field(default_factory=OverviewStaleness)
    skipped: bool = False


def run_consistency_checks(wiki: str | Path) -> ConsistencyOutcome:
    """Full deterministic index/overview consistency pass for one ingest run.

    Run this BEFORE ``reweave_overview_if_needed`` at each of its wiring
    sites: the mechanical index repair must land first so the re-weave (which
    synthesizes overview.md FROM index.md) sees the complete catalog, and the
    staleness signal must be measured against the PRE-re-weave overview.

    Fail-soft throughout: observability must never break the run -- any
    internal error becomes a printed WARN, never an exception to the caller.
    """
    wiki = Path(wiki)
    if not (wiki / "index.md").is_file():
        # Fresh/empty wiki -- nothing to check, mirroring the re-weave gate's
        # own skip case (see reweave_overview_if_needed).
        return ConsistencyOutcome(
            skipped=True, index=IndexConsistencyResult(skipped=True)
        )

    signals: list[tuple[str, str]] = []

    index_result = IndexConsistencyResult(skipped=True)
    try:
        index_result = check_index_consistency(wiki)
    except Exception as exc:  # noqa: BLE001 -- observability must never break the run
        print(f"WARN: index-consistency check could not run: {exc}")

    if index_result.missing:
        signals.append(
            (
                "index-consistency",
                (
                    "index-consistency check (ADVISORY -- deterministic repair "
                    f"applied): index.md was missing {len(index_result.missing)} "
                    f"page(s): {', '.join(index_result.missing)} -- mechanically "
                    f"appended under '{AUTO_INDEX_HEADING.lstrip('# ')}'; the "
                    "next re-weave should organize them properly"
                ),
            )
        )
    if index_result.dead_entries:
        signals.append(
            (
                "index-consistency",
                (
                    "index-consistency check (ADVISORY -- nothing deleted): "
                    f"index.md contains {len(index_result.dead_entries)} dead "
                    "entry(ies) pointing at no existing page: "
                    f"{', '.join(index_result.dead_entries)} -- review and "
                    "remove or fix these lines manually"
                ),
            )
        )

    staleness = OverviewStaleness()
    try:
        staleness = check_overview_staleness(wiki)
    except Exception as exc:  # noqa: BLE001 -- observability must never break the run
        print(f"WARN: overview-staleness check could not run: {exc}")

    if staleness.stale:
        signals.append(
            (
                "overview-staleness",
                (
                    "overview-staleness check (ADVISORY): "
                    f"{staleness.message} -- also counted as a failure by the "
                    "overview re-weave gate (OV3) so the LLM pass refreshes it"
                ),
            )
        )

    return ConsistencyOutcome(
        advisory_signals=signals, index=index_result, staleness=staleness
    )
