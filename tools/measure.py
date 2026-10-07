"""Report measurements for a corpus. Deterministic; reads files only. Not part of the package.

    python tools/measure.py <corpus> [--asks N] [--run RUN_ID] [--topics] [--markers]
      --asks N     include the N latest ask runs (pages used, chars read per question)
      --run ID     Current state metrics for the pages that run touched
      --markers    a seeded 30-marker superseded sample with heuristic verdicts

Definitions
- sentences: body lines minus headings and citations, split on . ! ? ; fragments of 3+ words.
  "superseded" = sentences on a line carrying a superseded marker, or under a heading that
  carries one or says "superseded"; the rest are "live".
- citation share: bytes inside [s<id>: "..."] citations / page bytes.
- chars read by ask: every tool result returned to the pick and answer agents.
"""

from __future__ import annotations

import itertools
import json
import random
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wiki_weaver.checks import CITE_RE, MARKER_RE, superseded_spans
from wiki_weaver.ledger import read_rows
from wiki_weaver.lens import page_type_for, parse_page_types
from wiki_weaver.lib import page_files
from wiki_weaver.sources import normalize_ws, read_text, split_frontmatter

OLD_CITE_RE = re.compile(r'\[([^\[\]\n]+?\.md): "([^"\n]+)"\]')
DATE_PAREN = re.compile(r"\([^)]*\d{4}-\d{2}-\d{2}[^)]*\)")


def _frags(line: str) -> int:
    line = re.sub(r"^\s*([-*]|\d+\.)\s+", "", line)
    return sum(1 for f in re.split(r"(?<=[.!?])\s+", line) if len(f.split()) >= 3)


def sentence_split(text: str) -> tuple[int, int]:
    _, body = split_frontmatter(text)
    live = sup = 0
    in_sup_section = False
    for raw in body.splitlines():
        if raw.lstrip().startswith("#"):
            in_sup_section = bool(MARKER_RE.search(raw)) or "superseded" in raw.lower()
            continue
        marked = bool(MARKER_RE.search(raw))
        line = CITE_RE.sub("", OLD_CITE_RE.sub("", MARKER_RE.sub("", raw)))
        if not line.strip():
            continue
        n = _frags(line)
        if marked or in_sup_section:
            sup += n
        else:
            live += n
    return live, sup


def topic_stats(text: str) -> dict:
    _, body = split_frontmatter(text)
    heads = [l for l in body.splitlines() if l.startswith("## ")]
    cs_lines = 0
    inside = False
    has_cs = False
    for l in body.splitlines():
        if l.startswith(("## ", "# ")):
            inside = bool(re.match(r"^##\s+current state\b", l, re.IGNORECASE))
            has_cs = has_cs or inside
            continue
        if inside and l.strip():
            cs_lines += 1
    return {
        "h2": len(heads),
        "h2_dated": sum(bool(DATE_PAREN.search(h)) for h in heads),
        "current_state": has_cs,
        "current_state_lines": cs_lines,
    }


SOURCE_WORDS = re.compile(
    r"\b(meeting|recording|recorded|chat|call|note|notes|digest|sync|transcript|1:1|"
    r"standup|session|thread|channel|update from)\b",
    re.IGNORECASE,
)


def current_state_metrics(text: str) -> dict | None:
    """Lines in `## Current state`, and bullets whose subject is a source: the bullet's
    lead (text before the first ':' or the first 80 chars) names a meeting, recording,
    chat, call, note, digest, sync, transcript or session."""
    _, body = split_frontmatter(text)
    lines, inside, found = [], False, False
    for ln in body.splitlines():
        if ln.startswith(("## ", "# ")):
            inside = bool(re.match(r"^##\s+current state\b", ln, re.IGNORECASE))
            found = found or inside
            continue
        if inside and ln.strip():
            lines.append(ln)
    if not found:
        return None
    src_bullets = src_prose = 0
    for ln in lines:
        m = re.match(r"^\s*([-*]|\d+\.)\s+(.*)", ln)
        if not m:
            # a prose paragraph line: does its opener name a source?
            opener = re.sub(r"[*_]", "", ln.strip())[:80]
            if not ln.startswith((" ", "\t")) and SOURCE_WORDS.search(opener):
                src_prose += 1
            continue
        lead = re.sub(r"[*_]", "", m.group(2))
        lead = lead.split(":", 1)[0][:80] if ":" in lead[:80] else lead[:80]
        if SOURCE_WORDS.search(lead):
            src_bullets += 1
    return {"lines": len(lines), "source_bullets": src_bullets, "source_prose": src_prose}


