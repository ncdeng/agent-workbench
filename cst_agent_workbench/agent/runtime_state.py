import json
import logging
import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from cst_agent_workbench import config
from cst_agent_workbench.cst.primitives import get_model_summary
from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.agent.planner import summarize_plan
from cst_agent_workbench.results.summary import summarize_s11_result


DEFAULT_TRACE_RETENTION_LIMIT = 10
TRACE_PREVIEW_LIMIT = 600
TRACE_RESULT_PREVIEW_LIMIT = 1200
TRACE_MESSAGES_PREVIEW_COUNT = 8
TRACE_TOOL_RESULT_LIST_LIMIT = 12
TRACE_TOOL_RESULT_STRING_LIMIT = 4000


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_path_within(path: str, root: str) -> bool:
    if not path or not root:
        return False
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(root)]) == os.path.abspath(root)
    except ValueError:
        return False


def _truncate_text(value: Any, limit: int = TRACE_PREVIEW_LIMIT) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]} ... [truncated {len(text) - limit} chars]"


def _truncate_sequence(items: List[Any], limit: int) -> List[Any]:
    if len(items) <= limit:
        return items
    truncated = items[:limit]
    truncated.append({"_truncated": len(items) - limit})
    return truncated


def _sanitize_for_trace(value: Any, *, max_list_items: int = TRACE_TOOL_RESULT_LIST_LIMIT, max_string_length: int = TRACE_TOOL_RESULT_STRING_LIMIT) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value

    if isinstance(value, str):
        lower = value.lower()
        if len(value) > max_string_length:
            return _truncate_text(value, max_string_length)
        if "base64," in lower:
            prefix, _, payload = value.partition("base64,")
            return f"{prefix}base64,[omitted {len(payload)} chars]"
        return value

    if isinstance(value, list):
        sanitized = [_sanitize_for_trace(item, max_list_items=max_list_items, max_string_length=max_string_length) for item in value[:max_list_items]]
        if len(value) > max_list_items:
            sanitized.append({"_truncated_items": len(value) - max_list_items})
        return sanitized

    if isinstance(value, tuple):
        return _sanitize_for_trace(list(value), max_list_items=max_list_items, max_string_length=max_string_length)

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key == "image_url":
                url = ""
                if isinstance(item, dict):
                    url = str(item.get("url", ""))
                sanitized[key] = {"url": _truncate_text(_sanitize_for_trace(url, max_string_length=200), 200)}
                continue
            sanitized[key] = _sanitize_for_trace(item, max_list_items=max_list_items, max_string_length=max_string_length)
        return sanitized

    return _truncate_text(repr(value), max_string_length)


def _content_preview(content: Any) -> str:
    sanitized = _sanitize_for_trace(content, max_list_items=4, max_string_length=800)
    if isinstance(sanitized, str):
        return _truncate_text(sanitized, TRACE_PREVIEW_LIMIT)
    return _truncate_text(json.dumps(sanitized, ensure_ascii=False, indent=2), TRACE_PREVIEW_LIMIT)


def _message_trace_payload(message: Dict[str, Any]) -> Dict[str, Any]:
    payload = {"role": message.get("role", "")}
    if "tool_call_id" in message:
        payload["tool_call_id"] = message.get("tool_call_id")
    if "content" in message:
        payload["content" ] = _sanitize_for_trace(message.get("content"))
        payload["content_preview"] = _content_preview(message.get("content"))
    if message.get("tool_calls"):
        payload["tool_calls"] = _sanitize_for_trace(message.get("tool_calls"))
    return payload


def ensure_trace_state(agent) -> None:
    if not hasattr(agent, "current_run_id"):
        agent.current_run_id = None
    if not hasattr(agent, "current_trace"):
        agent.current_trace = None
    if not hasattr(agent, "trace_history"):
        agent.trace_history = []
    if not hasattr(agent, "selected_trace_run_id"):
        agent.selected_trace_run_id = None
    if not hasattr(agent, "trace_enabled"):
        agent.trace_enabled = True
    if not hasattr(agent, "trace_retention_limit"):
        agent.trace_retention_limit = DEFAULT_TRACE_RETENTION_LIMIT


