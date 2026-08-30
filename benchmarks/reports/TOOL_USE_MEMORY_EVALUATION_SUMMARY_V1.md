# ToolUseMemory evaluation summary v1

## What was evaluated

The production ToolUseMemory path was evaluated in three separate layers. They must not be merged
into one accuracy claim.

1. The deterministic mechanism pair verifies production failure write, disk persistence,
   project-scoped cross-instance recall, and safe allowlist-only reranking.
2. The developer-visible v1 real-model pilot contains six status-audit paraphrases. Both cold and
   learned arms scored 6/6, so it is retained as a ceiling-effect null result.
3. The developer-visible v2 set contains eight independent tasks across four failure families:
   dedicated backend selection, parameter precondition, material precondition, and solver
   excitation preflight. Every authored argument-level oracle was executed successfully through
   Tool Runtime against Fake-CST before the first Terra run. All four training fixtures produced a
   production memory record, persisted it, recalled it in a new agent instance under the same
   project scope, and demoted the failed tool without changing the safe tool set.

The v2 dataset SHA is
`5ac1bb0c25e361116d9cff789f6aa12ded76a11f8d928b4af8304e312d6ad681`.
It is model-assisted, developer-visible, and not blinded.

## Real Terra repeat-3 result

Model: `gpt-5.6-terra`. Eight cases × two paired arms × three repeats produced 48 report samples.

| Metric | No memory | Learned memory |
|---|---:|---:|
| machine task success | 24/24 | 24/24 |
| case majority success | 8/8 | 8/8 |
| case all-repeats success | 8/8 | 8/8 |
| invalid call rate | 0 | 0 |
| failed-tool repetition rate | 0 | 0 |
| first-executor Memory application | 0/24 | 24/24 |
| mean tokens | 1,986.0 | 2,206.1 |

Every pair tied on machine task success. The learned-minus-cold success delta is 0 and the exact
paired McNemar/binomial test has no discordant cases (`p=1.0`). Memory therefore demonstrably
changed the tool context and ordering, but this dataset does not show a quality lift. Learned runs
used about 220 additional tokens per sample on average.

The report contains mean latency 34.8 s versus 20.6 s and p95 70.4 s versus 63.6 s. These numbers
must not be interpreted as Memory acceleration: after an outer Windows shell timeout, the original
Python child continued while a resume attempt briefly overlapped it. The final report and
checkpoint have identical records for all 48 sample IDs, so task/tool/token evidence is internally
consistent, but tail latency is contaminated by overlapping provider load. The provenance audit is
bound to report SHA
`63aa470a2a5cbbdb7892e290ec2ed0f28b42b1d8ec509d080361db968ecf3842`.

The runner now supports `--max-new-samples`, which exits itself after a completed sample is atomically
checkpointed. Future long runs must use that mechanism instead of depending on a shell timeout.

## Earlier repeat-1 semantic diagnostic

The first v2 repeat-1 run used the same dataset but an earlier observability revision of the runner.
The strict machine grader scored both arms 7/8. A response-SHA-bound single-reviewer audit judged
the cold arm 7/8 and learned arm 8/8: learned used the correct port plus one harmless status check,
while cold used the wrong port axis and then claimed the requested coordinates in its answer.
That is one directional learned win, seven ties, and exact paired `p=1.0`; it is not evidence of a
Memory gain. The later repeat-3 run's 48/48 result also shows that the one-shot difference was not a
stable effect.

## Deterministic semantic revalidation

The runner now preserves exact sequence as a strict diagnostic while also computing a separate,
deterministic semantic endpoint. Required material calls must appear as an ordered subsequence with
matching arguments and successful tool results. Only the explicit harmless-extra allowlist
(`check_cst_status`) may appear outside that subsequence. Material extra calls fail the endpoint.
The grader separately checks whether the final response's success/failure stance is consistent with
the verified tool outcome; this is deliberately called **outcome consistency**, not claim-level
natural-language groundedness.

Two zero-model-call revalidations bind the source report, dataset/manifest, grader contract, grader
code and revalidator code by SHA:

- Repeat-1: all 16 samples were score-valid. Deterministic semantic success was cold 7/8 and learned
  8/8. It identified one learned exact-sequence false negative caused by a harmless status check and
  one cold unsupported success claim after the wrong port-axis arguments. Its task-success labels
  agreed 16/16 with the existing response-SHA-bound model-assisted single review. That agreement is
  a useful regression check, not independent human calibration. The revalidation report SHA is
  `da47d3c7faf1d964ca36121c6844edb18424a3e49f02e51a99a38ce5fea6dee7`.
- Repeat-3: all 48 samples were score-valid; both arms were 24/24 on deterministic semantic success
  and 24/24 on final-response outcome consistency, with zero unsupported success claims. At the
  independent-case level both majority and all-repeats endpoints were 8/8 versus 8/8, delta 0 and
  exact paired `p=1.0`. The revalidation report SHA is
  `d6e88d16f7d04fbdce533598253df06b1eab3cfd7231a71667e6b089fcc88548`.

Arm-level 24-call rates are descriptive correlated-call statistics and receive no confidence
interval. Wilson intervals and exact paired inference are reported only after reducing repeats to
the eight unique-case majority/all-repeats endpoints. Because this grader was added after the
saved outputs, both revalidations are explicitly post-hoc. New runner v3 reports fingerprint the
grader bytes and compute the same endpoint during execution, so future runs can freeze it before
model outputs exist.

## Evaluation conclusion

The defensible claim is:

> ToolUseMemory is a project-scoped procedural-memory control plane whose production write,
> persistence, recall, guidance injection, and safe-tool reranking are verified. In an eight-case,
> four-family developer-visible Terra A/B with three repeats, Memory was applied in 24/24 learned
> samples but both arms completed 24/24, so no success-rate lift was demonstrated; the learned arm
> paid roughly 220 extra tokens per sample.

Do not claim “Memory improves Agent success rate” until an independently authored
sealed set produces a stable paired lift. The current result is valuable because it separates
mechanism effectiveness from behavioral effectiveness and retains null findings.
