# Testing Strategy

本项目的测试目标不是“跑一条命令没报错”，而是把不同风险分层验证。默认测试必须离线可复现，不连接真实 CST；真实 CST 只作为显式 opt-in 的本地集成验证。

## 测试分层

| 层级 | 命令 | 标准 | 适用场景 |
| --- | --- | --- | --- |
| Smoke | `python scripts/check.py --level smoke` | 快速覆盖错误分类、结果摘要、Planner 路由、FastAPI chat/API 主路径 | 每次小改后先跑 |
| Core | `python scripts/check.py --level core` | 覆盖 agent runtime、tool runtime、UI wiring、web API，必须不连接真实 CST | 修改 agent / web / UI glue 后必跑 |
| Eval | `python scripts/check.py --level eval` | 覆盖 RAG、Agent E2E、Memory grader/revalidation、sealed verifier 的离线契约；不调用模型、不连接 CST | 修改 benchmark、grader、manifest 或评测报告逻辑后必跑 |
| Offline | `python scripts/check.py --level offline` | `pytest -m "not cst"`，完整离线 Python 套件，不跑 live solver/COM | 提交前必跑 |
| Frontend | `python scripts/check.py --level frontend` | Vitest API、hooks、components 全部通过 | 修改 React/TS 后必跑 |
| E2E | `python scripts/check.py --level e2e` | Playwright 浏览器测试；mock `/api/*`，覆盖 Dashboard / Chat SSE / Trace / Clear | 修改跨页流程、SSE、状态刷新后必跑 |
| Build | `python scripts/check.py --level build` | TypeScript + Vite build 成功，临时 dist 自动清理 | 修改前端依赖、路由、构建配置后必跑 |
| All | `python scripts/check.py --level all` | Smoke + Core + Offline + Ruff + Frontend + E2E + Build 全通过 | 合并前门禁 |
| Live CST | `CST_LIVE_PROJECT_COPY=D:\...\copy.cst RUN_LIVE_CST=1 python scripts/check.py --level live-cst` | 显式 opt-in，只连接 D 盘工程副本；不运行 solver | Windows + CST 工作站本地连接验证 |
| Live CST Solver | 再开启 `RUN_LIVE_CST_MUTATING=1 RUN_LIVE_CST_SOLVER=1` | 三重显式 opt-in，修改 D 盘一次性副本、运行 solver、读取 S11 | Windows + CST 工作站本地闭环验证 |
| CST Batch Smoke | `benchmarks/cst_batch_solver_runner.py` | 官方 Windows batch 入口，无需人工点击，记录退出码和 D 盘 artifacts | 验证无人值守 CST 执行契约 |
| Typed Results Live | `benchmarks/cst_typed_results_validation.py` | D 盘 solved project；结果树、typed 1D envelope、官方 ASCIIExport、SHA 报告 | 修改结果读取/导出契约后运行 |
| Complex Geometry Live | `benchmarks/cst_complex_geometry_smoke.py` | D 盘新工程；typed geometry/Boolean、solid inventory、save/close/reopen 与 SHA 报告；不运行 solver | 修改复杂几何 contract 或 controller 后运行 |
| Pi Harness | `npm run smoke --prefix integrations/pi_agent_core` + `pytest tests/test_pi_brain.py` | Faux provider、无网络/无 CST；验证受限 Tool Loop、动态目录、History/Trace、超时与协议错误 | 修改 Agent Harness / sidecar 后必跑 |

兼容旧入口：

```powershell
python scripts/check.py --python-only
python scripts/check.py --frontend-only
python scripts/check.py --skip-frontend
```

首次运行 Playwright 前需要安装浏览器运行时：

```powershell
npm.cmd --prefix frontend exec playwright install chromium
```

Windows PowerShell 下运行 live CST smoke：

```powershell
$env:RUN_LIVE_CST="1"
$env:CST_LIVE_PROJECT_COPY="D:\cst_agent_rag_data\projects\status_copy.cst"
python scripts/check.py --level live-cst
```

Windows PowerShell 下运行真实 solver smoke：
```powershell
$env:RUN_LIVE_CST="1"
$env:RUN_LIVE_CST_MUTATING="1"
$env:RUN_LIVE_CST_SOLVER="1"
$env:CST_LIVE_PROJECT_COPY="D:\cst_agent_rag_data\projects\solver_copy.cst"
python scripts/check.py --level live-cst-solver
```

Windows 官方 batch smoke（运行工程内已保存的 active solver）同样只允许 D 盘副本和报告：

```powershell
$env:RUN_LIVE_CST="1"
$env:RUN_LIVE_CST_SOLVER="1"
$env:RUN_LIVE_CST_BATCH="1"
python benchmarks/cst_batch_solver_runner.py `
  --project-copy D:\cst_agent_rag_data\projects\solver_copy.cst `
  --mode active_solver `
  --output D:\cst_agent_rag_data\cst_batch_evidence\latest.json
