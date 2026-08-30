"""Protocol-aware client composed over :mod:`sidecar_process`.

The client owns the versioned wire envelope plus handshake and health
semantics.  Pi run events, CST tools and AgentSession state remain
responsibilities of higher-level adapters.
"""

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Collection, Mapping

from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
)
from cst_agent_workbench.agent.harness_protocol import (
    PROTOCOL_VERSION,
    HarnessCapability,
    HarnessMessageType,
    ProtocolMessage,
    make_message,
    negotiate_capabilities,
    parse_message,
)
from cst_agent_workbench.agent.sidecar_process import SidecarProcess


@dataclass(frozen=True)
class SidecarHandshake:
    runtime_name: str
    runtime_version: str
    protocol_version: int
    capabilities: frozenset[HarnessCapability]


@dataclass(frozen=True)
class SidecarHealth:
    ok: bool
    status: str
    active_runs: int
    details: Mapping[str, Any]


class SidecarClientError(AgentLayerError):
    pass


def _client_error(message: str, *, details: Mapping[str, Any] | None = None) -> SidecarClientError:
    return SidecarClientError(
        ErrorEnvelope(
            layer=ErrorLayer.HARNESS,
            code=HarnessErrorCode.PROTOCOL_ERROR.value,
            message=message,
            retryable=False,
            cause_type="SidecarClientError",
            details=dict(details or {}),
        )
    )


class HarnessSidecarClient:
    def __init__(
        self,
        process: SidecarProcess,
        *,
        required_capabilities: Collection[HarnessCapability],
        handshake_timeout_sec: float = 5.0,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.process = process
        self.required_capabilities = frozenset(required_capabilities)
        self.handshake_timeout_sec = max(0.01, float(handshake_timeout_sec))
        self.request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)
        self.handshake: SidecarHandshake | None = None

    def start(self) -> SidecarHandshake:
        self.process.start()
        try:
            request_id = self._request_id("hello")
            hello = make_message(
                HarnessMessageType.HELLO,
                request_id=request_id,
                payload={
                    "host": {"name": "cst-agent-python", "version": platform.python_version()},
                    "protocol_versions": [PROTOCOL_VERSION],
                    "required_capabilities": sorted(item.value for item in self.required_capabilities),
                },
            )
            self.process.send_json(hello.to_dict())
            reply = parse_message(
                self.process.receive_json(timeout_sec=self.handshake_timeout_sec),
                allowed_types={HarnessMessageType.HELLO_ACK},
                expected_request_id=request_id,
            )
            runtime = reply.payload.get("runtime")
            if not isinstance(runtime, Mapping):
                raise _client_error("hello_ack payload must include a runtime object")
            capabilities = negotiate_capabilities(
                required=self.required_capabilities,
                offered=reply.payload.get("capabilities") or [],
            )
            self.handshake = SidecarHandshake(
                runtime_name=str(runtime.get("name") or "unknown"),
                runtime_version=str(runtime.get("version") or "unknown"),
                protocol_version=reply.protocol_version,
                capabilities=capabilities,
            )
            return self.handshake
        except Exception:
            self.process.terminate()
            raise

    def health(self, *, timeout_sec: float = 2.0) -> SidecarHealth:
        if self.handshake is None:
            raise _client_error("sidecar handshake has not completed")
        if HarnessCapability.HEALTHCHECK not in self.handshake.capabilities:
            raise _client_error("sidecar did not negotiate the healthcheck capability")
        request_id = self._request_id("health")
        self.process.send_json(
            make_message(
                HarnessMessageType.HEALTH,
                request_id=request_id,
                payload={},
            ).to_dict()
        )
        reply = parse_message(
            self.process.receive_json(timeout_sec=timeout_sec),
            allowed_types={HarnessMessageType.HEALTH_RESULT},
            expected_request_id=request_id,
        )
        ok = bool(reply.payload.get("ok"))
        status = str(reply.payload.get("status") or ("ready" if ok else "unhealthy"))
        active_runs = int(reply.payload.get("active_runs") or 0)
        return SidecarHealth(
            ok=ok,
            status=status,
            active_runs=active_runs,
            details=dict(reply.payload),
        )

    def send_message(
        self,
        message_type: HarnessMessageType,
        *,
        request_id: str,
        payload: Mapping[str, Any] | None = None,
        session_id: str | None = None,
    ) -> None:
        """Send one versioned message after capability negotiation."""

        if self.handshake is None:
            raise _client_error("sidecar handshake has not completed")
        self.process.send_json(
            make_message(
                message_type,
                request_id=request_id,
                payload=payload,
                session_id=session_id,
            ).to_dict()
        )

    def receive_message(
        self,
        *,
        timeout_sec: float,
        allowed_types: Collection[HarnessMessageType],
        expected_request_id: str,
        expected_session_id: str | None = None,
    ) -> ProtocolMessage:
        """Receive and validate one correlated versioned message."""

        if self.handshake is None:
            raise _client_error("sidecar handshake has not completed")
        return parse_message(
            self.process.receive_json(timeout_sec=timeout_sec),
            allowed_types=allowed_types,
            expected_request_id=expected_request_id,
            expected_session_id=expected_session_id,
        )

    def close(self) -> None:
        self.process.terminate()
        self.handshake = None

    def _request_id(self, prefix: str) -> str:
        value = str(self.request_id_factory() or "").strip()
        if not value:
            raise _client_error("request_id_factory returned an empty value")
        return f"{prefix}-{value}"

    def __enter__(self) -> "HarnessSidecarClient":
        self.start()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()
