from __future__ import annotations

import json
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


def protocol_message(
    message_type: str,
    request_id: str,
    payload: dict,
    *,
    version: int = 1,
    session_id: str = "",
):
    message = {
        "protocol": {"name": "cst-agent-harness", "version": version},
        "type": message_type,
        "request_id": request_id,
        "payload": payload,
    }
    if session_id:
        message["session_id"] = session_id
    return message


for line in sys.stdin:
    request = json.loads(line)
    if request.get("type") == "hello":
        request_id = str(request.get("request_id") or "")
        version = 99 if MODE == "bad_version" else 1
        if MODE == "wrong_request_id":
            request_id = "wrong-request-id"
        print(
            json.dumps(
                protocol_message(
                    "hello_ack",
                    request_id,
                    {
                        "runtime": {"name": "fixture", "version": "1"},
                        "capabilities": [
                            "sequential_tools",
                            "dynamic_tool_catalog",
                            "structured_errors",
                            "healthcheck",
                            "cancel",
                        ],
                    },
                    version=version,
                )
            ),
            flush=True,
        )
        continue
    if request.get("type") == "health":
        print(
            json.dumps(
                protocol_message(
                    "health_result",
                    str(request.get("request_id") or ""),
                    {"ok": True, "status": "ready", "active_runs": 0},
                )
            ),
            flush=True,
        )
        continue
    if request.get("type") == "run":
        print(
            json.dumps(
                protocol_message(
                    "result",
                    str(request.get("request_id") or ""),
                    {"ok": True, "echo": dict(request.get("payload") or {})},
                    session_id=str(request.get("session_id") or ""),
                )
            ),
            flush=True,
        )
        if MODE == "crash_after_run":
            raise SystemExit(7)
        continue
    action = request.get("action")
    if action == "echo":
        print(json.dumps({"ok": True, "value": request.get("value")}), flush=True)
    elif action == "invalid_json":
        print("{not-json", flush=True)
    elif action == "array":
        print("[]", flush=True)
    elif action == "sleep":
        time.sleep(float(request.get("seconds") or 1.0))
        print(json.dumps({"ok": True}), flush=True)
    elif action == "stderr_exit":
        print("fixture exploded", file=sys.stderr, flush=True)
        raise SystemExit(7)
    elif action == "exit":
        raise SystemExit(0)
