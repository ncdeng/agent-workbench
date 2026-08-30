import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from cst_agent_workbench.web.chat_routes import ChatControlRequest, register_chat_routes


class FakeApp:
    def __init__(self):
        self.routes = {}

    def get(self, path):
        return self._capture("GET", path)

    def post(self, path):
        return self._capture("POST", path)

    def _capture(self, method, path):
        def decorator(func):
            self.routes[(method, path)] = func
            return func

        return decorator


class FakeBrain:
    def __init__(self):
        self.calls = []

    def cancel(self, **kwargs):
        self.calls.append(("cancel", kwargs))
        return True

    def steer(self, message, **kwargs):
        self.calls.append(("steer", message, kwargs))
        return True

    def follow_up(self, message, **kwargs):
        self.calls.append(("follow_up", message, kwargs))
        return True


def _register(state):
    app = FakeApp()
    register_chat_routes(
        app,
        dry_run=True,
        get_app_state=lambda **_kwargs: state,
        operation_lock=asyncio.Lock(),
        run_exclusive=lambda *_args, **_kwargs: None,
        serialize_chat_history=lambda history: history,
        serialize_tool_event=lambda event: event,
        chat_status_payload=lambda *_args, **_kwargs: {},
        stream_produced_frames=lambda *_args, **_kwargs: None,
    )
    return app.routes[("POST", "/api/chat/control")]


def test_chat_control_routes_steering_follow_up_and_cancel_to_active_pi_run():
    brain = FakeBrain()
    state = SimpleNamespace(
        agent=SimpleNamespace(_pi_brain=brain),
        session=SimpleNamespace(session_id="session-a", metadata={}, history=[]),
    )
    control = _register(state)

    assert control(ChatControlRequest(action="steer", message="change target"))["accepted"] is True
    assert control(ChatControlRequest(action="follow_up", message="summarize"))["accepted"] is True
    assert control(ChatControlRequest(action="cancel"))["accepted"] is True

    assert brain.calls == [
        ("steer", "change target", {"session_id": "session-a"}),
        ("follow_up", "summarize", {"session_id": "session-a"}),
        ("cancel", {"session_id": "session-a", "reason": "user_cancelled"}),
    ]


def test_chat_control_rejects_native_brain_and_unknown_actions():
    state = SimpleNamespace(
        agent=SimpleNamespace(_pi_brain=None),
        session=SimpleNamespace(session_id="session-a", metadata={}, history=[]),
    )
    control = _register(state)
    with pytest.raises(HTTPException) as native_error:
        control(ChatControlRequest(action="cancel"))
    assert native_error.value.status_code == 409

    state.agent._pi_brain = FakeBrain()
    with pytest.raises(HTTPException) as action_error:
        control(ChatControlRequest(action="unknown"))
    assert action_error.value.status_code == 422
