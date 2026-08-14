"""validate: deterministic structural checks. Exit code drives ingest.dot's
shell-level `&& printf structural_ok || printf structural_bad` wrapper --
see the flagged contract discrepancy in validate.py's module docstring."""

from __future__ import annotations

import pytest

from wiki_weaver.ingest import validate
from wiki_weaver.lib import WikiRoot

CLEAN_PAGE = """---
title: Index
type: index
---

# Index

- [[other-page|Other Page]]
"""

OTHER_PAGE = """---
title: Other Page
type: concept
---

# Other Page

Links back to [[index|Index]].
"""

BROKEN_LINK_PAGE = """---
title: Broken
type: concept
---

# Broken

See [[does-not-exist|Nowhere]].
"""

MISSING_SCHEMA_PAGE = """---
type: concept
---

# No title field
"""

ORPHAN_PAGE = """---
title: Orphan
type: concept
---

# Orphan

Nothing links to this page, and it links to nothing.
"""


def test_clean_wiki_exits_zero(wiki_root):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "No structural issues found." in report


def test_broken_link_exits_nonzero(wiki_root, capsys):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.add_page("broken.md", BROKEN_LINK_PAGE)
    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 1
    assert "broken link" in capsys.readouterr().err


def test_missing_required_frontmatter_field_exits_nonzero(wiki_root):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("no-title.md", MISSING_SCHEMA_PAGE)
    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 1


def test_stdout_is_empty_shell_wrapper_owns_the_sentinel(wiki_root, capsys):
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wr = WikiRoot(wiki_root.root)
    validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert capsys.readouterr().out == ""


def test_orphan_page_still_fails_validation(wiki_root, capsys):
    """Regression guard for the every-gap-declined incident (see
    wiki_weaver.synthesize.write_gap_page's ORPHAN FIX docstring): the fix
    for synthesize.dot's misapplied orphan check is to LINK a new page
    before it is validated, never to weaken the check itself. A page with
    genuinely zero incoming wikilinks -- one nothing was ever wired up to
    link to -- must still fail validation exactly as before."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.add_page("orphan.md", ORPHAN_PAGE)
    wr = WikiRoot(wiki_root.root)

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])

    assert code == 1
    err = capsys.readouterr().err
    assert "orphan: orphan.md has no incoming wikilinks" in err
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "orphan: orphan.md has no incoming wikilinks" in report


def test_duplicate_section_fails_validation(wiki_root, capsys):
    """Regression guard for the duplicate-section incident (see
    lib.find_duplicate_sections_in_page's docstring and
    docs/KNOWN_ISSUES.md): a page carrying the exact same ``##`` heading
    twice must fail structural validation and be folded into the SAME
    exit-code-driving issues list as broken links/orphans -- routing to
    the existing reweave_bound retry, not a silent pass. This is the
    fix: before this check existed, a page shaped exactly like this one
    (a real, byte-for-byte shape pulled from the affected corpus) reported
    "No structural issues found.\""""
    duplicate_page = (
        "---\ntitle: Signal Deck\ntype: source\n---\n\n"
        "# Signal Deck\n\n"
        "## Orchard Design for Signal Deck \u2014 Inbox-Based Architecture (2026-08-11)\n\n"
        "Renata Ossovski described a detailed workflow. (some-source.md)\n\n"
        "## Made-Team-App Onboarding \u2014 Repo Access Architecture (2026-08-11 to 2026-08-12)\n\n"
        "Tomas Berglund validated end-to-end retrieval. (some-source.md)\n\n"
        "## Orchard Design for Signal Deck \u2014 Inbox-Based Architecture (2026-08-11)\n\n"
        "Renata Ossovski described a detailed workflow. (some-source.md)\n"
    )
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("signal-deck.md", duplicate_page)
    wr = WikiRoot(wiki_root.root)

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])

    assert code == 1
    err = capsys.readouterr().err
    assert "duplicate section: signal-deck.md" in err
    assert "Orchard Design for Signal Deck" in err
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "duplicate section: signal-deck.md" in report
    # The genuinely unique heading in the same page must NOT be flagged as
    # a duplicate -- only the heading that actually repeats is named.
    assert not any("Made-Team-App Onboarding" in line for line in err.splitlines() if "duplicate section" in line)


