# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-frozen-dev-v3` (40 unique cases; 40 samples)
- Repeats per case: `1`
- Split: `frozen_evaluation`
- Provider/model: `deterministic_proxy` / `scripted-deterministic-v1`
- Agent Harness: `native`
- Harness SHA256: `2819f33582357d2e4a364bafe0a81af918fb2e018796e5a67c72b9368bc3cc02`
- Dataset SHA256: `df75e8e980f084fca946db659f2b775cc75cb9051ac3983a0b71df86415c1606`

| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 0.900 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.929 | 370.0 | 10.1 |
| no_memory | 1.000 | 0.900 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.929 | 370.0 | 9.7 |
| no_context | 1.000 | 0.700 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.786 | 370.0 | 9.0 |
| no_planner | 0.000 | 0.000 | 0.618 | 0.100 | 0.100 | 0.000 | 0.000 | 0 | 0.000 | 0.0 | 24.6 |
| no_recovery | 0.800 | 0.700 | 0.971 | 1.000 | 1.000 | 0.000 | 0.000 | 0 | 0.786 | 390.0 | 12.6 |
| no_tool_use_memory | 1.000 | 0.900 | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.929 | 370.0 | 13.0 |

## Sample-level 95% Wilson intervals

- full: execution `1.000` [0.912, 1.000]; strict-grounded `0.900` [0.769, 0.960]
- no_memory: execution `1.000` [0.912, 1.000]; strict-grounded `0.900` [0.769, 0.960]
- no_context: execution `1.000` [0.912, 1.000]; strict-grounded `0.700` [0.546, 0.819]
- no_planner: execution `0.000` [0.000, 0.088]; strict-grounded `0.000` [0.000, 0.088]
- no_recovery: execution `0.800` [0.652, 0.895]; strict-grounded `0.700` [0.546, 0.819]
- no_tool_use_memory: execution `1.000` [0.912, 1.000]; strict-grounded `0.900` [0.769, 0.960]

## Repeat stability by unique case

- full: all-repeat success `1.000`; majority success `1.000`; task instability `0.000`; tool-sequence instability `0.000`
- no_memory: all-repeat success `1.000`; majority success `1.000`; task instability `0.000`; tool-sequence instability `0.000`
- no_context: all-repeat success `1.000`; majority success `1.000`; task instability `0.000`; tool-sequence instability `0.000`
- no_planner: all-repeat success `0.000`; majority success `0.000`; task instability `0.000`; tool-sequence instability `0.000`
- no_recovery: all-repeat success `0.800`; majority success `0.800`; task instability `0.000`; tool-sequence instability `0.000`
- no_tool_use_memory: all-repeat success `1.000`; majority success `1.000`; task instability `0.000`; tool-sequence instability `0.000`

## Error taxonomy

- full: grounding_or_context_failure=4
- no_memory: grounding_or_context_failure=4
- no_context: grounding_or_context_failure=12
- no_planner: hard_constraint_violation=36, recovery_failure=8, trace_incomplete=40, wrong_tool_sequence=36
- no_recovery: grounding_or_context_failure=4, hard_constraint_violation=8, recovery_failure=8, tool_execution_failure=8
- no_tool_use_memory: grounding_or_context_failure=4

## Thresholds

- [x] full_task_success_rate: `1.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_exact_tool_sequence_match_rate: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [x] full_lexical_required_fact_recall: `0.9285714285714286` (>= 0.9)
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
- Sample-level confidence intervals are descriptive because repeated provider calls for one task are correlated; use repeat_stability for unique-case conclusions.
- Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.
- Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.
- This frozen set was visible to developers and must not be described as blinded held-out.
