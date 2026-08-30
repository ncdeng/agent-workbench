"""消息上下文构建器 — 将领域相关的 working_messages 组装逻辑从编排层分离。"""

from __future__ import annotations

from typing import Any, Dict, List

from cst_agent_workbench.results.summary import build_s11_snapshot_text, summarize_s11_result


_STATUS_BROADCAST_PATTERNS = [
    "请查看右侧", "请查看结果", "已完成", "仿真已完成",
    "已读取", "已连接", "无法读取", "无法连接",
    "interactive mode", "交互模式",
]

_NOISE_PATTERNS = [
    "You are working in interactive mode",
    "你正在使用交互模式",
    "VBA 已在 CST 中执行",
    "已连接到 CST 工程",
    "求解器已完成",
    "求解完成并触发结果模板",
]


def _is_noise_message(msg: Dict) -> bool:
    """检测低价值噪声消息（如模式提示、空内容），不应送入模型上下文。"""
    content = msg.get("content", "")
    if not content:
        return False
    if isinstance(content, list):
        text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        content = " ".join(text_parts)
    for pattern in _NOISE_PATTERNS:
        if pattern in content:
            return True
    return False


def _is_meaningful_analysis(content: str) -> bool:
    """判断 assistant 文本是否包含有分析价值的内容（参数调整/频率/S11 分析）。
    用于优化上下文中筛选“上轮分析摘要”。"""
    if not content or not isinstance(content, str):
        return False
    if len(content) < 50:
        return False
    content_lower = content.lower()
    for pat in _STATUS_BROADCAST_PATTERNS:
        if pat.lower() in content_lower and len(content) < 120:
            return False
    analysis_keywords = ["S11", "dB", "频率", "参数", "patch", "feed",
                         "偏高", "偏低", "增大", "减小", "调整",
                         "谐振", "阻抗", "GHz", "匹配"]
    for kw in analysis_keywords:
        if kw in content:
            return True
    return len(content) >= 150


def _extract_last_meaningful_assistant_summary(history, max_chars: int = 300) -> str:
    """从历史中倒序查找最近一条有分析价值的 assistant 文本，截断后返回。
    跳过噪声消息、带 tool_calls 的消息、纯状态播报。"""
    for msg in reversed(history):
        if msg.get("role") != "assistant":
            continue
        if msg.get("tool_calls"):
            continue
        if _is_noise_message(msg):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        if not _is_meaningful_analysis(content):
            continue
        if len(content) > max_chars:
            return "..." + content[-max_chars:]
        return content
    return ""


