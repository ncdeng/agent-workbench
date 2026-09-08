# Agent Workbench

[中文](README.md) | **English**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

CST-Agent is an engineering agent that operates CST Studio Suite 2025, a stateful desktop CAE application, over a Windows COM bridge. A natural-language antenna request runs as a closed loop:

`User request -> Planner -> Tool Runtime -> CST modeling/solver -> Result reading -> Diagnosis/optimization -> Trace/report`

Software like CST is stateful and has real side effects: a solve takes minutes, a wrong parameter ruins the model, and a hung external process cannot always be killed. Most of the work here is not the LLM call itself but everything around it — tool permissions, human approval, failure recovery, tracing, reproducible evaluation. The design history, including decisions that were later reverted, is in [PROJECT_STORY.md](PROJECT_STORY.md).

> **On how this was built**: much of the implementation work here was done with AI coding tools. The architectural decisions, the evaluation design, and the safety boundaries are mine; the decision records are in [`docs/adr/`](docs/adr/). The evaluation results are reproducible — datasets, manifests, and reports are byte-bound in the evidence registry [benchmarks/agent_e2e_canonical.json](benchmarks/agent_e2e_canonical.json), raw reports are under [benchmarks/reports/](benchmarks/reports/), and the commands are in the Evaluation and Verification sections below. The boundaries around every number, and the null results, are documented rather than dropped.

---

<p align="center">
  <img src="docs/images/dashboard-light.png" alt="Dashboard (Light Theme)" width="100%">
</p>

<p align="center">
  <em>Dashboard: S11 chart, optimization metrics, CST status, recent tool events, and workflow progress</em>
</p>

<p align="center">
  <img src="docs/images/dashboard-dark.png" alt="Dashboard (Dark Theme)" width="49%">
  &nbsp;
  <img src="docs/images/chat.png" alt="Chat Page" width="49%">
</p>

<p align="center">
  <em>Left: dark theme dashboard | Right: agent chat with result viewer</em>
</p>

## How this differs from a stateless API

- A CST project is a single stateful resource, and modeling/solver calls change it irreversibly. Tools run sequentially, the active plan decides which tools each step can see, and a failed step leaves real state behind that recovery has to work from — not an HTTP error code to retry.
- CST's COM interface has Windows STA thread affinity and a pinned Python version, so all COM/VBA work runs in a dedicated subprocess and handles are released with the process. The cost is one extra subprocess hop per command.
- On a bridge timeout, Python can kill its own subprocess but not the CST process, which may still be running the previous solve; CST has no reliable cross-process abort API. The runtime marks the connection dead, returns `timeout: True`, and tells the user CST may still be solving ([ADR-006](docs/adr/adr-006-cst-timeout-honest-degradation.md)). It does not pretend to abort.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│ User / React UI                                                     │
│ Dashboard · Chat · Trace                                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST + SSE
┌──────────────────────────────▼──────────────────────────────────────┐
│ FastAPI Orchestration API                                           │
│ chat · cst actions · optimization · results · trace · RAG           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Agent Runtime                                                       │
│ Planner → pluggable Tool Loop (Native / restricted Pi) → Reflection │
│ structured Plan · conditional routing · replan/reflect/end          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Tool Runtime: 42 canonical tools, one execution entry               │
│ active-step allowlist · schema validate · approval gate · dispatch  │
│ error typing · summarize/recall · token budget · trace hooks        │
└───────────────┬───────────────┬────────────────┬────────────────────┘
                │               │                │
┌───────────────▼───┐ ┌─────────▼──────┐ ┌───────▼────────┐ ┌─────────▼──────┐
│ CST Domain Tools  │ │ Results Service│ │ Optimization   │ │ RAG + Memory   │
│ COM/VBA bridge    │ │ S11/farfield   │ │ diagnosis      │ │ expert rules   │
│ primitives        │ │ fallback chain │ │ rollback       │ │ official docs  │
│ fast paths        │ │ summaries      │ │ reports        │ │ reflection     │
└───────────────┬───┘ └─────────┬──────┘ └───────┬────────┘ └─────────┬──────┘
                └───────────────┴────────────────┴────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ AgentSession                                                        │
│ single source of truth · artifacts · tool results · trace history   │
│ structured memory · benchmark/report evidence                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Tool Runtime

All 42 tools go through one entry point, `tool_runtime.execute_tool`; there is no second dispatch path. Normal calls and recovery retries share the same pipeline:

