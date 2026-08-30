from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent.tool_approval import ToolApprovalStore
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.model_ir import (
    AntennaModelIR,
    CompiledModelPlan,
    CompiledToolCall,
    ModelIRExecutionBusyError,
    ModelIRExecutionContractError,
    ParameterSpec,
    execute_compiled_model_plan,
    execute_model_ir,
)


class _Agent:
    def __init__(self, responses=None):
        self.session = SimpleNamespace(
            active_plan=None,
            metadata={},
            trace=SimpleNamespace(active_tool_call_id=None),
        )
        self.calls = []
        self.responses = list(responses or [])

    @property
    def _active_tool_call_id(self):
        return self.session.trace.active_tool_call_id

    @_active_tool_call_id.setter
    def _active_tool_call_id(self, value):
        self.session.trace.active_tool_call_id = value

    def _execute_tool(self, tool_name, arguments):
        self.calls.append(
            {
                "tool_name": tool_name,
                "arguments": arguments,
                "tool_call_id": self._active_tool_call_id,
                "active_plan": self.session.active_plan,
            }
        )
        response = self.responses.pop(0) if self.responses else {"success": True}
        if isinstance(response, Exception):
            raise response
        # Match the canonical Host runtime contract.
        self._active_tool_call_id = None
        return json.dumps(response)


def _plan(*calls: CompiledToolCall) -> CompiledModelPlan:
    return CompiledModelPlan("fixture", 3, calls)


def test_executor_delegates_ordered_repeated_calls_through_agent_entrypoint():
    agent = _Agent()
    plan = _plan(
        CompiledToolCall("store_parameter", {"name": "a", "value": "1"}, "parameters[0]"),
        CompiledToolCall("store_parameter", {"name": "b", "value": "2"}, "parameters[1]"),
        CompiledToolCall("set_frequency_range", {"fmin": "1", "fmax": "2"}, "solver"),
    )

    report = execute_compiled_model_plan(agent, plan, execution_id="exec-1")

    assert report.status == "configuration_completed"
    assert report.configuration_execution_success is True
    assert report.completed_call_count == 3
    assert [item["tool_name"] for item in agent.calls] == [
        "store_parameter",
        "store_parameter",
        "set_frequency_range",
    ]
    assert len({item["tool_call_id"] for item in agent.calls}) == 3
    assert [item.call_index for item in report.call_results] == [0, 1, 2]


def test_executor_installs_exact_allowlist_and_restores_outer_context():
    agent = _Agent()
    sentinel_plan = {"plan_id": "outer"}
    agent.session.active_plan = sentinel_plan
    agent._active_tool_call_id = "outer-call"
    plan = _plan(
        CompiledToolCall("set_units", {"geometry": "mm"}, "parameters[*].unit"),
        CompiledToolCall("set_units", {"geometry": "mm"}, "parameters[*].unit"),
    )

    execute_compiled_model_plan(agent, plan, execution_id="exec-scope")

    temporary = agent.calls[0]["active_plan"]
    step = temporary["steps"][0]
    assert step["allowed_tools"] == ["set_units"]
    assert step["completion_contract"] == "application_managed_call_sequence"
    assert agent.session.active_plan is sentinel_plan
    assert agent._active_tool_call_id == "outer-call"


def test_executor_stops_on_first_failure_and_restores_plan_after_exception():
    agent = _Agent([{"success": True}, RuntimeError("boom"), {"success": True}])
    sentinel_plan = {"plan_id": "outer"}
    agent.session.active_plan = sentinel_plan
    plan = _plan(
        CompiledToolCall("set_units", {}, "units"),
        CompiledToolCall("store_parameter", {}, "parameters[0]"),
        CompiledToolCall("create_brick", {}, "geometry[0]"),
    )

    report = execute_compiled_model_plan(agent, plan, execution_id="exec-fail")

    assert report.status == "configuration_failed"
    assert report.configuration_execution_success is False
    assert report.completed_call_count == 1
    assert report.failed_call_index == 1
    assert report.error_type == "RuntimeError"
    assert len(agent.calls) == 2
    assert agent.session.active_plan is sentinel_plan


def test_executor_rejects_noncompiler_and_approval_gated_tools_before_side_effects():
    agent = _Agent()
    plan = _plan(
        CompiledToolCall("set_units", {}, "units"),
        CompiledToolCall("execute_vba_script", {}, "fallback"),
    )

    with pytest.raises(ModelIRExecutionContractError, match="not emitted"):
        execute_compiled_model_plan(agent, plan, execution_id="exec-unsafe")
    assert agent.calls == []


