# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v1` (5 cases)
- Provider/model: `openai_compatible` / `gpt-5.5`
- Dataset SHA256: `6683ece46fc3b9322048d73edb7c8113916fe3b3cd57c876fec31dfc4e9faa5c`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Groundedness | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.800 | 0.929 | 0.800 | 0.167 | 1.000 | 0.833 | 6063.2 | 106547.8 |

## Thresholds

- [x] full_task_success_rate: `0.8` (>= 0.8)
- [ ] full_hard_constraint_case_rate: `0.8` (== 1.0)
- [x] full_tool_selection_accuracy: `0.8` (>= 0.8)
- [ ] full_invalid_call_rate: `0.16666666666666666` (<= 0.1)
- [ ] full_groundedness: `0.8333333333333334` (>= 0.9)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Free-text groundedness uses exact frozen facts; semantic entailment requires a blinded judge or manual review.
