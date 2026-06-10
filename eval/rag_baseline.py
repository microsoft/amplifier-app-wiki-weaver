#!/usr/bin/env python3
"""Faithful naive-RAG baseline: lexical retrieval over the raw sources.

This is Variant B of the A/B. It models what plain RAG does — retrieve the
top-k chunks for a query and answer from those alone, with NO pre-compiled
cross-source synthesis and NO contradiction awareness. Deterministic (BM25-ish
token overlap), no API, so the comparison is honest and reproducible.

For each question it prints the retrieved chunks. A RAG-answerer agent then
answers each question using ONLY its retrieved chunks (no global view of the
corpus), which is the whole point of the contrast with the woven wiki.

Usage: python rag_baseline.py <sources_dir> [--k 5] [--chunk-words 220]
"""

from __future__ import annotations

import argparse
import math
import re
from collections import Counter
from pathlib import Path

# High-signal A/B questions (subset of questions.yaml: cross-source +
# contradiction-probe + one single-source calibration).
QUESTIONS = {
    "D1": "What problem does LLM Wiki solve, and is it the same problem that memory engines like Zep or Mem0 solve?",
    "D3": "Should you use Obsidian to build an LLM Wiki?",
    "D4": "What is the difference between using a SKILL.md, an AGENTS.md, and a Python package to implement Karpathy's pattern, and when should you choose each?",
    "C-FORKS": "How many stars and forks did Karpathy's LLM Wiki gist get, and how fast?",
    "C-RAGDEAD": "Do these articles claim RAG is dead or obsolete?",
    "E3": "What infrastructure does the Hermes-based LLM wiki run on?",
}

TOKEN = re.compile(r"[a-z0-9]+")
STOP = set(
    "the a an and or of to in is are for on with as it its this that these those "
    "be by at from into not no do does what how when which who you your we our".split()
)


def tokens(s: str) -> list[str]:
    return [t for t in TOKEN.findall(s.lower()) if t not in STOP and len(t) > 2]


def chunk(text: str, n_words: int) -> list[str]:
    words = text.split()
    return [" ".join(words[i : i + n_words]) for i in range(0, len(words), n_words)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sources_dir", type=Path)
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--chunk-words", type=int, default=220)
    args = ap.parse_args()

    chunks: list[tuple[str, str]] = []  # (source_name, chunk_text)
    for p in sorted(args.sources_dir.glob("*.md")):
        body = p.read_text(encoding="utf-8", errors="replace")
        for c in chunk(body, args.chunk_words):
            if c.strip():
                chunks.append((p.stem, c))

    # IDF over chunks.
    df: Counter[str] = Counter()
    chunk_toks: list[set[str]] = []
    for _, c in chunks:
        ts = set(tokens(c))
        chunk_toks.append(ts)
        for t in ts:
            df[t] += 1
    n = len(chunks)
    idf = {t: math.log(1 + n / (1 + d)) for t, d in df.items()}

    print(f"# Naive-RAG retrieval context\n\n{n} chunks from {args.sources_dir}\n")
    for qid, q in QUESTIONS.items():
        qt = tokens(q)
        scored = []
        for i, (src, c) in enumerate(chunks):
            score = sum(idf.get(t, 0) for t in qt if t in chunk_toks[i])
            scored.append((score, src, c))
        scored.sort(key=lambda x: -x[0])
        print(f"\n## {qid}: {q}\n")
        for rank, (score, src, c) in enumerate(scored[: args.k], 1):
            snippet = c[:700]
            print(f"### chunk {rank} (score {score:.1f}, source {src})\n{snippet}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
