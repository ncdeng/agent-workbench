from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cst_agent_workbench.agent.error_model import HarnessErrorCode
from cst_agent_workbench.agent.harness_protocol import (
    HarnessCapability,
    HarnessMessageType,
    ProtocolViolation,
)
from cst_agent_workbench.agent.sidecar_client import HarnessSidecarClient, SidecarClientError
from cst_agent_workbench.agent.sidecar_process import SidecarProcess, SidecarState

FIXTURE = Path(__file__).parent / "fixtures" / "harness_sidecar_fixture.py"
REQUIRED = {
    HarnessCapability.SEQUENTIAL_TOOLS,
    HarnessCapability.DYNAMIC_TOOL_CATALOG,
    HarnessCapability.STRUCTURED_ERRORS,
    HarnessCapability.HEALTHCHECK,
}


def _client(*, mode: str = "normal", required=REQUIRED) -> HarnessSidecarClient:
    command = [sys.executable, str(FIXTURE)]
    if mode != "normal":
        command.append(mode)
    return HarnessSidecarClient(
        SidecarProcess(command, shutdown_timeout_sec=0.5),
        required_capabilities=required,
        handshake_timeout_sec=1,
        request_id_factory=lambda: "fixed",
    )


def test_handshake_negotiates_capabilities_and_health():
    client = _client()
    try:
        handshake = client.start()
        health = client.health(timeout_sec=1)

        assert handshake.runtime_name == "fixture"
        assert handshake.runtime_version == "1"
        assert REQUIRED <= handshake.capabilities
        assert health.ok is True
        assert health.status == "ready"
        assert health.active_runs == 0
    finally:
        client.close()


def test_missing_capability_fails_and_closes_process():
    client = _client(required={*REQUIRED, HarnessCapability.TOKEN_STREAM})

    with pytest.raises(ProtocolViolation) as exc_info:
        client.start()

    assert exc_info.value.envelope.code == HarnessErrorCode.MISSING_CAPABILITY.value
    assert client.process.state == SidecarState.STOPPED


def test_version_mismatch_fails_and_closes_process():
    client = _client(mode="bad_version")

    with pytest.raises(ProtocolViolation) as exc_info:
        client.start()

    assert exc_info.value.envelope.code == HarnessErrorCode.UNSUPPORTED_VERSION.value
    assert client.process.state == SidecarState.STOPPED


def test_request_correlation_mismatch_fails_closed():
    client = _client(mode="wrong_request_id")

    with pytest.raises(ProtocolViolation) as exc_info:
        client.start()

    assert exc_info.value.envelope.code == HarnessErrorCode.CORRELATION_MISMATCH.value
    assert client.process.state == SidecarState.STOPPED


def test_health_before_handshake_is_rejected():
    client = _client()

    with pytest.raises(SidecarClientError, match="handshake has not completed"):
        client.health()


def test_versioned_run_messages_are_correlated_after_handshake():
    client = _client()
    try:
        client.start()
        client.send_message(
            HarnessMessageType.RUN,
            request_id="run-fixed",
            payload={"value": 7},
            session_id="session-fixed",
        )

        result = client.receive_message(
            timeout_sec=1,
            allowed_types={HarnessMessageType.RESULT},
            expected_request_id="run-fixed",
        )

        assert result.payload == {"ok": True, "echo": {"value": 7}}
    finally:
        client.close()


def test_context_manager_closes_sidecar():
    client = _client()

    with client:
        assert client.process.state == SidecarState.RUNNING

    assert client.process.state == SidecarState.STOPPED
    assert client.handshake is None
