"""wiki_weaver.ingest.person_check -- person-sensitivity gate for content
this pass NEWLY wrote (KNOWN_ISSUES.md #7).

THE DEFECT THIS CATCHES. A pre-share review of a 253-page wiki built from
164 team transcripts found ~40 passages that should not have circulated:
health and personal circumstance, formal performance-review content, candid
assessment of named individuals discussed in their ABSENCE, and people
outside the team discussed candidly. The pipeline had no mechanism to catch
any of it -- and worse, the synthesis layer MANUFACTURED some of it: a
concept page gathered scattered moments across three months into a named
"recurring failure pattern" with one engineer the subject of 3 of its 5
instances, a claim NO source transcript makes. Remediation was entirely
manual and recurs on every run.

WHY THIS IS CODE AND NOT A PROMPT. The wiki's own page recorded the team's
policy that 1:1 and performance content must never cross into team
knowledge; the pipeline recorded that policy and then violated it four
times. That is not an isolated result -- a stated instruction has failed as
a control in this codebase four separate, documented times:

  1. ``retract.py``'s module docstring: a human issued a standing
     instruction ("a page named after a PERSON is never correct in this
     wiki ... Delete joe-njenga.md"), then RESTATED it ("Standing
     instruction, still in force"). "It was still there at the end of the
     run." The fix that worked was a deterministic tool, not a clearer
     sentence.
  2. KNOWN_ISSUES.md #9: a prompt clarification was added alongside
     ``find_duplicate_sections_in_page`` and is described there as
     "best-effort mitigation, explicitly **not** the load-bearing fix."
  3. ``validate.py``'s ZERO-TOUCH GUARD: weave's prompt "always names an
     existing-or-new page to fold into", and a real run still committed 26
     sources with ``pages_touched: 0``.
  4. ``retention_check.py``: "an LLM judge asked to spot the same losses
     caught 0/3, at ~2x latency" -- the ~50-line deterministic detector
     caught 2/3.

So this module never asks a model anything, and never edits a page. It
DETECTS and routes into machinery that already exists.

WHAT IT REFUSES TO PRETEND. "Characterization of a person" is semantic, not
lexical, and KNOWN_ISSUES.md #7 is explicit that a keyword filter cannot
make this call: two keyword-driven passes each missed items the other
caught. This module is therefore built in three layers of DECLINING
confidence, and only the first two are deterministic:

  LAYER 0 -- PRIVATE-SOURCE PROVENANCE (fully deterministic, no vocabulary).
    A source with exactly two participants is structurally a private
    conversation. Any content newly written onto a page that cites such a
    source is flagged, regardless of what it says. This is the load-bearing
    arm: it does not depend on any word list, and it covers the category the
    manual review found the harm concentrated in (26 one-on-one and planning
    recordings).

  LAYER 1 -- ABSENT-PARTY NAMING (fully deterministic).
    "Participation is consent": someone characterized while absent is in a
    different position from someone in the room who can answer back. A
    person named in new content who is NOT a participant of any source that
    page cites is an absent third party. Roster and participation both come
    from the source headers/turn lines -- structure, not judgment.

    PRESENCE IS A MITIGATION, NOT A LICENCE, so Layer 1 gates only ONE of
    Layer 2's two vocabularies. Capability/performance/attitude language
    about someone in the room is left alone -- they were there and could
    answer back. Health, personnel, credential and interpersonal-conflict
    material fires whether or not the person was present: there is no room
    whose attendance list makes a diagnosis or a comp conversation into
    team knowledge.

  LAYER 2 -- PERSON-PREDICATE TRIGGER (ENUMERATED HEURISTIC -- has false
    negatives, and is documented as such in KNOWN_ISSUES.md #7).
    Layer 1 alone would fire on every legitimate mention of an absent
    colleague and destroy the product, so a finding additionally requires
    a predicate that attaches to the PERSON rather than to their WORK.

THE POLARITY FLIP THAT MAKES LAYER 2 SURVIVABLE. The keyword passes that
failed enumerated the UNBOUNDED side (every way a disclosure might be
worded); an unlisted term there is a silent ship. Layer 2 additionally
enumerates the BOUNDED side -- the corpus's own WORK vocabulary, read
straight off the wiki's non-person page titles -- and uses it as an
EXEMPTION. An unlisted work noun costs a false positive, which routes to a
bounded retry and then to human review. That fails in the safe direction.
The asymmetry is deliberate:

    performance / capability / attitude markers  -> exemptible by a work
        anchor ("struggling with the snapshot reader migration" is work
        talk; "struggling since the reorg" is not)
    personnel / health / credential markers      -> NEVER exemptible; there
        is no reading of "diagnosed", "on a PIP", or "didn't finish the
        degree" that is criticism of work

THE LINE THIS HOLDS. Criticism of WORK must survive -- architecture
disagreements, someone being wrong about a design, frustration at a system
-- that material is the entire reason the wiki is worth building. Layer 2's
vocabulary therefore contains NO work-judgment words at all: "wrong",
"broken", "bad design", "doesn't scale", "pushed back", "disagreed",
"objected" are absent by construction, so a sentence containing only those
cannot fire no matter who it names. Over-redaction is as much a failure as
under-redaction, and this module is calibrated to let blunt technical
criticism through untouched.

SELF-DISCLOSURE DOES NOT LICENSE RETENTION. A person mentioning their own
circumstance in passing is treated exactly like any other disclosure: Layer
2 does not exempt a speaker talking about themselves. The keep requires the
person to have independently made the material part of their public work
story, which is a human judgment made at the review gate this routes to --
not one this module makes.

SCOPED TO WHAT THIS PASS WROTE, so it cannot wedge the pipeline. Every other
whole-wiki check here is idempotent under retry (a broken link stays fixable
by reweaving). A person-sensitivity finding on a page written 200 sources
ago is NOT fixable by reweaving the current source, so scanning the whole
wiki would fail every subsequent source forever -- the pipeline would
quarantine everything and the gate would be turned off, which is how this
project has already shipped thirteen broken checkers. Scope is therefore
lines present in the working tree that were NOT in the page's HEAD blob
(same git measurement ``find_zero_touch`` and ``retention_check`` already
use). Consequences that fall out of that choice, all of them wanted:
  - content a reviewer already approved and committed stops firing forever,
    with no marker mechanism and no state file;
  - a finding is always attributable to the pass that is running now;
  - git unavailable -> skipped silently, exactly like ``find_zero_touch``:
    never fabricate a signal that cannot be measured.

WIRED WHERE BOTH RE-DERIVING PATHS ALREADY MEET. ``pipeline/ingest.dot``'s
``weave`` (autonomous, writes person-typed entity pages and re-argues
``overview.md``) and ``pipeline/synthesize.dot``'s ``answer_gap`` ->
``write_gap_page`` (compression across sources -- the arm that MANUFACTURED
the finding above) both route through ``wiki_weaver.ingest.validate``. Its
``issues`` list is the one chokepoint that covers both, so findings fold in
there and exit non-zero -> ``structural_bad`` -> the bounded retry that
already exists. Terminal behaviour, unchanged by this module: ingest ->
``reweave_bound`` give_up -> ``prepare_quarantine_brief`` -> human/proxy
review; synthesize -> ``retry_bound`` give_up -> ``commit_declined``, which
REVERTS the partial page. That is exactly KNOWN_ISSUES.md #7's "refuses to
synthesize ... without explicit review. Loud, not silent." No new
machinery, no warning-only path, no config flag to switch a safety gate off.

Usage (as a library -- ``wiki_weaver.ingest.validate`` is the only caller):
    from wiki_weaver.ingest.person_check import find_person_sensitivity
"""

