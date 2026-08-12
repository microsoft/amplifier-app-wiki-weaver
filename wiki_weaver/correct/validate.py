"""wiki_weaver.correct.validate -- structural checks after re_derive.

``pipeline/CLI-CONTRACT.md``: "Same structural-check contract as
``ingest.validate``." Delegates directly to
``wiki_weaver.ingest.validate.main`` rather than duplicating the check
logic -- one implementation (broken links, schema conformance, orphan
pages, zero-touch/leak guards), two entry points, matching this codebase's
existing DRY precedent (e.g. ``review_brief.py``/``guidance_brief.py``
share a brief shape rather than each re-deriving it).

Usage:
    python3 -m wiki_weaver.correct.validate --wiki-root <path> \\
        --out .ai/validation-report.md
"""

from __future__ import annotations

from wiki_weaver.ingest.validate import main as _ingest_validate_main


def main(argv: list[str] | None = None) -> int:
    return _ingest_validate_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
