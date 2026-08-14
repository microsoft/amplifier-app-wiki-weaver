"""Person-sensitivity gate (KNOWN_ISSUES.md #7).

ALL NAMES AND CONTENT HERE ARE SYNTHETIC (AGENTS.md: "Test fixtures are
synthetic by design"). The people, projects, and incidents below do not
exist.

THIS FILE IS DELIBERATELY BALANCED. Half of it proves the gate FIRES on
characterization of a person; the other half proves it stays SILENT on
blunt technical criticism of someone's work. The second half is not
decoration -- a gate that strips every mention of a colleague is not a fix,
it is the opposite failure, and KNOWN_ISSUES.md #7 says so explicitly:
"Blunt technical criticism must survive ... that is the record the wiki
exists to preserve."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wiki_weaver.ingest.person_check import (
    _PERSON_STATE_MARKERS,
    _SENTENCE_SPLIT_RE,
    _UNCONDITIONAL_MARKERS,
    _name_pattern,
    build_work_anchors,
    find_person_sensitivity,
    is_private_source,
    new_lines,
    parse_participants,
)
from wiki_weaver.ingest.validate import main as validate_main

# --- synthetic corpus -------------------------------------------------------

GROUP_SOURCE = "041-Platform Weekly — Sync.md"
PRIVATE_SOURCE = "042-1on1 — Checkpoint.md"

GROUP_TRANSCRIPT = """# Transcript: Platform Weekly — Sync

Duration: 0:48:10
Speakers: Amara Okonkwo, Zoë Ferreira-Lund, Bjørn Ødegård, Unknown, @1
Chat type: Meeting
Attendees: Amara Okonkwo, Zoë Ferreira-Lund, Bjørn Ødegård, Huddle Room 2 (8), Unknown

---

[0:00:05] Amara Okonkwo: The snapshot reader still isn't in the hot path.

[0:00:22] Bjørn Ødegård: I want a kill switch before this goes near prod-west.
"""

PRIVATE_TRANSCRIPT = """# Transcript: 1on1 — Checkpoint

Duration: 0:29:03
Speakers: Amara Okonkwo, Kwabena Osei
Chat type: Meeting
Attendees: Amara Okonkwo, Kwabena Osei

---

[0:00:04] Amara Okonkwo: Let's talk about how the quarter went.

[0:01:11] Kwabena Osei: Sure.
"""

INDEX_BASE = """---
title: Index
type: index
---

# Index

- [[snapshot-reader|Snapshot Reader]]
"""

PAGE_HEADER = """---
title: Snapshot Reader
type: concept
sources: [{sources}]
---

# Snapshot Reader

"""


def build_corpus(wiki_root, *, page_body: str, sources: str = GROUP_SOURCE):
    """A structurally clean, git-committed wiki, then ONE new page section.

    Baseline is committed first so ``find_person_sensitivity``'s HEAD-diff
    scoping sees exactly the appended body as new -- the same measurement
    the real pipeline makes after ``weave``/``write_gap_page`` writes.
    """
    wiki_root.add_source(GROUP_SOURCE, GROUP_TRANSCRIPT)
    wiki_root.add_source(PRIVATE_SOURCE, PRIVATE_TRANSCRIPT)
    wiki_root.add_page("index.md", INDEX_BASE)
    wiki_root.add_page("snapshot-reader.md", PAGE_HEADER.format(sources=sources) + "Baseline paragraph.\n")
    wiki_root.init_git()
    wiki_root.commit_all("baseline")
    wiki_root.add_page(
        "snapshot-reader.md",
        PAGE_HEADER.format(sources=sources) + "Baseline paragraph.\n\n" + page_body,
    )
    return wiki_root


def findings(wiki_root) -> list[str]:
    return find_person_sensitivity(wiki_root.root, wiki_root.root / "wiki", wiki_root.root / "sources")


# ===========================================================================
# DIRECTION 1 -- characterization of a PERSON must be caught
# ===========================================================================


def test_catches_capability_characterization_of_absent_person(wiki_root, git_available):
    """Kwabena is in the corpus roster but did NOT attend the group meeting
    this page cites -- the exact shape of the defect: characterized while
    absent, with no chance to answer back."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Kwabena Osei has been struggling since the reorg.\n")
    out = findings(wiki_root)
    assert any("Kwabena Osei" in f for f in out), out
    assert any("person-sensitivity" in f for f in out)