def _dist(xs: list[int]) -> dict:
    if not xs:
        return {}
    return {
        "n": len(xs),
        "min": min(xs),
        "median": statistics.median(xs),
        "max": max(xs),
        "zero": sum(1 for x in xs if x == 0),
    }


def heading_fit(text: str, types: dict) -> dict | None:
    """## headings on a page against the lens's set for its type."""
    fm, body = split_frontmatter(text)
    pt = page_type_for(types, (fm or {}).get("type"))
    h2 = [ln[3:].strip() for ln in body.splitlines() if ln.startswith("## ")]
    if pt is None or not pt.heading_rule():
        return {"type": (fm or {}).get("type"), "h2": len(h2), "in_set": None, "off_set": None}
    cs = [h for h in h2 if re.match(r"current state\b", h, re.IGNORECASE)]
    ins = [h for h in h2 if pt.allows(h)]
    return {
        "type": pt.name,
        "h2": len(h2),
        "in_set": len(ins),
        "current_state": len(cs),
        "off_set": len(h2) - len(ins) - len(cs),
    }


SUSPECT = re.compile(
    r"\b(now|overtaken|replaced|updated|instead|later|since then|as of)\b", re.IGNORECASE
)


def marker_sample(w: Path, pages: list[Path], n: int = 30) -> list[dict]:
    """A seeded sample of superseded spans with a heuristic verdict: 'suspect' when the
    wrapped text reads like the replacement (now, overtaken, replaced, ...). Heuristic
    only: a reader confirms each verdict."""
    spans = []
    for p in pages:
        if p.name.startswith("source-"):
            continue
        _, body = split_frontmatter(read_text(p))
        for a, b in superseded_spans(body):
            spans.append((p.name, normalize_ws(body[a:b])[:240]))
    pick = random.Random(0).sample(spans, min(n, len(spans)))
    return [
        {"page": pg, "text": t, "verdict": "suspect" if SUSPECT.search(t) else "plausible"}
        for pg, t in pick
    ]


def ask_stats(ask_dir: Path) -> dict:
    q = read_text(ask_dir / "question.txt").strip() if (ask_dir / "question.txt").exists() else "?"
    out = {}
    try:
        out = json.loads(read_text(ask_dir / "out.txt"))
    except (OSError, ValueError):
        pass
    chars = 0
    for ev in (ask_dir / "engine").rglob("events.jsonl"):
        for line in ev.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("event") != "tool:post":
                continue
            o = ((r.get("data") or {}).get("result") or {}).get("output")
            if isinstance(o, dict):
                chars += len(str(o.get("content") or "")) + len(str(o.get("stdout") or ""))
            elif o is not None:
                chars += len(str(o))
    return {
        "question": q,
        "pages_used": out.get("pages_used"),
        "refused": out.get("refused"),
        "answer_chars": len(out.get("answer", "")),
        "chars_read_by_agents": chars,
    }