def test_oversized_overview_gets_specific_actionable_guidance(wiki_root):
    """The size trip-wire (find_oversized_pages) must stay report-only --
    never fold into the exit code, never auto-split (module docstring's
    PAGE-SIZE TRIP-WIRE section) -- but its report must be ACTIONABLE for
    overview.md specifically: it is one of the two files scan_arguments
    (pipeline/synthesize.dot) reads on every corpus-wide argument scan, so
    unmonitored growth there risks degrading the gap-finder itself, not
    just wasting bytes."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.add_page(
        "overview.md",
        "---\ntitle: Overview\ntype: overview\n---\n\n# Overview\n\n" + ("filler " * 5000),
    )
    wr = WikiRoot(wiki_root.root)

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])

    # Still report-only: an oversized page alone must not fail validation.
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "overview.md" in report
    assert "ACTION NEEDED" in report
    assert "scan_arguments" in report
    assert "index.md" in report  # names the split-to-linked-pages option


def test_oversized_ordinary_page_gets_no_nav_specific_guidance(wiki_root):
    """An ordinary oversized content page (not index.md/overview.md) is
    still reported, but never gets the NAV-page-specific ACTION NEEDED
    guidance -- that guidance is about scan_arguments' bounded read, which
    only ever touches the two nav pages."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page(
        "other-page.md",
        OTHER_PAGE + ("filler " * 5000),
    )
    wr = WikiRoot(wiki_root.root)

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])

    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "other-page.md" in report
    assert "ACTION NEEDED" not in report


def test_empty_wiki_is_not_an_error(wiki_root):
    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0


def test_zero_touch_weave_fails_validation(wiki_root):
    """Regression (the 27-source silent-empty-weave incident, see
    validate.py's ZERO-TOUCH GUARD docstring section): a structurally clean
    wiki with a committed git history but NO changes since HEAD (i.e. weave
    ran and wrote nothing) must fail validation, not pass it. Without the
    fix this asserts False -- the wiki has no broken links, no missing
    frontmatter, no orphans, so the OLD validate reported 'No structural
    issues found' and exited 0 even though weave touched zero pages."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.init_git()
    wiki_root.commit_all("seed")  # baseline HEAD -- nothing changes after this

    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 1
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "zero-touch" in report


def test_zero_touch_check_skipped_without_git(wiki_root):
    """When the wiki_root has no .git (git_available is false), the
    zero-touch check cannot be measured and must be silently skipped --
    never fabricated -- so a structurally clean, git-less wiki still
    passes exactly as it always has (test_clean_wiki_exits_zero above)."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)
    assert not (wr.root / ".git").exists()
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0


def test_root_level_content_page_fails_validation(wiki_root):
    """The write-path bug (see validate.py's WRITE-PATH LEAK GUARD): a
    content page that landed directly at wiki_root instead of wiki/ must
    fail validation loudly -- every other check here is scoped to
    wr.wiki_dir and would otherwise report a clean pass while the page sits
    one directory too high. Proves BOTH directions: plant the stray page
    and confirm failure, then remove it and confirm the same wiki passes."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)

    # Direction 1: a content page leaked to wiki_root -- must fail.
    stray = wr.root / "leaked-page.md"
    stray.write_text("---\ntitle: Leaked\ntype: concept\n---\n\n# Leaked\n", encoding="utf-8")
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 1
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "root-level leak" in report
    assert "leaked-page.md" in report

    # Direction 2: remove the stray page -- the same wiki must now pass.
    stray.unlink()
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "root-level leak" not in report


def test_log_md_at_root_does_not_trip_root_leak_check(wiki_root):
    """log.md is legitimate root-level scaffolding (the gist's append-only
    log at wiki_root -- see lib.ROOT_LEVEL_MD_ALLOWLIST) and must never be
    mistaken for a leaked content page."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)
    wr.log_path.write_text("## [2026-01-01] ingest | s1.txt\n\n1 page(s) touched.\n\n", encoding="utf-8")

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "root-level leak" not in report


def test_agents_md_at_root_does_not_trip_root_leak_check(wiki_root):
    """AGENTS.md is legitimate root-level scaffolding (written by
    init.persist -- see lib.ROOT_LEVEL_MD_ALLOWLIST), not a leaked page."""
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wr = WikiRoot(wiki_root.root)
    (wr.root / "AGENTS.md").write_text("# Wiki agent instructions\n", encoding="utf-8")

    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "root-level leak" not in report


def test_pages_touched_since_head_passes_zero_touch_check(wiki_root):
    """Positive control: once weave has actually modified a tracked page
    since HEAD, count_pages_touched is > 0 and the zero-touch finding must
    NOT fire -- only the pre-existing structural checks decide the exit
    code, unaffected by this guard."""
    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git not available")
    wiki_root.add_page("index.md", CLEAN_PAGE)
    wiki_root.add_page("other-page.md", OTHER_PAGE)
    wiki_root.init_git()
    wiki_root.commit_all("seed")

    # Simulate weave touching an existing page after HEAD.
    wiki_root.add_page("other-page.md", OTHER_PAGE + "\nA new sentence weave added.\n")

    wr = WikiRoot(wiki_root.root)
    code = validate.main(["--wiki-root", str(wr.root), "--out", ".ai/validation-report.md"])
    assert code == 0
    report = (wr.root / ".ai" / "validation-report.md").read_text(encoding="utf-8")
    assert "zero-touch" not in report
