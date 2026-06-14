#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""Cross-page semantic tension verification probe — wiki-weaver.

Determines (with evidence) whether UNRECONCILED cross-page semantic
contradictions exist in the corpus, so we can decide whether a
``lint --semantic`` feature is justified — rather than taking a suspect
external source's word for it.

Two pair-selection modes
------------------------
``--mode shared-source`` (default)
    Selects pairs by highest SHARED-SOURCE count (≥2 shared sources).
    Pages sharing the most sources discuss the same topics — but are also
    most likely to *agree* because they drew from the same evidence.
    Bias: tests the agreement zone, not the contradiction risk zone.

``--mode topic-disjoint``
    Targets the actual contradiction *risk zone*: pages covering the
    SAME TOPIC from DIFFERENT sources that are NOT directly cross-linked.
    Per pair, all three criteria must hold:
      1. Shared topic-keyword count ≥ 2  (topically related, same domain).
      2. Source-overlap ≤ 1              (evidentially distinct — different articles).
      3. Neither page contains a ``[[Other Page Title]]`` wikilink to the
         other (structurally disconnected — no reconciliation can fire).
    Topic keywords are extracted from each page's title (filename stem) and
    its ``##`` / ``###`` headings, lowercased, with generic English stopwords
    removed.  Domain terms (rag, mcp, memory, cache, retrieval, context,
    evaluation, agent, pipeline, etc.) are always kept.
    Ranked by shared-topic-keyword count DESC, top ``--top-pairs`` selected
    (default 15 in this mode).

What the probe does (both modes)
---------------------------------
1. **Pair selection (deterministic).** As above.
2. **Snapshot.** Copies selected pages to a tempdir (READ-ONLY from the
   live corpus). The corpus is actively rewritten by a background ingest;
   snapshotting before judging prevents a mid-rewrite from corrupting a
   read. The live corpus is NEVER written to.