`active-step allowlist -> schema normalize/validate -> approval gate -> dispatch -> typed result`

- Permission and completion are separate concerns ([ADR-010](docs/adr/adr-010-plan-step-tool-allowlist.md)). `allowed_tools` is only a whitelist; its subset `required_tools` is the completion contract. Progress accumulates across batches and unfinished tools are injected back into plan context, so a step no longer completes just because some call happened.
- Arguments are validated against canonical JSON Schemas, with optional defaults applied before validation, hashing, and evaluation. Omitting a flag and passing its default are the same call at every layer.
- Failures are typed. The recovery engine registers bounded actions only: reconnect, timeout retry, farfield-monitor repair, argument repair, dry-run fallback. Recovery goes back through the allowlist and approval gate, and every retry outcome lands on the tool event and the trace.
- Large results are summarized; the full payload stays in the session and comes back through `recall_tool_result`. Curves may be deterministically downsampled with endpoints and global extrema preserved, and scalar summaries are always computed on the full raw curve.

## Approval

Autonomy is tiered by what a call can bypass:

- Typed modeling, solver, and result-reading tools run without approval; their arguments are already constrained by the schema and the controller's guards.
- `execute_vba_script` can bypass both, so it needs a human grant ([ADR-011](docs/adr/adr-011-parameter-bound-tool-approval.md)). A grant binds the tool name, the SHA-256 of the normalized arguments, the actor, and an expiry, and works exactly once: a CST-side failure needs re-approval, and changing one byte of the arguments creates a new request. The server keeps the exact arguments (the UI sees a preview of at most 500 characters) and re-runs a side-effect-free preflight before issuing.
- A pending approval is neither a failure nor a completion. The plan stays on the current step, only successfully executed tools count toward completion, and approval replays the exact bound call in the same HTTP request.
- Clearing the session or opening/closing a project revokes pending requests and active grants. This is a single-user desktop tool with no multi-tenant authentication; do not read it as RBAC.

## Harnesses: Native and Pi

The tool loop inside `run_agent_turn()` is replaceable. `AGENT_BRAIN=native` (default) keeps the hand-written Python loop; `AGENT_BRAIN=pi` hands model turns to a restricted Node sidecar running the MIT-licensed pi-agent-core. Planner, session state, recovery, trace, and the COM boundary stay in Python either way. See [`integrations/pi_agent_core/README.md`](integrations/pi_agent_core/README.md).

- Pi does not load pi-coding-agent and gets no Bash/Read/Write/Edit/Web/MCP built-ins. It sees only the active-step subset of the tool catalog, re-filtered by Python after every batch. Every call still returns to `execute_tool`, so allowlists, approval, recovery, and COM isolation apply unchanged, and Pi never holds a mutable session or a COM handle.
- The control protocol is versioned: hello/health/run/result/error envelopes, request/session correlation, capability negotiation, heartbeats, ordered events, and correlated cancel/steer/follow-up.
- A missing sidecar, a timeout, malformed JSONL, or a model error fails explicitly. There is no silent fallback to Native, so evaluations and traces never mis-attribute the harness. After a crash the sidecar restarts before the next task; a possibly half-executed CST turn is never replayed automatically.
- Real-model pairing (gpt-5.6-terra, 12 unique cases x 3 repeats, developer-visible and post-audit): task-majority Pi 12/12 vs Native 10/12, strict-majority 10/12 vs 7/12, concentrated in two solver workflow families — but exact McNemar is p=0.5 / 0.25 and mean tokens are flat, so this is directional improvement, not a significant claim. SHA-bound detail: [docs/PI_HARNESS_EVALUATION.md](docs/PI_HARNESS_EVALUATION.md).

## Evaluation

House rules for numbers in this repository: recomputable, boundaries stated, null results kept.

