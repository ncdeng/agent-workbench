import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.errors import classify_error
from cst_agent_workbench.results.contracts import downsample_curve
from cst_agent_workbench.results.summary import resolve_target_frequency, summarize_s11_result

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_bool_call(obj: Any, name: str, default: bool = False) -> bool:
    func = getattr(obj, name, None)
    if not callable(func):
        return default
    try:
        return bool(func())
    except Exception:
        return default


def _serialize_chat_history(history: list[dict]) -> list[dict]:
    """Filter session.history to user + final-assistant entries for the chat UI.

    - User content lists (multi-modal) flatten to text + image count; base64 data is dropped.
    - Assistant entries with tool_calls are intermediate; only final replies survive.
    - Tool result messages are dropped.
    """
    out: list[dict] = []
    for msg in history or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            content = msg.get("content")
            if isinstance(content, list):
                texts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                images = sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")
                entry: dict = {"role": "user", "content": "\n".join(t for t in texts if t)}
                if images:
                    entry["images"] = images
                out.append(entry)
            elif isinstance(content, str):
                out.append({"role": "user", "content": content})
        elif role == "assistant" and not msg.get("tool_calls"):
            entry = {"role": "assistant", "content": msg.get("content") or ""}
            tool_events = msg.get("_tool_events")
            if tool_events:
                entry["toolEvents"] = [_serialize_tool_event(evt) for evt in tool_events if isinstance(evt, dict)]
            out.append(entry)
    return out


def _serialize_tool_event(event: dict[str, Any]) -> dict[str, Any]:
    # Idempotent: tolerate both raw tool events (snake_case source keys) and
    # already-serialized events (camelCase). The SSE path stashes serialized
    # events onto the assistant message, and /api/chat/history serializes again;
    # reading both key styles keeps approvalRequest/errorType alive across the
    # history round-trip so the approval card survives a page refresh.
    message = event.get("message", "")
    description = event.get("description", "") or message
    payload = {
        "phase": event.get("phase", ""),
        "tool": event.get("tool", "") or event.get("tool_name", ""),
        "success": coerce_success(event.get("success", False)),
        "description": description,
    }
    error_type = event.get("error_type") or event.get("errorType")
    if error_type:
        payload["errorType"] = error_type
    approval_request = event.get("approval_request") or event.get("approvalRequest")
    if approval_request:
        payload["approvalRequest"] = approval_request
    return payload


def _result_success(result: dict[str, Any]) -> bool:
    return coerce_success(result.get("success", result.get("ok", False)))


def _coerce_action_result(result: Any, *, default_message: str = "") -> dict[str, Any]:
    if isinstance(result, dict):
        return result
    return {"success": False, "message": default_message or str(result)}


def _chat_status_payload(agent: Any, reply: str, *, tool_event_count: int) -> dict[str, Any]:
    status = getattr(agent, "last_chat_status", {}) or {}
    status_ok = coerce_success(status.get("ok", True))
    had_tool_failure = coerce_success(status.get("had_tool_failure", False))
    error = str(status.get("error") or "")
    ok = status_ok and not had_tool_failure
    if not ok and not error:
        error = reply or "Agent reported failure"
    return {
        "ok": ok,
        "reply": reply,
        "error": error,
        "hadToolFailure": had_tool_failure,
        "mode": status.get("mode", ""),
        "toolEvents": tool_event_count,
    }


def _record_direct_event(
    session: Any,
    *,
    phase: str,
    tool_name: str,
    result: dict[str, Any],
    description: str,
) -> None:
    try:
        message = result.get("message", "") or result.get("error", "")
        success = _result_success(result)
        event = {
            "phase": phase,
            "tool_name": tool_name,
            "success": success,
            "message": message,
            "description": description if not message else f"{description}: {message}",
        }
        if not success:
            event["error_type"] = classify_error(message).value
        session.tool_events.append(event)
    except Exception:
        logger.debug("failed to record direct event", exc_info=True)


def _summarize_results(last_results: dict[str, Any], target_freq: float) -> dict[str, Any]:
    plot_data = last_results.get("plot_data") or []
    if not plot_data:
        return {
            "available": False,
            "minS11Db": None,
            "minFreqGhz": None,
            "targetS11Db": None,
            "points": 0,
            "bandwidthGhz": None,
            "plotData": [],
        }

    summary = summarize_s11_result(last_results, target_freq)
    curve = downsample_curve(plot_data, max_points=500, y_key="s_db")
    return {
        "available": bool(summary.success),
        "minS11Db": summary.min_s11_db,
        "minFreqGhz": summary.min_freq_ghz,
        "targetS11Db": summary.target_s11_db,
        "points": len(plot_data),
        "bandwidthGhz": summary.bandwidth_ghz,
        "plotData": curve.points,
    }


