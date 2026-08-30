# Human Evaluation Rubric

This document defines the exact binary-label policy used by the Agent E2E and RAG groundedness double-review workflows. Reviewer packs bind the SHA256 of these bytes. Change the rubric version before changing label semantics; do not silently reinterpret completed reviews.

## Common Review Rules

1. Judge only the `review_context` and evidence supplied in the assigned pack. Do not open the source report, machine scores, another reviewer's file, or external CST documentation.
2. Complete every boolean field. `null` means unfinished, not uncertain. If the supplied evidence is insufficient, choose the conservative negative label and explain the ambiguity in `notes`.
3. Do not alter IDs, SHA fields, `pack_id`, protocol metadata, ordering, or review context.
4. Do not reward fluent wording. Judge whether the required outcome is evidenced and whether the response stays within that evidence.
5. Reviewers work independently. Discussion is allowed only after both files are frozen and only through the disagreement-only adjudication stage.

## Agent Semantic Rubric V1

Protocol identifier: `agent-semantic-rubric-v1`.

The Agent pack contains user messages, tool events, recovery events, and the final response. Reviewers assign two labels. `semantic_task_success` is later derived as the conjunction of these labels; reviewers do not fill it directly.

### `semantic_execution_success`

Label `true` only when the supplied execution evidence demonstrates the material outcome requested by the user.

Accept as successful:

- all task-critical tool calls succeed with materially correct arguments;
- a failure occurs but the trace shows a completed recovery/retry and the requested outcome is subsequently achieved;
- harmless read-only or diagnostic calls appear in addition to the required sequence;
- the final response is terse, provided the execution evidence itself proves the outcome.

Label `false` when any of the following applies:

- a required action or state transition is missing;
- the only task-critical call fails and no successful recovery is evidenced;
- arguments are materially wrong for the user request, even if the tool reports transport-level success;
- the Agent performs a different task, stops before the requested outcome, exceeds a zero-tool constraint, or makes a forbidden material call;
- the final response claims success but the tool evidence does not show the required execution.

Execution success is not electromagnetic correctness. Fake-CST events can prove the control-flow contract only; a real CST result needs separate live evidence.

### `grounded_final_response`

Label `true` only when every material statement about actions, outcomes, failures, recovery, and returned values is consistent with the supplied tool/recovery evidence.

Accept as grounded:

- the response accurately reports success demonstrated by the trace;
- the response accurately reports failure, uncertainty, or inability when execution did not complete;
- the response omits nonessential details without inventing results;
- wording differs from the oracle but preserves the evidenced meaning.

Label `false` when any of the following applies:

- it claims success after a failed or missing state transition;
- it invents a result, metric, file, parameter value, recovery, or CST state absent from the trace;
- it contradicts tool output or hides a material failure behind an unconditional success statement;
- it states that a recovery completed when only an attempted recovery is shown.

Do not fail grounding merely because the response is incomplete or stylistically weak. Missing task execution belongs under `semantic_execution_success`; unsupported factual claims belong under `grounded_final_response`.

### Agent Examples

| Evidence | Final response | Execution | Grounded |
| --- | --- | ---: | ---: |
| Required calls succeed; one harmless status read is added | Accurately reports completion | true | true |
| Required call uses the wrong material/axis but returns success | Claims the requested task completed | false | false |
| Required call fails; retry succeeds after bounded recovery | Reports the recovered completion | true | true |
| Required call fails with no recovery | Clearly reports the failure and does not claim completion | false | true |
| Trace succeeds | Invents an S11 value not present in evidence | true | false |

## RAG Claim-Support Rubric V1

Protocol identifier: `rag-claim-support-rubric-v1`.

The RAG pack contains one machine-segmented claim, the full saved answer, the query, and the exact retrieved Top-3 evidence. This rubric calibrates the machine support label; it does not judge whether the machine omitted claims or split them optimally.

### `supported`

Label `true` only when every material proposition in the claim is directly supported by at least one supplied evidence chunk.

Requirements for `true`:

- support must come from the supplied `evidence`, not outside knowledge or the answer's citation text alone;
- the cited `source_path` and `chunk_idx` must exactly identify supporting supplied chunks;
- product names, menu paths, units, solver restrictions, version qualifiers, and numerical conditions must agree with the evidence;
- a small paraphrase or logically equivalent restatement is allowed when it does not add a new technical assertion.

Label `false` when any of the following applies:

- only part of a compound claim is supported;
- the claim adds a recommendation, causal explanation, limitation, value, unit, or workflow step absent from the supplied evidence;
- the evidence is related but does not entail the claim;
- the claim overgeneralizes a version-, solver-, port-, mode-, or format-specific statement;
- support would require external CST knowledge or an unsupported inference.

For `supported=true`, add every necessary exact evidence reference. For `supported=false`, leave `evidence_refs` empty and briefly note the unsupported part when it is not obvious.

### RAG Examples

| Claim and supplied evidence | Supported | Evidence refs |
| --- | ---: | --- |
| Claim paraphrases an explicit menu path and format description | true | exact source/chunk |
| Evidence supports Touchstone export, but claim additionally asserts an undocumented default impedance | false | empty |
| Evidence states a value is in metres; claim reports millimetres | false | empty |
| Two chunks jointly support two parts of one claim | true | cite both chunks |
| Claim is plausible CST practice but absent from Top-3 evidence | false | empty |

## Adjudication Rubric

The adjudicator receives reviewer consensus prefilled and only disagreements as `null`.

- Fill only null fields; do not change consensus labels.
- Re-read the supplied context without consulting machine verdicts.
- Resolve the label using the same rubric version, not by majority preference or wording quality.
- Record a short reason when the disagreement exposes an ambiguous rubric boundary.
- Repeated ambiguity should trigger a future rubric version and a new pack, not a silent reinterpretation of completed labels.

## Interpreting Calibration Outputs

- Report raw agreement and Cohen's kappa together. Do not hide high agreement behind undefined kappa when both reviewers assign a constant label.
- Agent's 21 samples are 7 cases × 3 correlated repeats; they are not 21 independent tasks.
- RAG's 55 claims are clustered within 5 judge-valid responses; claim-level confidence intervals must not assume 55 independent answers.
- `human_adjudicated_gold` describes the completed development-set labels only. It does not make the underlying developer-visible cases blinded or demonstrate unseen generalization.
- Reviewer independence is a real-world process fact. Matching IDs, schemas, and SHA values cannot prove that two different people followed the access rules.
