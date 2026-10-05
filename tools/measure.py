"""Report measurements for a corpus. Deterministic; reads files only. Not part of the package.

    python tools/measure.py <corpus> [--asks N]   (N = how many latest ask runs to include)

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
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wiki_weaver.checks import CITE_RE, MARKER_RE
from wiki_weaver.ledger import read_rows
from wiki_weaver.lib import page_files
from wiki_weaver.sources import read_text, split_frontmatter

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
        cite_bytes = sum(len(m.group(0).encode()) for m in CITE_RE.finditer(t))
        cite_bytes += sum(len(m.group(0).encode()) for m in OLD_CITE_RE.finditer(t))
        big.append(
            {
                "page": n,
                "bytes": sizes[n],
                "sentences_live": live,
                "sentences_superseded": sup,
                "citation_share": round(cite_bytes / max(sizes[n], 1), 3),
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
        if d:
            wall[run.name] = {
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
    }
    if "--topics" in sys.argv:
        report["topics"] = topics
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
