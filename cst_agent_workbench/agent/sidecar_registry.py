"""Persistent sidecar leases keyed by host session identity.

The registry owns worker reuse, liveness checks, generation fencing and
cancellation.  It deliberately stores no AgentSession, CST or tool state.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
)
from cst_agent_workbench.agent.harness_protocol import HarnessMessageType
from cst_agent_workbench.agent.sidecar_client import HarnessSidecarClient


class SidecarRegistryError(AgentLayerError):
    pass


def _registry_error(message: str, *, details: dict | None = None) -> SidecarRegistryError:
    return SidecarRegistryError(
        ErrorEnvelope(
            layer=ErrorLayer.HARNESS,
            code=HarnessErrorCode.PROTOCOL_ERROR.value,
            message=message,
            retryable=False,
            cause_type="SidecarRegistryError",
            details=dict(details or {}),
        )
    )


@dataclass(frozen=True)
class SidecarLease:
    client: HarnessSidecarClient
    session_id: str
    request_id: str
    generation: int


class SidecarSessionRegistry:
    """Serialize runs through one reusable, stateless sidecar worker."""

    def __init__(
        self,
        client_factory: Callable[[], HarnessSidecarClient],
        *,
        health_timeout_sec: float = 2.0,
    ) -> None:
        self._client_factory = client_factory
        self._health_timeout_sec = max(0.01, float(health_timeout_sec))
        self._run_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._client: HarnessSidecarClient | None = None
        self._generation = 0
        self._active: SidecarLease | None = None
        self._closed = False

    @property
    def generation(self) -> int:
        with self._state_lock:
            return self._generation

    @property
    def active_lease(self) -> SidecarLease | None:
        with self._state_lock:
            return self._active

    def acquire(self, *, session_id: str, request_id: str) -> SidecarLease:
        normalized_session = str(session_id or "").strip()
        normalized_request = str(request_id or "").strip()
        if not normalized_request:
            raise _registry_error("request_id is required for a sidecar lease")
        self._run_lock.acquire()
        try:
            with self._state_lock:
                if self._closed:
                    raise _registry_error("sidecar registry is closed")
                client = self._ensure_healthy_client()
                lease = SidecarLease(
                    client=client,
                    session_id=normalized_session,
                    request_id=normalized_request,
                    generation=self._generation,
                )
                self._active = lease
                return lease
        except Exception:
            self._run_lock.release()
            raise

    def release(self, lease: SidecarLease, *, reusable: bool) -> None:
        client_to_close: HarnessSidecarClient | None = None
        with self._state_lock:
            if self._active != lease:
                raise _registry_error(
                    "attempted to release a stale sidecar lease",
                    details={
                        "lease_generation": lease.generation,
                        "active_generation": self._active.generation if self._active else None,
                    },
                )
            self._active = None
            if not reusable or self._closed:
                client_to_close = self._client
                self._client = None
        try:
            if client_to_close is not None:
                client_to_close.close()
        finally:
            self._run_lock.release()

    @contextmanager
    def lease(self, *, session_id: str, request_id: str) -> Iterator[SidecarLease]:
        lease = self.acquire(session_id=session_id, request_id=request_id)
        reusable = True
        try:
            yield lease
        except Exception:
            reusable = False
            raise
        finally:
            self.release(lease, reusable=reusable)

    def cancel(self, *, session_id: str | None = None, reason: str = "host_cancelled") -> bool:
        return self._send_control(
            HarnessMessageType.ABORT,
            session_id=session_id,
            payload={"reason": str(reason or "host_cancelled")},
        )

    def steer(self, message: str, *, session_id: str | None = None) -> bool:
        normalized = str(message or "").strip()
        if not normalized:
            raise _registry_error("steering message must not be empty")
        return self._send_control(
            HarnessMessageType.STEER,
            session_id=session_id,
            payload={"message": normalized},
        )

    def follow_up(self, message: str, *, session_id: str | None = None) -> bool:
        normalized = str(message or "").strip()
        if not normalized:
            raise _registry_error("follow-up message must not be empty")
        return self._send_control(
            HarnessMessageType.FOLLOW_UP,
            session_id=session_id,
            payload={"message": normalized},
        )

    def _send_control(
        self,
        message_type: HarnessMessageType,
        *,
        session_id: str | None,
        payload: dict,
    ) -> bool:
        with self._state_lock:
            lease = self._active
            if lease is None:
                return False
            if session_id is not None and str(session_id) != lease.session_id:
                return False
            lease.client.send_message(
                message_type,
                request_id=lease.request_id,
                session_id=lease.session_id or None,
                payload=payload,
            )
            return True

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            lease = self._active
            client = self._client
            self._client = None
        if lease is not None:
            try:
                lease.client.send_message(
                    HarnessMessageType.ABORT,
                    request_id=lease.request_id,
                    session_id=lease.session_id or None,
                    payload={"reason": "registry_shutdown"},
                )
            except AgentLayerError:
                pass
        if client is not None:
            client.close()

    def _ensure_healthy_client(self) -> HarnessSidecarClient:
        client = self._client
        if client is not None:
            try:
                health = client.health(timeout_sec=self._health_timeout_sec)
                if health.ok:
                    return client
            except AgentLayerError:
                pass
            client.close()
            self._client = None

        client = self._client_factory()
        try:
            client.start()
            health = client.health(timeout_sec=self._health_timeout_sec)
            if not health.ok:
                raise _registry_error(
                    f"sidecar health check failed: {health.status}",
                    details=dict(health.details),
                )
        except Exception:
            client.close()
            raise
        self._generation += 1
        self._client = client
        return client