- The evidence registry [benchmarks/agent_e2e_canonical.json](benchmarks/agent_e2e_canonical.json) byte-binds the frozen datasets, manifests, and reports; the early ten-case development reports are marked historical diagnostics.
- 40-case deterministic ablation (manifest/SHA-bound, developer-visible; the current evidence is `post_approval_v3_deterministic_full` in the registry): full 40/40 (strict-lexical 36/40), no-context execution 40/40 but strict-lexical 28/40, no-recovery 32/40 (strict-lexical 28/40), no-planner 0/40; no-memory and no-ToolUseMemory both 40/40. The planner and recovery contributions hold up; a memory gain does not show, and that is stated as is.
- Real-model audit corrections stay on record: the first Terra audit (7 representative cases) exposed lexical/argument false negatives and one under-specified solver oracle. The post-audit v2 regression scores execution 7/7, exact sequence 7/7, strict lexical 6/7, and records that v1 outputs informed the oracle revisions. A response-SHA-bound semantic review of the 3-repeat run scores execution 19/21 and grounded task 16/21. Repeats are correlated, not 21 independent tasks, and none of this is blinded.
- The real-model ToolUseMemory A/B (four failure families, 8 cases x 2 arms x 3 repeats) injected memory in 24/24 learned samples and changed first-turn tool ordering, yet both arms finished 24/24 with paired delta 0 and roughly 220 extra learned-arm tokens. The null result stays in the repository.
- Sealed evaluation is a protocol, not a result: the v2 verifier binds an external trust policy to the real bytes of the public cases, fixtures, runner, prompts, and tool catalog, and passes negative tests — but there is no real external issuer and no completed sealed run. See [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md).
- Until independent human labeling completes, LLM-judge scores (for example RAG macro groundedness 0.766 with 17/55 unsupported claims) are development diagnostics, not effect claims. Dual-reviewer packs are exported; the process is in [docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md).

## The CST side

CST is the substrate that makes the control plane above necessary. On top of the COM/VBA bridge there are deterministic builders for common antennas (rectangular patch, half-wave dipole, pixel patch), so routine requests skip the LLM entirely; open-ended tasks use tool calling. The optimization loop: solve, read S11/farfield through a fallback chain, triage with `diagnose_s11`, then physics-guided tuning — f∝1/L secant step sizing, direction memory, rollback validated by a real re-solve, and stagnation detection that restores the best-so-far point. When the CST Native Optimizer is used, the split is fixed: the agent interprets the goal, diagnoses results, configures objectives, and owns rollback; the optimizer does numerical search; the solver stays the physical ground truth.

## RAG and memory

Two retrieval channels, two systems; their numbers are not merged.

Official-document RAG: CST Online Help is chunked into 13,242 English chunks embedded with `bge-base-en-v1.5` in Chroma. Retrieval is dense Top-20 -> `ms-marco-MiniLM-L6-v2` cross-encoder rerank -> source dedup to Top-3, with full rank/provenance kept. On the frozen 30-query held-out set, Recall@3 goes 0.900 -> 0.967 and MRR 0.750 -> 0.794; the orthogonal ablation shows dedup alone reaches 0.900 and the cross-encoder is needed for 0.967. Only 2/30 queries actually change outcome, and the paired-bootstrap 95% CI on the Recall delta is [0.000, 0.167] — directional, not significant. Warm p95 latency rises 63.29 -> 1019.50 ms as a same-machine observation. Details: [docs/RAG_DESIGN.md](docs/RAG_DESIGN.md).

Memory comes in three parts:

- StructuredMemory keeps optimization lessons and failures, scoped by project and design signature. Confidence does not trust the LLM's self-score: a measured improvement keeps it, no improvement halves it, a rollback caps it at 0.2.
- ToolUseMemory records tool failures and corrections, and only reorders the active-step allowlist; it never adds or removes permissions. The mechanism loop is proven end to end; a behavioral gain is not — that is the null result above.
- Conversation-level goals, constraints, and pending questions survive follow-up turns. Constraints are extracted deterministically with regex and stored in the user's own words; planner paraphrases are a fallback only.

## Boundaries

- Real CST solves and COM tests need a local Windows machine with CST Studio Suite; CI runs offline tests only.
- Some 9.4 GHz Rogers5880 microstrip cases still miss a stable -10 dB; diagnosis-driven tuning needs more work.
- LangGraph was adopted and then withdrawn: the graph nodes were thin pass-throughs, the checkpointer could not work with non-serializable COM handles, and the replan edge was unreachable in production. Control flow is hand-written; step-level single-tool driving is future work.
- Chat SSE polls session state every 0.5 s; it is not token-level streaming.
- The backend is a single-user desktop tool: one global session, one operation lock, no authentication. Keep it bound to `127.0.0.1`.
- The BO/PSO/DE module is a bounded fallback sampler for when LLM proposals are unavailable, not a full optimization-loop replacement.
- Some farfield post-processing VBA paths have COM context restrictions and may need CST result templates.
- The old Gradio implementation is gone; React + FastAPI is the only supported UI path.