def _resolve_dashboard_target_frequency(state: Any, opt_settings: dict[str, Any]) -> float:
    mode = str(opt_settings.get("mode") or getattr(state.agent.opt_state, "target_mode", "at_f0"))
    if mode != "at_f0":
        return 0.0
    configured = float(
        opt_settings.get("target_freq")
        or getattr(state.agent.opt_state, "target_freq", 0.0)
        or 0.0
    )
    from cst_agent_workbench.cst.primitives import get_parameters

    return resolve_target_frequency(
        mode=mode,
        configured_target_freq=configured,
        parameter_lookup=get_parameters,
    )


def _execution_projection(agent: Any, session: Any) -> dict[str, Any]:
    metadata = getattr(session, "metadata", {}) or {}
    brain = str(metadata.get("agent_brain") or getattr(agent, "_agent_brain_name", "native"))
    if brain not in {"native", "pi"}:
        brain = "native"
    mode = str(
        (getattr(agent, "last_chat_status", {}) or {}).get("mode")
        or getattr(agent, "last_execution_mode", "")
    )
    if mode in {"fast_path", "llm_routed_fast_path", "dipole_fast_path", "pixel_patch_fast_path"}:
        strategy = "fast_path"
    elif mode == "pi_harness":
        strategy = "pi_harness"
    elif mode == "llm_path":
        strategy = "native_loop"
    else:
        strategy = None
    return {"agentBrain": brain, "executionStrategy": strategy}


def build_dashboard_snapshot(state: Any) -> dict[str, Any]:
    cst = state.cst
    agent = state.agent
    session = state.session
    opt_settings = getattr(state, "opt_settings", {}) or {}
    projection = session.get_projection()
    artifacts = session.artifacts
    opt_projection = projection.get("optimization") or {}
    memory_impact = opt_projection.get("memory_impact") or {}
    last_results = artifacts.last_results or {}
    target_freq = _resolve_dashboard_target_frequency(state, opt_settings)

    model_projection = projection.get("model") or {}
    trace_summary = projection.get("trace_last_run_summary") or {}
    events = []
    for event in (session.tool_events or [])[-8:]:
        events.append(_serialize_tool_event(event))

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project": {
            "connected": _safe_bool_call(cst, "is_connected"),
            "offlineMode": bool(getattr(cst, "offline_mode", False)),
            "path": getattr(cst, "project_path", "") or "",
        },
        "execution": _execution_projection(agent, session),
        "optimization": {
            "active": bool(opt_projection.get("active", False)),
            "round": int(opt_projection.get("round", 0) or 0),
            "bestRound": int(opt_projection.get("best_round", 0) or 0),
            "bestMetricValue": opt_projection.get("best_metric_value"),
            "targetMode": opt_settings.get("mode") or getattr(agent.opt_state, "target_mode", "at_f0"),
            "targetFreqGhz": target_freq,
            "targetDb": float(opt_settings.get("target_db") or getattr(agent.opt_state, "target_db", -10.0) or -10.0),
            "lastStrategy": opt_projection.get("last_strategy", ""),
            "lastRolledBack": bool(opt_projection.get("last_rolled_back", False)),
            "lastRollbackReason": opt_projection.get("last_rollback_reason", ""),
            "lastMemoryRecallCount": int(opt_projection.get("last_memory_recall_count", 0) or 0),
            "lastMemoryEnforced": bool(memory_impact.get("memory_enforced_by_validator", False)),
        },
        "results": _summarize_results(last_results, target_freq),
        "model": {
            "objectCount": int(model_projection.get("object_count", 0) or 0),
            "portCount": int(model_projection.get("port_count", 0) or 0),
            "parameterCount": int(model_projection.get("parameter_count", 0) or 0),
        },
        "trace": {
            "status": trace_summary.get("status", "idle"),
            "runId": trace_summary.get("run_id", "") or "",
            "toolCalls": int(trace_summary.get("tool_call_count", 0) or 0),
            "failedToolCalls": int(trace_summary.get("failed_tool_call_count", 0) or 0),
        },
        "recentEvents": events,
    }


