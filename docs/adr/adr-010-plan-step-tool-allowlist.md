# ADR-010：工具白名单按计划步动态开放，权限与完成条件分离

- 状态：生效（含一处待确认，见文末）
- 日期：2026-08

## 背景

42 个 canonical 工具里，有一部分能直接改动一个不可逆的真实工程：建几何、改边界、跑求解、导出文件。把全部 42 个工具在每一轮都摊给模型，等于让「这一步该不该动几何」完全由模型自觉。

同时有一个真实踩过的缺陷：旧的 `update_plan_after_turn` 在一个多工具步里，**只要发生过任意一次 tool call 就把整步标记完成**，动态工具目录随即切到下一步——那些还没执行的 sibling tools 就这么消失了。模型「调用过一次工具」被当成了「这一步做完了」。

这两件事看起来是一件事（都关于 `allowed_tools`），实际上是两件：**能不能调**和**调完没有**。

## 备选方案

1. **全量工具目录 + 提示词约束。** 靠 system prompt 告诉模型这一步只该用哪些。代价：约束不可执行，越权只能事后从 trace 里发现。
2. **静态角色白名单。** 按工具类别固定分组，不随 plan 变。代价：粒度太粗——同一个 `tool` 步里，建模和 `run_solver` 的风险完全不同。
3. **按计划步动态开放 + 完成契约单列（采纳）。**

## 决策

### 权限：白名单由当前活动步解析，执行前逐次检查

`runtime.allowed_tool_names_for_active_step(session)` 是唯一的权限解析入口：

- 按 `step["kind"]` 从 `_STEP_ALLOWED_TOOL_NAMES` 取基集（`analyze` / `tool` / `geometry` / `read_result` / `optimize` / `judge` / `respond` …）；
- **步 kind 不认识时返回空集**——空集意味着一个工具都不放行，不是「不限制」；
- planner 显式给了 `allowed_tools` / `selected_tools` 时，取它与基集的**交集**，模型不能靠自己声明来扩权；
- 只有 `analyze` 步在 planner 未显式声明时，按 plan 的 intent kind 做一次受控扩展。

执行侧在 `tool_runtime._tool_allowlist_rejection` 落地，并且**检查发生在每次调用前**，不是每步一次：

- 解析过程抛异常时**拒绝执行**（`error_type: "allowlist_unavailable"`，并写入 `observability_degradations`，`fallback="fail_closed"`）——权限判断本身失败时不放行；
- 工具不在集合内时拒绝（`error_type: "tool_not_allowed"`），返回值里带上当前允许集合，便于模型自我纠正。

`preflight_tool_call` 把顺序固定为 **白名单 → schema 规范化/校验**，审批 HTTP 端点在签发授权前复用同一个 preflight，堵住「计划或 schema 已经变了、授权却还按旧状态发出去」的 stale grant 缺口（见 [ADR-011](adr-011-parameter-bound-tool-approval.md)）。`do_execute_tool` 在分发前再查一次白名单，属于有意的纵深防御。

Native 和 Pi 两种执行引擎都只经由 Python Host 的 `execute_tool`，共用同一份判断；恢复路径修完参数后同样重新过门。

### 完成条件：`required_tools` 是 `allowed_tools` 的子集，另算

- `allowed_tools` **只是权限白名单**；
- planner 另输出子集 `required_tools` 作为 all-of 完成契约，`respond` 步的 `required_tools` 必须为空；
- 运行时跨 batch 累计 `completed_tools`，未完成项通过 `remaining_required_tools` 注回 Plan 上下文；
- **禁止从 `allowed_tools` 反推 required**：历史真实 planner 输出里，多工具白名单既可能表示全部必做，也可能包含 `recall_tool_result` 这类条件/候选工具。旧 plan 缺字段时保留 `completion_contract = "legacy_any_call"`，不静默改变已有 checkpoint 的语义。

## 后果与代价

- 越权调用在执行前被拒绝并留痕，而不是靠事后审计。
- 一步不再因为「随便发生过一次调用」就算完；配套地，只有**真正执行成功**的工具计入完成度（审批挂起既不算失败也不算完成）。
- 代价：planner 输出质量直接决定可用工具面。planner 少给一个必要工具，这一步就会卡在「不在允许列表中」，需要靠 replan 信号绕回去。
- 代价：`_STEP_ALLOWED_TOOL_NAMES` 是硬编码的步类型→工具映射，新增工具必须同步维护这张表，漏加的表现是「工具存在但永远调不到」。
- 40-case 确定性消融的 grader 会显式检查空白名单与零工具上限这两种边界，防止「白名单形同虚设」被测试放过。

## 待确认（TODO）

本文**没有**主张「无 plan 即无授权」。`allowed_tool_names_for_active_step` 在没有活动 plan 时的返回值，以及 `tool_runtime.py` 里 `if allowed is None` 这一分支的实际放行语义，需要作者本人读代码确认后再决定怎么写：

- 无 plan（含 `skip_plan=True` 路径）时，白名单是被显式初始化为空集、每次执行前检查，还是根本没走到检查那一步？

结论确定前，README / PROJECT_STORY 不应出现「无计划即无授权」这类表述。
