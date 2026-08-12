"""wiki_weaver.ingest.takeaways_brief -- the self-contained brief for
``takeaways_gate``, the ONE pre-write discussion site the source pattern
describes (../amplifier-bundle-llm-wiki/docs/llm-wiki-pattern.md L50: "the
LLM reads the source, discusses key takeaways with you, writes a summary
page in the wiki, updates the index, updates relevant entity and concept
pages") that this pipeline never built until now.

WHY THIS SITE, NOT ``review_gate`` (see pipeline/ingest.dot's header and the
task that commissioned this module): ``review_gate`` sits AFTER weave has
already written -- measured at -0.5pp on the single-source ratio, proven
inert (n=3 vs n=2). The lens/canon direction, which steers BEFORE weave
writes, measured -6.4pp and is proven effective. The gap between "reads the
source" (retrieve_slice/build_catalog) and "writes a summary page" (weave)
is exactly where the gist puts the human/agent discussion, and exactly
where this pipeline had no gate at all: retrieve_slice -> build_catalog ->
weave, straight through.

THE FIX, same shape as ``prepare_review_brief`` / ``guidance_brief.py``
(both already proven twice in this codebase): a deterministic node (NO
model call) between ``build_catalog`` and the new ``takeaways_gate``,
assembling everything an answerer needs to say "emphasize X" cold, without
having read the source or the wiki itself:

  - source id, kind, segment position (identical to ``review_brief.py``)
  - a DETERMINISTIC gist of what the source's opening content appears to be
    about -- the first substantive paragraph(s) of the body, header AND
    nav/paywall chrome stripped (see ``lib.split_header`` and
    ``gist_from_content`` below) -- never an LLM summary. This is a gloss,
    not a judgment; the answerer is free to disagree with it entirely.
  - the read-bounded candidate pages (``retrieve_slice``'s slice), by name
    -- what the answerer already knows weave is ALLOWED to read.
  - ``build_catalog``'s own already-computed "Predicted merge targets"
    section, read VERBATIM (never recomputed -- no second BM25 pass, no new
    node deciding anything on weave's behalf; see ``build_catalog
    .build_predicted_targets``). This is "what it might connect to."

FAIL LOUD, NEVER FABRICATE: identical ``current_source_id`` contract as
``review_brief.py``/``guidance_brief.py`` -- no current source means
nothing real to brief, and this tool refuses to invent one.

Usage:
    python3 -m wiki_weaver.ingest.takeaways_brief --wiki-root <path>
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path

from wiki_weaver.lib import (
    WikiRoot,
    atomic_write_text,
    ledgered_source_ids,
    read_current_kind,
    read_current_source_id,
    read_source_kind,
    split_header,
)

# Keep the brief compact -- same discipline as review_brief.py's
# MAX_PAGES_LISTED: this goes into a prompt, not a log file.
MAX_PAGES_LISTED = 8

# A "gist" is a gloss, not a summary -- still capped so it reads as a
# pointer to go read the thing, not a substitute for reading it -- but
# raised from the original 300 to 600 (see LIVE FAILURE below: a single
# first sentence proved too thin a signal even once boilerplate was
# skipped; a deterministic node has no per-call cost, so the only real
# constraint is what fits usefully in a prompt, and several sentences of
# real argument fit comfortably in 600 chars).
MAX_GIST_CHARS = 600

# How much real (non-boilerplate) prose to gather before stopping -- see
# gist_from_content's docstring. Gathering continues past one qualifying
# line until this much text is collected (or MAX_GIST_PARAGRAPHS lines are
# used up), so a short opening sentence doesn't starve the gist of the
# substantive paragraph that follows it.
_MIN_GIST_CHARS = 220
_MAX_GIST_PARAGRAPHS = 3

# LIVE FAILURE THIS SECTION FIXES (see delivery report / task brief): for
# HALF the real evaluation corpus (Medium exports), the first real,
# non-heading, non-frontmatter prose line was a paywall banner --
# "Member-only story" -- because a blocklist of only headings/comments/
# frontmatter treats a paywall banner as ordinary prose (it IS a real,
# non-empty line of body text; skipping headers alone doesn't touch it).
# The proxy correctly refused every one of those briefs ("no substantive
# description of argument or content"), and every evaluation run reading
# from that corpus died at zero sources ingested.
#
# THE FIX has two parts, because a blocklist alone is insufficient (the
# task brief's own words): a single first sentence is a thin signal even
# once boilerplate is skipped, so this hunts for MULTIPLE qualifying
# lines (see _MIN_GIST_CHARS above) -- but first, every candidate line is
# scored as "prose" or "label/boilerplate/nav" using signals that
# generalize across Medium/Substack-style exports, not a per-file
# enumeration:
#   1. Markdown link/image syntax is reduced to its visible text before
#      anything else is measured -- a raw "[Free link](https://...long...)"
#      inflates a line's length with a URL that carries no signal, which
#      previously let a short call-to-action line alone exhaust the
#      gathering budget before any real paragraph was ever reached.
#   2. A small, explicit denylist of literal nav/paywall phrases
#      (case-insensitive, matched against the LINK-STRIPPED text) --
#      "Member-only story", "Listen", "Share", "More", "Press enter or
#      click to view image in full size", etc. -- covers what a
#      structural check below cannot: real English words that still
#      aren't prose.
#   3. Structural regexes catch the rest of the recurring non-prose
#      shapes found in the live corpus scan: a bare reaction count or
#      "--" separator, an interpunct, a "N min read" or "N days ago"
#      label, a "Mon DD, YYYY" byline date, and a line that IS, in its
#      entirety, a single markdown link or image (an avatar, a byline, a
#      lone image) once link syntax is stripped to nothing.
#   4. Two final, generalizable heuristics do the rest without hard-
#      coding this corpus: a minimum real-word count (catches short
#      labels/captions the phrase list didn't anticipate) and a
#      requirement that the line actually END like a sentence (period/
#      bang/question mark, ignoring a trailing markdown emphasis/quote/
#      paren wrapper) -- which is what a genuine sentence does and a nav
#      label or bare image caption typically does not.
#
# Verified against all 20 real sources in corpus-articles/ (see task
# brief) -- zero of the 20 produce a gist containing any paywall/nav
# phrase; every one carries real, source-specific content.
_HTML_COMMENT_RE = re.compile(r"^<!--.*-->$")
_FRONTMATTER_KV_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*:\s")

# Markdown link/image syntax, reduced to its visible (anchor/alt) text --
# used BOTH to score a line's prose-ness and as the actual text collected
# into the gist, so a URL never inflates length or leaks into the brief.
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

# A line that IS, in its entirety, one markdown link or image (a byline,
# an avatar, a lone illustration) -- distinct from a real sentence that
# merely CONTAINS a link.
_WHOLE_LINE_LINK_RE = re.compile(r"^!?\[[^\]]*\]\([^)]*\)$")

# Structural nav-chrome shapes found across the live corpus scan: a bare
# reaction-count/"--" separator, an interpunct, a bare number (claps/
# comments), a "N min read" label, a "N <unit> ago" relative timestamp,
# and a "Mon DD, YYYY" byline date.
_DASH_ONLY_RE = re.compile(r"^-{1,3}$")
_INTERPUNCT_ONLY_RE = re.compile(r"^\u00b7$")
_DIGIT_ONLY_RE = re.compile(r"^\d+$")
_MIN_READ_RE = re.compile(r"^\d+\s+min(ute)?s?\s+read$", re.IGNORECASE)
_TIME_AGO_RE = re.compile(r"^\d+\s+(second|minute|hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
_DATE_LABEL_RE = re.compile(r"^[A-Z][a-z]{2}\s+\d{1,2},?\s*\d{4}$")

# A small, explicit denylist of literal nav/paywall phrases (checked
# case-insensitively against the LINK-STRIPPED text, trailing "." or ":"
# ignored) -- real English words a structural regex can't distinguish
# from prose any other way. Generalizes across Medium/Substack-style
# exports; not a per-file enumeration.
_BOILERPLATE_PHRASES = frozenset(
    {
        "member-only story",
        "listen",
        "share",
        "more",
        "follow",
        "sign up",
        "sign in",
        "subscribe",
        "read this for free",
        "read here for free",
        "press enter or click to view image in full size",
    }
)

# A trailing markdown/quote/paren wrapper to strip before checking whether
# a line ends like a sentence -- a real sentence inside emphasis
# (*...possible.*) or a blockquote/quotation mark still ends in terminal
# punctuation once the wrapper is peeled off.
_TRAILING_WRAP_RE = re.compile(r"[*_\"'\u201d\u2019)\]]+$")

# The minimum count of real (alphabetic) words a line needs to even be a
# CANDIDATE prose line -- generalizable backstop for short labels/captions
# the phrase list and structural regexes didn't anticipate. Low enough to
# never reject a genuinely short, complete sentence (e.g. "The ONNX
# Runtime accelerates inference." is 5 words) -- this is a floor against
# one- or two-word fragments, not a substitute for the terminal-
# punctuation check below.
_MIN_PROSE_WORDS = 3

# Footer/CTA boilerplate that -- unlike _BOILERPLATE_PHRASES above -- does
# NOT occupy a whole line by itself: it's embedded inside a real, complete,
# multi-word sentence (so the exact-match denylist above never catches it),
# and it recurs across headings and bold lead-ins specifically (the two
# places this module now looks that gist scoring never did). Verified live
# across 13 of the 50 real corpus sources -- e-g- "## About the Author --
# Claude Certified Architect", "**...please consider subscribing to my
# Substack newsletter.**", "*...book him to speak and train your team...*",
# "...appreciate your full 50 claps...", "*Further reading:* [...]" -- a
# generalizable Medium/Substack author-footer genre, not specific to any
# one source. Matched by SUBSTRING (case-insensitive) rather than exact
# equality, since the boilerplate is embedded in an otherwise normal-
# looking sentence.
_BOILERPLATE_SUBSTRINGS = (
    "about the author",
    "further reading",
    "claps",
    "subscribing to",
    "subscribe to my",
    "book him to speak",
    "book her to speak",
    "if this helped you",
    "if this helped,",
)

# build_catalog.py's own section header -- see _PREDICTED_TARGETS_HEADER
# there. Matched by prefix only (not the full string) so this module never
# has to stay byte-identical to that string to keep working.
_PREDICTED_TARGETS_MARKER = "## Predicted merge targets"

# ---------------------------------------------------------------------------
# Structure signal -- E3: the opening is not privileged.
#
# LIVE FAILURE THIS FIXES (see task brief / delivery report): on real
# source 005, gist_from_content's opening-paragraph gist correctly followed
# its own contract -- and still misled every answerer, because the source's
# OPENING paragraph is adoption-stat noise ("OpenCode crossed 165,000 GitHub
# stars... monthly active developer counts reported anywhere from 2.5
# million... to over 7 million"), while the source's real, specific
# contribution (per-tool permission scoping -- an authorization control) is
# buried under a generic "Stage 5" heading, ten paragraphs in. A human
# skimming the source did not read linearly to find this -- they scanned
# STRUCTURE: section headings, and the bolded lead-in phrases a writer uses
# to flag the sentence that matters ("**Permission scoping per tool.**").
# Source 008 ("OpenCode Is Powerful. That's Exactly the Problem.") shows the
# same shape from a different angle: its own opening paragraph is flat
# scene-setting ("OpenCode is an open-source coding agent with a workflow
# similar to Claude Code...") while the piece's actual thesis lives in its
# subtitle heading, discarded today because gist_from_content deliberately
# skips every line starting with "#" (see _is_prose_line).
#
# THE FIX gathers a SEPARATE, ADDITIONAL deterministic signal --
# ``extract_structure_signals`` -- that scans the WHOLE segment body (never
# stops at an opening-paragraph budget the way gist_from_content does) and
# collects, in reading order: every markdown heading (any level) and every
# bold lead-in phrase (a line beginning "**...**"), deduplicated, filtered
# through the same boilerplate/word-count floor gist scoring already uses
# (_structure_worthy), and fence-aware (a "#" inside a fenced code block is a
# Python comment, not a heading -- see _CODE_FENCE_RE below; verified live
# against corpus-articles/011, /037, /041, whose embedded code examples
# contain "#"-prefixed comment lines that would otherwise masquerade as
# section headings).
#
# This is still a GLOSS, not a judgment, and still never an LLM summary --
# same fail-loud, falsifiable-pointer contract as gist_from_content. It is
# additive: the opening gist is NOT removed (the opening IS sometimes
# representative -- most of the corpus's 50 sources open with real
# argument), it is simply no longer the answerer's ONLY window into the
# source. See build_brief's ``structure_line`` and backend.py's
# ``_TAKEAWAYS_GATE_NOTE`` for how the two signals are presented together
# and what an answerer is told to do when they disagree.
# ---------------------------------------------------------------------------

# Keep the structure section in the same order of magnitude as the gist's
# own budget (MAX_GIST_CHARS) -- it is a second, equally-important signal,
# not an afterthought.
MAX_STRUCTURE_CHARS = 700

# Cap on how many individual headings/lead-ins are listed before falling
# back to "+N more" (mirrors _format_pages' identical discipline) -- a
# ten-stage guide's ten headings all fit; a much longer source degrades
# gracefully instead of blowing the character budget on enumeration alone.
MAX_STRUCTURE_ITEMS = 14

# A cap on any SINGLE heading/lead-in's contribution to the structure
# section -- without this, one PATHOLOGICALLY long heading could consume the
# whole budget and crowd out every other heading in the list. Deliberately
# generous (well above a normal heading's length, ~200 chars covers real
# corpus subtitle-headings like source 005's/008's in full): the signal a
# long subtitle-heading carries is often at its END, not its start (see
# source 008's real subtitle -- the safety fix it describes is the LAST
# clause), so a tight per-item cap would silently amputate exactly the part
# that matters. The section-wide MAX_STRUCTURE_CHARS budget below still
# bounds the total; an item this cap doesn't shorten may instead be dropped
# whole by that outer truncation, which is a better failure mode than
# mutilating it mid-sentence.
MAX_STRUCTURE_ITEM_CHARS = 220

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.*)$")

# A bold lead-in -- **like this** -- at the very START of a (already
# link-stripped) line. Deliberately anchored to the start: a bold span
# midway through a sentence is ordinary emphasis, not the label-like lead-in
# a writer uses to flag their most important point (verified live across
# corpus-articles/002, /003, /020, /041 -- a generalizable authoring
# convention, not specific to source 005's "**Permission scoping per
# tool.**").
_BOLD_LEADIN_RE = re.compile(r"^\*\*([^*]{1,120})\*\*")

# A fenced code block delimiter (```...). Lines inside a fence are never
# scanned for headings or bold lead-ins -- a "#"-prefixed line inside a
# fenced example is a Python/shell COMMENT, not a markdown heading (verified
# live: corpus-articles/011, /037, /041 all embed code/config examples whose
# "#"-comment lines would otherwise be misread as real section structure).
_CODE_FENCE_RE = re.compile(r"^`{3,}")

# A run of 26+ non-whitespace characters is, in practice, never a real
# English word -- it is a garbled markup artifact (most commonly a Medium
# table export collapsed onto one line with no separators, e.g. a table
# header row rendered as "CommandsRunOn your laptopRemotely..."). Reject
# rather than surface it as though it were a genuine heading/lead-in.
_LONG_TOKEN_RE = re.compile(r"\S{26,}")

# A lowercase letter immediately followed by an uppercase letter, WITHIN a
# single whitespace-delimited token -- used to count how many words look
# glued together with no space between them. A real word (even a genuine
# CamelCase brand like "OpenCode" or "JavaScript") has AT MOST ONE such
# transition. A collapsed Medium table row -- verified live on
# corpus-articles/008's real "**Local OpenCodeOpenCode + Tensorlake**..."
# table-header artifact -- glues multiple words together and racks up 2+
# transitions in a single token (e.g. "OpenCodeOpenCode" has 3: this is NOT
# a general ban on CamelCase, only on tokens with more joins than any real
# single word plausibly has).
_CAMEL_JOIN_RE = re.compile(r"[a-z][A-Z]")
_MAX_CAMEL_JOINS_PER_TOKEN = 1

# Markdown emphasis markers left over after extracting a heading/lead-in's
# visible text (e.g. an embedded "**bold**" span inside a "## heading text"
# line) -- stripped for the final displayed string; the emphasis markup
# itself carries no signal once the phrase has already been selected for
# being a heading or a leading bold span.
_RESIDUAL_EMPHASIS_RE = re.compile(r"[*_]{1,3}")


def _strip_emphasis_wrap(text: str) -> str:
    """Peel a single layer of *ved*/**bold** wrapping some headings/subtitles
    carry as a whole (e.g. a Medium deck line rendered as ``## *...*`` --
    see corpus-articles/008's actual subtitle heading) -- the visible text
    is the signal, the wrapping emphasis markup is not."""
    text = text.strip()
    for wrapper in ("***", "**", "*", "_"):
        if len(text) > 2 * len(wrapper) and text.startswith(wrapper) and text.endswith(wrapper):
            return text[len(wrapper) : -len(wrapper)].strip()
    return text


def _structure_worthy(cleaned: str) -> bool:
    """Shared quality gate for BOTH headings and bold lead-ins: real,
    substantive content -- not boilerplate/nav chrome (same denylist
    gist scoring uses), not a garbled markup artifact, not too short to
    carry a real idea. Deliberately does NOT require terminal sentence
    punctuation the way ``_is_prose_line`` does for gist prose -- a heading
    like \"Stage 1: learn the CLI and work in small repositories first\"
    is a perfectly good structural signal despite ending without a
    period."""
    if not cleaned or _LONG_TOKEN_RE.search(cleaned):
        return False
    if any(len(_CAMEL_JOIN_RE.findall(token)) > _MAX_CAMEL_JOINS_PER_TOKEN for token in cleaned.split()):
        return False  # multiple words glued with no space -- a garbled table/markup artifact
    normalized = cleaned.lower().strip().rstrip(".:")
    if normalized in _BOILERPLATE_PHRASES:
        return False
    if any(sub in normalized for sub in _BOILERPLATE_SUBSTRINGS):
        return False  # footer/CTA boilerplate embedded in an otherwise real-looking sentence
    return _prose_word_count(cleaned) >= _MIN_PROSE_WORDS


def _finalize_structure_item(text: str) -> str:
    """Strip residual emphasis markup and cap length -- see
    ``MAX_STRUCTURE_ITEM_CHARS``'s docstring for why a per-item cap exists
    independent of the section-wide ``MAX_STRUCTURE_CHARS`` cap."""
    text = _RESIDUAL_EMPHASIS_RE.sub("", text).strip()
    if len(text) <= MAX_STRUCTURE_ITEM_CHARS:
        return text
    return text[: MAX_STRUCTURE_ITEM_CHARS - 1].rstrip() + "\u2026"


def extract_structure_signals(body: str) -> list[str]:
    """Every markdown heading (any level) AND every bold lead-in phrase in
    ``body``, in reading order, deduplicated (case-insensitive) and fence-
    aware. See the module-level comment above ``MAX_STRUCTURE_CHARS`` for
    the full rationale: this is the antidote to the opening-privileges-
    itself assumption ``gist_from_content`` necessarily carries -- it scans
    the WHOLE body, never just the first ``_MIN_GIST_CHARS`` of it, because
    a source's real thesis can live in a heading or a bolded aside far past
    where the gist stops gathering (see source 005/008 in the module-level
    comment).
    """
    seen: set[str] = set()
    signals: list[str] = []
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _CODE_FENCE_RE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        heading_match = _HEADING_LINE_RE.match(stripped)
        if heading_match:
            candidate = _strip_emphasis_wrap(_clean_line(heading_match.group(2)))
        elif stripped.startswith("**"):
            bold_match = _BOLD_LEADIN_RE.match(_clean_line(stripped))
            candidate = bold_match.group(1).strip() if bold_match else ""
        else:
            continue

        if not _structure_worthy(candidate):
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        signals.append(_finalize_structure_item(candidate))
    return signals


# ---------------------------------------------------------------------------
# The spread signal -- what a source shows about itself when it has no
# markdown structure at all.
#
# Transcripts and chat exports carry neither headings nor bold lead-ins, so
# the structure scan finds nothing for them. That left the brief telling an
# answerer to "trust this over the opening" and then handing over an empty
# section -- and a meeting opening is almost always "can you hear me / let me
# share my screen", which says nothing about whether anything was decided.
#
# So when there is no authored structure to read, show the source's own words
# from ACROSS its whole length instead: evenly-spaced turns, sampled
# deterministically, first words of each. No LLM, no summary, no judgment
# baked in -- just enough of the middle and end for a reader to tell a
# logistics meeting from a decisive one, and form their own view.
# ---------------------------------------------------------------------------

# Fewer, longer samples beat more, shorter ones. At 90 chars a substantive
# turn gets cut off before its point lands -- "I let it go way too long before
# really challenging whether it was even..." reads as filler, when the rest of
# that sentence is the most useful thing in the meeting. This whole line is the
# only view an answerer gets of a transcript's middle and end, so it earns a
# budget in the same order as the gist's own.
SPREAD_SAMPLES = 10
SPREAD_SAMPLE_CHARS = 240

_TURN_LINE_RE = re.compile(
    r"^\[[^\]]{1,24}\]\s+(?:\*\*)?([^*\n:]{1,60})(?:\*\*)?:\s+(.*)$"
)


def extract_spread_samples(body: str) -> list[str]:
    """Evenly-spaced turns from across the whole body, in reading order.

    Deterministic: same input, same samples. Speaker-attributed so a reader
    can see who carried the conversation, not just that words occurred.
    """
    turns: list[tuple[str, str]] = []
    for line in body.splitlines():
        m = _TURN_LINE_RE.match(line.strip())
        if not m:
            continue
        speaker = _clean_line(m.group(1)).strip()
        said = _clean_line(m.group(2)).strip()
        if not said or len(said) < 25:
            continue
        turns.append((speaker, said))
    if not turns:
        return []
    if len(turns) <= SPREAD_SAMPLES:
        picked = turns
    else:
        # Bucket the source into equal spans and take the LONGEST turn from
        # each. Spread comes from the buckets, substance from the choice
        # within one: in a meeting the long turns are where someone explains,
        # decides, or argues, while the short ones are "yeah" and "correct".
        # Even spacing alone lands on filler, because most turns are filler.
        picked = []
        step = len(turns) / SPREAD_SAMPLES
        for i in range(SPREAD_SAMPLES):
            lo = int(i * step)
            hi = max(lo + 1, int((i + 1) * step))
            bucket = turns[lo:hi]
            if bucket:
                picked.append(max(bucket, key=lambda tv: len(tv[1])))
    out: list[str] = []
    for speaker, said in picked:
        if len(said) > SPREAD_SAMPLE_CHARS:
            said = said[:SPREAD_SAMPLE_CHARS].rstrip() + "\u2026"
        out.append(f"{speaker}: {said}" if speaker else said)
    return out


def spread_from_content(body: str) -> str:
    """The structure line's stand-in for sources with no authored structure."""
    samples = extract_spread_samples(body)
    if not samples:
        return "(no section headings, emphasized lead-in phrases, or speaker turns found in this source)"
    return (
        "no headings in this source (it is a transcript or chat), so here are "
        f"{len(samples)} turns sampled evenly across its whole length, in order: "
        + " | ".join(samples)
    )


def structure_from_content(content: str) -> str:
    """The formatted structure-signal line for the brief: every heading and
    bold lead-in phrase found across the WHOLE segment body, joined and
    capped at ``MAX_STRUCTURE_CHARS``. FAIL LOUD (an explicit, honest
    string, never a silently-omitted section) when the source has neither
    headings nor bold lead-ins -- same discipline ``gist_from_content``
    already applies to an unreadable/empty body."""
    _header, body = split_header(content)
    items = extract_structure_signals(body)
    if not items:
        return spread_from_content(body)
    shown = items[:MAX_STRUCTURE_ITEMS]
    text = "; ".join(shown)
    remaining = len(items) - len(shown)
    if remaining > 0:
        text += f"; +{remaining} more"
    if len(text) <= MAX_STRUCTURE_CHARS:
        return text
    budget = MAX_STRUCTURE_CHARS - 1
    truncated = text[:budget]
    last_boundary = truncated.rfind("; ")
    if last_boundary > budget * 0.5:
        truncated = truncated[:last_boundary]
    return truncated.rstrip("; ").rstrip() + "\u2026"


# ---------------------------------------------------------------------------
# Near-duplicate signal -- F5 move 4: a human comparison the proxy cannot
# reliably make on its own.
#
# LIVE FAILURE THIS FIXES (see GOAL-followups.md F5, human-baseline
# answers.json source 011): a human driving the takeaways gate recognized
# that source 011 was the SAME article as already-ingested source 010,
# republished (identical headings, identical argument, a ~2-byte body
# difference -- verified live: corpus-articles/010 and /011 differ only in
# a source-URL id, a byline permalink id, and a relative-vs-absolute
# publish-date line; see the delivery report). The human explicitly refused
# to let it count as a second, independent source corroborating the same
# page. Three independent proxy runs, given the identical brief and
# standing instructions but with NO computed duplicate signal, all missed
# it and folded 011 in as fresh corroborating content. Spotting a
# byte-level republication requires either a computed signal or the
# answerer still holding source 010's full text in memory to compare --
# neither is guaranteed for a model reasoning from a compact brief.
#
# THE FIX is a DETERMINISTIC (no model call) comparison of this source's
# body against the body of every source ALREADY INGESTED before it
# (``lib.ledgered_source_ids`` -- a source with a full ledger decision
# recorded, exactly "already committed to the wiki" -- the same durable
# state ``already_ledgered``/``select_source`` already use for resume
# bookkeeping). Modeled directly on ``build_catalog.build_predicted_targets``:
# computed once, surfaced as ITS OWN clearly-labeled, falsifiable line, never
# an instruction -- the gate answerer decides what a flagged near-duplicate
# means for THIS source (fold as a footnote? treat as independent? something
# else?), this signal only reports the resemblance.
#
# CHEAP BY CONSTRUCTION, NOT BY CHANCE: comparing every source against every
# prior source is inherently O(n) per source (O(n^2) total over a full
# corpus), so the run cost that matters is the PER-PAIR cost, not the pair
# COUNT. difflib.SequenceMatcher exists for exactly this cascade (the same
# pattern behind difflib.get_close_matches): ``real_quick_ratio()`` is an
# O(1) length-only upper bound, ``quick_ratio()`` is an O(n+m) character-
# multiset upper bound, and the expensive O(n*m) ``ratio()`` alignment is
# only ever computed for the rare pair that survives BOTH cheap filters --
# in practice, only genuine near-duplicates. Compared as LINES (via
# ``str.splitlines()``, the same granularity ``difflib.unified_diff`` uses
# for file comparison), not raw characters: a document-length text reduces
# to a few hundred elements this way, and a single differing line (e.g. a
# byline URL id or a relative-date stamp -- exactly what 010/011 differ by)
# contributes one mismatch out of hundreds rather than corrupting a
# character-level alignment.
#
# VERIFIED LIVE against the real corpus (see delivery report): 010 vs 011
# scores 0.98 on this exact line-based ratio; the single highest-scoring
# pair among all other 48*49/2 combinations in corpus-articles/ (sources
# that legitimately share topic and vocabulary without being duplicates)
# scores 0.56 -- comfortably below MIN_NEAR_DUPLICATE_RATIO, so the
# threshold is chosen with a wide, empirically-measured margin rather than
# a guess.
#
# REPORT, NEVER ACT: this module only ever adds a line to the brief. It
# does not skip, quarantine, or otherwise suppress the source -- see the
# task brief's explicit constraint and this project's own history of
# over-eager automatic paths destroying good sources.
#
# FAIL LOUD ON AN UNREADABLE PRIOR SOURCE: a ledgered source_id whose file
# cannot be read (deleted, permissions, corrupt encoding) is a real
# problem with the wiki's own durable state -- never silently treated as
# "no duplicate found" (that would make a broken check indistinguishable
# from a genuine negative result, the exact confusion CLI-CONTRACT.md's
# fail-loud discipline exists to prevent). ``Path.read_text`` raising is
# left to propagate uncaught, same as every other fail-loud path in this
# module (``read_current_source_id``, ``lib.read_ledger``).
# ---------------------------------------------------------------------------

# Chosen with a wide empirical margin (see comment above): the true
# duplicate pair scores 0.98, the highest-scoring genuinely-distinct pair
# in the same 50-source corpus scores 0.56. This is nowhere near that
# boundary -- a source would have to be overwhelmingly line-for-line
# identical to a prior source to cross it, which is exactly the
# republication case this signal exists to catch, not ordinary topical
# overlap.
MIN_NEAR_DUPLICATE_RATIO = 0.85

# How many near-duplicate hits to name explicitly before falling back to a
# "+N more" tail -- mirrors _format_pages'/structure's identical discipline.
# In practice this is almost always 0 or 1; the cap exists so a pathological
# case (many prior sources all republishing the same wire copy) still
# degrades gracefully instead of blowing the brief's size budget.
MAX_NEAR_DUPLICATE_HITS = 5


def find_near_duplicate_sources(
    wr: WikiRoot,
    current_source_id: str,
    current_body: str,
    threshold: float = MIN_NEAR_DUPLICATE_RATIO,
) -> list[tuple[str, float]]:
    """Every already-ingested source (``lib.ledgered_source_ids`` -- a full
    ledger decision recorded, i.e. already committed to the wiki) whose BODY
    (header stripped, via ``split_header``, same convention every other
    signal in this module uses) is at least ``threshold``-similar to
    ``current_body``, compared line-by-line via ``difflib.SequenceMatcher``.
    Returns ``(source_id, ratio)`` pairs, best match first.

    Cheap by construction (see module-level comment): ``real_quick_ratio()``
    then ``quick_ratio()`` gate every candidate before the expensive
    ``ratio()`` alignment is ever computed, so the O(n*m) cost is only ever
    paid for pairs that were already very likely near-duplicates.

    FAIL LOUD: a ledgered source_id whose file cannot be read propagates
    whatever ``Path.read_text`` raises -- never swallowed into an empty
    result (a broken check must never look identical to a clean one).
    ``current_source_id`` itself is excluded from the comparison set (a
    source cannot be a duplicate of itself; harmless in practice since
    ``ledgered_source_ids`` only returns FULLY-ledgered sources and the one
    currently being briefed is, by definition, not yet fully ledgered --
    excluded explicitly anyway so this holds even for an unusual resume).
    """
    prior_ids = sorted(sid for sid in ledgered_source_ids(wr.ledger_path) if sid != current_source_id)
    if not prior_ids:
        return []

    current_lines = current_body.splitlines()
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq2(current_lines)

    hits: list[tuple[str, float]] = []
    for prior_id in prior_ids:
        prior_path = wr.sources_dir / prior_id
        prior_text = prior_path.read_text(encoding="utf-8")  # fail loud -- see docstring
        _prior_header, prior_body = split_header(prior_text)
        matcher.set_seq1(prior_body.splitlines())

        # Cheap cascade: cheapest, loosest bound first: only pay for the
        # next, more expensive check when the previous one couldn't
        # already rule this pair out.
        if matcher.real_quick_ratio() < threshold:
            continue
        if matcher.quick_ratio() < threshold:
            continue
        ratio = matcher.ratio()
        if ratio >= threshold:
            hits.append((prior_id, ratio))

    hits.sort(key=lambda item: item[1], reverse=True)
    return hits


def near_duplicate_text(wr: WikiRoot, source_id: str, source_path: Path) -> str:
    """The formatted near-duplicate-signal line for the brief, or ``\"\"``
    (no line at all) when nothing crosses ``MIN_NEAR_DUPLICATE_RATIO`` --
    deliberately SILENT rather than an explicit "(none found)" string (unlike
    the gist/structure signals above): this line would otherwise appear on
    every single source, which is exactly the noise the task brief warns
    against. Silence here means "checked, nothing near-duplicate" -- an
    unreadable prior source is a DIFFERENT condition and fails loud instead
    (propagates, never silently folded into this same empty string; see
    ``find_near_duplicate_sources``'s docstring).

    ``source_path`` absent (no raw source file backing this pass -- mirrors
    ``_segment_content``'s identical fallback) means there is nothing of
    this source's own to compare, which is a real "nothing to check" case,
    not a failure of the check itself.
    """
    if not source_path.is_file():
        return ""
    current_text = source_path.read_text(encoding="utf-8")
    _header, current_body = split_header(current_text)
    hits = find_near_duplicate_sources(wr, source_id, current_body)
    if not hits:
        return ""

    shown = hits[:MAX_NEAR_DUPLICATE_HITS]
    parts = [f"{sid} (~{round(ratio * 100)}% similar)" for sid, ratio in shown]
    text = ", ".join(parts)
    remaining = len(hits) - len(shown)
    if remaining > 0:
        text += f", +{remaining} more"
    return (
        "NEAR-DUPLICATE SIGNAL (deterministic line-by-line comparison against every already-ingested "
        "source's body -- a computed resemblance, NOT a decision; verify independently before treating "
        f"this as independent corroboration of the same point): {text}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m wiki_weaver.ingest.takeaways_brief")
    parser.add_argument("--wiki-root", required=True)
    return parser


def _kind_for_brief(wr: WikiRoot, source_path: Path) -> str:
    """Identical fallback to review_brief.py's ``_kind_for_brief`` --
    prefer what ``detect_kind`` actually determined over the best-effort
    frontmatter-only guess."""
    try:
        return read_current_kind(wr)
    except FileNotFoundError:
        return read_source_kind(source_path)


def _current_segment(wr: WikiRoot) -> tuple[int, int]:
    """(index, total) of the segment about to be woven. Duplicated (not
    imported) from commit.py's/review_brief.py's identically-shaped private
    helper -- keeps this an additive, zero-risk change (same reasoning
    review_brief.py's own module docstring gives for its copy)."""
    path = wr.current_segment_file
    if not path.is_file():
        return 1, 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 1, 1
    return int(data.get("index", 1)), int(data.get("total", 1))


def _segment_content(wr: WikiRoot, source_path: Path) -> str:
    """The exact text weave is about to read for this pass -- the bounded
    segment when ``segment_source --select`` has run, otherwise the raw
    source file. Mirrors ``retrieve_slice.py``'s own ``content_path``
    fallback so the gist always describes what weave will ACTUALLY see,
    not the (possibly much larger) whole source."""
    content_path = wr.current_segment_content_file
    if content_path.is_file():
        return content_path.read_text(encoding="utf-8")
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")
    return ""


def _clean_line(stripped: str) -> str:
    """Markdown link/image syntax reduced to its visible (anchor/alt) text,
    whitespace-normalized. Used BOTH to score a line's prose-ness and as
    the actual text collected into the gist -- a raw URL never inflates
    length or leaks into the brief (see module-level comment for the live
    failure this fixes: a single call-to-action line with a long URL
    exhausted the whole gathering budget before any real paragraph was
    reached)."""
    return " ".join(_MD_LINK_RE.sub(lambda m: m.group(1), stripped).split())


def _prose_word_count(cleaned: str) -> int:
    return sum(1 for word in cleaned.split() if any(ch.isalpha() for ch in word))


def _is_prose_line(stripped: str, cleaned: str) -> bool:
    """True if ``stripped`` (whose link-stripped form is ``cleaned``) is a
    real, substantive prose line -- not blank/frontmatter/heading/comment,
    not nav/paywall chrome, not a bare label or caption. See the module-
    level comment above these regexes for the full rationale and the live
    corpus scan that shaped each check."""
    if not stripped or stripped == "---":
        return False
    if stripped.startswith("#") or _HTML_COMMENT_RE.match(stripped) or _FRONTMATTER_KV_RE.match(stripped):
        return False
    if _WHOLE_LINE_LINK_RE.match(stripped):
        return False  # an avatar/byline/lone image -- link syntax IS the whole line
    if _DASH_ONLY_RE.match(stripped) or _INTERPUNCT_ONLY_RE.match(stripped) or _DIGIT_ONLY_RE.match(stripped):
        return False  # reaction-count separators, interpunct, bare claps/comment counts
    if _MIN_READ_RE.match(stripped) or _TIME_AGO_RE.match(stripped) or _DATE_LABEL_RE.match(stripped):
        return False  # "N min read" / "N days ago" / "Mon DD, YYYY" byline chrome
    normalized = cleaned.lower().strip().rstrip(".:")
    if normalized in _BOILERPLATE_PHRASES:
        return False
    if _prose_word_count(cleaned) < _MIN_PROSE_WORDS:
        return False  # too short to be a real sentence, even a terse one
    trimmed = _TRAILING_WRAP_RE.sub("", cleaned.strip())
    return bool(trimmed) and trimmed[-1] in ".!?"  # a real sentence ends like one; a label doesn't


def _truncate_gist(text: str) -> str:
    """Cap at ``MAX_GIST_CHARS``, preferring to cut at the last word
    boundary (never mid-word) so long as that doesn't throw away more
    than ~40% of the budget -- otherwise a hard cut. Always ends in an
    ellipsis when truncated, signaling more real content follows (same
    contract the original single-sentence cap gave callers)."""
    if len(text) <= MAX_GIST_CHARS:
        return text
    budget = MAX_GIST_CHARS - 1
    truncated = text[:budget]
    last_space = truncated.rfind(" ")
    if last_space > budget * 0.6:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "\u2026"


def gist_from_content(content: str) -> str:
    """A deterministic, non-LLM gloss of what the source's opening content
    appears to be about: the first substantive paragraph(s) of the BODY
    (header stripped, nav/paywall chrome skipped), gathered until
    ``_MIN_GIST_CHARS`` of real prose is collected (or ``_MAX_GIST_PARAGRAPHS``
    lines are used up), capped at ``MAX_GIST_CHARS``. This is a falsifiable
    pointer for the answerer to confirm or reject, never a model-written
    summary -- see module docstring.

    LIVE FAILURE THIS FIXES: for half the real evaluation corpus (Medium
    exports), the first non-heading, non-frontmatter line of body text was
    a paywall banner ("Member-only story") -- a real, non-empty prose-
    shaped line that a heading/comment/frontmatter blocklist alone never
    catches, and a single first sentence is a thin signal even once
    boilerplate IS skipped. See the module-level comment above
    ``_BOILERPLATE_PHRASES`` for the full multi-signal fix and the live
    corpus verification.
    """
    _header, body = split_header(content)
    collected: list[str] = []
    total_len = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = _clean_line(stripped)
        if not _is_prose_line(stripped, cleaned):
            continue
        collected.append(cleaned)
        total_len += len(cleaned)
        if total_len >= _MIN_GIST_CHARS or len(collected) >= _MAX_GIST_PARAGRAPHS:
            break
    if not collected:
        return "(no readable body content -- source may be empty or header-only)"
    return _truncate_gist(" ".join(collected))


def _format_pages(pages: list[str]) -> str:
    if not pages:
        return "none (empty wiki, or no lexical overlap with this source yet)"
    shown = pages[:MAX_PAGES_LISTED]
    text = ", ".join(shown)
    remaining = len(pages) - len(shown)
    if remaining > 0:
        text += f", +{remaining} more"
    return text


def _slice_pages(wr: WikiRoot) -> list[str]:
    """The read-bounded candidate pages retrieve_slice already computed for
    this pass (``.ai/current_slice.json``'s ``pages`` list, wiki/-relative
    paths -- see retrieve_slice.py's ``_render_for_disk``). Absent ->
    empty list, never fabricated (mirrors build_catalog.py's own optional-
    upstream-artifact handling of the same file)."""
    path = wr.current_slice_file
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    pages = data.get("pages")
    return [str(p) for p in pages] if isinstance(pages, list) else []


def predicted_targets_text(wr: WikiRoot) -> str:
    """build_catalog's own already-computed "Predicted merge targets"
    section, read VERBATIM from ``.ai/current_catalog.md`` -- never
    recomputed here (no second BM25 pass, no new node deciding anything;
    see module docstring). Absent/missing catalog or no predicted section
    -> an honest, explicit statement, never a fabricated prediction."""
    path = wr.current_catalog_file
    if not path.is_file():
        return "Predicted merge targets: unavailable (build_catalog has not run yet)"
    text = path.read_text(encoding="utf-8")
    marker_index = text.find(_PREDICTED_TARGETS_MARKER)
    if marker_index == -1:
        return "Predicted merge targets: none yet (no lexical overlap with any existing page)"
    return text[marker_index:].strip()


def build_brief(wr: WikiRoot, source_id: str) -> str:
    """Assemble the compact, human/proxy-readable pre-write brief for
    ``source_id``. Every field is read live from disk state already
    computed by an earlier node in this SAME pass -- never invented."""
    source_path = wr.sources_dir / source_id
    kind = _kind_for_brief(wr, source_path)
    segment_index, segment_total = _current_segment(wr)
    content = _segment_content(wr, source_path)
    gist = gist_from_content(content)
    structure = structure_from_content(content)
    slice_pages = _slice_pages(wr)
    predicted = predicted_targets_text(wr)
    near_duplicate = near_duplicate_text(wr, source_id, source_path)

    about_line = (
        "What the OPENING looks like it's about (deterministic gist of the opening "
        "substantive prose ONLY, not an LLM summary -- confirm or reject freely; the "
        f"opening is not necessarily representative, see structure line below): {gist}"
    )
    structure_line = (
        "Structure across the WHOLE source (every section heading + bold lead-in "
        "phrase, in reading order, deterministic -- not an LLM summary; this scans the "
        f"entire source, not just its opening, and MAY point somewhere different than "
        f"the opening above -- when it does, trust this over the opening): {structure}"
    )
    lines = [
        f"Source: {source_id} (kind: {kind}, segment {segment_index}/{segment_total})",
        about_line,
        structure_line,
    ]
    # Silent when nothing to say (see near_duplicate_text's docstring) --
    # this line only appears when a genuine near-duplicate was found, so it
    # never becomes noise on the vast majority of ordinary sources.
    if near_duplicate:
        lines.append(near_duplicate)
    lines += [
        f"Candidate pages weave may read: {_format_pages(slice_pages)}",
        "",
        predicted,
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wr = WikiRoot(Path(args.wiki_root))

    try:
        source_id = read_current_source_id(wr)
    except FileNotFoundError as exc:
        # FAIL LOUD: no identifiable current source means there is nothing
        # real to brief. Never emit an empty or invented brief -- see
        # module docstring / review_brief.py's identical contract.
        print(str(exc), file=sys.stderr)
        return 1

    brief = build_brief(wr, source_id)

    # Same two-channel delivery as review_brief.py: stdout JSON only reaches
    # $takeaways_brief / Question.metadata["description"] -- a channel the
    # proxy's own question-reduction never reads. The stamped file lets
    # ProxyInterviewer read it directly, verifying freshness against
    # current_source.txt before trusting it.
    atomic_write_text(
        wr.takeaways_brief_file,
        json.dumps({"source_id": source_id, "stage": "takeaways_gate", "brief": brief}, indent=2) + "\n",
    )

    print(json.dumps({"takeaways_brief": brief}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
