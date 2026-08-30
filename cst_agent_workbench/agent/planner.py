from __future__ import annotations

import uuid
from typing import Any, Dict, List

from cst_agent_workbench.agent.plan_models import Plan, PlanIntent, PlanStep, plan_from_dict


def infer_plan_intent_kind(*, optimization_state: Any = None, optimization_mode: bool = False, user_message: str = "") -> str:
    text = str(user_message or "")
    opt = optimization_state
    if optimization_mode and bool(getattr(opt, "active", False)):
        return "continuous_optimization"
    if optimization_mode or any(keyword in text for keyword in ["优化一轮", "run one optimization round", "Automatic optimization round"]):
        return "optimization_round"
    if any(keyword in text.lower() for keyword in ["读取", "执行", "run", "read", "solve"]):
        return "direct_action"
    return "chat_task"


def default_stop_condition(intent_kind: str, optimization_state: Any = None) -> str:
    if intent_kind == "continuous_optimization":
        return "达标、停滞或达到最大轮数时停止"
    if intent_kind == "optimization_round":
        return "完成单轮分析、调参与结果判定后停止"
    if intent_kind == "direct_action":
        return "完成当前动作并产出结果后停止"
    return "完成分析/工具调用并给出答复后停止"


def default_stop_conditions(intent_kind: str) -> List[str]:
    if intent_kind == "continuous_optimization":
        return ["target_met", "stagnation_limit", "max_round_limit"]
    if intent_kind == "optimization_round":
        return ["task_complete", "target_met", "stagnation_limit", "max_round_limit"]
    return ["task_complete"]


def build_default_steps(intent_kind: str) -> List[PlanStep]:
    if intent_kind == "continuous_optimization":
        return [
            PlanStep(step_id="step_analyze", kind="analyze", title="分析当前结果", expected_output="识别当前指标、best-so-far 与停滞状态"),
            PlanStep(step_id="step_optimize", kind="optimize", title="执行一轮优化", expected_output="完成单参数调整与求解"),
            PlanStep(step_id="step_judge", kind="judge", title="判断继续、停止或重规划", expected_output="更新 stop / replan 决策"),
            PlanStep(step_id="step_respond", kind="respond", title="汇总优化进展", expected_output="向 UI/用户返回结构化状态"),
        ]
    if intent_kind == "optimization_round":
        return [
            PlanStep(step_id="step_analyze", kind="analyze", title="分析当前结果", expected_output="识别当前指标与关键问题"),
            PlanStep(step_id="step_optimize", kind="optimize", title="执行一轮优化", expected_output="完成单参数调整与求解"),
            PlanStep(step_id="step_judge", kind="judge", title="评估本轮结果", expected_output="判断是否达标或需要重规划"),
            PlanStep(step_id="step_respond", kind="respond", title="返回本轮结论", expected_output="输出当前轮状态与建议"),
        ]
    if intent_kind == "direct_action":
        return [
            PlanStep(step_id="step_analyze", kind="analyze", title="分析请求", expected_output="识别目标动作与约束"),
            PlanStep(step_id="step_tool", kind="tool", title="执行工具动作", expected_output="获得结构化结果或错误"),
            PlanStep(step_id="step_read_result", kind="read_result", title="读取关键结果", expected_output="提炼可回答的观察"),
            PlanStep(step_id="step_respond", kind="respond", title="给出答复", expected_output="返回结论与下一步"),
        ]
    return [
        PlanStep(step_id="step_analyze", kind="analyze", title="分析用户目标", expected_output="识别当前目标与上下文"),
        PlanStep(step_id="step_tool", kind="tool", title="必要时调用工具", expected_output="获取支持结论的证据"),
        PlanStep(step_id="step_respond", kind="respond", title="组织最终答复", expected_output="产出清晰结论"),
    ]


def coerce_plan(plan: Dict[str, Any] | Plan | None) -> Plan | None:
    if plan is None:
        return None
    if isinstance(plan, Plan):
        return plan
    return plan_from_dict(plan)


