"""Structured error contracts shared by Agent Harness implementations.

This module deliberately contains no retry, tracing, CST, session or tool
execution logic.  It gives each boundary a small JSON-serializable envelope so
callers can distinguish infrastructure failures from model decisions and tool
outcomes without parsing presentation text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class ErrorLayer(str, Enum):
    """The boundary that owns an error."""

    PROVIDER = "provider"
    HARNESS = "harness"
    TOOL = "tool"
    TASK = "task"
    UNKNOWN = "unknown"


class ProviderErrorCode(str, Enum):
    """Stable provider failure categories independent of SDK exception names."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    SERVER = "server"
    NETWORK = "network"
    BAD_REQUEST = "bad_request"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class HarnessErrorCode(str, Enum):
    """Stable failures owned by an executor Harness boundary."""

    INVALID_MESSAGE = "invalid_message"
    UNSUPPORTED_VERSION = "unsupported_version"
    UNKNOWN_MESSAGE_TYPE = "unknown_message_type"
    CORRELATION_MISMATCH = "correlation_mismatch"
    MISSING_CAPABILITY = "missing_capability"
    SIDECAR_UNAVAILABLE = "sidecar_unavailable"
    SIDECAR_TIMEOUT = "sidecar_timeout"
    SIDECAR_EXITED = "sidecar_exited"
    PROTOCOL_ERROR = "protocol_error"


@dataclass(frozen=True)
class ErrorEnvelope:
    """Portable error metadata for traces, APIs and evaluation reports."""

    layer: ErrorLayer
    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    request_id: str | None = None
    cause_type: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "layer": self.layer.value,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.status_code is not None:
            payload["status_code"] = self.status_code
        if self.request_id:
            payload["request_id"] = self.request_id
        if self.cause_type:
            payload["cause_type"] = self.cause_type
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class AgentLayerError(RuntimeError):
    """Base exception carrying a structured boundary error."""

    def __init__(self, envelope: ErrorEnvelope):
        super().__init__(envelope.message)
        self.envelope = envelope


class ProviderCallError(AgentLayerError):
    """Raised after a provider call is non-retryable or exhausts its policy."""
