"""Lightweight ordered events for live Harness consumers.

Trace remains the durable audit record.  This stream is a bounded projection
for SSE, diagnostics and evaluation progress without duplicating business
state or tool results.
"""

from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

HARNESS_EVENT_VERSION = 1


class HarnessEventStream:
    def __init__(
        self,
        *,
        retention: int = 200,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.retention = max(1, int(retention))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()

    def publish(
        self,
        session: Any,
        *,
        event_type: str,
        request_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        metadata = getattr(session, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        with self._lock:
            sequence = int(metadata.get("harness_event_sequence", 0) or 0) + 1
            metadata["harness_event_sequence"] = sequence
            event = {
                "version": HARNESS_EVENT_VERSION,
                "sequence": sequence,
                "timestamp": self._clock().isoformat(timespec="milliseconds"),
                "type": str(event_type),
                "session_id": str(getattr(session, "session_id", "") or ""),
                "request_id": str(request_id),
                "payload": deepcopy(dict(payload or {})),
            }
            events = metadata.setdefault("harness_events", [])
            if not isinstance(events, list):
                events = []
                metadata["harness_events"] = events
            events.append(event)
            del events[:-self.retention]
            return deepcopy(event)

    def snapshot(self, session: Any, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        metadata = getattr(session, "metadata", None)
        if not isinstance(metadata, dict):
            return []
        with self._lock:
            events = metadata.get("harness_events")
            if not isinstance(events, list):
                return []
            return [
                deepcopy(event)
                for event in events
                if isinstance(event, dict) and int(event.get("sequence", 0) or 0) > after_sequence
            ]
