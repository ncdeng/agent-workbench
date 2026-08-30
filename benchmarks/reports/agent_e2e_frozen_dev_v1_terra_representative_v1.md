# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-frozen-dev-v1` (7 unique cases; 7 samples)
- Repeats per case: `1`
- Split: `frozen_evaluation`
- Provider/model: `openai_compatible` / `gpt-5.6-terra`
- Dataset SHA256: `f09b95cef13bba72193743cf81a693ba27773e44be3c73bb80c511c7350471f2`

| Group | Execution success | Strict lexical-grounded | Constraints | Exact tool sequence | First tool | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.714 | 0.286 | 0.848 | 0.857 | 0.857 | 0.000 | 1.000 | 0 | 0.462 | 5644.4 | 77450.4 |

## 95% Wilson intervals

- full: execution `0.714` [0.359, 0.918]; strict-grounded `0.286` [0.082, 0.641]

## Error taxonomy

- full: grounding_or_context_failure=3, hard_constraint_violation=2, wrong_tool_sequence=1

## Thresholds

- [ ] full_task_success_rate: `0.7142857142857143` (>= 0.8)
- [ ] full_hard_constraint_case_rate: `0.7142857142857143` (== 1.0)
- [x] full_exact_tool_sequence_match_rate: `0.8571428571428571` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_lexical_required_fact_recall: `0.46153846153846156` (>= 0.9)
- [x] full_planner_fallback_count: `0` (== 0)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)
- [x] frozen_manifest_verified: `True` (true)
- [ ] frozen_release_scope: `filtered_debug` (full_frozen_evaluation)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- 7 unique frozen_evaluation cases across 1 repeat(s) are not automatically a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; task_success reports execution success separately, while strict_grounded_success additionally requires lexical fact matches.
- Claim precision and semantic entailment remain unevaluated until a blinded manual or calibrated claim-level review is attached.
- This frozen set was visible to developers and must not be described as blinded held-out.
- This is a filtered/debug frozen-set run and is not release-eligible; use full_frozen_eval with no filters for canonical thresholds.
