from datetime import datetime, timezone
from types import SimpleNamespace

from cst_agent_workbench.agent.harness_event_stream import HarnessEventStream


def test_event_stream_assigns_ordered_sequences_and_applies_retention():
    session = SimpleNamespace(session_id="session-a", metadata={})
    stream = HarnessEventStream(
        retention=2,
        clock=lambda: datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    stream.publish(session, event_type="one", request_id="run-a", payload={"value": 1})
    stream.publish(session, event_type="two", request_id="run-a", payload={"value": 2})
    stream.publish(session, event_type="three", request_id="run-a", payload={"value": 3})

    events = stream.snapshot(session)
    assert [event["sequence"] for event in events] == [2, 3]
    assert [event["type"] for event in events] == ["two", "three"]
    assert events[-1]["timestamp"] == "2026-08-12T00:00:00.000+00:00"
    assert stream.snapshot(session, after_sequence=2) == [events[-1]]


def test_event_stream_copies_payload_and_handles_missing_session_metadata():
    stream = HarnessEventStream()
    session = SimpleNamespace(session_id="session-a", metadata={})
    payload = {"nested": {"value": 1}}

    event = stream.publish(session, event_type="tool", request_id="run-a", payload=payload)
    payload["nested"]["value"] = 99

    assert event["payload"]["nested"]["value"] == 1
    assert stream.snapshot(SimpleNamespace()) == []
    assert stream.publish(SimpleNamespace(), event_type="ignored", request_id="run-a") is None