```

Windows batch 可以无人值守运行并提供进程退出码，但现有官方证据不足以称为“Windows 纯 headless/完全无窗口”。Linux 的 `DesignEnvironment.new(gui_linux=False)` 才有明确的无 GUI 文档。不要直接调用安装目录中的内部 `Solver_*_AMD64.exe`；稳定入口仍是官方 `CST DESIGN ENVIRONMENT.exe` batch contract。

Pi Harness 的离线协议与 Python 适配回归：

```powershell
npm run smoke --prefix .\integrations\pi_agent_core
python -m pytest tests/test_pi_brain.py tests/test_agent_runtime.py -q `
  --basetemp=.cache\pytest-pi-runtime
```

`AGENT_BRAIN=native` 是默认基线；Pi 只有显式配置才启用。sidecar 故障必须让本轮
失败，禁止自动切回 Native，否则 Native/Pi 的评测归因失真。

## 可复现评测

### D 盘执行环境

运行任何会安装依赖、加载 embedding/reranker 或写临时文件的评测前，先在同一个 PowerShell 会话中固定这些目录：

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
$env:CST_TEMP_DIR="$env:CST_AGENT_DATA_ROOT\tmp\cst_agent_workbench"
New-Item -ItemType Directory -Force $env:PIP_CACHE_DIR,$env:HF_HOME,$env:HF_HUB_CACHE,$env:TRANSFORMERS_CACHE,$env:SENTENCE_TRANSFORMERS_HOME,$env:RAG_CACHE_DIR,$env:CHROMA_PERSIST_DIR,$env:AGENT_MEMORY_DIR,$env:TEMP,$env:CST_TEMP_DIR | Out-Null
```

真实 typed result/export 验证只读取已有 solved project，不重新运行 solver，并在结束后关闭工程：

```powershell
python benchmarks/cst_typed_results_validation.py `
  --project D:\cst_agent_rag_data\projects\python_api_solver_smoke_20260810\fast_patch_1_c192cf.cst `
  --artifact-dir D:\cst_agent_rag_data\agent_eval\typed_results_v1\new_run `
  --max-points 64
```

输出目录必须为空或不存在。报告中的 `all_checks_passed` 只覆盖工程打开、结果树列举、选定 1D
结果读取、typed envelope 和非空 ASCII 文件，不代表本轮求解或物理目标通过。

真实 complex geometry smoke 会在 D 盘创建一个合成、无敏感结构的新工程，不读取未发表论文，
也不运行 solver：

```powershell
python benchmarks/cst_complex_geometry_smoke.py `
  --output-root D:\cst_agent_rag_data\agent_eval\complex_geometry
```

报告的 `success=true` 要求 10 个 typed geometry History 操作成功、save 前与 close/reopen 后的
solid inventory 都精确等于预期集合、工程文件存在且最终已关闭。这个终点仍不证明几何尺寸/拓扑经
独立测量，更不代表端口有效、solver 收敛或物理目标达成。

`--level eval` 是最快的评测代码门禁：

```powershell
python scripts/check.py --level eval
```

它验证数据/manifest 身份、`benchmarks/agent_e2e_canonical.json` 当前 evidence registry 的报告字节、grader、离线复算、统计聚合和 sealed fail-closed 逻辑，但不重新生成任何真实模型效果。

### 官方文档 RAG 检索

该命令不连接 CST、不调用在线 LLM，但需要已按 `docs/RAG_DESIGN.md` 构建并通过 cold-start 检查的 canonical Chroma index，以及本地 BGE/MiniLM 模型。preset 会核验 dataset、case IDs、index 和检索配置；缺失或不匹配时 fail closed。

```powershell
python -m benchmarks.rag_official_eval `
  --preset heldout_v1_minilm_top3 `
  --manifest benchmarks/rag_official_canonical.json `
  --output D:\cst_agent_rag_data\agent_eval\rag\heldout_v1_minilm_top3.json

python -m benchmarks.rag_official_ablation_matrix `
  --output D:\cst_agent_rag_data\agent_eval\rag\ablation_matrix_v1.json
