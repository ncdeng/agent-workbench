"""Tests for the failure recovery engine and its integration with tool_runtime."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from cst_agent_workbench.agent.failure_recovery import (
    FailureEvent,
    FailureRecoveryEngine,
    RecoveryAction,
    RecoveryResult,
    make_failure_event,
)
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.errors import ErrorType


class _FakeCST:
    def __init__(self, *, connected: bool = True, offline: bool = False, connect_success: bool = True):
        self.connected = connected
        self.offline_mode = offline
        self.connect_success = connect_success
        self.project_path = "D:/demo/project.cst"
        self.reconnect_calls = 0
        self.execute_vba_calls: list[tuple] = []
        self.run_solver_calls = 0

    def connect(self):
        self.reconnect_calls += 1
        self.connected = self.connect_success
        self.offline_mode = not self.connect_success
        return {"success": self.connect_success, "message": "reconnected" if self.connect_success else "failed"}

    def is_connected(self):
        return self.connected and not self.offline_mode

    def execute_vba(self, vba_code, label=None, timeout=None):
        self.execute_vba_calls.append((label, vba_code[:40] if vba_code else ""))
        return {"success": True, "message": "ok", "executed": True}

    def run_solver(self, timeout=300):
        self.run_solver_calls += 1
        return {"success": True, "message": "solver ok"}


class _FakeResults:
    """Minimal results reader that supports the path read_s11 takes."""

    def __init__(self, get_s_parameter_fn=None):
        self._get_s_parameter_fn = get_s_parameter_fn or self._default_s_parameter

    def open(self, project_path: str) -> dict:
        return {"success": True, "message": "opened"}

    def get_s_parameter(self, port_i: int, port_j: int, *, max_points=None) -> dict:
        return self._get_s_parameter_fn(port_i, port_j)

    @staticmethod
    def _default_s_parameter(port_i: int, port_j: int) -> dict:
        return {
            "success": True,
            "message": "s11",
            "item": f"S{port_i},{port_j}",
            "plot_data": [{"freq": 9.3, "s_db": -8.0}, {"freq": 9.4, "s_db": -12.0}],
        }


def _failing_then_successful_s_parameter(message: str):
    """Return a reader that fails on the first call and succeeds afterwards."""
    call_count = 0

    def reader(port_i: int, port_j: int) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "success": False,
                "message": message,
                "item": f"S{port_i},{port_j}",
            }
        return {
            "success": True,
            "message": "s11",
            "item": f"S{port_i},{port_j}",
            "plot_data": [{"freq": 9.3, "s_db": -8.0}, {"freq": 9.4, "s_db": -12.0}],
        }

    return reader


class _FakeAgent:
    def __init__(self, *, cst=None, connected: bool = True, offline: bool = False, results=None):
        self.cst = cst or _FakeCST(connected=connected, offline=offline)
        self.tool_events: list[dict] = []
        self.last_tool_message = ""
        self.last_results = {}
        self.last_vba = ""
        self._active_tool_call_id = "tc-1"
        self.results = results or _FakeResults()
        self.session = SimpleNamespace(
            artifacts=SimpleNamespace(tool_results={}),
            metadata={},
            memory=SimpleNamespace(
                decisions=SimpleNamespace(failure_reasons=[]),
                workspace=SimpleNamespace(last_results_summary={}),
            ),
        )
        self._failure_recovery_engine = FailureRecoveryEngine()
        self._failure_recovery_engine.register_default_actions()

    def _get_phase(self, tool_name: str) -> str:
        return f"phase:{tool_name}"

    def _remember_failure(self, reason: str) -> None:
        self.session.memory.decisions.failure_reasons.append(reason)

    def _refresh_session_memory_from_runtime(self, persist: bool = False) -> None:
        pass


def test_make_failure_event_classifies_timeout() -> None:
    event = make_failure_event(
        tool_name="run_solver",
        message="Operation timed out after 300s",
        phase="phase:run_solver",
        arguments={},
    )
    assert event.error_type == ErrorType.CST_TIMEOUT
    assert event.tool_name == "run_solver"


def test_engine_records_attempt_counters() -> None:
    agent = _FakeAgent()
    event = make_failure_event(
        tool_name="run_solver",
        message="Operation timed out after 300s",
        phase="phase:run_solver",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    assert engine._attempt_counters(agent) == {}
    engine.attempt_recovery(agent, event)
    assert engine._attempt_counters(agent)["timeout_wait_and_reconnect"] == 1


def test_reconnect_recovery_restores_connection() -> None:
    agent = _FakeAgent(connected=False)
    agent.cst.connected = False
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:check_cst_status",
        tool_name="check_cst_status",
        message="无法连接 CST",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    result = engine.attempt_recovery(agent, event)

    assert result.action_name == "reconnect_cst"
    assert result.recovered is True
    assert agent.cst.reconnect_calls == 1
    assert agent.cst.connected is True
    assert result.retry_tool == "check_cst_status"


def test_recovery_counter_resets_after_successful_recovery() -> None:
    """M2：恢复预算按故障事件计。恢复成功后计数器重置，同一会话内的下一次
    掉线仍可获得完整预算，而不是被历史成功恢复永久耗尽。"""
    agent = _FakeAgent(connected=False)
    agent.cst.connected = False
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:check_cst_status",
        tool_name="check_cst_status",
        message="无法连接 CST",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    first = engine.attempt_recovery(agent, event)
    assert first.recovered is True
    assert engine._attempt_counters(agent)["reconnect_cst"] == 0

    agent.cst.connected = False  # 新的故障事件
    second = engine.attempt_recovery(agent, event)
    assert second.recovered is True
    assert agent.cst.reconnect_calls == 2


def test_recovery_counter_persists_through_failed_recovery() -> None:
    """连续失败的计数不重置：max_attempts 预算仍然有界。"""
    cst = _FakeCST(connected=False, offline=True, connect_success=False)
    agent = _FakeAgent(cst=cst)
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:check_cst_status",
        tool_name="check_cst_status",
        message="无法连接 CST",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    first = engine.attempt_recovery(agent, event)
    assert first.recovered is False
    assert engine._attempt_counters(agent)["reconnect_cst"] == 1

    second = engine.attempt_recovery(agent, event)
    assert second.recovered is False
    assert engine._attempt_counters(agent)["reconnect_cst"] == 2

    exhausted = engine.attempt_recovery(agent, event)
    assert exhausted.action_name == "none"
    assert exhausted.recovered is False


def test_reconnect_failure_dict_is_not_treated_as_success() -> None:
    cst = _FakeCST(connected=False, offline=True, connect_success=False)
    agent = _FakeAgent(cst=cst)
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:check_cst_status",
        tool_name="check_cst_status",
        message="无法连接 CST",
        arguments={},
    )

    result = agent._failure_recovery_engine.attempt_recovery(agent, event)

    assert result.action_name == "reconnect_cst"
    assert result.recovered is False
    assert result.retry_tool is None
    assert result.details["outcome"]["success"] is False


def test_reconnect_uses_production_controller_connect_contract(monkeypatch) -> None:
    controller = CSTController()
    controller.connected = False
    controller.offline_mode = True
    monkeypatch.setattr(
        controller,
        "_run_com_script",
        lambda args, timeout: {
            "success": True,
            "message": "connected by contract test",
            "project_file": "D:/demo/recovered.cst",
        },
    )
    agent = _FakeAgent(cst=controller)
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:run_solver",
        tool_name="run_solver",
        message="not connected to CST",
        arguments={"timeout": 10},
    )

    result = agent._failure_recovery_engine.attempt_recovery(agent, event)

    assert result.recovered is True
    assert result.details["method"] == "connect"
    assert result.details["project_path"] == "D:/demo/recovered.cst"
    assert result.retry_tool == "run_solver"


def test_dry_run_fallback_when_offline() -> None:
    agent = _FakeAgent(offline=True)
    event = FailureEvent(
        error_type=ErrorType.OFFLINE_MODE,
        phase="phase:get_s_parameter",
        tool_name="get_s_parameter",
        message="当前为离线模式，无法导出 farfield ASCII",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    result = engine.attempt_recovery(agent, event)

    assert result.action_name == "dry_run_fallback"
    assert result.recovered is True
    assert result.details["dry_run"] is True


def test_farfield_monitor_recovery_triggers_vba() -> None:
    agent = _FakeAgent()
    event = FailureEvent(
        error_type=ErrorType.RESULT_READ,
        phase="phase:read_result",
        tool_name="read_result",
        message="未找到 farfield monitor",
        arguments={"item_path": "farfield"},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    with patch("cst_agent_workbench.cst.primitives.get_frequency_range", return_value={"fmin": "2.0", "fmax": "3.0"}), \
         patch("cst_agent_workbench.cst.primitives.register_farfield_monitor") as mock_register:
        result = engine.attempt_recovery(agent, event)

    assert result.action_name == "ensure_farfield_monitor"
    assert result.recovered is True
    assert len(agent.cst.execute_vba_calls) == 1
    assert mock_register.called


def test_missing_parameter_recovery_uses_current_model() -> None:
    agent = _FakeAgent()
    event = FailureEvent(
        error_type=ErrorType.PARAMETER_INVALID,
        phase="phase:store_parameter",
        tool_name="store_parameter",
        message="参数缺失",
        arguments={"name": "patch_L", "value": None},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    with patch("cst_agent_workbench.cst.primitives.get_parameters", return_value={"patch_L": "15.0"}):
        result = engine.attempt_recovery(agent, event)

    assert result.action_name == "repair_missing_parameter"
    assert result.recovered is True
    assert result.retry_arguments == {"name": "patch_L", "value": "15.0"}


def test_max_attempts_bounds_recovery() -> None:
    cst = _FakeCST(connected=False, offline=True, connect_success=False)
    agent = _FakeAgent(cst=cst)
    event = FailureEvent(
        error_type=ErrorType.CST_CONNECTION,
        phase="phase:check_cst_status",
        tool_name="check_cst_status",
        message="无法连接 CST",
        arguments={},
    )
    engine: FailureRecoveryEngine = agent._failure_recovery_engine

    # 连续失败的尝试有界：两次用尽预算后回落到 "none"。
    r1 = engine.attempt_recovery(agent, event)
    r2 = engine.attempt_recovery(agent, event)
    r3 = engine.attempt_recovery(agent, event)

    assert r1.action_name == "reconnect_cst"
    assert r1.recovered is False
    assert r2.action_name == "reconnect_cst"
    assert r2.recovered is False
    assert r3.action_name == "none"
    assert r3.recovered is False


def test_custom_recovery_action_can_be_registered() -> None:
    engine = FailureRecoveryEngine()
    seen: list[FailureEvent] = []

    def predicate(event: FailureEvent) -> bool:
        return event.error_type == ErrorType.UNKNOWN

    def run(agent, event: FailureEvent) -> RecoveryResult:
        seen.append(event)
        return RecoveryResult(
            action_name="custom_fallback",
            recovered=True,
            message="custom handled",
        )

    engine.register(RecoveryAction(
        name="custom_fallback",
        description="custom",
        predicate=predicate,
        run=run,
    ))
    engine.register_default_actions()

    event = FailureEvent(
        error_type=ErrorType.UNKNOWN,
        phase="phase:unknown",
        tool_name="unknown",
        message="weird failure",
        arguments={},
    )
    agent = _FakeAgent()
    result = engine.attempt_recovery(agent, event)

    assert result.action_name == "custom_fallback"
    assert result.recovered is True
    assert len(seen) == 1


def test_execute_tool_records_recovery_result_on_failure() -> None:
    """Integration test: execute_tool writes a recovery_result into the tool event."""
    agent = _FakeAgent(connected=False, results=_FakeResults(
        get_s_parameter_fn=_failing_then_successful_s_parameter("not connected to CST")
    ))
    agent.cst.connected = False

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)

    assert result["success"] is True
    assert result["recovery_result"]["action_name"] == "reconnect_cst"
    assert result["recovery_result"]["retry_success"] is True
    event = agent.tool_events[-1]
    assert event["tool_name"] == "get_s_parameter"
    assert event["success"] is True
    assert "recovery_result" in event
    assert event["recovery_result"]["action_name"] == "reconnect_cst"
    assert event["recovery_result"]["recovered"] is True
    assert event["recovery_result"]["retry_success"] is True
    assert agent.last_results["plot_data"] == [
        {"freq": 9.3, "s_db": -8.0},
        {"freq": 9.4, "s_db": -12.0},
    ]
    assert agent.session.memory.decisions.failure_reasons == []


def test_execute_tool_recovery_result_absent_when_no_engine() -> None:
    agent = _FakeAgent(results=_FakeResults(
        get_s_parameter_fn=lambda port_i, port_j: {
            "success": False,
            "message": "reader failed",
            "item": f"S{port_i},{port_j}",
        }
    ))
    agent._failure_recovery_engine = None  # type: ignore[assignment]

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)

    assert result["success"] is False
    event = agent.tool_events[-1]
    assert event["tool_name"] == "get_s_parameter"
    assert event["recovery_result"]["recovered"] is False
    assert event["recovery_result"]["reason"] == "no recovery engine attached"


def test_execute_tool_recovery_budget_resets_after_success() -> None:
    """M2：恢复成功即闭环。同一会话内多次"掉线→恢复成功"不会耗尽预算；
    连接本身的硬故障（重连失败）仍被 max_attempts 有界拦截。"""
    agent = _FakeAgent(connected=False, results=_FakeResults(
        get_s_parameter_fn=_failing_then_successful_s_parameter("无法连接 CST")
    ))
    agent.cst.connected = False

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    assert json.loads(result_text)["success"] is True
    # 恢复（重连）成功后计数器重置，不再永久累计。
    assert agent.session.metadata["failure_recovery_attempts"]["reconnect_cst"] == 0

    # 再来两次"掉线→恢复成功"：旧语义下这里已被历史成功耗尽（M2 回归）。
    for _ in range(2):
        agent.cst.connected = False
        agent.results = _FakeResults(
            get_s_parameter_fn=_failing_then_successful_s_parameter("无法连接 CST")
        )
        result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
        assert json.loads(result_text)["success"] is True

    # 硬故障：重连本身连续失败两次 → 预算耗尽 → 第三次不再自动重连。
    agent.cst.connect_success = False
    agent.cst.connected = False
    agent.results = _FakeResults(
        get_s_parameter_fn=lambda port_i, port_j: {
            "success": False,
            "message": "无法连接 CST",
            "item": f"S{port_i},{port_j}",
        }
    )
    execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    assert agent.session.metadata["failure_recovery_attempts"]["reconnect_cst"] == 2

    result_text = execute_tool(agent, "get_s_parameter", {"port_i": 1, "port_j": 1})
    result = json.loads(result_text)
    assert result["success"] is False
    event = agent.tool_events[-1]
    assert event["recovery_result"]["action_name"] == "none"
    assert event["recovery_result"]["recovered"] is False