def record_observability_degradation(
    subject,
    *,
    component: str,
    fallback: str,
    error: Any,
) -> Dict[str, Any]:
    """Record a bounded, user-inspectable degradation event.

    ``subject`` may be a full agent or an ``AgentSession``.  The helper is
    deliberately dependency-free so optional subsystems can report a fallback
    without turning observability itself into a new failure mode.
    """
    session = getattr(subject, "session", None)
    if session is None and hasattr(subject, "metadata"):
        session = subject
    trace_state = getattr(session, "trace", None) if session is not None else None
    run_id = (
        getattr(subject, "current_run_id", None)
        or getattr(trace_state, "current_run_id", None)
        or ""
    )
    event = {
        "timestamp": _now_iso(),
        "component": str(component or "unknown"),
        "fallback": str(fallback or "none"),
        "error": _truncate_text(error, 1000),
        "run_id": str(run_id or ""),
    }

    metadata = getattr(session, "metadata", None) if session is not None else None
    if isinstance(metadata, dict):
        events = metadata.setdefault("observability_degradations", [])
        events.append(dict(event))
        del events[:-50]

    current_trace = getattr(subject, "current_trace", None)
    if not isinstance(current_trace, dict) and trace_state is not None:
        current_trace = getattr(trace_state, "current_trace", None)
    if isinstance(current_trace, dict):
        trace_events = current_trace.setdefault("degradations", [])
        trace_events.append(dict(event))
        del trace_events[:-50]
    return event


def reset_trace_state(agent) -> None:
    ensure_trace_state(agent)
    agent.current_run_id = None
    agent.current_trace = None
    agent.trace_history = []
    agent.selected_trace_run_id = None


def _history_summary(agent, filtered_history: List[Dict[str, Any]]) -> Dict[str, Any]:
    history = list(getattr(agent, "history", []) or [])
    recent_roles = [msg.get("role", "") for msg in history[-8:]]
    filtered_roles = [msg.get("role", "") for msg in filtered_history[-8:]]
    return {
        "full_history_count": len(history),
        "context_history_count": len(filtered_history),
        "recent_roles": recent_roles,
        "context_roles": filtered_roles,
    }


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        return max(0, int((datetime.fromisoformat(str(finished_at)) - datetime.fromisoformat(str(started_at))).total_seconds() * 1000))
    except ValueError:
        return None


def _tool_status_label(success: Any) -> str:
    if success is True:
        return "success"
    if success is False:
        return "failed"
    return "running"


def _infer_result_kind(value: Any) -> str:
    candidate = value
    if isinstance(value, str):
        try:
            candidate = json.loads(value)
        except Exception:
            return "text"
    if isinstance(candidate, dict):
        if candidate.get("result_kind"):
            return str(candidate["result_kind"])
        if candidate.get("type"):
            return str(candidate["type"])
        if "plot_data" in candidate:
            return "plot_data"
        if "success" in candidate and len(candidate.keys()) <= 3:
            return "status"
        return "json"
    if isinstance(candidate, list):
        return "list"
    if candidate is None:
        return "empty"
    return type(candidate).__name__


def _dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _phase_summary(tool_calls: List[Dict[str, Any]]) -> str:
    phases = _dedupe_keep_order([call.get("phase", "") for call in tool_calls])
    if not phases:
        return "未调用工具"
    return " → ".join(phases)


def _observation_summary(tool_calls: List[Dict[str, Any]]) -> str:
    if not tool_calls:
        return "无工具观察"
    observations = []
    for call in tool_calls:
        if call.get("success") is False and call.get("error"):
            observations.append(f"{call.get('tool_name', 'tool')}失败：{_truncate_text(call.get('error', ''), 120)}")
            continue
        preview = _truncate_text(call.get("result_preview", ""), 120)
        if preview:
            observations.append(f"{call.get('tool_name', 'tool')}返回：{preview}")
    if not observations:
        return "工具已执行，但无可提炼观察"
    return "；".join(observations[:3])


