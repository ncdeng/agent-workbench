"""Multi-turn conversation integration tests — all mocked, no real LLM/CST."""
from __future__ import annotations

import json
from unittest.mock import MagicMock


from cst_agent_workbench.agent.runtime import build_initial_plan, evaluate_replan_or_stop
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.agent.memory import MemoryManager
from cst_agent_workbench.optimization.algorithms import auto_select_algorithm


# ── helpers ──────────────────────────────────────────────────────────────────

def _plan_json(intent_kind: str, goal: str = "test") -> str:
    return json.dumps({
        "intent_kind": intent_kind,
        "user_goal": goal,
        "steps": [
            {"step_id": "s1", "kind": "analyze", "title": "分析", "expected_output": "结论"},
            {"step_id": "s2", "kind": "respond", "title": "回复", "expected_output": "回复"},
        ],
        "stop_condition": "完成",
    })


def _mock_client(content: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 5
    resp.usage.completion_tokens = 10
    client.chat.completions.create.return_value = resp
    return client


def _session() -> AgentSession:
    return AgentSession(session_id="multi-turn-test")


# ── Scenario 1: 三轮链路 intent_kind 正确变化 ─────────────────────────────────

class TestMultiTurnIntentProgression:
    def test_turn1_direct_action_plan(self):
        session = _session()
        client = _mock_client(_plan_json("direct_action", "建9.4GHz贴片天线"))
        plan = build_initial_plan(session=session, user_message="建一个9.4GHz贴片天线", client=client, model="gpt-4o")
        assert plan["intent"]["kind"] == "direct_action"
        assert session.active_plan is plan

    def test_turn2_different_plan_replaces_previous(self):
        session = _session()
        client1 = _mock_client(_plan_json("direct_action", "建模"))
        build_initial_plan(session=session, user_message="建模", client=client1, model="gpt-4o")
        first_plan_id = session.active_plan["plan_id"]

        # 强制 plan 完成，触发新规划
        session.active_plan["status"] = "completed"
        client2 = _mock_client(_plan_json("chat_task", "读S11"))
        plan2 = build_initial_plan(session=session, user_message="读取S11", client=client2, model="gpt-4o")
        assert plan2["intent"]["kind"] == "chat_task"
        assert plan2["plan_id"] != first_plan_id

    def test_turn3_optimization_intent(self):
        session = _session()
        session.active_plan = {"status": "completed", "intent": {"kind": "chat_task"}}
        client = _mock_client(_plan_json("optimization_round", "优化S11"))
        plan = build_initial_plan(session=session, user_message="优化S11", client=client, model="gpt-4o")
        assert plan["intent"]["kind"] == "optimization_round"

    def test_plans_do_not_share_steps(self):
        session = _session()
        c1 = _mock_client(_plan_json("direct_action"))
        p1 = build_initial_plan(session=session, user_message="建模", client=c1, model="gpt-4o")
        session.active_plan["status"] = "completed"
        c2 = _mock_client(_plan_json("chat_task"))
        p2 = build_initial_plan(session=session, user_message="读S11", client=c2, model="gpt-4o")
        assert p1["steps"] is not p2["steps"]


# ── Scenario 2: replan 触发后 plan 更新 ────────────────────────────────────────

class TestReplanAfterToolFailure:
    def test_replan_needed_on_tool_failure(self):
        session = _session()
        client = _mock_client(_plan_json("direct_action"))
        build_initial_plan(session=session, user_message="建模", client=client, model="gpt-4o")
        result = evaluate_replan_or_stop(session=session, had_tool_failure=True)
        assert result["needs_replan"] is True
        assert result["replan_trigger"] == "tool_failure"

    def test_no_replan_on_success(self):
        session = _session()
        client = _mock_client(_plan_json("direct_action"))
        build_initial_plan(session=session, user_message="建模", client=client, model="gpt-4o")
        result = evaluate_replan_or_stop(session=session, had_tool_failure=False)
        assert result["needs_replan"] is False

    def test_new_plan_has_different_id_after_replan(self):
        session = _session()
        c1 = _mock_client(_plan_json("direct_action"))
        build_initial_plan(session=session, user_message="建模", client=c1, model="gpt-4o")
        old_id = session.active_plan["plan_id"]
        # replan: force completed then rebuild
        session.active_plan["status"] = "completed"
        c2 = _mock_client(_plan_json("direct_action"))
        build_initial_plan(session=session, user_message="重试建模", client=c2, model="gpt-4o")
        assert session.active_plan["plan_id"] != old_id


# ── Scenario 3: Memory 跨轮持久 ──────────────────────────────────────────────

class TestMemoryPersistsAcrossTurns:
    def test_user_goal_survives_plan_update(self):
        session = _session()
        MemoryManager.update_conversation(session.memory, user_goal="设计9.4GHz天线")
        # 模拟新一轮 plan 建立
        client = _mock_client(_plan_json("direct_action"))
        build_initial_plan(session=session, user_message="建模", client=client, model="gpt-4o")
        assert session.memory.conversation.user_goal == "设计9.4GHz天线"

    def test_workspace_memory_not_cleared_by_planning(self):
        session = _session()
        MemoryManager.update_workspace(session.memory, project_path="/tmp/test.cst")
        client = _mock_client(_plan_json("chat_task"))
        build_initial_plan(session=session, user_message="读S11", client=client, model="gpt-4o")
        assert session.memory.workspace.project_path == "/tmp/test.cst"

    def test_decisions_memory_accumulates(self):
        session = _session()
        MemoryManager.update_decisions(session.memory, failure_reason="solver timeout")
        MemoryManager.update_decisions(session.memory, failure_reason="port error")
        assert len(session.memory.decisions.failure_reasons) == 2


# ── Scenario 4: 算法自动选择在优化循环中 ───────────────────────────────────────

class TestAlgorithmAutoSelect:
    def test_bayesian_selected_for_few_samples(self):
        assert auto_select_algorithm(3, 3) == "bayesian"
        assert auto_select_algorithm(19, 4) == "bayesian"

    def test_pso_selected_for_medium_dim(self):
        assert auto_select_algorithm(25, 3) == "pso"
        assert auto_select_algorithm(50, 6) == "pso"

    def test_de_selected_for_high_dim(self):
        assert auto_select_algorithm(25, 8) == "de"
        assert auto_select_algorithm(100, 10) == "de"

    def test_boundary_exactly_20_samples(self):
        # 20 samples, 3 dims → pso (not bayesian, threshold is < 20)
        assert auto_select_algorithm(20, 3) == "pso"
