# ADR-011：raw VBA 的人工审批——授权票据绑定参数、单次消费

- 状态：生效
- 日期：2026-08

## 背景

工具分级的依据不是「看起来危不危险」，而是**这一次调用能绕过什么**。

typed 建模工具、solver 和结果读取都跑不出两层约束：canonical JSON Schema 和 CST controller 的护栏。参数是什么类型、取值在不在范围内、动的是哪个对象，都是被框住的。

`execute_vba_script` 不是。它把一段任意 VBA 交给 CST 执行，能绕过 canonical schema，也能绕过 controller 的全部保护。**唯一能越过类型契约的工具，就是唯一需要人批的工具**——`APPROVAL_REQUIRED_TOOLS` 因此被刻意保持成单元素集合。

难点在于「批准了什么」这件事本身。一个只绑定工具名的授权是没有意义的：模型可以在拿到批准后换一段 VBA 再调一次，人批的是名字，执行的是另一段代码。

## 备选方案

1. **一律不许 raw VBA。** 代价：CST 有大量没有 typed 封装的操作（部分远场后处理路径就是），砍掉等于砍掉开放任务的兜底能力。
2. **会话级开关：批一次，本会话内 raw VBA 全部放行。** 代价：授权粒度是「一段时间」而不是「一次调用」。人看到的是第一段 VBA，之后执行的是什么完全没有约束。
3. **按调用批准，授权与参数绑定、单次消费（采纳）。**

## 决策

### 授权票据绑定四元组

授权（grant）绑定 `(tool_name, 规范化参数的 SHA-256, actor, expiry)`。实现见 `agent/tool_approval.py::canonical_arguments_sha256()`：

```python
json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True,
           separators=(",", ":"), allow_nan=False)
```

- 先做 JSON Schema 规范化（补可选默认值）再算哈希——省略一个参数和显式传它的默认值，在校验、哈希、评测每一层都是同一次调用；
- `sort_keys=True`，所以 object key 顺序不影响哈希；数组顺序**有意保留**，因为它是语义的一部分；
- `allow_nan=False`，拒绝非标准 JSON 数值进入哈希输入。

**参数改一个字节就是一个新请求。** 这是这条设计的全部意义。

### 单次消费

`ToolApprovalStore.consume()` 命中后立即写 `consumed_at`，同一张票据不可能被用第二次。因此：

- CST 侧执行失败或抛错，**必须重新批准**——票据在 dispatch 前就已消费，失败不退还；
- 同参数重放需要新的批准，不存在「批过一次就一直有效」。

请求 TTL 与授权 TTL 上限都是 600 秒（`APPROVAL_REQUEST_TTL_SECONDS` / `MAX_APPROVAL_GRANT_TTL_SECONDS`），签发默认 120 秒。

### 门的位置与顺序

`tool_runtime._tool_approval_rejection` 在 **白名单 → schema 规范化/校验 → 审批门 → 分发** 这条流水线的第三格，对每一次调用生效：

- Session 上没有审批存储时返回 `approval_unavailable` 并拒绝，不退化成放行；
- 先尝试 `consume`，消费不到才创建 request 并返回 `approval_required`；
- 相同 `(tool, hash, actor)` 的未过期 pending request 会被复用，避免刷屏。

批准 API 使用**服务端保存的精确参数**（客户端不回传），签发前重跑一遍无副作用的 `preflight_tool_call`（白名单 + schema），防止计划或 schema 已经变化时发出 stale grant。

Native 和 Pi 两种执行引擎都只调用 Python Host 的 `execute_tool`；恢复路径修完参数后同样重新过门。

### 生命周期与可见性

- `clear session` 与 `new/open/close project` 都调用 `ToolApprovalStore.clear()`，撤销全部 pending request 和 active grant——审批能力不跨会话、不跨工程复用；
- UI 只拿到最多 500 字符的参数预览（`_arguments_preview`），完整参数留在服务端；
- request / grant 各有 50 条保留上限。

## 后果与代价

- 未批准时 raw VBA 的 dispatch 数为零，这条由独立 E2E 覆盖：从 `/api/chat` 发起、经 pending projection 与 approve API 执行，检查未批准零 dispatch、参数 hash 绑定、pending 不暴露完整参数、授权单次消费、同参重放与改参均需重新审批。
- **审批挂起既不是失败也不是完成**：Plan 停在当前步保持 `in_progress`，只有真正执行成功的工具计入完成度（见 [ADR-010](adr-010-plan-step-tool-allowlist.md) 的完成契约）。
- **边界必须说清楚**：批准端点是在同一个 HTTP 请求里**按绑定参数精确重放那一次工具调用**，它**不恢复原来的模型 turn**，也不把成功 observation 注回原回复。对外只能说「参数绑定审批与精确执行闭环」，**不能说「暂停/恢复推理」**。
- **身份边界**：当前是本机单用户桌面应用，`actor` 固定为 `local-desktop-user`（`_approval_actor` 刻意不从客户端 metadata 取值，不把任意请求元数据当可信身份）。API 没有多租户认证，**不得对外声称 RBAC、远程用户身份或企业级授权**。若将来开放远程访问，必须先引入可信认证上下文（loopback/local token 或等价边界）再扩展 actor 模型。
- 代价：每次 raw VBA 都要人点一次，批量场景体验差。这是有意的——把它做顺手就等于把门做没了。