3. **LLM judge (per pair, parallel, cap 4).** Gives the judge the FULL
   text of both pages (including any ``## Open tensions`` sections). The
   judge decides whether the two pages assert claims in GENUINE TENSION
   on the SAME QUESTION — i.e. they contradict or recommend opposite
   choices such that both can't be fully true. It then checks whether
   the tension is ALREADY RECONCILED (cross-link or ``## Open tensions``).
   Strict: related-but-different subtopics with no contradiction →
   tension: false. MUST quote both claims verbatim — unquotable →
   tension: false.

   Judge output (strict JSON per pair):
       {tension, question, claim_a: {page, quote},
        claim_b: {page, quote}, reconciled, reconciled_where,
        severity: "minor"|"real"}

4. **Output.** Writes to
   ``~/.amplifier/evaluation/wiki-weaver/<sortable-datetime>/cross-page-tension/``
   (shared-source) or ``cross-page-tension-topic-disjoint/`` (topic-disjoint).

       results.json  — all pairs + full judgments
       summary.md    — real unreconciled tensions with verbatim quotes,
                       per-pair table, rollup counts, verdict line

5. **VERDICT.**
       "gap is REAL (M unreconciled cross-page tensions found)"
    vs "gap not evidenced (0 real unreconciled tensions)"

LLM plumbing
------------
Reuses ``run_ask_eval._build_judge_fn`` verbatim: ``unified_llm.generate()``
wrapped in ``asyncio.run()`` so the sync callable can be safely offloaded
via ``loop.run_in_executor()`` in the async harness (each call gets its own
thread event loop — the same pattern run_ask_eval.py uses for its judge).

Usage
-----
    python eval/verify_cross_page_tension.py                       # shared-source mode
    python eval/verify_cross_page_tension.py --mode topic-disjoint # contradiction risk zone
    python eval/verify_cross_page_tension.py --limit 2             # smoke: 2 pairs
    python eval/verify_cross_page_tension.py --wiki <dir>
    python eval/verify_cross_page_tension.py --judge-model <model>
    python eval/verify_cross_page_tension.py --top-pairs 20
    python eval/verify_cross_page_tension.py --concurrency 2
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths + defaults
# ---------------------------------------------------------------------------

_EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = _EVAL_DIR.parent
DEFAULT_WIKI = REPO_ROOT / "runs" / "corpus" / "wiki"
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
OUTPUT_ROOT = Path.home() / ".amplifier" / "evaluation" / "wiki-weaver"

# Page type tags that make a page non-content — excluded from pair selection.
_SKIP_TYPES: frozenset[str] = frozenset({"overview", "index", "log", "meta"})

# Character cap per page injected into the judge prompt.
# Pages are typically 3–10 k chars; 20 k gives full text for almost all.
_PAGE_CHAR_CAP = 20_000

# Generic stopwords to strip when building topic signatures for topic-disjoint mode.
# Keep domain-specific terms (rag, mcp, memory, cache, retrieval, agent, etc.).
_TOPIC_STOPWORDS: frozenset[str] = frozenset(
    {
        # Articles / prepositions / conjunctions
        "the",
        "and",
        "with",
        "for",
        "from",
        "into",
        "onto",
        "over",
        "under",
        "about",
        "after",
        "before",
        "between",
        "through",
        "without",
        "within",
        "across",
        "along",
        "among",
        "around",
        # Auxiliary / generic verbs
        "are",
        "was",
        "were",
        "been",
        "being",
        "have",
        "has",
        "had",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "does",
        "did",
        "use",
        "used",
        "using",
        "make",
        "making",
        "build",
        "building",
        "get",
        "getting",
        "work",
        "working",
        # Generic nouns / adjectives that add no discrimination in this corpus
        "overview",
        "guide",
        "patterns",
        "best",
        "practices",
        "introduction",
        "advanced",
        "approach",
        "approaches",
        "basics",
        "fundamentals",
        "tips",
        "tricks",
        "notes",
        "part",
        "section",
        "chapter",
        "example",
        "examples",
        "way",
        "ways",
        "method",
        "methods",
        "technique",
        "techniques",
        "based",
        "driven",
        "oriented",
        "first",
        "last",
        "new",
        "old",
        "good",
        "bad",
        "key",
        "core",
        "main",
        "basic",
        "simple",
        "complex",
        "large",
        "small",
        "high",
        "low",
        # Pronouns / determiners
        "this",
        "that",
        "these",
        "those",
        "your",
        "our",
        "its",
        "their",
        "you",
        "we",
        "they",
        "not",
        "all",
        "any",
        "more",
        "most",
        # Short prepositions (also caught by min-len filter, belt-and-suspenders)
        "per",
        "via",
        "versus",
        # Common wiki / doc filler
        "page",
        "wiki",
        "doc",
        "docs",
        "note",
        "see",
        "also",
        "how",
        "what",
        "when",
        "why",
        "where",
        "which",
        "who",
    }
)

# ---------------------------------------------------------------------------
# Frontmatter parsers (deterministic, no LLM)
# ---------------------------------------------------------------------------

_TYPE_FM = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
_SOURCES_FM = re.compile(r"^sources:\s*\[([^\]]*)\]", re.MULTILINE)


def _parse_type(text: str) -> str:
    m = _TYPE_FM.search(text)
    return m.group(1).strip() if m else ""


def _parse_sources(text: str) -> frozenset[int]:
    m = _SOURCES_FM.search(text)
    if not m:
        return frozenset()
    return frozenset(
        int(t.strip()) for t in m.group(1).split(",") if t.strip().isdigit()
    )


# ---------------------------------------------------------------------------
# Pair selection — shared-source mode
# ---------------------------------------------------------------------------


def _select_pairs(
    wiki: Path,
    top_n: int = 12,
    min_shared: int = 2,
) -> list[tuple[str, str, frozenset[int]]]:
    """Return up to top_n page-name pairs ordered by shared-source count (desc).

    Each entry is (page_a_name, page_b_name, shared_source_ids).
    Only pairs with >= min_shared shared sources are included.
    Excludes pages whose ``type:`` is in _SKIP_TYPES.
    """
    # Collect (page_name, source_ids) for all content pages.
    content_pages: list[tuple[str, frozenset[int]]] = []
    for p in sorted(wiki.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _parse_type(text) in _SKIP_TYPES:
            continue
        src = _parse_sources(text)
        if src:  # skip pages with no sources (nav pages, empty frontmatter)
            content_pages.append((p.name, src))

    # Compute all pairwise shared-source counts in O(n²) — fine for ~160 pages.
    scored: list[tuple[int, str, str, frozenset[int]]] = []
    for i in range(len(content_pages)):
        for j in range(i + 1, len(content_pages)):
            name_a, src_a = content_pages[i]
            name_b, src_b = content_pages[j]
            shared = src_a & src_b
            if len(shared) >= min_shared:
                scored.append((len(shared), name_a, name_b, shared))

    # Sort descending by shared count; alphabetically within ties for stability.
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))

    return [(name_a, name_b, shared) for _, name_a, name_b, shared in scored[:top_n]]


# ---------------------------------------------------------------------------
# Topic keyword helpers — used only by topic-disjoint mode
# ---------------------------------------------------------------------------


def _title_from_name(name: str) -> str:
    """Convert a page filename to its likely wikilink title.

    Example: ``RAG-vs-CAG-Pattern.md`` → ``RAG vs CAG Pattern``
    """
    return name.removesuffix(".md").replace("-", " ").replace("_", " ")


def _is_cross_linked(text_a: str, text_b: str, name_a: str, name_b: str) -> bool:
    """Return True if either page contains a ``[[wikilink]]`` to the other.

    Comparison is case-insensitive to tolerate capitalisation variation.
    """
    title_a = _title_from_name(name_a)
    title_b = _title_from_name(name_b)
    return (
        f"[[{title_b}]]".lower() in text_a.lower()
        or f"[[{title_a}]]".lower() in text_b.lower()
    )


def _extract_topic_keywords(page_name: str, text: str) -> frozenset[str]:
    """Extract distinctive topic keywords from a page's title and headings.

    Sources:
    - Page filename (stem, dashes/underscores → spaces, lowercased).
    - All ``##`` and ``###`` heading lines in the page body.

    Filtering:
    - Tokens shorter than 3 characters are dropped.
    - Tokens in ``_TOPIC_STOPWORDS`` are dropped.

    The resulting frozenset is the page's topic signature.
    """
    title_raw = (
        page_name.removesuffix(".md").replace("-", " ").replace("_", " ").lower()
    )
    # Capture ##-level and ###-level headings (h2 / h3)
    heading_lines = re.findall(r"^#{2,3}\s+(.+)", text, re.MULTILINE)
    heading_raw = " ".join(heading_lines).lower()

    all_text = title_raw + " " + heading_raw
    # Tokenise: lowercase alphabetical words (no digits, no hyphens — keeps
    # compound terms split so "sub-agent" → ["sub", "agent"] which is fine)
    tokens = re.findall(r"[a-z][a-z]*", all_text)

    return frozenset(t for t in tokens if len(t) >= 3 and t not in _TOPIC_STOPWORDS)


# ---------------------------------------------------------------------------
# Pair selection — topic-disjoint mode
# ---------------------------------------------------------------------------


def _select_pairs_topic_disjoint(
    wiki: Path,
    top_n: int = 15,
    min_topic_shared: int = 2,
    max_source_overlap: int = 1,
) -> list[tuple[str, str, frozenset[int], frozenset[str]]]:
    """Select pairs that are topically related but evidentially distinct and unlinked.

    Each entry is (page_a_name, page_b_name, shared_source_ids, shared_topic_kws).

    All three criteria must hold for a pair to qualify:
      1. Shared topic-keyword count >= min_topic_shared  (topically related).
      2. Shared source count <= max_source_overlap        (evidentially distinct).
      3. Neither page wikilinks the other                 (structurally disconnected).

    This is the contradiction *risk zone*: same topic, different evidence sources,
    no structural link that would have triggered a reconciliation pass.

    Ranked by shared-topic-keyword count DESC, alphabetically within ties.
    """
    content_pages: list[tuple[str, frozenset[int], frozenset[str], str]] = []
    for p in sorted(wiki.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _parse_type(text) in _SKIP_TYPES:
            continue
        src = _parse_sources(text)
        if not src:
            continue  # skip pages with no source citations
        kws = _extract_topic_keywords(p.name, text)
        content_pages.append((p.name, src, kws, text))

    # Score all pairs — O(n²), fine for ~200 pages.
    scored: list[tuple[int, str, str, frozenset[int], frozenset[str]]] = []
    for i in range(len(content_pages)):
        for j in range(i + 1, len(content_pages)):
            name_a, src_a, kws_a, text_a = content_pages[i]
            name_b, src_b, kws_b, text_b = content_pages[j]

            # Criterion 1: source-overlap must be low (evidentially distinct)
            shared_src = src_a & src_b
            if len(shared_src) > max_source_overlap:
                continue

            # Criterion 2: topic overlap must be sufficient (topically related)
            shared_kws = kws_a & kws_b
            if len(shared_kws) < min_topic_shared:
                continue

            # Criterion 3: must NOT be directly cross-linked
            if _is_cross_linked(text_a, text_b, name_a, name_b):
                continue

            scored.append((len(shared_kws), name_a, name_b, shared_src, shared_kws))

    # Sort descending by topic keyword count, then alphabetically for stability.
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))

    return [
        (name_a, name_b, src_shared, kws_shared)
        for _, name_a, name_b, src_shared, kws_shared in scored[:top_n]
    ]


