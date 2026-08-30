# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-frozen-dev-v1` (40 unique cases; 40 samples)
- Repeats per case: `1`
- Split: `frozen_evaluation`
- Provider/model: `deterministic_proxy` / `scripted-deterministic-v1`
- Dataset SHA256: `f09b95cef13bba72193743cf81a693ba27773e44be3c73bb80c511c7350471f2`

| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 9.8 |
| no_memory | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 9.6 |
| no_context | 1.000 | 0.800 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.895 | 370.0 | 11.1 |
| no_planner | 0.000 | 0.000 | 0.606 | 0.100 | 0.100 | 0.000 | 0.000 | 0 | 0.000 | 0.0 | 22.6 |
| no_recovery | 0.800 | 0.800 | 0.970 | 1.000 | 1.000 | 0.000 | 0.000 | 0 | 0.947 | 390.0 | 9.7 |
| no_tool_use_memory | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 1.000 | 370.0 | 13.3 |

## 95% Wilson intervals

- full: execution `1.000` [0.912, 1.000]; strict-grounded `1.000` [0.912, 1.000]
- no_memory: execution `1.000` [0.912, 1.000]; strict-grounded `1.000` [0.912, 1.000]
- no_context: execution `1.000` [0.912, 1.000]; strict-grounded `0.800` [0.652, 0.895]
- no_planner: execution `0.000` [0.000, 0.088]; strict-grounded `0.000` [0.000, 0.088]
- no_recovery: execution `0.800` [0.652, 0.895]; strict-grounded `0.800` [0.652, 0.895]
- no_tool_use_memory: execution `1.000` [0.912, 1.000]; strict-grounded `1.000` [0.912, 1.000]

## Error taxonomy

- full: none
- no_memory: none
- no_context: grounding_or_context_failure=8
- no_planner: hard_constraint_violation=36, recovery_failure=8, trace_incomplete=40, wrong_tool_sequence=36
- no_recovery: hard_constraint_violation=8, recovery_failure=8, tool_execution_failure=8
- no_tool_use_memory: none

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
- [x] frozen_manifest_verified: `True` (true)
- [x] frozen_release_scope: `full_frozen_evaluation` (full_frozen_evaluation)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- 40 unique frozen_evaluation cases across 1 repeat(s) are not automatically a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.
- Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.
- This frozen set was visible to developers and must not be described as blinded held-out.
