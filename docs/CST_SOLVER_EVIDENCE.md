# CST Solver Evidence

本页只登记可由 D 盘原始报告复算的 CST Agent 证据。它不把 History 接受、solver API 返回、结果非空和物理目标达成混为一谈。

## 跨结构真实闭环

最终聚合报告：

```text
D:\cst_agent_rag_data\agent_eval\cross_structure_solver_v1.json
SHA-256: ead9af1c2869a48c5f242225dd9885e2f7626b75778ef0573983017b40fe831c
```

- 2 个天线拓扑：dipole、rectangular patch；
- 3 个几何 variant：plate dipole、wire dipole、microstrip inset patch；
- 4 个 developer-selected synthetic cases；
- execution success：4/4；
- S11@f0 ≤ -10 dB：3/4；
- 每例都要求 canonical tool 成功、真实 CST solver、非空 typed S11、D 盘工程存在及 clean close；
- dipole 的 plate/wire 只是同一拓扑的 variant，不能说成 3 或 4 种天线拓扑。

该结果不是 blinded/representative benchmark，不支持泛化成功率主张。矩形贴片是一个 canonical composite tool，内部 Host 阶段不是多次模型 tool calling。

## 真实优化 before/after

报告：

```text
D:\cst_agent_rag_data\agent_eval\dipole_tuning\20260812T130106Z\report.json
SHA-256: 7f998bb5db3e8367bd0b55f582d0df5778fe454b8f0a148c6560129d9f6d76a4
```

真实负例来自 5.8 GHz plate dipole：baseline 已完成 solver 与 S11 读回，但 S11@5.8 = -9.104 dB，未达到 -10 dB；实测谐振为 5.42068 GHz。

Agent 使用一阶物理关系 `f_res proportional to 1/L` 生成单变量候选，把 `arm_length` 从 12.146763 mm 缩短到 11.352365 mm。执行仍通过 canonical Host runtime：

```text
build_dipole_fast
→ store_parameter(arm_length=11.352365)
→ run_solver
→ get_s_parameter
```

真实 CST 复算后：

- 谐振从 5.42068 GHz 移到 5.77564 GHz；
- 绝对频率误差从 0.37932 GHz 降到 0.02436 GHz，下降约 93.6%；
- S11@5.8 从 -9.104 dB 改善到 -15.769 dB；
- 冻结标准 `|f_res - 5.8| ≤ 0.05 GHz` 且 `S11@5.8 ≤ -10 dB`，由失败变为通过；
- 候选一次改善，未触发 rollback；代码已定义退化/失败时的真实写回、重求解、结果读回门槛。

准确表述是：Agent 负责需求解释、物理诊断、参数候选、工具编排、成功判定与回滚策略，CST solver 负责物理真值。不能说 LLM 替代了 CST 优化器，也不能凭单例说该缩放公式对所有结构泛化。

## CST Native Optimizer 对照

三臂聚合报告：

```text
D:\cst_agent_rag_data\agent_eval\dipole_optimization_comparison_v2.json
SHA-256: 881889ba7097caaffd8711304b4f8020a546a61a4b40fb19a734ccef1163508a
```

三臂使用同一份 SHA-bound 5.8 GHz plate-dipole baseline、同一 `arm_length` 搜索范围
10.9–12.2 mm，以及同一最终验收标准：

```text
|f_res - 5.8| <= 0.05 GHz
S11@5.8 <= -10 dB
```

- physics-guided：Agent 根据实测谐振用 `f_res proportional to 1/L` 提出一次候选；包含
  baseline 共 2 次物理求解，最终 `arm_length=11.352365 mm`、`S11@5.8=-15.769 dB`、
  谐振误差 0.02436 GHz，联合标准通过；
- plain CST Native Optimizer：将用户的 -10 dB 阈值直接作为 Nelder–Mead goal；读取 baseline
  后 1 次新求解即满足该单点 goal，最终 `arm_length=11.576719 mm`、`S11@5.8=-14.128 dB`，
  但谐振误差仍为 0.12876 GHz，联合标准失败；
- Agent-configured CST Native Optimizer：Agent 把内部搜索 surrogate 收紧到 -15.5 dB，最终验收
  仍保持原标准。CST 用满 4 个 function evaluations（2 次新 solver、2 次 result reload），比较
  11.5767 与 11.0067 mm 后仍选择前者，联合标准失败。

这个结果不证明 Agent 普遍优于 CST Optimizer。它证明的是职责分离：Agent 必须把用户物理意图
转成可验收的 objective、选择合适的先验/搜索策略并检查终点；CST Native Optimizer 负责成熟的
数值搜索，CST solver 始终负责物理真值。`-15.5 dB` surrogate 是看过开发结果后设置的，不是
blinded protocol；三臂 wall time 也只作描述统计。

## Mesh study 的负结果

零模型复核报告：

```text
D:\cst_agent_rag_data\agent_eval\dipole_mesh\20260812T130651Z\report_revalidated_v2.json
SHA-256: a450abb51c279017123d9f7e14808ea8cb3e21d84ac19dcaafa11c56ac2d67ea
```

10/15/20 lines-per-wavelength 三个独立工程均完成建模、网格 History、solver、typed S11 与关闭，execution 为 3/3。三条完整 1001 点曲线却 byte-identical；当前工具没有读回实际 mesh cell count 或其他 realized mesh signature。因此：

- tolerance 数值表面满足；
- mesh convergence claim 不具备资格；
- 状态必须是 `inconclusive`，不能写成“已证明网格收敛”。

现已增加 typed `get_mesh_signature`，但本机 CST 2025 Online Help 与已审计接口中仍没有定位到可验证的 actual cell-count/statistics getter。该工具因此 fail closed 返回 `unsupported_mesh_signature`，不会用 LinesPerWavelength 配置值冒充 realized signature。下一步必须先取得官方 getter 依据或可复验的真实探针输出，再让该工具返回成功并重新评估 medium→fine 差值。

## 主张边界

可以主张：项目不是“自然语言生成一段 VBA”，而是 CST Agent 的多层闭环。Harness 负责模型循环和上下文，Python canonical runtime 负责 typed tool、权限、安全恢复与 Trace，CST 负责真实建模/求解，结果层用不同 endpoint 区分执行成功与物理成功。真实负例会进入诊断与调参闭环；候选必须重新求解，退化时必须真实 rollback。

不能主张：4 个 case 代表普遍成功率、mesh 已收敛、LLM 比 CST 原生 optimizer 更强、或矩形贴片内部每个 Host stage 都是模型单独选择的工具。Native Optimizer 单例对照只能讲 objective 设计与职责边界，不能讲算法优越性。
