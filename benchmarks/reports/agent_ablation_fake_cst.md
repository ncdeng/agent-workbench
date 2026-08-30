# Fake CST Agent Ablation Summary

- Cases: 20
- Max rounds: 6
- Proposal provider: deterministic_proxy

| Group | Success | Avg rounds | Avg tokens | Reject rate | Fallback rate | Repeat error | Memory hit | Memory changed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| heuristic_only | 0.60 | 1.17 | 0.0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| algorithm_baseline | 1.00 | 2.25 | 0.0 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| llm_no_memory | 0.50 | 2.20 | 758.5 | 0.07 | 0.07 | 0.00 | 0.00 | 0.00 |
| llm_with_memory | 0.60 | 2.00 | 676.0 | 0.00 | 0.00 | 0.00 | 0.06 | 0.03 |
| llm_memory_reflection | 0.60 | 2.00 | 748.0 | 0.00 | 0.00 | 0.00 | 0.06 | 0.03 |

## Memory impact

- Success-rate delta (with memory - no memory): +0.10
- Avg-round delta (with memory - no memory): -0.20
- Memory recall hit rate: 0.06
- Memory changed proposal rate: 0.03

## Failure cases

- heuristic_only: poor_feed_match_03 (-7.15 dB), mixed_offset_04 (-9.705 dB), poor_feed_match_08 (-7.425 dB), mixed_offset_09 (-9.385 dB), poor_feed_match_13 (-8.078 dB), +3 more
- algorithm_baseline: none
- llm_no_memory: poor_inset_match_02 (-7.957 dB), poor_feed_match_03 (-6.798 dB), mixed_offset_04 (-4.543 dB), poor_feed_match_08 (-7.199 dB), mixed_offset_09 (-4.981 dB), +5 more
- llm_with_memory: poor_inset_match_02 (-7.957 dB), mixed_offset_04 (-4.543 dB), mixed_offset_09 (-4.981 dB), poor_feed_match_13 (-7.73 dB), mixed_offset_14 (-5.599 dB), +3 more
- llm_memory_reflection: poor_inset_match_02 (-7.957 dB), mixed_offset_04 (-4.543 dB), mixed_offset_09 (-4.981 dB), poor_feed_match_13 (-7.73 dB), mixed_offset_14 (-5.599 dB), +3 more