## Quick Start

> Verified local Python versions: 3.11 and 3.14. Use Windows for live CST mode.

```bash
# Install package + optional feature dependencies
pip install -e .[dev,web,report,rag]

# Configure LLM provider
copy .env.example .env
# Edit .env: set MODEL_API_KEY/MODEL_NAME (or legacy OPENAI_*), then choose
# chat_completions, responses, or anthropic_messages with MODEL_API_PROTOCOL.
# Protocol and cache boundaries: docs/MODEL_PROTOCOLS_AND_CACHE.md

# Launch React frontend + FastAPI backend
启动.bat

# Or launch backend manually
python -m cst_agent_workbench.web_app              # live CST mode
python -m cst_agent_workbench.web_app --dry-run    # demo mode without CST
```

Open `http://127.0.0.1:5173` for the React UI.

On Windows, pin package, model, RAG, memory, and temporary caches to the D drive before installing or evaluating:

```powershell
$env:CST_AGENT_DATA_ROOT="D:\cst_agent_rag_data"
$env:PIP_CACHE_DIR="$env:CST_AGENT_DATA_ROOT\cache\pip"
$env:HF_HOME="$env:CST_AGENT_DATA_ROOT\cache\huggingface"
$env:HF_HUB_CACHE="$env:HF_HOME\hub"
$env:TRANSFORMERS_CACHE="$env:CST_AGENT_DATA_ROOT\cache\transformers"
$env:SENTENCE_TRANSFORMERS_HOME="$env:CST_AGENT_DATA_ROOT\cache\sentence_transformers"
$env:RAG_CACHE_DIR="$env:CST_AGENT_DATA_ROOT\cache\rag"
$env:CHROMA_PERSIST_DIR="$env:CST_AGENT_DATA_ROOT\indexes\chroma"
$env:AGENT_MEMORY_DIR="$env:CST_AGENT_DATA_ROOT\memory"
$env:TEMP="$env:CST_AGENT_DATA_ROOT\tmp"
$env:TMP="$env:CST_AGENT_DATA_ROOT\tmp"
New-Item -ItemType Directory -Force $env:PIP_CACHE_DIR,$env:HF_HOME,$env:HF_HUB_CACHE,$env:TRANSFORMERS_CACHE,$env:SENTENCE_TRANSFORMERS_HOME,$env:RAG_CACHE_DIR,$env:CHROMA_PERSIST_DIR,$env:AGENT_MEMORY_DIR,$env:TEMP | Out-Null
```

These settings are an execution prerequisite, not evidence metadata by themselves. Formal RAG runs still verify the canonical dataset/index/model contract recorded in their manifest.

Useful CLI commands (datasets, manifests, and reports are SHA-bound; human-calibration pack exports follow [docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md)):

