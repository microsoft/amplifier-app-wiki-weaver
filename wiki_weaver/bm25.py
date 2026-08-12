"""BM25 ranking -- the load-bearing retrieval mechanism for retrieve_slice.

Standard BM25 (Robertson/Sparck Jones): ``k1=1.5``, ``b=0.75``,
``IDF = ln((N - df + 0.5)/(df + 0.5) + 1)``. No third-party dependency.

This is measured, not preference (Experiment S7,
``.amplifier/evaluation/wiki-weaver/*/s7-bm25-result.md``): on a 365-page
corpus with topically-adjacent distractors, naive term-count retrieval got
17% recall@k=6; BM25 got 100%. k needed for >=90% recall: naive grows to
~40 (10% of the corpus); BM25 stays flat at 3. BM25 is a *precondition* of
the ingest cost law (docs/DESIGN.md §3), not an optimization -- without it,
per-source cost grows with wiki size.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens. Deliberately dumb -- DESIGN.md §14
    measured that even a naive full-text scan is ~5 orders of magnitude
    below one LLM pass; the ranking algorithm (BM25 vs term-count) is what
    matters for recall, not the tokenizer."""
    return _TOKEN_RE.findall(text.lower())


class BM25:
    """BM25 ranker over a fixed corpus of ``{doc_id: token_list}``."""

    def __init__(self, corpus: dict[str, list[str]], *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: list[str] = list(corpus.keys())
        self._term_freqs: dict[str, Counter[str]] = {doc_id: Counter(toks) for doc_id, toks in corpus.items()}
        self._doc_len: dict[str, int] = {doc_id: len(toks) for doc_id, toks in corpus.items()}

        self.n_docs = len(self.doc_ids)
        self.avgdl = (sum(self._doc_len.values()) / self.n_docs) if self.n_docs else 0.0

        df: Counter[str] = Counter()
        for tf in self._term_freqs.values():
            df.update(tf.keys())
        self._idf: dict[str, float] = {
            term: math.log((self.n_docs - freq + 0.5) / (freq + 0.5) + 1) for term, freq in df.items()
        }

    def idf(self, term: str) -> float:
        """IDF for a term seen in the corpus; 0.0 for an unseen term (it
        cannot match any document's term frequency, so it never
        contributes to that document's score anyway)."""
        return self._idf.get(term, 0.0)

    def score(self, query_tokens: list[str], doc_id: str) -> float:
        tf = self._term_freqs.get(doc_id)
        if tf is None or not query_tokens or not self.n_docs:
            return 0.0
        dl = self._doc_len.get(doc_id, 0)
        norm = self.k1 * (1 - self.b + self.b * (dl / self.avgdl if self.avgdl else 0.0))
        total = 0.0
        for term in query_tokens:
            f = tf.get(term, 0)
            if f == 0:
                continue
            total += self.idf(term) * (f * (self.k1 + 1)) / (f + norm)
        return total

    def top_k(self, query_tokens: list[str], k: int) -> list[tuple[str, float]]:
        """Top ``k`` (doc_id, score) pairs, highest score first. Ties break
        on doc_id ascending -- ranking must be deterministic (this feeds a
        pipeline artifact, ``.ai/current_slice.json``, not a UI)."""
        scored = [(doc_id, self.score(query_tokens, doc_id)) for doc_id in self.doc_ids]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:k]
