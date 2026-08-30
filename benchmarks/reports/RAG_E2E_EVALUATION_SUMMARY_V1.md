# CST Official-Documentation RAG — E2E Evaluation Summary v1

Frozen on 2026-08-09. Large models, indexes, caches, session artifacts and temporary files were stored under
`D:\cst_agent_rag_data`; the repository contains only compact benchmark inputs and reports.

## Retrieval

- Corpus: CST Studio Suite 2025 Online Help, 3,886 content-bearing pages / 13,242 chunks.
- Dense model: `BAAI/bge-base-en-v1.5`, query instruction only, 768 dimensions.
- Production: dense Top-20 chunks → `cross-encoder/ms-marco-MiniLM-L6-v2` → source dedup → Top-3.
- Dataset: `rag_official_docs_heldout_v1.json`, 30 frozen cases; zh/en/mixed = 10/10/10; graded qrels 0–3.

| Pipeline | Recall@3 | MRR | nDCG@3 | warm p95 |
|---|---:|---:|---:|---:|
| Dense | 0.900 | 0.750 | 0.754 | 63.29 ms |
| MiniLM rerank | 0.967 | 0.794 | 0.817 | 1019.50 ms |
| BGE-reranker-base | 0.967 | 0.772 | 0.799 | 6328.98 ms |

MiniLM rerank has Recall@5=0.967 and Recall@10=1.000. All 30 Top-3 cases were actually reranked; fallback=0.
These latency values are the 2026-08-10 same-machine observations and vary with workstation load; retrieval quality
was unchanged from the earlier run. `benchmarks/rag_official_canonical.json` pins the byte-level dataset SHA, index
contract and the three CLI presets. The dataset is developer-visible (`blinded=false`), not a blinded benchmark.

## Full Agent subset

Runner: `benchmarks/agent_rag_groundedness_eval.py`. It executes the real Planner, query translation, dense retrieval,
reranker, Executor and Trace. Only CST side effects are disabled. The subset is deterministic: first two frozen cases per
language (6 total), not selected by outcome.

| Metric | Value |
|---|---:|
| Completion | 6/6 |
| qrel Recall@3 | 0.833 |
| Citation presence / precision | 1.000 / 1.000 |
| Provenance / Trace completeness | 1.000 / 1.000 |
| Groundedness / answer relevance | 0.863 / 0.883 |
| Unsupported claim rate | 0.172 |
| E2E latency mean / p95 | 37.0 s / 60.8 s |

The single qrel miss retrieved plausible alternative official farfield-source documents, showing that one-positive qrels
are not exhaustive. The frozen qrels were not edited after inspection. Groundedness uses an LLM judge constrained to the
retrieved evidence. These 0.863/0.172 values are a six-case, single-run, uncalibrated-judge development diagnostic, not
human gold and not an overstated effect claim. The v2 runner now requires atomic claims with exact Top-3 evidence refs,
validates count/score invariants, and binds dataset/code/execution-contract, answer, evidence, prompt and raw-response
SHA256 identities. `benchmarks/agent_rag_claim_review.py` exports verdict-blind review packs and can compute claim-level
Cohen's kappa and machine-vs-adjudicated precision/recall/F1. No real two-reviewer plus adjudicator artifact exists yet.
