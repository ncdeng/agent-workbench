# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v1` (5 cases)
- Provider/model: `deterministic_proxy` / `gpt-5.5`
- Dataset SHA256: `59d716875c96a18a55beba729827fd4c4831e0ea22efaad7b4ba4a462d1c7e93`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Groundedness | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.000 | 1.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.0 | 145397.4 |

## Thresholds

- [ ] full_task_success_rate: `0.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [ ] full_tool_selection_accuracy: `0.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_groundedness: `0.0` (>= 0.9)
- [ ] full_ablation_path_valid: `['prior_turn_missing_from_context']` (empty)
- [ ] full_recovery_rate: `0.0` (== 1.0)
- [ ] full_path_valid: `['prior_turn_missing_from_context']` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Free-text groundedness uses exact frozen facts; semantic entailment requires a blinded judge or manual review.