def _assistant_action(assistant_content: Any, tool_calls_raw: List[Dict[str, Any]]) -> str:
    has_text = bool(str(assistant_content or "").strip())
    has_tools = bool(tool_calls_raw)
    if has_tools and has_text:
        return "tool_call_and_text"
    if has_tools:
        return "tool_call"
    if has_text:
        return "final_answer"
    return "empty"


def _turn_request_summary(trace: Dict[str, Any], request_messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], system_prompts: List[Any]) -> Dict[str, Any]:
    return {
        "message_count": len(request_messages),
        "context_history_count": ((trace.get("history_summary") or {}).get("context_history_count", 0)),
        "system_prompt_chars": sum(len(str(item or "")) for item in system_prompts),
        "tool_schema_count": len(tools or []),
    }


def _turn_decision_summary(turn: Dict[str, Any]) -> Dict[str, Any]:
    request = turn.get("request") or {}
    response = turn.get("response") or {}
    tool_calls = turn.get("tool_calls") or []
    successful = sum(1 for call in tool_calls if call.get("success") is True)
    failed = sum(1 for call in tool_calls if call.get("success") is False)
    action = _assistant_action(response.get("assistant_content_preview", ""), response.get("tool_calls_raw") or [])
    if failed > 0:
        decision_result = "tool_failed"
    elif successful > 0:
        decision_result = "tool_succeeded"
    elif action == "final_answer":
        decision_result = "answered"
    elif action == "empty":
        decision_result = "empty"
    else:
        decision_result = "pending_tool_result"
    return {
        "assistant_action": action,
        "assistant_intent_preview": _truncate_text(response.get("assistant_content_preview", ""), 160),
        "tool_names": [call.get("tool_name", "") for call in tool_calls] or [
            (item.get("function", {}) or {}).get("name", "") for item in (response.get("tool_calls_raw") or []) if isinstance(item, dict)
        ],
        "tool_call_count": len(tool_calls) or len(response.get("tool_calls_raw") or []),
        "successful_tool_call_count": successful,
        "failed_tool_call_count": failed,
        "phase_summary": _phase_summary(tool_calls),
        "observation_summary": _observation_summary(tool_calls),
        "decision_result": decision_result,
        "duration_ms": _duration_ms(turn.get("started_at"), turn.get("finished_at")),
        "active_step_title": turn.get("active_step_title", ""),
        "active_step_kind": turn.get("active_step_kind", ""),
        "replan_trigger": turn.get("replan_trigger", ""),
    }


def _run_metrics(trace: Dict[str, Any]) -> Dict[str, Any]:
    turns = trace.get("turns", [])
    tool_calls = [call for turn in turns for call in (turn.get("tool_calls") or [])]
    turn_count = len(turns)
    tool_call_count = len(tool_calls)
    successful = sum(1 for call in tool_calls if call.get("success") is True)
    duration_ms = _duration_ms(trace.get("started_at"), trace.get("finished_at"))
    total_tokens = sum(int((turn.get("usage") or {}).get("total_tokens", 0) or 0) for turn in turns)
    return {
        "duration_ms": duration_ms,
        "turn_count": turn_count,
        "tool_call_count": tool_call_count,
        "success_rate": round(successful / tool_call_count, 3) if tool_call_count else 1.0,
        "avg_turn_tokens": int(total_tokens / turn_count) if turn_count else 0,
    }


