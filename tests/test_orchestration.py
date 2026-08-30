"""Orchestration integration tests — all mocked, no real LLM/CST needed."""
from __future__ import annotations

import json
from unittest.mock import MagicMock


from cst_agent_workbench.agent.runtime import build_initial_plan, evaluate_replan_or_stop
from cst_agent_workbench.agent.session import AgentSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_valid_plan_json() -> str:
    """Return a valid Plan JSON string that satisfies llm_planner._validate_plan."""
    plan = {
        "intent_kind": "direct_action",
        "user_goal": "run simulation and read farfield",
        "steps": [
            {"step_id": "s1", "kind": "analyze", "title": "Analyse request", "expected_output": "identify action"},
            {"step_id": "s2", "kind": "tool", "title": "Execute tool", "expected_output": "tool result"},
            {"step_id": "s3", "kind": "respond", "title": "Reply", "expected_output": "answer"},
        ],
        "stop_condition": "action complete",
    }
    return json.dumps(plan)


def _make_mock_client(content: str) -> MagicMock:
    """Build a mock openai-compatible client that returns *content* as the LLM reply."""
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 20
    client.chat.completions.create.return_value = resp
    return client


def _make_session(session_id: str = "test-session") -> AgentSession:
    return AgentSession(session_id=session_id)


# ---------------------------------------------------------------------------
# Scenario 1: Planner → Executor full loop (valid LLM response)
# ---------------------------------------------------------------------------

class TestPlannerExecutorLoop:
    def test_build_initial_plan_sets_active_plan(self):
        session = _make_session()
        client = _make_mock_client(_make_valid_plan_json())

        plan = build_initial_plan(
            session=session,
            user_message="run simulation",
            client=client,
            model="gpt-mock",
        )

        assert plan is not None
        assert session.active_plan is not None
        assert session.active_plan is plan

    def test_intent_kind_direct_action(self):
        session = _make_session()
        client = _make_mock_client(_make_valid_plan_json())

        build_initial_plan(
            session=session,
            user_message="run simulation",
            client=client,
            model="gpt-mock",
        )

        intent = session.active_plan.get("intent", {})
        assert intent.get("kind") == "direct_action"

    def test_evaluate_replan_or_stop_returns_expected_keys(self):
        session = _make_session()
        client = _make_mock_client(_make_valid_plan_json())

        build_initial_plan(
            session=session,
            user_message="run simulation",
            client=client,
            model="gpt-mock",
        )

        result = evaluate_replan_or_stop(session=session)

        assert isinstance(result, dict)
        for key in ("continue", "stop", "needs_replan", "stop_reason", "replan_trigger"):
            assert key in result, f"missing key: {key}"

    def test_evaluate_replan_or_stop_no_failure_continues(self):
        session = _make_session()
        client = _make_mock_client(_make_valid_plan_json())

        build_initial_plan(
            session=session,
            user_message="run simulation",
            client=client,
            model="gpt-mock",
        )

        result = evaluate_replan_or_stop(session=session)

        assert result["continue"] is True
        assert result["stop"] is False
        assert result["needs_replan"] is False


# ---------------------------------------------------------------------------
# Scenario 2: Planner returns invalid JSON → fallback heuristic plan
# ---------------------------------------------------------------------------

