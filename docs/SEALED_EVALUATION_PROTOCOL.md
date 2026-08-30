# Sealed evaluation protocol

`frozen` proves only that bytes did not change after a manifest was created. It does not prove that
the developer never saw the prompts or oracles. Externally verifiable unseen-generalization
claims use `sealed` only when an independent issuer controls private-oracle custody and the final
promotion record.

The repository implements the verifier and its negative tests. It does **not** contain an
independently issued sealed pack, external key custody, or a completed sealed Terra run. A local
self-test therefore proves protocol behavior, not independent custody.

## Roles and D-drive layout

- The issuer prepares `public_cases.json`, a public Fake-CST fixture pack, the frozen runner,
  prompt and tool catalog, plus a private oracle kept outside the executor's readable directory.
- The executor receives the public bundle and `sealed_eval_handoff.json`. The manifest exposes only
  the private-oracle SHA256 and byte size.
- The evaluator grades response/trace-SHA-bound outputs and emits a private score artifact, a
  redacted public report, and a signed promotion document.
- Bundles, receipts, checkpoints, traces, provider request records and reports stay under
  `D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/`. Keys and private oracles must not be added
  to Git, editor sync or cloud backup.

## State machine and authority boundary

`received -> execution_verified -> executed -> privately_verified -> adjudicated -> promoted`

Three booleans have deliberately different meanings:

- `manifest_integrity_verified=true`: a legacy v1 HMAC matched. It grants no execution or release
  authority because v1 does not bind real runner/prompt/tool bytes or a trusted key identity.
- `execution_eligible=true`: a v2 issuer/key with `handoff` capability is trusted, `not_before` has
  passed, and all five public artifacts match their declared SHA/size. Public verification still
  returns `release_eligible_handoff=false`.
- `release_eligible_handoff=true`: returned only by `verify-promotion` after the public and private
  receipts, result artifacts, full trace coverage and a `promotion`-capable signature all verify.

The old `sealed-eval-handoff-v1` parser remains available for historical integrity checks. A
correct v1 signature is always marked `legacy_v1=true`, `execution_eligible=false` and
`release_eligible_handoff=false`. `--signature-key-file` and `--allow-unsigned-debug` apply only to
that legacy path.

## v2 trust policy and signatures

v2 uses domain-separated HMAC payloads for handoff and promotion. The signature covers the
`algorithm` and `key_id`; only `signature.value` is removed before canonicalization. A caller cannot
authorize v2 by passing an arbitrary key file. The verifier selects a key only through the external
trust policy's `(issuer, key_id, algorithm, usage)` mapping.

Example trust policy stored outside Git:

```json
{
  "schema_version": "sealed-eval-trust-policy-v1",
  "recipient": "cst-agent-executor",
  "trusted_keys": [
    {
      "issuer": "independent-evaluator",
      "key_id": "issuer-key-2026-08",
      "algorithm": "hmac-sha256-v2",
      "key_file": "issuer-key.bin",
      "usages": ["handoff"]
    },
    {
      "issuer": "independent-evaluator",
      "key_id": "promotion-key-2026-08",
      "algorithm": "hmac-sha256-v2",
      "key_file": "promotion-key.bin",
      "usages": ["promotion"]
    }
  ]
}
```

Relative `key_file` paths resolve against the trust-policy directory. Raw key bytes never appear in
verification receipts. Separate handoff and promotion keys are recommended; the capability field
still prevents a handoff-only key from promoting even if the same key material is reused.

HMAC is a dependency-free symmetric authentication mechanism, not public non-repudiation. A real
third-party publication should replace it with an asymmetric signature whose private key never
reaches the developer machine. A self-generated trust policy or HMAC cannot establish independent
custody.

## Verification commands

Public verification checks the public cases, fixture pack, runner, prompt, tool catalog, trusted
signature and `not_before`, then writes a path-free, canonical-SHA-bound receipt:

```powershell
python -m benchmarks.sealed_eval_handoff verify-public `
  --manifest D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/sealed_eval_handoff.json `
  --trust-policy D:/independent-evaluator/trust-policy.json `
  --receipt-out D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/public-verification.json
```

The private verifier authenticates the same v2 manifest, checks the private oracle identity, and
requires the exact public receipt. It does not grant release authority:

```powershell
python -m benchmarks.sealed_eval_handoff verify-private `
  --manifest D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/sealed_eval_handoff.json `
  --private-oracles D:/independent-evaluator/<handoff_id>/private_oracles.json `
  --trust-policy D:/independent-evaluator/trust-policy.json `
  --public-receipt D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/public-verification.json `
  --receipt-out D:/independent-evaluator/<handoff_id>/private-verification.json
```

Promotion requires both receipts and the exact public report, private scores and response-trace
bytes:

```powershell
python -m benchmarks.sealed_eval_handoff verify-promotion `
  --promotion D:/independent-evaluator/<handoff_id>/promotion.json `
  --manifest D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/sealed_eval_handoff.json `
  --public-receipt D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/public-verification.json `
  --private-receipt D:/independent-evaluator/<handoff_id>/private-verification.json `
  --public-report D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/public-report.json `
  --private-scores D:/independent-evaluator/<handoff_id>/private-scores.json `
  --response-trace D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/response-trace.jsonl `
  --trust-policy D:/independent-evaluator/trust-policy.json
```

## Promotion artifact contract

SHA/size equality alone is not enough to prove a full run. `verify-promotion` parses and
cross-checks three artifacts:

- Each `response-trace.jsonl` row uses `sealed-eval-response-trace-v1` and records contiguous
  `sequence_index`, `case_id`, `arm`, one-based `repeat_index`, non-empty provider request IDs,
  `response_sha256` and `trace_sha256`. The verifier recomputes exact
  `case × arm × repeat` coverage, rejects duplicates, checks first-seen case order against the
  manifest, and requires the flattened request-ID list to equal the signed promotion list.
- `private-scores.json` uses `sealed-eval-private-scores-v1`, binds the same handoff, manifest and
  response-trace SHA, and contains one boolean primary success endpoint per case in manifest order.
- `public-report.json` uses `sealed-eval-public-report-v1`, binds the same handoff/manifest, exact
  signed `run` object, response-trace SHA and private-score SHA.

The signed promotion also binds run fingerprint, provider request IDs, code revision, case and
response counts, the ordered case-ID SHA, `full_manifest_order=true`, `filters_applied=false` and
`checkpoint_fingerprint_verified=true`. The verifier recomputes counts from the manifest and
requires `response_count = cases × arms × repeats`; it does not trust those booleans in isolation.

## One-shot and statistical rules

1. Freeze runner, prompts, tools, model, arms, repeat count and ordering seed before handoff.
2. Run every public case; filtered or partial promotion is forbidden.
3. The executor never loads the private oracle and never shares learned memory across paired arms
   unless the handoff explicitly defines a training fixture.
4. Inspecting sealed outputs consumes the set. Any later prompt, tool, grader or oracle edit turns
   it into a post-audit regression set; another unseen-generalization claim requires a new
   independently issued pack.
5. Aggregate repeated calls at unique-case level. Use Wilson intervals per arm and exact paired
   McNemar/binomial tests for binary A/B endpoints. Keep latency, tokens, instability and failure
   taxonomy separate from success metrics.
