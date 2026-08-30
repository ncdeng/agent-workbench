from __future__ import annotations

import itertools
import json
import logging
import os
from typing import Any

from cst_agent_workbench.agent.runtime_state import (
    finish_tool_call_trace,
    record_observability_degradation,
    start_tool_call_trace,
)
from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.errors import classify_error
from cst_agent_workbench.agent.failure_recovery import make_failure_event
from cst_agent_workbench.agent.tool_contracts import (
    ToolArgumentValidationError,
    normalize_and_validate_tool_arguments,
)
from cst_agent_workbench.agent.tool_approval import (
    DEFAULT_APPROVAL_ACTOR,
    requires_tool_approval,
)
logger = logging.getLogger(__name__)
from cst_agent_workbench.results.service import (
    export_project_result_ascii,
    list_project_results,
    open_project_results,
    read_project_result,
    read_s11,
)
from cst_agent_workbench.cst.primitives import (
    PRIMITIVES,
    register_material,
    register_object,
    register_parameter,
    register_port,
    register_waveguide_port,
    register_farfield_monitor,
    register_field_monitor,
    register_frequency_range,
    get_farfield_monitors,
    get_frequency_range,
)
from cst_agent_workbench.cst.solver_safety import validate_solver_ready
from cst_agent_workbench.templates.vba_templates import VBA_TEMPLATES

_FALLBACK_TOOL_ID_COUNTER = itertools.count(1)


def _tool_result_store(agent) -> dict:
    session = getattr(agent, "session", None)
    artifacts = getattr(session, "artifacts", None)
    if artifacts is None:
        return {}
    store = getattr(artifacts, "tool_results", None)
    if store is None:
        artifacts.tool_results = {}
        store = artifacts.tool_results
    return store


def _enforce_session_retention(agent) -> None:
    session = getattr(agent, "session", None)
    enforce = getattr(session, "enforce_retention", None)
    if callable(enforce):
        enforce()


def _summarize_tool_result(tool_name: str, result: dict) -> str:
    message = str(result.get("message", "") or "")
    if tool_name in {"get_s_parameter", "read_result"}:
        item = result.get("item", "")
        plot_data = result.get("plot_data") or []
        if plot_data:
            total = int(result.get("total_points", len(plot_data)) or len(plot_data))
            returned = int(result.get("returned_points", len(plot_data)) or len(plot_data))
            return f"{item or tool_name}: {returned}/{total} points; {message}".strip()
    if tool_name == "list_results":
        items = result.get("items") or result.get("results") or []
        if isinstance(items, list):
            total = int(result.get("total", len(items)) or 0)
            offset = int(result.get("offset", 0) or 0)
            return f"结果项 {offset}-{offset + len(items)} / {total}; {message}".strip()
    return message or tool_name


def _metadata_for_tool_result(result: dict) -> dict:
    metadata = {}
    if "plot_data" in result and isinstance(result.get("plot_data"), list):
        metadata["plot_data_points"] = len(result.get("plot_data") or [])
    for key in ("result_kind", "total_points", "returned_points", "downsampled", "offset", "limit", "total", "has_more", "next_offset"):
        if key in result:
            metadata[key] = result[key]
    for key in ("item", "output_path", "project_file", "mode", "request"):
        if key in result:
            metadata[key] = result[key]
    return metadata


def _store_and_summarize_tool_result(agent, tool_name: str, result_dict: dict, tool_call_id: str | None) -> dict:
    store = _tool_result_store(agent)
    # fallback id 不能用 len(store)+1：retention 把 store 驱逐回同一长度后，
    # 下一次会生成同名 id 并静默覆盖上一条 payload，recall_tool_result 就召回了
    # 错误数据。进程内单调计数器保证唯一。
    tool_event_id = tool_call_id or f"tool_{next(_FALLBACK_TOOL_ID_COUNTER)}"
    if store is not None:
        store[tool_event_id] = dict(result_dict)
        _enforce_session_retention(agent)
    summary = {
        "success": coerce_success(result_dict.get("success", False)),
        "message": result_dict.get("message", ""),
        "display_summary": _summarize_tool_result(tool_name, result_dict),
        "tool_event_id": tool_event_id,
        "full_payload_ref": f"session.artifacts.tool_results.{tool_event_id}",
        "metadata": _metadata_for_tool_result(result_dict),
    }
    # Stable failure codes are control signals, not bulky artifacts. Preserve
    # them in the bounded prompt result so Native and Pi can repair the next
    # call without recalling the full payload first.
    if not summary["success"]:
        if result_dict.get("error_type"):
            summary["error_type"] = result_dict["error_type"]
        violations = result_dict.get("violations")
        if isinstance(violations, list):
            summary["violations"] = violations[:8]
        if isinstance(result_dict.get("approval_request"), dict):
            summary["approval_request"] = dict(result_dict["approval_request"])
    for key in ("stage", "project_saved", "project_closed", "project_file", "verification"):
        if key in result_dict:
            summary[key] = result_dict[key]
    return summary


