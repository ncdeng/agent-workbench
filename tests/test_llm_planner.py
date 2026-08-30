"""Unit tests for agent/llm_planner.py and build_initial_plan LLM path."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock


from cst_agent_workbench.agent.llm_planner import (
    _validate_plan,
    call_planner_llm,
)
from cst_agent_workbench.agent.runtime import build_initial_plan


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_valid_plan_json() -> str:
    return json.dumps({
        "intent_kind": "chat_task",
        "user_goal": "分析当前 S11 结果",
        "constraints": ["必须保持 microstrip feed"],
        "steps": [
            {"step_id": "s1", "kind": "analyze", "title": "分析", "expected_output": "结论", "allowed_tools": ["get_s_parameter"], "required_tools": ["get_s_parameter"]},
            {"step_id": "s2", "kind": "respond", "title": "回复", "expected_output": "回复文字", "allowed_tools": ["recall_tool_result"], "required_tools": []},
        ],
        "stop_condition": "完成分析后停止",
        "stop_conditions": ["task_complete"],
    })


def _make_mock_client(content: str, raise_exc: Exception | None = None) -> MagicMock:
    client = MagicMock()
    if raise_exc:
        client.chat.completions.create.side_effect = raise_exc
    else:
        choice = MagicMock()
        choice.message.content = content
        resp = MagicMock()
        resp.choices = [choice]
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 20
        client.chat.completions.create.return_value = resp
    return client


def _make_session() -> SimpleNamespace:
    return SimpleNamespace(active_plan=None, memory=None, optimization_state=None, history=[], token_usage={})


# ─── _validate_plan ────────────────────────────────────────────────────────────

class TestValidatePlan:
    def test_valid(self):
        plan = json.loads(_make_valid_plan_json())
        assert _validate_plan(plan) is True

    def test_invalid_intent_kind(self):
        plan = json.loads(_make_valid_plan_json())
        plan["intent_kind"] = "unknown_kind"
        assert _validate_plan(plan) is False

    def test_last_step_not_respond(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][-1]["kind"] = "analyze"
        assert _validate_plan(plan) is False

    def test_allowed_tools_outside_step_whitelist_rejected(self):
        """allowed_tools 与 kind 静态白名单无交集时拒绝：运行时过滤会得到空工具目录。"""
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][-1]["allowed_tools"] = ["run_solver"]
        assert _validate_plan(plan) is False

    def test_allowed_tools_intersecting_step_whitelist_accepted(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][-1]["allowed_tools"] = ["recall_tool_result"]
        assert _validate_plan(plan) is True

    def test_single_respond_step_is_valid_for_acknowledgement(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"] = [
            {"step_id": "s1", "kind": "respond", "title": "x", "allowed_tools": []}
        ]
        assert _validate_plan(plan) is True

    def test_single_respond_step_cannot_expose_tools(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"] = [
            {
                "step_id": "s1",
                "kind": "respond",
                "title": "x",
                "allowed_tools": ["check_cst_status"],
            }
        ]
        assert _validate_plan(plan) is False

    def test_too_many_steps(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"] = [
            {"step_id": f"s{i}", "kind": "analyze", "title": "x"} for i in range(5)
        ] + [{"step_id": "s6", "kind": "respond", "title": "x"}]
        assert _validate_plan(plan) is False

    def test_invalid_middle_step_kind_is_rejected(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][0]["kind"] = "invented_kind"

        assert _validate_plan(plan) is False

    def test_required_tools_must_be_subset_of_allowed_tools(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][0]["required_tools"] = ["run_solver"]

        assert _validate_plan(plan) is False

    def test_missing_required_tools_remains_valid_for_legacy_plans(self):
        plan = json.loads(_make_valid_plan_json())
        plan["steps"][0].pop("required_tools")

        assert _validate_plan(plan) is True


# ─── call_planner_llm ──────────────────────────────────────────────────────────

class TestCallPlannerLlm:
    def test_valid_json_returns_plan_dict(self):
        client = _make_mock_client(_make_valid_plan_json())
        plan, usage = call_planner_llm(client, "gpt-4o", "分析结果", "状态文字")
        assert plan is not None
        assert plan["intent"]["kind"] == "chat_task"
        assert plan["intent"]["constraints"] == ["必须保持 microstrip feed"]
        assert plan["steps"][-1]["kind"] == "respond"
        assert plan["steps"][0]["allowed_tools"] == ["get_s_parameter"]
        assert plan["steps"][0]["required_tools"] == ["get_s_parameter"]
        assert plan["steps"][0]["completion_contract"] == "required_tools_all"
        assert plan["stop_conditions"] == ["task_complete"]
        assert usage["prompt_tokens"] == 10
        assert usage["completion_tokens"] == 20

    def test_non_json_response_returns_none(self):
        client = _make_mock_client("这不是 JSON 内容，纯文字")
        plan, usage = call_planner_llm(client, "gpt-4o", "分析", "")
        assert plan is None

    def test_invalid_schema_returns_none(self):
        bad = json.dumps({"intent_kind": "bad_kind", "user_goal": "x", "steps": [], "stop_condition": ""})
        client = _make_mock_client(bad)
        plan, usage = call_planner_llm(client, "gpt-4o", "分析", "")
        assert plan is None

    def test_api_exception_returns_none(self):
        client = _make_mock_client("", raise_exc=RuntimeError("API timeout"))
        plan, usage = call_planner_llm(client, "gpt-4o", "分析", "")
        assert plan is None
        assert usage == {}

    def test_markdown_codeblock_stripped(self):
        wrapped = f"```json\n{_make_valid_plan_json()}\n```"
        client = _make_mock_client(wrapped)
        plan, usage = call_planner_llm(client, "gpt-4o", "分析", "")
        assert plan is not None


# ─── build_initial_plan ────────────────────────────────────────────────────────

class TestBuildInitialPlan:
    def test_falls_back_to_heuristic_when_no_client(self):
        session = _make_session()
        plan = build_initial_plan(session=session, user_message="你好", optimization_mode=False)
        assert plan is not None
        assert session.active_plan == plan

    def test_llm_path_used_when_client_provided(self):
        session = _make_session()
        client = _make_mock_client(_make_valid_plan_json())
        plan = build_initial_plan(session=session, user_message="分析结果", client=client, model="gpt-4o")
        assert plan is not None
        assert plan["intent"]["kind"] == "chat_task"
        client.chat.completions.create.assert_called_once()

    def test_llm_failure_falls_back_to_heuristic(self):
        session = _make_session()
        client = _make_mock_client("not json")
        plan = build_initial_plan(session=session, user_message="你好", client=client, model="gpt-4o")
        # fallback heuristic 仍应返回合法 plan
        assert plan is not None
        assert session.active_plan == plan
