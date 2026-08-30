# Fake CST Agent Ablation Summary

- Cases: 50
- Max rounds: 6
- Proposal provider: deterministic_proxy
- Timestamp UTC: 2026-04-30T12:16:31+00:00
- Provider model: 
- Case seed range: 10000–10049

| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed | Memory compliance | Memory violation | Enforced | Provider errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| heuristic_only | 0.64 | 1.50 | 0.0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| algorithm_baseline | 1.00 | 2.28 | 0.0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llm_no_memory | 0.84 | 2.71 | 599.4 | 0.00 | 0.28 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0 |
| llm_with_memory | 0.84 | 2.45 | 597.7 | 0.00 | 0.27 | 0.00 | 0.09 | 0.09 | 1.00 | 0.00 | 0.00 | 0 |
| llm_memory_reflection | 0.84 | 2.45 | 658.1 | 0.00 | 0.27 | 0.00 | 0.09 | 0.09 | 1.00 | 0.00 | 0.00 | 0 |

## Memory impact

- Success-rate delta (with memory - no memory): +0.00
- Avg-round delta (with memory - no memory): -0.26
- Memory recall hit rate: 0.09
- Memory changed proposal rate: 0.09
- Memory compliance rate: 1.00
- Memory violation rate: 0.00
- Memory enforced rate: 0.00

## Failure cases

- heuristic_only: poor_feed_match_03 (-7.15 dB), mixed_offset_04 (-9.705 dB), poor_feed_match_08 (-7.425 dB), mixed_offset_09 (-9.385 dB), poor_feed_match_13 (-8.078 dB), +13 more
- algorithm_baseline: none
- llm_no_memory: mixed_offset_04 (-8.681 dB), mixed_offset_09 (-9.257 dB), mixed_offset_14 (-9.744 dB), mixed_offset_19 (-8.891 dB), mixed_offset_29 (-9.426 dB), +3 more
- llm_with_memory: mixed_offset_04 (-8.681 dB), mixed_offset_09 (-9.257 dB), mixed_offset_14 (-9.744 dB), mixed_offset_19 (-8.891 dB), mixed_offset_29 (-9.426 dB), +3 more
- llm_memory_reflection: mixed_offset_04 (-8.681 dB), mixed_offset_09 (-9.257 dB), mixed_offset_14 (-9.744 dB), mixed_offset_19 (-8.891 dB), mixed_offset_29 (-9.426 dB), +3 more