def build_optimization_context(
    *,
    user_message: str,
    feed_strategy: str = "microstrip",
    session: Any,
    opt_state: Any,
    last_results: Dict[str, Any],
    history: List[Dict[str, Any]],
    system_prompt: str,
) -> List[Dict[str, Any]]:
    """构建优化模式下的精简上下文，仅包含必要的结构化信息。"""
    from cst_agent_workbench.cst.primitives import get_parameters

    opt = opt_state
    params = get_parameters()
    memory = session.memory

    context_parts = []
    if memory.conversation.user_goal:
        context_parts.append(f"用户目标:\n{memory.conversation.user_goal}")
    if memory.conversation.recent_summary:
        context_parts.append(f"最近会话摘要:\n{memory.conversation.recent_summary}")
    if memory.workspace.project_path:
        context_parts.append(f"当前工程: {memory.workspace.project_path}")

    workspace_result = memory.workspace.last_results_summary or {}
    if workspace_result.get("available"):
        result_lines = [
            f"- 类型: {workspace_result.get('type') or 'unknown'}",
            f"- 项目: {workspace_result.get('item', '')}",
        ]
        if workspace_result.get("target_freq_ghz") is not None:
            result_lines.append(f"- 目标频率: {workspace_result['target_freq_ghz']:.4f} GHz")
        if workspace_result.get("target_s11_db") is not None:
            result_lines.append(f"- 目标频率 S11: {workspace_result['target_s11_db']:.2f} dB")
        if workspace_result.get("min_s11_db") is not None and workspace_result.get("min_freq_ghz") is not None:
            result_lines.append(f"- 最小 S11: {workspace_result['min_s11_db']:.2f} dB @ {workspace_result['min_freq_ghz']:.4f} GHz")
        if workspace_result.get("message"):
            result_lines.append(f"- 说明: {workspace_result['message']}")
        context_parts.append("最新结果摘要:\n" + "\n".join(result_lines))

    if params:
        important_keys = ["f0", "patch_L", "patch_W", "feed_W", "feed_L", "inset_depth", "substrate_h", "copper_t"]
        ordered_items = [(k, params[k]) for k in important_keys if k in params]
        ordered_items.extend((k, v) for k, v in params.items() if k not in dict(ordered_items))
        param_lines = [f"  {k}={v}" for k, v in ordered_items[:12]]
        context_parts.append("当前参数快照:\n" + "\n".join(param_lines))

    if opt.history:
        mode_label = "目标频率处 S11" if opt.target_mode == "at_f0" else "最小 S11"
        context_parts.append(f"优化进度: 已完成 {opt.round} 轮")
        if opt.baseline_metric_value is not None:
            context_parts.append(f"baseline {mode_label}: {opt.baseline_metric_value:.2f} dB")
        if opt.best_metric_value is not None:
            context_parts.append(f"best-so-far {mode_label}: {opt.best_metric_value:.2f} dB (第{opt.best_round}轮)")
        context_parts.append(f"stagnation: {opt.stagnation_count}")
        recent = opt.history[-3:]
        recent_lines = []
        for rec in recent:
            parts = [f"第{rec['round']}轮"]
            if rec.get("strategy"):
                parts.append(f"策略={rec['strategy']}")
            if rec.get("changed_params"):
                parts.append(f"改动={rec['changed_params']}")
            if rec.get("proposal_reason"):
                parts.append(f"说明={rec['proposal_reason']}")
            if rec.get("metric_value") is not None:
                parts.append(f"metric={rec['metric_value']:.2f}dB")
            if rec.get("min_s11") is not None and rec.get("min_freq") is not None:
                parts.append(f"min={rec['min_s11']:.2f}dB@{rec['min_freq']:.3f}GHz")
            if rec.get("at_f0_s11") is not None:
                parts.append(f"target={rec['at_f0_s11']:.2f}dB")
            parts.append("PASS" if rec.get("met") else "FAIL")
            recent_lines.append(" | ".join(parts))
        context_parts.append("最近3轮摘要:\n" + "\n".join(recent_lines))

    decision_memory = memory.decisions
    if decision_memory.best_so_far:
        best = decision_memory.best_so_far
        best_lines = [f"- 轮次: {best.get('round', 0)}"]
        if best.get("metric_value") is not None:
            best_lines.append(f"- metric: {best['metric_value']:.2f} dB")
        if best.get("changed_params"):
            best_lines.append(f"- 参数变化: {best['changed_params']}")
        context_parts.append("结构化 best-so-far:\n" + "\n".join(best_lines))
    if decision_memory.recent_strategies:
        strategy_lines = []
        for item in decision_memory.recent_strategies[-3:]:
            parts = [f"第{item.get('round', '?')}轮"]
            if item.get("strategy"):
                parts.append(f"策略={item['strategy']}")
            if item.get("changed_params"):
                parts.append(f"改动={item['changed_params']}")
            if item.get("proposal_reason"):
                parts.append(f"原因={item['proposal_reason']}")
            strategy_lines.append(" | ".join(parts))
        if strategy_lines:
            context_parts.append("结构化策略记忆:\n" + "\n".join(strategy_lines))
    if decision_memory.failure_reasons:
        failure_lines = [f"- {item}" for item in decision_memory.failure_reasons[-3:]]
        context_parts.append("最近失败原因:\n" + "\n".join(failure_lines))
    if decision_memory.rollback_points:
        rollback = decision_memory.rollback_points[-1]
        context_parts.append(
            f"最近回退记录:\n- round={rollback.get('round', '?')}\n- reason={rollback.get('reason', '')}"
        )

    if last_results:
        last_summary = summarize_s11_result(last_results, opt.target_freq or 0.0)
        if last_summary.success:
            context_parts.append("当前S11摘要:\n" + build_s11_snapshot_text(last_summary))

    last_analysis = _extract_last_meaningful_assistant_summary(history)
    if last_analysis:
        context_parts.append(f"最近一次有价值分析:\n{last_analysis}")

    system_context = "\n\n".join(context_parts)
    short_guidance = (
        "你在执行多轮优化。只做一轮分析与单参数调整；优先保持当前建模拓扑；"
        "频偏先调 patch_L，匹配不足优先调馈电相关参数；避免重复输出完整建模规则。"
    )
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": short_guidance},
    ]
    if feed_strategy == "probe":
        messages.append({
            "role": "system",
            "content": "[贴片馈电策略]\n保持 probe-fed/coax-fed 结构，不要退化成理想竖直离散端口。",
        })
    else:
        messages.append({
            "role": "system",
            "content": "[贴片馈电策略]\n保持 microstrip line feed 结构，不要退化成地到贴片的理想竖直离散端口。",
        })
    messages.extend([
        {"role": "system", "content": f"[优化上下文]\n{system_context}"},
        {"role": "user", "content": user_message},
    ])
    return messages