def _remember_tool_use_result(
    agent,
    *,
    tool_name: str,
    success: bool,
    message: str,
    arguments: dict,
    tool_call_id: str | None,
) -> None:
    if success:
        return
    session = getattr(agent, "session", None)
    if session is None:
        return
    try:
        from cst_agent_workbench.agent.tool_use_memory import (
            ToolUseMemoryRecord,
            ToolUseMemoryStore,
            persist_session_tool_use_memory,
        )

        store = getattr(session, "tool_use_memory", None)
        if store is None:
            store = ToolUseMemoryStore()
            setattr(session, "tool_use_memory", store)
        # 重复失败是"这条记录有多可信"的直接证据：同一工具连续失败越多次，
        # 越确定这不是偶发。此前恒为 0.8，使 recall 的 confidence 加权
        # (0.5+0.5*conf) 退化成常数，完全没有区分度。
        prior_failures = sum(
            1 for record in getattr(store, "records", [])
            if getattr(record, "task_signature", "") == f"tool_failure:{tool_name}"
        )
        confidence = min(0.9, 0.5 + 0.1 * prior_failures)
        record = ToolUseMemoryRecord(
            task_signature=f"tool_failure:{tool_name}",
            selected_tools=(tool_name,),
            success=False,
            failure_reason=str(message or f"{tool_name} failed"),
            corrective_hint="Review tool preconditions and arguments before retrying.",
            confidence=confidence,
            metadata={
                "tool_call_id": tool_call_id or "",
                "arguments": dict(arguments or {}),
                "prior_failures": prior_failures,
                "project_scope": str(
                    ((getattr(session, "metadata", {}) or {}).get("memory_scope") or {}).get("project_scope") or ""
                ),
                "design_signature": str(
                    (getattr(session, "metadata", {}) or {}).get("current_design_signature") or ""
                ),
            },
        )
        stored = store.add(record)
        metadata = getattr(session, "metadata", None)
        if metadata is not None:
            metadata["last_tool_use_memory_write"] = record.to_dict() if stored else {}
        if stored:
            persist_session_tool_use_memory(session)
    except Exception as exc:
        logger.warning("tool-use memory write failed: %s", exc)
        record_observability_degradation(
            agent,
            component="tool_use_memory_write",
            fallback="continue_without_persisting_failure_experience",
            error=exc,
        )


