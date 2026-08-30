"""Restricted Pi Agent Core adapter for the CST agent tool loop.

Pi owns model turns and tool-loop continuation. The Python host remains the
authority for tool filtering, execution, recovery, trace and AgentSession.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import weakref
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence

from cst_agent_workbench import config
from cst_agent_workbench.agent.context_envelope import build_context_envelope
from cst_agent_workbench.agent.error_model import (
    AgentLayerError,
    ErrorEnvelope,
    ErrorLayer,
    HarnessErrorCode,
    ProviderErrorCode,
)
from cst_agent_workbench.agent.harness_protocol import HarnessCapability, HarnessMessageType
from cst_agent_workbench.agent.harness_event_stream import HarnessEventStream
from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.agent.model_provider import normalize_model_protocol, pi_api_name
from cst_agent_workbench.agent.runtime import (
    ToolLoopResult,
    _active_step_kind,
    _tool_name,
    _trim_messages_to_budget,
    filter_tools_for_active_step,
    record_observability_degradation,
    update_plan_after_turn,
)
from cst_agent_workbench.agent.sidecar_client import HarnessSidecarClient
from cst_agent_workbench.agent.sidecar_process import SidecarProcess
from cst_agent_workbench.agent.sidecar_registry import SidecarSessionRegistry

logger = logging.getLogger(__name__)


class PiBrainError(AgentLayerError):
    """Raised when the Pi sidecar cannot complete a valid protocol run."""


_PI_CAPABILITIES = {
    HarnessCapability.SEQUENTIAL_TOOLS,
    HarnessCapability.DYNAMIC_TOOL_CATALOG,
    HarnessCapability.STRUCTURED_ERRORS,
    HarnessCapability.HEALTHCHECK,
    HarnessCapability.CANCEL,
    HarnessCapability.STEERING,
    HarnessCapability.FOLLOW_UP,
}

_PI_EVENT_TYPES = {
    HarnessMessageType.ASSISTANT_MESSAGE,
    HarnessMessageType.TOOL_REQUEST,
    HarnessMessageType.TOOL_BATCH_END,
    HarnessMessageType.PREPARE_NEXT_TURN,
    HarnessMessageType.RESULT,
    HarnessMessageType.ERROR,
}


def _error_envelope_from_payload(
    payload: Mapping[str, Any],
    *,
    request_id: str,
) -> ErrorEnvelope:
    try:
        layer = ErrorLayer(str(payload.get("layer") or "unknown"))
    except ValueError:
        layer = ErrorLayer.UNKNOWN
    status_code_raw = payload.get("status_code")
    try:
        status_code = int(status_code_raw) if status_code_raw is not None else None
    except (TypeError, ValueError):
        status_code = None
    details = payload.get("details")
    return ErrorEnvelope(
        layer=layer,
        code=str(payload.get("code") or HarnessErrorCode.PROTOCOL_ERROR.value),
        message=str(payload.get("message") or "Pi sidecar returned a structured error"),
        retryable=bool(payload.get("retryable", False)),
        status_code=status_code,
        request_id=str(payload.get("request_id") or request_id),
        cause_type=str(payload.get("cause_type") or "PiSidecarError"),
        details=dict(details) if isinstance(details, Mapping) else {},
    )


def _pi_error(
    message: str,
    *,
    code: str,
    layer: ErrorLayer = ErrorLayer.HARNESS,
    request_id: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> PiBrainError:
    return PiBrainError(
        ErrorEnvelope(
            layer=layer,
            code=code,
            message=message,
            retryable=False,
            request_id=request_id,
            cause_type="PiBrainError",
            details=dict(details or {}),
        )
    )


def _project_boundary_error(exc: AgentLayerError) -> PiBrainError:
    envelope = exc.envelope
    message = envelope.message
    if envelope.code == HarnessErrorCode.SIDECAR_TIMEOUT.value:
        message = f"Pi sidecar 超时：{message}"
    elif envelope.code == HarnessErrorCode.SIDECAR_EXITED.value:
        message = f"Pi sidecar 提前退出：{message}"
    elif envelope.code == HarnessErrorCode.SIDECAR_UNAVAILABLE.value:
        message = f"Pi sidecar 不可用：{message}"
    elif envelope.code == HarnessErrorCode.PROTOCOL_ERROR.value and "invalid JSONL" in message:
        message = f"Pi sidecar 输出了非法 JSONL：{message}"
    elif envelope.layer == ErrorLayer.HARNESS:
        message = f"Pi sidecar 协议错误：{message}"
    return PiBrainError(
        ErrorEnvelope(
            layer=envelope.layer,
            code=envelope.code,
            message=message,
            retryable=envelope.retryable,
            status_code=envelope.status_code,
            request_id=envelope.request_id,
            cause_type=envelope.cause_type,
            details=envelope.details,
        )
    )


class PiAgentBrain:
    """Run one CST agent turn through the restricted Pi Node sidecar."""

    def __init__(
        self,
        *,
        node_executable: str,
        sidecar_path: str | os.PathLike[str],
        timeout_sec: int,
        api_key: str,
        base_url: str,
        model: str,
        context_window: int,
        max_output_tokens: int,
        thinking_level: str = "off",
        api_protocol: str = "chat_completions",
        prompt_cache_enabled: bool = True,
        prompt_cache_ttl: str = "5m",
        scripted_responses: Sequence[Dict[str, Any]] | None = None,
        sidecar_client_factory: Callable[[], HarnessSidecarClient] | None = None,
        event_stream: HarnessEventStream | None = None,
    ) -> None:
        self.node_executable = str(node_executable or "node")
        self.sidecar_path = Path(sidecar_path).expanduser().resolve()
        self.timeout_sec = max(1, int(timeout_sec))
        self.api_key = str(api_key or "")
        self.base_url = str(base_url or "")
        self.model = str(model or "")
        self.context_window = max(1024, int(context_window))
        self.max_output_tokens = max(1, int(max_output_tokens))
        self.thinking_level = str(thinking_level or "off").strip().lower()
        self.api_protocol = normalize_model_protocol(api_protocol)
        self.prompt_cache_enabled = bool(prompt_cache_enabled)
        self.prompt_cache_ttl = str(prompt_cache_ttl or "5m").strip().lower()
        self.scripted_responses = [dict(item) for item in scripted_responses] if scripted_responses is not None else None
        self.sidecar_client_factory = sidecar_client_factory
        self.event_stream = event_stream or HarnessEventStream()
        self._sidecar_registry = SidecarSessionRegistry(
            self._make_sidecar_client,
            health_timeout_sec=min(2.0, float(self.timeout_sec)),
        )
        self._registry_finalizer = weakref.finalize(self, self._sidecar_registry.close)

    @classmethod
    def from_config(cls) -> "PiAgentBrain":
        return cls(
            node_executable=config.PI_NODE_EXECUTABLE,
            sidecar_path=config.PI_SIDECAR_PATH,
            timeout_sec=config.PI_SIDECAR_TIMEOUT_SEC,
            api_key=config.PI_API_KEY,
            base_url=config.PI_BASE_URL,
            model=config.PI_MODEL,
            context_window=config.PI_CONTEXT_WINDOW,
            max_output_tokens=config.PI_MAX_OUTPUT_TOKENS,
            thinking_level=config.PI_THINKING_LEVEL,
            api_protocol=config.PI_API_PROTOCOL,
            prompt_cache_enabled=config.PI_PROMPT_CACHE_ENABLED,
            prompt_cache_ttl=config.PI_PROMPT_CACHE_TTL,
        )

    def _validate_runtime(self) -> None:
        if self.sidecar_client_factory is None and not self.sidecar_path.is_file():
            raise _pi_error(
                f"Pi sidecar 不存在: {self.sidecar_path}",
                code=HarnessErrorCode.SIDECAR_UNAVAILABLE.value,
                details={"sidecar_path": str(self.sidecar_path)},
            )
        node_path = shutil.which(self.node_executable)
        if self.sidecar_client_factory is None and node_path is None and not Path(self.node_executable).is_file():
            raise _pi_error(
                f"找不到 Pi Node 可执行文件: {self.node_executable}",
                code=HarnessErrorCode.SIDECAR_UNAVAILABLE.value,
                details={"node_executable": self.node_executable},
            )
        if self.scripted_responses is None and not self.api_key:
            raise _pi_error(
                "Pi Harness 未配置 PI_API_KEY（可留空以复用 MODEL_API_KEY）。",
                code=ProviderErrorCode.AUTH.value,
                layer=ErrorLayer.PROVIDER,
            )

    def _make_sidecar_client(self) -> HarnessSidecarClient:
        if self.sidecar_client_factory is not None:
            return self.sidecar_client_factory()
        process = SidecarProcess(
            [self.node_executable, str(self.sidecar_path)],
            cwd=self.sidecar_path.parent,
            shutdown_timeout_sec=2.0,
        )
        return HarnessSidecarClient(
            process,
            required_capabilities=_PI_CAPABILITIES,
            handshake_timeout_sec=min(5.0, float(self.timeout_sec)),
        )

    def cancel(self, *, session_id: str | None = None, reason: str = "host_cancelled") -> bool:
        return self._sidecar_registry.cancel(session_id=session_id, reason=reason)

    def steer(self, message: str, *, session_id: str | None = None) -> bool:
        return self._sidecar_registry.steer(message, session_id=session_id)

    def follow_up(self, message: str, *, session_id: str | None = None) -> bool:
        return self._sidecar_registry.follow_up(message, session_id=session_id)

    def close(self) -> None:
        if self._registry_finalizer.alive:
            self._registry_finalizer()

    def _emit_event(
        self,
        session: Any,
        *,
        event_type: str,
        request_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.event_stream.publish(
            session,
            event_type=event_type,
            request_id=request_id,
            payload=payload,
        )

    @staticmethod
    def _log(log_fn: Callable[[str], None] | None, message: str) -> None:
        if log_fn is not None:
            log_fn(message)

    @staticmethod
    def _select_active_tools(
        *,
        session: Any,
        tools: List[Dict[str, Any]],
        tool_memory_query: str,
        working_messages: List[Dict[str, Any]],
        inject_guidance: bool,
    ) -> List[Dict[str, Any]]:
        active_tools = filter_tools_for_active_step(session, tools) if session is not None else tools
        if session is None:
            return list(active_tools)
        try:
            from cst_agent_workbench.agent.tool_use_memory import (
                build_tool_use_memory_guidance,
                rerank_safe_tools,
            )

            guidance, recalled = build_tool_use_memory_guidance(
                session,
                tool_memory_query,
                allowed_tool_names=[_tool_name(tool) for tool in active_tools],
            )
            active_tools = rerank_safe_tools(active_tools, recalled)
            if inject_guidance:
                working_messages[:] = [
                    message
                    for message in working_messages
                    if not (
                        message.get("role") == "system"
                        and str(message.get("content") or "").startswith("[工具使用经验；")
                    )
                ]
                if guidance:
                    working_messages.append({"role": "system", "content": guidance})
            metadata = getattr(session, "metadata", None)
            if isinstance(metadata, dict):
                metadata["tool_use_memory_recall"] = [record.to_dict() for record in recalled]
                metadata["tool_use_memory_reranked_tools"] = [_tool_name(tool) for tool in active_tools]
        except Exception as exc:
            logger.warning("Pi tool-use memory recall degraded to static order: %s", exc)
            record_observability_degradation(
                session,
                component="pi_tool_use_memory_recall",
                fallback="static_safe_tool_order",
                error=exc,
            )
        return list(active_tools)

    def run_tool_loop(
        self,
        *,
        client: Any,
        model: str,
        tools: List[Dict[str, Any]],
        working_messages: List[Dict[str, Any]],
        pending_history: List[Dict[str, Any]],
        execute_tool: Callable[[str, dict], str],
        token_stats: Dict[str, int],
        offline_notice: str,
        optimization_mode: bool,
        max_tool_iterations: int,
        log_fn: Callable[[str], None] | None = None,
        trace_turn_callback: Callable[[Dict[str, Any]], None] | None = None,
        trace_tool_context_callback: Callable[[str, str], None] | None = None,
        session: Any = None,
        max_context_tokens: int = 6000,
        context_summary_message: Dict[str, Any] | None = None,
        tool_memory_query: str = "",
    ) -> ToolLoopResult:
        del client  # Planner still uses the Python client; Pi owns executor model calls.
        self._validate_runtime()
        request_id = f"pi-{time.time_ns()}"
        session_id = str(getattr(session, "session_id", "") or "")

        active_tools = self._select_active_tools(
            session=session,
            tools=tools,
            tool_memory_query=tool_memory_query,
            working_messages=working_messages,
            inject_guidance=True,
        )
        if session is not None and tools and not active_tools:
            # 与 native loop 的 fail-closed 守卫一致：空 allowlist 时拒绝请求模型，
            # 而不是把零工具目录发给 Pi（召回占位符会指向不存在的工具）。
            message = (
                "当前计划步骤的工具白名单为空（step kind 未登记或 allowed_tools "
                "与静态白名单无交集），已停止请求 Pi；请重新规划该步骤。"
            )
            pending_history.append({"role": "assistant", "content": message})
            record_observability_degradation(
                session,
                component="pi_step_tool_allowlist",
                fallback="refuse_model_call_empty_allowlist",
                error=ValueError(f"step_kind={_active_step_kind(session) or 'unknown'}"),
            )
            return ToolLoopResult(message, "empty_step_allowlist", False, message, False, [])
        budget_metrics: Dict[str, Any] = {}
        working_messages[:] = _trim_messages_to_budget(
            working_messages,
            max_tokens=max_context_tokens,
            context_summary_message=context_summary_message,
            tools=active_tools,
            model=model,
            metrics=budget_metrics,
        )
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            events = metadata.setdefault("context_budget_events", [])
            events.append({**budget_metrics, "brain": "pi"})
            del events[:-50]
        if not budget_metrics.get("within_budget", False):
            message = (
                "当前系统提示、工具定义和本轮必要上下文超过 token 预算，"
                "已停止请求 Pi；请缩小工具范围或提高 AGENT_CONTEXT_MAX_TOKENS。"
            )
            pending_history.append({"role": "assistant", "content": message})
            self._emit_event(
                session,
                event_type="context_rejected",
                request_id=request_id,
                payload={"reason": "context_budget_exceeded", "budget": budget_metrics},
            )
            return ToolLoopResult(message, "context_budget_exceeded", False, message, False, [])

        continuation_prompt = "Continue the current task under the latest host instruction."
        for message in reversed(working_messages):
            if message.get("role") == "system" and message.get("content"):
                continuation_prompt = str(message["content"])
                break

        context_envelope = build_context_envelope(
            messages=working_messages,
            tools=active_tools,
            active_plan=getattr(session, "active_plan", None),
            budget_metrics=budget_metrics,
            max_context_tokens=max_context_tokens,
            continuation_prompt=continuation_prompt,
            optimization_mode=optimization_mode,
        )
        request: Dict[str, Any] = {
            "context": context_envelope.to_dict(),
            "model": {
                "api_key": self.api_key,
                "api": pi_api_name(self.api_protocol),
                "base_url": self.base_url,
                "model": self.model or model,
                "context_window": self.context_window,
                "max_tokens": self.max_output_tokens,
                "thinking_level": self.thinking_level,
                "prompt_cache_enabled": self.prompt_cache_enabled,
                "prompt_cache_ttl": self.prompt_cache_ttl,
            },
            "limits": {"max_turns": max_tool_iterations},
        }
        if self.scripted_responses is not None:
            request["scripted_responses"] = deepcopy(self.scripted_responses)
        active_plan = getattr(session, "active_plan", None) or {}
        self._emit_event(
            session,
            event_type="context_prepared",
            request_id=request_id,
            payload={
                "context_version": context_envelope.version,
                "tool_names": [_tool_name(tool) for tool in active_tools],
                "current_step_id": active_plan.get("current_step_id"),
                "budget": dict(context_envelope.budget),
            },
        )

        successful_tool_call_count = 0
        routed_execution_mode = ""
        had_tool_failure = False
        executed_tool_names: List[str] = []
        successful_tool_names: List[str] = []
        approval_pending = False
        final_event: Dict[str, Any] | None = None
        turn_started_at = datetime.now().isoformat(timespec="seconds")
        deadline = time.monotonic() + self.timeout_sec
        self._log(log_fn, f"[Agent/Pi] 启动受限 sidecar，工具数 {len(active_tools)}")
        try:
            with self._sidecar_registry.lease(session_id=session_id, request_id=request_id) as lease:
                sidecar = lease.client
                self._emit_event(
                    session,
                    event_type="run_started",
                    request_id=request_id,
                    payload={"worker_generation": lease.generation},
                )
                sidecar.send_message(
                    HarnessMessageType.RUN,
                    request_id=request_id,
                    session_id=session_id or None,
                    payload=request,
                )
                while final_event is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _pi_error(
                            f"Pi sidecar 超时（{self.timeout_sec}s），已终止本轮。",
                            code=HarnessErrorCode.SIDECAR_TIMEOUT.value,
                            request_id=request_id,
                        )
                    message = sidecar.receive_message(
                        timeout_sec=remaining,
                        allowed_types=_PI_EVENT_TYPES,
                        expected_request_id=request_id,
                        expected_session_id=session_id or None,
                    )
                    event_type = message.type
                    event = dict(message.payload)
                    if event_type == HarnessMessageType.ASSISTANT_MESSAGE:
                        assistant = dict(event.get("message") or {})
                        assistant_dict: Dict[str, Any] = {
                            "role": "assistant",
                            "content": str(assistant.get("content") or ""),
                        }
                        tool_calls_raw = list(assistant.get("tool_calls") or [])
                        if tool_calls_raw:
                            assistant_dict["tool_calls"] = tool_calls_raw
                        usage = dict(assistant.get("usage") or {})
                        if usage:
                            token_stats["prompt"] = int(token_stats.get("prompt", 0)) + int(
                                usage.get("prompt_tokens", 0)
                            )
                            token_stats["completion"] = int(token_stats.get("completion", 0)) + int(
                                usage.get("completion_tokens", 0)
                            )
                            token_stats["calls"] = int(token_stats.get("calls", 0)) + 1
                            token_stats["cached"] = int(token_stats.get("cached", 0)) + int(
                                usage.get("cached_tokens", 0)
                            )
                            token_stats["cache_write"] = int(token_stats.get("cache_write", 0)) + int(
                                usage.get("cache_write_tokens", 0)
                            )
                        turn_finished_at = datetime.now().isoformat(timespec="seconds")
                        if trace_turn_callback is not None:
                            trace_turn_callback(
                                {
                                    "turn_index": int(event.get("turn_index") or 1),
                                    "started_at": turn_started_at,
                                    "finished_at": turn_finished_at,
                                    "model": self.model or model,
                                    "request_messages": deepcopy(working_messages),
                                    "tools": deepcopy(active_tools),
                                    "assistant_content": assistant_dict["content"],
                                    "tool_calls_raw": deepcopy(tool_calls_raw),
                                    "usage": usage,
                                    "context_budget": {**budget_metrics, "brain": "pi"},
                                }
                            )
                        working_messages.append(assistant_dict)
                        pending_history.append(assistant_dict)
                        self._emit_event(
                            session,
                            event_type="model_turn",
                            request_id=request_id,
                            payload={
                                "turn_index": int(event.get("turn_index") or 1),
                                "usage": usage,
                                "tool_names": [
                                    str((call.get("function") or {}).get("name") or "")
                                    for call in tool_calls_raw
                                    if isinstance(call, dict)
                                ],
                                "stop_reason": str(assistant.get("stop_reason") or ""),
                            },
                        )
                        turn_started_at = datetime.now().isoformat(timespec="seconds")
                        continue

                    if event_type == HarnessMessageType.TOOL_REQUEST:
                        tool_call_id = str(event.get("tool_call_id") or "")
                        tool_name = str(event.get("name") or "")
                        arguments = event.get("arguments")
                        if trace_tool_context_callback is not None:
                            trace_tool_context_callback(tool_call_id, tool_name)
                        executed_tool_names.append(tool_name)
                        self._emit_event(
                            session,
                            event_type="tool_requested",
                            request_id=request_id,
                            payload={"tool_call_id": tool_call_id, "tool_name": tool_name},
                        )
                        try:
                            tool_result = execute_tool(tool_name, arguments)
                        except Exception as exc:
                            had_tool_failure = True
                            tool_result = json.dumps(
                                {
                                    "success": False,
                                    "message": f"工具执行异常: {exc}",
                                    "error_type": type(exc).__name__,
                                    "tool_name": tool_name,
                                },
                                ensure_ascii=False,
                            )
                        try:
                            tool_result_dict = json.loads(tool_result)
                        except Exception:
                            tool_result_dict = {}
                        success = coerce_success(tool_result_dict.get("success", False))
                        if success:
                            successful_tool_call_count += 1
                            successful_tool_names.append(tool_name)
                        else:
                            if tool_result_dict.get("error_type") != "approval_required":
                                had_tool_failure = True
                            else:
                                approval_pending = True
                        recovery_result = dict(tool_result_dict.get("recovery_result") or {})
                        if (
                            recovery_result.get("action_name") == "reconnect_cst"
                            and coerce_success(recovery_result.get("recovered", False))
                            and coerce_success(recovery_result.get("retry_success", False))
                        ):
                            offline_notice = ""
                        if tool_result_dict.get("mode") == "llm_routed_fast_path":
                            routed_execution_mode = "llm_routed_fast_path"
                        tool_message = {"role": "tool", "tool_call_id": tool_call_id, "content": tool_result}
                        working_messages.append(tool_message)
                        pending_history.append(tool_message)
                        self._emit_event(
                            session,
                            event_type="tool_completed",
                            request_id=request_id,
                            payload={
                                "tool_call_id": tool_call_id,
                                "tool_name": tool_name,
                                "success": success,
                                "recovery_action": recovery_result.get("action_name"),
                            },
                        )
                        if recovery_result:
                            self._emit_event(
                                session,
                                event_type="recovery",
                                request_id=request_id,
                                payload={
                                    "tool_call_id": tool_call_id,
                                    "tool_name": tool_name,
                                    "action_name": recovery_result.get("action_name"),
                                    "recovered": coerce_success(recovery_result.get("recovered", False)),
                                    "retry_success": coerce_success(
                                        recovery_result.get("retry_success", False)
                                    ),
                                },
                            )
                        sidecar.send_message(
                            HarnessMessageType.TOOL_RESULT,
                            request_id=request_id,
                            session_id=session_id or None,
                            payload={
                                "tool_call_id": tool_call_id,
                                "content": tool_result,
                                "is_error": not success,
                            },
                        )
                        continue

                    if event_type == HarnessMessageType.TOOL_BATCH_END:
                        if session is not None:
                            batch_successful_tool_names = [
                                str(name)
                                for name in event.get("tool_names") or []
                                if str(name) in successful_tool_names
                            ]
                            update_plan_after_turn(
                                session=session,
                                assistant_text=str(event.get("assistant_content") or ""),
                                had_tool_calls=bool(batch_successful_tool_names),
                                had_tool_failure=had_tool_failure,
                                tool_names=batch_successful_tool_names,
                                observation="Pi tool batch completed",
                                final_action=(
                                    "approval_pending" if approval_pending else "tool_batch"
                                ),
                            )
                            plan = getattr(session, "active_plan", None) or {}
                            self._emit_event(
                                session,
                                event_type="plan_updated",
                                request_id=request_id,
                                payload={
                                    "current_step_id": plan.get("current_step_id"),
                                    "status": plan.get("status"),
                                    "tool_names": [
                                        str(name) for name in event.get("tool_names") or [] if name
                                    ],
                                },
                            )
                        continue

                    if event_type == HarnessMessageType.PREPARE_NEXT_TURN:
                        active_tools = self._select_active_tools(
                            session=session,
                            tools=tools,
                            tool_memory_query=tool_memory_query,
                            working_messages=working_messages,
                            inject_guidance=False,
                        )
                        sidecar.send_message(
                            HarnessMessageType.TURN_UPDATE,
                            request_id=request_id,
                            session_id=session_id or None,
                            payload={
                                "update_id": str(event.get("update_id") or ""),
                                "tools": deepcopy(active_tools),
                            },
                        )
                        self._emit_event(
                            session,
                            event_type="tool_catalog_updated",
                            request_id=request_id,
                            payload={"tool_names": [_tool_name(tool) for tool in active_tools]},
                        )
                        continue

                    if event_type == HarnessMessageType.ERROR:
                        self._emit_event(
                            session,
                            event_type="run_error",
                            request_id=request_id,
                            payload={"error": event},
                        )
                        raise PiBrainError(_error_envelope_from_payload(event, request_id=request_id))
                    if event_type == HarnessMessageType.RESULT:
                        final_event = event
                        self._emit_event(
                            session,
                            event_type="run_result",
                            request_id=request_id,
                            payload={
                                "ok": coerce_success(event.get("ok", False)),
                                "stop_reason": event.get("stop_reason"),
                                "limit_reached": bool(event.get("limit_reached")),
                                "assistant_turns": event.get("assistant_turns"),
                            },
                        )
                        continue
                    raise _pi_error(
                        f"Pi sidecar 返回未知事件类型: {event_type.value!r}",
                        code=HarnessErrorCode.UNKNOWN_MESSAGE_TYPE.value,
                        request_id=request_id,
                    )
        except PiBrainError as exc:
            self._emit_event(
                session,
                event_type="run_failed",
                request_id=request_id,
                payload={"error": exc.envelope.to_dict()},
            )
            raise
        except AgentLayerError as exc:
            projected = _project_boundary_error(exc)
            self._emit_event(
                session,
                event_type="run_failed",
                request_id=request_id,
                payload={"error": projected.envelope.to_dict()},
            )
            raise projected from exc

        assert final_event is not None
        if final_event.get("limit_reached"):
            final_text = offline_notice + "本轮 Pi 工具调用次数过多，已停止继续执行。请缩小需求范围后重试。"
            pending_history.append({"role": "assistant", "content": final_text})
            working_messages.append({"role": "assistant", "content": final_text})
            self._emit_event(
                session,
                event_type="run_finished",
                request_id=request_id,
                payload={"ok": False, "reason": "limit_reached"},
            )
            return ToolLoopResult(final_text, "pi_harness", False, final_text, had_tool_failure, executed_tool_names, successful_tool_names, approval_pending)

        final_text = str(final_event.get("final_text") or "")
        if not coerce_success(final_event.get("ok", False)):
            error_text = str(final_event.get("error") or "Pi Harness 未完成本轮执行。")
            final_text = offline_notice + (final_text or error_text)
            if pending_history and pending_history[-1].get("role") == "assistant":
                pending_history[-1] = {"role": "assistant", "content": final_text}
            else:
                pending_history.append({"role": "assistant", "content": final_text})
            self._emit_event(
                session,
                event_type="run_finished",
                request_id=request_id,
                payload={"ok": False, "reason": "sidecar_result_error"},
            )
            return ToolLoopResult(final_text, "pi_harness", False, error_text, had_tool_failure, executed_tool_names, successful_tool_names, approval_pending)

        if optimization_mode and successful_tool_call_count == 0:
            no_op_text = "本轮优化未执行有效调参或仿真操作，未记录结果。"
            if final_text:
                no_op_text += f"\n\n模型回复：{final_text}"
            final_text = offline_notice + no_op_text
            if pending_history and pending_history[-1].get("role") == "assistant":
                pending_history[-1] = {"role": "assistant", "content": final_text}
            else:
                pending_history.append({"role": "assistant", "content": final_text})
            self._emit_event(
                session,
                event_type="run_finished",
                request_id=request_id,
                payload={"ok": False, "reason": "optimization_noop"},
            )
            return ToolLoopResult(final_text, "pi_harness", False, final_text, had_tool_failure, executed_tool_names, successful_tool_names, approval_pending)

        if offline_notice and not final_text.startswith("提示：当前为离线模式"):
            final_text = offline_notice + final_text
            if pending_history and pending_history[-1].get("role") == "assistant":
                pending_history[-1] = {"role": "assistant", "content": final_text}
        self._emit_event(
            session,
            event_type="run_finished",
            request_id=request_id,
            payload={"ok": True, "reason": "completed"},
        )
        return ToolLoopResult(
            final_text=final_text,
            final_mode=routed_execution_mode or "pi_harness",
            ok=True,
            error="",
            had_tool_failure=had_tool_failure,
            executed_tool_names=executed_tool_names,
            successful_tool_names=successful_tool_names,
            approval_pending=approval_pending,
        )