def _run_decision_summary(trace: Dict[str, Any]) -> Dict[str, Any]:
    turns = trace.get("turns", [])
    tool_calls = [call for turn in turns for call in (turn.get("tool_calls") or [])]
    successful = sum(1 for call in tool_calls if call.get("success") is True)
    failed = sum(1 for call in tool_calls if call.get("success") is False)
    key_tools = _dedupe_keep_order([call.get("tool_name", "") for call in tool_calls])[:6]
    phases = _dedupe_keep_order([call.get("phase", "") for call in tool_calls])
    status = str(trace.get("status", ""))
    error_preview = _truncate_text(trace.get("error", ""), 160)
    final_answer_preview = _truncate_text((trace.get("final_response") or {}).get("preview", ""), 200)
    plan_summary = summarize_plan(trace.get("plan_state") or {})
    if error_preview:
        run_outcome = "failed"
    elif status == "completed":
        run_outcome = "completed"
    else:
        run_outcome = status or "unknown"
    if failed > 0:
        final_action = "tool_failure"
    elif successful > 0 and final_answer_preview:
        final_action = "tool_then_answer"
    elif successful > 0:
        final_action = "tool_only"
    elif final_answer_preview:
        final_action = "answer_only"
    else:
        final_action = "empty"
    token_delta = trace.get("token_delta") or {}
    total_tokens = int(token_delta.get("total", 0) or 0)
    round_count = len(turns)
    token_efficiency_hint = f"{int(total_tokens / round_count) if round_count else total_tokens} tok/turn"
    return {
        "user_goal_preview": _truncate_text(((trace.get("user_input") or {}).get("preview", "")), 160),
        "run_outcome": run_outcome,
        "final_action": final_action,
        "round_count": round_count,
        "successful_tool_call_count": successful,
        "failed_tool_call_count": failed,
        "dominant_phases": phases[:4],
        "key_tools": key_tools,
        "final_answer_preview": final_answer_preview,
        "error_preview": error_preview,
        "token_efficiency_hint": token_efficiency_hint,
        "plan_summary": {
            "intent_kind": plan_summary.get("intent_kind", ""),
            "step_count": plan_summary.get("step_count", 0),
            "completed_step_count": plan_summary.get("completed_step_count", 0),
            "replan_count": plan_summary.get("replan_count", 0),
            "stop_reason": plan_summary.get("stop_reason", ""),
        },
    }


def start_trace_run(
    agent,
    *,
    user_input: Any,
    working_messages: List[Dict[str, Any]],
    filtered_history: List[Dict[str, Any]],
    pending_history: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    if not agent.trace_enabled:
        return None

    token_stats = agent.get_token_stats() if hasattr(agent, "get_token_stats") else {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "cost_usd": 0.0}
    run_id = uuid.uuid4().hex[:12]
    trace = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "finished_at": None,
        "status": "running",
        "user_input": {
            "preview": _content_preview(user_input),
            "full": _sanitize_for_trace(user_input),
        },
        "entry_snapshot": build_runtime_snapshot(agent),
        "history_summary": _history_summary(agent, filtered_history),
        "pending_history_preview": [_message_trace_payload(msg) for msg in pending_history[-4:]],
        "token_baseline": {
            "prompt": token_stats.get("prompt", 0),
            "completion": token_stats.get("completion", 0),
            "total": token_stats.get("total", 0),
            "calls": token_stats.get("calls", 0),
            "cost_usd": token_stats.get("cost_usd", 0.0),
        },
        "working_messages_preview": [_message_trace_payload(msg) for msg in working_messages[-TRACE_MESSAGES_PREVIEW_COUNT:]],
        "degradations": [],
        "turns": [],
        "tool_call_count": 0,
        "run_metrics": {
            "duration_ms": None,
            "turn_count": 0,
            "tool_call_count": 0,
            "success_rate": 1.0,
            "avg_turn_tokens": 0,
        },
        "decision_summary": {
            "user_goal_preview": _content_preview(user_input),
            "run_outcome": "running",
            "final_action": "pending",
            "round_count": 0,
            "successful_tool_call_count": 0,
            "failed_tool_call_count": 0,
            "dominant_phases": [],
            "key_tools": [],
            "final_answer_preview": "",
            "error_preview": "",
            "token_efficiency_hint": "0 tok/turn",
            "plan_summary": {
                "intent_kind": "",
                "step_count": 0,
                "completed_step_count": 0,
                "replan_count": 0,
                "stop_reason": "",
            },
        },
        "plan_state": summarize_plan(getattr(getattr(agent, "session", None), "active_plan", None)),
        "final_response": {"preview": "", "full": ""},
        "error": "",
    }
    agent.current_run_id = run_id
    agent.current_trace = trace
    agent.selected_trace_run_id = run_id
    return trace


