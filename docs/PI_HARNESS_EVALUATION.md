# Pi Harness 架构与评测记录

更新时间：2026-08-12。

## 当前结论

Pi Agent Core 已接入为可替换的 Executor Harness，但不是整个 Agent 的替代品。

- Python Host 是业务与安全真值：`CSTAgent.chat()`、Planner、`AgentSession`、动态工具权限、
  `tool_runtime.execute_tool`、Recovery、Trace、solver safety 和 CST COM 均留在 Python；
- Pi/Node 只负责模型 turn、上下文格式转换和顺序 Tool Loop；
- Pi 不加载 coding-agent，也拿不到 Bash、文件、Web、MCP、COM 句柄或可变 Session；
- Native 是默认基线。Pi 故障显式失败，不静默 fallback，避免评测归因错误。

最新的真实 Terra 扩展配对包含 12 个 unique cases、每臂 3 repeats。Pi 的 case-majority
task/strict 分别为 `12/12`、`10/12`，Native 为 `10/12`、`7/12`；但 exact McNemar
`p=0.5`、`p=0.25`，且 all-repeats task/strict 两臂都为 `9/12`、`5/12`。因此只观察到
复杂工作流上的方向性改善，没有统计显著或稳定性优势。Pi 的可证明价值仍首先是 Harness
工程能力；质量增益属于待扩大独立样本后复验的假设。

## Harness 2.0 能力

### 协议与生命周期

- `hello / health / run / result / error` 版本化 JSONL envelope；
- request/session 双相关校验、capability negotiation 和稳定错误码；
- Python 持有 `SidecarProcess`，Node worker 长驻并按 session 注册；
- 每轮清理 Pi Agent 状态，避免跨任务泄漏；
- 心跳、worker generation、下一任务前崩溃恢复；
- Sidecar 出错后不自动重放可能已产生 CST 副作用的整轮任务。

### 上下文、事件与控制面

- Context Envelope v1：messages、动态 tools、Plan、token budget 和执行标志；
- 每个 tool batch 后由 Python 更新 Plan，再向 Pi 下发下一轮工具目录；
- 有序事件流覆盖 run/token/tool/recovery/plan/catalog 生命周期；
- FastAPI 提供 SSE 查询及 correlated cancel、steer、follow-up；
- steering 在当前工具 batch 结束后生效，follow-up 在原任务结束边界续接；
- cancel 只取消 Pi/模型层，不声称能强停 CST Design Environment 中已开始的 solver。

## 机械契约验证

- Protocol/Pi/Registry：41 passed；
- Host Runtime/Web/Recovery：84 passed；
- Steering/Follow-up：59 passed；
- Context Envelope：33 passed；
- Harness Event/Web：41 passed；
- Node 同进程 smoke：3 runs、3 tool requests、7 assistant messages，并实际覆盖 steer/follow-up；
- Ruff 通过，结束后无遗留 Pi Node 进程。

所有测试 temp、cache、模型与报告均在 D 盘。

## 早期 4-case Native/Pi 配对复测

配置：`gpt-5.6-terra`、OpenAI-compatible provider、developer-visible frozen v2、Fake-CST。
这不是 blinded/sealed evaluation；3 repeats 是同一任务的相关采样，统计单位是 4 个 unique
cases，不能把 12 次调用宣传成 12 个独立任务。

Cases：

- `status_read_01`
- `context_followup_01`
- `disconnect_recovery_01`
- `solver_workflow_01`

| 指标 | Native | Pi |
|---|---:|---:|
| task success | 9/12 | 9/12 |
| strict success | 9/12 | 9/12 |
| unique-case majority | 3/4 | 3/4 |
| unique-case all-repeats | 3/4 | 3/4 |
| exact sequence | 9/12 | 9/12 |
| first-tool accuracy | 10/12 | 10/12 |
| recovery | 3/3 | 3/3 |
| mean tokens | 7,583 | 8,924 |
| mean latency | 35.4 s | 39.9 s |
| p95 latency | 67.7 s | 119.8 s |
| provider failure | 0 | 0 |

SHA-bound paired endpoint：majority/all-repeat 均为 `both_pass=3, native_only=0,
pi_only=0, both_fail=1, delta=0, p=1.0`。

报告目录：

`D:\cst_agent_rag_data\agent_eval\pi_harness_2_0\pair_repeat3_20260812`

- Native SHA-256：`7419f22e24cd72e20fc20d0ca05cab889ba57602defd5e3b1b2a6588d6e75d7f`
- Pi SHA-256：`8393df1902c3e8cc67573553f9d3d1b388f8d502a222a9fc6ce2d46d3b2e3cd3`
- paired comparison SHA-256：`92aca20e2e9bfc2f92d908f274e3b6decca940350a1eb76ab5bd430005dbd523`

比较器从 `groups.full.cases` 重算 endpoint，校验模型、provider、dataset/manifest SHA、
case/repeat/sample/seed 对齐，并绑定两份源报告及比较器代码 SHA；不信任源报告已有聚合值。

## Solver 失败归因与通用修复

