"""Versioned host/sidecar protocol contract for executor Harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Collection, Mapping

from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
)

PROTOCOL_NAME = "cst-agent-harness"
PROTOCOL_VERSION = 1


class HarnessMessageType(str, Enum):
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    HEALTH = "health"
    HEALTH_RESULT = "health_result"
    RUN = "run"
    ABORT = "abort"
    STEER = "steer"
    FOLLOW_UP = "follow_up"
    TOOL_RESULT = "tool_result"
    TURN_UPDATE = "turn_update"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_REQUEST = "tool_request"
    TOOL_BATCH_END = "tool_batch_end"
    PREPARE_NEXT_TURN = "prepare_next_turn"
    RESULT = "result"
    ERROR = "error"


class HarnessCapability(str, Enum):
    SEQUENTIAL_TOOLS = "sequential_tools"
    DYNAMIC_TOOL_CATALOG = "dynamic_tool_catalog"
    STRUCTURED_ERRORS = "structured_errors"
    HEALTHCHECK = "healthcheck"
    CANCEL = "cancel"
    SESSION_RESUME = "session_resume"
    TOKEN_STREAM = "token_stream"
    STEERING = "steering"
    FOLLOW_UP = "follow_up"


@dataclass(frozen=True)
class ProtocolMessage:
    type: HarnessMessageType
    request_id: str
    payload: Mapping[str, Any]
    session_id: str | None = None
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = {
            "protocol": {"name": PROTOCOL_NAME, "version": self.protocol_version},
            "type": self.type.value,
            "request_id": self.request_id,
            "payload": dict(self.payload),
        }
        if self.session_id:
            value["session_id"] = self.session_id
        return value


class ProtocolViolation(AgentLayerError):
    pass


def _violation(
    code: HarnessErrorCode,
    message: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> ProtocolViolation:
    return ProtocolViolation(
        ErrorEnvelope(
            layer=ErrorLayer.HARNESS,
            code=code.value,
            message=message,
            retryable=False,
            cause_type="ProtocolViolation",
            details=dict(details or {}),
        )
    )


def make_message(
    message_type: HarnessMessageType,
    *,
    request_id: str,
    payload: Mapping[str, Any] | None = None,
    session_id: str | None = None,
) -> ProtocolMessage:
    request_id = str(request_id or "").strip()
    if not request_id:
        raise _violation(HarnessErrorCode.INVALID_MESSAGE, "request_id is required")
    return ProtocolMessage(
        type=message_type,
        request_id=request_id,
        payload=dict(payload or {}),
        session_id=str(session_id).strip() if session_id else None,
    )


def parse_message(
    raw: Mapping[str, Any],
    *,
    allowed_types: Collection[HarnessMessageType] | None = None,
    expected_request_id: str | None = None,
    expected_session_id: str | None = None,
) -> ProtocolMessage:
    if not isinstance(raw, Mapping):
        raise _violation(HarnessErrorCode.INVALID_MESSAGE, "protocol message must be an object")
    protocol = raw.get("protocol")
    if not isinstance(protocol, Mapping) or protocol.get("name") != PROTOCOL_NAME:
        raise _violation(
            HarnessErrorCode.INVALID_MESSAGE,
            "protocol name is missing or invalid",
            details={"expected": PROTOCOL_NAME},
        )
    version = protocol.get("version")
    if version != PROTOCOL_VERSION:
        raise _violation(
            HarnessErrorCode.UNSUPPORTED_VERSION,
            f"unsupported Harness protocol version: {version!r}",
            details={"supported_versions": [PROTOCOL_VERSION], "received_version": version},
        )
    try:
        message_type = HarnessMessageType(str(raw.get("type") or ""))
    except ValueError as exc:
        raise _violation(
            HarnessErrorCode.UNKNOWN_MESSAGE_TYPE,
            f"unknown Harness message type: {raw.get('type')!r}",
        ) from exc
    if allowed_types is not None and message_type not in allowed_types:
        raise _violation(
            HarnessErrorCode.UNKNOWN_MESSAGE_TYPE,
            f"message type {message_type.value!r} is not valid in the current state",
            details={"allowed_types": sorted(item.value for item in allowed_types)},
        )
    request_id = str(raw.get("request_id") or "").strip()
    if not request_id:
        raise _violation(HarnessErrorCode.INVALID_MESSAGE, "request_id is required")
    if expected_request_id is not None and request_id != expected_request_id:
        raise _violation(
            HarnessErrorCode.CORRELATION_MISMATCH,
            f"response request_id {request_id!r} does not match {expected_request_id!r}",
            details={"expected_request_id": expected_request_id, "received_request_id": request_id},
        )
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise _violation(HarnessErrorCode.INVALID_MESSAGE, "payload must be an object")
    session_id = str(raw.get("session_id") or "").strip() or None
    if expected_session_id is not None and session_id != expected_session_id:
        raise _violation(
            HarnessErrorCode.CORRELATION_MISMATCH,
            f"response session_id {session_id!r} does not match {expected_session_id!r}",
            details={"expected_session_id": expected_session_id, "received_session_id": session_id},
        )
    return ProtocolMessage(
        type=message_type,
        request_id=request_id,
        payload=dict(payload),
        session_id=session_id,
        protocol_version=PROTOCOL_VERSION,
    )


def negotiate_capabilities(
    *,
    required: Collection[HarnessCapability],
    offered: Collection[str | HarnessCapability],
) -> frozenset[HarnessCapability]:
    normalized: set[HarnessCapability] = set()
    for item in offered:
        try:
            normalized.add(item if isinstance(item, HarnessCapability) else HarnessCapability(str(item)))
        except ValueError:
            continue
    missing = set(required) - normalized
    if missing:
        raise _violation(
            HarnessErrorCode.MISSING_CAPABILITY,
            "sidecar does not provide required Harness capabilities",
            details={
                "required": sorted(item.value for item in required),
                "offered": sorted(item.value for item in normalized),
                "missing": sorted(item.value for item in missing),
            },
        )
    return frozenset(normalized)
