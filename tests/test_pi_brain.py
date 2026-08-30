import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent import runtime
from cst_agent_workbench.agent.error_model import HarnessErrorCode
from cst_agent_workbench.agent.harness_protocol import HarnessMessageType, make_message
from cst_agent_workbench.agent.pi_brain import PiAgentBrain, PiBrainError
from cst_agent_workbench.agent.runtime import ToolLoopResult
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.agent.agent import CSTAgent
from fakes import FakeCSTController


REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR = REPO_ROOT / "integrations" / "pi_agent_core" / "sidecar.mjs"


def _tool(name="check_cst_status"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test tool",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }


def _brain(
    *,
    scripted_responses,
    sidecar_path=SIDECAR,
    timeout_sec=10,
    sidecar_client_factory=None,
    api_protocol="chat_completions",
    prompt_cache_enabled=True,
    prompt_cache_ttl="5m",
):
    return PiAgentBrain(
        node_executable="node",
        sidecar_path=sidecar_path,
        timeout_sec=timeout_sec,
        api_key="test-key",
        base_url="",
        model="faux",
        context_window=128000,
        max_output_tokens=1024,
        api_protocol=api_protocol,
        prompt_cache_enabled=prompt_cache_enabled,
        prompt_cache_ttl=prompt_cache_ttl,
        scripted_responses=scripted_responses,
        sidecar_client_factory=sidecar_client_factory,
    )


def test_pi_brain_runs_restricted_tool_loop_and_projects_host_history():
    brain = _brain(
        scripted_responses=[
            {
                "tool_call": {"id": "call-1", "name": "check_cst_status", "arguments": {}},
                "stop_reason": "toolUse",
            },
            {"text": "CST is connected.", "stop_reason": "stop"},
        ]
    )
    working = [
        {"role": "system", "content": "Use the host tools."},
        {"role": "user", "content": "Check CST."},
    ]
    pending = [{"role": "user", "content": "Check CST."}]
    token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    trace_turns = []
    execution_order = []

    def execute_tool(name, arguments):
        execution_order.append(("execute", name, arguments))
        return json.dumps({"success": True, "connected": True})

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool()],
        working_messages=working,
        pending_history=pending,
        execute_tool=execute_tool,
        token_stats=token_stats,
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=4,
        trace_turn_callback=trace_turns.append,
        trace_tool_context_callback=lambda call_id, name: execution_order.append(("trace", call_id, name)),
    )

    assert result.ok is True
    assert result.final_mode == "pi_harness"
    assert result.executed_tool_names == ["check_cst_status"]
    assert [message["role"] for message in pending] == ["user", "assistant", "tool", "assistant"]
    assert pending[-1]["content"] == "CST is connected."
    assert execution_order[0] == ("trace", "call-1", "check_cst_status")
    assert execution_order[1] == ("execute", "check_cst_status", {})
    assert token_stats["calls"] == 2
    assert len(trace_turns) == 2
    assert trace_turns[0]["tool_calls_raw"][0]["function"]["name"] == "check_cst_status"


@pytest.mark.parametrize("api_protocol", ["chat_completions", "responses", "anthropic_messages"])
def test_pi_sidecar_accepts_all_supported_model_protocols_offline(api_protocol):
    brain = _brain(
        api_protocol=api_protocol,
        scripted_responses=[{"text": "ok", "stop_reason": "stop"}],
    )

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool()],
        working_messages=[{"role": "user", "content": "Check CST."}],
        pending_history=[],
        execute_tool=lambda _name, _args: json.dumps({"success": True}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=2,
    )

    assert result.ok is True
    assert result.final_text == "ok"
    brain.close()


