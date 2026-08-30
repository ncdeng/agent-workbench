"""Agent Eval — 通过 mock LLM client 验证 Planner 规划质量。

覆盖场景：
  1. Intent Classification 准确性（3 个测试）
  2. Plan 结构完整性（1 个测试）
  3. Fallback 健壮性（2 个测试）
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


from cst_agent_workbench.agent.llm_planner import call_planner_llm
from cst_agent_workbench.agent.runtime import build_initial_plan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(plan_dict: dict | None = None, raw_content: str | None = None):
    """构造 mock OpenAI-style client。

    - plan_dict  : 若指定，序列化为 JSON 作为 LLM 回包内容。
    - raw_content: 若指定，直接作为 LLM 回包内容（用于测试空串等边缘情况）。
    两者互斥，raw_content 优先级更高。
    """
    content = raw_content if raw_content is not None else json.dumps(plan_dict, ensure_ascii=False)
    mock_choice = SimpleNamespace(message=SimpleNamespace(content=content))
    mock_usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20)
    mock_resp = SimpleNamespace(choices=[mock_choice], usage=mock_usage)

    client = MagicMock()
    client.chat.completions.create.return_value = mock_resp
    return client


def _make_valid_plan(intent_kind: str, user_goal: str = "测试目标") -> dict:
    """构造一个 _validate_plan 校验可通过的最小合法 plan。"""
    return {
        "intent_kind": intent_kind,
        "user_goal": user_goal,
        "steps": [
            {"step_id": "s1", "kind": "analyze", "title": "分析", "expected_output": "分析结果"},
            {"step_id": "s2", "kind": "respond", "title": "回复", "expected_output": "回复内容"},
        ],
        "stop_condition": "完成后停止",
    }


def _make_session():
    """构造最小化 session 对象，与 runtime.build_initial_plan 期望字段对齐。"""
    return SimpleNamespace(
        active_plan=None,
        memory=None,
        optimization_state=None,
        history=[],
        token_usage={},
    )


# ---------------------------------------------------------------------------
# 场景 1 — Intent Classification 准确性
# ---------------------------------------------------------------------------

class TestIntentClassification:
    """验证 call_planner_llm 解析 intent_kind 的正确性。"""

    def test_chat_task_intent(self):
        """'分析当前 S11 结果' → intent_kind in {chat_task, direct_action}"""
        plan_dict = _make_valid_plan("chat_task", "分析当前 S11 结果")
        client = _make_client(plan_dict)

        result, usage = call_planner_llm(
            client=client,
            model="gpt-4o",
            user_message="分析当前 S11 结果",
            context_text="",
        )

        assert result is not None, "plan 解析不应为 None"
        assert result["intent"]["kind"] in (
            "chat_task", "direct_action"
        ), f"期望 chat_task 或 direct_action，实际: {result['intent']['kind']}"

    def test_optimization_round_intent(self):
        """'优化一轮' → intent_kind == optimization_round"""
        plan_dict = _make_valid_plan("optimization_round", "优化一轮")
        client = _make_client(plan_dict)

        result, usage = call_planner_llm(
            client=client,
            model="gpt-4o",
            user_message="优化一轮",
            context_text="",
        )

        assert result is not None
        assert result["intent"]["kind"] == "optimization_round"

    def test_continuous_optimization_intent(self):
        """'连续优化直到 S11 < -15dB' → intent_kind == continuous_optimization"""
        plan_dict = _make_valid_plan("continuous_optimization", "连续优化直到 S11 < -15dB")
        client = _make_client(plan_dict)

        result, usage = call_planner_llm(
            client=client,
            model="gpt-4o",
            user_message="连续优化直到 S11 < -15dB",
            context_text="",
        )

        assert result is not None
        assert result["intent"]["kind"] == "continuous_optimization"


# ---------------------------------------------------------------------------
# 场景 2 — Plan 结构完整性
# ---------------------------------------------------------------------------

class TestPlanStructure:
    """验证 build_initial_plan 返回的 plan 结构满足 Agent 编排要求。"""

    def test_plan_structure_completeness(self):
        """plan 字段完整性：steps 非空、最后一步 kind==respond、intent.user_goal 非空、
        stop_condition 非空，session.active_plan 已更新。"""
        plan_dict = _make_valid_plan("direct_action", "执行 CST 求解")
        client = _make_client(plan_dict)
        session = _make_session()

        plan = build_initial_plan(
            session=session,
            user_message="执行 CST 求解",
            client=client,
            model="gpt-4o",
        )

        # steps 非空
        assert plan.get("steps"), "steps 不应为空"

        # 最后一步 kind == respond
        last_step = plan["steps"][-1]
        assert last_step.get("kind") == "respond", (
            f"最后一步 kind 应为 respond，实际: {last_step.get('kind')}"
        )

        # intent.user_goal 非空
        user_goal = (plan.get("intent") or {}).get("user_goal", "")
        assert user_goal, "intent.user_goal 不应为空"

        # stop_condition 非空
        assert plan.get("stop_condition"), "stop_condition 不应为空"

        # session.active_plan 已更新
        assert session.active_plan is not None, "session.active_plan 应已更新"


# ---------------------------------------------------------------------------
# 场景 3 — Fallback 健壮性
# ---------------------------------------------------------------------------

class TestFallbackRobustness:
    """验证 LLM 返回异常内容时 Planner 仍能提供有效 fallback plan。"""

    def test_empty_llm_response_fallback(self):
        """LLM 返回空字符串 → fallback heuristic plan 仍有效，session.active_plan 非 None。"""
        client = _make_client(raw_content="")
        session = _make_session()

        plan = build_initial_plan(
            session=session,
            user_message="分析 S11",
            client=client,
            model="gpt-4o",
        )

        assert plan is not None, "空串回包时 fallback plan 不应为 None"
        assert plan.get("steps"), "fallback plan steps 不应为空"
        assert session.active_plan is not None, "session.active_plan 在 fallback 路径仍应被赋值"

    def test_valid_llm_response_no_fallback(self):
        """LLM 返回合法 optimization_round plan → 直接使用 LLM plan，不走 fallback，
        session.active_plan 非 None 且 intent.kind == optimization_round。"""
        plan_dict = _make_valid_plan("optimization_round", "执行一轮优化")
        client = _make_client(plan_dict)
        session = _make_session()

        plan = build_initial_plan(
            session=session,
            user_message="优化一轮",
            client=client,
            model="gpt-4o",
        )

        assert session.active_plan is not None
        intent_kind = (plan.get("intent") or {}).get("kind", "")
        assert intent_kind == "optimization_round", (
            f"LLM plan 应被直接采用，期望 optimization_round，实际: {intent_kind}"
        )
