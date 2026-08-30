# CST-Agent Full E2E Ablation

- Dataset: `cst-agent-e2e-fake-cst-v2` (3 cases)
- Provider/model: `openai_compatible` / `gpt-5.6-terra`
- Dataset SHA256: `d2220bbd4fc5fb3ae54d4ee74df39389b5037146e841afa5b9e5f9ec7026c4c8`

| Group | Success | Constraints | Tool accuracy | Invalid calls | Recovery | Planner fallback | Required facts | Tokens mean | p95 latency ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| full | 0.667 | 1.000 | 1.000 | 0.000 | 1.000 | 0 | 0.750 | 7775.0 | 55048.1 |

## Thresholds

- [ ] full_task_success_rate: `0.6666666666666666` (>= 0.8)
- [x] full_hard_constraint_case_rate: `1.0` (== 1.0)
- [x] full_tool_selection_accuracy: `1.0` (>= 0.8)
- [x] full_invalid_call_rate: `0.0` (<= 0.1)
- [ ] full_required_fact_recall: `0.75` (>= 0.9)
- [x] full_planner_fallback_count: `0` (== 0)
- [x] full_ablation_path_valid: `[]` (empty)
- [x] full_recovery_rate: `1.0` (== 1.0)
- [x] full_path_valid: `[]` (empty)

## Honest boundary

- Fake CST validates orchestration and recovery contracts, not CST electromagnetic fidelity.
- The deterministic provider is a mechanism smoke test, bypasses RAG retrieval, uses lexical memory fallback, and must not be cited as LLM quality evidence.
- Five frozen cases are a regression gate, not a statistically powered benchmark.
- Required-fact recall uses case-authored lexical alternatives; claim precision and semantic entailment require a blinded judge or manual review.