# ---------------------------------------------------------------------------
# Snapshot helper — READ-ONLY from corpus, write to tempdir
# ---------------------------------------------------------------------------


def _snapshot_pages(wiki: Path, pages: list[str], tmp_dir: Path) -> None:
    """Copy named pages from wiki to tmp_dir.  Never writes to wiki."""
    for name in pages:
        src = wiki / name
        if src.is_file():
            shutil.copy2(src, tmp_dir / name)


def _read_snapshot(tmp_dir: Path, name: str, char_cap: int = _PAGE_CHAR_CAP) -> str:
    """Read a page from the snapshot dir, capping at char_cap chars."""
    p = tmp_dir / name
    if not p.is_file():
        return f"(page not found in snapshot: {name})"
    text = p.read_text(encoding="utf-8", errors="replace")
    if len(text) > char_cap:
        return (
            text[:char_cap]
            + f"\n\n[TRUNCATED: showing first {char_cap} of {len(text)} chars]"
        )
    return text


# ---------------------------------------------------------------------------
# Judge prompt
#
# Uses str.replace() — NOT .format() — so that { } characters inside page
# text (code fences, JSON examples, etc.) are never misinterpreted as
# format placeholders.  The JSON schema in the "Return format" section also
# contains { } but its keys don't match our placeholder names, so it passes
# through untouched.
# ---------------------------------------------------------------------------

