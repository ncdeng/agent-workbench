from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
)
from cst_agent_workbench.agent.harness_protocol import HarnessCapability, HarnessMessageType
from cst_agent_workbench.agent.sidecar_client import HarnessSidecarClient
from cst_agent_workbench.agent.sidecar_process import SidecarProcess
from cst_agent_workbench.agent.sidecar_registry import SidecarRegistryError, SidecarSessionRegistry

FIXTURE = Path(__file__).parent / "fixtures" / "harness_sidecar_fixture.py"
REQUIRED = {
    HarnessCapability.SEQUENTIAL_TOOLS,
    HarnessCapability.DYNAMIC_TOOL_CATALOG,
    HarnessCapability.STRUCTURED_ERRORS,
    HarnessCapability.HEALTHCHECK,
}


class FakeClient:
    def __init__(self, *, fail_health_after: int | None = None):
        self.fail_health_after = fail_health_after
        self.start_count = 0
        self.health_count = 0
        self.close_count = 0
        self.sent = []

    def start(self):
        self.start_count += 1

    def health(self, *, timeout_sec):
        assert timeout_sec > 0
        self.health_count += 1
        if self.fail_health_after is not None and self.health_count > self.fail_health_after:
            raise AgentLayerError(
                ErrorEnvelope(
                    layer=ErrorLayer.HARNESS,
                    code=HarnessErrorCode.SIDECAR_EXITED.value,
                    message="worker exited",
                )
            )
        return SimpleNamespace(ok=True, status="idle", details={"ok": True})

    def send_message(self, message_type, *, request_id, payload, session_id=None):
        self.sent.append((message_type, request_id, payload, session_id))

    def close(self):
        self.close_count += 1


def test_registry_reuses_one_healthy_worker_across_sessions():
    client = FakeClient()
    registry = SidecarSessionRegistry(lambda: client)

    with registry.lease(session_id="session-a", request_id="request-a") as first:
        assert first.client is client
        assert first.generation == 1
    with registry.lease(session_id="session-b", request_id="request-b") as second:
        assert second.client is client
        assert second.generation == 1

    assert client.start_count == 1
    assert client.health_count == 2
    assert client.close_count == 0
    registry.close()
    assert client.close_count == 1


def test_registry_restarts_unhealthy_idle_worker_without_replaying_a_run():
    first_client = FakeClient(fail_health_after=1)
    second_client = FakeClient()
    clients = iter([first_client, second_client])
    registry = SidecarSessionRegistry(lambda: next(clients))

    with registry.lease(session_id="session-a", request_id="request-a") as first:
        assert first.generation == 1
    with registry.lease(session_id="session-a", request_id="request-b") as second:
        assert second.client is second_client
        assert second.generation == 2

    assert first_client.close_count == 1
    assert second_client.start_count == 1


def test_run_exception_invalidates_worker_and_next_run_gets_new_generation():
    first_client = FakeClient()
    second_client = FakeClient()
    clients = iter([first_client, second_client])
    registry = SidecarSessionRegistry(lambda: next(clients))

    with pytest.raises(RuntimeError, match="run failed"):
        with registry.lease(session_id="session-a", request_id="request-a"):
            raise RuntimeError("run failed")

    with registry.lease(session_id="session-a", request_id="request-b") as lease:
        assert lease.client is second_client
        assert lease.generation == 2

    assert first_client.close_count == 1


def test_cancel_is_correlated_to_the_active_session_and_request():
    client = FakeClient()
    registry = SidecarSessionRegistry(lambda: client)

    with registry.lease(session_id="session-a", request_id="request-a"):
        assert registry.cancel(session_id="session-b") is False
        assert registry.cancel(session_id="session-a", reason="user_stop") is True
        assert registry.steer("Do this instead", session_id="session-a") is True
        assert registry.follow_up("Then summarize", session_id="session-a") is True

    assert client.sent == [
        (
            HarnessMessageType.ABORT,
            "request-a",
            {"reason": "user_stop"},
            "session-a",
        ),
        (
            HarnessMessageType.STEER,
            "request-a",
            {"message": "Do this instead"},
            "session-a",
        ),
        (
            HarnessMessageType.FOLLOW_UP,
            "request-a",
            {"message": "Then summarize"},
            "session-a",
        ),
    ]


def test_empty_steering_and_follow_up_messages_are_rejected():
    registry = SidecarSessionRegistry(FakeClient)

    with pytest.raises(SidecarRegistryError, match="must not be empty"):
        registry.steer("  ")
    with pytest.raises(SidecarRegistryError, match="must not be empty"):
        registry.follow_up("")


def test_closed_registry_rejects_new_leases():
    registry = SidecarSessionRegistry(FakeClient)
    registry.close()

    with pytest.raises(SidecarRegistryError, match="closed"):
        registry.acquire(session_id="session-a", request_id="request-a")


def _real_client_factory(created, *, mode="normal"):
    def factory():
        command = [sys.executable, str(FIXTURE)]
        if mode != "normal":
            command.append(mode)
        client = HarnessSidecarClient(
            SidecarProcess(command, shutdown_timeout_sec=0.5),
            required_capabilities=REQUIRED,
            handshake_timeout_sec=1,
        )
        created.append(client)
        return client

    return factory


def _run_fixture(lease):
    lease.client.send_message(
        HarnessMessageType.RUN,
        request_id=lease.request_id,
        session_id=lease.session_id,
        payload={"value": lease.request_id},
    )
    return lease.client.receive_message(
        timeout_sec=1,
        allowed_types={HarnessMessageType.RESULT},
        expected_request_id=lease.request_id,
        expected_session_id=lease.session_id,
    )


def test_real_registry_reuses_the_same_process_for_multiple_runs():
    created = []
    registry = SidecarSessionRegistry(_real_client_factory(created))

    with registry.lease(session_id="session-a", request_id="request-a") as first:
        first_pid = first.client.process.pid
        assert _run_fixture(first).payload["ok"] is True
    with registry.lease(session_id="session-b", request_id="request-b") as second:
        assert second.client.process.pid == first_pid
        assert _run_fixture(second).payload["echo"] == {"value": "request-b"}

    assert len(created) == 1
    registry.close()


def test_real_registry_restarts_a_crashed_idle_process_on_next_run():
    created = []
    registry = SidecarSessionRegistry(_real_client_factory(created, mode="crash_after_run"))

    with registry.lease(session_id="session-a", request_id="request-a") as first:
        assert _run_fixture(first).payload["ok"] is True
    with registry.lease(session_id="session-a", request_id="request-b") as second:
        assert second.generation == 2

    assert len(created) == 2
    registry.close()
