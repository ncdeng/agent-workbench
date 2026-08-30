from cst_agent_workbench.agent.planner import build_plan_context_text, summarize_plan
from cst_agent_workbench.agent.runtime import (
    build_initial_plan,
    evaluate_optimization_next_action,
    evaluate_replan_or_stop,
    update_plan_after_turn,
)
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.optimization.state import OptimizationState
from types import SimpleNamespace


class _Conversation:
    def __init__(self):
        self.constraints = ["保持 microstrip 拓扑"]


class _Memory:
    def __init__(self):
        self.conversation = _Conversation()


class _SessionStub:
    def __init__(self):
        self.memory = _Memory()
        self.optimization_state = OptimizationState()
        self.active_plan = None


def test_build_initial_plan_populates_session_active_plan():
    session = _SessionStub()

    plan = build_initial_plan(session=session, user_message="读取当前 S11 并总结", optimization_mode=False)

    assert session.active_plan == plan
    assert plan["intent"]["kind"] == "direct_action"
    assert plan["current_step_id"] == "step_analyze"
    assert plan["steps"][0]["status"] == "in_progress"


def test_new_user_goal_rebuilds_even_when_previous_plan_is_active(monkeypatch):
    from cst_agent_workbench.agent import agent as agent_module

    instance = agent_module.CSTAgent.__new__(agent_module.CSTAgent)
    instance.session = SimpleNamespace(
        active_plan={"intent": {"kind": "direct_action"}, "status": "active", "steps": []},
        metadata={"active_plan_user_message": "建立贴片天线"},
    )
    instance._optimization_mode = False
    instance.client = None
    instance.model = "test-model"
    instance.token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    calls = []

    def fake_build(**kwargs):
        calls.append(kwargs["user_message"])
        kwargs["session"].active_plan = {
            "intent": {"kind": "direct_action", "user_goal": kwargs["user_message"]},
            "status": "active",
            "steps": [],
        }
        return kwargs["session"].active_plan, {}

    monkeypatch.setattr(agent_module, "build_initial_plan_with_usage", fake_build)

    instance._ensure_active_plan_for_message("读取当前 S11")
    instance._ensure_active_plan_for_message("读取当前 S11")

    assert calls == ["读取当前 S11"]
    assert instance.session.metadata["active_plan_user_message"] == "读取当前 S11"


def test_plan_summary_and_context_expose_active_step_and_stop_condition():
    session = _SessionStub()
    build_initial_plan(session=session, user_message="执行连续优化", optimization_mode=True)
    plan = dict(session.active_plan)
    plan["intent"] = {**plan["intent"], "kind": "continuous_optimization"}
    session.active_plan = plan

    summary = summarize_plan(session.active_plan)
    context = build_plan_context_text(session.active_plan)

    assert summary["active_step_title"]
    assert summary["stop_condition"]
    assert "[当前计划摘要]" in context
    assert "needs_replan" in context


def test_update_plan_after_turn_marks_replan_after_tool_failure():
    session = _SessionStub()
    build_initial_plan(session=session, user_message="读取结果", optimization_mode=False)

    updated = update_plan_after_turn(
        session=session,
        assistant_text="工具执行失败",
        had_tool_calls=True,
        had_tool_failure=True,
        tool_names=["get_s_parameter"],
        observation="连接失败",
        final_action="tool_failure",
    )
    evaluation = evaluate_replan_or_stop(session=session, had_tool_failure=True)

    assert updated["needs_replan"] is True
    assert evaluation["needs_replan"] is True
    assert evaluation["replan_trigger"] == "tool_failure"


