"""wiki_weaver.synthesize.check_nav_pages -- fail-loud precondition gate for
the corpus-wide argument scan.

``scan_arguments`` (the LLM box immediately downstream in
``pipeline/synthesize.dot``) reads exactly ``wiki/index.md`` (the entity
roster, for the "does an existing page already cover this" cross-check) and
``.ai/source-arguments.md`` (``wiki_weaver.synthesize.extract_source_arguments``'
deterministic, condensed ARGUMENT view of every ``type: source`` wiki page --
iteration-4 fix; it replaced ``wiki/overview.md`` here because an
independent evaluation found the index+overview seed measurably
self-referential: both are organized per-entity/per-arrival-order, so an
argument with no entity and no shared vocabulary was invisible to a scan
seeded from them -- see ``pipeline/synthesize.dot``'s header for the full
account).

A gap-finder pointed at a missing/stub ``index.md``, or at a near-empty set
of source-level pages (so ``extract_source_arguments`` would have nothing
real to condense), would silently produce zero candidates and look exactly
like a clean, honest "no_gap" run -- indistinguishable from "the wiki is
genuinely well covered" without ever having read anything real. This
project has been bitten by this exact class of failure (a step running
against a stub/missing/near-empty input and silently producing nothing)
three times before -- this node exists so a fourth time fails LOUD, before
any model call, rather than silently.

Deliberately conservative: the body-length and page-count floors below are
small. A genuinely small corpus (a handful of sources) can still have a
short-but-real ``index.md`` and a handful of real source pages; the bar
here is "not empty/missing/stub/near-empty", never "large".

Usage:
    python3 -m wiki_weaver.synthesize.check_nav_pages --wiki-root <path> [--min-source-pages 4]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, int_or_default, parse_frontmatter
from wiki_weaver.synthesize.extract_source_arguments import extract_thesis, find_source_pages
from wiki_weaver.synthesize.rank_candidates import DEFAULT_MIN_SOURCES

# A stub nav page (frontmatter only, or a placeholder line or two) is a
# real, observed failure mode -- see module docstring. This floor exists to
# catch "empty"/"near-empty", not to demand a large wiki.
MIN_BODY_CHARS = 80

# The single nav page scan_arguments still reads directly (see module
# docstring -- wiki/overview.md is no longer part of its input).
REQUIRED_NAV_PAGE = "index.md"

# Reuses rank_candidates' own min_sources floor: if fewer than min_sources
# usable source-level pages exist, no candidate could ever clear that
# threshold downstream anyway, so demanding at least that many here (a
# "handful") ties this precondition to the same number rather than
# inventing a second, unrelated one.
DEFAULT_MIN_SOURCE_PAGES = DEFAULT_MIN_SOURCES

# Floor on the per-page EXTRACT (not the whole page body) below which a
# source-level page is treated as unusable for this scan -- matches
# extract_source_arguments.MIN_EXTRACT_CHARS's own reasoning, duplicated
# here as a literal so this module's precondition doesn't have to import a
# threshold constant meant to describe the *other* module's own output.
MIN_EXTRACT_CHARS = 30


def _body_chars(path: Path) -> int:
    """Body length (frontmatter stripped) in characters; ``-1`` if the file
    does not exist at all -- distinct from ``0`` (exists but empty body) so
    the two failure modes can be reported separately."""
    if not path.is_file():
        return -1
    text = path.read_text(encoding="utf-8")
    _meta, body = parse_frontmatter(text)
    return len(body.strip())


def count_usable_source_pages(wiki_dir: Path) -> int:
    """How many ``type: source`` wiki pages would actually contribute a
    real extract to ``.ai/source-arguments.md`` -- i.e. how many
    ``extract_source_arguments`` would count, without writing anything.
    This is the proxy ``check_nav_pages`` gates on, since
    ``extract_source_arguments`` itself runs AFTER this node."""
    return sum(
        1 for _path, _meta, body in find_source_pages(wiki_dir) if len(extract_thesis(body)) >= MIN_EXTRACT_CHARS
    )


def find_nav_problems(
    wiki_dir: Path, min_body_chars: int = MIN_BODY_CHARS, min_source_pages: int = DEFAULT_MIN_SOURCE_PAGES
) -> list[str]:
    """Return a list of problems with scan_arguments' actual inputs; empty
    list == both the required nav page and enough usable source-level
    pages exist."""
    problems: list[str] = []

    nav_path = wiki_dir / REQUIRED_NAV_PAGE
    chars = _body_chars(nav_path)
    if chars < 0:
        problems.append(f"wiki/{REQUIRED_NAV_PAGE} does not exist")
    elif chars < min_body_chars:
        problems.append(
            f"wiki/{REQUIRED_NAV_PAGE} has only {chars} body char(s) -- looks like a stub, not a real corpus view"
        )

    usable = count_usable_source_pages(wiki_dir)
    if usable < min_source_pages:
        problems.append(
            f"only {usable} source-level (type: source) wiki page(s) with a usable thesis extract found "
            f"-- need at least {min_source_pages}; refusing to run the corpus-wide argument scan against "
            "a near-empty source-page set"
        )

    return problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.check_nav_pages")
    parser.add_argument("--wiki-root", required=True)
    parser.add_argument(
        "--min-source-pages",
        required=False,
        default=str(DEFAULT_MIN_SOURCE_PAGES),
        type=int_or_default(DEFAULT_MIN_SOURCE_PAGES),
        help=f"floor on usable type:source wiki pages (default {DEFAULT_MIN_SOURCE_PAGES})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    problems = find_nav_problems(wr.wiki_dir, min_source_pages=args.min_source_pages)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(
            "refusing to run the corpus-wide argument scan against a missing/stub nav page or a "
            "near-empty source-page set -- run ingest.dot first so wiki/index.md and the per-source "
            "(type: source) wiki pages reflect the real corpus",
            file=sys.stderr,
        )
        print("nav_missing")
        return 0

    print(f"wiki/{REQUIRED_NAV_PAGE} present and populated; enough usable source-level pages found", file=sys.stderr)
    print("nav_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