def start_optimizer_trace_run(
    agent,
    *,
    user_input: Any,
    working_messages: List[Dict[str, Any]],
    pending_history: List[Dict[str, Any]],
) -> Dict[str, Any] | None:
    """Lightweight trace entry for programmatic optimization rounds.

    Avoids ``build_runtime_snapshot``, which reads ``agent.cst`` and other
    full-CSTAgent attributes. This lets optimization rounds be traced in
    minimal test stubs and production contexts where the full agent snapshot
    is not needed for observability.
    """
    ensure_trace_state(agent)
    if not agent.trace_enabled:
        return None

    token_stats = agent.get_token_stats() if hasattr(agent, "get_token_stats") else {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "cost_usd": 0.0}
    run_id = uuid.uuid4().hex[:12]
    trace = {
        "run_id": run_id,
        "started_at": _now_iso(),
        "finished_at": None,
        "status": "running",
        "user_input": {
            "preview": _content_preview(user_input),
            "full": _sanitize_for_trace(user_input),
        },
        "entry_snapshot": {},
        "history_summary": _history_summary(agent, []),
        "pending_history_preview": [_message_trace_payload(msg) for msg in pending_history[-4:]],
        "token_baseline": {
            "prompt": token_stats.get("prompt", 0),
            "completion": token_stats.get("completion", 0),
            "total": token_stats.get("total", 0),
            "calls": token_stats.get("calls", 0),
            "cost_usd": token_stats.get("cost_usd", 0.0),
        },
        "working_messages_preview": [_message_trace_payload(msg) for msg in working_messages[-TRACE_MESSAGES_PREVIEW_COUNT:]],
        "degradations": [],
        "turns": [],
        "tool_call_count": 0,
        "run_metrics": {
            "duration_ms": None,
            "turn_count": 0,
            "tool_call_count": 0,
            "success_rate": 1.0,
            "avg_turn_tokens": 0,
        },
        "decision_summary": {
            "user_goal_preview": _content_preview(user_input),
            "run_outcome": "running",
            "final_action": "pending",
            "round_count": 0,
            "successful_tool_call_count": 0,
            "failed_tool_call_count": 0,
            "dominant_phases": [],
            "key_tools": [],
            "final_answer_preview": "",
            "error_preview": "",
            "token_efficiency_hint": "0 tok/turn",
            "plan_summary": {
                "intent_kind": "",
                "step_count": 0,
                "completed_step_count": 0,
                "replan_count": 0,
                "stop_reason": "",
            },
        },
        "plan_state": summarize_plan(getattr(getattr(agent, "session", None), "active_plan", None)),
        "final_response": {"preview": "", "full": ""},
        "error": "",
    }
    agent.current_run_id = run_id
    agent.current_trace = trace
    agent.selected_trace_run_id = run_id
    return trace


