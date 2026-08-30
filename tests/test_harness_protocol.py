import pytest

from cst_agent_workbench.agent.error_model import ErrorLayer, HarnessErrorCode
from cst_agent_workbench.agent.harness_protocol import (
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    HarnessCapability,
    HarnessMessageType,
    ProtocolViolation,
    make_message,
    negotiate_capabilities,
    parse_message,
)


def test_message_round_trip_preserves_correlation_and_payload():
    original = make_message(
        HarnessMessageType.RUN,
        request_id="run-1",
        session_id="session-1",
        payload={"messages": [], "tools": []},
    )

    parsed = parse_message(
        original.to_dict(),
        allowed_types={HarnessMessageType.RUN},
        expected_request_id="run-1",
        expected_session_id="session-1",
    )

    assert parsed == original
    assert original.to_dict()["protocol"] == {
        "name": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
    }


def test_request_id_is_required_when_building_message():
    with pytest.raises(ProtocolViolation) as exc_info:
        make_message(HarnessMessageType.RUN, request_id="")

    assert exc_info.value.envelope.layer == ErrorLayer.HARNESS
    assert exc_info.value.envelope.code == HarnessErrorCode.INVALID_MESSAGE.value


def test_protocol_version_mismatch_is_structured():
    raw = make_message(HarnessMessageType.HELLO, request_id="hello-1").to_dict()
    raw["protocol"]["version"] = 99

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(raw)

    envelope = exc_info.value.envelope
    assert envelope.code == HarnessErrorCode.UNSUPPORTED_VERSION.value
    assert envelope.details["supported_versions"] == [PROTOCOL_VERSION]
    assert envelope.details["received_version"] == 99


def test_unknown_message_type_is_rejected():
    raw = make_message(HarnessMessageType.HELLO, request_id="hello-1").to_dict()
    raw["type"] = "surprise"

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(raw)

    assert exc_info.value.envelope.code == HarnessErrorCode.UNKNOWN_MESSAGE_TYPE.value


def test_state_specific_allowed_types_fail_closed():
    raw = make_message(HarnessMessageType.TOOL_REQUEST, request_id="run-1").to_dict()

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(raw, allowed_types={HarnessMessageType.HELLO_ACK})

    assert exc_info.value.envelope.code == HarnessErrorCode.UNKNOWN_MESSAGE_TYPE.value
    assert exc_info.value.envelope.details["allowed_types"] == ["hello_ack"]


def test_correlation_mismatch_is_rejected():
    raw = make_message(HarnessMessageType.RESULT, request_id="run-2").to_dict()

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(raw, expected_request_id="run-1")

    assert exc_info.value.envelope.code == HarnessErrorCode.CORRELATION_MISMATCH.value
    assert exc_info.value.envelope.details["received_request_id"] == "run-2"


def test_session_correlation_mismatch_is_rejected():
    raw = make_message(
        HarnessMessageType.RESULT,
        request_id="run-1",
        session_id="session-b",
    ).to_dict()

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(
            raw,
            expected_request_id="run-1",
            expected_session_id="session-a",
        )

    assert exc_info.value.envelope.code == HarnessErrorCode.CORRELATION_MISMATCH.value
    assert exc_info.value.envelope.details["received_session_id"] == "session-b"


def test_payload_must_be_an_object():
    raw = make_message(HarnessMessageType.RESULT, request_id="run-1").to_dict()
    raw["payload"] = []

    with pytest.raises(ProtocolViolation) as exc_info:
        parse_message(raw)

    assert exc_info.value.envelope.code == HarnessErrorCode.INVALID_MESSAGE.value


def test_required_capabilities_are_negotiated():
    result = negotiate_capabilities(
        required={HarnessCapability.SEQUENTIAL_TOOLS, HarnessCapability.DYNAMIC_TOOL_CATALOG},
        offered=[
            "sequential_tools",
            "dynamic_tool_catalog",
            "healthcheck",
            "future_unknown_capability",
        ],
    )

    assert result == frozenset(
        {
            HarnessCapability.SEQUENTIAL_TOOLS,
            HarnessCapability.DYNAMIC_TOOL_CATALOG,
            HarnessCapability.HEALTHCHECK,
        }
    )


def test_missing_required_capability_is_structured():
    with pytest.raises(ProtocolViolation) as exc_info:
        negotiate_capabilities(
            required={HarnessCapability.SEQUENTIAL_TOOLS, HarnessCapability.STRUCTURED_ERRORS},
            offered=["sequential_tools"],
        )

    envelope = exc_info.value.envelope
    assert envelope.code == HarnessErrorCode.MISSING_CAPABILITY.value
    assert envelope.details["missing"] == ["structured_errors"]
