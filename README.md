# Agent 工作台

**中文** | [English](README.en.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

CST-Agent 是一个操作 CST Studio Suite 2025 的工程 Agent：通过 Windows COM 桥接这个真实的桌面电磁仿真软件，把自然语言描述的天线需求跑成闭环：

`用户请求 -> Planner -> Tool Runtime -> CST 建模/求解 -> 结果读取 -> 诊断/优化 -> Trace/报告`

CST 这类软件有状态、有真实副作用：一次求解几分钟起步，参数改错模型就废，外部进程卡死也不一定杀得掉。所以这个项目的工作量大头不在调 LLM，而在调用外面那一圈——工具权限、人工审批、失败恢复、trace、评测可复现。为什么这么设计、中途撤销过哪些方案，记录在 [PROJECT_STORY.md](PROJECT_STORY.md)。

> **关于协作方式**：本项目的大量实现工作在 AI 编码工具辅助下完成。架构决策、评测设计与安全边界由我确定，决策记录见 [`docs/adr/`](docs/adr/)。评测结论可复现——数据集、manifest 与报告按字节绑定在证据登记表 [benchmarks/agent_e2e_canonical.json](benchmarks/agent_e2e_canonical.json)，原始报告在 [benchmarks/reports/](benchmarks/reports/)，复现命令见下面的「评测」与「验证」两节。数字的边界和 null result 都写在文档里，没有删。

---

<p align="center">
  <img src="docs/images/dashboard-light.png" alt="Dashboard（浅色主题）" width="100%">
</p>

<p align="center">
  <em>Dashboard：S11 曲线、优化指标、CST 状态、最近工具事件和工作流进度</em>
</p>

<p align="center">
  <img src="docs/images/dashboard-dark.png" alt="Dashboard（深色主题）" width="49%">
  &nbsp;
  <img src="docs/images/chat.png" alt="对话页" width="49%">
</p>

<p align="center">
  <em>左：深色主题 Dashboard　右：带结果查看器的 Agent 对话</em>
</p>

## 这和调一个无状态 API 的区别

- CST 工程是单一有状态资源，建模和求解不可逆，所以工具串行执行，当前 Plan 决定每一步能用哪些工具。一步失败会留下真实状态，恢复逻辑要从这个状态接着走，不能当成 HTTP 错误码直接重试。
- CST 的 COM 接口有 Windows STA 线程亲和，还锁 Python 版本。所有 COM/VBA 调用放在独立子进程里跑，句柄随进程释放，代价是每条命令多一跳子进程。
- 桥接超时时 Python 只能杀自己的子进程；CST 主进程可能还在算上一次求解，而 CST 没有可靠的跨进程 abort API。运行时的做法是标记连接失效、返回 `timeout: True`、明说 CST 可能还在算（[ADR-006](docs/adr/adr-006-cst-timeout-honest-degradation.md)），不假装 abort 成功。

## 架构

```
┌─────────────────────────────────────────────────────────────────────┐
│ 用户 / React UI                                                     │
│ Dashboard · Chat · Trace                                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST + SSE
┌──────────────────────────────▼──────────────────────────────────────┐
│ FastAPI 编排 API                                                    │
│ chat · cst actions · optimization · results · trace · RAG           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Agent Runtime                                                       │
│ Planner → 可替换 Tool Loop（Native / 受限 Pi）→ Reflection          │
│ 结构化 Plan · 条件路由 · replan/reflect/end                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│ Tool Runtime：42 个规范工具，单一执行入口                           │
│ 当前步白名单 · schema 校验 · 审批门 · 分发                          │
│ 错误分型 · 摘要/召回 · token 预算 · trace hooks                     │
└───────────────┬───────────────┬────────────────┬────────────────────┘
                │               │                │
┌───────────────▼───┐ ┌─────────▼──────┐ ┌───────▼────────┐ ┌─────────▼──────┐
│ CST 领域工具      │ │ 结果服务       │ │ 优化           │ │ RAG + Memory   │
│ COM/VBA 桥        │ │ S11/远场       │ │ 诊断           │ │ 专家规则       │
│ primitives        │ │ 回退链         │ │ 回滚           │ │ 官方文档       │
│ fast paths        │ │ 摘要           │ │ 报告           │ │ reflection     │
└───────────────┬───┘ └─────────┬──────┘ └───────┬────────┘ └─────────┬──────┘
                └───────────────┴────────────────┴────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ AgentSession                                                        │
│ 单一真值源 · artifacts · 工具结果 · trace history                   │
│ 结构化记忆 · benchmark/报告证据                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## Tool Runtime

42 个工具走同一个入口 `tool_runtime.execute_tool`，没有第二条分发路径。正常调用和恢复重试过的是同一条流水线：

`当前步白名单 -> schema 规范化/校验 -> 审批门 -> 分发 -> 类型化结果`

- 权限和完成条件分开（[ADR-010](docs/adr/adr-010-plan-step-tool-allowlist.md)）。`allowed_tools` 只是白名单，完成契约是它的子集 `required_tools`：跨 batch 累计完成度，没做完的工具注回 Plan 上下文，一步不会因为随便发生过一次调用就算完。
- 参数按 JSON Schema 校验，可选默认值在校验、哈希、评测之前统一填充。省略一个参数和显式传默认值，在每一层都是同一次调用。
- 失败带类型。恢复引擎只注册有界动作：重连、超时重试、远场监视器补建、参数修复、dry-run 回退。恢复本身也要重新过白名单和审批门，每次恢复的结果挂在 tool event 和 trace 上。
- 大结果先摘要，完整载荷存在 session 里，要用时通过 `recall_tool_result` 召回。曲线可以确定性降采样，端点和全局极值必须保留，标量统计一律按完整原始曲线算。

## 审批

按一次调用能绕过什么来分级：

- 类型化的建模、求解、读结果工具自主执行，参数已经被 schema 和 controller 的护栏框住。
- `execute_vba_script` 能绕过这两层，所以要人批（[ADR-011](docs/adr/adr-011-parameter-bound-tool-approval.md)）。授权绑定工具名、规范化参数的 SHA-256、actor 和有效期，单次消费：CST 侧失败要重新批，参数改一个字节就是新请求。服务端保存精确参数（UI 只看到 500 字符预览），签发前重跑一遍无副作用的预检。
- 审批挂起不算失败也不算完成。Plan 停在当前步，只有真正执行成功的工具计入完成度；批准后在同一个 HTTP 请求里按服务端保存的参数精确重放。
- 清会话、打开或关闭工程都会撤销 pending 请求和已发授权。这是单机单用户工具，没有多租户认证，不要理解成 RBAC。

## 执行引擎：Native 和 Pi

`run_agent_turn()` 里的 tool loop 可以整体替换。默认 `AGENT_BRAIN=native` 用手写 Python loop；`AGENT_BRAIN=pi` 把模型轮次交给受限 Node sidecar，跑 MIT 许可的 pi-agent-core。两种模式下 Planner、会话状态、恢复、trace 和 COM 边界都留在 Python。细节见 [`integrations/pi_agent_core/README.md`](integrations/pi_agent_core/README.md)。

- Pi 不加载 pi-coding-agent，没有 Bash/Read/Write/Web/MCP 内置工具，只能看到当前步过滤后的工具目录，每个 batch 结束由 Python 重新过滤。每次调用照样回到 `execute_tool`，白名单、审批、恢复、COM 隔离同样生效。
- 控制协议带版本：hello/health/run/result/error 信封、request/session 双相关、能力协商、心跳、有序事件、cancel/steer/follow-up。
- sidecar 缺失、超时、坏 JSONL、模型错误都显式失败，不静默回退 Native，评测和 trace 才不会把 harness 归因搞错。崩溃后只在下个任务前重启，不自动重放可能已有副作用的轮次。
- 真实模型配对（gpt-5.6-terra，12 个 case × 3 repeats）：task-majority Pi 12/12 对 Native 10/12，strict 10/12 对 7/12，优势集中在 solver 工作流；但精确 McNemar p=0.5 / 0.25，token 基本持平，只能说方向性改善，不能说显著。数据 SHA 绑定在 [docs/PI_HARNESS_EVALUATION.md](docs/PI_HARNESS_EVALUATION.md)。

## 评测

这个仓库对数字的规矩：能复算、边界写清楚、null result 不删。

- 证据登记表 [benchmarks/agent_e2e_canonical.json](benchmarks/agent_e2e_canonical.json) 按字节绑定冻结数据集、manifest 和各报告；早期 10-case 开发报告降级成历史诊断。
- 40-case 确定性消融（manifest/SHA 绑定，developer-visible；现行证据是登记表里的 `post_approval_v3_deterministic_full`）：full 40/40（严格词面 36/40），no-context 执行 40/40 但严格词面 28/40，no-recovery 32/40（严格词面 28/40），no-planner 0/40；no-memory 和 no-ToolUseMemory 都是 40/40。planner 和 recovery 的贡献成立，memory 增益没测出来。
- 真实模型审计的修正留在记录里：第一轮 Terra 审计（7 个代表 case）暴露了词面/参数假阴性和一个欠指定的 solver oracle；post-audit v2 回归 execution 7/7、exact sequence 7/7、strict lexical 6/7，并写明 v1 输出参与了 oracle 修订。3-repeat 的语义审阅（response-SHA 绑定）execution 19/21、有据性 16/21。重复样本相关，不是 21 个独立任务，也都不是 blinded。
- ToolUseMemory 真实模型 A/B（四个失败族，8 case × 2 臂 × 3 repeats）：learned 臂 24/24 注入了记忆、确实改变了首轮工具排序，但两臂都 24/24、paired delta=0，learned 平均多约 220 token。这个 null result 保留在仓库里。
- 密封评测是协议不是结果：v2 verifier 把外置信任策略绑定到公开 case、fixture、runner、prompt 和工具目录的真实字节，负例测试通过；但没有真实外部 issuer，也没跑过完整 sealed run。见 [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md)。
- 人工校准没完成之前，LLM judge 分数（如 RAG 宏平均有据性 0.766、17/55 条 unsupported）只当开发诊断。双 reviewer 标注包已导出，流程在 [docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md)。

## CST 这一侧

CST 是让上面这些控制面变得必要的底座。COM/VBA 桥之上给常见天线（矩形贴片、半波偶极子、像素贴片）做了确定性构建器，常规请求不经过 LLM 直接出模型，开放任务走 tool calling。优化闭环是：求解、带回退链的 S11/远场读取、`diagnose_s11` 分诊，然后物理引导调参——f∝1/L 割线步长、方向记忆、经真实重求解验证的回滚、停滞时恢复 best-so-far。用 CST 自带优化器时分工写死：Agent 管目标解释、诊断、objective 配置和回滚，优化器管数值搜索，求解器始终是物理真值。

## RAG 和记忆

两条检索通道是两套系统，数字不合并。

官方文档 RAG：CST Online Help 切成 13,242 段英文 chunk，`bge-base-en-v1.5` 写入 Chroma；检索是 dense Top-20 -> `ms-marco-MiniLM-L6-v2` 重排 -> 来源去重 Top-3，保留完整 rank/provenance。30 条冻结 held-out 上 Recall@3 0.900 -> 0.967，MRR 0.750 -> 0.794；消融显示只做去重是 0.900，加 cross-encoder 才到 0.967。不过真正改变检索结果的只有 2/30 条，Recall delta 的 paired-bootstrap 95% CI 是 [0.000, 0.167]，增益方向性成立、统计不显著。热 p95 从 63.29 涨到 1019.50 ms（同机观测）。设计和完整数字见 [docs/RAG_DESIGN.md](docs/RAG_DESIGN.md)。

记忆分三块：

- StructuredMemory 存优化轮的经验和失败，按工程和设计签名隔离。置信度不信 LLM 自评：本轮实测改善保持原分，无改善减半，回滚压到 0.2。
- ToolUseMemory 记录工具失败和纠正，只用来重排当前步白名单的顺序，不加减任何权限。机制闭环验证过；行为增益没测出来，就是上面那个 null result。
- 对话层的目标、约束、未决问题跨轮保留。约束用正则确定性抽取、存用户原话，planner 的转述只做兜底。

## 边界

- 真实 CST 求解和 COM 测试要本机 Windows 加 CST Studio Suite，CI 只跑离线测试。
- 部分 9.4 GHz Rogers5880 微带案例还压不稳 -10 dB，诊断驱动的调参有待加强。
- LangGraph 用过又撤了：graph 节点是 runtime 函数的薄透传，checkpointer 因 COM 句柄没法序列化用不了，replan 边在生产路径走不到，所以改回手写控制流。步级单工具驱动还没做。
- Chat SSE 每 0.5 秒轮询会话状态上报工具事件，不是 token 级流式。
- 后端是单用户桌面工具：一个全局 session、一把操作锁、没有认证，只应绑定 `127.0.0.1`。
- BO/PSO/DE 是 LLM 提案不可用时的兜底采样器，不是完整优化环。
- 远场后处理的部分 VBA 路径有 COM 上下文限制，可能需要 CST 结果模板。
- 旧 Gradio 实现已删除，React + FastAPI 是唯一支持的 UI。

## 快速开始

> 已验证的本地 Python 版本：3.11 和 3.14。真实 CST 模式请用 Windows。

```bash
# 安装包和可选功能依赖
pip install -e .[dev,web,report,rag]

# 配置 LLM provider
copy .env.example .env
# 编辑 .env：设置 MODEL_API_KEY/MODEL_NAME（或旧的 OPENAI_*），然后选择
# chat_completions、responses 或 anthropic_messages，对应 MODEL_API_PROTOCOL。
# 协议与缓存边界：docs/MODEL_PROTOCOLS_AND_CACHE.md

# 启动 React 前端 + FastAPI 后端
启动.bat

# 或手动启动后端
python -m cst_agent_workbench.web_app              # 真实 CST 模式
python -m cst_agent_workbench.web_app --dry-run    # 无 CST 的演示模式
```

打开 `http://127.0.0.1:5173` 使用 React UI。

在 Windows 上，安装或评测前把包、模型、RAG、记忆和临时缓存钉到 D 盘：

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

这些设置是执行前提，本身不是证据元数据。正式 RAG 跑次仍要核验 manifest 里记录的 dataset/index/model 契约。

常用 CLI 命令（dataset、manifest 和报告按上面的评测规矩做 SHA 绑定；人工校准包的导出流程见 [docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md)）：

```bash
# 不连接 CST，生成确定性贴片 VBA
python -m cst_agent_workbench.cli build-patch --dry-run --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --export-vba runs/demo/generated.vba

# 真实 CST 闭环报告
python -m cst_agent_workbench.cli optimize-patch-report --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --max-rounds 3 --output runs/demo/patch_optimization_report.md

# 真实 CST 评测矩阵
python -m cst_agent_workbench.cli patch-matrix --microstrip-rounds 3 --output-dir runs/patch_matrix --summary runs/patch_matrix/summary.md --json-output runs/patch_matrix/summary.json

# 优化提案消融（不跑完整 Agent runtime）
python benchmarks/agent_ablation_runner.py --cases 20 --max-rounds 6 --assert-thresholds --output benchmarks/reports/agent_ablation_fake_cst.json --summary-md benchmarks/reports/agent_ablation_fake_cst.md

# 完整 40-case Agent 机制评测，带 manifest/SHA 与 release 门（复现上面"评测"节的 v3 消融数字；
# 确定性代理不真正调模型，但 .env 里仍需有一个 MODEL_API_KEY 占位值，否则执行器会直接拒绝、全组 0/40）
python -m benchmarks.agent_e2e_ablation_runner --dataset benchmarks/agent_e2e_frozen_dev_v3.json --manifest benchmarks/agent_e2e_frozen_dev_v3.manifest.json --full-frozen-eval --provider deterministic_proxy --artifact-root D:/cst_agent_rag_data/agent_e2e_frozen_artifacts --output benchmarks/reports/agent_e2e_frozen_dev_v3_deterministic_ablation_v1.json --summary-md benchmarks/reports/agent_e2e_frozen_dev_v3_deterministic_ablation_v1.md
```

公开命令只授予 execution eligibility。私有核验、promotion 命令、外部托管要求和 fail-closed 发布规则写在 [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md)。仓库不包含真实外部签发的密封包。完整命令列表（Terra 回归、repeat 稳定性、记忆配对、语义复验）见英文版 [README.en.md](README.en.md)。

## 验证

跑 `--level eval` / `offline` / `all` 之前，有三个前提要先备齐，否则会看到成片的失败而不是跳过：

```bash
# 1. Pi sidecar 的 Node 依赖（test_pi_brain 需要，node_modules 不入库）
npm install --prefix integrations/pi_agent_core

# 2. 一个 MODEL_API_KEY 占位值。确定性代理不真正调模型，但执行器会先检查
#    key 是否存在，缺了就直接拒绝、整组归零。
copy .env.example .env    # 里面填任意占位值即可

# 3. LF 检出。冻结数据集/manifest/报告按原始字节绑定 SHA-256，行尾被改写
#    就永远对不上。仓库已用 .gitattributes 把 benchmarks/ 钉成 LF；如果你在
#    加 .gitattributes 之前就克隆过，跑一次：
git rm --cached -r benchmarks && git checkout HEAD -- benchmarks
```

```bash
# 快速冒烟：错误、摘要、planner 路由、FastAPI chat/API（无需上面的前提）
python scripts/check.py --level smoke

# 核心后端门禁：agent runtime、tool runtime、UI 接线、web API
python scripts/check.py --level core

# 离线评测契约：RAG、Agent E2E、Memory grader、sealed verifier
python scripts/check.py --level eval

# 完整离线 Python 套件，排除真实 CST 测试
python scripts/check.py --level offline

# 前端 Vitest
python scripts/check.py --level frontend

# 浏览器 E2E（mock API）
npm.cmd --prefix frontend exec playwright install chromium  # 仅首次
python scripts/check.py --level e2e

# TypeScript + Vite 构建
python scripts/check.py --level build

# 合并门禁：smoke + core + offline + ruff + frontend + e2e + build
python scripts/check.py --level all

# 仅本机 Windows + CST：对 D 盘工程副本做连接冒烟
$env:CST_LIVE_PROJECT_COPY="D:\cst_agent_rag_data\projects\status_copy.cst"
$env:RUN_LIVE_CST="1"; python scripts/check.py --level live-cst

# 仅本机 Windows + CST：Python API 构建 + 真实求解冒烟
$env:RUN_LIVE_CST_MUTATING="1"; $env:RUN_LIVE_CST_SOLVER="1"
python scripts/check.py --level live-cst-solver
```

分层标准见 [docs/TESTING.md](docs/TESTING.md)。默认这些检查不连接真实 CST；live 检查是本机显式 opt-in，不进 `--level all`，因为托管 runner 没有 CST Studio Suite 和许可证。

CI 跑 lint、多个 Python 版本的离线测试、fake-CST smoke、TypeScript 检查、前端构建和 Vitest。

## 目录结构

```
cst_agent_workbench/
├── agent/          # Planner、runtime、session、memory、recovery、reflection、trace、tool runtime
├── cst/            # CST controller、受控 primitives、确定性天线构建器
├── optimization/   # S11 诊断、优化策略、回滚、报告
├── rag/            # 专家规则、官方文档摄入/检索、旧 dynamic 迁移
├── results/        # S11 和远场读取、回退链、摘要
├── web/            # FastAPI 路由模块
├── web_api.py      # FastAPI 应用工厂和共享 API helper
├── web_app.py      # 后端入口，支持 --dry-run
└── cli.py          # 批量构建、优化报告、benchmark 矩阵

frontend/
├── src/pages/      # Dashboard、Chat、Trace
├── src/components/ # S11 图、工作流轨道、指标/状态组件
├── src/api/        # 类型化 API 客户端
└── src/__tests__/  # Vitest API、hook、组件测试

integrations/       # 可选 Pi harness 的受限 Node sidecar
tests/              # Python 单元/集成/评测测试
benchmarks/         # Fake-CST 消融和可复现证据报告
docs/               # 安装、审计、交接、归档历史
```