def summarize_plan(plan: Dict[str, Any] | Plan | None) -> Dict[str, Any]:
    current = coerce_plan(plan)
    if current is None:
        return {
            "intent_kind": "",
            "step_count": 0,
            "completed_step_count": 0,
            "replan_count": 0,
            "stop_reason": "",
            "active_step_title": "",
            "active_step_kind": "",
            "status": "idle",
            "needs_replan": False,
            "final_action": "",
            "current_step_id": "",
            "stop_condition": "",
            "constraints": [],
            "stop_conditions": [],
            "required_tools": [],
            "completed_tools": [],
            "remaining_required_tools": [],
            "completion_contract": "",
        }
    active_step = next((step for step in current.steps if step.step_id == current.current_step_id), None)
    return {
        "intent_kind": current.intent.kind,
        "step_count": len(current.steps),
        "completed_step_count": sum(1 for step in current.steps if step.status == "completed"),
        "replan_count": current.replan_count,
        "stop_reason": current.stop_reason,
        "active_step_title": active_step.title if active_step else "",
        "active_step_kind": active_step.kind if active_step else "",
        "status": current.status,
        "needs_replan": current.needs_replan,
        "final_action": current.final_action,
        "current_step_id": current.current_step_id,
        "stop_condition": current.stop_condition,
        "stop_conditions": list(current.stop_conditions),
        "constraints": list(current.intent.constraints),
        "required_tools": list(active_step.required_tools) if active_step else [],
        "completed_tools": list(active_step.completed_tools) if active_step else [],
        "remaining_required_tools": (
            [
                name
                for name in active_step.required_tools
                if name not in set(active_step.completed_tools)
            ]
            if active_step
            else []
        ),
        "completion_contract": active_step.completion_contract if active_step else "",
    }


def build_plan_context_text(plan: Dict[str, Any] | Plan | None, lessons: List[str] | None = None) -> str:
    summary = summarize_plan(plan)
    if not summary["intent_kind"]:
        return ""
    lines = [
        "[当前计划摘要]",
        f"- intent={summary['intent_kind']}",
        f"- active_step={summary['active_step_title'] or '-'} ({summary['active_step_kind'] or '-'})",
        f"- completed_steps={summary['completed_step_count']}/{summary['step_count']}",
        f"- stop_condition={summary['stop_condition'] or '-'}",
        f"- executable_stop_conditions={','.join(summary.get('stop_conditions') or []) or '-'}",
        f"- needs_replan={summary['needs_replan']}",
    ]
    if summary.get("constraints"):
        lines.append("- constraints=" + "; ".join(summary["constraints"][:8]))
    if summary.get("completion_contract"):
        lines.append(f"- completion_contract={summary['completion_contract']}")
    if summary.get("remaining_required_tools"):
        lines.append(
            "- remaining_required_tools="
            + ",".join(summary["remaining_required_tools"])
        )
    if summary["stop_reason"]:
        lines.append(f"- stop_reason={summary['stop_reason']}")
    if lessons:
        lines.append("")
        lines.append("[历史经验]")
        for lesson in lessons:
            lines.append(f"- {lesson[:150]}")
    return "\n".join(lines)


def build_initial_plan(*, user_message: str, session_memory: Any = None, optimization_state: Any = None, optimization_mode: bool = False) -> Dict[str, Any]:
    constraints = []
    if session_memory is not None:
        constraints = [str(item) for item in (getattr(getattr(session_memory, "conversation", None), "constraints", []) or [])]
    intent_kind = infer_plan_intent_kind(
        optimization_state=optimization_state,
        optimization_mode=optimization_mode,
        user_message=user_message,
    )
    plan = Plan(
        plan_id=uuid.uuid4().hex[:12],
        intent=PlanIntent(kind=intent_kind, user_goal=str(user_message or ""), constraints=constraints),
        steps=build_default_steps(intent_kind),
        stop_condition=default_stop_condition(intent_kind, optimization_state),
        stop_conditions=default_stop_conditions(intent_kind),
        status="active",
    )
    if plan.steps:
        plan.current_step_id = plan.steps[0].step_id
        plan.steps[0].status = "in_progress"
        plan.steps[0].decision_note = "初始计划已建立"
    return plan.to_dict()