def main() -> None:
    w = Path(sys.argv[1]).resolve()
    n_asks = int(sys.argv[sys.argv.index("--asks") + 1]) if "--asks" in sys.argv else 0
    pages = page_files(w)
    sizes = {p.name: len(p.read_bytes()) for p in pages}
    largest = sorted(sizes, key=lambda n: -sizes[n])[:5]
    big = []
    for n in largest:
        t = read_text(w / n)
        live, sup = sentence_split(t)
        n_cites = len(CITE_RE.findall(t)) + len(OLD_CITE_RE.findall(t))
        body_lines = [x for x in split_frontmatter(t)[1].splitlines() if x.strip()]
        cite_bytes = sum(len(m.group(0).encode()) for m in CITE_RE.finditer(t))
        cite_bytes += sum(len(m.group(0).encode()) for m in OLD_CITE_RE.finditer(t))
        big.append(
            {
                "page": n,
                "bytes": sizes[n],
                "sentences_live": live,
                "sentences_superseded": sup,
                "citation_share": round(cite_bytes / max(sizes[n], 1), 3),
                "citations_per_line": round(n_cites / max(len(body_lines), 1), 2),
            }
        )
    topics = {p.name: topic_stats(read_text(p)) for p in pages if not p.name.startswith("source-")}
    rows = read_rows(w)
    wall = {}
    for run in sorted((w / ".wiki" / "runs").glob("ingest-*")):
        ts = []
        for tr in sorted(run.rglob("trace.jsonl")):
            for line in tr.read_text().splitlines():
                r = json.loads(line)
                if r["node_id"] in ("select", "index_prep"):
                    ts.append(datetime.fromisoformat(r["ts"]))
        d = [x for x in ((b - a).total_seconds() for a, b in itertools.pairwise(ts)) if x > 2]
        ch = [
            r["writer_input_chars"]
            for r in rows
            if r.get("run_id") == run.name and r.get("writer_input_chars")
        ]
        timeouts: dict[str, int] = {}
        for st in run.rglob("status.json"):
            try:
                sj = json.loads(st.read_text())
            except ValueError:
                continue
            if sj.get("failure_reason") == "timeout":
                timeouts[sj.get("node_id", "?")] = timeouts.get(sj.get("node_id", "?"), 0) + 1
        over = sum(1 for x in ch if x > 800_000)
        if d:
            wall[run.name] = {
                "writer_chars_over_800k": over,
                "timeouts": timeouts,
                "sources": len(d),
                "wall_median_s": round(statistics.median(d)),
                "wall_mean_s": round(statistics.mean(d)),
                "wall_max_s": round(max(d)),
                "first10_mean_s": round(statistics.mean(d[:10])),
                "last10_mean_s": round(statistics.mean(d[-10:])),
                "writer_chars_median": statistics.median(ch) if ch else None,
                "writer_chars_max": max(ch) if ch else None,
            }
    touched_cs = {}
    if "--run" in sys.argv:
        run_id = sys.argv[sys.argv.index("--run") + 1]
        names = {p for r in rows if r.get("run_id") == run_id for p in r.get("pages_touched", [])}
        for n in sorted(names):
            if (w / n).exists() and not n.startswith("source-"):
                m = current_state_metrics(read_text(w / n))
                touched_cs[n] = m
    lens = w / "lens.md"
    types = parse_page_types(read_text(lens)) if lens.exists() else {}
    fit = {
        p.name: heading_fit(read_text(p), types) for p in pages if not p.name.startswith("source-")
    }
    ruled = [v for v in fit.values() if v and v["in_set"] is not None]
    idx = w / "index.md"
    asks = sorted((w / ".wiki" / "ask").glob("*"))[-n_asks:] if n_asks else []
    report = {
        "pages": len(pages),
        "topic_pages": len(topics),
        "index_lines": len(read_text(idx).splitlines()) if idx.exists() else 0,
        "largest5": big,
        "topic_summary": {
            "h2_median": statistics.median(t["h2"] for t in topics.values()) if topics else 0,
            "h2_max": max((t["h2"] for t in topics.values()), default=0),
            "h2_dated_share": round(
                sum(t["h2_dated"] for t in topics.values())
                / max(sum(t["h2"] for t in topics.values()), 1),
                3,
            ),
            "with_current_state": sum(t["current_state"] for t in topics.values()),
            "current_state_lines_median": statistics.median(
                [t["current_state_lines"] for t in topics.values() if t["current_state"]] or [0]
            ),
        },
        "per_run": wall,
        "asks": [ask_stats(a) for a in asks],
        "touched_current_state": {
            "touched_topic_pages": len(touched_cs),
            "with_section": sum(1 for v in touched_cs.values() if v),
            "lines": _dist([v["lines"] for v in touched_cs.values() if v]),
            "source_bullets": _dist([v["source_bullets"] for v in touched_cs.values() if v]),
            "source_prose": _dist([v["source_prose"] for v in touched_cs.values() if v]),
            "pages": touched_cs if "--topics" in sys.argv else None,
        },
    }
    report["headings_vs_lens"] = {
        "pages_with_a_set": len(ruled),
        "h2": _dist([v["h2"] for v in ruled]),
        "off_set": _dist([v["off_set"] for v in ruled]),
        "pages": fit if "--topics" in sys.argv else None,
    }
    if "--markers" in sys.argv:
        report["superseded_sample"] = marker_sample(w, pages)
    if "--topics" in sys.argv:
        report["topics"] = topics
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