from __future__ import annotations

import re
from pathlib import Path

from wiki_weaver.lib import (
    extract_source_citations,
    git_available,
    git_changed_wiki_files,
    git_show_head,
    list_wiki_pages,
    parse_frontmatter,
)

# ---------------------------------------------------------------------------
# Participation -- who was actually in the room (deterministic, from source
# headers and turn lines; the same two transcript shapes detect_kind.py
# already documents, plus the Speakers:/Attendees: header fields).
# ---------------------------------------------------------------------------

_HEADER_PARTICIPANT_RE = re.compile(r"^(?:Speakers|Attendees|Participants):\s*(.+)$", re.MULTILINE)

# "[0:00:04] Priya Raghunathan: text" / "[00:03] Hana Sasaki: text" -- the
# meeting turn shape. Anchored on the bracketed timestamp so ordinary prose
# containing a colon can never be mistaken for a speaker turn.
_MEETING_TURN_SPEAKER_RE = re.compile(r"^\[\d{1,2}(?::\d{2}){1,2}\]\s+([^\[\*\n:]{1,60}):\s", re.MULTILINE)

# "[23:26] **Devon Achebe**" -- the stream (group-chat export) turn shape.
_STREAM_TURN_SPEAKER_RE = re.compile(r"^\[\d{2}:\d{2}\]\s+\*\*([^*\n]{1,60})\*\*", re.MULTILINE)

