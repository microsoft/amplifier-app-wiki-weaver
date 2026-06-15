# Per-Source Convergence Assessment: Source 548

**Source:** Allen Kuo (kwyshell), "Qwen3.6-35B-A3B on Desktop Blackwell: The First Time vLLM Beats Ollama on Decode"
**Source ID:** 548
**Assessment Date:** 2026-06-15

## Pages Touched by Source 548

1. **`llm-inference-optimization.md`** — New subsection "Model Architecture Changes the Winner: Hybrid-Attention MoE on Blackwell" added within Framework Selection section; frontmatter sources includes 548
2. **`vllm.md`** — New section "When vLLM Beats Ollama on Single-User Decode" added; Open Tensions entry added; frontmatter sources includes 548
3. **`gpu-hardware-for-llm-inference.md`** — RTX Pro 6000 Blackwell paragraph in Architecture Class 2 extended with [548] benchmark data; frontmatter sources includes 548
4. **`open-source-llm-landscape.md`** — Qwen3.6-35B-A3B section (lines 78-92) added with full architecture and deployment details; frontmatter sources includes 548
5. **`moe-expert-streaming.md`** — Line 49 adds cross-reference to [548] throughput figure for hardware-spectrum comparison; frontmatter sources includes 548

---

## Dimension Scores

### C1 Synthesis — Score: 4/5

**Evidence:** Source 548's claims land in thematically correct pages and are generally woven with other sources. In `llm-inference-optimization.md` line 223, [548] is cited alongside [379][430] in the same sentence. In `gpu-hardware-for-llm-inference.md` line 51, [548] is woven with [357] in a single paragraph about bandwidth-bound inference. In `moe-expert-streaming.md` line 49, [548] and [90] are fused in the same sentence. The vllm.md and open-source-llm-landscape.md sections are predominantly [548]-cited, though they cross-reference [379][517][90] in adjacent sentences. Score is 4 (not 5) because those two sections are primarily single-source, though thematically integrated.

### C2 Merge-Correctness — Score: 5/5

**Evidence:** All concepts from source 548 were merged into existing pages. No duplicate or near-duplicate page was created. The Qwen3.6-35B-A3B model entry was added as a subsection within the existing open-source-llm-landscape.md, not as a standalone page.

### C3 Contradiction-Handling — Score: 5/5

**Evidence:** Source 548 directly contradicts the existing claim that "Ollama wins single-user decode" (established by [379][430]). This genuine conflict is surfaced in vllm.md's "Open Tensions" section citing both sides with source IDs and marked "(status: architecture-dependent; unresolved for other model families)." The same tension is acknowledged in llm-inference-optimization.md. No averaging or silent overwrite occurred.

### C4 No-Confabulation — Score: 5/5

**Evidence:** Every non-trivial claim from [548] is attributed with inline [548] citations. Benchmark figures (208.6 tok/s, 144.2 tok/s, 25ms TTFT, 279,840 tokens in 21.4 GiB KV cache) are consistently tagged "from [548] and have not been independently verified." No invented facts or strawman framings detected.

### C5 Provenance — Score: 5/5

**Evidence:** All five pages carrying [548]'s claims have 548 in their frontmatter sources: list: llm-inference-optimization.md ✓, vllm.md ✓, gpu-hardware-for-llm-inference.md ✓, open-source-llm-landscape.md ✓, moe-expert-streaming.md ✓.

---

## Summary

| Dimension | Score | Status |
|-----------|-------|--------|
| C1 Synthesis | 4/5 | PASS |
| C2 Merge-Correctness | 5/5 | PASS |
| C3 Contradiction-Handling | 5/5 | PASS |
| C4 No-Confabulation | 5/5 | PASS |
| C5 Provenance | 5/5 | PASS |

**All dimensions >= 4/5. Source 548 has converged.**
