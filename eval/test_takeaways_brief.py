"""takeaways_brief: the self-contained PRE-write brief prepare_takeaways_brief
hands takeaways_gate -- source id, kind, segment position, a deterministic
(non-LLM) gist of the source's opening content, the read-bounded candidate
pages, and build_catalog's own "Predicted merge targets" section read
verbatim. FAIL LOUD (never fabricate) when the current-source breadcrumb is
missing/unreadable -- same contract review_brief.py already enforces via
read_current_source_id."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wiki_weaver.ingest import takeaways_brief
from wiki_weaver.lib import WikiRoot, atomic_append_jsonl, ensure_dir


def _seed_current_source(wiki_root, name: str = "s1.txt", content: str = "Sample source content.\n") -> WikiRoot:
    wiki_root.add_source(name, content)
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text(name, encoding="utf-8")
    return wr


def test_missing_current_source_fails_loud_no_fabricated_brief(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)  # .ai/ exists, but current_source.txt does not

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    out, err = capsys.readouterr()

    assert code == 1
    assert out.strip() == ""  # never a fabricated JSON brief on failure
    assert "current_source" in err


def test_empty_current_source_file_fails_loud(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wr.current_source_file.write_text("", encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out

    assert code == 1
    assert out.strip() == ""


def test_no_takeaways_brief_file_written_on_fail_loud_path(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    capsys.readouterr()

    assert code == 1
    assert not wr.takeaways_brief_file.is_file()


def test_gist_from_content_strips_header_and_gathers_the_opening_paragraph():
    # split_header's convention (see lib.split_header): a leading metadata
    # block of key: value lines terminated by ONE bare "---" line -- not a
    # doubly-fenced YAML frontmatter block. Unlike the original
    # first-sentence-only gist, gist_from_content now carries the whole
    # qualifying paragraph (both sentences here) -- a single sentence
    # proved too thin a signal (see module docstring's LIVE FAILURE).
    content = "kind: article\n---\n\nThis is the real opening sentence. This is a second sentence.\n"
    assert takeaways_brief.gist_from_content(content) == "This is the real opening sentence. This is a second sentence."


def test_gist_from_content_skips_headings():
    content = "# A Heading\n\nActual prose starts here. More text follows.\n"
    assert takeaways_brief.gist_from_content(content) == "Actual prose starts here. More text follows."


def test_gist_from_content_handles_empty_body():
    assert "no readable body" in takeaways_brief.gist_from_content("kind: article\n---\n\n")


def test_gist_from_content_skips_single_word_fragments_as_not_substantive():
    """A line with too few real words to be a sentence (and no terminal
    punctuation) is treated the same as nav chrome -- never emitted as
    though it were the source's thesis (see module docstring's fail-loud
    contract)."""
    content = "kind: article\n---\n\nHi.\n"
    assert "no readable body" in takeaways_brief.gist_from_content(content)


def test_gist_is_capped_and_ellipsized():
    # A real, many-word sentence repeated until it exceeds MAX_GIST_CHARS --
    # exercises the truncation/ellipsis mechanic without relying on a
    # single 500-character non-word, which the new prose heuristic
    # correctly treats as not a sentence at all (see the
    # skips_single_word_fragments test above).
    long_sentence = "This is a long real sentence with plenty of actual words in it. " * 12
    gist = takeaways_brief.gist_from_content(long_sentence)
    assert len(gist) <= takeaways_brief.MAX_GIST_CHARS
    assert gist.endswith("\u2026")


def test_gist_truncation_prefers_a_word_boundary_over_a_mid_word_cut():
    long_sentence = "This is a long real sentence with plenty of actual words in it. " * 12
    gist = takeaways_brief.gist_from_content(long_sentence)
    body = gist[: -len("\u2026")]
    # A word-boundary cut never leaves a trailing space before the
    # ellipsis (rstrip'd away) and never severs a repeated word's spelling
    # mid-way -- the text right before the ellipsis is a real word ending
    # from the source sentence, not an arbitrary character offset.
    assert not body.endswith(" ")
    assert body.split()[-1] in long_sentence.split()


def test_gist_skips_medium_paywall_banner_and_finds_real_content():
    """THE live defect this fixes (see module docstring's LIVE FAILURE):
    a Medium-style export's first non-heading, non-frontmatter body line
    is the "Member-only story" paywall banner, followed by nav chrome
    (byline, avatar, reaction counts, "min read", relative timestamp)
    before the real opening paragraph. The gist must skip ALL of that and
    surface the real paragraph -- never the banner itself."""
    content = (
        '---\ntitle: "Some Title"\nauthor: "Some Author"\n---\n\n'
        "# Some Title\n\n"
        "Member-only story\n\n"
        "# Some Title\n\n"
        "## A subtitle that summarizes the piece\n\n"
        "[Some Author](https://example.com/@author)\n\n"
        "5 min read\n\n"
        "\u00b7\n\n"
        "2 days ago\n\n"
        "--\n\n"
        "1\n\n"
        "Listen\n\n"
        "Share\n\n"
        "More\n\n"
        "Press enter or click to view image in full size\n\n"
        "![]()\n\n"
        "This is the real opening paragraph that argues something substantive about the topic.\n"
    )
    gist = takeaways_brief.gist_from_content(content)
    assert "member-only story" not in gist.lower()
    assert "min read" not in gist.lower()
    assert "listen" not in gist.lower()
    assert "share" not in gist.lower()
    assert "press enter" not in gist.lower()
    assert "real opening paragraph that argues something substantive" in gist


def test_gist_strips_markdown_link_syntax_to_visible_text():
    """A URL never leaks into the gist (it carries no signal and, for a
    long enough link, previously exhausted the gathering budget before any
    real paragraph was ever reached -- see module docstring)."""
    content = (
        "[Free link](https://example.com/very/long/path/that/would/otherwise/dominate/the/budget) => "
        "This sentence has real content worth keeping around for the gist.\n"
    )
    gist = takeaways_brief.gist_from_content(content)
    assert "https://" not in gist
    assert "Free link" in gist
    assert "real content worth keeping" in gist


def test_gist_skips_segment_source_html_comment_marker():
    """Regression guard (found via live DTU verification): segment_source.py
    stamps a "<!-- wiki-weaver segment N/M (chars ...) -->" HTML comment as
    the FIRST line of current-segment-content.md. lib.split_header only
    strips up to the FIRST bare '---' line, so a doubly-fenced frontmatter
    block (this project's own real source files: key: value lines
    terminated by a SECOND '---') leaves the comment AND the frontmatter
    key:value lines in what split_header calls 'body'. The gist must skip
    past all of that to the real prose, or an answerer sees only the
    comment string and correctly refuses (the exact live failure this
    guards against)."""
    content = (
        "---\n"
        "<!-- wiki-weaver segment 1/1 (chars 0-9998 of 9998) -->\n"
        'title: "Some Title"\n'
        'author: "Some Author"\n'
        "---\n"
        "\n"
        "This is the real opening sentence of the article. More text follows.\n"
    )
    assert (
        takeaways_brief.gist_from_content(content)
        == "This is the real opening sentence of the article. More text follows."
    )


def test_brief_includes_source_kind_and_segment_position(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "042-onnx-runtime-notes.txt", "The ONNX Runtime accelerates inference.\n")
    wr.current_kind_file.write_text(json.dumps({"kind": "article"}), encoding="utf-8")
    wr.current_segment_file.write_text(json.dumps({"index": 2, "total": 5}), encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "042-onnx-runtime-notes.txt" in brief
    assert "kind: article" in brief
    assert "segment 2/5" in brief
    assert "ONNX Runtime accelerates inference" in brief


def test_brief_uses_segmented_content_not_raw_source_when_present(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "big.txt", "RAW FULL SOURCE TEXT, should not appear.\n")
    wr.current_segment_content_file.write_text("The bounded segment discusses caching strategies.\n", encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "caching strategies" in payload["takeaways_brief"]
    assert "RAW FULL SOURCE TEXT" not in payload["takeaways_brief"]


def test_brief_lists_candidate_pages_from_current_slice(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    wr.current_slice_file.write_text(
        json.dumps({"pages": ["wiki/index.md", "wiki/onnx-runtime.md"], "bm25_hits": []}),
        encoding="utf-8",
    )

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "wiki/index.md" in brief
    assert "wiki/onnx-runtime.md" in brief


def test_brief_reports_none_candidate_pages_honestly_when_slice_absent(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "none" in payload["takeaways_brief"]


def test_brief_carries_predicted_merge_targets_verbatim_from_catalog(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    ensure_dir(wr.ai_dir)
    catalog_text = (
        "# Wiki Catalog\n\n1 page(s) exist.\n\n"
        "## Predicted merge targets for this source (unconfirmed)\n\n"
        "- wiki/onnx-runtime.md \u2014 ONNX Runtime (3 sources): an inference engine\n"
    )
    wr.current_catalog_file.write_text(catalog_text, encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "Predicted merge targets for this source" in brief
    assert "onnx-runtime.md" in brief
    # Never the FULL catalog inventory -- only the predicted-targets tail.
    assert "Wiki Catalog" not in brief


def test_brief_reports_no_predicted_targets_honestly_when_catalog_has_none(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    ensure_dir(wr.ai_dir)
    wr.current_catalog_file.write_text("# Wiki Catalog\n\n0 page(s) exist.\n", encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "Predicted merge targets: none yet" in payload["takeaways_brief"]


def test_brief_reports_predicted_targets_unavailable_when_catalog_missing(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "Predicted merge targets: unavailable" in payload["takeaways_brief"]


def test_kind_falls_back_to_source_guess_when_current_kind_file_absent(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "kind: article" in payload["takeaways_brief"]  # read_source_kind's documented default


def test_output_is_exactly_one_json_line_on_success(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "s1.txt")
    takeaways_brief.main(["--wiki-root", str(wr.root)])
    out = capsys.readouterr().out
    assert len(out.strip().splitlines()) == 1


def test_writes_takeaways_brief_file_stamped_with_source_id(wiki_root, capsys):
    """THE delivery mechanism: takeaways_gate's actual consumer
    (ProxyInterviewer) never sees $takeaways_brief / stdout -- it reads
    takeaways_brief_file from disk instead, stamped with source_id so a
    stale read (wrong source) is detectable, never silently trusted."""
    wr = _seed_current_source(wiki_root, "042-onnx-runtime-notes.txt")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    stdout_payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert wr.takeaways_brief_file.is_file()
    file_payload = json.loads(wr.takeaways_brief_file.read_text(encoding="utf-8"))
    assert file_payload["source_id"] == "042-onnx-runtime-notes.txt"
    assert file_payload["stage"] == "takeaways_gate"
    assert file_payload["brief"] == stdout_payload["takeaways_brief"]


@pytest.mark.parametrize("pages", [[f"wiki/page-{i:02d}.md" for i in range(12)]])
def test_many_candidate_pages_truncated_with_more_count(wiki_root, capsys, pages):
    wr = _seed_current_source(wiki_root, "s1.txt")
    wr.current_slice_file.write_text(json.dumps({"pages": pages, "bm25_hits": []}), encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "+4 more" in brief


# ---------------------------------------------------------------------------
# E3: structure signal (extract_structure_signals / structure_from_content)
#
# THE live defect (see module docstring / task brief): on real source 005,
# gist_from_content's opening-paragraph gist was 100% faithful to its own
# contract and STILL misled every answerer -- the source's opening is
# adoption-stat noise ("crossed 165,000 GitHub stars... 2.5 million...
# 7 million"), while the source's real, specific contribution
# (per-tool permission scoping) is a bolded lead-in buried under a generic
# "Stage 5" heading, ten paragraphs in. These tests use a source SHAPED
# like 005 (numbered stage headings, a bolded lead-in naming the real
# contribution, an adoption-stat opening) -- not source 005's literal text --
# so the test guards the GENERAL mechanism, not a memorized fixture.
# ---------------------------------------------------------------------------

_SOURCE_005_SHAPED_CONTENT = (
    "---\n"
    'title: "How to Become an Expert"\n'
    "---\n\n"
    "# How to Become an Expert\n\n"
    "Member-only story\n\n"
    "# How to Become an Expert\n\n"
    "## A ten-stage path covering agent modes and multi-provider models.\n\n"
    "[byline](https://example.com/@author)\n\n"
    "16 min read\n\n"
    "\u00b7\n\n"
    "3 days ago\n\n"
    "--\n\n"
    "Listen\n\n"
    "Share\n\n"
    "More\n\n"
    "Press enter or click to view image in full size\n\n"
    "![]()\n\n"
    "ToolX crossed 165,000 GitHub stars in 2026 and became the most-used open-source "
    "coding agent by a wide margin, with monthly active developer counts reported "
    "anywhere from 2.5 million in February to over 7 million by June.\n\n"
    "## Stage 1: learn the CLI and work in small repositories first\n\n"
    "Install the tool and start small. Read every diff by hand before trusting it.\n\n"
    "## Stage 5: get fluent with the tools ToolX actually has access to\n\n"
    "Shell execution, file editing, search, and testing are the primitives.\n\n"
    "**Permission scoping per tool.** You don't have to grant blanket access. The "
    "config lets you enable or disable specific capabilities globally or per agent.\n\n"
    "## Stage 10: build your own tools and integrations\n\n"
    "The final stage is custom tools that reach into your own infrastructure.\n"
)


def test_structure_from_content_surfaces_the_real_contribution_not_the_adoption_stats():
    """THE fix this experiment builds: the structure signal must surface the
    source's real, specific engineering contribution (a bolded lead-in
    naming per-tool permission scoping) and the stage headings that carry
    the source's actual arc -- while the opening's adoption-stat noise
    (165,000 stars / 2.5-7 million developers) never leaks into it, because
    none of that text is a heading or a bold lead-in."""
    structure = takeaways_brief.structure_from_content(_SOURCE_005_SHAPED_CONTENT)
    assert "Permission scoping per tool" in structure
    assert "Stage 1: learn the CLI" in structure
    assert "Stage 5: get fluent" in structure
    assert "Stage 10: build your own tools" in structure
    assert "165,000" not in structure
    assert "2.5 million" not in structure
    assert "7 million" not in structure


def test_gist_still_carries_the_misleading_opening_unchanged():
    """The opening gist is NOT removed by this change (see module docstring:
    additive, not a replacement) -- it still reports the opening verbatim,
    adoption noise and all. It is the STRUCTURE signal's job to correct for
    this, not the gist's -- confirmed here so a future refactor doesn't
    accidentally start filtering the gist by content instead."""
    gist = takeaways_brief.gist_from_content(_SOURCE_005_SHAPED_CONTENT)
    assert "165,000" in gist or "GitHub stars" in gist


def test_build_brief_includes_both_opening_and_structure_lines_and_answerer_can_see_the_conflict(wiki_root, capsys):
    wr = _seed_current_source(wiki_root, "005-shaped.txt", _SOURCE_005_SHAPED_CONTENT)

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "What the OPENING looks like it's about" in brief
    assert "Structure across the WHOLE source" in brief
    assert "Permission scoping per tool" in brief  # the real contribution, now visible
    assert "GitHub stars" in brief  # the misleading opening, still visible for comparison


def test_structure_signals_are_fence_aware_code_comments_are_not_headings():
    """LIVE FAILURE THIS GUARDS AGAINST (verified against corpus-articles/011,
    /037, /041): a fenced code/config example containing '#'-prefixed
    comment lines (a Python or shell comment) must never be mistaken for a
    real markdown section heading."""
    content = (
        "kind: article\n---\n\n"
        "## Real section heading that matters\n\n"
        "Some real prose here that is long enough to count as a paragraph.\n\n"
        "```python\n"
        "# This is a code comment, not a heading\n"
        "# Another comment line that could be mistaken for one\n"
        "def handler():\n"
        "    pass\n"
        "```\n\n"
        "## Another real section heading worth noting\n"
    )
    structure = takeaways_brief.structure_from_content(content)
    assert "Real section heading that matters" in structure
    assert "Another real section heading worth noting" in structure
    assert "code comment" not in structure
    assert "Another comment line" not in structure


def test_structure_signals_dedupe_repeated_title_heading():
    """A Medium export commonly repeats the title as an H1 twice (once
    before, once after the paywall banner) -- the structure list must list
    it once, not twice."""
    content = (
        "kind: article\n---\n\n"
        "# The Same Title Every Time\n\n"
        "Member-only story\n\n"
        "# The Same Title Every Time\n\n"
        "## A unique subtitle that adds real information here\n"
    )
    structure = takeaways_brief.structure_from_content(content)
    assert structure.count("The Same Title Every Time") == 1


def test_structure_signals_strip_wrapping_emphasis_from_a_whole_heading():
    """A Medium 'deck' line is sometimes rendered as a heading wholly
    wrapped in emphasis (``## *...*``) -- see corpus-articles/008's real
    subtitle heading. The visible text must survive with the wrapping
    markup removed, not be dropped or left with stray asterisks."""
    content = (
        "kind: article\n---\n\n"
        "# OpenCode Is Powerful. That's Exactly the Problem.\n\n"
        "## *OpenCode is the free, open-source alternative to Claude Code, "
        "here's how I ran it safely using a sandbox instead of my laptop.*\n"
    )
    structure = takeaways_brief.structure_from_content(content)
    assert "sandbox instead of my laptop" in structure
    assert "*" not in structure.replace("\u2026", "")  # no stray emphasis markers leaked through


def test_structure_signals_reject_garbled_long_token_as_noise():
    """A botched Medium table export can collapse onto one bold-looking
    line with no real word breaks -- reject it as noise rather than
    surface it as though it were a genuine heading/lead-in (generalizable
    heuristic, not specific to any one article)."""
    content = (
        "kind: article\n---\n\n"
        "**LocalToolXOpenCodeRemoteSandboxDisposableEnvironmentAffectsMachine**\n\n"
        "## A perfectly normal heading with real words in it\n"
    )
    structure = takeaways_brief.structure_from_content(content)
    assert "LocalToolXOpenCodeRemoteSandboxDisposableEnvironmentAffectsMachine" not in structure
    assert "A perfectly normal heading with real words in it" in structure


def test_structure_from_content_fails_loud_when_no_headings_or_leadins_present():
    """FAIL LOUD, same discipline as gist_from_content's empty-body case:
    a source with no section headings and no bold lead-ins gets an
    explicit, honest statement -- never a silently-empty or fabricated
    structure line."""
    content = "kind: article\n---\n\nJust plain prose with no headings or bold text anywhere in it.\n"
    structure = takeaways_brief.structure_from_content(content)
    assert "no section headings" in structure.lower()


def test_structure_signals_skip_short_and_boilerplate_headings():
    """A heading that is itself nav chrome, or too short to carry a real
    idea, is filtered the same way gist scoring filters boilerplate prose
    lines -- a heading is not automatically signal just because it is a
    heading."""
    content = "kind: article\n---\n\n## Share\n\n## More\n\n## A real heading that actually says something useful\n"
    structure = takeaways_brief.structure_from_content(content)
    assert "Share" not in structure
    assert "A real heading that actually says something useful" in structure


def test_structure_many_items_truncated_with_more_count():
    headings = "\n\n".join(f"## Stage {i}: a heading with enough real words in it" for i in range(1, 20))
    content = f"kind: article\n---\n\n{headings}\n"
    structure = takeaways_brief.structure_from_content(content)
    assert "+" in structure and "more" in structure


def test_extract_structure_signals_returns_empty_list_for_plain_prose():
    body = "Just some plain prose here.\n\nAnd another paragraph, still no structure.\n"
    assert takeaways_brief.extract_structure_signals(body) == []


# ---------------------------------------------------------------------------
# F5 move 4: near-duplicate signal (find_near_duplicate_sources /
# near_duplicate_text)
#
# THE live defect this fixes (see module-level comment above
# MIN_NEAR_DUPLICATE_RATIO / GOAL-followups.md F5): a human driving the
# takeaways gate correctly recognized that source 011 was the SAME article
# as already-ingested source 010, republished with a ~2-byte body
# difference, and refused to let it count as independent corroboration.
# Three proxy runs given the same brief but no computed duplicate signal
# all missed it. These tests exercise the synthetic mechanism directly
# (fast, no external fixtures); the real-corpus tests further down replay
# the actual 010/011 pair and check for false positives across all 50 real
# articles.
# ---------------------------------------------------------------------------


def _ledger_source(wr: WikiRoot, source_id: str) -> None:
    """Record ``source_id`` as a FULLY-ledgered (already-ingested) source --
    the same durable state ``lib.ledgered_source_ids`` reads. A bare
    ``{"source_id": ...}`` line is enough: absent segment_index/
    segment_total default to 1/1 (see lib._segment_status_by_source)."""
    atomic_append_jsonl(wr.ledger_path, {"source_id": source_id})


_REPUBLISHED_BODY = (
    "## The Booking No One Approved\n\n"
    "This is a long-form article about a human-in-the-loop harness that let an agent book "
    "a flight nobody actually approved, and what the team changed afterward.\n\n"
    "## What went wrong\n\n"
    "The approval gate looked correct in review but never actually blocked the write path "
    "because of a race between two async handlers.\n\n"
    "## The fix\n\n"
    "A synchronous confirmation step was added before any external side effect, closing "
    "the race entirely.\n"
)


def test_find_near_duplicate_sources_flags_a_true_near_duplicate(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    # 010 and 011: same body, tiny byte-level differences (a URL id and a
    # date line) -- exactly the shape of the real republished pair.
    wiki_root.add_source("010-booking.md", "source: url-a\n---\n\n" + _REPUBLISHED_BODY)
    _ledger_source(wr, "010-booking.md")

    current_text = "source: url-b\n---\n\n" + _REPUBLISHED_BODY.replace("nobody actually approved", "no one approved")
    _header, current_body = takeaways_brief.split_header(current_text)

    hits = takeaways_brief.find_near_duplicate_sources(wr, "011-booking.md", current_body)

    assert len(hits) == 1
    sid, ratio = hits[0]
    assert sid == "010-booking.md"
    assert ratio >= takeaways_brief.MIN_NEAR_DUPLICATE_RATIO


def test_find_near_duplicate_sources_silent_for_genuinely_different_sources(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wiki_root.add_source(
        "001-typescript.md",
        "kind: article\n---\n\n## Why TypeScript helps\n\nStatic types catch mistakes at compile time.\n",
    )
    _ledger_source(wr, "001-typescript.md")

    _h, current_body = takeaways_brief.split_header(
        "kind: article\n---\n\n## Local LLMs on a laptop\n\nRunning a quantized model locally saves cost.\n"
    )

    hits = takeaways_brief.find_near_duplicate_sources(wr, "002-local-llms.md", current_body)
    assert hits == []


def test_find_near_duplicate_sources_excludes_current_source_id_from_comparison(wiki_root):
    """A source cannot be a duplicate of itself -- even if (unusually) its
    own id already appears in the ledger, it must never be compared against
    its own content and reported as a match."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wiki_root.add_source("011-booking.md", "source: url-b\n---\n\n" + _REPUBLISHED_BODY)
    _ledger_source(wr, "011-booking.md")

    _h, current_body = takeaways_brief.split_header("source: url-b\n---\n\n" + _REPUBLISHED_BODY)
    hits = takeaways_brief.find_near_duplicate_sources(wr, "011-booking.md", current_body)
    assert hits == []


def test_find_near_duplicate_sources_returns_empty_when_no_ledger_yet(wiki_root):
    """No prior sources ledgered at all (e.g. the very first source of a
    fresh run) -- nothing to compare against, not a failure."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    _h, current_body = takeaways_brief.split_header("kind: article\n---\n\nSome opening content.\n")
    assert takeaways_brief.find_near_duplicate_sources(wr, "001-first.md", current_body) == []


def test_find_near_duplicate_sources_fails_loud_when_ledgered_source_unreadable(wiki_root):
    """FAIL LOUD: a source_id recorded in the ledger but no longer present
    on disk (deleted/corrupted) must propagate an error -- never be
    silently treated as "no duplicate found." A broken check must not look
    identical to a clean one (see module docstring)."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    _ledger_source(wr, "missing-source.md")  # never actually written to sources/

    _h, current_body = takeaways_brief.split_header("kind: article\n---\n\nSome opening content.\n")

    with pytest.raises(OSError):
        takeaways_brief.find_near_duplicate_sources(wr, "current.md", current_body)


def test_near_duplicate_text_is_empty_string_when_nothing_flagged(wiki_root):
    wr = _seed_current_source(wiki_root, "s1.txt", "Some ordinary content.\n")
    assert takeaways_brief.near_duplicate_text(wr, "s1.txt", wr.sources_dir / "s1.txt") == ""


def test_near_duplicate_text_empty_when_current_source_file_absent(wiki_root):
    """Mirrors ``_segment_content``'s identical fallback: no raw source file
    backing this pass means nothing of the current source to compare --
    a real "nothing to check" case, not a failure."""
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    assert takeaways_brief.near_duplicate_text(wr, "ghost.md", wr.sources_dir / "ghost.md") == ""


def test_near_duplicate_text_reports_flagged_source_and_percentage(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wiki_root.add_source("010-booking.md", "source: url-a\n---\n\n" + _REPUBLISHED_BODY)
    _ledger_source(wr, "010-booking.md")
    current_path = wiki_root.add_source("011-booking.md", "source: url-b\n---\n\n" + _REPUBLISHED_BODY)

    text = takeaways_brief.near_duplicate_text(wr, "011-booking.md", current_path)

    assert "NEAR-DUPLICATE SIGNAL" in text
    assert "010-booking.md" in text
    assert "%" in text


def test_build_brief_includes_near_duplicate_line_when_flagged(wiki_root, capsys):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    wiki_root.add_source("010-booking.md", "source: url-a\n---\n\n" + _REPUBLISHED_BODY)
    _ledger_source(wr, "010-booking.md")
    wiki_root.add_source("011-booking.md", "source: url-b\n---\n\n" + _REPUBLISHED_BODY)
    wr.current_source_file.write_text("011-booking.md", encoding="utf-8")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    brief = payload["takeaways_brief"]
    assert "NEAR-DUPLICATE SIGNAL" in brief
    assert "010-booking.md" in brief


def test_build_brief_omits_near_duplicate_line_when_nothing_flagged(wiki_root, capsys):
    """Silence, not a fabricated "(none found)" string -- see
    near_duplicate_text's docstring: this line would otherwise appear on
    every ordinary source, which is the exact noise the task brief warns
    against."""
    wr = _seed_current_source(wiki_root, "s1.txt", "Some ordinary, unremarkable content.\n")

    code = takeaways_brief.main(["--wiki-root", str(wr.root)])
    payload = json.loads(capsys.readouterr().out.strip())

    assert code == 0
    assert "NEAR-DUPLICATE" not in payload["takeaways_brief"]


# ---------------------------------------------------------------------------
# Real-corpus verification: replays the actual 010/011 republished pair and
# checks for false positives across all 50 real articles. Guarded with
# skipif rather than a hard dependency -- corpus-articles/ lives one level
# above this repo in the dev workspace (see GOAL-followups.md), not inside
# it, so a checkout of this repo alone (CI, a fresh clone) legitimately
# won't have it. When it IS present (this workspace), the test actually
# runs against the real files and is real evidence, not a stand-in.
# ---------------------------------------------------------------------------

_REAL_CORPUS_DIR = Path("/home/bkrabach/dev/wiki-weaver-improvements/corpus-articles")


@pytest.mark.skipif(not _REAL_CORPUS_DIR.is_dir(), reason="real corpus-articles/ not present in this checkout")
def test_real_010_011_pair_is_flagged_and_no_false_positives_across_50_articles(wiki_root):
    wr = WikiRoot(wiki_root.root)
    ensure_dir(wr.ai_dir)
    real_files = sorted(_REAL_CORPUS_DIR.glob("*.md"))
    assert len(real_files) >= 50, f"expected the full real corpus, found {len(real_files)}"

    for f in real_files:
        wiki_root.add_source(f.name, f.read_text(encoding="utf-8"))
        _ledger_source(wr, f.name)

    republished_pair = {
        "010-Harness_Engineering_-_Human-in-the-Loop_The_Booking_No_One_A.md",
        "011-Harness_Engineering_-_Human-in-the-Loop_The_Booking_No_One_A.md",
    }
    assert republished_pair <= {f.name for f in real_files}, "expected real 010/011 republished pair in corpus"

    false_positives = []
    saw_the_real_pair_flagged = False
    for f in real_files:
        current_text = f.read_text(encoding="utf-8")
        _h, current_body = takeaways_brief.split_header(current_text)
        hits = takeaways_brief.find_near_duplicate_sources(wr, f.name, current_body)
        for sid, ratio in hits:
            if {f.name, sid} == republished_pair:
                saw_the_real_pair_flagged = True
            else:
                false_positives.append((f.name, sid, ratio))

    assert saw_the_real_pair_flagged, "the real 010/011 republished pair was not flagged"
    assert false_positives == [], f"unexpected near-duplicate hits among genuinely distinct sources: {false_positives}"