# Header entries that are not a person: a room with its capacity
# ("Meeting Room 4 (6)"), the export's own placeholder ("Unknown"), and
# anonymized speaker labels ("@1", "@2"). Anonymized labels still COUNT
# toward the headcount (they are a human, just unnamed) but never enter the
# name roster -- naming rules cannot apply to a person with no name.
_ROOM_LIKE_RE = re.compile(r"\(\s*\d+\s*\)\s*$|\broom\b", re.IGNORECASE)
_ANONYMIZED_RE = re.compile(r"^@\w+$")
_PLACEHOLDER_NAMES = frozenset({"unknown", "unknown user", "guest", "n/a", ""})

# A private conversation, structurally: exactly this many participants.
# Not a heuristic about content -- a count of who was there.
PRIVATE_PARTICIPANT_COUNT = 2


def _clean_participant(raw: str) -> str:
    """One header entry -> a bare name, or "" if it is not a nameable person.

    Never calls ``.split()`` on the entry: this project has shipped a
    checker that split a name on whitespace and silently mismatched
    everything containing a space, and every real name here contains one.
    """
    name = raw.strip().strip(",").strip()
    if not name or name.lower() in _PLACEHOLDER_NAMES:
        return ""
    if _ANONYMIZED_RE.match(name) or _ROOM_LIKE_RE.search(name):
        return ""
    return name


def parse_participants(source_text: str) -> tuple[set[str], int]:
    """``(named participants, total headcount)`` for one source file.

    Names come from the ``Speakers:``/``Attendees:`` header fields when
    present and from turn lines otherwise (a chat export has no header
    field, only ``[HH:MM] **Name**`` turns), so both corpus shapes are
    covered by the same call.

    ``headcount`` counts anonymized ``@N`` labels -- distinct people, just
    unnamed -- so a four-person meeting with two anonymized speakers is NOT
    mistaken for a 1:1. It deliberately does NOT count the export's
    ``Unknown`` placeholder, which marks an unattributed turn rather than
    an additional person and is usually one of the named participants
    anyway. That choice errs toward classifying a source as private, which
    is the safe direction here: over-classifying costs one review,
    under-classifying ships 1:1 content.
    """
    named: set[str] = set()
    headcount_tokens: set[str] = set()

    for field in _HEADER_PARTICIPANT_RE.findall(source_text):
        for raw in field.split(","):
            token = raw.strip()
            if not token or token.lower() in _PLACEHOLDER_NAMES:
                continue
            if _ROOM_LIKE_RE.search(token):
                continue  # a room is not a participant, in either count
            headcount_tokens.add(token)
            cleaned = _clean_participant(token)
            if cleaned:
                named.add(cleaned)

    for pattern in (_MEETING_TURN_SPEAKER_RE, _STREAM_TURN_SPEAKER_RE):
        for raw in pattern.findall(source_text):
            cleaned = _clean_participant(raw)
            if cleaned:
                named.add(cleaned)
                headcount_tokens.add(cleaned)

    return named, len(headcount_tokens)


