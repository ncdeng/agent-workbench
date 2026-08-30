# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v1` (1 cases)
- Provider/model: `openai_compatible` / `gpt-5.5`
- Dataset SHA256: `a71fcb3fc2a2be23047f3909e1bc48f9d87f7d9db51ee6dcb25b59554066fdd3`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Groundedness | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.000 | 1.000 | 1.000 | 0.000 | 1.000 | 0.000 | 8113.0 | 68433.3 |

## Thresholds

- [ ] full_task_success_rate: `0.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_tool_selection_accuracy: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_groundedness: `0.0` (>= 0.9)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Free-text groundedness uses exact frozen facts; semantic entailment requires a blinded judge or manual review.