```

这两条命令使用开发者可见的 30-case 冻结集；`heldout` 表示与旧开发集分离，不表示 blinded 或外部 custody。

### Agent E2E 与生成层 groundedness

确定性 full-Agent 门禁使用 Fake-CST 和 scripted provider，不需要模型 key，也不会启动 CST GUI：

```powershell
python -m benchmarks.agent_e2e_ablation_runner `
  --dataset benchmarks/agent_e2e_frozen_dev_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --full-frozen-eval --provider deterministic_proxy `
  --artifact-root D:\cst_agent_rag_data\agent_eval\agent_e2e `
  --output D:\cst_agent_rag_data\agent_eval\agent_e2e\deterministic_full.json

python -m benchmarks.agent_e2e_semantic_audit `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --review benchmarks/reviews/agent_e2e_frozen_dev_v1_terra_repeat3_semantic_review_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --output D:\cst_agent_rag_data\agent_eval\agent_e2e\terra_repeat3_semantic_audit_v2.json
```

semantic audit v2 同时绑定源报告、审阅文件、manifest 和 auditor 源码 SHA，并核验源报告保持 manifest case order；它仍然只是 developer-visible 的单模型审阅，不能变成独立人工 gold。

当前双 reviewer 入口与上述历史单模型 audit 分开。下面两条零模型命令导出绑定同一 `pack_id`、顺序经过 SHA-bound permutation 的 21-sample verdict-blind 空白包：

```powershell
python -m benchmarks.agent_e2e_reviewer_calibration export-template `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --dataset benchmarks/agent_e2e_frozen_dev_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --reviewer-id agent-reviewer-a-001 `
  --output D:\cst_agent_rag_data\human_review\agent_e2e_repeat3\reviewer_a.json

python -m benchmarks.agent_e2e_reviewer_calibration export-template `
  --report benchmarks/reports/agent_e2e_frozen_dev_v1_terra_representative_repeat3_v1.json `
  --dataset benchmarks/agent_e2e_frozen_dev_v1.json `
  --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json `
  --reviewer-id agent-reviewer-b-001 `
  --output D:\cst_agent_rag_data\human_review\agent_e2e_repeat3\reviewer_b.json
```

Groundedness 的在线 runner 会调用真实 Agent provider 和 judge provider，但仍使用离线 controller，不启动 CST。已有报告可以零模型复算：

```powershell
python -m benchmarks.agent_rag_groundedness_revalidate `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_fixed.json `
  --output D:\cst_agent_rag_data\agent_eval\rag_groundedness\terra_v2_revalidated.json

python -m benchmarks.agent_rag_claim_review export-template `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-id rag-reviewer-a-001 `
  --output D:\cst_agent_rag_data\human_review\rag_groundedness\reviewer_a.json

python -m benchmarks.agent_rag_claim_review export-template `
  --report benchmarks/reports/agent_rag_groundedness_terra_v2_6case_revalidated.json `
  --reviewer-id rag-reviewer-b-001 `
  --output D:\cst_agent_rag_data\human_review\rag_groundedness\reviewer_b.json
```

canonical RAG review 必须从离线复算后的报告导出，得到 5 个 judge-valid 回答上的 55 claims；从 `..._fixed.json` 导出只会得到历史阶段已有的 25 claims，不是完整校准输入。两类 pack 都绑定 [`HUMAN_EVALUATION_RUBRIC.md`](HUMAN_EVALUATION_RUBRIC.md) 的真实字节，移除 machine verdict/reason 和实验字段，但保留自然 ID 与源身份，因此只能称 verdict-blind，不能称完全 provenance-blind。没有两名真实独立 reviewer 和第三名 adjudicator 的实际结果时，不能声称 judge 已校准。完整交接、只处理分歧的 adjudication 和 calibration 命令见 [`HUMAN_EVALUATION_RUNBOOK.md`](HUMAN_EVALUATION_RUNBOOK.md)。

### ToolUseMemory 零模型语义复算

repeat-1 的单模型 review 只用于核对确定性 grader 是否复现既有标签；repeat-3 的统计单位是 8 个 unique cases，不是 48 个独立任务。

```powershell
python -m benchmarks.tool_use_memory_semantic_revalidate `
  --source-report benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v2_repeat1.json `
  --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json `
  --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json `
  --semantic-review benchmarks/reviews/tool_use_memory_llm_pair_terra_dev_v2_repeat1_semantic_review_v1.json `
  --output D:\cst_agent_rag_data\agent_eval\memory\repeat1_revalidated.json

python -m benchmarks.tool_use_memory_semantic_revalidate `
  --source-report benchmarks/reports/tool_use_memory_llm_pair_terra_dev_v2_repeat3_v1.json `
  --dataset benchmarks/tool_use_memory_llm_pair_dev_v2.json `
  --manifest benchmarks/tool_use_memory_llm_pair_dev_v2.manifest.json `
  --output D:\cst_agent_rag_data\agent_eval\memory\repeat3_revalidated.json
```

### Sealed verifier

仓库只能复验 verifier/protocol 的工程行为：

```powershell
python -m pytest -q tests/test_sealed_eval_handoff.py
```

真实 sealed 结果还要求仓库外部 issuer、private oracle custody 和 promotion-capable key。完整三阶段命令见 `docs/SEALED_EVALUATION_PROTOCOL.md`；本仓库当前没有可被诚实报告为 blinded/sealed 的 Terra run。

## 通过标准

