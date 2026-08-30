# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v2` (3 unique cases; 9 samples)
- Repeats per case: `3`
- Split: `development_regression`
- Provider/model: `openai_compatible` / `gpt-5.6-terra`
- Dataset SHA256: `9127d1b9bfc4868977ca36ae7ce0ff8bffb146c60ec158efcef24136034ba9c0`

| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 0.667 | 1.000 | 1.000 | 1.000 | 0.000 | n/a | 0 | 0.800 | 8905.2 | 50645.3 |

## 95% Wilson intervals

- full: execution `1.000` [0.701, 1.000]; strict-grounded `0.667` [0.354, 0.879]

## Error taxonomy

- full: grounding_or_context_failure=3

## Thresholds

- [x] full_task_success_rate: `1.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_exact_tool_sequence_match_rate: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_lexical_required_fact_recall: `0.8` (>= 0.9)
- [x] full_planner_fallback_count: `0` (== 0)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- 3 unique development_regression cases across 3 repeat(s) are not automatically a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.
- Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.
- The development_regression split and its lexical oracle were iterated during development; it is not a blinded held-out set.