def test_pi_sidecar_preserves_provider_cache_usage_in_trace_and_cumulative_stats():
    brain = _brain(
        api_protocol="responses",
        scripted_responses=[
            {
                "text": "cached",
                "stop_reason": "stop",
            }
        ],
    )
    token_stats = {"prompt": 0, "completion": 0, "calls": 0}
    trace_turns = []

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool()],
        working_messages=[{"role": "user", "content": "Check CST."}],
        pending_history=[],
        execute_tool=lambda _name, _args: json.dumps({"success": True}),
        token_stats=token_stats,
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=2,
        trace_turn_callback=trace_turns.append,
    )

    assert result.ok is True
    usage = trace_turns[0]["usage"]
    assert token_stats["prompt"] == usage["prompt_tokens"]
    assert token_stats["completion"] == usage["completion_tokens"]
    assert token_stats["cached"] == usage["cached_tokens"]
    assert token_stats["cache_write"] == usage["cache_write_tokens"]
    assert token_stats["calls"] == 1
    assert usage["cached_tokens"] + usage["cache_write_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]
    brain.close()


def test_pi_brain_keeps_failed_tool_observation_in_host_history():
    brain = _brain(
        scripted_responses=[
            {
                "tool_call": {"id": "call-fail", "name": "check_cst_status", "arguments": {}},
                "stop_reason": "toolUse",
            },
            {"text": "CST status check failed.", "stop_reason": "stop"},
        ]
    )
    working = [{"role": "user", "content": "Check CST."}]
    pending = [{"role": "user", "content": "Check CST."}]

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool()],
        working_messages=working,
        pending_history=pending,
        execute_tool=lambda _name, _args: json.dumps({"success": False, "message": "offline"}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=4,
    )

    assert result.ok is True
    assert result.had_tool_failure is True
    assert result.successful_tool_names == []
    assert result.approval_pending is False
    assert json.loads(pending[2]["content"])["success"] is False


def test_pi_loop_preserves_host_approval_required_semantics():
    brain = _brain(
        scripted_responses=[
            {
                "tool_call": {
                    "id": "call-approval",
                    "name": "execute_vba_script",
                    "arguments": {"vba_code": "Sub Main()\nEnd Sub", "description": "probe"},
                },
                "stop_reason": "toolUse",
            },
            {"text": "Approval is required.", "stop_reason": "stop"},
        ]
    )
    pending = [{"role": "user", "content": "Run raw VBA."}]
    approval_payload = {
        "success": False,
        "message": "approval required",
        "error_type": "approval_required",
        "approval_request": {"request_id": "req-pi", "tool_name": "execute_vba_script"},
    }

    raw_vba_tool = {
        "type": "function",
        "function": {
            "name": "execute_vba_script",
            "description": "Execute raw VBA after Host approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vba_code": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["vba_code", "description"],
                "additionalProperties": False,
            },
        },
    }

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[raw_vba_tool],
        working_messages=[{"role": "user", "content": "Run raw VBA."}],
        pending_history=pending,
        execute_tool=lambda _name, _args: json.dumps(approval_payload),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=4,
    )

    assert result.had_tool_failure is False
    assert result.successful_tool_names == []
    assert result.approval_pending is True
    assert json.loads(pending[2]["content"])["error_type"] == "approval_required"


def test_pi_brain_refreshes_host_filtered_catalog_after_each_tool_batch():
    brain = _brain(
        scripted_responses=[
            {
                "tool_call": {"id": "call-analyze", "name": "check_cst_status", "arguments": {}},
                "stop_reason": "toolUse",
            },
            {
                "tool_call": {"id": "call-solve", "name": "run_solver", "arguments": {}},
                "stop_reason": "toolUse",
            },
            {"text": "Solver finished.", "stop_reason": "stop"},
        ]
    )
    session = AgentSession(session_id="pi-stepwise")
    session.active_plan = {
        "plan_id": "stepwise",
        "intent": {"kind": "direct_action", "user_goal": "run", "constraints": []},
        "current_step_id": "analyze",
        "status": "active",
        "steps": [
            {
                "step_id": "analyze",
                "kind": "analyze",
                "status": "in_progress",
                "allowed_tools": ["check_cst_status"],
            },
            {
                "step_id": "tool",
                "kind": "tool",
                "status": "pending",
                "allowed_tools": ["run_solver"],
            },
            {
                "step_id": "respond",
                "kind": "respond",
                "status": "pending",
                "allowed_tools": ["recall_tool_result"],
            },
        ],
    }
    executed = []

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool("check_cst_status"), _tool("run_solver"), _tool("recall_tool_result")],
        working_messages=[{"role": "user", "content": "Run solver."}],
        pending_history=[{"role": "user", "content": "Run solver."}],
        execute_tool=lambda name, _args: executed.append(name) or json.dumps({"success": True}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=5,
        session=session,
        tool_memory_query="Run solver.",
    )

    assert result.ok is True
    assert executed == ["check_cst_status", "run_solver"]
    assert result.executed_tool_names == executed
    event_types = [event["type"] for event in session.metadata["harness_events"]]
    assert event_types[0:2] == ["context_prepared", "run_started"]
    assert "model_turn" in event_types
    assert "tool_requested" in event_types
    assert "tool_completed" in event_types
    assert "plan_updated" in event_types
    assert "tool_catalog_updated" in event_types
    assert event_types[-2:] == ["run_result", "run_finished"]