def execute_tool(agent, tool_name: str, arguments: Any) -> str:
    """Run a tool call and record a structured tool event on the agent."""
    tool_call_id = getattr(agent, "_active_tool_call_id", None)
    normalized_arguments, authorization_rejection, validation_error = preflight_tool_call(
        agent,
        tool_name,
        arguments,
    )
    approval_rejection = None
    approval_grant = None
    if authorization_rejection is None and validation_error is None:
        approval_rejection, approval_grant = _tool_approval_rejection(
            agent,
            tool_name,
            normalized_arguments,
        )
    trace_entry = None
    try:
        trace_entry = start_tool_call_trace(
            agent,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            phase=agent._get_phase(tool_name),
            arguments=normalized_arguments,
        )
    except Exception as exc:
        logger.warning("tool trace start failed (non-critical): %s", exc)
        record_observability_degradation(
            agent,
            component="tool_trace_start",
            fallback="execute_tool_without_call_trace",
            error=exc,
        )
        trace_entry = None

    try:
        if authorization_rejection is not None:
            result_text = authorization_rejection
        elif validation_error is not None:
            result_text = json.dumps(
                {
                    "success": False,
                    "message": str(validation_error),
                    "error_type": "invalid_tool_arguments",
                    "tool_name": tool_name,
                    "violations": [
                        item.to_dict() for item in validation_error.violations
                    ],
                    "received_type": type(arguments).__name__,
                },
                ensure_ascii=False,
            )
        elif approval_rejection is not None:
            result_text = approval_rejection
        else:
            result_text = do_execute_tool(agent, tool_name, normalized_arguments)
        prompt_result_text = result_text
        success = False
        message = ""
        result_dict = {}

        try:
            result_dict = json.loads(result_text)
            success = coerce_success(result_dict.get("success", False))
            message = result_dict.get("message", "")
        except (json.JSONDecodeError, TypeError) as _parse_exc:
            message = str(result_text)
            record_observability_degradation(
                agent,
                component="tool_result_parse",
                fallback="raw_text_status_only",
                error=_parse_exc,
            )

        if success and tool_name in ("get_s_parameter", "read_result"):
            agent.last_results = result_dict
        if success and hasattr(agent, "_refresh_session_memory_from_runtime"):
            try:
                agent._refresh_session_memory_from_runtime()
            except Exception as exc:
                logger.warning("session memory refresh after tool success failed: %s", exc)
                record_observability_degradation(
                    agent,
                    component="tool_session_memory_refresh",
                    fallback="continue_with_existing_session_memory",
                    error=exc,
                )
        if isinstance(result_dict, dict) and result_dict and tool_name != "recall_tool_result":
            try:
                prompt_result = _store_and_summarize_tool_result(agent, tool_name, result_dict, tool_call_id)
                prompt_result_text = json.dumps(prompt_result, ensure_ascii=False)
            except Exception as exc:
                logger.warning("tool result storage/summary failed: %s", exc)
                record_observability_degradation(
                    agent,
                    component="tool_result_summary",
                    fallback="bounded_status_only_result",
                    error=exc,
                )
                prompt_result_text = json.dumps(
                    {
                        "success": success,
                        "message": str(message or "")[:1000],
                        "result_kind": "summary_unavailable",
                    },
                    ensure_ascii=False,
                )

        description = agent.last_tool_message or tool_name
        event = {
            "phase": agent._get_phase(tool_name),
            "tool_name": tool_name,
            "arguments": normalized_arguments,
            "success": success,
            "message": message,
            "description": description,
        }
        if approval_grant is not None:
            event["approval"] = approval_grant.to_public_dict()
        if result_dict.get("approval_request"):
            event["approval_request"] = result_dict["approval_request"]
        if not success:
            event["error_type"] = str(
                result_dict.get("error_type") or classify_error(message).value
            )
            if event["error_type"] in {"invalid_tool_arguments", "approval_required"}:
                recovery_result, retry_success, retry_prompt_text, retry_raw_result = (
                    {
                        "attempted": False,
                        "reason": (
                            "approval_required"
                            if event["error_type"] == "approval_required"
                            else "contract_violation"
                        ),
                    },
                    False,
                    "",
                    {},
                )
            else:
                recovery_result, retry_success, retry_prompt_text, retry_raw_result = _attempt_recovery(
                    agent, tool_name, message, normalized_arguments, tool_call_id
                )
            event["recovery_result"] = recovery_result
            recovery_approval_request = recovery_result.get("approval_request")
            if isinstance(recovery_approval_request, dict):
                # A repaired recovery call may cross the raw-VBA boundary.  Make
                # that new request visible to both Harnesses and the Web UI.
                event["approval_request"] = dict(recovery_approval_request)
                try:
                    prompt_payload = json.loads(prompt_result_text)
                except (json.JSONDecodeError, TypeError):
                    prompt_payload = {"success": False, "message": message}
                prompt_payload["recovery_result"] = recovery_result
                prompt_payload["approval_request"] = dict(recovery_approval_request)
                prompt_result_text = json.dumps(prompt_payload, ensure_ascii=False)
            if retry_success and retry_prompt_text:
                success = True
                prompt_result_text = retry_prompt_text
                event["success"] = True
                message = str((retry_raw_result or {}).get("message") or "恢复后重试成功")
                event["message"] = message
                try:
                    result_dict = json.loads(prompt_result_text)
                    message = result_dict.get("message", "")
                    event["message"] = message
                    # The loop must see the recovery outcome as well as Trace.
                    # Without this field, both Native and Pi retain the stale
                    # entry-time offline notice even after reconnect + retry
                    # succeeded, contradicting the audited tool event.
                    result_dict["recovery_result"] = recovery_result
                    prompt_result_text = json.dumps(result_dict, ensure_ascii=False)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("recovery retry result parsing failed: %s", exc)
                    record_observability_degradation(
                        agent,
                        component="recovery_retry_result_parse",
                        fallback="preserve_success_with_raw_retry_message",
                        error=exc,
                    )
                if tool_name in ("get_s_parameter", "read_result"):
                    # Keep the complete artifact for plotting/analysis. The
                    # summarized JSON is only for the LLM context window.
                    agent.last_results = dict(retry_raw_result or result_dict)
                if hasattr(agent, "_refresh_session_memory_from_runtime"):
                    try:
                        agent._refresh_session_memory_from_runtime()
                    except Exception as exc:
                        logger.warning("session memory refresh after recovery failed: %s", exc)
                        record_observability_degradation(
                            agent,
                            component="recovery_session_memory_refresh",
                            fallback="continue_with_existing_session_memory",
                            error=exc,
                        )
        if not success and message and hasattr(agent, "_remember_failure"):
            agent._remember_failure(f"{tool_name}: {message}")
        _remember_tool_use_result(
            agent,
            tool_name=tool_name,
            success=success,
            message=message,
            arguments=normalized_arguments,
            tool_call_id=tool_call_id,
        )
        agent.tool_events.append(event)
        _enforce_session_retention(agent)
        trace_result = prompt_result_text
        if event.get("recovery_result"):
            trace_result = {
                "tool_result": prompt_result_text,
                "recovery_result": event["recovery_result"],
            }
        try:
            finish_tool_call_trace(trace_entry, success=success, result=trace_result)
        except Exception as _trace_exc:
            logger.warning("trace finish skipped (non-critical): %s", _trace_exc)
            record_observability_degradation(
                agent,
                component="tool_trace_finish",
                fallback="preserve_session_tool_event_only",
                error=_trace_exc,
            )
        return prompt_result_text
    except Exception as exc:
        if hasattr(agent, "_remember_failure"):
            agent._remember_failure(f"{tool_name}: {exc}")
        try:
            finish_tool_call_trace(trace_entry, success=False, error=str(exc))
        except Exception as trace_exc:
            logger.warning("trace finish on tool exception failed: %s", trace_exc)
            record_observability_degradation(
                agent,
                component="tool_trace_finish_error_path",
                fallback="raise_original_tool_error_without_trace_finish",
                error=trace_exc,
            )
        raise
    finally:
        if hasattr(agent, "_active_tool_call_id"):
            agent._active_tool_call_id = None


