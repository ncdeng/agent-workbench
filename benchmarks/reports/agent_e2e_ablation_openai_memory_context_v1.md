# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v1` (2 cases)
- Provider/model: `openai_compatible` / `gpt-5.5`
- Dataset SHA256: `693f531e8a723f5fdc2c22f023aaa64df06ac3deb3e30795e5167314a6e997c2`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Groundedness | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 1.000 | 1.000 | 1.000 | 0.000 | n/a | 1.000 | 6312.5 | 77368.7 |
| no_memory | 0.500 | 1.000 | 0.500 | 0.000 | n/a | 0.667 | 4149.0 | 32497.2 |
| no_context | 0.500 | 0.833 | 0.500 | 0.333 | n/a | 0.667 | 7641.5 | 48000.6 |
| no_tool_use_memory | 1.000 | 1.000 | 1.000 | 0.000 | n/a | 1.000 | 6176.0 | 30472.9 |

## Thresholds

- [x] full_task_success_rate: `1.0` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_tool_selection_accuracy: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [x] full_groundedness: `1.0` (>= 0.9)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_path_valid: `[]` (empty)
- [x] no_memory_path_valid: `[]` (empty)
- [x] no_context_path_valid: `[]` (empty)
- [x] no_tool_use_memory_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Free-text groundedness uses exact frozen facts; semantic entailment requires a blinded judge or manual review.