def is_private_source(source_text: str) -> bool:
    """A source with exactly ``PRIVATE_PARTICIPANT_COUNT`` participants.

    Deterministic and vocabulary-free -- this is Layer 0, and it holds even
    when Layer 2's enumerated vocabulary does not cover how something was
    worded.
    """
    _, headcount = parse_participants(source_text)
    return headcount == PRIVATE_PARTICIPANT_COUNT


def load_source_participation(sources_dir: Path) -> dict[str, tuple[set[str], bool]]:
    """``{source filename: (named participants, is_private)}`` for the corpus.

    Best-effort like every other measurement in this gate: an unreadable or
    absent ``sources/`` yields ``{}`` and the caller degrades to the checks
    that do not need it, rather than crashing or inventing participation.
    """
    participation: dict[str, tuple[set[str], bool]] = {}
    if not sources_dir.is_dir():
        return participation
    for path in sorted(sources_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        named, headcount = parse_participants(text)
        participation[path.name] = (named, headcount == PRIVATE_PARTICIPANT_COUNT)
    return participation


# ---------------------------------------------------------------------------
# Layer 2 vocabulary.
#
# READ THE ASYMMETRY BEFORE EDITING EITHER LIST. The two groups below are
# NOT interchangeable, and the difference is the whole design:
#
#   _PERSON_STATE_MARKERS  -- capability/performance/attitude. Exemptible by
#       a work anchor, because "struggling with the migration" is a fact
#       about work and "struggling since the reorg" is a fact about a person.
#   _UNCONDITIONAL_MARKERS -- personnel, health, personal circumstance,
#       credentials. NEVER exemptible: no work anchor turns a diagnosis, a
#       comp conversation, or an unannounced departure into criticism of work.
#
# WHAT MUST NEVER BE ADDED TO EITHER: work-judgment words. "wrong",
# "broken", "doesn't scale", "bad design", "pushed back", "disagreed",
# "objected", "over-engineered", "regressed" and their kin are the record
# this wiki exists to preserve. A sentence containing only those cannot fire
# here no matter whom it names, and that is a property to protect on every
# future edit -- adding one work-judgment word to these lists silently
# converts this gate from a safety mechanism into the over-redaction failure
# that destroys the product.
# ---------------------------------------------------------------------------

_PERSON_STATE_MARKERS = (
    "struggling",
    "struggled",
    "not ready",
    "isn't ready",
    "is not ready",
    "wasn't ready",
    "underperforming",
    "underperformed",
    "not delivering",
    "coasting",
    "mentally checked out",
    "disengaged",
    "unreliable",
    "careless",
    "sloppy",
    "hard to work with",
    "difficult to work with",
    "got defensive",
    "was defensive",
    "being defensive",
    "dismissive",
    "abrasive",
    "territorial",
    "not a strong engineer",
    "not strong enough",
    "weak performer",
    "low performer",
    "strong performer",
    "out of his depth",
    "out of her depth",
    "out of their depth",
    "in over his head",
    "in over her head",
    "in over their head",
    "can't be trusted",
    "cannot be trusted",
    "doesn't take feedback",
    "does not take feedback",
    "attitude problem",
    "a setback",
)

_UNCONDITIONAL_MARKERS = (
    # personnel
    "performance review",
    "performance improvement",
    "on a pip",
    "put on a pip",
    "calibration meeting",
    "performance calibration",
    "promotion",
    "promoted",
    "not getting promoted",
    "leveling",
    "level expectations",
    "career level",
    "compensation",
    "comp conversation",
    "pay band",
    "pay raise",
    "a raise",
    # NOT bare "backfill": measured 126 hits across the real corpus, 20 of
    # them in a sentence naming a colleague, every one of them about
    # backfilling DATA. Only the personnel sense belongs here.
    "backfill the role",
    "backfill his role",
    "backfill her role",
    "backfill their role",
    "backfill the position",
    "backfill the req",
    "headcount",
    "laid off",
    "layoff",
    "let go",
    "terminated",
    "resigning",
    "resignation",
    "quitting",
    "handing in notice",
    "moving off the team",
    "moved off the team",
    "reorg",
    "reorganization",
    "org change",
    "reporting to",
    "manages out",
    "managed out",
    "severance",
    "offer letter",
    "counteroffer",
    "interview loop",
    "hiring decision",
    "no-hire",
    # health and personal circumstance
    "diagnosed",
    "diagnosis",
    "adhd",
    "burnout",
    "burned out",
    "burnt out",
    "on leave",
    "medical leave",
    "sick leave",
    "parental leave",
    "therapy",
    "surgery",
    "hospital",
    "hospitalized",
    "chronic illness",
    "chronic condition",
    "chronic pain",
    "mental health",
    "anxiety",
    "depression",
    "medication",
    "divorce",
    "separation from his",
    "separation from her",
    "family situation",
    "personal situation",
    "personal circumstances",
    "caregiving",
    "custody",
    "bereavement",
    "funeral",
    "passed away",
    "pregnant",
    "pregnancy",
    "immigration status",
    "visa status",
    "financial trouble",
    # credentials
    "dropped out",
    "didn't finish the degree",
    "did not finish the degree",
    "no formal training",
    "self-taught",
    "no degree",
    "fake degree",
    "academic credentials",
    "fake credentials",
    "lacks the credentials",
    # interpersonal conflict that would embarrass a named person
    "blew up at",
    "yelled at",
    "shouted at",
    "went off on",
    "doesn't get along",
    "does not get along",
    "friction with",
    "personality conflict",
    "interpersonal conflict",
    "bad blood",
    "resents",
    "resentful",
    "undermining",
    "behind his back",
    "behind her back",
    "behind their back",
    "talking about him",
    "talking about her",
)

# Structural work vocabulary -- the floor under the corpus-derived anchors
# below. Deliberately generic: the corpus supplies its own domain nouns.
_STRUCTURAL_WORK_ANCHORS_RAW = """
    adr api architecture backend backlog benchmark branch bug build cache ci
    cli client cluster code commit compiler component config container
    contract dashboard database dependency deploy deployment design diagram
    doc docs documentation endpoint feature frontend gateway implementation
    incident index infra infrastructure integration interface issue job
    latency library linter loop merge migration model module monitoring
    optimization outage package parser patch pipeline pr proposal protocol
    prototype query queue refactor regression release repo repository request
    review rewrite roadmap rollout runtime scaling schema script sdk server
    service session spec spike sprint stack storage stream suite sync system
    table task test testing throughput ticket tooling transaction ui upgrade
    validation version workflow workstream
    """

_STRUCTURAL_WORK_ANCHORS = frozenset(_STRUCTURAL_WORK_ANCHORS_RAW.split())

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def build_work_anchors(wiki_dir: Path) -> frozenset[str]:
    """Work vocabulary = structural floor + the wiki's OWN non-person page
    titles and slugs.

    The corpus defines what counts as work talk, which is why this exemption
    can be enumerated at all: page titles are a bounded, self-maintaining
    list that grows with the wiki. ``type: person`` pages are excluded --
    a person's name is never evidence that a sentence is about work, and
    including it would punch a hole straight through Layer 2.
    """
    anchors = set(_STRUCTURAL_WORK_ANCHORS)
    if not wiki_dir.is_dir():
        return frozenset(anchors)
    for page_path in list_wiki_pages(wiki_dir):
        try:
            text = page_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _ = parse_frontmatter(text)
        if str(meta.get("type", "")).strip().lower() == "person":
            continue
        title = str(meta.get("title", "")).strip()
        for token in _WORD_RE.findall(f"{title} {page_path.stem}".lower()):
            if len(token) > 2:
                anchors.add(token)
    return frozenset(anchors)


# ---------------------------------------------------------------------------
# New-content extraction and sentence scanning
# ---------------------------------------------------------------------------

# Sentence break on terminal punctuation followed by whitespace. Applied
# per-line, so a heading or a bullet is its own unit and a break can never
# straddle a list item.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def new_lines(old_text: str | None, new_text: str) -> list[str]:
    """Lines present in ``new_text`` that were not anywhere in ``old_text``.

    Set difference on WHOLE, unmodified lines -- not a diff library, and
    nothing is normalized, stripped, or ``.split()`` on. A line that already
    existed at HEAD was already committed and reviewed; re-flagging it would
    wedge every later pass on this page. ``old_text is None`` (a page created
    this pass) means every line is new.
    """
    if old_text is None:
        return new_text.splitlines()
    previous = set(old_text.splitlines())
    return [line for line in new_text.splitlines() if line not in previous]


def _name_pattern(name: str) -> re.Pattern[str]:
    """Whole-name match tolerant of the names that actually occur.

    Explicit ``[\\w-]`` lookarounds rather than ``\\b``: a trailing ``\\b``
    after "Mei" would happily match inside "Mei-Lin", and a leading one
    would match "Ana" inside "Ana-Lucia". ``\\w`` is Unicode-aware for
    ``str`` patterns, so "Bjorn Odegard", "Adaeze Nwosu" and "Mei-Lin Cho"
    all behave. ``re.escape`` keeps periods and hyphens in a name literal.
    """
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def _referenced_names(sentence: str, roster: dict[str, re.Pattern[str]]) -> set[str]:
    return {name for name, pattern in roster.items() if pattern.search(sentence)}


def _first_name_index(full_names: set[str]) -> dict[str, str]:
    """First names that identify EXACTLY ONE person in the roster.

    "Priya has been struggling" must be catchable, but a first name shared
    by two colleagues -- or one that is also an ordinary word -- would
    attribute a characterization to the wrong person, so an ambiguous first
    name is dropped rather than guessed at.
    """
    owners: dict[str, set[str]] = {}
    for full in full_names:
        head = full.split(" ", 1)[0].strip()
        if len(head) < 3:
            continue
        owners.setdefault(head, set()).add(full)
    return {head: next(iter(owns)) for head, owns in owners.items() if len(owns) == 1}


def _has_work_anchor(sentence: str, anchors: frozenset[str]) -> bool:
    return any(token in anchors for token in _WORD_RE.findall(sentence.lower()))


def _markers_in(sentence_lower: str, markers: tuple[str, ...]) -> list[str]:
    return [m for m in markers if m in sentence_lower]


def scan_sentence(
    sentence: str,
    roster: dict[str, re.Pattern[str]],
    present: set[str],
    anchors: frozenset[str],
) -> list[tuple[str, str]]:
    """``[(person, marker)]`` for one sentence -- Layers 1 and 2.

    A rostered person must be named -- no name, no finding, which is why a
    page full of blunt architectural criticism that happens to mention
    nobody can never trip this. Beyond that the two vocabularies are gated
    DIFFERENTLY, and the asymmetry is the design:

      UNCONDITIONAL (health, personnel, credentials, interpersonal
        conflict) fires for anyone named, present or absent, and no work
        anchor exempts it. Presence mitigates a candid assessment; it does
        not turn a diagnosis or a comp conversation into team knowledge.

      PERSON-STATE (capability, performance, attitude) fires only for an
        ABSENT third party -- participation is consent, and someone in the
        room could answer back -- and only when no work anchor is present,
        so "struggled with the schema migration" reads as the fact about
        work that it is.
    """
    named = _referenced_names(sentence, roster)
    if not named:
        return []

    lowered = sentence.lower()
    unconditional = _markers_in(lowered, _UNCONDITIONAL_MARKERS)
    if unconditional:
        return [(person, m) for m in unconditional for person in sorted(named)]

    absent = named - present
    if not absent or _has_work_anchor(sentence, anchors):
        return []
    return [(person, m) for m in _markers_in(lowered, _PERSON_STATE_MARKERS) for person in sorted(absent)]


def _page_cited_sources(text: str) -> set[str]:
    """Every source this page claims to draw on: frontmatter ``sources:``
    plus inline ``NNN-Name.md`` citations anywhere in the file (weave's
    prompt mandates the inline form; ``write_gap_page`` emits the
    frontmatter form -- both paths are covered by taking the union)."""
    meta, _ = parse_frontmatter(text)
    cited = {str(s).strip() for s in (meta.get("sources") or []) if str(s).strip()}
    cited.update(extract_source_citations(text))
    return cited


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


def find_person_sensitivity(root: Path, wiki_dir: Path, sources_dir: Path) -> list[str]:
    """Person-sensitivity findings for content this pass NEWLY wrote.

    Returns issue strings for ``wiki_weaver.ingest.validate``'s ``issues``
    list -- non-empty means exit 1 -> ``structural_bad`` -> the bounded
    retry that already exists in both ``pipeline/ingest.dot`` and
    ``pipeline/synthesize.dot``. Never edits a page, never asks a model,
    never redacts.

    Silently returns ``[]`` when git is unavailable: without a HEAD baseline
    there is no way to tell new content from old, and the alternatives are
    scanning the whole wiki (wedges every later pass) or fabricating a
    signal. Same best-effort contract ``find_zero_touch`` already uses.
    """
    if not git_available(root):
        return []

    changed = git_changed_wiki_files(root)
    if not changed:
        return []

    participation = load_source_participation(sources_dir)
    anchors = build_work_anchors(wiki_dir)

    all_named: set[str] = set()
    for named, _ in participation.values():
        all_named |= named
    roster = {name: _name_pattern(name) for name in all_named}
    roster.update({head: _name_pattern(head) for head, _ in _first_name_index(all_named).items()})
    first_name_owner = _first_name_index(all_named)

    issues: list[str] = []
    for page_name in sorted(changed):
        page_path = wiki_dir / page_name
        if not page_path.is_file():
            continue  # deleted this pass -- nothing new was written
        try:
            text = page_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        added = new_lines(git_show_head(root, f"{wiki_dir.name}/{page_name}"), text)
        if not added:
            continue

        cited = _page_cited_sources(text)

        # LAYER 0 -- private-source provenance. Vocabulary-free, and
        # reported per (page, source) so a reviewer sees exactly which
        # recording the new material came from.
        for source_name in sorted(cited):
            entry = participation.get(source_name)
            if entry and entry[1]:
                issues.append(
                    f"person-sensitivity: {page_name} adds new content citing {source_name}, "
                    f"a {PRIVATE_PARTICIPANT_COUNT}-participant (1:1) source -- 1:1 and "
                    "performance conversations must not cross into team knowledge; requires "
                    "explicit review before this can be committed"
                )

        # LAYERS 1+2 -- absent-party characterization in the new lines only.
        present: set[str] = set()
        for source_name in cited:
            entry = participation.get(source_name)
            if entry:
                present |= entry[0]
        present |= {head for head, full in first_name_owner.items() if full in present}

        seen: set[tuple[str, str]] = set()
        for line in added:
            for sentence in _SENTENCE_SPLIT_RE.split(line):
                for person, marker in scan_sentence(sentence, roster, present, anchors):
                    subject = first_name_owner.get(person, person)
                    key = (subject, marker)
                    if key in seen:
                        continue
                    seen.add(key)
                    issues.append(
                        f"person-sensitivity: {page_name} newly characterizes {subject}, who is "
                        f"not a participant in any source this page cites (marker: {marker!r}) -- "
                        "characterization of a person, as distinct from criticism of their work, "
                        "requires explicit review before this can be committed"
                    )

    return issues
