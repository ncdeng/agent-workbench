# Fake CST Agent Ablation Summary

- Cases: 5
- Max rounds: 4
- Proposal provider: openai_compatible
- Timestamp UTC: 2026-04-30T08:42:11+00:00
- Provider model: gpt-5.5
- Case seed range: 10000–10004

| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed | Memory compliance | Memory violation | Enforced |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| llm_no_memory | 0.80 | 1.75 | 1762.8 | 0.18 | 0.18 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| llm_with_memory | 1.00 | 1.60 | 1303.2 | 0.00 | 0.00 | 0.00 | 1.00 | 0.50 | 1.00 | 0.00 | 0.00 |

## Memory impact

- Success-rate delta (with memory - no memory): +0.20
- Avg-round delta (with memory - no memory): -0.15
- Memory recall hit rate: 1.00
- Memory changed proposal rate: 0.50
- Memory compliance rate: 1.00
- Memory violation rate: 0.00
- Memory enforced rate: 0.00

## Failure cases

- llm_no_memory: poor_feed_match_03 (-6.557 dB)
- llm_with_memory: none