class TestPlannerFallback:
    def test_invalid_json_triggers_fallback(self):
        session = _make_session()
        client = _make_mock_client("this is not json {{{}")

        plan = build_initial_plan(
            session=session,
            user_message="read results",
            client=client,
            model="gpt-mock",
        )

        # Fallback plan must still be created
        assert plan is not None
        assert session.active_plan is not None

    def test_fallback_plan_has_steps(self):
        session = _make_session()
        client = _make_mock_client("not-json")

        build_initial_plan(
            session=session,
            user_message="read results",
            client=client,
            model="gpt-mock",
        )

        steps = session.active_plan.get("steps", [])
        assert len(steps) >= 2
        # Last step must be respond
        assert steps[-1].get("kind") == "respond"

    def test_evaluate_replan_or_stop_works_after_fallback(self):
        session = _make_session()
        client = _make_mock_client("bad json")

        build_initial_plan(
            session=session,
            user_message="read results",
            client=client,
            model="gpt-mock",
        )

        result = evaluate_replan_or_stop(session=session)

        assert isinstance(result, dict)
        assert "needs_replan" in result

    def test_invalid_schema_plan_triggers_fallback(self):
        """LLM returns valid JSON but invalid schema (missing respond as last step)."""
        bad_plan = json.dumps({
            "intent_kind": "chat_task",
            "user_goal": "test",
            "steps": [
                {"step_id": "s1", "kind": "analyze", "title": "A", "expected_output": "x"},
                {"step_id": "s2", "kind": "tool", "title": "B", "expected_output": "y"},
            ],
            "stop_condition": "done",
        })
        session = _make_session()
        client = _make_mock_client(bad_plan)

        build_initial_plan(
            session=session,
            user_message="test",
            client=client,
            model="gpt-mock",
        )

        # Fallback heuristic plan's last step kind must be respond
        steps = session.active_plan.get("steps", [])
        assert steps[-1].get("kind") == "respond"


# ---------------------------------------------------------------------------
# Scenario 3: replan triggered by tool failure
# ---------------------------------------------------------------------------

class TestReplanTrigger:
    def _session_with_active_plan(self) -> AgentSession:
        session = _make_session("replan-session")
        # Build a valid heuristic plan (no LLM needed)
        build_initial_plan(
            session=session,
            user_message="run simulation",
        )
        return session

    def test_had_tool_failure_sets_needs_replan(self):
        session = self._session_with_active_plan()

        result = evaluate_replan_or_stop(
            session=session,
            had_tool_failure=True,
        )

        assert result["needs_replan"] is True

    def test_had_tool_failure_replan_trigger_value(self):
        session = self._session_with_active_plan()

        result = evaluate_replan_or_stop(
            session=session,
            had_tool_failure=True,
        )

        assert result["replan_trigger"] == "tool_failure"

    def test_had_tool_failure_continue_is_false(self):
        session = self._session_with_active_plan()

        result = evaluate_replan_or_stop(
            session=session,
            had_tool_failure=True,
        )

        assert result["continue"] is False

    def test_session_active_plan_updated_after_replan_eval(self):
        session = self._session_with_active_plan()

        evaluate_replan_or_stop(
            session=session,
            had_tool_failure=True,
        )

        # session.active_plan should be updated to reflect needs_replan
        assert session.active_plan is not None
        assert session.active_plan.get("needs_replan") is True


# ---------------------------------------------------------------------------
# Scenario 4: AgentSession memory structured validation
# ---------------------------------------------------------------------------

class TestAgentSessionMemory:
    def test_session_memory_user_goal_persists(self):
        session = _make_session("mem-session")
        session.memory.conversation.user_goal = "optimise antenna gain"

        assert session.memory.conversation.user_goal == "optimise antenna gain"

    def test_build_initial_plan_after_memory_set(self):
        session = _make_session("mem-session-2")
        session.memory.conversation.user_goal = "optimise antenna gain"

        plan = build_initial_plan(
            session=session,
            user_message="optimise antenna gain",
        )

        assert session.active_plan is not None
        assert plan is not None

    def test_memory_to_dict_structure(self):
        session = _make_session()
        session.memory.conversation.user_goal = "test goal"
        session.memory.workspace.project_path = "/tmp/proj"

        d = session.memory.to_dict()

        assert d["conversation"]["user_goal"] == "test goal"
        assert d["workspace"]["project_path"] == "/tmp/proj"

    def test_active_plan_not_none_after_heuristic_build(self):
        session = _make_session()
        session.memory.conversation.user_goal = "read farfield"

        build_initial_plan(
            session=session,
            user_message="read farfield",
        )

        assert session.active_plan is not None
        intent = session.active_plan.get("intent", {})
        assert intent.get("kind") in (
            "chat_task", "direct_action", "optimization_round", "continuous_optimization"
        )