def test_invalid_host_json_fails_closed_and_projection_stays_minimal():
    agent = _Agent()

    def invalid_result(tool_name, arguments):
        return "not-json"

    agent._execute_tool = invalid_result
    report = execute_compiled_model_plan(
        agent,
        _plan(CompiledToolCall("set_units", {}, "units")),
        execution_id="exec-invalid",
    )

    assert report.status == "configuration_failed"
    assert report.error_type == "invalid_host_result"
    assert agent.session.metadata["model_ir_execution"] == {
        "ir_id": "fixture",
        "revision": 3,
        "status": "configuration_failed",
        "last_execution_id": "exec-invalid",
    }


def test_compile_failure_is_reported_without_touching_host_runtime():
    agent = _Agent()
    draft = AntennaModelIR(
        ir_id="draft",
        title="Unconfirmed fixture",
        task_mode="requirement_synthesis",
        source_ids=("user-message-1",),
        parameters=(ParameterSpec("length", "10"),),
    )

    report = execute_model_ir(agent, draft, execution_id="exec-compile")

    assert report.status == "compile_failed"
    assert report.compile_success is False
    assert report.configuration_execution_success is None
    assert report.planned_call_count == 0
    assert agent.calls == []


def test_empty_compiled_configuration_is_a_successful_noop():
    agent = _Agent()

    report = execute_compiled_model_plan(agent, _plan(), execution_id="exec-empty")

    assert report.status == "configuration_completed"
    assert report.configuration_execution_success is True
    assert report.planned_call_count == 0
    assert report.completed_call_count == 0


def test_execution_id_cannot_be_reused_and_overwrite_tool_artifacts():
    agent = _Agent()
    plan = _plan(CompiledToolCall("set_units", {}, "units"))

    execute_compiled_model_plan(agent, plan, execution_id="exec-once")

    with pytest.raises(ModelIRExecutionContractError, match="already used"):
        execute_compiled_model_plan(agent, plan, execution_id="exec-once")
    assert len(agent.calls) == 1


def test_same_agent_reentrant_execution_fails_without_overwriting_context():
    entered = threading.Event()
    release = threading.Event()
    agent = _Agent()
    plan = _plan(CompiledToolCall("set_units", {}, "units"))

    def blocking_execute(tool_name, arguments):
        entered.set()
        assert release.wait(timeout=5)
        agent._active_tool_call_id = None
        return json.dumps({"success": True})

    agent._execute_tool = blocking_execute
    first_result = []
    thread = threading.Thread(
        target=lambda: first_result.append(
            execute_compiled_model_plan(agent, plan, execution_id="exec-thread-1")
        )
    )
    thread.start()
    assert entered.wait(timeout=5)

    with pytest.raises(ModelIRExecutionBusyError, match="already active"):
        execute_compiled_model_plan(agent, plan, execution_id="exec-thread-2")

    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert first_result[0].status == "configuration_completed"
    # A busy attempt releases its reservation because it executed no side effect.
    agent._execute_tool = lambda tool_name, arguments: json.dumps({"success": True})
    assert execute_compiled_model_plan(
        agent, plan, execution_id="exec-thread-2"
    ).status == "configuration_completed"


class _RuntimeAgent(_Agent):
    def __init__(self):
        super().__init__()
        self.session.artifacts = SimpleNamespace(tool_results={})
        self.session.tool_approvals = ToolApprovalStore()
        self.tool_events = []
        self.last_tool_message = ""
        self.trace_enabled = False
        self.current_trace = None

    def _get_phase(self, tool_name):
        return f"phase:{tool_name}"

    def _execute_tool(self, tool_name, arguments):
        return execute_tool(self, tool_name, arguments)


def test_executor_integrates_with_canonical_host_allowlist_trace_id_and_artifact_store(monkeypatch):
    agent = _RuntimeAgent()
    dispatched = []

    def fake_dispatch(subject, tool_name, arguments):
        dispatched.append((tool_name, arguments, subject.session.active_plan))
        return json.dumps(
            {"success": True, "message": f"configured {tool_name}", "executed": True}
        )

    monkeypatch.setattr(
        "cst_agent_workbench.agent.tool_runtime.do_execute_tool", fake_dispatch
    )
    plan = _plan(
        CompiledToolCall("set_units", {"geometry": "mm", "frequency": "GHz", "time": "ns"}, "units"),
        CompiledToolCall("store_parameter", {"name": "length", "value": "10"}, "parameters[0]"),
    )

    report = execute_compiled_model_plan(agent, plan, execution_id="exec-host")

    assert report.status == "configuration_completed"
    assert [item[0] for item in dispatched] == ["set_units", "store_parameter"]
    assert [event["tool_name"] for event in agent.tool_events] == [
        "set_units",
        "store_parameter",
    ]
    assert sorted(agent.session.artifacts.tool_results) == [
        "exec-host:call:0000",
        "exec-host:call:0001",
    ]
    assert all(
        item.result["full_payload_ref"].startswith(
            "session.artifacts.tool_results.exec-host:call:"
        )
        for item in report.call_results
    )
    assert agent._active_tool_call_id is None
