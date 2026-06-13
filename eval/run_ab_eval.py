#!/usr/bin/env python3
# pyright: reportMissingImports=false
"""A/B eval harness — wiki-ask (synthesized wiki) vs naive-RAG (raw articles).

Thesis under test (Karpathy):
  The compiled wiki (index.md + [[wikilinks]] + ~150 synthesized concept pages)
  answers questions BETTER and MORE EFFICIENTLY than naive grep/read over the
  raw 750-article source pile.

Design:
  ONE variable: the substrate. The same ask mechanism (bash/web removed, writes
  denied, reads scoped) runs against two targets:
    A — runs/corpus/wiki      (compiled, synthesized)
    B — ~/medium_articles     (raw article pile, no index, no wikilinks)

  Wiki side: REUSED from saved results.json — no re-running, no re-cost.
  Raw side:  subprocess calls `python -m cli rag <question> --articles <dir> --json`
             in parallel (--concurrency, default 3), with ERROR-vs-FAIL separation
             and retry logic (mirrors run_ask_eval.py).

  Comparison agent (BLINDED):
    - Receives: QUESTION + GROUND TRUTH key_facts + Answer X + Answer Y
      (wiki/raw identity HIDDEN; label assignment randomized per trial)
    - Runs 2 trials per scenario with A/B order SWAPPED to control position bias
    - Returns WIN-X / WIN-Y / TIE per trial
    - Final verdict: wiki-wins | raw-wins | tie | contested (trials disagree)

  Output:
    ~/.amplifier/evaluation/wiki-weaver/<datetime>/ab_results.json
    ~/.amplifier/evaluation/wiki-weaver/<datetime>/ab_summary.md

Usage:
    python eval/run_ab_eval.py                        # all 9 scenarios
    python eval/run_ab_eval.py --limit 2 --concurrency 2   # smoke
    python eval/run_ab_eval.py --wiki-results <path>  # override saved results
    python eval/run_ab_eval.py --articles <dir>       # different raw corpus
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml  # type: ignore[import-unresolved]

# ---------------------------------------------------------------------------
# sys.path: let eval/event_metrics import cleanly regardless of cwd
# ---------------------------------------------------------------------------
_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

from event_metrics import ask_run_metrics  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / defaults
# ---------------------------------------------------------------------------

REPO_ROOT = _EVAL_DIR.parent
SCENARIOS_FILE = _EVAL_DIR / "ask_scenarios.yaml"
DEFAULT_ARTICLES = Path.home() / "medium_articles"
DEFAULT_WIKI_RESULTS = (
    Path.home()
    / ".amplifier"
    / "evaluation"
    / "wiki-weaver"
    / "20260613-120656"
    / "results.json"
)
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"
OUTPUT_ROOT = Path.home() / ".amplifier" / "evaluation" / "wiki-weaver"

# ---------------------------------------------------------------------------
# Load scenarios
# ---------------------------------------------------------------------------


def load_scenarios() -> list[dict]:
    """Load scenarios from eval/ask_scenarios.yaml."""
    raw = SCENARIOS_FILE.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    return doc.get("scenarios", [])


# ---------------------------------------------------------------------------
# Error detection (mirrors run_ask_eval._is_error_text)
# ---------------------------------------------------------------------------

_ERROR_SIGNATURES = (
    "rag error:",
    "ask error:",
    "CheckpointMismatchError",
    "Execution failed",
    "Traceback",
    "RuntimeError:",
)


def _is_error_text(text: str) -> bool:
    """Return True if text is empty, an ANSI error banner, or contains error signatures."""
    if not text:
        return True
    if text.startswith("\x1b["):  # ANSI escape — CLI banner
        return True
    return any(sig in text for sig in _ERROR_SIGNATURES)


# ---------------------------------------------------------------------------
# RAG subprocess runner (mirrors run_ask_eval._run_ask_subprocess)
# ---------------------------------------------------------------------------


def _run_rag_subprocess(
    question: str,
    articles_dir: Path,
) -> tuple[dict, Path | None, str | None]:
    """Run `wiki-weaver rag` once and return (result_dict, logs_dir, error_reason).

    error_reason is None on success; a descriptive string on infrastructure failure.
    NEVER uses error banners or stderr as the answer.
    """
    runs_dir = articles_dir / ".runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    wall_start = time.time()

    cmd = [
        sys.executable,
        "-m",
        "cli",
        "rag",
        question,
        "--articles",
        str(articles_dir),
        "--json",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(REPO_ROOT),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"answer": "", "pages_used": [], "refused": False}, None, "TIMEOUT"
    except Exception as exc:  # noqa: BLE001
        return (
            {"answer": "", "pages_used": [], "refused": False},
            None,
            f"subprocess error: {exc}",
        )

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "")[:300]
        return (
            {"answer": "", "pages_used": [], "refused": False},
            None,
            f"exit {proc.returncode}: {detail}",
        )
    if proc.stderr and _is_error_text(proc.stderr):
        return (
            {"answer": "", "pages_used": [], "refused": False},
            None,
            f"stderr error: {proc.stderr[:300]}",
        )

    stdout = proc.stdout.strip()

    # Extract JSON: find the last { starting at a line boundary
    result: dict = {"answer": "", "pages_used": [], "refused": False}
    for m in reversed(list(re.finditer(r"^\{", stdout, re.MULTILINE))):
        try:
            result = json.loads(stdout[m.start() :])
            break
        except json.JSONDecodeError:
            continue

    answer_text = result.get("answer", "")
    if _is_error_text(answer_text):
        return (
            {"answer": "", "pages_used": [], "refused": False},
            None,
            f"answer is error banner: {answer_text[:200]}",
        )

    # Find the rag- directory created during this run (by mtime)
    logs_dir: Path | None = None
    try:
        rag_dirs = [
            d
            for d in runs_dir.iterdir()
            if d.is_dir()
            and d.name.startswith("rag-")
            and d.stat().st_mtime >= (wall_start - 2)
        ]
        if rag_dirs:
            logs_dir = max(rag_dirs, key=lambda d: d.stat().st_mtime)
    except OSError:
        pass

    return result, logs_dir, None


def _run_rag_with_retry(
    question: str,
    articles_dir: Path,
    max_attempts: int = 3,
) -> tuple[dict, Path | None, str | None]:
    """Run rag up to max_attempts times, retrying on infrastructure errors."""
    last_reason: str | None = None
    for attempt in range(1, max_attempts + 1):
        result, logs_dir, error_reason = _run_rag_subprocess(question, articles_dir)
        if error_reason is None:
            return result, logs_dir, None
        last_reason = error_reason
        if attempt < max_attempts:
            print(
                f"    [rag retry {attempt}/{max_attempts - 1}] "
                f"infra error: {error_reason[:100]}",
                file=sys.stderr,
            )
    return {"answer": "", "pages_used": [], "refused": False}, None, last_reason


# ---------------------------------------------------------------------------
# LLM judge (same plumbing as run_ask_eval._build_judge_fn)
# ---------------------------------------------------------------------------


def _build_judge_fn(model: str = DEFAULT_JUDGE_MODEL):
    """Build a sync callable judge_fn(prompt) -> str, or None if unavailable."""
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
# Blinded comparison judge
# ---------------------------------------------------------------------------


def _build_comparison_prompt(
    question: str,
    sc_type: str,
    ground_truth: dict,
    label_a: str,
    answer_a: str,
    label_b: str,
    answer_b: str,
) -> str:
    """Build a blinded comparison prompt.

    Labels are randomized per trial so the judge cannot infer which answer is
    from the synthesized wiki and which is from the raw articles.
    Ground truth key_facts are injected so the judge has an evidence anchor.
    """
    gt_json = json.dumps(ground_truth, indent=2)

    if sc_type in ("single_page", "cross_source"):
        task_desc = (
            "Judge which answer better satisfies the GROUND TRUTH KEY FACTS below.\n"
            "Criteria (in priority order):\n"
            "  1. Completeness — does the answer contain ALL key facts?\n"
            "  2. Accuracy — are the facts correct? Any fabrication is a hard disqualifier.\n"
            "  3. Citation/traceability — are sources named?\n"
            "For cross_source scenarios: also judge whether the answer genuinely integrates\n"
            "multiple perspectives (not just one side of a tension)."
        )
    else:  # absent
        task_desc = (
            "Judge which answer correctly handles a topic NOT in the source material.\n"
            "Correct behavior: explicitly states the topic is not covered, WITHOUT\n"
            "fabricating any substantive content about it.\n"
            "A clean explicit refusal beats a long answer with fabricated content.\n"
            "Penalize any specific claims about the absent topic asserted as fact."
        )

    return (
        "You are a blinded judge comparing two answers to the same question.\n"
        "You do NOT know which system produced each answer. Judge ONLY on content quality.\n"
        "Do NOT let answer length, formatting, or writing style influence your verdict.\n"
        "\n"
        f"QUESTION:\n{question}\n"
        "\n"
        f"SCENARIO TYPE: {sc_type}\n"
        "\n"
        f"GROUND TRUTH (what a correct answer must contain):\n{gt_json}\n"
        "\n"
        f"=== ANSWER {label_a} ===\n{answer_a}\n"
        "\n"
        f"=== ANSWER {label_b} ===\n{answer_b}\n"
        "\n"
        f"TASK:\n{task_desc}\n"
        "\n"
        "Return ONLY valid JSON — no text outside the JSON object:\n"
        '{"winner": "' + label_a + '" | "' + label_b + '" | "TIE",'
        ' "reason": "<one sentence citing the specific fact or gap that decided it>"}\n'
        "\n"
        "RULES:\n"
        "  - TIE if both are equally good, equally bad, or you genuinely cannot decide\n"
        "  - A single fabricated fact in an answerable scenario disqualifies that answer\n"
        "  - For absent: a clean refusal beats a long answer with invented content\n"
        "  - Do NOT infer which system produced which answer\n"
    )


def _compare_pair(
    wiki_answer: str,
    raw_answer: str,
    scenario: dict,
    judge_fn,
) -> dict:
    """Run 2 blinded trials (A/B order swapped) and aggregate to a final verdict.

    Trial 1: wiki=A, raw=B  (or randomized)
    Trial 2: wiki=B, raw=A  (swapped from trial 1)

    Consistent wiki-win:  trial1 wiki-wins AND trial2 wiki-wins  → wiki-wins
    Consistent raw-win:   trial1 raw-wins  AND trial2 raw-wins   → raw-wins
    Consistent tie:       trial1 tie       AND trial2 tie         → tie
    Otherwise:                                                       contested

    Returns dict with verdict + per-trial detail.
    """
    if judge_fn is None:
        return {"verdict": "judge-unavailable", "trial_1": {}, "trial_2": {}}

    question = scenario.get("question", "")
    sc_type = scenario.get("type", "single_page")
    ground_truth = scenario.get("ground_truth", {})

    # Randomise wiki/raw → A/B assignment for trial 1
    wiki_is_a_trial1 = random.choice([True, False])

    def _run_trial(wiki_is_a: bool) -> dict:
        if wiki_is_a:
            a_ans, b_ans = wiki_answer, raw_answer
        else:
            a_ans, b_ans = raw_answer, wiki_answer

        prompt = _build_comparison_prompt(
            question, sc_type, ground_truth, "A", a_ans, "B", b_ans
        )
        try:
            raw = judge_fn(prompt)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            result: dict = (
                json.loads(m.group(0))
                if m
                else {"winner": "TIE", "reason": f"parse error: {raw[:200]}"}
            )
        except Exception as exc:  # noqa: BLE001
            result = {"winner": "TIE", "reason": f"judge error: {exc}"}

        raw_winner = str(result.get("winner", "TIE")).strip()

        # Translate judge label → wiki-wins/raw-wins/tie
        if raw_winner == "TIE":
            winner = "tie"
        elif (raw_winner == "A" and wiki_is_a) or (raw_winner == "B" and not wiki_is_a):
            winner = "wiki-wins"
        else:
            winner = "raw-wins"

        return {
            "wiki_was": "A" if wiki_is_a else "B",
            "raw_was": "B" if wiki_is_a else "A",
            "judge_winner_label": raw_winner,
            "winner": winner,
            "reason": result.get("reason", ""),
        }

    t1 = _run_trial(wiki_is_a_trial1)
    t2 = _run_trial(not wiki_is_a_trial1)  # swap

    v1, v2 = t1["winner"], t2["winner"]
    verdict = v1 if v1 == v2 else "contested"

    return {"verdict": verdict, "trial_1": t1, "trial_2": t2}


# ---------------------------------------------------------------------------
# Single-scenario runner
# ---------------------------------------------------------------------------


async def _run_one(
    scenario: dict,
    articles_dir: Path,
    judge_fn,
    wiki_result: dict,
    sem: asyncio.Semaphore,
) -> dict:
    """Run RAG baseline for one scenario, then compare to saved wiki result.

    status field for raw side: "OK" | "ERROR"
    comparison.verdict: "wiki-wins" | "raw-wins" | "tie" | "contested" |
                        "raw-ERROR" | "judge-unavailable"
    """
    async with sem:
        loop = asyncio.get_event_loop()
        sc_id = scenario["id"]

        # 1. RAG baseline (subprocess in thread pool; retries on infra error)
        rag_result, logs_dir, error_reason = await loop.run_in_executor(
            None, _run_rag_with_retry, scenario["question"], articles_dir
        )

        if error_reason is not None:
            raw_side: dict = {
                "answer": "",
                "pages_used": [],
                "refused": False,
                "cost_usd": 0.0,
                "wall_time_s": None,
                "pages_read": 0,
                "tool_calls": 0,
                "events_found": False,
                "logs_dir": None,
                "status": "ERROR",
                "error_reason": error_reason,
            }
        else:
            # 2. CI metrics for RAG run (deterministic from events.jsonl)
            metrics = await loop.run_in_executor(
                None,
                ask_run_metrics,
                articles_dir,
                logs_dir if logs_dir else Path("/dev/null"),
            )
            raw_side = {
                "answer": rag_result.get("answer", ""),
                "pages_used": rag_result.get("pages_used", []),
                "refused": bool(rag_result.get("refused", False)),
                "cost_usd": metrics.get("cost_usd", 0.0),
                "wall_time_s": metrics.get("wall_time_s"),
                "pages_read": metrics.get("pages_read", 0),
                "tool_calls": metrics.get("tool_calls", 0),
                "events_found": metrics.get("events_found", False),
                "logs_dir": str(logs_dir) if logs_dir else None,
                "status": "OK",
                "error_reason": None,
            }

        # Wiki side: pulled directly from saved results (no re-run)
        wiki_side: dict = {
            "answer": wiki_result.get("answer", ""),
            "pages_used": wiki_result.get("pages_used", []),
            "refused": bool(wiki_result.get("refused", False)),
            "cost_usd": wiki_result.get("cost_usd", 0.0),
            "wall_time_s": wiki_result.get("wall_time_s"),
            "pages_read": wiki_result.get("pages_read", 0),
            "tool_calls": wiki_result.get("tool_calls", 0),
            "events_found": bool(wiki_result.get("events_found", False)),
            "logs_dir": wiki_result.get("logs_dir"),
            "status": wiki_result.get("status", "PASS"),
        }

        # 3. Blinded comparison (in thread — judge calls asyncio.run() internally)
        if error_reason is not None:
            comparison: dict = {
                "verdict": "raw-ERROR",
                "trial_1": {},
                "trial_2": {},
                "note": f"RAG infra error — cannot compare: {error_reason[:200]}",
            }
        else:
            comparison = await loop.run_in_executor(
                None,
                _compare_pair,
                wiki_side["answer"],
                raw_side["answer"],
                scenario,
                judge_fn,
            )

    return {
        "id": sc_id,
        "type": scenario["type"],
        "held_out": scenario.get("held_out", False),
        "question": scenario["question"],
        "wiki": wiki_side,
        "raw": raw_side,
        "comparison": comparison,
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def _fmt_wall(val: float | None) -> str:
    return f"{val:.1f}s" if val is not None else "-"


def _write_ab_summary(
    results: list[dict],
    out_dir: Path,
    wiki_results_path: str,
) -> None:
    """Write ab_summary.md to out_dir."""
    n_total = len(results)
    n_error = sum(1 for r in results if r["raw"]["status"] == "ERROR")
    n_valid = n_total - n_error

    verdicts = [
        r["comparison"]["verdict"] for r in results if r["raw"]["status"] != "ERROR"
    ]
    n_wiki = verdicts.count("wiki-wins")
    n_raw = verdicts.count("raw-wins")
    n_tie = verdicts.count("tie")
    n_contested = verdicts.count("contested")

    held_out = [r for r in results if r.get("held_out")]

    wiki_costs = [r["wiki"]["cost_usd"] or 0.0 for r in results]
    raw_costs = [
        r["raw"]["cost_usd"] or 0.0 for r in results if r["raw"]["status"] != "ERROR"
    ]
    wiki_walls = [
        r["wiki"]["wall_time_s"]
        for r in results
        if r["wiki"]["wall_time_s"] is not None
    ]
    raw_walls = [
        r["raw"]["wall_time_s"] for r in results if r["raw"]["wall_time_s"] is not None
    ]

    avg_wc = sum(wiki_costs) / len(wiki_costs) if wiki_costs else 0.0
    avg_rc = sum(raw_costs) / len(raw_costs) if raw_costs else 0.0
    avg_ww = sum(wiki_walls) / len(wiki_walls) if wiki_walls else None
    avg_rw = sum(raw_walls) / len(raw_walls) if raw_walls else None

    lines: list[str] = [
        "# wiki-weaver — A/B Eval: Synthesized Wiki vs Naive RAG",
        "",
        f"_(wiki results loaded from: `{wiki_results_path}`)_",
        "",
        "## Thesis under test",
        "",
        "> The compiled wiki (index.md + [[wikilinks]] + ~150 synthesized concept pages)",
        "> answers questions BETTER and MORE EFFICIENTLY than naive grep/read over the",
        "> raw 750-article source pile — Karpathy's original claim.",
        "",
        "## Comparison Verdict",
        "",
    ]

    verdict_line = (
        f"- **wiki-wins: {n_wiki}** / raw-wins: {n_raw} / tie: {n_tie}"
        + (f" / contested: {n_contested}" if n_contested else "")
        + (f" / **{n_error} RAW ERROR (infra)**" if n_error else "")
    )
    lines.append(verdict_line)

    valid_note = f"- Valid comparisons: {n_valid}/{n_total}"
    if n_error:
        valid_note += " — verdict INCOMPLETE (0 errors required for valid verdict)"
    lines.append(valid_note)

    lines += [
        "",
        "## Efficiency (mean per scenario)",
        "",
        "| | wiki-ask | raw-RAG |",
        "|---|---|---|",
        f"| cost_usd | ${avg_wc:.4f} | ${avg_rc:.4f} |",
        f"| wall_time_s | {_fmt_wall(avg_ww)} | {_fmt_wall(avg_rw)} |",
        "",
        "## Per-Scenario Results",
        "",
        "| ID | Type | H/O | Verdict | wiki cost | raw cost | wiki wall_s | raw wall_s | wiki pg | raw pg |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for r in sorted(results, key=lambda x: x["id"]):
        ho = "✓" if r.get("held_out") else ""
        v = r["comparison"]["verdict"]
        if v == "wiki-wins":
            v_cell = "**wiki-wins**"
        elif v == "raw-wins":
            v_cell = "raw-wins"
        elif v == "tie":
            v_cell = "tie"
        else:
            v_cell = f"_{v}_"
        w = r["wiki"]
        rr = r["raw"]
        wc = w.get("cost_usd") or 0.0
        rc = rr.get("cost_usd") or 0.0
        lines.append(
            f"| {r['id']} | {r['type']} | {ho} | {v_cell} "
            f"| ${wc:.4f} | ${rc:.4f} "
            f"| {_fmt_wall(w.get('wall_time_s'))} "
            f"| {_fmt_wall(rr.get('wall_time_s'))} "
            f"| {w.get('pages_read', 0)} | {rr.get('pages_read', 0)} |"
        )

    lines += [
        "",
        "## Per-Scenario Verdict Detail",
        "",
    ]

    for r in sorted(results, key=lambda x: x["id"]):
        v = r["comparison"]["verdict"]
        t1 = r["comparison"].get("trial_1", {})
        t2 = r["comparison"].get("trial_2", {})
        w_exc = str(r["wiki"].get("answer", ""))[:400]
        r_exc = str(r["raw"].get("answer", ""))[:400]
        t1_wiki = t1.get("wiki_was", "?")
        t2_wiki = t2.get("wiki_was", "?")
        lines += [
            f"### {r['id']} ({r['type']}) — {v}",
            f"**Q:** {r['question']}",
            "",
            "**wiki answer (excerpt):**",
            w_exc,
            "",
            "**raw-RAG answer (excerpt):**",
            r_exc,
            "",
            f"**Trial 1** (wiki='{t1_wiki}'): {t1.get('winner', '?')} "
            f"— _{t1.get('reason', 'n/a')}_",
            f"**Trial 2** (wiki='{t2_wiki}'): {t2.get('winner', '?')} "
            f"— _{t2.get('reason', 'n/a')}_",
            "",
        ]

    if held_out:
        lines += [
            "## Held-Out Generalization Signal",
            "",
            "_(Do not tune prompts/grader against these.)_",
            "",
        ]
        for r in held_out:
            lines.append(f"- **{r['id']}** ({r['type']}): {r['comparison']['verdict']}")
        lines.append("")

    lines += [
        "## Notes",
        "",
        "- Comparison is BLINDED: judge sees 'Answer A'/'Answer B'; order randomized per trial.",
        "- 2 trials per scenario (A/B swapped) to control position bias.",
        "- 'contested' = trials disagree; treated as tie for tally purposes.",
        "- Efficiency numbers from CI events.jsonl (deterministic, not estimated).",
        "- ERROR = RAG infra failure after retries; excluded from verdict tally.",
        "- Verdict is valid ONLY if 0 unresolved ERROR scenarios.",
    ]

    (out_dir / "ab_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Main eval runner
# ---------------------------------------------------------------------------


async def _run_ab_eval(
    wiki_results_path: Path,
    articles_dir: Path,
    judge_model: str,
    concurrency: int,
    limit: int | None,
) -> int:
    # Load saved wiki results (no re-run)
    wiki_results: list[dict] = json.loads(wiki_results_path.read_text(encoding="utf-8"))
    wiki_by_id = {r["id"]: r for r in wiki_results}
    print(f"Loaded {len(wiki_results)} saved wiki results from: {wiki_results_path}")

    # Load scenarios
    scenarios = load_scenarios()
    if limit is not None:
        scenarios = scenarios[:limit]

    missing = [sc["id"] for sc in scenarios if sc["id"] not in wiki_by_id]
    if missing:
        print(
            f"WARN: {len(missing)} scenario(s) not in saved wiki results (skipping): {missing}",
            file=sys.stderr,
        )
    scenarios = [sc for sc in scenarios if sc["id"] in wiki_by_id]

    judge_fn = _build_judge_fn(judge_model)
    sem = asyncio.Semaphore(concurrency)

    print(f"Running {len(scenarios)} scenario(s) — RAG baseline over: {articles_dir}")
    print(f"Judge model: {judge_model}  |  Concurrency: {concurrency}")
    if judge_fn is None:
        print("WARN: judge unavailable — verdicts will be 'judge-unavailable'")
    print()

    tasks = [
        _run_one(sc, articles_dir, judge_fn, wiki_by_id[sc["id"]], sem)
        for sc in scenarios
    ]
    results: list[dict] = list(await asyncio.gather(*tasks))

    # Write output
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = OUTPUT_ROOT / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "ab_results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    _write_ab_summary(results, out_dir, str(wiki_results_path))

    # Console summary
    n_error = sum(1 for r in results if r["raw"]["status"] == "ERROR")
    verdicts = [
        r["comparison"]["verdict"] for r in results if r["raw"]["status"] != "ERROR"
    ]
    n_wiki = verdicts.count("wiki-wins")
    n_raw = verdicts.count("raw-wins")
    n_tie = verdicts.count("tie")
    n_contested = verdicts.count("contested")

    wiki_total = sum(r["wiki"]["cost_usd"] or 0.0 for r in results)
    raw_total = sum(
        r["raw"]["cost_usd"] or 0.0 for r in results if r["raw"]["status"] != "ERROR"
    )

    print(f"\nResults → {out_dir}")
    print(
        f"Verdict: wiki-wins={n_wiki} raw-wins={n_raw} "
        f"tie={n_tie} contested={n_contested}"
        + (f"  |  RAW ERRORS: {n_error}" if n_error else "")
    )
    print(f"Cost: wiki total=${wiki_total:.4f}  raw total=${raw_total:.4f}")
    print()

    for r in sorted(results, key=lambda x: x["id"]):
        v = r["comparison"]["verdict"]
        w = r["wiki"]
        rr = r["raw"]
        err_note = (
            f"  ERR: {rr.get('error_reason', '')[:60]}"
            if rr["status"] == "ERROR"
            else ""
        )
        ev_mark_r = "" if rr.get("events_found") else " [no CI events]"
        wc = w.get("cost_usd") or 0.0
        rc = rr.get("cost_usd") or 0.0
        ww = _fmt_wall(w.get("wall_time_s"))
        rw = _fmt_wall(rr.get("wall_time_s"))
        print(
            f"  {r['id']:3s}  {v:<12}  "
            f"wiki: ${wc:.4f}  {ww:>7}  {w.get('pages_read', 0)}pg  |  "
            f"raw: ${rc:.4f}  {rw:>7}  {rr.get('pages_read', 0)}pg"
            f"{ev_mark_r}{err_note}"
        )

    if n_error > 0:
        print(
            f"\n⚠ INCOMPLETE — {n_error} RAG infra error(s)."
            " Verdict only valid with 0 errors.",
            file=sys.stderr,
        )
        return 2

    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description=(
            "A/B eval: wiki-ask (synthesized wiki) vs naive-RAG (raw articles).\n"
            "Wiki side is REUSED from saved results (no re-run). "
            "RAG side runs fresh subprocesses in parallel."
        )
    )
    ap.add_argument(
        "--wiki-results",
        type=Path,
        default=DEFAULT_WIKI_RESULTS,
        metavar="RESULTS_JSON",
        help=f"saved wiki-ask results.json to reuse (default: {DEFAULT_WIKI_RESULTS})",
    )
    ap.add_argument(
        "--articles",
        type=Path,
        default=DEFAULT_ARTICLES,
        metavar="DIR",
        help=f"raw articles directory for RAG baseline (default: {DEFAULT_ARTICLES})",
    )
    ap.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        metavar="MODEL",
        help=f"LLM model for blinded comparison judge (default: {DEFAULT_JUDGE_MODEL})",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="max parallel RAG baseline subprocesses (default: 3)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="run only the first N scenarios (smoke test)",
    )
    args = ap.parse_args()

    wiki_results_path = args.wiki_results.expanduser().resolve()
    if not wiki_results_path.is_file():
        print(f"ERROR: wiki results not found: {wiki_results_path}", file=sys.stderr)
        sys.exit(1)

    articles_dir = args.articles.expanduser().resolve()
    if not articles_dir.is_dir():
        print(f"ERROR: articles dir not found: {articles_dir}", file=sys.stderr)
        sys.exit(1)

    sys.exit(
        asyncio.run(
            _run_ab_eval(
                wiki_results_path,
                articles_dir,
                args.judge_model,
                args.concurrency,
                args.limit,
            )
        )
    )


if __name__ == "__main__":
    main()
