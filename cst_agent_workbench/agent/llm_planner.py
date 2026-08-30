"""LLM Planner — 独立 LLM call，输出结构化 Plan JSON。

职责：仅负责调用 LLM 生成计划，不维护任何状态。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from cst_agent_workbench.agent.json_parse import extract_json_object

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """你是 CST 仿真 Agent 的 Planner。
根据用户目标和当前仿真状态，输出执行计划（纯 JSON，无其他内容）：
{
  "intent_kind": "<chat_task|direct_action|optimization_round|continuous_optimization>",
  "user_goal": "<一句话描述>",
  "constraints": ["<用户明确说出的必须/禁止条件；没有则为空>"],
  "steps": [
    {"step_id": "<唯一id>", "kind": "<analyze|tool|optimize|judge|respond|read_result>",
     "title": "<简短标题>", "expected_output": "<期望产出>",
     "allowed_tools": ["<本步允许使用的工具，仅从 Available tools 中选择>"],
     "required_tools": ["<本步完成前必须全部成功调用的工具；候选/回退工具不要放入>"]}
  ],
  "stop_condition": "<给人看的停止条件>",
  "stop_conditions": ["task_complete|target_met|stagnation_limit|max_round_limit"]
}
规则：steps 1-5 个；最后一步 kind 必须是 respond；单步计划只允许用于无需工具的直接回复，
此时唯一一步必须是 respond 且 allowed_tools 为空；intent_kind 只能是以上4种之一；
constraints 只能复制用户明确表达的限制，不得根据常识自行补充。
required_tools 必须是 allowed_tools 的子集；多项配置都不可缺少时全部列入；
候选、条件回退或只需任选其一的工具只放 allowed_tools，不放 required_tools；
respond 步的 required_tools 必须为空。
“记住/记录/更正”审计代号、任务编号、标签或对话约束属于会话记忆，不是 CST 设计参数；
除非用户明确要求修改 CST 模型参数，否则不得为此选择 store_parameter/delete_parameter。
若用户明确要求执行某个目标工具操作，并要求连接中断后恢复重试，第一执行步只选择目标工具；
不要用 check_cst_status 替代目标操作，也不要在同一步并列状态检查与目标工具，运行时会在目标工具失败后触发恢复。"""

_VALID_INTENT_KINDS = {"chat_task", "direct_action", "optimization_round", "continuous_optimization"}
_VALID_STEP_KINDS = {"analyze", "tool", "optimize", "judge", "respond", "read_result"}
_VALID_STOP_CONDITIONS = {"task_complete", "target_met", "stagnation_limit", "max_round_limit"}


def _validate_plan(plan: dict) -> bool:
    """校验 LLM 返回的 plan dict 结构合法性。"""
    if not isinstance(plan, dict):
        return False
    if plan.get("intent_kind") not in _VALID_INTENT_KINDS:
        return False
    constraints = plan.get("constraints", [])
    if not isinstance(constraints, list) or any(not isinstance(item, str) for item in constraints):
        return False
    steps = plan.get("steps")
    if not steps or not isinstance(steps, list):
        return False
    if len(steps) > 5:
        return False
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") not in _VALID_STEP_KINDS:
            return False
        allowed_tools = step.get("allowed_tools", [])
        if not isinstance(allowed_tools, list) or any(not isinstance(item, str) for item in allowed_tools):
            return False
        if allowed_tools:
            # allowed_tools 必须与该 kind 的静态白名单有交集，否则运行时过滤会
            # 得到空工具列表（见 runtime.run_chat_completion_loop 的 fail-closed 守卫）。
            from cst_agent_workbench.agent.runtime import _STEP_ALLOWED_TOOL_NAMES

            base_allowed = _STEP_ALLOWED_TOOL_NAMES.get(str(step.get("kind")))
            if base_allowed is None or not set(allowed_tools) & base_allowed:
                return False
        required_tools = step.get("required_tools")
        if required_tools is not None:
            if not isinstance(required_tools, list) or any(
                not isinstance(item, str) for item in required_tools
            ):
                return False
            if not set(required_tools).issubset(set(allowed_tools)):
                return False
            if step.get("kind") == "respond" and required_tools:
                return False
    last_step = steps[-1]
    if not isinstance(last_step, dict) or last_step.get("kind") != "respond":
        return False
    if len(steps) == 1 and list(last_step.get("allowed_tools") or []):
        return False
    stop_conditions = plan.get("stop_conditions", ["task_complete"])
    if not isinstance(stop_conditions, list):
        return False
    return all(item in _VALID_STOP_CONDITIONS for item in stop_conditions)


def _normalize_plan(plan: dict) -> dict:
    """将 LLM 输出的扁平 plan 转换为 plan_from_dict 可接受的格式。"""
    steps = []
    for i, s in enumerate(plan.get("steps", [])):
        step_id = s.get("step_id") or f"step_{i}"
        allowed_tools = [str(item) for item in (s.get("allowed_tools") or []) if str(item)]
        required_tools_declared = "required_tools" in s
        required_tools = [str(item) for item in (s.get("required_tools") or []) if str(item)]
        steps.append({
            "step_id": step_id,
            "kind": s.get("kind", "analyze"),
            "title": s.get("title", ""),
            "expected_output": s.get("expected_output", ""),
            "allowed_tools": allowed_tools,
            "required_tools": required_tools,
            "completed_tools": [],
            "completion_contract": (
                "required_tools_all"
                if required_tools
                else "any_tool_call"
                if required_tools_declared
                else "legacy_any_call"
            ),
        })
    intent_kind = plan.get("intent_kind", "chat_task")
    user_goal = plan.get("user_goal", "")
    return {
        "plan_id": str(uuid.uuid4()),
        "intent": {
            "kind": intent_kind,
            "user_goal": user_goal,
            "constraints": [str(item) for item in (plan.get("constraints") or []) if str(item).strip()],
        },
        "steps": steps,
        "current_step_id": steps[0]["step_id"] if steps else "",
        "stop_condition": plan.get("stop_condition", ""),
        "stop_conditions": list(plan.get("stop_conditions") or ["task_complete"]),
        "status": "active",
    }


def call_planner_llm(
    client: Any,
    model: str,
    user_message: str,
    context_text: str,
    timeout: int = 15,
) -> tuple[dict | None, dict]:
    """调用 LLM 生成 Plan。返回 (plan_dict | None, usage_dict)。

    - 解析失败、schema 不合法、API 异常均返回 (None, usage_or_empty)。
    """
    usage: dict = {}
    try:
        messages = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": f"# 当前状态\n{context_text}\n\n# 用户目标\n{user_message}"},
        ]
        create_kwargs = dict(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=512,
            timeout=timeout,
        )
        try:
            # 优先用 JSON mode 约束输出；provider 不支持时降级为普通调用
            resp = client.chat.completions.create(
                **create_kwargs, response_format={"type": "json_object"}
            )
        except Exception:
            resp = client.chat.completions.create(**create_kwargs)
        raw = resp.choices[0].message.content or ""
        response_usage = getattr(resp, "usage", None)
        cached_tokens = getattr(response_usage, "cached_tokens", 0)
        cache_write_tokens = getattr(response_usage, "cache_write_tokens", 0)
        usage = {
            "prompt_tokens": getattr(response_usage, "prompt_tokens", 0),
            "completion_tokens": getattr(response_usage, "completion_tokens", 0),
            "cached_tokens": cached_tokens if isinstance(cached_tokens, (int, float)) else 0,
            "cache_write_tokens": cache_write_tokens if isinstance(cache_write_tokens, (int, float)) else 0,
        }
        plan = extract_json_object(raw)
        if plan is None:
            logger.warning("planner LLM returned unparseable output: %s", raw[:150])
            return None, usage
        if not _validate_plan(plan):
            logger.warning(
                "planner LLM plan failed schema validation (intent=%r, steps=%s)",
                plan.get("intent_kind"), len(plan.get("steps") or []),
            )
            return None, usage
        return _normalize_plan(plan), usage
    except Exception as exc:
        logger.warning("planner LLM call failed: %s", exc)
        return None, usage
