# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v2` (10 cases)
- Provider/model: `openai_compatible` / `gpt-5.6-terra`
- Dataset SHA256: `a6bec4756b2c326fc6b929d0ff42c7df97287bcb72efb410d201cf4ed3c958f0`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.700 | 0.964 | 0.700 | 0.182 | 0.500 | 1.000 | 6915.1 | 57559.6 |

## Thresholds

- [ ] full_task_success_rate: `0.7` (>= 0.8)
- [ ] full_hard_constraint_case_rate: `0.9` (== 1.0)
- [ ] full_tool_selection_accuracy: `0.7` (>= 0.8)
- [ ] full_invalid_call_rate: `0.18181818181818182` (<= 0.1)
- [x] full_required_fact_recall: `1.0` (>= 0.9)
- [x] full_ablation_path_valid: `[]` (empty)
- [ ] full_recovery_rate: `0.5` (== 1.0)
- [x] full_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; claim precision and semantic entailment require a blinded judge or manual review.