```bash
# Generate deterministic patch VBA without CST
python -m cst_agent_workbench.cli build-patch --dry-run --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --export-vba runs/demo/generated.vba

# Real CST closed-loop report
python -m cst_agent_workbench.cli optimize-patch-report --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --max-rounds 3 --output runs/demo/patch_optimization_report.md

# Real CST evaluation matrix
python -m cst_agent_workbench.cli patch-matrix --microstrip-rounds 3 --output-dir runs/patch_matrix --summary runs/patch_matrix/summary.md --json-output runs/patch_matrix/summary.json

# Optimization-proposal ablation (does not exercise the complete Agent runtime)
python benchmarks/agent_ablation_runner.py --cases 20 --max-rounds 6 --assert-thresholds --output benchmarks/reports/agent_ablation_fake_cst.json --summary-md benchmarks/reports/agent_ablation_fake_cst.md

# Full 40-case Agent mechanism evaluation with manifest/SHA and release gates (reproduces the v3 ablation
# numbers in "Evaluation" above; the deterministic proxy never calls a model, but .env still needs a
# placeholder MODEL_API_KEY, otherwise the executor refuses and every group scores 0/40)
python -m benchmarks.agent_e2e_ablation_runner --dataset benchmarks/agent_e2e_frozen_dev_v3.json --manifest benchmarks/agent_e2e_frozen_dev_v3.manifest.json --full-frozen-eval --provider deterministic_proxy --artifact-root D:/cst_agent_rag_data/agent_e2e_frozen_artifacts --output benchmarks/reports/agent_e2e_frozen_dev_v3_deterministic_ablation_v1.json --summary-md benchmarks/reports/agent_e2e_frozen_dev_v3_deterministic_ablation_v1.md

# Real Terra representative regression (filtered/debug scope, not a full frozen release)
python -m benchmarks.agent_e2e_ablation_runner --dataset benchmarks/agent_e2e_frozen_dev_v2.json --manifest benchmarks/agent_e2e_frozen_dev_v2.manifest.json --provider openai_compatible --model gpt-5.6-terra --group full --case status_read_01 --case no_tool_01 --case memory_guided_01 --case context_followup_01 --case multi_tool_materials_01 --case disconnect_recovery_01 --case solver_workflow_01 --artifact-root D:/cst_agent_rag_data/agent_e2e_frozen_artifacts --output benchmarks/reports/agent_e2e_frozen_dev_v2_terra_regression_v1.json

# Repeat stability with atomic checkpointing; add --resume after an interrupted run
python -m benchmarks.agent_e2e_ablation_runner --dataset benchmarks/agent_e2e_frozen_dev_v1.json --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json --provider openai_compatible --model gpt-5.6-terra --group full --repeat 3 --case status_read_01 --case no_tool_01 --case memory_guided_01 --case context_followup_01 --case multi_tool_materials_01 --case disconnect_recovery_01 --case solver_workflow_01 --artifact-root D:/cst_agent_rag_data/agent_e2e_frozen_artifacts --checkpoint benchmarks/reports/.checkpoints/agent_e2e_frozen_dev_v1_terra_repeat3.json --output benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json

# Orthogonal RAG ablation: candidate K x reranker x source dedup
python -m benchmarks.rag_official_ablation_matrix --output benchmarks/reports/rag_official_ablation_matrix_v1.json

# Validate a response-SHA-bound semantic review against the exact Agent outputs
python -m benchmarks.agent_e2e_semantic_audit --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json --review benchmarks/reviews/agent_e2e_frozen_dev_v1_terra_repeat3_semantic_review_v1.json --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json --output benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_semantic_audit_v2.json

python benchmarks/tool_use_memory_pair_runner.py --artifact-root D:/cst_agent_rag_data/agent_eval/tool_use_memory --output benchmarks/reports/tool_use_memory_pair_v1.json --assert-checks

# Real-model ToolUseMemory paired development run. The bundled six-case set is a preserved null
# pilot, not held-out evidence. Samples are manifest-bound, AB/BA-balanced and checkpointed.
python -m benchmarks.tool_use_memory_llm_pair_runner --dataset benchmarks/tool_use_memory_llm_pair_dev_v1.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v1.manifest.json --model gpt-5.6-terra --repeat 3 --artifact-root D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm --checkpoint D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm/checkpoints/dev_v1_repeat3.json --output benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v1_repeat3.json

# Validate the four-family v2 dataset before spending model calls.
python -m benchmarks.tool_use_memory_llm_pair_runner --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json --artifact-root D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm_dev_v2 --validate-oracles-only --output benchmarks/reports/tool_use_memory_llm_pair_dev_v2_oracle_validation.json
python -m benchmarks.tool_use_memory_llm_pair_runner --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json --artifact-root D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm_dev_v2 --validate-learning-only --output benchmarks/reports/tool_use_memory_llm_pair_dev_v2_learning_validation.json

# Long runs stop themselves at a checkpoint boundary; rerun with --resume for the next batch.
python -m benchmarks.tool_use_memory_llm_pair_runner --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json --model gpt-5.6-terra --repeat 3 --max-new-samples 12 --artifact-root D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm_dev_v2 --checkpoint D:/cst_agent_rag_data/agent_eval/tool_use_memory_llm_dev_v2/checkpoints/dev_v2_repeat3.json --output benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v2_repeat3.json

# Zero-model-call deterministic semantic revalidation of the saved repeat-1 artifact.
python -m benchmarks.tool_use_memory_semantic_revalidate --source-report benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v2_repeat1.json --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json --semantic-review benchmarks/reviews/tool_use_memory_llm_pair_terra_dev_v2_repeat1_semantic_review_v1.json --output D:/cst_agent_rag_data/agent_eval/revalidation/tool_use_memory_repeat1.json

# Repeat-3 revalidation; paired inference remains case-level over eight unique cases.
python -m benchmarks.tool_use_memory_semantic_revalidate --source-report benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v2_repeat3_v1.json --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json --output D:/cst_agent_rag_data/agent_eval/revalidation/tool_use_memory_repeat3.json

# Verify an independently issued v2 handoff. This grants execution eligibility only;
# release eligibility requires a separately trusted promotion receipt.
python -m benchmarks.sealed_eval_handoff verify-public --manifest D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/sealed_eval_handoff.json --trust-policy D:/independent-evaluator/trust-policy.json --receipt-out D:/cst_agent_rag_data/agent_eval/sealed/<handoff_id>/public-verification.json

# Real-LLM proposal ablation (requires OPENAI_API_KEY; separate from full-Agent E2E)
python benchmarks/agent_ablation_runner.py --cases 10 --max-rounds 4 --provider openai_compatible --group llm_no_memory --group llm_with_memory --output benchmarks/reports/agent_ablation_fake_cst_openai_10case.json
```

