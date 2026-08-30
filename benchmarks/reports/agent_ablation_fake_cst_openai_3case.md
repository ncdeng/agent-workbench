# Fake CST Agent Ablation Summary

- Cases: 3
- Max rounds: 4
- Proposal provider: openai_compatible
- Timestamp UTC: 2026-07-24T14:34:30+00:00
- Provider model: deepseek-v4-pro
- Case seed range: 10000–10002

| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed | Memory compliance | Memory violation | Enforced | Provider errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| llm_no_memory | 1.00 | 1.00 | 185.0 | 1.00 | 1.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llm_with_memory | 0.67 | 3.00 | 1116.7 | 1.00 | 1.30 | 0.00 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0 |

## Memory impact

- Success-rate delta (with memory - no memory): -0.33
- Avg-round delta (with memory - no memory): +2.00
- Memory recall hit rate: 1.00
- Memory changed proposal rate: 0.00
- Memory compliance rate: 0.00
- Memory violation rate: 1.00
- Memory enforced rate: 1.00

## Failure cases

- llm_no_memory: none
- llm_with_memory: poor_inset_match_02 (-9.296 dB)