def _attempt_recovery(
    agent: Any,
    tool_name: str,
    message: str,
    arguments: dict,
    tool_call_id: str | None,
) -> tuple[dict, bool, str, dict]:
    """Run the agent's failure recovery engine and record the outcome.

    Returns ``(recovery_result_dict, retry_success, retry_prompt_text, retry_raw_result)``.
    When a retry succeeds, ``retry_prompt_text`` is the summarized JSON that
    should be returned to the LLM instead of the original failure text.
    """
    engine = getattr(agent, "_failure_recovery_engine", None)
    if engine is None:
        return {"recovered": False, "reason": "no recovery engine attached"}, False, "", {}
    try:
        failure_event = make_failure_event(
            tool_name=tool_name,
            message=message,
            phase=agent._get_phase(tool_name),
            arguments=arguments,
            tool_event_id=tool_call_id,
        )
        result = engine.attempt_recovery(agent, failure_event)
        if result.recovered and result.retry_tool and result.retry_arguments is not None:
            retry_arguments, authorization_rejection, validation_error = preflight_tool_call(
                agent,
                result.retry_tool,
                result.retry_arguments,
            )
            if authorization_rejection is not None:
                rejection = json.loads(authorization_rejection)
                recovery_dict = result.to_dict()
                recovery_dict.update({
                    "recovered": False,
                    "retry_success": False,
                    "reason": "retry_tool_not_allowed",
                    "retry_authorization_error": rejection.get("message", ""),
                })
                return recovery_dict, False, "", {}
            if validation_error is not None:
                recovery_dict = result.to_dict()
                recovery_dict.update({
                    "recovered": False,
                    "retry_success": False,
                    "reason": "retry_contract_violation",
                    "retry_validation_error": str(validation_error),
                })
                return recovery_dict, False, "", {}
            approval_rejection, _approval_grant = _tool_approval_rejection(
                agent,
                result.retry_tool,
                retry_arguments,
            )
            if approval_rejection is not None:
                approval_dict = json.loads(approval_rejection)
                recovery_dict = result.to_dict()
                recovery_dict.update({
                    "recovered": False,
                    "retry_success": False,
                    "reason": "retry_approval_required",
                    "approval_request": approval_dict.get("approval_request"),
                })
                return recovery_dict, False, "", {}
            retry_text = do_execute_tool(agent, result.retry_tool, retry_arguments)
            retry_dict: dict[str, Any] = {}
            try:
                retry_dict = json.loads(retry_text)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("recovery retry returned non-JSON result: %s", exc)
                record_observability_degradation(
                    agent,
                    component="recovery_retry_json",
                    fallback="treat_retry_as_failed",
                    error=exc,
                )
            retry_success = coerce_success(retry_dict.get("success", False))
            retry_summary = None
            if retry_success and tool_name != "recall_tool_result":
                retry_summary = _store_and_summarize_tool_result(
                    agent, result.retry_tool, retry_dict, tool_call_id
                )
            recovery_dict = {
                **result.to_dict(),
                "recovered": retry_success,
                "retry_success": retry_success,
                "retry_message": retry_dict.get("message", "retry produced non-JSON"),
                "retry_tool_event_id": tool_call_id,
            }
            retry_prompt_text = (
                json.dumps(retry_summary, ensure_ascii=False)
                if retry_summary is not None
                else retry_text
            )
            return recovery_dict, retry_success, retry_prompt_text, retry_dict
        recovery_dict = result.to_dict()
        if result.recovered:
            # A fallback action without a retry may change mode/state, but it
            # has not demonstrated that the original tool call succeeded.
            recovery_dict["action_applied"] = True
            recovery_dict["recovered"] = False
            recovery_dict["retry_success"] = False
        return recovery_dict, False, "", {}
    except Exception as exc:
        logger.warning("failure recovery attempt raised: %s", exc)
        return {"recovered": False, "reason": f"recovery engine error: {exc}"}, False, "", {}


