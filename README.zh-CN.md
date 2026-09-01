# Agent 工作台

**中文** | [English](README.en.md)

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

仓库地址：[github.com/ncdeng/agent-workbench](https://github.com/ncdeng/agent-workbench)

**难点不在调用一次 LLM API，而在调用周围的一切**：在一个 Agent 并不拥有的桌面应用里管理真实副作用，在外部进程失败时如实恢复而不是假装 abort，把高风险操作挡在参数绑定审批后面，并产出可以按字节复算的评测证据。

**Agent 工作台**（CST-Agent）是一套面向真实工程工具的垂直 Agent 运行时。它通过 Windows COM 桥接操作 CST Studio Suite 2025，把自然语言天线需求变成可追溯闭环：

`用户请求 -> Planner -> Tool Runtime -> CST 建模/求解 -> 结果读取 -> 诊断/优化 -> Trace/报告证据`

这个仓库应被理解为**对接真实工程软件的 Agent 应用**，而不是通用 RAG 聊天机器人或一层薄 UI。架构说明见 [PROJECT_STORY.md](PROJECT_STORY.md)。

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

## 为什么这件事难

工具面是有状态的桌面工程软件，不是无状态 Web API。三条后果决定了大部分设计：

- **单一可变资源。** 一个 CST 工程被共享、有状态，建模和求解会不可逆地改写它。因此工具必须串行执行，当前 Plan 约束每一步可见的工具，失败步骤会留下真实状态，恢复逻辑必须据此推理，而不能只看成一个 HTTP 错误码。
- **COM 子进程隔离。** CST 的 COM 接口有 Windows STA 线程亲和，并锁定 Python 版本，所以所有 COM/VBA 工作跑在独立子进程桥里；COM 句柄随进程释放，不进 Agent 进程。代价是每条命令都要跨一次子进程。
- **不假装 abort（ADR-006）。** 桥接超时时，Python 可以杀掉自己的子进程——但 CST Design Environment 是另一个 OS 进程，可能仍在执行上一次求解，CST 也没有可靠的跨进程 abort API。运行时如实降级：标记连接已死以便下次命令走重连，返回 `timeout: True`，并告诉用户 CST 可能仍在执行上一次求解。它不会声称一次做不到的 abort。

## Agent 架构

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

## Tool Runtime 控制面

全部 42 个规范工具只走一个入口 `tool_runtime.execute_tool`；任何 harness 都没有第二条分发路径。正常调用和恢复都经过同一条流水线：

`当前步白名单 -> schema 规范化/校验 -> 审批门 -> 分发 -> 类型化结果`

- **权限与完成条件分离（ADR-010）。** Planner 的 `allowed_tools` 只是白名单；另有子集 `required_tools` 作为 all-of 完成契约。运行时跨 batch 累计 `completed_tools`，并把 `remaining_required_tools` 注回 Plan 上下文，因此一步不会因为任意一次调用就标完成。
- **Schema 驱动调用。** 参数按规范 JSON Schema 校验；可选默认值在校验、哈希和评测之前就规范化，因此省略一个标志和显式传 `false` 在每一层都是同一次调用。
- **错误分型与恢复。** 失败按类型分类，`FailureRecoveryEngine` 注册有界动作：重连、超时重试、远场 monitor 自动补建、参数修复、dry-run 回退。恢复本身再走白名单/schema/审批门，每次恢复与重试结果挂到 tool event 和 trace 上，形成可审计链。
- **大结果。** 超大载荷先摘要，完整载荷用 `recall_tool_result` 召回，避免灌爆上下文；1D/S 参数可按确定性规则降采样，必须保留端点和全局极值，所有标量摘要仍按完整原始曲线计算（ADR-012）。
- **Trace hooks。** 工具调用、token、恢复和审批写入同一条 run 级 trace，chat 与程序化优化轮共用（ADR-008）。

## 分级自治：类型化工具可跑，Raw VBA 先问

自治按一次调用能绕过什么来分级：

- **类型化工具自主执行。** 规范建模、求解和结果读取工具不经人工审批，因为参数被规范 schema 和 controller 自身护栏约束。
- **Raw VBA 走审批（ADR-011）。** `execute_vba_script` 可以同时绕过这两者，因此需要绑定 `(tool_name, 规范化参数的 SHA-256, actor, expiry)` 的一次性授权。Schema 默认值先规范化，object key 顺序不影响 hash。grant 在 handler 前消费且只能用一次：CST 侧失败仍须重新批准，参数任意变化都会产生新 request。
- **没有过期 grant。** 审批端点在服务端保存精确参数（UI 最多看到 500 字符预览），签发前重跑无副作用的白名单/schema/hash preflight；清空会话或打开/关闭工程会撤销所有 pending request 和有效 grant。
- **挂起既不是失败也不是完成（ADR-013）。** `approval_pending` 保持当前步骤 `in_progress`，只有真正执行成功的工具计入 `completed_tools`。批准端点在同一 HTTP 请求里重放精确绑定的调用；诚实表述是「参数绑定审批 + 精确重放执行」，不是「暂停/恢复推理」。
- **单用户范围。** 这是本机单用户桌面应用：actor 固定为 `local-desktop-user`，没有多租户认证，不能把它读成 RBAC 或企业级授权。

## 可插拔 Harness：Native 与 Pi

`run_agent_turn()` 内部的 tool loop 是可替换组件（ADR-009）。`AGENT_BRAIN=native`（默认）继续用自研 Python loop；`AGENT_BRAIN=pi` 把模型 turn 和 tool-loop 续跑交给受限 Node sidecar，运行 MIT 许可的 pi-agent-core。Planner、会话状态、恢复、Trace 和 CST COM 边界在两种模式下都留在 Python。见 [`integrations/pi_agent_core/README.md`](integrations/pi_agent_core/README.md)。

- **受限 sidecar。** Pi 不加载 pi-coding-agent，不开放 Bash/Read/Write/Edit/Web/MCP 内置工具；它只看到当前步过滤后的规范工具目录，每个 batch 结束后由 Python 重新过滤。每个 tool call 仍回到 `execute_tool`，因此白名单、审批、恢复、Trace 和 COM 隔离同样生效——Pi 也不持有可变 Session 或 COM 句柄。因为 CST 工程是单一有状态资源，工具执行强制串行。
- **版本化控制协议。** `hello/health/run/result/error` 信封、request/session 双相关、能力协商、带 heartbeat/generation 的长驻 sidecar session registry、有序 harness 事件，以及相关的 cancel/steer/follow-up。
- **失败语义。** sidecar 缺失、超时、非法 JSONL 或模型错误都显式失败——没有静默回退 Native，避免评测和 Trace 把 harness 归因错位。崩溃后只在下一任务前重启 sidecar；可能已有副作用的 CST 轮次不会自动重放。
- **目前测到的结果，以及边界。** 最新真实模型配对（`gpt-5.6-terra`，12 个 unique case × 3 repeats，developer-visible 且 post-audit）的 task-majority 为 Pi 12/12 vs Native 10/12，strict-majority 为 10/12 vs 7/12，优势集中在两类 solver 工作流——但精确 McNemar 为 p=0.5 / 0.25，all-repeats 端点相同（9/12 和 5/12），平均 token 基本持平（Pi +1%）。repeat 相关，统计单位是 12 个 case。这是复杂任务上的方向性改善，不是显著或普遍的质量主张；Pi 今天确定的价值仍是协议、隔离、恢复、可观测和控制面。SHA 绑定细节见 [docs/PI_HARNESS_EVALUATION.md](docs/PI_HARNESS_EVALUATION.md)。

## 评测纪律

这个仓库里的数字是证据，围绕数字的规则本身也是工作的一部分：

- **SHA 绑定证据链。** Agent 证据登记表 [benchmarks/agent_e2e_canonical.json](benchmarks/agent_e2e_canonical.json) 按字节绑定 frozen v1/v2 dataset 与 manifest、40-case 确定性跑次、Terra repeat-3、semantic-audit v2 以及 post-audit v2 报告。更早的 10-case 开发报告标为历史诊断，不是证据。
- **冻结集与消融。** 完整 Agent harness 从 `CSTAgent.chat()` 进入，只替换 CST 边界。manifest/SHA 绑定的 40-case developer-visible 确定性 v1：full 40/40，no-context execution 40/40 但 strict-lexical 32/40，no-recovery 32/40，no-planner 0/40。no-memory 与 no-ToolUseMemory 两臂都是 40/40——因此这次跑证明的是机制以及 planner/recovery 的贡献，并明确没有展示 memory gain。
- **真实模型审计，修正写在记录里。** 第一次 Terra v1 审计（7 个代表 case）暴露了词面/参数假阴和一次欠指定的 solver 失败；post-audit v2 回归——并记录 v1 输出参与了 oracle 修订——execution 7/7，exact sequence 7/7，invalid call 0，strict lexical 6/7。对 3-repeat 跑次的 response-SHA 绑定语义审阅为 execution 19/21、grounded task 16/21。三次重复是相关的，不是 21 个独立任务，这些集合也都不是 blinded。
- **空结果保留，不删除。** 真实模型 ToolUseMemory A/B（四个失败族，8 case × 2 臂 × 3 repeats）在 learned 臂 24/24 样本中注入了 memory 并改变了首轮工具排序——但两臂都是 24/24，paired delta=0，invalid call=0，learned 平均大约多 220 tokens。它作为保留的空结果留在仓库里；尾部延迟因已记录的原因不进入因果比较。
- **密封评测是协议，不是结果。** v2 sealed-eval verifier 把外置信任策略绑定到公开 case、fixture、runner、prompt 和工具目录的真实字节，只授予 execution eligibility；release eligibility 还需要另一份针对 response trace 的可信 promotion receipt。仓库没有真实外部 issuer、没有私有 oracle custody、也没有完成的密封跑——主张是「协议已实现并通过负例测试」，从来不是「已取得 blinded/sealed 证据」。见 [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md)。
- **人工校准尚未完成。** 导出器产出源绑定、verdict-blind 的 A/B pack（21 个 Agent 样本、55 条 RAG claim），共用 `pack_id`、SHA 绑定排列和 rubric 字节，供双 reviewer 校准；自然 sample/case/claim ID 仍保留，因此这些 pack 不能称为完全 provenance-blind。在独立标注完成前，LLM judge 分数（例如 RAG macro groundedness 0.766，17/55 条 unsupported）只是开发诊断，不是效果主张。操作手册：[docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md)。

## 工程底座：CST Studio Suite

CST 是让 Agent 工程变真实的底座——带真实副作用的桌面求解器——不是研究目标本身。在 COM/VBA 桥之上，项目为常见天线（矩形贴片、半波偶极子、像素贴片）提供确定性 fast-path 构建器，让常规请求避开 LLM 漂移；开放任务仍走 tool calling。闭环是求解执行、带回退链的 S11/远场读取、`diagnose_s11` 分诊，以及物理引导调参：f∝1/L 割线步长、方向记忆、经真实重求解验证的回滚，以及恢复 best-so-far 的停滞检测。使用 CST Native Optimizer 时职责分开：Agent 解释物理目标、诊断结果、配置 objective 并拥有回滚；原生优化器做数值搜索；求解器始终是物理真值（ADR-014）。完整回路——建模 -> 求解 -> S11 诊断 -> 带回滚的调参 -> 落盘证据——作为上面控制面的证据存在，下面的 CLI 报告命令会端到端跑一遍。

## RAG 与 Memory

两条检索通道不是同一套系统，数字不能合并。

### 官方文档 RAG

CST Online Help HTML 切成 13,242 段英文 schema-v4 chunk，用 `bge-base-en-v1.5` 写入 Chroma。检索是 dense Top-20 -> `ms-marco-MiniLM-L6-v2` cross-encoder 重排 -> 来源去重到 Top-3，并保留完整 rank/provenance。在冻结的 30 条 held-out v1 上，重排把 Recall@3 从 0.900 提到 0.967，MRR 0.750 -> 0.794，nDCG@3 0.754 -> 0.817；正交消融显示只做去重是 0.800 -> 0.900，要到 0.967 需要 cross-encoder。真正改变结果的只有 2/30 条，Recall delta 的 paired-bootstrap 95% CI 是 [0.000, 0.167]——cross-encoder 增益是方向性的，不是统计显著。热 p95 延迟从 63.29 升到 1019.50 ms，这是同机观测值。设计与完整数字见 [docs/RAG_DESIGN.md](docs/RAG_DESIGN.md)。

### Agent 记忆

- **StructuredMemory** 是 reflection 经验与失败的唯一生产存储，按工程和设计签名分 scope，经 confidence/evidence 门槛、`min_score` 和关键词回退；回滚轮以降低后的 confidence（0.2）写入。旧 dynamic JSON 只通过显式迁移开关读取；新 reflection 不再双写。
- **ToolUseMemory** 召回过往工具失败与纠正，并只安全重排当前步白名单——它从不扩大权限。生产写/持久化/scoped 召回/安全重排回路已端到端证明；真实模型上的行为增益没有（见上面保留的空结果）。
- **对话上下文。** 显式目标、约束和未决问题会跨 follow-up 轮次保留；token 预算同时计算消息和工具 schema。

## 工程边界

这些是对接真实桌面工程工具时的有意约束：

- 真实 CST 求解和 COM 测试需要本机 Windows 工作站和 CST Studio Suite；GitHub CI 只跑离线测试。
- 远场后处理可能需要 CST 结果模板，因为部分 live VBA 绘图路径有 COM 上下文限制。
- 真实 CST 优化可观测且可回滚，但部分 9.4 GHz Rogers5880 微带案例仍需要更强的诊断驱动调参，才能稳定达到 `S11@f0 <= -10 dB`。
- LangGraph 已撤销（ADR-001）：graph 节点只是 runtime 函数的薄透传，checkpointer 因 Agent 持有不可序列化 COM 句柄无法启用，replan 边在生产路径上走不到——因此 Agent 使用自研控制流（planner -> tool loop -> reflection）。步级单工具驱动是后续工作。
- Native vs Pi 的真实模型配对存在但规模小：12 个 unique case × 3 repeats，developer-visible、post-audit，repeat 相关。它显示 solver 工作流上的方向性改善（精确 McNemar p=0.5 / 0.25）——不是统计显著，不是 blinded/sealed，也不是 release-eligible。Native 仍是默认和回滚基线。
- Chat SSE 每 0.5s 轮询会话状态上报工具事件；不是 token 级流式。
- FastAPI 后端是单用户桌面工具：一个全局 agent/session，一把操作锁，没有认证。请绑定 `127.0.0.1`；不要在不信任的网络上暴露 `--host 0.0.0.0`。
- 确定性 provider 是机制回归，不是 LLM 质量证据。40-case Agent 集合是 developer-visible；v2 明确记录 Terra v1 输出参与了 oracle 修订。7-case Terra 跑次是方向性的，没有统计功效，也不是 blinded。ToolUseMemory 有生产写/持久化/scoped 召回/安全重排机制对，但尚未证明真实模型 held-out 成功率增益。
- BO/PSO/DE 模块是 LLM 提案不可用时的有界兜底采样器，不是完整优化环替代，也不应被表述成那样。
- Gradio 实现已从生产树移除。部分旧脚本和可选依赖声明仍是待清理尾巴；支持的 UI 是 React + FastAPI。

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

这些设置是执行前提，本身不是证据元数据。正式 RAG 跑次仍要核验 manifest 里记录的规范 dataset/index/model 契约。

常用 CLI 证据命令（dataset、manifest 和报告按上面的评测纪律做 SHA 绑定；人工校准 pack 导出见 [docs/HUMAN_EVALUATION_RUNBOOK.md](docs/HUMAN_EVALUATION_RUNBOOK.md)）：

```bash
# 不连接 CST，生成确定性贴片 VBA
python -m cst_agent_workbench.cli build-patch --dry-run --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --export-vba runs/demo/generated.vba

# 真实 CST 闭环报告
python -m cst_agent_workbench.cli optimize-patch-report --f0 9.4 --er 2.2 --h 1.6 --loss 0.0009 --material Rogers5880 --max-rounds 3 --output runs/demo/patch_optimization_report.md

# 真实 CST 评测矩阵
python -m cst_agent_workbench.cli patch-matrix --microstrip-rounds 3 --output-dir runs/patch_matrix --summary runs/patch_matrix/summary.md --json-output runs/patch_matrix/summary.json

# 优化提案消融（不跑完整 Agent runtime）
python benchmarks/agent_ablation_runner.py --cases 20 --max-rounds 6 --assert-thresholds --output benchmarks/reports/agent_ablation_fake_cst.json --summary-md benchmarks/reports/agent_ablation_fake_cst.md

# 完整 40-case Agent 机制评测，带 manifest/SHA 与 release 门
python -m benchmarks.agent_e2e_ablation_runner --dataset benchmarks/agent_e2e_frozen_dev_v1.json --manifest benchmarks/agent_e2e_frozen_dev_v1.manifest.json --full-frozen-eval --provider deterministic_proxy --artifact-root D:/cst_agent_rag_data/agent_e2e_frozen_artifacts --output benchmarks/reports/agent_e2e_frozen_dev_v1_deterministic_ablation_v2.json --summary-md benchmarks/reports/agent_e2e_frozen_dev_v1_deterministic_ablation_v2.md
```

公开命令只授予 execution eligibility。私有核验、promotion 命令、外部托管要求和 fail-closed 发布规则写在 [docs/SEALED_EVALUATION_PROTOCOL.md](docs/SEALED_EVALUATION_PROTOCOL.md)。仓库不包含真实外部签发的密封包。

## 验证

```bash
# 快速冒烟：错误、摘要、planner 路由、FastAPI chat/API
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

分层标准见 [docs/TESTING.md](docs/TESTING.md)。默认这些检查不连接真实 CST。真实 CST solver/API 检查仍是本机显式 opt-in，不是 `--level all` 的一部分，因为托管 runner 没有 CST Studio Suite 或许可证。

CI 会跑 lint、多个 Python 版本上的离线测试、fake-CST smoke、TypeScript 检查、前端构建和 Vitest。真实 CST 检查留在本地，因为托管 runner 没有 CST Studio Suite，也没有 Windows COM。

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

遗留说明：旧 Gradio 实现、它的 `ui` extra 和手工 HTTP 脚本已经去掉；React + FastAPI 是唯一支持的 UI 路径。