def append_trace_turn(
    agent,
    *,
    assistant_content: Any,
    tool_calls_raw: List[Dict[str, Any]] | None = None,
    request_messages: List[Dict[str, Any]] | None = None,
    tools: List[Dict[str, Any]] | None = None,
    model: str = "ui-orchestration",
    usage: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    now = _now_iso()
    trace = getattr(agent, "current_trace", None)
    turn_index = len((trace or {}).get("turns", [])) + 1
    return append_llm_turn(
        agent,
        turn_index=turn_index,
        started_at=now,
        finished_at=now,
        model=model,
        request_messages=list(request_messages or []),
        tools=list(tools or []),
        assistant_content=assistant_content,
        tool_calls_raw=list(tool_calls_raw or []),
        usage=usage or {"total_tokens": 0},
    )


def append_llm_turn(
    agent,
    *,
    turn_index: int,
    started_at: str,
    finished_at: str,
    model: str,
    request_messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    assistant_content: Any,
    tool_calls_raw: List[Dict[str, Any]],
    usage: Dict[str, Any],
) -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    trace = getattr(agent, "current_trace", None)
    if not agent.trace_enabled or trace is None:
        return None

    system_prompts = [msg.get("content", "") for msg in request_messages if msg.get("role") == "system"]
    turn = {
        "turn_index": turn_index,
        "started_at": started_at,
        "finished_at": finished_at,
        "request_summary": _turn_request_summary(trace, request_messages, tools, system_prompts),
        "request": {
            "message_count": len(request_messages),
            "messages_preview": [_message_trace_payload(msg) for msg in request_messages[-TRACE_MESSAGES_PREVIEW_COUNT:]],
            "messages_full": [_message_trace_payload(msg) for msg in request_messages],
            "system_prompt_preview": _truncate_text("\n\n".join(str(item) for item in system_prompts), TRACE_PREVIEW_LIMIT),
            "system_prompt_full": [_sanitize_for_trace(item) for item in system_prompts],
            "tools_summary": [
                {
                    "name": tool.get("function", {}).get("name") if isinstance(tool.get("function"), dict) else tool.get("name"),
                    "description": _truncate_text(
                        (tool.get("function", {}) or {}).get("description", "") if isinstance(tool.get("function"), dict) else tool.get("description", ""),
                        160,
                    ),
                }
                for tool in tools
            ],
        },
        "response": {
            "assistant_content_preview": _content_preview(assistant_content),
            "assistant_content_full": _sanitize_for_trace(assistant_content),
            "tool_calls_raw": _sanitize_for_trace(tool_calls_raw),
        },
        "usage": _sanitize_for_trace(usage),
        "tool_calls": [],
        "active_step_title": "",
        "active_step_kind": "",
        "replan_trigger": "",
        "decision_summary": {
            "assistant_action": _assistant_action(assistant_content, tool_calls_raw),
            "assistant_intent_preview": _truncate_text(_content_preview(assistant_content), 160),
            "tool_names": [
                (item.get("function", {}) or {}).get("name", "") for item in (tool_calls_raw or []) if isinstance(item, dict)
            ],
            "tool_call_count": len(tool_calls_raw or []),
            "successful_tool_call_count": 0,
            "failed_tool_call_count": 0,
            "phase_summary": "未调用工具",
            "observation_summary": "无工具观察",
            "decision_result": "pending_tool_result" if tool_calls_raw else ("answered" if assistant_content else "empty"),
            "duration_ms": _duration_ms(started_at, finished_at),
            "active_step_title": "",
            "active_step_kind": "",
            "replan_trigger": "",
        },
    }
    trace["turns"].append(turn)
    return turn


def start_tool_call_trace(
    agent,
    *,
    tool_call_id: str | None,
    tool_name: str,
    phase: str,
    arguments: Dict[str, Any],
    source: str = "agent_tool_call",
) -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    trace = getattr(agent, "current_trace", None)
    if not agent.trace_enabled or trace is None or not trace.get("turns"):
        return None

    tool_trace = {
        "tool_call_id": tool_call_id or uuid.uuid4().hex[:10],
        "tool_name": tool_name,
        "phase": phase,
        "arguments": _sanitize_for_trace(arguments),
        "started_at": _now_iso(),
        "finished_at": None,
        "duration_ms": None,
        "success": None,
        "status_label": "running",
        "result_kind": "pending",
        "result_preview": "",
        "result_full": None,
        "error": "",
        "source": source,
    }
    current_turn = trace["turns"][-1]
    tool_trace["_parent_turn"] = current_turn
    current_turn["tool_calls"].append(tool_trace)
    current_turn["decision_summary"] = _turn_decision_summary(current_turn)
    return tool_trace


def finish_tool_call_trace(
    tool_trace: Dict[str, Any] | None,
    *,
    success: bool,
    result: Any = None,
    error: Any = "",
) -> None:
    if tool_trace is None:
        return

    started_at_text = tool_trace.get("started_at")
    finished_at = _now_iso()
    duration_ms = None
    if started_at_text:
        try:
            duration_ms = max(0, int((datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at_text)).total_seconds() * 1000))
        except ValueError:
            duration_ms = None

    sanitized_result = _sanitize_for_trace(result)
    normalized_success = coerce_success(success)
    tool_trace.update(
        {
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "success": normalized_success,
            "status_label": _tool_status_label(normalized_success),
            "result_kind": _infer_result_kind(result),
            "result_preview": _content_preview(sanitized_result),
            "result_full": sanitized_result,
            "error": _truncate_text(error, 1000) if error else "",
        }
    )
    parent_turn = tool_trace.get("_parent_turn")
    if isinstance(parent_turn, dict):
        parent_turn["decision_summary"] = _turn_decision_summary(parent_turn)


