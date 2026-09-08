# ADR-001：评估 LangGraph 后撤销，回归自研 Planning→Execution 控制流

- 状态：已撤销框架，决策生效
- 引入：2026-03-23
- 接入生产路径：2026-05-30
- 撤销：2026-07-22
- 在树里存续约 4 个月、136 个 commit，其中约 2 个月在生产路径上

> 这个公开仓是 2026-08-30 发布的快照，历史从那天开始，所以上面这些日期和下文引用的删除前代码都来自开发仓，在这里检不到对应 commit。

## 背景

2026 年 3 月决定用 LangGraph StateGraph 替换 `runtime.py` 里的手写 tool loop。动机不是单一的，照实记录，因为后面的撤销判断只有放在这些动机上才成立：

- **想让这个项目里有一个主流 Agent 框架。** 这是叙事层面的动机，与工程需要无关。
- **手写循环当时确实难受。** 状态散在多处，加一条 replan 分支要动好几个地方。
- **外部参照。** 当时同类 Agent 项目普遍在用图编排，倾向于对齐。
- **想要 checkpointer / 持久化 / 并行节点这些具体能力。** 尤其是跨 invoke 恢复对话状态。

引入之后并没有立刻上生产：到 2026-05-30 才把 `/api/chat` 真正路由到图上，同时修掉了编排层与执行层双重发起 planner 调用的问题（`executor_node` 因此要传 `skip_plan=True`）。

## 备选方案

1. **保持 `runtime.py` 里的自研 tool loop。** 痛点是真的，但改造成本局限在一个文件内。
2. **采用 LangGraph StateGraph 承担 planner / executor / reflection 路由。** 拿到成熟的状态机、条件路由和持久化设施，代价是多一层调试间接层，且整个控制流要迁就框架的状态模型。

## 决策

2026-07-22 撤销 LangGraph，回归自研控制流。

先看规模：整张图只有 **3 个节点 + 1 条条件边**（`planner → executor →` 条件边 `{replan, reflect, end}`，`reflection → END`）。checkpointer、并行节点、子图这些不可替代能力，一个都没有真正用上。付出的是一整层间接性，换回来的是一个可以用三个函数调用写完的拓扑。

三条具体理由，都能在删除前的 `agent/graph.py`、`agent/nodes.py`、`agent/planner.py` 里核对：

### 1. 图节点退化成薄包装

`executor_node` 唯一的实际工作是一行：

```python
response_text = agent.chat(user_message, images=images, skip_plan=True)
```

其余全是 state 字典的搬运。它自己的 docstring 写着「内部处理消息组装、tool loop、plan 更新、trace 等全部逻辑」——等于承认真正的闭环控制流始终在 `runtime.py` / `optimizer.py` 里，节点只是把它包了一层。

### 2. COM 句柄不可序列化，checkpointer 用不了

agent 实例持有 CST 的 COM 句柄和锁，无法进入 checkpoint 序列化。实际做法是把 agent 通过 `config["configurable"]["agent_ref"]` 传进节点，显式绕开 checkpoint。

也就是说：**引入这个框架的动机之一是 checkpointer，而这个项目的领域约束恰好让 checkpointer 无法启用。** 没有持久化状态，`thread_id` 和跨 invoke 恢复都无从谈起。

### 3. replan 条件边在生产路径不可达

这条不需要靠回忆，是可以推的。

`nodes.py` 里放行 replan 的条件：

```python
needs_replan = plan_needs_replan and not had_tool_failure
```

而 `planner.py` 里 `needs_replan = True` 的**唯一**置位点（`update_plan_after_turn` 与 `evaluate_replan_or_stop` 两处都是）：

```python
if had_tool_failure:
    current.needs_replan = True
```

即 `plan_needs_replan` 为真 ⟺ `had_tool_failure` 为真。代入：

```
needs_replan = had_tool_failure AND NOT had_tool_failure  ≡  False
```

置位条件与放行条件互为补集，这条边恒为假。图上那条唯一的循环边——也是「用图」的主要理由——在生产路径上从来没有走到过。

## 后果与代价

- `agent/graph.py`、`agent/nodes.py`、`agent/state.py` 删除；`web/chat_routes.py` 的 `/api/chat` 与 `/api/chat/stream` 直接调用 `agent.chat()`。
- 原 nodes 的 reflection 逻辑由 `reflection.py` 的 `collect_recent_errors` + `run_reflection_for_agent` 承接。
- `pyproject.toml` 移除 `langgraph` / `langchain-core` 依赖。
- 放弃框架自带的持久化 checkpoint 与并行节点——理由 2 已经说明，这个项目本来也用不上。
- 代价：`tests/test_agent_nodes.py` 随 `nodes.py` 一起消失，但 `check.py` 的文件清单仍引用它，导致 smoke / core / all 三个门禁静默报错退出了一段时间。现在由 `test_check_script.py::test_check_script_referenced_test_files_exist` 兜住。
- 代价：步级单工具驱动仍是 future work。executor 当时以「整轮」为粒度委托 `agent.chat()`，撤销后这个粒度没有变。
- **撤销条件**：如果将来真的需要跨进程/跨会话恢复编排状态，或者出现可并行的执行分支，应重新评估图编排。前提是届时 COM 句柄的序列化问题已有解法——否则理由 2 依然成立。

## 这件事说明什么

引入框架前该问的是「现有代码加 20 行能解决吗」，以及「这个框架不可替代的能力，我这个项目会用到几个」。这次的答案分别是「能」和「零个」，但我是在框架进树 4 个月之后才把这两个问题问清楚的。
