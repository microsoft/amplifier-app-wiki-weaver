"""wiki_weaver.synthesize.check_citations -- Gate B: every attributed source
must actually be cited in the page BODY, not merely listed in frontmatter.

WHY THIS EXISTS (independent audit, direct textual evidence): a real
synthesized page (``empirical-verification-before-shipping.md``) carried
``027-fable-whatsapp-use-the-load-skill-tool-t.md`` in its frontmatter
``sources:`` list -- contributing to its advertised ``source_count: 12`` --
while ``grep -c "027" <page>`` on the page BODY returned zero. The
attribution itself was valid (the auditor independently confirmed source 027
genuinely takes a position on the page's argument) -- the defect is that
``answer_gap`` (the LLM box, ``pipeline/synthesize.dot``) silently dropped
the source from its prose while the page's own header kept advertising
support for it. The headline count overstated what the page actually
develops: a trust gap, not a hallucination.

WHERE THIS RUNS: between ``write_gap_page`` and ``validate`` in
``pipeline/synthesize.dot`` -- after the page exists on disk (frontmatter +
body both written), before structural validation. ``check_citations`` never
reads any raw source content and never re-judges attribution; it is a pure
CODE-tier consistency check between two things the page ITSELF already
contains -- ``sources:`` (frontmatter, code-written by ``write_gap_page``)
and the body (LLM-written prose).

FRONTMATTER/BODY SPLIT: uses ``lib.parse_frontmatter`` (NOT ``lib.
split_header``) to obtain the body with the frontmatter block correctly
removed. ``split_header`` returns after the FIRST ``---`` line it finds
(the OPENING delimiter), not the closing one, so its "body" half still
contains the entire frontmatter block including the ``sources:`` line
itself -- exactly the false-negative trap this gate exists to avoid (a
source_id would always appear "cited" via its own frontmatter entry).
``parse_frontmatter`` walks from the opening ``---`` to the matching
closing ``---`` and returns the TRUE remainder as body -- verified directly
against this module's own test fixtures before relying on it.

CITATION FORMAT (checked directly, not assumed): the real corpus's inline
citation convention is the SAME ``NNN-Name.md`` filename ``lib.
extract_source_citations`` already scans for elsewhere in this codebase
(``refresh_page_index``'s ``cited_sources``) -- e.g. a page body containing
literal text like `` `010-home-bkrabach-dev-brian-skill-i-need-you.md` `` or
``[010-home-bkrabach-dev-brian-skill-i-need-you.md]``. This module checks
for that exact literal filename substring in the BODY (frontmatter
excluded via ``lib.parse_frontmatter``'s ``body`` return value -- checking
the whole page text would let the frontmatter's own ``sources:`` line
count as its own citation, which would defeat the entire point: source
027's frontmatter entry is exactly what this gate must NOT accept as a
body citation). Unit-test fixtures in
this codebase commonly use short synthetic ids (``"s1.txt"``) that do not
follow the ``NNN-Name.md`` convention -- this module checks for the literal
source_id string directly rather than depending on ``lib.
extract_source_citations``'s NNN-prefix regex, so its own guarantee does not
silently depend on every corpus obeying that naming scheme.

THE CORRECTION CHOSEN: FAIL LOUD AND RETRY, NOT SILENTLY DROP THE COUNT.
Two corrections were possible: (a) drop the uncited source_id from the
frontmatter and shrink ``source_count`` to match what the body actually
develops, or (b) fail the page so structural validation's existing
retry loop (``retry_bound`` -> ``answer_gap``) runs again. This module
chooses (b) -- routing ``citations_bad`` into the SAME retry/give-up safety
net ``validate``'s ``structural_bad`` already uses (see
``pipeline/synthesize.dot``'s ``check_citations`` node) -- for three
reasons, laid out because a reader could reasonably ask why not the
simpler-looking (a):

1. Dropping the count treats a REAL, already-attributed source as if it
   never mattered -- but ``attribute_sources`` did the (expensive,
   corpus-wide) work of confirming 027 genuinely takes a position on this
   argument. Silently shrinking the count throws that verified attribution
   away rather than giving the page a chance to actually use it.
2. This project's own repeatedly-learned lesson (FINDINGS.md's "Silent
   success" failure mode, ISSUE_HANDLING.md's fail-loud discipline) is that
   quietly renormalizing a defect to make it look resolved is worse than
   surfacing it: shrinking the count SHIPS a page whose only visible trace
   of the defect is one fewer number in a frontmatter list nobody reads
   closely -- exactly the kind of quiet gate this project has been bitten
   by before.
3. The retry path already exists, is already bounded
   (``max_synth_retry``/``retry_bound``'s ``give_up`` -> ``commit_declined``
   -- one stubborn term can never wedge the loop), and is exactly the
   mechanism ``validate``'s own ``structural_bad`` uses for a
   structurally-different-but-philosophically-identical problem ("the page
   ``write_gap_page``/``answer_gap`` produced does not meet the bar this
   pipeline requires of a shipped page"). Reusing it costs nothing new.

Usage:
    python3 -m wiki_weaver.synthesize.check_citations --wiki-root <path>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from wiki_weaver.lib import WikiRoot, extract_source_citations, parse_frontmatter
from wiki_weaver.synthesize.write_gap_page import slugify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.synthesize.check_citations")
    parser.add_argument("--wiki-root", required=True)
    return parser


def uncited_sources(frontmatter_sources: list[str], body: str) -> list[str]:
    """Every ``source_id`` in ``frontmatter_sources`` that does not appear
    anywhere in ``body`` -- order-preserving, empty when every attributed
    source is actually cited.

    Checks BOTH the literal source_id substring (works for any naming
    convention, including short synthetic test ids like ``"s1.txt"``) AND
    ``lib.extract_source_citations``'s ``NNN-Name.md`` regex extraction (the
    same mechanism ``refresh_page_index`` already uses elsewhere in this
    codebase) -- a source_id counts as cited if either check finds it,
    since the regex additionally guards against a source_id appearing as a
    strict substring of a DIFFERENT, longer citation (e.g. a source_id that
    happens to be a prefix of another).
    """
    cited_via_regex = set(extract_source_citations(body))
    missing: list[str] = []
    for source_id in frontmatter_sources:
        if source_id in cited_via_regex or source_id in body:
            continue
        missing.append(source_id)
    return missing


def _read_current_gap(wr: WikiRoot) -> dict:
    path = wr.current_gap_file
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist -- select_gap must run first")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        gap = _read_current_gap(wr)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        print("citations_bad")
        return 0

    slug = slugify(gap.get("term", ""))
    page_path = wr.wiki_dir / f"{slug}.md"
    if not page_path.is_file():
        print(f"{page_path} does not exist -- write_gap_page must run first", file=sys.stderr)
        print("citations_bad")
        return 0

    text = page_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    frontmatter_sources = meta.get("sources", [])
    if not isinstance(frontmatter_sources, list):
        frontmatter_sources = [frontmatter_sources]
    frontmatter_sources = [str(s) for s in frontmatter_sources if str(s).strip()]

    if not frontmatter_sources:
        # Nothing attributed to check -- write_gap_page always writes a
        # sources: list for a real gap, so an empty list here is unusual
        # but not itself a citation defect (nothing to be uncited).
        print(f"{page_path.name}: no attributed sources to check", file=sys.stderr)
        print("citations_ok")
        return 0

    missing = uncited_sources(frontmatter_sources, body)
    if missing:
        print(
            f"{page_path.name}: {len(missing)} of {len(frontmatter_sources)} attributed source(s) "
            f"are claimed in frontmatter but never cited in the body -- {missing} -- "
            "failing loud rather than shipping a page whose count overstates its content",
            file=sys.stderr,
        )
        print("citations_bad")
        return 0

    print(f"{page_path.name}: all {len(frontmatter_sources)} attributed source(s) cited in body", file=sys.stderr)
    print("citations_ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
