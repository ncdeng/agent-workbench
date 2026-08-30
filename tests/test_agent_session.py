from cst_agent_workbench.agent.memory import StructuredMemory
from cst_agent_workbench.agent.runtime_state import record_observability_degradation
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.optimization.state import OptimizationState


def test_session_retention_caps_history_events_and_tool_results(monkeypatch):
    from cst_agent_workbench import config

    monkeypatch.setattr(config, "AGENT_HISTORY_RETENTION", 20)
    monkeypatch.setattr(config, "AGENT_TOOL_EVENT_RETENTION", 20)
    monkeypatch.setattr(config, "AGENT_TOOL_RESULT_RETENTION", 10)
    session = AgentSession(session_id="retention")
    session.history.extend({"role": "user", "content": str(i)} for i in range(30))
    session.tool_events.extend({"tool_name": str(i)} for i in range(30))
    for index in range(15):
        session.artifacts.tool_results[f"tool-{index}"] = {"index": index}

    session.enforce_retention()

    assert len(session.history) == 20
    assert session.history[0]["content"] == "10"
    assert len(session.tool_events) == 20
    assert len(session.artifacts.tool_results) == 10
    assert "tool-0" not in session.artifacts.tool_results


def test_agent_session_projection_contains_runtime_fields():
    opt = OptimizationState()
    session = AgentSession(session_id="sess-1")
    session.bind_optimization_state(opt)
    session.history.append({"role": "user", "content": "hello"})
    session.tool_events.append({"tool_name": "read_result", "success": True})
    session.artifacts.last_results = {"success": True, "item": "S1,1"}
    session.trace.selected_trace_run_id = "run-1"
    session.trace.trace_history = [
        {
            "run_id": "run-1",
            "status": "completed",
            "run_metrics": {"turn_count": 2, "tool_call_count": 3},
            "decision_summary": {"failed_tool_call_count": 1, "final_action": "tool_then_answer"},
        }
    ]

    projection = session.get_projection()

    assert projection["session_id"] == "sess-1"
    assert projection["history_length"] == 1
    assert projection["tool_event_count"] == 1
    assert projection["has_last_results"] is True
    assert projection["optimization"]["round"] == 0
    assert projection["selected_trace_run_id"] == "run-1"
    assert projection["trace_current_status"] == "completed"
    assert projection["trace_last_run_summary"]["tool_call_count"] == 3
    assert projection["trace_last_run_summary"]["failed_tool_call_count"] == 1
    assert projection["trace_last_run_summary"]["final_action"] == "tool_then_answer"
    assert projection["trace_last_run_summary"]["plan_summary"] == {}
    assert projection["memory_scope"] == {}


def test_agent_session_projection_exposes_memory_scope_metadata():
    session = AgentSession(session_id="sess-1")
    session.metadata["memory_scope"] = {
        "session_memory_path": "cache/sessions/sess-1.json",
        "persistent_memory_path": "cache/persistent_memory.json",
        "session_memory_loaded": False,
        "persistent_memory_loaded": True,
        "persistent_fields": ["conversation.constraints"],
    }

    projection = session.get_projection()

    assert projection["memory_scope"]["session_memory_path"] == "cache/sessions/sess-1.json"
    assert projection["memory_scope"]["persistent_memory_loaded"] is True
    assert projection["memory_scope"]["persistent_fields"] == ["conversation.constraints"]


def test_observability_degradation_is_bounded_and_projected():
    session = AgentSession(session_id="degradation")

    for index in range(55):
        record_observability_degradation(
            session,
            component="test_component",
            fallback="safe_fallback",
            error=f"error-{index}",
        )

    assert len(session.metadata["observability_degradations"]) == 50
    projected = session.get_projection()["observability_degradations"]
    assert len(projected) == 10
    assert projected[-1]["error"] == "error-54"


def test_structured_memory_to_dict_shape_is_stable():
    memory = StructuredMemory()
    memory.conversation.user_goal = "优化 9.4 GHz S11"
    memory.workspace.project_path = "D:/demo/project.cst"
    memory.decisions.best_so_far = {"round": 2, "metric_value": -13.2}

    data = memory.to_dict()

    assert data["conversation"]["user_goal"] == "优化 9.4 GHz S11"
    assert data["workspace"]["project_path"] == "D:/demo/project.cst"
    assert data["decisions"]["best_so_far"]["round"] == 2