_TENSION_JUDGE_PROMPT = """\
You are a semantic-tension detector for a woven wiki. Decide whether the two
pages below assert claims in GENUINE TENSION on the SAME QUESTION — meaning
both claims cannot be fully true at the same time — and whether that tension
is already reconciled in the wiki.

STRICTNESS RULES — apply these strictly, do NOT over-fire:
  * Two pages covering related-but-DIFFERENT subtopics with no contradiction
    → tension: false.  Difference in emphasis or level of detail → tension: false.
  * Only flag a tension if you can quote VERBATIM from each page a specific
    claim where the two pages assert contradictory things about the SAME
    concrete question.
  * If you cannot produce verbatim quotes from both pages, set tension: false.
    Do NOT paraphrase, summarize, or invent claims.
  * If the pages agree or are complementary → tension: false.

RECONCILIATION CHECK (only relevant when tension: true):
  After finding a tension, check whether it is ALREADY RECONCILED:
    (a) Does either page cross-link the other on that specific point
        (a wikilink [[Other Page]] near the relevant claim)?
    (b) Does either page have an "## Open tensions" or "## Tensions" section
        that explicitly addresses this specific disagreement?
  If yes to either: reconciled: true — fill in reconciled_where.
  If no: reconciled: false, reconciled_where: "".

SEVERITY (only when tension: true):
  "real"  — the two claims directly contradict on a matter of substance
            (opposite recommendations, conflicting factual assertions,
            incompatible design guidance) such that a reader following both
            pages would be confused or misled.
  "minor" — a tension exists but is unlikely to mislead in practice
            (e.g. slightly different categorisation; minor date discrepancy).

Return ONLY valid JSON — no text outside the JSON object.
Return format:
{
  "tension": <true|false>,
  "question": "<the specific question on which the pages disagree, or empty string when tension: false>",
  "claim_a": {"page": "<PAGE_A_NAME>", "quote": "<verbatim quote from page A, or empty string>"},
  "claim_b": {"page": "<PAGE_B_NAME>", "quote": "<verbatim quote from page B, or empty string>"},
  "reconciled": <true|false>,
  "reconciled_where": "<where it is reconciled, or empty string>",
  "severity": "<'real' or 'minor' or empty string when tension: false>"
}

=== PAGE A: PAGE_A_NAME ===
PAGE_A_TEXT
=== END PAGE A ===

=== PAGE B: PAGE_B_NAME ===
PAGE_B_TEXT
=== END PAGE B ===
"""