旧正式复测中，两臂都在 `solver_workflow_01` 为 `0/3`。六条轨迹说明失败不是 Pi 独有：

- Planner 偶发选择过粗的 `execute_vba_script`；
- Executor 偶发重复幂等调用或提前结束；
- 最明确的运行时代码缺陷是：多工具 active step 只要发生任意一次 tool call 就整体完成，
  随后的动态目录会移除尚未执行的 sibling tools。

修复没有读取 benchmark oracle，也没有把 `allowed_tools` 当作“全部必做”。历史真实计划中，
多工具白名单既有“全部必做”，也有候选/条件工具。新契约因此分离为：

- `allowed_tools`：权限边界，只说明本 step 能调用什么；
- `required_tools`：Planner 显式声明的 all-of 完成条件；
- `completed_tools`：跨 batch 累计的已完成必做调用；
- 旧 plan 缺少新字段时保留 `legacy_any_call`，不静默改变 checkpoint 语义；
- 未完成项写入 Plan context 的 `remaining_required_tools`，Native/Pi 共用同一状态推进逻辑。

专项回归：60 passed。

## Post-fix 定向复测

为验证上述缺陷而只重跑 `solver_workflow_01`，两臂各 3 repeats。它只有 1 个 unique case，
只能做诊断，不能替代前述 4-case 正式配对，也不能据此宣称 Pi 优于 Native。

| 指标 | Native | Pi raw |
|---|---:|---:|
| task / strict | 1/3 | 2/3 |
| exact sequence | 1/3 | 3/3 |
| first tool | 2/3 | 3/3 |
| Planner fallback | 2/8 | 0/5 |
| mean tokens | 10,323 | 16,403 |
| mean latency | 162.8 s | 100.6 s |

Native 两条失败分别是 Planner timeout 后 context budget fail，以及模型生成非法端口参数并在
后续计划/目录状态中提前终止。Pi 三次均执行唯一正确工具序列；其中一条 raw failure 只是
grader 要求显式输出 `use_subvolume=false`，但 canonical tool schema 将它定义为可选默认值，
运行时也按 `false` 执行。评测器现已统一从 canonical schema 补默认参数，相关 26 个测试通过；
原始报告保持不变，不能把 post-hoc 解释偷偷写回 raw score。

报告目录：

`D:\cst_agent_rag_data\agent_eval\pi_harness_2_0\solver_post_contract_20260812`

- Native raw SHA-256：`5f3f5e673d2e5f7f3532b43dbe9865811d8d1f7a11d3193a2e1b9fff163d5cf4`
- Pi raw SHA-256：`d1f0064c323b2fba21f22241a2acf56a9b727b9356807e20edb13715c0c48d83`
- raw paired comparison SHA-256：`405378f1ff2491a8c519cb8b640e341a39450ced63a96b5ee5574f7ba000d88e`

## 扩展 12-case × 3-repeat 配对评测

扩展清单不是把 4 个旧 case 简单改写，而是从 frozen v2 中分层选择 12 个 unique cases，
覆盖 10 类场景：在线/离线只读、硬约束、零工具确认、记忆引导、多轮上下文、目标修订、
多工具材料查询、断连恢复、恢复后上下文，以及两类 Solver 工作流。每臂各 36 次真实
`gpt-5.6-terra` 调用；3 repeats 用于观察随机稳定性，统计推断单位仍是 12 个 case。

评测清单：`benchmarks/pi_harness_expanded_eval_v1.json`。报告是 developer-visible、
post-audit、filtered-debug 证据，不是 blinded/sealed 或 release-eligible 结果。

| 样本级指标 | Native | Pi | Pi − Native |
|---|---:|---:|---:|
| task success | 31/36 = 86.1% | 33/36 = 91.7% | +5.6 pp |
| strict lexical success | 24/36 = 66.7% | 27/36 = 75.0% | +8.3 pp |
| exact tool sequence | 32/36 = 88.9% | 34/36 = 94.4% | +5.6 pp |
| first-tool accuracy | 33/36 = 91.7% | 35/36 = 97.2% | +5.6 pp |
| injected recovery action | 6/6 | 6/6 | 0 |
| invalid call rate | 2/53 = 3.8% | 1/56 = 1.8% | -2.0 pp |
| mean total tokens | 7,941 | 8,017 | +1.0% |
| mean latency | 35.9 s | 34.4 s | -4.3% |
| p95 latency | 83.9 s | 62.9 s | -25.0% |

Latency 仅描述本次运行：两臂没有随机交错，timeout/retry 也落在不同样本，不能据此宣称
Pi 稳定更快。平均 token 基本持平。`recovery=6/6` 只说明注入失败后的恢复动作和重试成功；
两臂的 `disconnect_recovery_02::repeat_01` 都因参数 oracle 不匹配而最终 task=false，不能写成
“恢复后任务 100% 成功”。

