# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v2` (10 cases)
- Split: `development_regression`
- Provider/model: `deterministic_proxy` / `scripted-deterministic-v1`
- Dataset SHA256: `9127d1b9bfc4868977ca36ae7ce0ff8bffb146c60ec158efcef24136034ba9c0`

| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 11.4 |
| no_memory | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 10.6 |
| no_context | 1.000 | 0.700 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.769 | 370.0 | 12.6 |
| no_planner | 0.000 | 0.000 | 0.695 | 0.100 | 0.100 | 0.000 | 0.000 | 0 | 0.000 | 0.0 | 21.5 |
| no_recovery | 0.800 | 0.800 | 1.000 | 1.000 | 1.000 | 0.000 | 0.000 | 0 | 0.846 | 390.0 | 11.1 |
| no_tool_use_memory | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 11.1 |

## Thresholds

- [x] full_task_success_rate: `1.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_exact_tool_sequence_match_rate: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [x] full_lexical_required_fact_recall: `1.0` (>= 0.9)
- [x] full_planner_fallback_count: `0` (== 0)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)
- [x] no_memory_path_valid: `[]` (empty)
- [x] no_context_path_valid: `[]` (empty)
- [x] no_planner_path_valid: `[]` (empty)
- [x] no_recovery_path_valid: `[]` (empty)
- [x] no_tool_use_memory_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- 10 development_regression cases are not a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.
- Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.
- The development_regression split and its lexical oracle were iterated during development; it is not a blinded held-out set.
