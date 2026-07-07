# pyright: reportMissingImports=false
"""Shipped subset of the eval graders needed by runtime code (wiki_weaver/reweave.py).

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
"""

from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = [
    "GradeResult",
    "grade_overview",
    "OVERVIEW_OPENER_THRESHOLD",
    "OVERVIEW_WIKILINK_MIN",
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
