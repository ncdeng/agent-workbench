# ADR-006：CST 超时/断连的限次恢复，以及为什么选「诚实降级」而不是重试

- 状态：生效
- 日期：2026-07-22

## 背景

CST Design Environment 不是被 Agent 拉起的子进程。它由 `connect_to_any_or_new` 连接到一个**独立的 OS 进程**，生命周期不归 Agent 管。

所有 COM/VBA 调用走独立 Python 子进程（Windows STA 线程亲和、CST Python 版本锁定、句柄随进程释放）。`subprocess.run` 超时会 SIGKILL 掉这个子进程——但被杀掉的只是「发指令的那一端」。CST DE 仍然活着，而且很可能仍卡在上一次 `run_solver` 里继续算。CST 侧没有可靠的跨进程 abort API：Python 这边没有任何手段真正中止 DE 内已经开始的求解。

于是超时分支面对一个事实：**Agent 不知道那次求解到底停没停，而且没有办法让它停。**

## 备选方案

1. **当成可重试错误，直接重发上一条命令。**
   代价：DE 可能还在算上一次。第二条命令要么排队阻塞，要么在一个状态不明的工程上叠加副作用。建模和求解不可逆，这是拿真实状态赌。

2. **报告「已中止」，然后重连。**
   代价：这是撒谎。杀掉 Python 子进程不等于杀掉 CST 的计算；对用户宣称已中止，会让人以为可以安全地改参数、重开工程。后面出现的任何异常都会被归因错。

3. **标记断连 + 诚实告知 + 限次恢复（采纳）。**
   代价：用户可能需要手动去 CST GUI 终止求解，Agent 帮不上忙。

## 决策

### 超时分支只做三件事（`cst/controller.py::CSTController._run_com_script`）

1. `self.connected = False`——强制下一条命令走 reconnect 路径；
2. 返回 dict 里带 `"timeout": True`，让调用方能分支判断，而不是把它混进通用失败；
3. message 明写「子进程已终止，但 CST Design Environment 可能仍在执行上一次求解；下次命令将尝试重连。如 CST GUI 卡死请手动终止求解。」

**不假装 abort 成功。** 这是诚实降级，不是真正的中止。

### 恢复是注册式、按故障事件限次的（`agent/failure_recovery.py`）

`FailureRecoveryEngine` 按注册顺序取第一个 predicate 命中、且预算未耗尽的动作。当前注册五个：

| 动作 | 触发 | max_attempts |
| --- | --- | --- |
| `reconnect_cst` | `ErrorType.CST_CONNECTION` | 2 |
| `timeout_wait_and_reconnect` | `ErrorType.CST_TIMEOUT` | 1 |
| `ensure_farfield_monitor` | 结果读取缺远场监视器 | 1 |
| `dry_run_fallback` | CST 离线 | 1 |
| `repair_missing_parameter` | 参数缺失/非法 | 1 |

`timeout_wait_and_reconnect` 本身**不重试求解**：它返回 `recovered=False`，只记录一条保守建议（`recommendation: reconnect_and_wait`）交给 planner。超时路径上真正可能恢复的只有重连，而重连成功也只代表通道恢复，不代表上一次求解的结果可信。

`reconnect_cst` 不从「返回了一个非空 dict」推断成功，而是复核控制器的连接状态（`is_connected()` 或 `connected` 且非 `offline_mode`）；只有确认连上才回填 `retry_tool` / `retry_arguments`。

### 预算按故障事件计，不按会话计

这是修过的一个语义缺陷。原实现计数器**永不重置**：会话内掉线两次（哪怕两次都成功恢复了）之后，第三次就直接「无恢复动作」——恢复能力被历史上的成功给耗尽了。

现在的语义是：恢复成功即重置该动作的计数器（`_reset_attempt`），连续失败仍被 `max_attempts` 有界拦住；plan 重建和 `clear_history` 也显式重置。预算限的是**一次故障事件**，不是会话寿命。

### 恢复不绕过安全门

恢复路径修完参数后重新过一遍 `白名单 → schema 规范化/校验 → 审批门`，和正常调用同一条流水线（见 [ADR-010](adr-010-plan-step-tool-allowlist.md)、[ADR-011](adr-011-parameter-bound-tool-approval.md)）。每次恢复的结果写进 tool event 和 trace，构成可审计链。

## 后果与代价

- 超时后 Agent 的能力边界是诚实的：能重连、能告知、能限次恢复，不能中止。GUI 卡死仍需用户手动终止求解。
- `result["timeout"]` 成了调用方必须处理的分支；漏判会把「状态不明」当成「普通失败」。
- 40-case 确定性消融里 `no-recovery` 臂是 32/40（严格词面 28/40），full 是 40/40（严格词面 36/40）——恢复层的贡献在这个 developer-visible 数据集上成立。数据绑定见 `benchmarks/agent_e2e_canonical.json`。
- **撤销条件**：若 CST Python API 将来暴露可靠的 `abort_solver()` 等价物，超时分支应改为真正调用它，本 ADR 的「诚实降级」前提随之失效。
