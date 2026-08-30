# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v2` (3 cases)
- Split: `development_regression`
- Provider/model: `openai_compatible` / `gpt-5.6-terra`
- Dataset SHA256: `973e8c9762658fc849be12acadddbca3be0b120429be4c74b219b73a07255f46`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.667 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.833 | 10196.0 | 66647.3 |
| no_context | 0.000 | 0.875 | 0.667 | 0.250 | 1.000 | 0 | 0.500 | 10217.3 | 31408.9 |

## Thresholds

- [ ] full_task_success_rate: `0.6666666666666666` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_tool_selection_accuracy: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_required_fact_recall: `0.8333333333333334` (>= 0.9)
- [x] full_planner_fallback_count: `0` (== 0)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)
- [x] no_context_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- 3 frozen development_regression cases are a regression gate, not a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; claim precision and semantic entailment require a blinded judge or manual review.
