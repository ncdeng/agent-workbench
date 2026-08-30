from __future__ import annotations

import sys
from pathlib import Path

import pytest

from cst_agent_workbench.agent.error_model import ErrorLayer, HarnessErrorCode
from cst_agent_workbench.agent.sidecar_process import (
    SidecarProcess,
    SidecarProcessError,
    SidecarState,
)

FIXTURE = Path(__file__).parent / "fixtures" / "harness_sidecar_fixture.py"


def _sidecar() -> SidecarProcess:
    return SidecarProcess([sys.executable, str(FIXTURE)], shutdown_timeout_sec=0.5)


def test_sidecar_round_trip_and_context_manager_cleanup():
    process = _sidecar()

    with process:
        assert process.state == SidecarState.RUNNING
        assert process.pid is not None
        process.send_json({"action": "echo", "value": "hello"})
        assert process.receive_json(timeout_sec=1) == {"ok": True, "value": "hello"}

    assert process.state == SidecarState.STOPPED


def test_invalid_json_is_structured_protocol_error():
    process = _sidecar()
    try:
        process.start()
        process.send_json({"action": "invalid_json"})
        with pytest.raises(SidecarProcessError) as exc_info:
            process.receive_json(timeout_sec=1)
        assert exc_info.value.envelope.layer == ErrorLayer.HARNESS
        assert exc_info.value.envelope.code == HarnessErrorCode.PROTOCOL_ERROR.value
        assert process.state == SidecarState.FAILED
    finally:
        process.terminate()


def test_non_object_json_is_rejected():
    process = _sidecar()
    try:
        process.start()
        process.send_json({"action": "array"})
        with pytest.raises(SidecarProcessError) as exc_info:
            process.receive_json(timeout_sec=1)
        assert exc_info.value.envelope.code == HarnessErrorCode.PROTOCOL_ERROR.value
        assert exc_info.value.envelope.details["value_type"] == "list"
    finally:
        process.terminate()


def test_receive_timeout_does_not_claim_process_exited():
    process = _sidecar()
    try:
        process.start()
        process.send_json({"action": "sleep", "seconds": 0.3})
        with pytest.raises(SidecarProcessError) as exc_info:
            process.receive_json(timeout_sec=0.02)
        assert exc_info.value.envelope.code == HarnessErrorCode.SIDECAR_TIMEOUT.value
        assert exc_info.value.envelope.retryable is False
        assert process.state == SidecarState.RUNNING
        assert process.receive_json(timeout_sec=1) == {"ok": True}
    finally:
        process.terminate()


def test_early_exit_includes_return_code_and_stderr():
    process = _sidecar()
    try:
        process.start()
        process.send_json({"action": "stderr_exit"})
        with pytest.raises(SidecarProcessError) as exc_info:
            process.receive_json(timeout_sec=1)
        envelope = exc_info.value.envelope
        assert envelope.code == HarnessErrorCode.SIDECAR_EXITED.value
        assert envelope.details["return_code"] == 7
        assert "fixture exploded" in envelope.details["stderr_tail"]
    finally:
        process.terminate()


def test_send_before_start_fails_closed():
    process = _sidecar()

    with pytest.raises(SidecarProcessError) as exc_info:
        process.send_json({"action": "echo"})

    assert exc_info.value.envelope.code == HarnessErrorCode.SIDECAR_EXITED.value


def test_terminate_is_idempotent():
    process = _sidecar()
    process.start()

    process.terminate()
    process.terminate()

    assert process.state == SidecarState.STOPPED