The public command above grants execution eligibility only. The private-verification and promotion commands, external-custody requirements, and fail-closed release rules are documented in [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md). The repository does not contain a real externally issued sealed pack.

## Verification

```bash
# Fast smoke gate: errors, summaries, planner routing, FastAPI chat/API
python scripts/check.py --level smoke

# Core backend gate: agent runtime, tool runtime, UI wiring, web API
python scripts/check.py --level core

# Offline evaluation-contract gate: RAG, Agent E2E, Memory graders, sealed verifier
python scripts/check.py --level eval

# Full offline Python suite, excluding live CST tests
python scripts/check.py --level offline

# Frontend Vitest
python scripts/check.py --level frontend

# Browser E2E with mocked API responses
npm.cmd --prefix frontend exec playwright install chromium  # first run only
python scripts/check.py --level e2e

# TypeScript + Vite build
python scripts/check.py --level build

# Merge gate: smoke + core + offline + ruff + frontend + e2e + build
python scripts/check.py --level all

# Local Windows + CST only: opt-in connection smoke against a D-drive project copy
$env:CST_LIVE_PROJECT_COPY="D:\cst_agent_rag_data\projects\status_copy.cst"
$env:RUN_LIVE_CST="1"; python scripts/check.py --level live-cst

# Local Windows + CST only: Python-API build + real solver smoke on a disposable D-drive copy
$env:RUN_LIVE_CST_MUTATING="1"; $env:RUN_LIVE_CST_SOLVER="1"
python scripts/check.py --level live-cst-solver

# Official CST Windows batch entry point: unattended active-solver smoke
$env:RUN_LIVE_CST_BATCH="1"
python benchmarks/cst_batch_solver_runner.py --project-copy $env:CST_LIVE_PROJECT_COPY `
  --mode active_solver `
  --output D:\cst_agent_rag_data\cst_batch_evidence\latest.json
```

The layered standard is documented in [docs/TESTING.md](docs/TESTING.md). By default these checks do not connect to real CST; live solver/API checks stay explicit local opt-in and are not part of `--level all`, because hosted runners have no CST Studio Suite or license. All live paths require an existing D-drive project copy; pytest temporary data is rooted at the repository's ignored `tmp/pytest` directory instead of the Windows C-drive temp directory.

CI runs lint, offline Python tests on multiple Python versions, fake-CST smoke benchmarks, TypeScript checks, frontend build, and Vitest.

## Project Layout

```
cst_agent_workbench/
├── agent/          # Planner, runtime, session, memory, recovery, reflection, trace, tool runtime
├── cst/            # CST controller, controlled primitives, deterministic antenna builders
├── optimization/   # S11 diagnosis, optimization strategy, rollback, reports
├── rag/            # Expert rules, official-doc ingestion/retrieval, legacy dynamic migration
├── results/        # S11 and farfield reading, fallback chain, summaries
├── web/            # FastAPI route modules
├── web_api.py      # FastAPI app factory and shared API helpers
├── web_app.py      # Backend entrypoint with --dry-run support
└── cli.py          # Batch build, optimization report, benchmark matrix

frontend/
├── src/pages/      # Dashboard, Chat, Trace
├── src/components/ # S11 chart, workflow rail, metric/status components
├── src/api/        # Typed API client modules
└── src/__tests__/  # Vitest API, hook, component tests

integrations/       # Restricted Node sidecar for the optional Pi harness
tests/              # Python unit/integration/eval tests
benchmarks/         # Fake-CST ablation and reproducible evidence reports
docs/               # Setup, audit, handoff, archived history
```