- Python 离线套件必须 `0 failed`。第三方 deprecation warning 可以记录，但不能掩盖失败。
- 前端测试必须 `0 failed`。
- 前端 build 必须 exit code 0。Vite chunk-size warning 属于性能/包体积风险，不等同功能失败，但需要在 tracker 里记录。
- 默认不允许连接真实 CST。连接级 COM 检查必须显式使用 `python scripts/check.py --level live-cst`。
- 真实 solver 检查必须使用 `python scripts/check.py --level live-cst-solver`，并同时设置 `RUN_LIVE_CST=1` 和 `RUN_LIVE_CST_SOLVER=1`。报告里必须写明 CST 版本/环境、工程路径、是否运行求解器和关键 S11 结果。
- `live-cst` 不属于 `all`，必须显式运行，并且测试层有 `--run-live-cst` / `RUN_LIVE_CST=1` 守卫。
- `live-cst-solver` 不属于 `all`，并额外由 `RUN_LIVE_CST_SOLVER=1` 守卫，避免连接 smoke 误触发耗时求解。
- 旧的 `--include-cst` 入口不再用于默认门禁；真实 CST 只能走 `--level live-cst`，避免 `offline/all` 变成隐式 live 检查。
- 不为过测试修改断言来隐藏真实问题；测试应反映真实行为。

## 模块边界

- `scripts/check.py` 只负责编排测试命令，不包含业务判断。
- Python 测试按文件组织 suite，避免给每个用例硬塞 marker。
- `cst` marker 只用于真实 CST / COM / live solver 依赖。
- `slow` marker 用于 benchmark 或明显耗时的集成测试。
- 前端单元测试仍放在 `frontend/src/__tests__/`；浏览器 E2E 放在 `frontend/e2e/`；构建验证通过 runner 调用 Vite，不复制到 Python 测试里。

## 当前覆盖边界

已覆盖：

- 自研 Planner、tool loop、reflection 与重规划边界。
- Tool runtime、错误分类、trace/session projection。
- FastAPI REST + SSE chat 主路径，包括 tool event 绑定回归。
- S11/farfield 结果摘要、RAG/memory/reflection 的离线行为。
- React API client、hooks、核心组件渲染和 build。
- Playwright mock-API E2E 覆盖 Dashboard、Chat SSE tool event、Trace 和清空状态。

未由默认离线测试证明：

- 真实 CST 安装、COM 连接、CST 求解器运行稳定性。
- 真实 CST 工程文件读写和结果树差异。
- 浏览器端人工体验细节，例如拖拽、滚动、长会话视觉回归。
- 真实 LLM provider 的协议兼容性和质量波动。

这些边界不应该被包装成“已完全验证”。需要上线/演示前，应在 Windows + CST 工作站单独跑 live smoke，并做一次浏览器人工检查。
# Post-Approval Agent E2E v3（2026-08-12）

当前参数绑定审批架构使用新的 developer-visible frozen regression：

```text
benchmarks/agent_e2e_frozen_dev_v3.json
benchmarks/agent_e2e_frozen_dev_v3.manifest.json
```

v3 从 v2 派生且明确是 post-audit，不是 blinded/sealed 数据。它保留 40-case family matrix，
将 8 条普通断连恢复从审批门控的 raw VBA 改为 typed `save_cst_project`；raw VBA 审批由独立
control-plane E2E 评测。

完整 deterministic full eval 报告只落 D 盘：

```text
D:\cst_agent_rag_data\agent_eval\post_approval_v3\deterministic_full_20260812T191500\report.json
SHA-256: 4d152118460d08d2b9400efba621c040ddc623fcad827ae3da07cbd96f2c3d08
```

关键结果：full task 40/40、exact sequence 40/40、disconnect recovery 8/8、invalid call 0；
strict lexical 36/40，4 条失败来自已有材料清单词面 oracle。no-planner task 为 12/40，
no-recovery 为 32/40；no-memory/no-ToolUseMemory 仍为 40/40，不能宣称 memory gain。

raw VBA 审批 E2E：

```powershell
python -m benchmarks.raw_vba_approval_e2e `
  --output D:\cst_agent_rag_data\agent_eval\raw_vba_approval_e2e\report.json
```

Canonical 报告：

```text
D:\cst_agent_rag_data\agent_eval\raw_vba_approval_e2e\final_20260812T203000\report.json
SHA-256: 1cb0a03bf5ed991fc996b57ebe15bb0c75f1e28acbdb246632c09c80a5adb52f
```

9/9 检查覆盖 `/api/chat` 创建参数绑定 request、未批准零 dispatch、pending 不暴露完整参数、
approve 使用服务端 exact args 执行、grant 单次消费、同参 replay 与改参均重新审批。批准 API
不会恢复原模型 turn，因此不得称为 suspended-turn resume。