def test_catches_health_disclosure(wiki_root, git_available):
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Kwabena Osei was diagnosed last spring and has been on leave.\n")
    out = findings(wiki_root)
    assert any("diagnosed" in f or "on leave" in f for f in out), out


def test_catches_personnel_content_even_with_work_anchor(wiki_root, git_available):
    """UNCONDITIONAL markers are not exemptible. A sentence dense with work
    vocabulary still fires when the material is a comp/leveling matter --
    there is no reading of that which is criticism of work."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body=("Kwabena Osei is not getting promoted this cycle despite the migration and the API rewrite.\n"),
    )
    out = findings(wiki_root)
    assert any("Kwabena Osei" in f for f in out), out


def test_catches_content_from_a_one_on_one_source_regardless_of_wording(wiki_root, git_available):
    """LAYER 0 -- the load-bearing arm. No vocabulary involved: the source
    has two participants, so anything newly derived from it stops here."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body="The team agreed the rollout order should follow the dependency graph.\n",
        sources=PRIVATE_SOURCE,
    )
    out = findings(wiki_root)
    assert any("1:1" in f for f in out), out
    assert any(PRIVATE_SOURCE in f for f in out), out


def test_self_disclosure_does_not_license_retention(wiki_root, git_available):
    """A one-off aside about oneself is treated like any other disclosure.
    Kwabena is a participant of the 1:1 he disclosed in, and it still stops:
    the keep requires the person to have made it part of their public work
    story, which is a judgment for the review gate, not for this code."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body="Kwabena Osei mentioned his own burnout while explaining the delay.\n",
        sources=PRIVATE_SOURCE,
    )
    out = findings(wiki_root)
    assert out, "self-disclosure inside a 1:1 must still require review"


# ===========================================================================
# DIRECTION 2 -- criticism of WORK must survive untouched
# This half matters as much as the half above.
# ===========================================================================


@pytest.mark.parametrize(
    "body",
    [
        "Bjørn Ødegård's design for the snapshot reader is wrong — it assumes a hot path that does not exist.\n",
        "Amara Okonkwo was wrong about the retry semantics; the queue already guarantees ordering.\n",
        "Zoë Ferreira-Lund pushed back hard on the ADR and objected to the whole rollout plan.\n",
        "Half of ADR-09 describes a system we no longer run. The schema is a mess.\n",
        "Kwabena Osei's proposal doesn't scale past a single region and should be rejected.\n",
        "This architecture is over-engineered and the latency budget is indefensible.\n",
        "Bjørn Ødegård disagreed with Amara Okonkwo about the kill switch; the thread is still open.\n",
    ],
)
def test_blunt_technical_criticism_passes_through_untouched(wiki_root, git_available, body):
    """THE PRODUCT-PRESERVING DIRECTION. Every one of these names a real
    colleague and is harshly critical -- of a design, a proposal, an
    architecture, a position. None of it is a characterization of a person,
    and the gate must stay completely silent."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body=body)
    assert findings(wiki_root) == []


def test_capability_language_about_work_is_exempted_by_a_work_anchor(wiki_root, git_available):
    """ "struggling with the migration" is a fact about work; the work anchor
    exemption is what separates it from "struggling since the reorg"."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Kwabena Osei struggled with the schema migration for most of the sprint.\n")
    assert findings(wiki_root) == []


def test_person_in_the_room_is_not_an_absent_third_party(wiki_root, git_available):
    """Participation is consent. Amara is a participant of the cited group
    source and can answer back, so Layer 1 does not fire for her."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Amara Okonkwo said she has been struggling to keep up lately.\n")
    assert findings(wiki_root) == []


# ===========================================================================
# Scoping -- must not wedge the pipeline
# ===========================================================================