def _format_trace_decision_summary(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""

    outcome = str(value.get("run_outcome", "")).strip()
    final_answer_preview = str(value.get("final_answer_preview", "")).strip()
    error_preview = str(value.get("error_preview", "")).strip()
    key_tools = [str(tool).strip() for tool in (value.get("key_tools") or []) if str(tool).strip()]

    if error_preview:
        return error_preview
    if final_answer_preview:
        return final_answer_preview
    if key_tools:
        tool_preview = ", ".join(key_tools[:3])
        return f"{outcome}: {tool_preview}" if outcome else tool_preview
    return outcome



def _serialize_trace_detail(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if not entry:
        return None

    run_metrics = entry.get("run_metrics") or {}
    decision_summary = entry.get("decision_summary") or {}
    return {
        "runId": entry.get("run_id", "") or "",
        "status": entry.get("status", "") or "",
        "startedAt": entry.get("started_at"),
        "finishedAt": entry.get("finished_at"),
        "turnCount": int(run_metrics.get("turn_count", len(entry.get("turns") or [])) or 0),
        "toolCallCount": int(entry.get("tool_call_count", run_metrics.get("tool_call_count", 0)) or 0),
        "turns": entry.get("turns", []) or [],
        "planState": entry.get("plan_state", {}) or {},
        "snapshotEntry": entry.get("entry_snapshot", {}) or {},
        "snapshotExit": entry.get("exit_snapshot", {}) or {},
        "runMetrics": run_metrics,
        "decisionSummary": _format_trace_decision_summary(decision_summary),
        "decisionSummaryRaw": decision_summary,
        "finalResponse": entry.get("final_response", {}) or {},
        "error": entry.get("error", "") or "",
        "tokenDelta": entry.get("token_delta", {}) or {},
    }


async def _await_blocking_task(task):
    try:
        return await task
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done():
            try:
                task.result()
            except Exception:
                logger.debug("exclusive operation failed after caller cancellation", exc_info=True)
        raise


async def _run_blocking(fn):
    task = asyncio.get_running_loop().run_in_executor(None, fn)
    return await _await_blocking_task(task)


async def _run_exclusive(operation_lock: asyncio.Lock, fn):
    async with operation_lock:
        return await _run_blocking(fn)


def _log_background_task_failure(task):
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.debug("stream producer failed after caller cancellation", exc_info=True)


async def _stream_produced_frames(produce):
    queue = asyncio.Queue()
    consumer_active = True

    def emit(frame: str) -> None:
        if consumer_active:
            queue.put_nowait(frame)

    async def run_producer():
        try:
            await produce(emit)
        finally:
            emit("data: [DONE]\n\n")
            if consumer_active:
                queue.put_nowait(None)

    producer_task = asyncio.create_task(run_producer())
    try:
        while True:
            frame = await queue.get()
            if frame is None:
                break
            yield frame
    finally:
        consumer_active = False
        if producer_task.done():
            _log_background_task_failure(producer_task)
        else:
            producer_task.add_done_callback(_log_background_task_failure)


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------

def create_app(dry_run: bool = False):
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError as exc:
        raise RuntimeError("React API server requires the optional web dependencies: pip install -e .[web]") from exc

    from cst_agent_workbench.bootstrap import get_app_state, reset_app_state
    from cst_agent_workbench.web.chat_routes import register_chat_routes
    from cst_agent_workbench.web.approval_routes import register_approval_routes
    from cst_agent_workbench.web.cst_routes import register_cst_routes
    from cst_agent_workbench.web.optimization_routes import register_optimization_routes
    from cst_agent_workbench.web.rag_routes import register_rag_routes
    from cst_agent_workbench.web.readonly_routes import register_readonly_routes
    from cst_agent_workbench.web.settings_routes import register_settings_routes
    from cst_agent_workbench.web.state_routes import register_state_routes

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            reset_app_state()

    app = FastAPI(title="CST-Agent Workbench API", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    operation_lock = asyncio.Lock()
    app.state.operation_lock = operation_lock
    register_approval_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
    )
    register_readonly_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        build_dashboard_snapshot=build_dashboard_snapshot,
        serialize_trace_detail=_serialize_trace_detail,
    )
    register_settings_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
    )
    register_chat_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
        run_exclusive=_run_exclusive,
        serialize_chat_history=_serialize_chat_history,
        serialize_tool_event=_serialize_tool_event,
        chat_status_payload=_chat_status_payload,
        stream_produced_frames=_stream_produced_frames,
    )
    register_cst_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
        run_exclusive=_run_exclusive,
        coerce_action_result=_coerce_action_result,
        result_success=_result_success,
        record_direct_event=_record_direct_event,
    )
    register_optimization_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
        run_blocking=_run_blocking,
        run_exclusive=_run_exclusive,
        stream_produced_frames=_stream_produced_frames,
        result_success=_result_success,
        record_direct_event=_record_direct_event,
    )
    register_state_routes(
        app,
        dry_run=dry_run,
        get_app_state=get_app_state,
        operation_lock=operation_lock,
        run_exclusive=_run_exclusive,
    )
    register_rag_routes(app)

    return app
