# Human Evaluation Runbook

This runbook turns the saved Agent and RAG development reports into two independent review files, a disagreement-only adjudication file, and a calibrated report. It does not create human gold by itself: the reviewers must be real, independent people and must follow the access rules below. All labels use [`HUMAN_EVALUATION_RUBRIC.md`](HUMAN_EVALUATION_RUBRIC.md); each pack binds the exact rubric bytes through `rubric_version` and `rubric_sha256`.

## What Is Being Reviewed

| Pack | Source | Review unit | Expected count | Current boundary |
| --- | --- | --- | ---: | --- |
| Agent E2E | Terra representative repeat-3 report | one response/tool-trace sample | 21 | 7 developer-visible cases × 3 correlated repeats |
| RAG groundedness | offline-revalidated Terra v2 report | one machine-segmented atomic claim | 55 | 5 judge-valid responses; the sixth response is excluded from claim calibration |

The exported packs are **verdict-blind**, not fully provenance-blind. Machine verdicts, machine reasons, provider/model fields, and experiment groups are removed; source identities and natural sample/case/claim IDs remain visible. A SHA-bound permutation breaks the original report grouping order, and A/B/adjudication files must bind the same `pack_id`. These controls make accidental label leakage harder, but they cannot prove reviewer independence or prevent a reviewer from opening the source report.

## D-Drive Environment

All generated files remain outside the repository on D:

```powershell
$env:CST_AGENT_DATA_ROOT="D:\cst_agent_rag_data"
$env:TEMP="$env:CST_AGENT_DATA_ROOT\tmp"
$env:TMP=$env:TEMP
$env:PYTHONPYCACHEPREFIX="$env:CST_AGENT_DATA_ROOT\cache\pycache"

$agentReviewDir="$env:CST_AGENT_DATA_ROOT\human_review\agent_e2e_repeat3"
$ragReviewDir="$env:CST_AGENT_DATA_ROOT\human_review\rag_groundedness"
New-Item -ItemType Directory -Force $agentReviewDir,$ragReviewDir,$env:TEMP | Out-Null
```

Do not place completed reviews, reviewer identities, or adjudication files in the repository.

## Export Two Independent Packs

Use pseudonymous reviewer IDs assigned by the person coordinating the review. Do not reuse `reviewer-a-assigned` or `reviewer-b-assigned` for a completed result.

```powershell
$agentReviewerA="agent-reviewer-a-001"
$agentReviewerB="agent-reviewer-b-001"
$ragReviewerA="rag-reviewer-a-001"
$ragReviewerB="rag-reviewer-b-001"

python -m benchmarks.agent_e2e_reviewer_calibration export-template `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --dataset benchmarks/agent_e2e_frozen_dev_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --reviewer-id $agentReviewerA `
  --output "$agentReviewDir\reviewer_a.json"

python -m benchmarks.agent_e2e_reviewer_calibration export-template `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --dataset benchmarks/agent_e2e_frozen_dev_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --reviewer-id $agentReviewerB `
  --output "$agentReviewDir\reviewer_b.json"

python -m benchmarks.agent_rag_claim_review export-template `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-id $ragReviewerA `
  --output "$ragReviewDir\reviewer_a.json"

python -m benchmarks.agent_rag_claim_review export-template `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-id $ragReviewerB `
  --output "$ragReviewDir\reviewer_b.json"
```

The canonical regression tests require the Agent export to contain 21 blank judgments and the RAG export to contain 55 blank judgments. Exporting RAG review files from `agent_rag_groundedness_terra_v2_6case_fixed.json` yields only the 25 claims that were present before offline revalidation and is therefore a legacy partial pack, not the canonical 55-claim calibration input.

## Reviewer Rules

Each reviewer receives only their assigned file and a byte-identical copy of `HUMAN_EVALUATION_RUBRIC.md`. They must verify that its SHA256 matches `review_protocol.rubric_sha256`; they must not receive the source report, machine scores, the other review file, or an adjudication file.

Agent reviewers fill only:

- `semantic_execution_success`;
- `grounded_final_response`;
- `notes` when the decision needs explanation.

RAG reviewers fill only:

- `supported`;
- `evidence_refs` using the supplied `source_path` and `chunk_idx` when `supported=true`;
- `notes` when the decision needs explanation.

Do not change IDs, SHA fields, `pack_id`, ordering metadata, review context, or protocol fields. The calibrators fail closed on incomplete coverage, identity drift, unknown evidence references, duplicate IDs, mismatched packs, and rubric-byte drift.

## Adjudicate Only Disagreements

After both reviewers finish, generate the adjudication files. Reviewer consensus is prefilled and locked; the adjudicator fills only null disagreement fields and must use a third identity.

```powershell
$agentAdjudicator="agent-adjudicator-001"
$ragAdjudicator="rag-adjudicator-001"

python -m benchmarks.agent_e2e_reviewer_calibration export-adjudication `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --reviewer-a "$agentReviewDir\reviewer_a.json" `
  --reviewer-b "$agentReviewDir\reviewer_b.json" `
  --adjudicator-id $agentAdjudicator `
  --output "$agentReviewDir\adjudication.json"

python -m benchmarks.agent_rag_claim_review export-adjudication `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-a "$ragReviewDir\reviewer_a.json" `
  --reviewer-b "$ragReviewDir\reviewer_b.json" `
  --adjudicator-id $ragAdjudicator `
  --output "$ragReviewDir\adjudication.json"
```

## Calibrate The Machine Graders

```powershell
python -m benchmarks.agent_e2e_reviewer_calibration calibrate `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --reviewer-a "$agentReviewDir\reviewer_a.json" `
  --reviewer-b "$agentReviewDir\reviewer_b.json" `
  --adjudication "$agentReviewDir\adjudication.json" `
  --output "$agentReviewDir\calibration.json"

python -m benchmarks.agent_rag_claim_review calibrate `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-a "$ragReviewDir\reviewer_a.json" `
  --reviewer-b "$ragReviewDir\reviewer_b.json" `
  --adjudication "$ragReviewDir\adjudication.json" `
  --output "$ragReviewDir\calibration.json"
```

The outputs report inter-reviewer agreement, Cohen's kappa, disagreement IDs, and machine-vs-adjudicated confusion metrics. RAG calibration also reports per-response groundedness error. A `human_adjudicated_gold` status is valid only when the three IDs correspond to three real people and the stated access separation actually occurred; metadata alone cannot prove that fact.

## What May Be Claimed

Before real reviews are completed:

> Implemented and regression-tested verdict-blind double-review and disagreement-only adjudication workflows; human calibration remains pending.

After real independent reviews and adjudication are completed:

> Calibrated the development-set machine graders against two independent human reviewers plus a third adjudicator, reporting agreement and machine-vs-human error with SHA-bound pack identity.

Even after calibration, do not call these packs blinded held-out evaluation: the underlying Agent/RAG cases are developer-visible, natural IDs remain visible, and reviewer independence depends on the real handoff process.
