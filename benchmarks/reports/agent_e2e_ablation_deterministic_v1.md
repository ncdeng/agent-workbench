# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v1` (5 cases)
- Provider/model: `deterministic_proxy` / `scripted-deterministic-v1`
- Dataset SHA256: `a175032315da293fa6bb8f0d03c5aa450d07cacfdd3413ea98009ca49087b0c0`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 340.0 | 12.2 |
| no_memory | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 340.0 | 9.8 |
| no_context | 0.800 | 1.000 | 1.000 | 0.000 | 1.000 | 0.833 | 340.0 | 9.6 |
| no_planner | 0.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.0 | 16.7 |
| no_recovery | 0.800 | 1.000 | 1.000 | 0.000 | 0.000 | 1.000 | 360.0 | 8.5 |
| no_tool_use_memory | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 340.0 | 10.2 |

## Thresholds

- [x] full_task_success_rate: `1.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_tool_selection_accuracy: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [x] full_required_fact_recall: `1.0` (>= 0.9)
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
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; claim precision and semantic entailment require a blinded judge or manual review.
