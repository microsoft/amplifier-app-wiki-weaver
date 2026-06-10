# Scenario 01 — Ground Truth

Hand-authored, quote-verified against the 6 source articles in `sources/`.
This file is the answer key: what a *good* woven wiki must capture, merge, and
flag. It is the basis for both the structural graders and the quality rubric.

Articles (referenced by number throughout):

1. `Andrej_Karpathy_Killed_RAG._Or_Did_He_The_LLM_Wiki_Pattern.md`
2. `Why_Andrej_Karpathys_LLM_Wiki_is_the_Future_of_Personal_Knowledge.md`
3. `LLM_Wiki_Skill_Build_a_Second_Brain_With_Claude_Code_and_Obsidian.md`
4. `How_I_Built_a_Self-Improving_LLM_Wiki_with_Hermes_Agent_(and_Why_Im_Not_Using_Obsidian).md`
5. `I_built_Karpathys_LLM_Wiki_twice_-_once_as_code,_once_as_a_.md.md`
6. `RAG_Isnt_Memory_These_5_Open-Source_Engines_Give_AI_Real_Memory.md`

---

## A. Entities that MUST merge into one page each

A good wiki creates ONE page per concept, fed by every source that discusses it
— not N near-duplicate pages. Coverage grader checks these exist and are
multi-sourced.

| canonical entity | sourced from | merge test |
|---|---|---|
| LLM Wiki pattern | 1,2,3,4,5 | single page, not one-per-article |
| Three-layer architecture (Raw Sources / Wiki / Schema) | 1,2,3,4 | one page; Art 5's 8-stage pipeline noted as a superset |
| Three operations (Ingest / Query / Lint) | 1,2,3,4 | one page |
| Karpathy gist ("idea file, not code") | 1,2,3,5 | one page |
| Obsidian (as interface) | 1,2,3,4 | one page that carries the C2 tension below |
| qmd (Tobi Lütke; BM25+vector; MCP) | 1,2,3 | one page |
| Scale ceiling (~100–200 docs) | 1,3,5 | one page; reconcile the numbers (see N1) |
| RAG limitations | 1,2,3,6 | one page that carries the C1 distinction below |
| Memory engines (Zep, Mem0, Letta, Memori, MemU) | 6 | one page (single-source ok) |

---

## C. Contradictions / tensions that MUST be surfaced (not averaged away)

The core hypothesis test: a compounding wiki should **flag** these as open
tensions on the relevant page. Silently picking one side, or averaging them into
mush, is a FAIL. Each is verified present in the text.

**C1 — What is RAG actually missing?**
Two genuinely different critiques that must NOT be conflated:
- *No knowledge accumulation* — Arts 1,2,3. Art 1: "Every query is a fresh start… There's no accumulation."
- *No user continuity/personalization* — Art 6. Art 6: "RAG… doesn't remember you. It won't recall what you told it last week."
- Verdict page must preserve BOTH as distinct gaps (they're complementary, not the same claim).

**C2 — Obsidian: yes or no?**
- *Yes, it's the natural interface* — Arts 1,2,3. Art 1: "Obsidian is the IDE. The LLM is the programmer. The wiki is the codebase."
- *No — desktop lock-in, maintenance debt* — Art 4 (title + body): "Obsidian is just one of many possible frontends. I'm no longer tied to a specific desktop app." Uses VPS + Telegram + web instead.
- Verdict: conditional on access pattern. Must be surfaced as a real disagreement.

**C3 — Implementation: agent-trust (markdown) vs structural guarantees (code).** *(most technically substantive)*
- *Agentic .md, trust the agent* — Arts 1–4.
- *Programmatic, don't trust agent markdown* — Art 5: structural noise "corrupts every downstream stage"; uses Pydantic v2 + `markdown-hero` for type-checked guarantees. Boundary: agent-trust holds for human-readable output; fails when the wiki feeds downstream automation.

**C4 — Retrieval at scale: BM25-only vs hybrid BM25+vector.**
- *BM25 alone is enough* — Art 5: "BM25 chat, no vector store… lexical retrieval is fast, transparent, and good enough. Save the embeddings infrastructure for when you actually need it."
- *Hybrid BM25+vector (qmd)* — Arts 1,2,3 recommend qmd's hybrid as the scale solution.

---

## N. Numeric / factual reconciliations

**N1 — Scale ceiling (consistent ballpark, different measures).** Art 1 ≈100 articles (Karpathy's own usage); Art 3 ≈200 pages / 100 sources; Art 5 "under ~200 documents." NOT a contradiction — wiki should give the ~100–200 range, not pick one number.

**N2 — Karpathy gist star/fork count (REAL within-corpus factual inconsistency).** ⭐ highest-signal probe.
- Art 1 (line 57): "5,000+ stars. **1,294 forks**. In **48 hours**."
- Art 3 (line 47): "5,000 stars in **four days**, nearly **3,000 forks**."
- Same event, **conflicting fork count and timeframe**. A good wiki must FLAG this discrepancy (e.g. note both figures with sources), not silently pick one or average to "~2000 forks." This is the cleanest test of contradiction-detection vs. confabulation.

---

## STRAWMAN — do NOT plant this as a contradiction

"RAG is dead." No article in the corpus actually argues this. It exists only as
the rhetorical question in Art 1's title, which Art 1 explicitly *rejects* ("RAG
is the search engine. LLM Wiki is the encyclopedia. Both useful."). If a woven
wiki asserts "RAG is dead" as a finding, that is a **confabulation FAIL** — the
inverse probe to N2.
