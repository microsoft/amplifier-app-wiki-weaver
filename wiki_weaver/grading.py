# pyright: reportMissingImports=false
"""Shipped subset of the eval graders needed by runtime code (wiki_weaver/reweave.py,
wiki_weaver/retention.py).

WHY THIS MODULE EXISTS: ``eval/`` is deliberately excluded from the installed
wheel (see pyproject.toml's ``[tool.hatch.build.targets.wheel]`` -- only
``wiki_weaver`` and ``pipeline`` are packaged). ``eval/grade_wiki.py`` used to
be the sole home of ``GradeResult`` and ``grade_overview()``, but
``wiki_weaver/reweave.py`` (SHIPPED, runtime code) needs to call
``grade_overview()`` as its free, deterministic gate before paying for an LLM
re-weave call. Shipped code importing from an unshipped directory via
``sys.path.insert(0, ".../eval")`` works in a source checkout but is a hard
``ModuleNotFoundError`` crash for every real ``uv tool install`` user --
``eval/`` simply is not present in the installed package.

THE FIX: dependency inversion. This module -- inside the shipped
``wiki_weaver`` package -- is now the canonical home of ``GradeResult`` and
``grade_overview()`` (plus the constants/regexes they use).
``eval/grade_wiki.py`` imports them FROM here and re-exports them, so every
existing caller (other graders in ``grade_wiki.py``, its test suite, its CLI)
keeps working completely unchanged. ``wiki_weaver/reweave.py`` now imports
directly from this module -- no more reaching into ``eval/`` at all.

This is a pure relocation, not a behavior change: the code below is
byte-identical in logic to what previously lived in ``eval/grade_wiki.py``.

SECOND RELOCATION (claim-retention backstop): the same dependency-inversion
problem applies to ``eval/grade_claim_retention.py``'s ``grade_claim_retention()``
/ ``RetentionResult``, and to ``eval/grade_wiki.py``'s ``_build_judge_fn()`` LLM
plumbing that grader depends on. ``wiki_weaver/retention.py`` (SHIPPED, wired
into ``wiki_weaver/lib.py``'s ``ingest()``) needs to call
``grade_claim_retention()`` as an independent, LLM-judge-backed re-check of
whether a page re-write silently dropped grounded claims -- so both the grader
and the judge-builder it depends on must live here too, not in ``eval/``.
Unlike ``grade_overview()``, this grader is NOT free/deterministic -- it always
requires ``judge_fn`` (an LLM call) to produce a verdict; see its docstring for
the honest framing. ``eval/grade_claim_retention.py`` and ``eval/grade_wiki.py``
both import from here and re-export, so existing callers keep working
unchanged. Pure relocation, not a behavior change.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

__all__ = [
    "GradeResult",
    "grade_overview",
    "OVERVIEW_OPENER_THRESHOLD",
    "OVERVIEW_WIKILINK_MIN",
    "RetentionResult",
    "grade_claim_retention",
]

# ---------------------------------------------------------------------------
# Overview-quality constants  (grade_overview)
# ---------------------------------------------------------------------------

# Parenthetical source reference in prose: "(source N)", "(sources N, M, …)".
# This is the PRIMARY concatenation signal in overview.md -- each source gets a
# prose paragraph that names its source number parenthetically.
_OVERVIEW_SOURCE_REF = re.compile(
    r"\(source[s]?\s+\d+",
    re.IGNORECASE,
)

# Thread-ordinal opener: "A fifth thread …", "An eleventh thread …".
# Secondary / diagnostic -- detects ordinal-based per-source organisation.
_OVERVIEW_THREAD_OPENER = re.compile(
    r"\bA[n]?\s+\w+(?:-\w+)?\s+thread\b",
    re.IGNORECASE,
)

# Wikilinks [[Page Name]] -- expected in a navigational synthesis overview.
_WIKILINK = re.compile(r"\[\[[^\]]+\]\]")

# ATX section headers h2+ -- thematic structure indicator; absent = flat log.
_SECTION_HEADER_H2 = re.compile(r"(?m)^#{2,}\s+")

OVERVIEW_OPENER_THRESHOLD: int = 2  # max source-narration openers → PASS
OVERVIEW_WIKILINK_MIN: int = 5  # min wikilinks for a navigational overview


class GradeResult:
    """Tiny PASS/FAIL accumulator (one per scenario)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures: list[str] = []
        self.notes: list[str] = []

    def check(self, ok: bool, fail_msg: str, ok_msg: str | None = None) -> None:
        if ok:
            if ok_msg:
                self.notes.append(ok_msg)
        else:
            self.failures.append(fail_msg)

    @property
    def passed(self) -> bool:
        return not self.failures

    def report(self) -> str:
        lines = [f"[{'PASS' if self.passed else 'FAIL'}] {self.name}"]
        for n in self.notes:
            lines.append(f"    · {n}")
        for f in self.failures:
            lines.append(f"    ✗ {f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Overview-quality grader  (deterministic primary; LLM judge secondary)
# ---------------------------------------------------------------------------

_OVERVIEW_JUDGE_RUBRIC = """\
You are grading the OVERVIEW PAGE of a woven wiki.

Score whether it is a SYNTHESIZED NAVIGATIONAL MAP (themes→hubs, orienting prose
with [[wikilinks]], thematic ## sections) or a CONCATENATED PER-SOURCE LOG (one
paragraph per source article, source numbers in prose such as "(source N)") on 1-5:

  5 = Thematic ## sections, [[wikilinks]] as primary navigation, zero or near-zero
      per-source paragraph enumeration. A reader learns the THEMES, not the article list.
  4 = Mostly thematic; minor source enumeration present (1-2 openers).
  3 = Mixed — some thematic grouping, many per-source paragraphs.
  2 = Mostly per-source paragraphs but some thematic grouping.
  1 = Pure per-source log — each source article gets its own numbered paragraph.

Return ONLY valid JSON:
{"score": <1-5>, "rationale": "<one sentence>"}

OVERVIEW:
"""


def grade_overview(wiki: Path, judge_fn=None) -> GradeResult:
    """Grade whether overview.md is a synthesized navigational map vs. a per-source log.

    Hard gates (deterministic -- pass judge_fn=None for fully offline run):
        OV1  source_narration_openers <= OVERVIEW_OPENER_THRESHOLD (2)
             Counts "(source N)" / "(sources N, M)" parenthetical openers.
             A synthesized overview has ~0; a concatenation log has one per source.
        OV2  wikilink_count >= OVERVIEW_WIKILINK_MIN (5)
             A navigational map links to pages; a flat log may not.

    Diagnostics (reported but NOT gated):
        thread_openers        "A X-th thread …" sentence count (secondary signal)
        section_headers_h2    ## header count (0 = flat log; good overview is sectioned)

    Also inspects any ``type: index`` page in the wiki and reports its stats.

    Optional gate (requires judge_fn):
        OV-J  LLM synthesis score >= 4/5.  The deterministic gates OV1/OV2 stand
              alone and are sufficient to catch concatenation; the judge adds a
              qualitative corroboration but is NOT a hard gate.

    Pass judge_fn=None for a fully offline, zero-network run.
    CLI: ``python grade_wiki.py overview <wiki_dir> [--judge]``
    Exit 0 == pass; non-zero == fail (hard gate violated).
    """
    res = GradeResult("overview-quality")

    ov_path = wiki / "overview.md"
    if not ov_path.is_file():
        res.check(False, "overview.md not found in wiki")
        return res

    ov_text = ov_path.read_text(encoding="utf-8", errors="replace")

    # --- Deterministic metrics ---
    opener_count = len(_OVERVIEW_SOURCE_REF.findall(ov_text))
    thread_opener_count = len(_OVERVIEW_THREAD_OPENER.findall(ov_text))
    wikilink_count = len(_WIKILINK.findall(ov_text))
    section_count = len(_SECTION_HEADER_H2.findall(ov_text))

    # OV1 — hard gate: too many per-source parenthetical openers
    res.check(
        opener_count <= OVERVIEW_OPENER_THRESHOLD,
        f"OV1 FAIL: {opener_count} source-narration openers "
        f"(threshold <= {OVERVIEW_OPENER_THRESHOLD}; "
        f"0 expected in a synthesized overview)",
        f"OV1 source-narration openers: {opener_count} "
        f"(<= {OVERVIEW_OPENER_THRESHOLD})",
    )

    # OV2 — hard gate: too few wikilinks → not a navigational document
    res.check(
        wikilink_count >= OVERVIEW_WIKILINK_MIN,
        f"OV2 FAIL: {wikilink_count} wikilinks "
        f"(need >= {OVERVIEW_WIKILINK_MIN} for a navigational overview)",
        f"OV2 wikilinks: {wikilink_count} (>= {OVERVIEW_WIKILINK_MIN})",
    )

    # Diagnostics
    res.notes += [
        f"[diag] thread_openers: {thread_opener_count}",
        f"[diag] section_headers_h2plus: {section_count} "
        f"(0 = flat log; good overview has thematic ## sections)",
    ]

    # --- Inspect type:index pages as additional diagnostic ---
    _TYPE_FM = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
    for p in sorted(wiki.glob("*.md")):
        if p.name == "overview.md":
            continue
        page_text = p.read_text(encoding="utf-8", errors="replace")
        tm = _TYPE_FM.search(page_text)
        if tm and tm.group(1).strip() == "index":
            idx_openers = len(_OVERVIEW_SOURCE_REF.findall(page_text))
            idx_wl = len(_WIKILINK.findall(page_text))
            idx_sec = len(_SECTION_HEADER_H2.findall(page_text))
            res.notes.append(
                f"[index:{p.name}] source_openers={idx_openers} "
                f"wikilinks={idx_wl} sections_h2={idx_sec}"
            )

    # --- Optional LLM judge (OV-J, corroborating signal only) ---
    if judge_fn is not None:
        prompt = _OVERVIEW_JUDGE_RUBRIC + ov_text[:6000]
        try:
            raw = judge_fn(prompt)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                j = json.loads(m.group(0))
                score = j.get("score", 3)
                rationale = j.get("rationale", "")
                res.notes.append(
                    f"[OV-J] overview_synthesis_score: {score}/5 — {rationale}"
                )
                # OV-J is a corroborating diagnostic; it is NOT an independent hard gate.
        except Exception:
            pass
    else:
        res.notes.append(
            "llm_judge: disabled (OV-J skipped; deterministic gates OV1/OV2 only)"
        )

    return res


# ---------------------------------------------------------------------------
# LLM-judge plumbing (relocated from eval/grade_wiki.py's _build_judge_fn)
# ---------------------------------------------------------------------------


def _build_judge_fn():
    """Wire judge_fn to unified_llm.generate() if importable; else return None.

    Uses the top-level generate() convenience function (Spec §4.3) which takes
    a plain-text ``prompt`` kwarg and returns a GenerateResult with a .text
    attribute.  generate() is async; asyncio.run() bridges the sync CLI caller.

    max_tokens is set explicitly (rather than left at the client default).
    FINDING (from wiki_weaver/retention.py's incident-replay tests): with the
    default (unset) max_tokens, grade_claim_retention's JSON response was
    observed to cut off mid-string on a real, claim-dense page (byo-agent-
    ecosystem-recon.md, ~15.9k before-chars) -- "JSON parse error: Expecting
    ',' delimiter" at a truncation point consistent with a small default
    output-token ceiling, not a genuine model-authoring error. Extracting
    EVERY grounded claim from a long page (this grader's own instruction, see
    _RETENTION_RUBRIC) produces a correspondingly long JSON response; a
    generous explicit ceiling avoids truncating it mid-document. Applies to
    every caller of this shared judge (grade_overview, grade_synthesis,
    grade_hub_integration too) -- raising the ceiling only adds headroom for
    responses that need it and does not change behavior for short ones.
    """
    try:
        import asyncio  # noqa: PLC0415

        from unified_llm import generate  # type: ignore  # noqa: PLC0415

        def _judge(prompt: str) -> str:
            result = asyncio.run(
                generate("claude-sonnet-4-6", prompt=prompt, max_tokens=8_192)
            )
            return result.text

        return _judge
    except Exception as exc:
        print(
            f"WARN: unified_llm not importable ({exc}); "
            "falling back to deterministic-only grading.",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Claim-retention grader (relocated from eval/grade_claim_retention.py)
# ---------------------------------------------------------------------------
#
# Answers: when a new source forces a page re-write, does any previously-grounded
# claim SILENTLY DISAPPEAR with no trace -- or is every disappearance legitimate
# (superseded with a visible trace, or moved with a link)?
#
# HONEST FRAMING: unlike grade_overview() above, this grader is NOT free or
# deterministic -- it has no offline/no-judge mode. Every call requires a real
# LLM judge_fn and produces a probabilistic verdict (an independent LLM
# re-read of the before/after pages), not a mechanically guaranteed one. Never
# describe this grader -- or wiki_weaver/retention.py's gate built on top of
# it -- as "deterministic" or "cannot be bypassed" in code, comments, or docs.
#
# Fate taxonomy (trace-or-justify, NOT naive monotonic)
# ------------------------------------------------------
#   RETAINED      -- claim present in after-wiki with same/equivalent meaning.
#   SUPERSEDED    -- claim's SUBJECT still addressed, but value/fact updated to a
#                    newer value with a visible trace (e.g. "500, up from 100").
#                    This is LEGITIMATE -- NOT a loss. Do NOT flag as SILENTLY_LOST.
#   MOVED         -- claim is on a different after-wiki page, ideally linked.
#   SILENTLY_LOST -- claim's subject/topic COMPLETELY absent from ALL after-wiki
#                    pages; the topic is not mentioned in any form, old or new.
#
# PASS = zero SILENTLY_LOST.

_RETENTION_RUBRIC = """\
CLAIM RETENTION GRADER

Verify whether a wiki page re-write silently dropped any grounded claims.

DEFINITIONS
-----------
A GROUNDED CLAIM is a specific, verifiable assertion: a date, number, proper name,
version, procedure, or measurable property. Omit vague meta-sentences about the page
or the topic in general.

Fate taxonomy -- classify EACH grounded claim found in the BEFORE page:

  RETAINED      -- The claim is present in the after-wiki with the same or equivalent
                  meaning.  Quote the after-wiki sentence verbatim as evidence.

  SUPERSEDED    -- The claim's SUBJECT/TOPIC is still addressed in the after-wiki, but
                  with an UPDATED or CONTRADICTING value that replaces the old one.
                  A visible trace exists: the new value itself, "up from X", or an
                  explicit note that the old value changed.
                  Quote the after-wiki sentence verbatim.

  MOVED         -- The claim now appears on a DIFFERENT page in the after-wiki, ideally
                  with a wikilink [[Page Title]] pointing back to it.
                  Quote the after-wiki sentence verbatim.

  SILENTLY_LOST -- The claim's subject/TOPIC is COMPLETELY ABSENT from ALL after-wiki
                  pages. Zero sentences address this topic in any form.
                  Note that you searched for the subject and found nothing.

CRITICAL DISTINCTION -- SUPERSEDED vs SILENTLY_LOST
  If the after-wiki still discusses the claim's subject/topic -- even with a different
  value -- that is SUPERSEDED, NOT SILENTLY_LOST.
  Example: before says "supports up to 100 concurrent connections", after says
  "raised to 500, up from 100" -- fate = SUPERSEDED (subject still addressed).
  Example: before says "first released March 2019 by Redway Systems", after has NO
  mention of founding date or founding company -- fate = SILENTLY_LOST (topic absent).

INSTRUCTIONS
------------
1. Extract EVERY GROUNDED claim from the BEFORE page, not just a
   representative handful -- short pages typically have 4-8, longer pages
   have more. Under-extracting is the single biggest way this grader misses
   a real silent loss: a claim that is never extracted can never be
   classified SILENTLY_LOST. Quote each one verbatim.
2. For each claim, search ALL after-wiki pages for its subject/topic.
3. Classify the fate using the taxonomy above.
4. For RETAINED/SUPERSEDED/MOVED: provide a verbatim quote from the after-wiki as
   evidence so a human can spot-check (required -- prevents hallucination).
5. For SILENTLY_LOST: state what subject you searched for and confirm absence.

Return ONLY valid JSON, no prose before or after:
{
  "claims": [
    {
      "claim_quote": "<exact verbatim quote from BEFORE page>",
      "fate": "RETAINED|SUPERSEDED|MOVED|SILENTLY_LOST",
      "evidence_quote_or_absence_note": "<verbatim from after-wiki OR absence note>"
    }
  ]
}

BEFORE PAGE (before re-write):
"""


# ---------------------------------------------------------------------------
# Context-length caps -- RAISED from the eval-era 8_000/12_000 defaults.
# ---------------------------------------------------------------------------
#
# FINDING (from wiki_weaver/retention.py's incident-replay regression tests,
# eval/test_claim_retention_backstop.py): grading a real production page
# (eval/fixtures/incident_2026_07/before/design-and-promotion.md, 19_052
# chars) against the original before_page_text[:8_000] cap produced ZERO
# CONFIRMED_LOSS verdicts across 5 replays (0/5) -- not judge inconsistency,
# a structural miss. The lost section in that page begins at character
# offset ~15_195, entirely past the 8_000-char window: the judge never saw
# the deleted content in the BEFORE text at all, so it could not possibly
# flag it. An 8_000-char cap was a reasonable guess for an eval-only tool of
# unknown real page sizes; it is not adequate for a real runtime backstop
# against real wiki pages, which routinely run 15-20k+ chars.
#
# This is a deliberate, evidence-driven WIDENING, distinct from the pure
# relocation of grade_claim_retention's logic above (that move is
# byte-identical). Raised generously given the judge model's actual context
# window is far larger than either the old or new cap; a real page still
# exceeding _BEFORE_TEXT_CHAR_CAP is an accepted residual Phase 1 limit
# (chunking a single before-page across multiple judge calls is future work,
# not built here) -- but it must be a rare, oversized-page edge case, not the
# common case a real backstop silently fails on.
_BEFORE_TEXT_CHAR_CAP = 60_000
# The after-side text is now the FOCUSED context built by check_retention()
# (the changed page + its 1-hop neighbors, not the whole corpus -- see
# wiki_weaver/retention.py), so a generous cap here is safe: it bounds a
# handful of real pages, not an entire corpus concatenation.
_AFTER_TEXT_CHAR_CAP = 100_000


def _gather_after_text(after_wiki_dir: Path) -> str:
    """Concatenate all .md files in after_wiki_dir, labelled by filename."""
    parts: list[str] = []
    for md in sorted(after_wiki_dir.glob("*.md")):
        parts.append(f"=== {md.name} ===\n")
        try:
            parts.append(md.read_text(encoding="utf-8"))
        except OSError:
            parts.append(f"[read error: {md.name}]\n")
        parts.append("\n")
    return "\n".join(parts)


class RetentionResult:
    """Outcome of grade_claim_retention() -- PASS iff zero SILENTLY_LOST claims.

    HONEST FRAMING: this result reflects a single LLM judge call's read of the
    before/after pages. It is a probabilistic re-check, not a mechanical proof --
    see grade_claim_retention()'s docstring.
    """

    def __init__(self) -> None:
        # Each dict: {claim_quote, fate, evidence_quote_or_absence_note}
        self.claims: list[dict] = []
        self.error: str | None = None

    @property
    def passed(self) -> bool:
        """True iff no error and zero SILENTLY_LOST claims."""
        return self.error is None and not any(
            c.get("fate") == "SILENTLY_LOST" for c in self.claims
        )

    @property
    def silently_lost(self) -> list[dict]:
        """Claims whose fate is SILENTLY_LOST."""
        return [c for c in self.claims if c.get("fate") == "SILENTLY_LOST"]

    def report(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        lines = [f"[{verdict}] claim-retention"]
        if self.error:
            lines.append(f"  ERROR: {self.error}")
            return "\n".join(lines)
        fate_icons = {
            "RETAINED": "\u2713",
            "SUPERSEDED": "~",
            "MOVED": "\u2192",
            "SILENTLY_LOST": "\u2717",
        }
        for c in self.claims:
            fate = c.get("fate", "?")
            icon = fate_icons.get(fate, "?")
            tag = f" [{fate}]" if fate == "SILENTLY_LOST" else f" [{fate}]"
            quote = c.get("claim_quote", "")[:80]
            evidence = c.get("evidence_quote_or_absence_note", "")[:120]
            lines.append(f"  {icon}{tag}  {quote}")
            lines.append(f"         evidence: {evidence}")
        if self.silently_lost:
            lines.append(
                f"\n  {len(self.silently_lost)} SILENTLY_LOST claim(s) -- BUGs in re-write:"
            )
            for c in self.silently_lost:
                lines.append(f"    \u2717 {c.get('claim_quote', '')}")
        return "\n".join(lines)


def grade_claim_retention(
    before_page_text: str,
    after_wiki_dir: Path,
    judge_fn=None,
) -> RetentionResult:
    """Grade whether a page re-write silently dropped any grounded claims.

    HONEST FRAMING: this is an independent, LLM-judge-backed re-check of claim
    retention -- NOT a deterministic gate. It always requires a real judge_fn
    (an LLM call over the before/after text) to produce a verdict; there is no
    offline/no-judge mode (contrast with grade_overview() above, which is free
    and deterministic with an optional LLM corroboration). Treat a PASS/FAIL
    verdict from this grader as strong probabilistic evidence, not proof --
    callers that gate irreversible actions on it should still combine it with
    a fail-open/fail-closed escalation policy for judge unavailability (see
    wiki_weaver/retention.py) rather than trusting it unconditionally.

    Args:
        before_page_text: Full text of the wiki page BEFORE the re-write.
        after_wiki_dir:   Path to a directory containing wiki .md files AFTER
                          the re-write.  May contain multiple pages (the subject
                          page might have been split or merged).
        judge_fn:         Sync callable ``(prompt: str) -> str``.  If None,
                          builds one via _build_judge_fn() (requires unified_llm).

    Returns:
        RetentionResult.  result.passed is True iff zero SILENTLY_LOST claims.
        result.claims holds per-claim {claim_quote, fate, evidence_...} dicts
        with verbatim evidence quotes so a human can spot-check.
    """
    result = RetentionResult()

    if judge_fn is None:
        judge_fn = _build_judge_fn()
        if judge_fn is None:
            result.error = "unified_llm not importable; LLM judge unavailable"
            return result

    after_text = _gather_after_text(after_wiki_dir)
    if not after_text.strip():
        result.error = f"after_wiki_dir '{after_wiki_dir}' contains no .md files"
        return result

    prompt = (
        _RETENTION_RUBRIC
        + before_page_text[:_BEFORE_TEXT_CHAR_CAP]
        + "\n\nAFTER WIKI (all pages after re-write, separated by filename):\n"
        + after_text[:_AFTER_TEXT_CHAR_CAP]
    )

    try:
        raw = judge_fn(prompt)
    except Exception as exc:
        result.error = f"judge_fn raised: {exc}"
        return result

    # Extract the JSON block; tolerate leading/trailing prose in the response.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        result.error = f"judge returned no JSON block; raw response (first 500 chars):\n{raw[:500]}"
        return result

    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        result.error = f"JSON parse error: {exc}; raw (first 500 chars):\n{raw[:500]}"
        return result

    claims = data.get("claims", [])
    if not isinstance(claims, list):
        result.error = f"'claims' key is not a list; got {type(claims).__name__}"
        return result

    result.claims = [c for c in claims if isinstance(c, dict)]
    return result