def _build_judge_prompt(
    page_a_name: str,
    page_a_text: str,
    page_b_name: str,
    page_b_text: str,
) -> str:
    """Interpolate page names and text into the judge prompt via str.replace()."""
    prompt = _TENSION_JUDGE_PROMPT
    # Replace names first so that the page text can't accidentally corrupt them.
    prompt = prompt.replace("PAGE_A_NAME", page_a_name)
    prompt = prompt.replace("PAGE_B_NAME", page_b_name)
    prompt = prompt.replace("PAGE_A_TEXT", page_a_text)
    prompt = prompt.replace("PAGE_B_TEXT", page_b_text)
    return prompt


# ---------------------------------------------------------------------------
# LLM judge — mirrors run_ask_eval._build_judge_fn exactly
#
# unified_llm.generate() wrapped in asyncio.run() for sync compatibility.
# In the async harness the callable is run via loop.run_in_executor() so
# asyncio.run() inside it creates its own thread event loop — same pattern
# as run_ask_eval.py.
# ---------------------------------------------------------------------------


def _build_judge_fn(model: str = DEFAULT_JUDGE_MODEL):
    """Return a sync callable judge_fn(prompt) -> str, or None if unavailable."""
    try:
        import asyncio as _asyncio  # noqa: PLC0415

        from unified_llm import generate  # type: ignore[import-unresolved]  # noqa: PLC0415

        def _judge(prompt: str) -> str:
            result = _asyncio.run(generate(model, prompt=prompt))
            return result.text

        return _judge
    except Exception as exc:  # noqa: BLE001
        print(
            f"WARN: unified_llm not importable ({exc}); judge unavailable.",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Judge one pair (sync — called from run_in_executor)
# ---------------------------------------------------------------------------


def _judge_pair(
    page_a_name: str,
    page_b_name: str,
    shared_sources: frozenset[int],
    tmp_dir: Path,
    judge_fn,
) -> dict:
    """Judge a single page pair for semantic tension.

    Returns a result dict with all fields populated, including error if the
    judge call failed.  This is a sync function — callers run it in a thread
    via loop.run_in_executor() so judge_fn's asyncio.run() gets its own loop.
    """
    base: dict = {
        "pair": [page_a_name, page_b_name],
        "shared_sources": sorted(shared_sources),
        "shared_count": len(shared_sources),
        "tension": False,
        "question": "",
        "claim_a": {"page": page_a_name, "quote": ""},
        "claim_b": {"page": page_b_name, "quote": ""},
        "reconciled": False,
        "reconciled_where": "",
        "severity": "",
        "error": None,
    }

    if judge_fn is None:
        base["error"] = "judge unavailable"
        return base

    page_a_text = _read_snapshot(tmp_dir, page_a_name)
    page_b_text = _read_snapshot(tmp_dir, page_b_name)

    prompt = _build_judge_prompt(page_a_name, page_a_text, page_b_name, page_b_text)

    try:
        raw = judge_fn(prompt)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            base["error"] = f"judge returned no JSON: {raw[:200]}"
            return base
        verdict: dict = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        base["error"] = f"JSON parse error: {exc}"
        return base
    except Exception as exc:  # noqa: BLE001
        base["error"] = f"judge error: {exc}"
        return base

    base.update(
        {
            "tension": bool(verdict.get("tension", False)),
            "question": verdict.get("question", ""),
            "claim_a": verdict.get("claim_a", {"page": page_a_name, "quote": ""}),
            "claim_b": verdict.get("claim_b", {"page": page_b_name, "quote": ""}),
            "reconciled": bool(verdict.get("reconciled", False)),
            "reconciled_where": verdict.get("reconciled_where", ""),
            "severity": verdict.get("severity", ""),
        }
    )
    return base


# ---------------------------------------------------------------------------
# Async pair runner
# ---------------------------------------------------------------------------


async def _run_one(
    page_a: str,
    page_b: str,
    shared: frozenset[int],
    tmp_dir: Path,
    judge_fn,
    sem: asyncio.Semaphore,
    idx: int,
    total: int,
) -> dict:
    """Run the judge for one pair under the concurrency semaphore."""
    async with sem:
        loop = asyncio.get_event_loop()
        print(
            f"  [{idx}/{total}] judging {page_a} × {page_b}"
            f"  (shared sources: {sorted(shared)}) ...",
            flush=True,
        )
        result = await loop.run_in_executor(
            None, _judge_pair, page_a, page_b, shared, tmp_dir, judge_fn
        )
        if result.get("error"):
            print(f"    → ERROR: {result['error']}", flush=True)
        elif (
            result.get("tension")
            and not result.get("reconciled")
            and result.get("severity") == "real"
        ):
            print("    → REAL UNRECONCILED TENSION", flush=True)
        elif result.get("tension"):
            sev = result.get("severity") or "?"
            rec = " (reconciled)" if result.get("reconciled") else ""
            print(f"    → tension ({sev}){rec}", flush=True)
        else:
            print("    → no tension", flush=True)
        return result


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _write_results(results: list[dict], out_dir: Path) -> None:
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )


def _write_summary(
    results: list[dict],
    out_dir: Path,
    verdict: str,
    mode: str = "shared-source",
) -> None:
    """Write summary.md with real unreconciled tensions (verbatim quotes) + rollup."""
    real_unreconciled = [
        r
        for r in results
        if r.get("tension") and not r.get("reconciled") and r.get("severity") == "real"
    ]
    errors = [r for r in results if r.get("error")]

    lines: list[str] = [
        "# Cross-Page Semantic Tension Verification",
        "",
        f"**Mode:** `{mode}`",
        "",
        f"**VERDICT: {verdict}**",
        "",
        f"- Pairs judged: {len(results)}",
        f"- Real unreconciled tensions: {len(real_unreconciled)}",
        f"- Judge errors: {len(errors)}",
        "",
    ]

    if real_unreconciled:
        lines += [
            "## Real Unreconciled Tensions",
            "",
            "> Pairs where the judge found a verbatim-quoted contradiction on the same",
            "> question that is NOT already cross-linked or addressed in `## Open tensions`.",
            "> Verify verbatim quotes against the live pages before acting.",
            "",
        ]
        for r in real_unreconciled:
            ca = r.get("claim_a") or {}
            cb = r.get("claim_b") or {}
            lines += [
                f"### `{r['pair'][0]}` × `{r['pair'][1]}`",
                "",
                f"**Shared sources:** {r['shared_sources']}",
                "",
                f"**Question in tension:** {r.get('question', '')}",
                "",
                f"**Claim A** — `{ca.get('page', '')}` (verbatim):",
                f"> {ca.get('quote', '')}",
                "",
                f"**Claim B** — `{cb.get('page', '')}` (verbatim):",
                f"> {cb.get('quote', '')}",
                "",
                f"**Severity:** `{r.get('severity', '')}`  |  "
                f"**Reconciled:** {'yes — ' + r.get('reconciled_where', '') if r.get('reconciled') else 'no'}",
                "",
            ]
    else:
        lines += [
            "## No Real Unreconciled Tensions Found",
            "",
            "The judge found no verbatim-quoted contradictions on the same question",
            "in the judged pairs.",
            "",
        ]

    # Minor / reconciled tensions (informational)
    minor_or_rec = [
        r
        for r in results
        if r.get("tension")
        and not (not r.get("reconciled") and r.get("severity") == "real")
    ]
    if minor_or_rec:
        lines += [
            "## Minor or Already-Reconciled Tensions",
            "",
            "_These were flagged as tensions but are either minor, reconciled, or both._",
            "",
        ]
        for r in minor_or_rec:
            ca = r.get("claim_a") or {}
            cb = r.get("claim_b") or {}
            rec_note = (
                f" (reconciled: {r.get('reconciled_where', '')})"
                if r.get("reconciled")
                else ""
            )
            lines += [
                f"**`{r['pair'][0]}` × `{r['pair'][1]}`** — {r.get('severity', '?')}{rec_note}",
                "",
                f"Question: {r.get('question', '')}",
                "",
                f"Claim A (`{ca.get('page', '')}`): {ca.get('quote', '')}",
                "",
                f"Claim B (`{cb.get('page', '')}`): {cb.get('quote', '')}",
                "",
            ]

    # Per-pair table — topic-disjoint mode gets an extra Topic Keywords column.
    if mode == "topic-disjoint":
        lines += [
            "## All Pairs Judged",
            "",
            "| Page A | Page B | Src∩ | Shared Topic Keywords | Tension? | Severity | Reconciled? | Error |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            t = "yes" if r.get("tension") else "no"
            sev = r.get("severity") or "—"
            rec = "yes" if r.get("reconciled") else "no"
            err = (r.get("error") or "")[:50]
            kws = r.get("topic_keywords", [])
            kw_str = ", ".join(kws[:5]) + ("…" if len(kws) > 5 else "")
            lines.append(
                f"| {r['pair'][0]} | {r['pair'][1]}"
                f" | {r['shared_count']} | {kw_str} | {t} | {sev} | {rec} | {err} |"
            )
    else:
        lines += [
            "## All Pairs Judged",
            "",
            "| Page A | Page B | Shared N | Tension? | Severity | Reconciled? | Error |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in results:
            t = "yes" if r.get("tension") else "no"
            sev = r.get("severity") or "—"
            rec = "yes" if r.get("reconciled") else "no"
            err = (r.get("error") or "")[:50]
            lines.append(
                f"| {r['pair'][0]} | {r['pair'][1]}"
                f" | {r['shared_count']} | {t} | {sev} | {rec} | {err} |"
            )

    # Notes section — mode-specific
    lines += ["", "## Notes", ""]
    if mode == "topic-disjoint":
        lines += [
            "- **Pair selection (`topic-disjoint` mode):** pages sharing ≥2 topic keywords",
            "  (extracted from title + `##`/`###` headings) AND source-overlap ≤1 (evidentially",
            "  distinct) AND no direct `[[wikilink]]` between them (structurally disconnected).",
            "  This is the contradiction *risk zone*: same topic, different evidence,",
            "  no reconciliation mechanism that could have fired.",
        ]
    else:
        lines += [
            "- **Pair selection (`shared-source` mode):** top pairs by shared-source count (≥2).",
            "  Pages that share more sources discuss the same topics and are most",
            "  likely to harbour real tensions — but also most likely to agree.",
        ]

    lines += [
        "- **Snapshots:** pages were copied to a tempdir before judging;",
        "  the live corpus (`runs/corpus/wiki/`) was not modified.",
        "- **Strictness:** judge instructed to quote verbatim — unquotable claims",
        "  → `tension: false`. Related-but-different subtopics → `tension: false`.",
        "- **Real unreconciled:** `tension: true` AND `reconciled: false` AND `severity: 'real'`.",
        "",
        f"**VERDICT: {verdict}**",
    ]

    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------


async def _run(
    wiki: Path,
    limit: int | None,
    top_pairs: int | None,
    judge_model: str,
    concurrency: int,
    mode: str = "shared-source",
) -> int:
    """Run the probe.  Returns exit code: 0=no tensions, 1=tensions found, 2=error."""
    # Apply mode-specific default for top_pairs if not explicitly set.
    if top_pairs is None:
        top_pairs = 15 if mode == "topic-disjoint" else 12

    print(f"Wiki:         {wiki}")
    print(f"Mode:         {mode}")
    print(f"Judge model:  {judge_model}  |  Concurrency: {concurrency}")
    print()

    judge_fn = _build_judge_fn(judge_model)
    if judge_fn is None:
        print("ERROR: judge unavailable — cannot run probe.", file=sys.stderr)
        return 2

    # -----------------------------------------------------------------------
    # Pair selection — branch on mode.
    # Internal representation for both modes:
    #   run_pairs: list[tuple[str, str, frozenset[int]]]  — (a, b, src_shared)
    #   kw_lookup: dict[(a, b), frozenset[str]]           — topic kws if available
    # -----------------------------------------------------------------------

    kw_lookup: dict[tuple[str, str], frozenset[str]] = {}

    if mode == "topic-disjoint":
        td_pairs = _select_pairs_topic_disjoint(wiki, top_n=top_pairs)
        run_pairs = [(a, b, src) for a, b, src, _ in td_pairs]
        kw_lookup = {(a, b): kws for a, b, _, kws in td_pairs}
        print(
            f"Content pages scanned; selected {len(run_pairs)} pair(s) "
            f"(≥2 shared topic keywords, source-overlap ≤1, not cross-linked; "
            f"top {top_pairs} requested)."
        )
    else:
        run_pairs = _select_pairs(wiki, top_n=top_pairs)
        print(
            f"Content pages scanned; selected {len(run_pairs)} pair(s) with ≥2 shared sources"
            f" (top {top_pairs} requested)."
        )

    if limit is not None:
        run_pairs = run_pairs[:limit]
        print(f"--limit {limit}: judging {len(run_pairs)} pair(s).")

    if not run_pairs:
        print("No qualifying pairs found. Nothing to judge.")
        return 0

    print()
    print("Pairs selected:")
    for i, (a, b, src) in enumerate(run_pairs, 1):
        print(f"  {i:2d}. {a}")
        print(f"      × {b}")
        if mode == "topic-disjoint":
            kws = kw_lookup.get((a, b), frozenset())
            print(f"      shared topics ({len(kws)}): {sorted(kws)}")
            print(f"      shared sources ({len(src)}): {sorted(src)}")
        else:
            print(f"      shared sources: {sorted(src)}")
    print()

    # Snapshot — copy pages to a tempdir so the live corpus isn't read during judging.
    page_names_needed = list({name for a, b, _ in run_pairs for name in (a, b)})

    with tempfile.TemporaryDirectory(prefix="wiki-tension-") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        _snapshot_pages(wiki, page_names_needed, tmp_dir)
        snapped = sum(1 for n in page_names_needed if (tmp_dir / n).is_file())
        print(f"Snapshotted {snapped}/{len(page_names_needed)} page(s) to temp dir.")
        print()

        # Judge all pairs in parallel up to concurrency.
        sem = asyncio.Semaphore(concurrency)
        tasks = [
            _run_one(a, b, src, tmp_dir, judge_fn, sem, idx + 1, len(run_pairs))
            for idx, (a, b, src) in enumerate(run_pairs)
        ]
        results: list[dict] = list(await asyncio.gather(*tasks))
    # tempdir is deleted here — all reading is done

    # Enrich results with topic_keywords (populated for topic-disjoint, empty for shared-source).
    # asyncio.gather preserves task order, so zip is safe.
    for r, (a, b, _) in zip(results, run_pairs):
        r["topic_keywords"] = sorted(kw_lookup.get((a, b), frozenset()))

    # Tally
    real_unreconciled = [
        r
        for r in results
        if r.get("tension") and not r.get("reconciled") and r.get("severity") == "real"
    ]
    M = len(real_unreconciled)
    verdict = (
        f"gap is REAL ({M} unreconciled cross-page tension{'s' if M != 1 else ''} found)"
        if M > 0
        else "gap not evidenced (0 real unreconciled tensions)"
    )

    # Write output — use separate subdirs so both modes can coexist in the same run dir.
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_subdir = (
        "cross-page-tension-topic-disjoint"
        if mode == "topic-disjoint"
        else "cross-page-tension"
    )
    out_dir = OUTPUT_ROOT / ts / out_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_results(results, out_dir)
    _write_summary(results, out_dir, verdict, mode=mode)

    # Console summary
    print()
    print(f"Results → {out_dir}")
    print()
    for r in results:
        is_real = (
            r.get("tension") and not r.get("reconciled") and r.get("severity") == "real"
        )
        marker = " ← REAL UNRECONCILED" if is_real else ""
        if r.get("error"):
            tag = f"ERROR: {r['error']}"
        elif r.get("tension"):
            sev = r.get("severity") or "?"
            rec = " (reconciled)" if r.get("reconciled") else ""
            tag = f"TENSION ({sev}){rec}{marker}"
        else:
            tag = "no tension"
        print(f"  {r['pair'][0]}")
        print(f"    × {r['pair'][1]}  → {tag}")
    print()
    print(f"VERDICT: {verdict}")

    return 0 if M == 0 else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description=(
            "Cross-page semantic tension verification probe for wiki-weaver. "
            "Determines whether real unreconciled contradictions exist in the corpus."
        )
    )
    ap.add_argument(
        "--wiki",
        type=Path,
        default=DEFAULT_WIKI,
        help=f"wiki directory (default: {DEFAULT_WIKI})",
    )
    ap.add_argument(
        "--mode",
        choices=["shared-source", "topic-disjoint"],
        default="shared-source",
        help=(
            "pair-selection strategy: "
            "'shared-source' (default) selects by highest shared-source count; "
            "'topic-disjoint' selects same-topic / source-disjoint / not-cross-linked pairs "
            "(the contradiction risk zone)"
        ),
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="judge only the first N pairs (smoke test)",
    )
    ap.add_argument(
        "--top-pairs",
        type=int,
        default=None,
        metavar="N",
        help=(
            "select top N pairs (default: 12 for shared-source, 15 for topic-disjoint)"
        ),
    )
    ap.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        metavar="MODEL",
        help=f"LLM model for the tension judge (default: {DEFAULT_JUDGE_MODEL})",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=4,
        metavar="N",
        help="max parallel judge calls (default: 4)",
    )
    args = ap.parse_args()

    sys.exit(
        asyncio.run(
            _run(
                wiki=args.wiki,
                limit=args.limit,
                top_pairs=args.top_pairs,
                judge_model=args.judge_model,
                concurrency=args.concurrency,
                mode=args.mode,
            )
        )
    )


if __name__ == "__main__":
    main()