def test_approval_pending_keeps_current_step_authorized_and_in_progress():
    session = _SessionStub()
    session.active_plan = {
        "plan_id": "approval",
        "intent": {"kind": "direct_action", "user_goal": "run VBA", "constraints": []},
        "steps": [
            {
                "step_id": "run-script",
                "kind": "tool",
                "title": "执行脚本",
                "status": "in_progress",
                "allowed_tools": ["execute_vba_script"],
                "required_tools": ["execute_vba_script"],
                "completion_contract": "required_tools_all",
            },
            {
                "step_id": "respond",
                "kind": "respond",
                "title": "回复",
                "status": "pending",
                "allowed_tools": [],
            },
        ],
        "current_step_id": "run-script",
        "status": "active",
    }

    updated = update_plan_after_turn(
        session=session,
        had_tool_calls=False,
        had_tool_failure=False,
        tool_names=[],
        observation="approval requested",
        final_action="approval_pending",
    )

    assert updated["current_step_id"] == "run-script"
    assert updated["steps"][0]["status"] == "in_progress"
    assert updated["steps"][0]["completed_tools"] == []
    assert updated["steps"][1]["status"] == "pending"
    assert updated["needs_replan"] is False


def test_update_plan_after_turn_records_observation_and_completion():
    session = _SessionStub()
    build_initial_plan(session=session, user_message="回答当前状态", optimization_mode=False)

    updated = update_plan_after_turn(
        session=session,
        assistant_text="已读取结果并总结",
        had_tool_calls=False,
        had_tool_failure=False,
        tool_names=[],
        observation="最小 S11 = -12 dB",
        final_action="answer_only",
    )

    active_step = updated["steps"][0]
    assert active_step["observation"] == "最小 S11 = -12 dB"
    assert updated["final_action"] == "answer_only"
    assert updated["status"] == "completed"


def test_multi_tool_step_waits_until_all_explicit_required_tools_complete():
    session = _SessionStub()
    session.active_plan = {
        "plan_id": "multi-tool",
        "intent": {"kind": "direct_action", "user_goal": "build", "constraints": []},
        "steps": [
            {
                "step_id": "configure",
                "kind": "tool",
                "title": "配置端口、频段与监视器",
                "status": "in_progress",
                "allowed_tools": [
                    "create_discrete_port",
                    "set_frequency_range",
                    "create_farfield_monitor",
                ],
                "required_tools": [
                    "create_discrete_port",
                    "set_frequency_range",
                    "create_farfield_monitor",
                ],
                "completion_contract": "required_tools_all",
            },
            {
                "step_id": "solve",
                "kind": "tool",
                "title": "求解",
                "status": "pending",
                "allowed_tools": ["run_solver"],
                "required_tools": ["run_solver"],
                "completion_contract": "required_tools_all",
            },
        ],
        "current_step_id": "configure",
        "status": "active",
    }

    partial = update_plan_after_turn(
        session=session,
        had_tool_calls=True,
        tool_names=["create_discrete_port"],
        final_action="tool_batch",
    )

    assert partial["current_step_id"] == "configure"
    assert partial["steps"][0]["status"] == "in_progress"
    assert partial["steps"][0]["completed_tools"] == ["create_discrete_port"]
    assert summarize_plan(partial)["remaining_required_tools"] == [
        "set_frequency_range",
        "create_farfield_monitor",
    ]

    completed = update_plan_after_turn(
        session=session,
        had_tool_calls=True,
        tool_names=["set_frequency_range", "create_farfield_monitor"],
        final_action="tool_batch",
    )

    assert completed["steps"][0]["status"] == "completed"
    assert completed["current_step_id"] == "solve"
    assert completed["steps"][1]["status"] == "in_progress"


def test_legacy_step_without_required_tools_keeps_any_call_compatibility():
    session = _SessionStub()
    session.active_plan = {
        "plan_id": "legacy",
        "intent": {"kind": "direct_action", "user_goal": "read", "constraints": []},
        "steps": [
            {
                "step_id": "read",
                "kind": "tool",
                "title": "读取结果",
                "status": "in_progress",
                "allowed_tools": ["list_results", "read_result", "recall_tool_result"],
            },
            {
                "step_id": "respond",
                "kind": "respond",
                "title": "回复",
                "status": "pending",
                "allowed_tools": [],
            },
        ],
        "current_step_id": "read",
        "status": "active",
    }

    updated = update_plan_after_turn(
        session=session,
        had_tool_calls=True,
        tool_names=["list_results"],
        final_action="tool_batch",
    )

    assert updated["steps"][0]["completion_contract"] == "legacy_any_call"
    assert updated["steps"][0]["status"] == "completed"
    assert updated["current_step_id"] == "respond"


