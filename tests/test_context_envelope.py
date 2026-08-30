from __future__ import annotations

from copy import deepcopy

import pytest

from cst_agent_workbench.agent.context_envelope import (
    CONTEXT_ENVELOPE_VERSION,
    ContextEnvelope,
    ContextEnvelopeError,
    build_context_envelope,
)


def test_context_envelope_round_trip_is_versioned_and_json_safe():
    messages = [{"role": "user", "content": "Run solver"}]
    tools = [{"type": "function", "function": {"name": "run_solver"}}]
    plan = {"current_step_id": "solve"}

    envelope = build_context_envelope(
        messages=messages,
        tools=tools,
        active_plan=plan,
        budget_metrics={"after_tokens": 120, "within_budget": True},
        max_context_tokens=6000,
        continuation_prompt="Continue safely.",
        optimization_mode=False,
    )
    payload = envelope.to_dict()
    parsed = ContextEnvelope.from_mapping(payload)

    assert payload["version"] == CONTEXT_ENVELOPE_VERSION
    assert parsed.to_dict() == payload
    assert payload["budget"]["max_context_tokens"] == 6000
    assert payload["execution"]["optimization_mode"] is False


def test_context_envelope_does_not_alias_mutable_host_state():
    messages = [{"role": "user", "content": "before"}]
    plan = {"steps": [{"step_id": "one"}]}
    original = deepcopy(plan)

    envelope = build_context_envelope(
        messages=messages,
        tools=[],
        active_plan=plan,
        budget_metrics={},
        max_context_tokens=100,
        continuation_prompt="continue",
        optimization_mode=True,
    )
    messages[0]["content"] = "after"
    plan["steps"].append({"step_id": "two"})

    payload = envelope.to_dict()
    assert payload["messages"][0]["content"] == "before"
    assert payload["plan"] == original


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 99, "messages": [], "tools": [], "plan": None, "budget": {}, "execution": {}},
        {"version": 1, "messages": {}, "tools": [], "plan": None, "budget": {}, "execution": {}},
        {"version": 1, "messages": [], "tools": ["bad"], "plan": None, "budget": {}, "execution": {}},
        {"version": 1, "messages": [], "tools": [], "plan": [], "budget": {}, "execution": {}},
    ],
)
def test_context_envelope_rejects_invalid_contracts(payload):
    with pytest.raises(ContextEnvelopeError):
        ContextEnvelope.from_mapping(payload)