@pytest.mark.parametrize(
    ("fixture_name", "timeout_sec", "error_fragment", "expected_code"),
    [
        ("pi_sidecar_timeout.mjs", 1, "超时", HarnessErrorCode.SIDECAR_TIMEOUT.value),
        ("pi_sidecar_malformed.mjs", 5, "非法 JSONL", HarnessErrorCode.PROTOCOL_ERROR.value),
    ],
)
def test_pi_brain_fails_explicitly_on_sidecar_protocol_errors(
    fixture_name,
    timeout_sec,
    error_fragment,
    expected_code,
):
    brain = _brain(
        scripted_responses=[],
        sidecar_path=REPO_ROOT / "tests" / "fixtures" / fixture_name,
        timeout_sec=timeout_sec,
    )
    with pytest.raises(PiBrainError, match=error_fragment) as exc_info:
        brain.run_tool_loop(
            client=None,
            model="faux",
            tools=[_tool()],
            working_messages=[{"role": "user", "content": "Check CST."}],
            pending_history=[],
            execute_tool=lambda _name, _args: json.dumps({"success": True}),
            token_stats={"prompt": 0, "completion": 0, "calls": 0},
            offline_notice="",
            optimization_mode=False,
            max_tool_iterations=2,
        )

    assert exc_info.value.envelope.code == expected_code
    assert exc_info.value.envelope.layer.value == "harness"