def _json_result(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _handle_recall_tool_result(agent, arguments: dict) -> str:
    tool_event_id = str(arguments.get("tool_event_id", "") or "")
    payload = _tool_result_store(agent).get(tool_event_id)
    if payload is None:
        agent.last_tool_message = f"未找到工具结果: {tool_event_id}"
        return _json_result({"success": False, "message": f"未找到工具结果: {tool_event_id}"})
    agent.last_tool_message = f"召回工具结果: {tool_event_id}"
    return _json_result({"success": True, "message": "工具结果已召回", "tool_event_id": tool_event_id, "payload": payload})


def _handle_create_cst_project(agent, arguments: dict) -> str:
    from cst_agent_workbench.agent.project_lifecycle import (
        synchronize_project_transition,
        validate_cst_project_path,
    )

    try:
        project_path = validate_cst_project_path(
            arguments["project_path"],
            must_exist=False,
            output_must_be_on_d_drive=True,
        )
    except ValueError as exc:
        return _json_result({"success": False, "message": str(exc), "error_type": "invalid_project_path"})
    result = agent.cst.new_project(project_path)
    if coerce_success(result.get("success")):
        agent.cst.project_path = str(result.get("project_file") or project_path)
        synchronize_project_transition(
            agent,
            agent.cst.project_path,
            reason="created_new_cst_project",
        )
    agent.last_tool_message = f"新建 CST 工程: {project_path}"
    return _json_result(result)


def _handle_open_cst_project(agent, arguments: dict) -> str:
    from cst_agent_workbench.agent.project_lifecycle import (
        synchronize_project_transition,
        validate_cst_project_path,
    )

    try:
        project_path = validate_cst_project_path(arguments["project_path"], must_exist=True)
    except ValueError as exc:
        return _json_result({"success": False, "message": str(exc), "error_type": "invalid_project_path"})
    result = agent.cst.open_project(project_path)
    if coerce_success(result.get("success")):
        agent.cst.project_path = str(result.get("project_file") or project_path)
        synchronize_project_transition(
            agent,
            agent.cst.project_path,
            reason="opened_different_cst_project",
        )
    agent.last_tool_message = f"打开 CST 工程: {project_path}"
    return _json_result(result)


def _handle_save_cst_project(agent, arguments: dict) -> str:
    result = agent.cst.save_project(include_results=arguments.get("include_results", True))
    agent.last_tool_message = "保存当前 CST 工程"
    return _json_result(result)


def _handle_save_cst_project_as(agent, arguments: dict) -> str:
    from cst_agent_workbench.agent.project_lifecycle import validate_cst_project_path

    try:
        target_path = validate_cst_project_path(
            arguments["target_path"],
            must_exist=False,
            output_must_be_on_d_drive=True,
        )
    except ValueError as exc:
        return _json_result({"success": False, "message": str(exc), "error_type": "invalid_project_path"})
    result = agent.cst.save_project_as(
        target_path,
        include_results=arguments.get("include_results", True),
    )
    if coerce_success(result.get("success")):
        agent.cst.project_path = str(result.get("project_file") or target_path)
    agent.last_tool_message = f"CST 工程另存为: {target_path}"
    return _json_result(result)


def _handle_save_and_close_cst_project(agent, arguments: dict) -> str:
    from cst_agent_workbench.agent.project_lifecycle import synchronize_project_transition

    project_path = str(agent.cst.project_path or "")
    if not project_path:
        return _json_result({"success": False, "message": "当前无可关闭的 CST 工程"})
    save_result = agent.cst.save_project(include_results=arguments.get("include_results", True))
    if not coerce_success(save_result.get("success")):
        return _json_result(
            {
                "success": False,
                "message": f"关闭前保存失败，工程保持打开: {save_result.get('message', '')}",
                "stage": "save",
                "save_result": save_result,
            }
        )
    close_result = agent.cst.close_project(project_path)
    if not coerce_success(close_result.get("success")):
        return _json_result(
            {
                "success": False,
                "message": f"工程已保存但关闭失败: {close_result.get('message', '')}",
                "stage": "close",
                "save_result": save_result,
                "close_result": close_result,
            }
        )
    synchronize_project_transition(agent, "", reason="saved_and_closed_cst_project")
    agent.last_tool_message = f"保存并关闭 CST 工程: {project_path}"
    return _json_result(
        {
            "success": True,
            "message": "CST 工程已保存并关闭",
            "project_file": project_path,
            "project_saved": True,
            "project_closed": True,
        }
    )


def _record_auto_farfield_monitor(agent, result: dict, auto_freq: str) -> None:
    agent.tool_events.append(
        {
            "phase": agent._get_phase("create_farfield_monitor"),
            "tool_name": "create_farfield_monitor",
            "success": coerce_success(result.get("success")),
            "message": result.get("message", ""),
            "description": f"自动补建 farfield monitor: farfield (f={auto_freq})",
        }
    )
    _enforce_session_retention(agent)


def _record_auto_farfield_failure(agent, exc: Exception) -> None:
    agent.tool_events.append(
        {
            "phase": agent._get_phase("create_farfield_monitor"),
            "tool_name": "create_farfield_monitor",
            "success": False,
            "message": str(exc),
            "description": "自动补建 farfield monitor 失败",
        }
    )
    _enforce_session_retention(agent)


def _ensure_farfield_monitor_before_solver(agent) -> None:
    if get_farfield_monitors():
        return
    freq_range = get_frequency_range()
    auto_freq = freq_range.get("fmax") or freq_range.get("fmin") or "1.0"
    try:
        _, auto_farfield_vba = PRIMITIVES["create_farfield_monitor"](
            f"farfield (f={auto_freq})",
            auto_freq,
            False,
        )
        auto_farfield_result = agent.cst.execute_vba(
            auto_farfield_vba,
            label="auto_farfield_monitor_before_solver",
            timeout=60,
        )
        if coerce_success(auto_farfield_result.get("success")) and auto_farfield_result.get("executed", False):
            register_farfield_monitor(f"farfield (f={auto_freq})", auto_freq, False)
        _record_auto_farfield_monitor(agent, auto_farfield_result, auto_freq)
    except Exception as exc:
        _record_auto_farfield_failure(agent, exc)


def _run_solver_primitive(agent) -> dict:
    _ensure_farfield_monitor_before_solver(agent)
    preflight = validate_solver_ready(agent.cst, source="tool_runtime.run_solver")
    if not coerce_success(preflight.get("success")):
        agent.last_vba = f"# 求解前检查失败: {preflight.get('message', '')}"
        return {"success": False, "message": preflight.get("message", ""), "preflight": preflight}

    project_path = agent.cst.project_path
    if not project_path:
        project_path = agent.cst._query_best_effort_project_path() or ""
    model3d_dir = project_path.replace('.cst', '') + os.sep + 'Model' + os.sep + '3D' if project_path else ""
    rpp_path = os.path.join(model3d_dir, 'Model.rpp') if model3d_dir else ""
    logger.debug("run_solver: project=%s, rpp_exists=%s", project_path, bool(rpp_path and os.path.exists(rpp_path)))
    if rpp_path and os.path.exists(rpp_path):
        result = agent.cst.run_solver_with_templates(timeout=360)
    else:
        result = agent.cst.run_solver(timeout=300)
    agent.last_vba = "# 原生 API: model3d.run_solver()"
    return result


def _register_primitive_side_effects(tool_name: str, arguments: dict, exec_result: dict) -> None:
    if not (coerce_success(exec_result.get("success")) and exec_result.get("executed", False)):
        return
    if tool_name in ("create_brick", "create_cylinder", "create_extruded_polygon"):
        register_object(arguments["component"], arguments["name"], arguments.get("material", ""))
    elif tool_name == "create_material":
        register_material(arguments["name"])
    elif tool_name == "store_parameter":
        register_parameter(arguments["name"], arguments.get("value", ""))
    elif tool_name == "create_discrete_port":
        register_port(arguments.get("port_number", 1), arguments.get("impedance", "50"))
    elif tool_name == "create_waveguide_port":
        register_waveguide_port(
            arguments["port_number"],
            arguments.get("number_of_modes", 1),
            arguments["coordinate_mode"],
        )
    elif tool_name == "create_farfield_monitor":
        register_farfield_monitor(
            arguments.get("name", "farfield"),
            arguments.get("frequency", ""),
            arguments.get("use_subvolume", False),
        )
    elif tool_name == "create_frequency_field_monitor":
        register_field_monitor(
            arguments["name"],
            arguments["frequency"],
            arguments["field_type"],
        )
    elif tool_name == "set_frequency_range":
        register_frequency_range(arguments.get("fmin", ""), arguments.get("fmax", ""))


def _handle_primitive_tool(agent, tool_name: str, arguments: dict) -> str:
    primitive_fn = PRIMITIVES[tool_name]
    logger.debug("primitive call: %s, args: %s", tool_name, json.dumps(arguments, ensure_ascii=False))
    try:
        label, vba_code = primitive_fn(**arguments)
    except (TypeError, ValueError) as exc:
        result = {"success": False, "message": f"原语参数错误: {exc}"}
        agent.last_tool_message = f"原语失败: {tool_name}"
        logger.warning("primitive validation failed: %s", exc)
        return _json_result(result)

    logger.debug("generated VBA (%s):\n%s", label, vba_code)
    if tool_name == "run_solver":
        exec_result = _run_solver_primitive(agent)
    else:
        exec_result = agent.cst.execute_vba(vba_code, label=label, timeout=60)
        agent.last_vba = vba_code
        if (
            coerce_success(exec_result.get("success"))
            and exec_result.get("executed", False)
            and tool_name in {
                "create_farfield_monitor",
                "create_frequency_field_monitor",
                "create_mesh_refinement",
                "add_solids_to_mesh_group",
                "create_waveguide_port",
            }
        ):
            exec_result = dict(exec_result)
            exec_result.setdefault("verification", "history_accepted")
    agent.last_tool_message = f"原语: {label}"
    _register_primitive_side_effects(tool_name, arguments, exec_result)
    return _json_result(exec_result)


def _handle_execute_vba_script(agent, arguments: dict) -> str:
    result = agent.cst.execute_vba(arguments["vba_code"])
    agent.last_vba = arguments["vba_code"]
    agent.last_tool_message = arguments.get("description", "执行 VBA 脚本")
    return _json_result(result)


def _handle_rectangular_patch_fast(agent, arguments: dict) -> str:
    agent.last_tool_message = "LLM 路由到矩形贴片 fast path"
    try:
        request = agent._resolve_rectangular_patch_request_from_tool_arguments(arguments)
    except (TypeError, ValueError) as exc:
        return _json_result(
            {
                "success": False,
                "message": f"矩形贴片 fast path 参数错误: {exc}",
                "mode": "llm_routed_fast_path",
            }
        )

    allow_solver = coerce_success(arguments.get("run_solver", arguments.get("solve", False)))
    message = agent._run_rectangular_patch_fast_path_from_request(
        request,
        execution_mode="llm_routed_fast_path",
        allow_solver=allow_solver,
    )
    return _json_result(
        {
            "success": bool(agent.last_chat_status.get("ok"))
            and not bool(agent.last_chat_status.get("had_tool_failure")),
            "message": message,
            "mode": "llm_routed_fast_path",
            "request": {
                "f0_ghz": request.f0_ghz,
                "substrate_name": request.substrate_name,
                "epsilon_r": request.epsilon_r,
                "loss_tangent": request.loss_tangent,
                "substrate_thickness_mm": request.substrate_thickness_mm,
                "conductor_name": request.conductor_name,
                "conductor_thickness_mm": request.conductor_thickness_mm,
                "feed_strategy": request.feed_strategy,
            },
        }
    )


def _handle_dipole_fast(agent, arguments: dict) -> str:
    from cst_agent_workbench.cst.dipole_fast import DipoleRequest
    f0_ghz = float(arguments.get("f0_ghz", 2.4))
    wire_or_plate = arguments.get("wire_or_plate", "plate")
    arm_radius_mm = float(arguments.get("arm_radius_mm", 0.5))
    req = DipoleRequest(f0_ghz=f0_ghz, wire_or_plate=wire_or_plate, arm_radius_mm=arm_radius_mm)
    outcome = agent._execute_dipole_request(
        req,
        allow_solver=coerce_success(arguments.get("run_solver", False)),
        execution_mode="llm_routed_dipole_fast_path",
        create_project=True,
    )
    agent.last_tool_message = f"半波振子 fast path: f0={f0_ghz}GHz"
    return _json_result(outcome)


def _handle_pixel_patch_fast(agent, arguments: dict) -> str:
    from cst_agent_workbench.cst.pixel_patch import PixelPatchConfig, random_initial_grid, pixel_grid_to_vba
    f0_ghz = float(arguments.get("f0_ghz", 5.8))
    config = PixelPatchConfig(
        n_rows=int(arguments.get("n_rows", 8)),
        n_cols=int(arguments.get("n_cols", 8)),
        cell_size_mm=float(arguments.get("pixel_size_mm", 3.0)),
        substrate_thickness_mm=float(arguments.get("substrate_h_mm", 1.6)),
        epsilon_r=float(arguments.get("epsilon_r", 4.4)),
        f0_ghz=f0_ghz,
    )
    grid = random_initial_grid(config)
    vba = pixel_grid_to_vba(grid, config)
    result = agent.cst.execute_vba(vba, timeout=120)
    success = coerce_success(result.get("success"))
    agent.last_tool_message = f"像素贴片 fast path: {config.n_rows}x{config.n_cols}, f0={f0_ghz}GHz"
    return _json_result({
        "success": success,
        "message": agent.last_tool_message,
        "grid_shape": [config.n_rows, config.n_cols],
        "pixel_size_mm": config.cell_size_mm,
    })


def _handle_template_tool(agent, arguments: dict) -> str:
    template_name = arguments["template_name"]
    template_parameters = arguments.get("parameters", {})
    if template_name not in VBA_TEMPLATES:
        result = {"success": False, "message": f"未找到模板：{template_name}"}
        agent.last_tool_message = f"调用模板失败：{template_name}"
        return _json_result(result)

    try:
        vba_code = VBA_TEMPLATES[template_name].format(**template_parameters)
    except KeyError as exc:
        result = {"success": False, "message": f"模板参数缺失：{exc}"}
        agent.last_tool_message = f"模板参数缺失：{template_name}"
        return _json_result(result)

    result = agent.cst.execute_vba(vba_code)
    agent.last_vba = vba_code
    agent.last_tool_message = f"使用模板：{template_name}"
    return _json_result(result)


def _handle_status_tool(agent, arguments: dict) -> str:
    agent.last_tool_message = "检查 CST 连接状态"
    return _json_result({"success": True, "message": agent.cst.get_status()})


def _handle_open_results(agent, arguments: dict) -> str:
    agent.last_tool_message = "打开结果文件"
    return _json_result(open_project_results(agent.results, arguments["cst_path"]))


def _handle_list_results(agent, arguments: dict) -> str:
    agent.last_tool_message = "列出可用结果"
    return _json_result(list_project_results(
        agent.results,
        agent.cst.project_path,
        offset=arguments.get("offset", 0),
        limit=arguments.get("limit", 50),
        category=arguments.get("category"),
        query=arguments.get("query"),
    ))


def _handle_read_result(agent, arguments: dict) -> str:
    item_path = arguments["item_path"]
    agent.last_tool_message = f"读取结果: {item_path}"
    return _json_result(read_project_result(
        agent.results,
        agent.cst.project_path,
        item_path,
        max_points=arguments.get("max_points"),
    ))


def _handle_get_s_parameter(agent, arguments: dict) -> str:
    port_i = arguments.get("port_i", 1)
    port_j = arguments.get("port_j", 1)
    agent.last_tool_message = f"读取 S{port_i},{port_j}"
    return _json_result(read_s11(
        agent.results,
        agent.cst.project_path,
        port_i,
        port_j,
        max_points=arguments.get("max_points"),
    ))


def _handle_export_result_ascii(agent, arguments: dict) -> str:
    item_path = arguments["item_path"]
    agent.last_tool_message = f"导出结果: {item_path}"
    return _json_result(export_project_result_ascii(
        agent.cst,
        item_path,
        arguments["output_path"],
        timeout=arguments.get("timeout", 120),
    ))


def _handle_list_project_materials(agent, arguments: dict) -> str:
    from cst_agent_workbench.cst.primitives import BUILTIN_MATERIALS, _created_materials

    agent.last_tool_message = "列出可用材料"
    return _json_result(
        {
            "success": True,
            "message": f"共 {len(BUILTIN_MATERIALS) + len(_created_materials)} 种可用材料",
            "builtin_materials": sorted(BUILTIN_MATERIALS),
            "custom_created_materials": sorted(_created_materials),
        }
    )


def _handle_set_global_hexahedral_mesh(agent, arguments: dict) -> str:
    result = agent.cst.set_global_hexahedral_mesh(
        lines_per_wavelength=arguments.get("lines_per_wavelength", 15),
        minimum_step_number=arguments.get("minimum_step_number", 5),
    )
    agent.last_tool_message = "按频率设置全局网格"
    if coerce_success(result.get("success")):
        result = dict(result)
        result.setdefault("verification", "history_accepted")
    return _json_result(result)


def _handle_get_mesh_signature(agent, _arguments: dict) -> str:
    getter = getattr(agent.cst, "get_mesh_signature", None)
    if not callable(getter):
        return _json_result({
            "success": False,
            "error_type": "unsupported_mesh_signature",
            "message": "当前 CST controller 未提供经过官方文档或真实探针验证的实际网格统计 getter",
            "configured_mesh_is_not_signature": True,
        })
    result = dict(getter() or {})
    if coerce_success(result.get("success")) and not result.get("signature"):
        return _json_result({
            "success": False,
            "error_type": "invalid_mesh_signature",
            "message": "mesh getter 未返回非空 realized signature",
        })
    return _json_result(result)


_TOOL_HANDLERS = {
    "create_cst_project": _handle_create_cst_project,
    "open_cst_project": _handle_open_cst_project,
    "save_cst_project": _handle_save_cst_project,
    "save_cst_project_as": _handle_save_cst_project_as,
    "save_and_close_cst_project": _handle_save_and_close_cst_project,
    "execute_vba_script": _handle_execute_vba_script,
    "build_rectangular_patch_fast": _handle_rectangular_patch_fast,
    "build_dipole_fast": _handle_dipole_fast,
    "build_pixel_patch_fast": _handle_pixel_patch_fast,
    "use_template": _handle_template_tool,
    "check_cst_status": _handle_status_tool,
    "open_results": _handle_open_results,
    "list_results": _handle_list_results,
    "read_result": _handle_read_result,
    "get_s_parameter": _handle_get_s_parameter,
    "export_result_ascii": _handle_export_result_ascii,
    "list_project_materials": _handle_list_project_materials,
    "set_global_hexahedral_mesh": _handle_set_global_hexahedral_mesh,
    "get_mesh_signature": _handle_get_mesh_signature,
}


def _tool_allowlist_rejection(agent, tool_name: str) -> str | None:
    try:
        from cst_agent_workbench.agent.runtime import allowed_tool_names_for_active_step

        allowed = allowed_tool_names_for_active_step(getattr(agent, "session", None))
    except Exception as exc:
        logger.warning("tool allowlist evaluation failed; rejecting tool call: %s", exc)
        record_observability_degradation(
            agent,
            component="tool_allowlist",
            fallback="fail_closed",
            error=exc,
        )
        message = f"无法验证工具 {tool_name} 的当前步骤权限，已按安全策略拒绝执行"
        agent.last_tool_message = message
        return _json_result(
            {
                "success": False,
                "message": message,
                "error_type": "allowlist_unavailable",
            }
        )
    if allowed is None or tool_name in allowed:
        return None
    message = f"工具 {tool_name} 不在当前步骤允许列表中"
    agent.last_tool_message = message
    return _json_result(
        {
            "success": False,
            "message": message,
            "error_type": "tool_not_allowed",
            "tool_name": tool_name,
            "allowed_tools": sorted(allowed),
        }
    )


def _approval_actor(agent: Any) -> str:
    # The current product is an unauthenticated, loopback desktop application.
    # Do not pretend arbitrary client/session metadata is a trusted identity.
    return DEFAULT_APPROVAL_ACTOR


def preflight_tool_call(
    agent: Any,
    tool_name: str,
    arguments: Any,
) -> tuple[dict[str, Any], str | None, ToolArgumentValidationError | None]:
    """Apply the canonical allowlist-then-schema checks used before execution.

    The approval HTTP endpoint uses the same preflight immediately before it
    issues a grant, closing the stale-plan/schema gap. Approval consumption is
    deliberately separate. This is side-effect free with respect to the world
    (no tool execution, no CST call, no grant consumed), but not fully pure:
    the allowlist rejection path sets ``agent.last_tool_message`` and may
    record an observability degradation.
    """

    authorization_rejection = _tool_allowlist_rejection(agent, tool_name)
    if authorization_rejection is not None:
        normalized = dict(arguments) if isinstance(arguments, dict) else {}
        return normalized, authorization_rejection, None
    try:
        normalized = normalize_and_validate_tool_arguments(tool_name, arguments)
    except ToolArgumentValidationError as exc:
        normalized = dict(arguments) if isinstance(arguments, dict) else {}
        return normalized, None, exc
    return normalized, None, None


def _tool_approval_rejection(
    agent: Any,
    tool_name: str,
    normalized_arguments: dict[str, Any],
) -> tuple[str | None, Any]:
    if not requires_tool_approval(tool_name):
        return None, None
    session = getattr(agent, "session", None)
    store = getattr(session, "tool_approvals", None)
    actor = _approval_actor(agent)
    if store is None:
        message = f"工具 {tool_name} 需要人工批准，但当前 Session 没有审批存储"
        agent.last_tool_message = message
        return _json_result({
            "success": False,
            "message": message,
            "error_type": "approval_unavailable",
            "tool_name": tool_name,
        }), None
    grant = store.consume(tool_name, normalized_arguments, actor=actor)
    if grant is not None:
        return None, grant
    request = store.request(tool_name, normalized_arguments, actor=actor)
    message = f"工具 {tool_name} 需要人工批准；批准只绑定当前规范化参数且仅可使用一次"
    agent.last_tool_message = message
    return _json_result({
        "success": False,
        "message": message,
        "error_type": "approval_required",
        "tool_name": tool_name,
        "approval_request": request.to_public_dict(),
    }), None


def do_execute_tool(agent, tool_name: str, arguments: dict) -> str:
    """Dispatch already-gated arguments inside the Host Runtime.

    Production callers must use :func:`execute_tool`; this lower-level helper
    remains module-private by convention for focused handler tests and the
    recovery path after it has repeated contract and approval checks.
    """
    rejection = _tool_allowlist_rejection(agent, tool_name)
    if rejection is not None:
        return rejection
    if tool_name == "recall_tool_result":
        return _handle_recall_tool_result(agent, arguments)
    if tool_name in PRIMITIVES:
        return _handle_primitive_tool(agent, tool_name, arguments)

    handler = _TOOL_HANDLERS.get(tool_name)
    if handler is not None:
        return handler(agent, arguments)

    agent.last_tool_message = f"未知工具：{tool_name}"
    return _json_result({"success": False, "message": f"未知工具：{tool_name}"})