def update_plan_after_turn(
    plan: Dict[str, Any] | Plan | None,
    *,
    assistant_text: str = "",
    had_tool_calls: bool = False,
    had_tool_failure: bool = False,
    tool_names: List[str] | None = None,
    observation: str = "",
    final_action: str = "",
    turn_failed_soft: bool = False,
) -> Dict[str, Any] | None:
    current = coerce_plan(plan)
    if current is None:
        return None
    active_step = next((step for step in current.steps if step.step_id == current.current_step_id), None)
    if active_step is None and current.steps:
        active_step = current.steps[0]
        current.current_step_id = active_step.step_id
    if active_step is None:
        return current.to_dict()

    note_parts = []
    if tool_names:
        note_parts.append("tools=" + ", ".join(str(name) for name in tool_names if name))
    if final_action:
        note_parts.append(f"action={final_action}")
    if assistant_text:
        note_parts.append(str(assistant_text).strip()[:160])
    if observation:
        active_step.observation = str(observation).strip()[:240]
    if note_parts:
        active_step.decision_note = " | ".join(note_parts)

    if had_tool_failure:
        active_step.status = "failed"
        current.needs_replan = True
        current.status = "needs_replan"
    elif final_action == "approval_pending":
        # The model proposed a high-risk call but the Host did not dispatch it.
        # Record any successful siblings without advancing beyond the step that
        # still owns the parameter-bound approval request.
        if active_step.completion_contract == "required_tools_all":
            completed = list(active_step.completed_tools)
            for name in tool_names or []:
                if name in active_step.required_tools and name not in completed:
                    completed.append(name)
            active_step.completed_tools = completed
        active_step.status = "in_progress"
        current.needs_replan = False
        current.status = "active"
    elif turn_failed_soft:
        # 软失败：本轮未产出有效结果但没有工具硬错误（如 tool loop 轮次耗尽、
        # 优化轮无有效调参）。标记 needs_replan，由编排层决定是否重规划重试。
        active_step.status = "failed"
        current.needs_replan = True
        current.status = "needs_replan"
    elif final_action == "answer_only" and active_step.kind in {"analyze", "respond"}:
        active_step.status = "completed"
        current.final_action = final_action
        current.status = "completed"
    elif had_tool_calls and active_step.kind in {"analyze", "tool"}:
        if active_step.completion_contract == "required_tools_all":
            completed = list(active_step.completed_tools)
            for name in tool_names or []:
                if name in active_step.required_tools and name not in completed:
                    completed.append(name)
            active_step.completed_tools = completed
            remaining = [
                name for name in active_step.required_tools if name not in set(completed)
            ]
            if remaining:
                active_step.status = "in_progress"
                remaining_note = "remaining_required_tools=" + ", ".join(remaining)
                active_step.decision_note = " | ".join(
                    item for item in (active_step.decision_note, remaining_note) if item
                )
                return current.to_dict()
        active_step.status = "completed"
        next_step = next((step for step in current.steps if step.status == "pending"), None)
        if next_step is not None:
            next_step.status = "in_progress"
            current.current_step_id = next_step.step_id
    elif final_action:
        active_step.status = "completed"
        current.final_action = final_action
        if active_step.kind == "respond":
            current.status = "completed"

    return current.to_dict()


def evaluate_replan_or_stop(
    plan: Dict[str, Any] | Plan | None,
    *,
    had_tool_failure: bool = False,
    result_available: bool = False,
    target_met: bool = False,
    stagnation_hit: bool = False,
    max_round_reached: bool = False,
) -> Dict[str, Any]:
    current = coerce_plan(plan)
    if current is None:
        return {"continue": True, "stop": False, "needs_replan": False, "stop_reason": "", "replan_trigger": ""}

    stop_reason = ""
    replan_trigger = ""
    enabled_stops = set(current.stop_conditions or default_stop_conditions(current.intent.kind))
    if had_tool_failure:
        current.needs_replan = True
        current.replan_count += 1
        current.status = "needs_replan"
        replan_trigger = "tool_failure"
    elif target_met and "target_met" in enabled_stops:
        stop_reason = "target_met"
        current.stop_reason = stop_reason
        current.status = "completed"
    elif stagnation_hit and "stagnation_limit" in enabled_stops:
        stop_reason = "stagnation_limit"
        current.stop_reason = stop_reason
        current.status = "completed"
    elif max_round_reached and "max_round_limit" in enabled_stops:
        stop_reason = "max_round_limit"
        current.stop_reason = stop_reason
        current.status = "completed"
    elif result_available:
        active_step = next((step for step in current.steps if step.step_id == current.current_step_id), None)
        if active_step is not None:
            active_step.observation = active_step.observation or "已得到结果，可继续判断"

    return {
        "continue": not bool(stop_reason) and not current.needs_replan,
        "stop": bool(stop_reason),
        "needs_replan": current.needs_replan,
        "stop_reason": current.stop_reason,
        "replan_trigger": replan_trigger,
        "plan": current.to_dict(),
    }