def finish_trace_run(agent, *, status: str, final_response: Any = "", error: Any = "") -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    trace = getattr(agent, "current_trace", None)
    if not agent.trace_enabled or trace is None:
        return None

    token_stats = agent.get_token_stats() if hasattr(agent, "get_token_stats") else {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "cost_usd": 0.0}
    baseline = trace.get("token_baseline", {})
    trace["finished_at"] = _now_iso()
    trace["status"] = status
    trace["final_response"] = {
        "preview": _content_preview(final_response),
        "full": _sanitize_for_trace(final_response),
    }
    trace["error"] = _truncate_text(error, 2000) if error else ""
    trace["token_delta"] = {
        "prompt": token_stats.get("prompt", 0) - baseline.get("prompt", 0),
        "completion": token_stats.get("completion", 0) - baseline.get("completion", 0),
        "total": token_stats.get("total", 0) - baseline.get("total", 0),
        "calls": token_stats.get("calls", 0) - baseline.get("calls", 0),
        "cost_usd": round(token_stats.get("cost_usd", 0.0) - baseline.get("cost_usd", 0.0), 4),
    }
    trace["tool_call_count"] = sum(len(turn.get("tool_calls", [])) for turn in trace.get("turns", []))
    trace["entry_snapshot"] = _sanitize_for_trace(trace.get("entry_snapshot"))
    try:
        trace["exit_snapshot"] = build_runtime_snapshot(agent)
    except Exception as exc:
        logger.warning("trace exit snapshot failed (non-critical): %s", exc)
        record_observability_degradation(
            agent,
            component="trace_exit_snapshot",
            fallback="empty_exit_snapshot",
            error=exc,
        )
        trace["exit_snapshot"] = {}
    for turn in trace.get("turns", []):
        for tool_call in turn.get("tool_calls", []):
            tool_call.pop("_parent_turn", None)
        turn["decision_summary"] = _turn_decision_summary(turn)
    trace["run_metrics"] = _run_metrics(trace)
    trace["decision_summary"] = _run_decision_summary(trace)

    history = list(getattr(agent, "trace_history", []))
    history.append(deepcopy(trace))
    retention_limit = max(1, _safe_int(getattr(agent, "trace_retention_limit", DEFAULT_TRACE_RETENTION_LIMIT), DEFAULT_TRACE_RETENTION_LIMIT))
    if len(history) > retention_limit:
        history = history[-retention_limit:]
    agent.trace_history = history
    agent.selected_trace_run_id = trace["run_id"]
    agent.current_run_id = None
    agent.current_trace = None
    return history[-1]


def select_trace_run(agent, run_id: str | None) -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    if not run_id:
        if agent.trace_history:
            agent.selected_trace_run_id = agent.trace_history[-1]["run_id"]
            return agent.trace_history[-1]
        agent.selected_trace_run_id = None
        return None

    for trace in reversed(agent.trace_history):
        if trace.get("run_id") == run_id:
            agent.selected_trace_run_id = run_id
            return trace
    return None


def get_selected_trace(agent) -> Dict[str, Any] | None:
    ensure_trace_state(agent)
    if agent.current_trace and agent.current_trace.get("run_id") == agent.selected_trace_run_id:
        return agent.current_trace
    if agent.selected_trace_run_id:
        selected = select_trace_run(agent, agent.selected_trace_run_id)
        if selected is not None:
            return selected
    if agent.current_trace is not None:
        return agent.current_trace
    if agent.trace_history:
        latest = agent.trace_history[-1]
        agent.selected_trace_run_id = latest.get("run_id")
        return latest
    return None


def build_trace_run_choices(agent) -> List[Dict[str, str]]:
    ensure_trace_state(agent)
    choices = []
    for trace in reversed(agent.trace_history):
        token_delta = trace.get("token_delta", {})
        label = (
            f"{trace.get('run_id', 'unknown')}"
            f" | {trace.get('status', 'unknown')}"
            f" | {trace.get('started_at', '')}"
            f" | turns {len(trace.get('turns', []))}"
            f" | tools {trace.get('tool_call_count', 0)}"
            f" | Δtok {token_delta.get('total', 0)}"
        )
        choices.append({"label": label, "value": trace.get("run_id", "")})
    return choices