def test_agent_session_projection_contains_plan_summary():
    session = AgentSession(session_id="sess-plan")
    session.bind_optimization_state(OptimizationState())
    session.active_plan = build_initial_plan(session=session, user_message="执行连续优化", optimization_mode=True)
    session.active_plan["intent"]["kind"] = "continuous_optimization"
    session.trace.trace_history = [
        {
            "run_id": "run-1",
            "status": "completed",
            "run_metrics": {"turn_count": 1, "tool_call_count": 1},
            "decision_summary": {
                "failed_tool_call_count": 0,
                "final_action": "tool_then_answer",
                "plan_summary": {
                    "intent_kind": "continuous_optimization",
                    "step_count": 4,
                    "completed_step_count": 2,
                    "replan_count": 1,
                    "stop_reason": "stagnation_limit",
                },
            },
        }
    ]

    projection = session.get_projection()

    assert projection["active_plan"]["intent_kind"] == "continuous_optimization"
    assert projection["trace_last_run_summary"]["plan_summary"]["stop_reason"] == "stagnation_limit"


def test_update_plan_after_turn_marks_replan_after_soft_failure():
    """turn_failed_soft=True（无工具硬错误，但本轮没产出有效结果）应触发 needs_replan。

    与硬失败分支共享 replan 信号，但场景不同：例如优化轮没产生 changed_params、
    或 tool loop 轮次耗尽没拿到结果。这里固定不传 had_tool_failure=True，
    确保 soft 路径自身就能标记 replan。
    """
    session = _SessionStub()
    build_initial_plan(session=session, user_message="读取结果", optimization_mode=False)

    updated = update_plan_after_turn(
        session=session,
        assistant_text="本轮无有效产出",
        had_tool_calls=True,
        had_tool_failure=False,
        tool_names=["get_s_parameter"],
        observation="未产出有效调参",
        final_action="tool_only",
        turn_failed_soft=True,
    )

    assert updated["needs_replan"] is True
    assert updated["status"] == "needs_replan"
    active_step = next(step for step in updated["steps"] if step["step_id"] == updated["current_step_id"])
    assert active_step["status"] == "failed"
    assert active_step["observation"] == "未产出有效调参"


def test_evaluate_optimization_next_action_skips_llm_replan_when_disabled():
    """replan_with_llm=False 时即使 needs_replan 也不重建 plan，next_action 仍为 replan。

    生产图路径要求 plan 的唯一写入点是 planner_node；本函数此时只返回信号，
    交由图决定是否重规划。验证点：
      - 不传 client/model 也安全执行
      - needs_replan=True 且 next_action='replan'
      - 结果不含 replanned_via_llm（即没原地重建）
      - session.active_plan 的 plan_id 不变（未被覆盖）
    """
    session = _SessionStub()
    plan = build_initial_plan(session=session, user_message="执行一轮优化", optimization_mode=True)
    original_plan_id = plan["plan_id"]

    update_plan_after_turn(
        session=session,
        assistant_text="工具失败",
        had_tool_calls=True,
        had_tool_failure=True,
        tool_names=["get_s_parameter"],
        observation="连接失败",
        final_action="tool_failure",
    )

    result = evaluate_optimization_next_action(
        session=session,
        had_tool_failure=True,
        replan_with_llm=False,
    )

    assert result["needs_replan"] is True
    assert result["next_action"] == "replan"
    assert "replanned_via_llm" not in result
    assert session.active_plan["plan_id"] == original_plan_id