def test_already_committed_content_does_not_re_fire(wiki_root, git_available):
    """A finding committed after review is in HEAD, so it is no longer new
    and never fires again. This is what stops one flagged page from failing
    validate for every subsequent source forever."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Kwabena Osei has been struggling since the reorg.\n")
    assert findings(wiki_root), "precondition: fires while uncommitted"
    wiki_root.commit_all("reviewed and accepted")
    assert findings(wiki_root) == []


def test_untouched_pages_are_never_scanned(wiki_root, git_available):
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Nothing notable.\n")
    wiki_root.commit_all("all clean")
    wiki_root.add_page("index.md", INDEX_BASE + "\n- unrelated edit\n")
    assert findings(wiki_root) == []


def test_no_git_is_skipped_silently_never_fabricated(wiki_root):
    wiki_root.add_source(GROUP_SOURCE, GROUP_TRANSCRIPT)
    wiki_root.add_page("snapshot-reader.md", PAGE_HEADER.format(sources=GROUP_SOURCE) + "Kwabena Osei was diagnosed.\n")
    assert findings(wiki_root) == []


def test_new_lines_is_exact_and_never_normalizes():
    old = "alpha\n  spaced  \nBjørn — Ødegård\n"
    new = "alpha\n  spaced  \nBjørn — Ødegård\nbrand new line\n"
    assert new_lines(old, new) == ["brand new line"]
    assert new_lines(None, "only\nlines") == ["only", "lines"]


# ===========================================================================
# Adversarial inputs -- this project has shipped thirteen broken checkers,
# several of them broken on exactly these characters.
# ===========================================================================


def test_participants_parse_across_both_corpus_shapes():
    named, headcount = parse_participants(GROUP_TRANSCRIPT)
    assert named == {"Amara Okonkwo", "Zoë Ferreira-Lund", "Bjørn Ødegård"}
    assert headcount == 4, "@1 is a distinct person; 'Unknown' is an unattributed-turn marker"
    assert not is_private_source(GROUP_TRANSCRIPT)

    stream = "# Chat: Guild\n\nChat type: Group\n\n---\n\n## 2026-05-21\n\n[20:24] **Mei-Lin Cho**\n> text\n"
    stream_named, _ = parse_participants(stream)
    assert stream_named == {"Mei-Lin Cho"}


def test_two_participant_source_is_private_and_rooms_do_not_count():
    assert is_private_source(PRIVATE_TRANSCRIPT)
    with_room = PRIVATE_TRANSCRIPT.replace(
        "Attendees: Amara Okonkwo, Kwabena Osei",
        "Attendees: Amara Okonkwo, Kwabena Osei, Huddle Room 2 (8)",
    )
    assert is_private_source(with_room), "a conference room is not a third participant"


def test_names_with_spaces_hyphens_and_non_ascii_match_whole_only(wiki_root, git_available):
    """Substring matching on names is how a checker fires on the wrong
    person forever. 'Amara' must not match inside 'Amaralyn', and 'Zoë'
    must not match inside 'Zoë Ferreira-Lund' as a separate person."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body=(
            "Amaralyn Systems has been struggling as a vendor.\n"
            "The Osei-Nakamura benchmark has been struggling to reproduce.\n"
        ),
    )
    assert findings(wiki_root) == []


def test_em_dash_and_unicode_body_still_fires_on_a_real_finding(wiki_root, git_available):
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body='## Résumé Review — Déjà Vu — "Quoted" Heading\n\nBjørn Ødegård was diagnosed — he is on leave.\n',
        sources=GROUP_SOURCE,
    )
    out = findings(wiki_root)
    assert any("Bjørn Ødegård" in f for f in out), out


def test_work_anchors_exclude_person_pages(wiki_root):
    wiki_root.add_page("kwabena-osei.md", "---\ntitle: Kwabena Osei\ntype: person\n---\n\n# Kwabena Osei\n")
    wiki_root.add_page("snapshot-reader.md", "---\ntitle: Snapshot Reader\ntype: concept\n---\n\n# Snapshot Reader\n")
    anchors = build_work_anchors(wiki_root.root / "wiki")
    assert "snapshot" in anchors and "reader" in anchors
    assert "kwabena" not in anchors and "osei" not in anchors


# ===========================================================================
# End-to-end through validate -- the real routing contract
# ===========================================================================


