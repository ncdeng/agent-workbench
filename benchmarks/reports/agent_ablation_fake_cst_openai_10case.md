# Fake CST Agent Ablation Summary

- Cases: 10
- Max rounds: 4
- Proposal provider: openai_compatible
- Timestamp UTC: 2026-04-30T10:03:15+00:00
- Provider model: gpt-5.5
- Case seed range: 10000–10009

| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed | Memory compliance | Memory violation | Enforced | Provider errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| llm_no_memory | 0.70 | 1.71 | 1993.3 | 0.21 | 0.21 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llm_with_memory | 1.00 | 1.50 | 1212.1 | 0.00 | 0.00 | 0.00 | 1.00 | 0.47 | 1.00 | 0.00 | 0.00 | 0 |

## Memory impact

- Success-rate delta (with memory - no memory): +0.30
- Avg-round delta (with memory - no memory): -0.21
- Memory recall hit rate: 1.00
- Memory changed proposal rate: 0.47
- Memory compliance rate: 1.00
- Memory violation rate: 0.00
- Memory enforced rate: 0.00

## Failure cases

- llm_no_memory: poor_feed_match_03 (-6.587 dB), poor_feed_match_08 (-6.517 dB), mixed_offset_09 (-8.877 dB)
- llm_with_memory: none
