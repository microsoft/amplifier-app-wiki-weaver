"""BM25 is the load-bearing retrieval mechanism (S7 result). These tests
verify the two properties the ingest cost law depends on: relevant pages
outrank irrelevant ones, and common terms are down-weighted by IDF."""

from __future__ import annotations

from wiki_weaver.bm25 import BM25, tokenize


def test_tokenize_lowercases_and_splits_on_non_alnum():
    assert tokenize("Andrej Karpathy's LLM-Wiki Pattern!") == [
        "andrej",
        "karpathy",
        "s",
        "llm",
        "wiki",
        "pattern",
    ]


def test_bm25_ranks_relevant_page_above_irrelevant_one():
    corpus = {
        "quokka.md": tokenize("Quokkas are small marsupials native to Western Australia. Quokka habitat is shrinking."),
        "generic.md": tokenize(" ".join(["the"] * 50 + ["document", "about", "nothing", "in", "particular"])),
    }
    ranker = BM25(corpus)
    query = tokenize("quokka habitat")
    ranked = ranker.top_k(query, k=2)
    assert ranked[0][0] == "quokka.md"
    assert ranked[0][1] > ranked[1][1]
    assert ranked[1][1] == 0.0  # "generic.md" shares no query terms at all


def test_bm25_idf_down_weights_common_terms():
    # "common" appears in every document; "rare" appears in exactly one.
    corpus = {
        "a.md": tokenize("common common rare topic alpha"),
        "b.md": tokenize("common common beta"),
        "c.md": tokenize("common common gamma"),
        "d.md": tokenize("common common delta"),
    }
    ranker = BM25(corpus)
    assert ranker.idf("rare") > ranker.idf("common")

    # A document matching only on the rare term should still be findable
    # above documents that only share the ubiquitous "common" term.
    hits = ranker.top_k(tokenize("rare"), k=4)
    assert hits[0][0] == "a.md"
    assert hits[0][1] > 0.0


def test_bm25_top_k_is_deterministic_on_ties():
    corpus = {name: [] for name in ("z.md", "a.md", "m.md")}
    ranker = BM25(corpus)
    # No query terms match anything -- every score is 0.0, tie-break must
    # be alphabetical by doc_id so downstream ordering is reproducible.
    hits = ranker.top_k(tokenize("anything"), k=3)
    assert [doc_id for doc_id, _ in hits] == ["a.md", "m.md", "z.md"]


def test_bm25_empty_corpus_does_not_crash():
    ranker = BM25({})
    assert ranker.top_k(tokenize("anything"), k=6) == []
