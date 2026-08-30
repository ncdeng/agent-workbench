# RAG groundedness evaluation — Terra v2

Run date: 2026-08-10. Agent and judge model: `gpt-5.6-terra`. The run executed the real `CSTAgent.chat()` path,
query translation, English BGE dense retrieval, MiniLM cross-encoder reranking, Executor and Trace; only CST side
effects were disabled. All caches and session artifacts were stored under `D:\cst_agent_rag_data`.

## Identity and protocol

- Frozen, developer-visible source dataset: 30 cases, SHA256
  `356c81af399842624c1c5607b35da426ab9791f10ead224fc917ac25d7d77ed2`; this proves byte identity, not
  independent/blinded authoring.
- Evaluated subset: fixed first two cases per language, 6 total; no outcome filter.
- Source run report: `agent_rag_groundedness_terra_v2_6case_fixed.json`, SHA256
  `227b75de94ebebb346a240f36e330b705637ad6eff285ceb01fad764cfa3caca`.
- Offline-revalidated report: `agent_rag_groundedness_terra_v2_6case_revalidated.json`, SHA256
  `4103d77b3d668a214087e47113d2357d52f97a2294b7af74d95f6a042b9ff268`.
- Revalidation made zero model calls. It re-scored the saved answer, canonical Top-3 evidence and raw judge JSON after
  fixing integer-zero `chunk_idx` serialization. The derived report binds source-report, validator and revalidator SHA.
- The runner records Git revision, dirty-worktree state, runner SHA, judge-template SHA, execution-contract SHA,
  answer SHA, evidence SHA, judge prompt SHA and raw-response SHA.

## Results and denominators

| Metric | Result | Verified denominator |
|---|---:|---:|
| Agent completion | 1.000 | 6 responses |
| Judge schema-valid rate | 0.833 | 5/6 responses |
| Citation presence / precision | 1.000 / 1.000 | 6 responses |
| Macro groundedness | 0.766 | 5 judge-valid responses |
| Answer relevance | 0.888 | 5 judge-valid responses |
| Unsupported claim rate | 0.309 | 17/55 judge-valid claims |
| qrel retrieval Recall@3 | 1.000 | 2 legacy rows with preserved field |
| Provenance / Trace completeness | 1.000 / 1.000 | 2 legacy rows with preserved fields |
| E2E latency mean / p95 | 67.7 s / 177.8 s | 6 responses; same-machine observation |

The old v1 `groundedness=0.863` and `unsupported=0.172` values came from a six-case, single-run, uncalibrated aggregate
judge. They are retained as historical development diagnostics, not as overstated effect claims. The stricter v2 judge
contract exposed one internally inconsistent response: it declared 8/12 claims supported while only 7 claim verdicts
were true and reported the 8/12 score. That response remains invalid and is excluded from groundedness aggregates.

## Bugs found by the evaluation

1. `chunk_idx=0` was converted to an empty string by `value or ""` both when canonicalizing retrieved evidence and when
   parsing judge evidence references. This produced false judge-validation failures despite exact Top-3 source matches.
2. Agent completion and judge validity were previously conflated into one `success_rate`. v2 reports their denominators
   separately and never turns a judge-schema failure into an Agent task failure.
3. Early failed rows did not preserve every deterministic provenance boolean. The revalidated report therefore reports
   available denominators (2) instead of expanding those 1.000 values to all 6 responses.
4. Git HEAD alone could not identify dirty-worktree scoring code. v2 binds actual runner and judge-template bytes.

## Human calibration status

The current exporter creates two D-drive packs under `D:\cst_agent_rag_data\human_review\rag_groundedness`; each
contains 55 SHA-bound claims across the 5 valid responses and both bind `pack_id=pk_e8e69a2366ca7b9f7b120fac` plus
the exact `rag-claim-support-rubric-v1` bytes (`rubric_sha256=4295bdd3...eeb18`).
Machine support labels/reasons and experiment fields are absent, and a SHA-bound permutation breaks the original report
order. Natural case/claim IDs and source identities remain visible, so the packs are verdict-blind rather than fully
provenance-blind. The repository's older `agent_rag_groundedness_terra_v2_6case_claim_review_template.json` uses the
legacy template schema and is not a current calibration input. Two truly independent reviewers plus an adjudicator have
**not** completed the current packs. Therefore:

- claim-level review/calibration infrastructure is implemented and tested;
- the LLM judge is not yet calibrated against human gold;
- the 0.766 / 30.9% values are honest development diagnostics, not publication-grade effect claims;
- claim segmentation coverage is still machine-proposed and remains outside the current calibration target.