| 独立 case endpoint | Native | Pi | 配对结果 |
|---|---:|---:|---|
| task majority | 10/12 | 12/12 | Pi-only 2，Native-only 0，`p=0.5` |
| task all-repeats | 9/12 | 9/12 | 一胜一负，`p=1.0` |
| strict majority | 7/12 | 10/12 | Pi-only 3，Native-only 0，`p=0.25` |
| strict all-repeats | 5/12 | 5/12 | 一胜一负，`p=1.0` |

Task-majority 的两个 Pi-only case 是 `solver_workflow_01` 和 `solver_workflow_02`。这表明
Pi 在本批复杂多步工具流上更容易完成，但 12 个独立样本不足以证明普遍优势；all-repeats
持平进一步说明随机稳定性仍未改善。

### 失败归因

- **共享参数 oracle**：`disconnect_recovery_02::repeat_01` 两臂都完成重连与重试，但
  `execute_vba_script` 参数不满足 case oracle；不是 Pi/Native loop 差异；
- **共享 Planner/provider 波动**：Native 的 Solver 样本出现额外 `set_units`；两臂各有一次
  terminal `APITimeoutError`。没有保存到 HTTP 502/503 证据；
- **Native loop**：`solver_workflow_02::repeat_02` 的 Plan 正确，但执行首个 `create_brick`
  后提前结束；
- **Pi loop**：`multi_tool_materials_01::repeat_03` 在 respond 阶段额外调用
  `recall_tool_result`，违反动态 allowlist 与 exact sequence；
- **Grader 边界**：task-pass 但 strict-fail 的多数样本实际表达了“已连接/在线”或
  “恢复连接并自动重试成功”，只是 case-authored lexical alternatives 没覆盖这些中文词形。
  所以 strict 是词面事实召回，不是已校准的语义 groundedness，`claim_precision=null`。

两臂各有 1/36 terminal provider timeout。端到端 endpoint 保留这些失败；另行排除 terminal
provider failure 后，agent-eligible task 为 Native `31/35=88.6%`、Pi `33/35=94.3%`，
strict 为 `24/35=68.6%`、`27/35=77.1%`。这组 conditioned 指标只用于归因，不能替换
端到端结果。

报告目录：

`D:\cst_agent_rag_data\agent_eval\pi_harness_2_0\expanded_12case_repeat3_20260812`

- Native SHA-256：`2e95fb86bea790e48adc1477abbd44e947970f4db5b6063daec3397bbabd6f0c`
- Pi SHA-256：`e15ed76e227c2184b220b24eb6984e4ba03adc95acafae8a67b5a234cd193741`
- paired comparison SHA-256：`caae8af331acdef74ffd8a5ef63700863dd79399a60cddc80b5a9422a799813a`

比较器校验两臂 dataset/manifest/model/provider/code revision、case/repeat/sample/seed 对齐，
并绑定两份源报告 SHA。provider sampling 没有 seed，因此本地 seed 只保证 case 注入条件，
不保证模型生成完全相同。

## 已知限制

- 最新扩展集有 12 个 unique cases，仍不足以支持小效应统计推断；早期正式集只有 4 个，
  post-fix 定向诊断只有 1 个；
- frozen v2 是 developer-visible、且用过 v1 模型输出修订 oracle，不是 unseen generalization；
- provider sampling 没有 seed；相同本地 seed 不等于相同生成；
- Native/Pi 共用 Python Planner，Planner timeout 会同时污染两臂，不能归因给 Pi；
- 官方文档 RAG 在本次 Harness 评测中出现离线 cache warning，但两臂环境一致，RAG 不是本次 endpoint；
- 扩展集的 Pi 平均 token 仅高约 1%；latency 因运行未随机交错，只能作为描述统计；
- strict grader 依赖 case-authored lexical alternatives，尚未用独立人工语义标注校准。

## 主张边界

可以主张：

> 我把 Executor 抽成 Native/Pi 可替换 Harness。Pi 只负责模型 turn、上下文格式和顺序
> Tool Loop；Python Host 继续掌握 Session、动态权限、Recovery、Trace、solver safety
> 和 CST COM。我做了版本化协议、长驻 Sidecar、状态隔离、事件流、steering/follow-up，
> 再用同模型、同 frozen Fake-CST 做 SHA-bound 配对评测。扩展到 12 类独立任务、每臂
> 36 次真实调用后，Pi 的 case-majority task 是 12/12，Native 是 10/12，优势集中在复杂
> Solver 工具流；但 all-repeats 都是 9/12，McNemar p=0.5，不能说显著更强。它当前最确定
> 的价值仍是把执行内核变成可替换、可观测、可恢复的 Harness，同时评测能把 Planner、
> provider、loop、参数 oracle 和 grader 失败分开归因。

不能主张：

- “Pi 显著提高成功率或降低延迟”；
- “36 repeats 是 36 个独立任务”；
- “strict 指标等价于经过人工校准的语义 groundedness”；
- “1-case post-fix 证明 Pi 优于 Native”；
- “Pi 接管了整个 Agent”；
- “Pi 自带上下文、权限、Recovery 和 Trace，因此 Python 控制面可以删除”；
- “developer-visible frozen v2 是 blinded/sealed 测试”。