def summarize_last_results(last_results: dict, target_freq: float) -> dict:
    if not last_results:
        return {"available": False}

    summary = {
        "available": True,
        "success": coerce_success(last_results.get("success", True)),
        "item": last_results.get("item", ""),
        "type": last_results.get("type", ""),
        "message": last_results.get("message", ""),
    }
    plot_data = last_results.get("plot_data") or []
    if plot_data and isinstance(plot_data, list) and "freq" in plot_data[0] and "s_db" in plot_data[0]:
        s11_summary = summarize_s11_result(last_results, target_freq)
        summary.update(
            {
                "points": len(plot_data),
                "min_s11_db": s11_summary.min_s11_db,
                "min_freq_ghz": s11_summary.min_freq_ghz,
            }
        )
        if target_freq and target_freq > 0:
            summary.update(
                {
                    "target_freq_ghz": s11_summary.target_freq_ghz,
                    "target_s11_db": s11_summary.target_s11_db,
                }
            )
    return summary


def build_runtime_snapshot(agent) -> dict:
    model_summary = get_model_summary()
    project_path = agent.cst.project_path or ""
    temp_root = os.path.abspath(config.CST_FAST_PATH_DIR)
    session = getattr(agent, "session", None)
    optimizer_result = getattr(getattr(session, "artifacts", None), "last_optimizer_result", {}) or {}
    memory_impact = getattr(session, "metadata", {}).get("optimizer_memory_impact", {}) if session is not None else {}
    return {
        "timestamp_local": datetime.now().isoformat(timespec="seconds"),
        "session": {
            "session_id": getattr(getattr(agent, "session", None), "session_id", ""),
            "projection": getattr(getattr(agent, "session", None), "get_projection", lambda: {})(),
        },
        "project": {
            "connected": agent.cst.is_connected(),
            "offline_mode": agent.cst.offline_mode,
            "path": project_path,
            "is_temp_fast_path_project": _is_path_within(project_path, temp_root),
        },
        "chat": {
            "history_length": len(agent.history),
            "mode": agent.last_chat_status.get("mode", "llm"),
            "ok": agent.last_chat_status.get("ok", True),
            "error": agent.last_chat_status.get("error", ""),
            "had_tool_failure": agent.last_chat_status.get("had_tool_failure", False),
            "last_tool_message": agent.last_tool_message,
            "has_last_vba": bool(agent.last_vba),
        },
        "model": {
            "object_count": len(model_summary["objects"]),
            "port_count": len(model_summary["ports"]),
            "parameter_count": len(model_summary["parameters"]),
            "objects": model_summary["objects"],
            "ports": model_summary["ports"],
            "parameters": model_summary["parameters"],
        },
        "results": summarize_last_results(agent.last_results, agent.opt_state.target_freq),
        "optimization": {
            "enabled": agent._optimization_mode,
            "active": agent.opt_state.active,
            "round": agent.opt_state.round,
            "history_count": len(agent.opt_state.history),
            "target_mode": agent.opt_state.target_mode,
            "target_freq_ghz": agent.opt_state.target_freq,
            "target_db": agent.opt_state.target_db,
            "best_metric_value": agent.opt_state.best_metric_value,
            "best_round": agent.opt_state.best_round,
            "last_result": _sanitize_for_trace(optimizer_result),
            "last_memory_impact": _sanitize_for_trace(memory_impact),
        },
        "execution": {
            "feed_strategy": agent._patch_feed_strategy,
            "fast_path_counter": agent._fast_path_counter,
            "tool_event_count": len(agent.tool_events),
            "recent_tool_events": agent.tool_events[-8:],
            "trace_enabled": getattr(agent, "trace_enabled", True),
            "trace_retention_limit": getattr(agent, "trace_retention_limit", DEFAULT_TRACE_RETENTION_LIMIT),
            "trace_history_count": len(getattr(agent, "trace_history", []) or []),
        },
        "token_stats": agent.get_token_stats(),
    }