def test_validate_exits_nonzero_and_reports_the_finding(wiki_root, git_available, tmp_path):
    """Non-zero is what ingest.dot/synthesize.dot translate into
    ``structural_bad`` -> the existing bounded retry."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(wiki_root, page_body="Kwabena Osei has been struggling since the reorg.\n")
    out = tmp_path / "report.md"
    assert validate_main(["--wiki-root", str(wiki_root.root), "--out", str(out)]) == 1
    assert "person-sensitivity" in out.read_text(encoding="utf-8")


def test_validate_exits_zero_when_only_work_criticism_was_written(wiki_root, git_available, tmp_path):
    """The product-preserving direction, end to end: a page that calls a
    named colleague's design wrong validates clean and ships."""
    if not git_available:
        pytest.skip("git not available")
    build_corpus(
        wiki_root,
        page_body="Bjørn Ødegård's design for the snapshot reader is wrong — the hot path claim does not hold.\n",
    )
    out = tmp_path / "report.md"
    assert validate_main(["--wiki-root", str(wiki_root.root), "--out", str(out)]) == 0
    assert "person-sensitivity" not in out.read_text(encoding="utf-8")


# ===========================================================================
# Standing guards on the Layer 2 vocabulary itself.
#
# These exist because the adversarial pass caught a real one: bare
# "backfill" was in the NON-exemptible list and fired on 20 sentences of
# genuine data-backfill discussion in the real corpus. That is the
# over-redaction failure -- the gate quietly deleting the record the wiki
# exists to preserve -- and it is the same shape as the thirteen broken
# checkers this project has already shipped. Any future marker added to
# either list must survive both guards below.
# ===========================================================================

REAL_FIXTURES = ("real-largest-meeting-201kb.md", "real-largest-stream-236kb.md")

# Words that ARE the record: architecture disagreement, blunt judgment,
# someone being wrong about a design. If one of these ever appears in a
# marker list, the gate has become the opposite of a fix.
WORK_JUDGMENT_WORDS = (
    "wrong",
    "broken",
    "bad design",
    "doesn't scale",
    "does not scale",
    "pushed back",
    "push back",
    "disagreed",
    "disagree",
    "objected",
    "objection",
    "over-engineered",
    "overengineered",
    "regressed",
    "regression",
    "rejected",
    "indefensible",
    "mess",
)


def test_no_work_judgment_word_is_ever_a_marker():
    """Criticism of WORK must survive synthesis. A marker list containing
    any of these would strip exactly the material the wiki is for."""
    markers = set(_PERSON_STATE_MARKERS) | set(_UNCONDITIONAL_MARKERS)
    collisions = sorted(w for w in WORK_JUDGMENT_WORDS if any(w in m for m in markers))
    assert collisions == [], f"work-judgment vocabulary must never be a marker: {collisions}"


def test_no_marker_fires_on_real_corpus_technical_prose():
    """THE over-redaction regression guard.

    Runs every Layer 2 marker against the two largest real transcript
    fixtures and fails if any of them lands in a sentence that also names a
    corpus participant -- which is precisely the condition under which the
    gate would fire. Zero is the required answer: these fixtures are
    ordinary engineering discussion, and a gate that flags ordinary
    engineering discussion is a different failure, not a fix.
    """
    fixtures = Path(__file__).parent / "fixtures"
    corpus = [(f, (fixtures / f).read_text(encoding="utf-8")) for f in REAL_FIXTURES]

    roster: dict[str, object] = {}
    for _, text in corpus:
        for name in parse_participants(text)[0]:
            roster[name] = _name_pattern(name)

    offenders: list[str] = []
    for marker in (*_PERSON_STATE_MARKERS, *_UNCONDITIONAL_MARKERS):
        for fname, text in corpus:
            for line in text.splitlines():
                for sentence in _SENTENCE_SPLIT_RE.split(line):
                    if marker in sentence.lower() and any(p.search(sentence) for p in roster.values()):  # type: ignore[attr-defined]
                        offenders.append(f"{marker!r} in {fname}: {sentence.strip()[:110]}")
                        break
    assert offenders == [], "marker(s) fire on ordinary technical prose:\n  " + "\n  ".join(offenders[:10])


def test_backfill_regression_only_the_personnel_sense_is_a_marker():
    """The specific collision the adversarial pass caught: 126 raw hits in
    the real corpus, all about backfilling DATA."""
    markers = set(_PERSON_STATE_MARKERS) | set(_UNCONDITIONAL_MARKERS)
    assert "backfill" not in markers
    assert any(m.startswith(("backfill the", "backfill his")) for m in markers)