def test_pi_brain_uses_injected_protocol_client_and_closes_it():
    class FakeSidecarClient:
        def __init__(self):
            self.started = False
            self.closed = False
            self.sent = []
            self.request_id = ""

        def start(self):
            self.started = True

        def health(self, *, timeout_sec):
            assert timeout_sec > 0
            return SimpleNamespace(ok=True, status="ready", details={"ok": True})

        def send_message(self, message_type, *, request_id, payload, session_id=None):
            self.sent.append((message_type, request_id, payload, session_id))
            if message_type == HarnessMessageType.RUN:
                self.request_id = request_id

        def receive_message(
            self,
            *,
            timeout_sec,
            allowed_types,
            expected_request_id,
            expected_session_id=None,
        ):
            assert timeout_sec > 0
            assert HarnessMessageType.RESULT in allowed_types
            assert expected_request_id == self.request_id
            assert expected_session_id is None
            return make_message(
                HarnessMessageType.RESULT,
                request_id=expected_request_id,
                payload={"ok": True, "final_text": "done", "stop_reason": "stop"},
            )

        def close(self):
            self.closed = True

    fake = FakeSidecarClient()
    brain = _brain(
        scripted_responses=[],
        sidecar_client_factory=lambda: fake,
        api_protocol="responses",
        prompt_cache_enabled=False,
        prompt_cache_ttl="1h",
    )

    result = brain.run_tool_loop(
        client=None,
        model="faux",
        tools=[_tool()],
        working_messages=[{"role": "user", "content": "Check CST."}],
        pending_history=[],
        execute_tool=lambda _name, _args: json.dumps({"success": True}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        offline_notice="",
        optimization_mode=False,
        max_tool_iterations=2,
    )

    assert result.ok is True
    assert result.final_text == "done"
    assert fake.started is True
    assert fake.closed is False
    assert [item[0] for item in fake.sent] == [HarnessMessageType.RUN]
    run_payload = fake.sent[0][2]
    assert run_payload["context"]["version"] == 1
    assert run_payload["context"]["messages"][-1]["content"] == "Check CST."
    assert "messages" not in run_payload
    assert run_payload["model"]["api"] == "openai-responses"
    assert run_payload["model"]["prompt_cache_enabled"] is False
    assert run_payload["model"]["prompt_cache_ttl"] == "1h"
    brain.close()
    assert fake.closed is True


def test_pi_brain_reuses_one_persistent_sidecar_for_multiple_runs():
    brain = _brain(scripted_responses=[{"text": "done", "stop_reason": "stop"}])

    for index in range(2):
        result = brain.run_tool_loop(
            client=None,
            model="faux",
            tools=[_tool()],
            working_messages=[{"role": "user", "content": f"Run {index}."}],
            pending_history=[],
            execute_tool=lambda _name, _args: json.dumps({"success": True}),
            token_stats={"prompt": 0, "completion": 0, "calls": 0},
            offline_notice="",
            optimization_mode=False,
            max_tool_iterations=2,
        )
        assert result.ok is True
        assert result.final_text == "done"

    assert brain._sidecar_registry.generation == 1
    brain.close()


def test_run_agent_turn_uses_injected_loop_for_same_turn_replan(monkeypatch):
    calls = []

    def loop_runner(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ToolLoopResult("first", "pi_harness", False, "first", False, ["check_cst_status"])
        return ToolLoopResult("second", "pi_harness", True, "", False, ["run_solver"])

    monkeypatch.setattr(runtime, "update_plan_after_turn", lambda **_kwargs: None)
    evaluations = iter(
        [
            {"needs_replan": True, "replanned_via_llm": True},
            {"needs_replan": False},
        ]
    )
    monkeypatch.setattr(runtime, "evaluate_optimization_next_action", lambda **_kwargs: next(evaluations))
    monkeypatch.setattr(runtime.config, "AGENT_MAX_REPLAN_RETRIES", 1)
    session = AgentSession(session_id="pi-replan")

    result = runtime.run_agent_turn(
        session=session,
        client=None,
        model="faux",
        user_message="solve",
        working_messages=[{"role": "user", "content": "solve"}],
        pending_history=[],
        tools=[_tool()],
        execute_tool_fn=lambda _name, _args: json.dumps({"success": True}),
        token_stats={"prompt": 0, "completion": 0, "calls": 0},
        loop_runner=loop_runner,
    )

    assert len(calls) == 2
    assert result.final_text == "second"
    assert result.executed_tool_names == ["check_cst_status", "run_solver"]
    assert result.plan_eval["same_turn_replan_retry"] is True


def test_cst_agent_chat_can_select_pi_brain_without_bypassing_host_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr("cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR", str(tmp_path / "memory"))
    monkeypatch.setattr("cst_agent_workbench.agent.agent.config.AGENT_BRAIN", "pi")
    monkeypatch.setattr("cst_agent_workbench.agent.agent.config.PI_API_KEY", "test-key")
    agent = CSTAgent(FakeCSTController())
    agent._pi_brain = _brain(
        scripted_responses=[
            {
                "tool_call": {"id": "call-chat", "name": "check_cst_status", "arguments": {}},
                "stop_reason": "toolUse",
            },
            {"text": "Host status inspected.", "stop_reason": "stop"},
        ]
    )
    monkeypatch.setattr(agent, "_try_short_circuit_response", lambda **_kwargs: None)

    response = agent.chat("Inspect the current runtime with the available host tool.", skip_plan=True)

    assert response == "Host status inspected."
    assert agent.session.metadata["agent_brain"] == "pi"
    assert agent.last_chat_status["mode"] == "pi_harness"
    assert [event["tool_name"] for event in agent.tool_events] == ["check_cst_status"]
    assert agent.trace_history[-1]["status"] == "completed"
