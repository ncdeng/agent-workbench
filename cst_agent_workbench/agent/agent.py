import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from cst_agent_workbench import config
logger = logging.getLogger(__name__)
from cst_agent_workbench.agent.memory import (
    MemoryManager,
    load_memory,
    merge_persistent_memory,
    project_scope_from_path,
    prune_session_memory_files,
    save_memory,
    save_persistent_memory,
)
from cst_agent_workbench.agent.prompt import SYSTEM_PROMPT as IMPORTED_SYSTEM_PROMPT, OPTIMIZATION_SUFFIX as IMPORTED_OPTIMIZATION_SUFFIX
from cst_agent_workbench.agent.runtime import (
    build_initial_plan_with_usage,
    evaluate_optimization_next_action,
    prepare_history_for_context,
    run_agent_turn,
    sync_plan_to_memory,
    update_plan_after_turn,
)
from cst_agent_workbench.agent.planner import build_plan_context_text, summarize_plan
from cst_agent_workbench.agent.runtime_state import (
    append_llm_turn,
    build_runtime_snapshot,
    finish_trace_run,
    record_observability_degradation,
    reset_trace_state,
    start_trace_run,
    summarize_last_results,
)
from cst_agent_workbench.agent.tool_runtime import execute_tool as runtime_execute_tool
from cst_agent_workbench.agent.failure_recovery import FailureRecoveryEngine
from cst_agent_workbench.agent.conversation_memory import (
    apply_constraints,
    extract_pending_questions,
    should_replace_user_goal,
)
from cst_agent_workbench.agent.session import AgentSession
from cst_agent_workbench.agent.tool_use_memory import load_tool_use_memory
from cst_agent_workbench.agent.tools import TOOLS as IMPORTED_TOOLS, get_tool_phase
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst.primitives import get_model_summary, reset_created_objects
from cst_agent_workbench.optimization.state import OptimizationState
from cst_agent_workbench.cst.patch_fast_executor import PatchFastExecutorMixin
from cst_agent_workbench.optimization.optimizer import PatchOptimizerMixin
from cst_agent_workbench.agent.fast_path import FastPathMixin
from cst_agent_workbench.cst.rectangular_patch_fast import get_patch_feed_strategy
from cst_agent_workbench.results.reader import ResultsReader
from cst_agent_workbench.agent.helpers import (
    _truncate_tool_result,
    is_meta_llm_query,
    _format_mm,
    coerce_success,
)

from cst_agent_workbench.agent.model_provider import create_model_provider_client

try:
    # Kept as a compatibility/test seam for the deterministic offline benchmark,
    # which temporarily sets this symbol to None before replacing the client.
    from openai import OpenAI
except ImportError:
    OpenAI = None


SYSTEM_PROMPT = IMPORTED_SYSTEM_PROMPT
OPTIMIZATION_SUFFIX = IMPORTED_OPTIMIZATION_SUFFIX
TOOLS = IMPORTED_TOOLS


MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ITERATIONS = 16  # 复杂天线设计可能需要 10+ 步原语调用

# ── 低价值消息过滤 ──
from cst_agent_workbench.agent.context_builder import _is_noise_message



class CSTAgent(FastPathMixin, PatchFastExecutorMixin, PatchOptimizerMixin):
    """CST 仿真 Agent 主类。

    架构分层：
    - 编排层（本文件）：chat() 入口、消息组装、trace 生命周期管理
    - 执行层（runtime.py）：run_agent_turn() — tool loop + plan update + plan eval
    - 规划层（llm_planner.py）：call_planner_llm() — LLM Planner，输出结构化 Plan
    - 领域层（mixins）：PatchFastExecutorMixin — 贴片天线快速建模
                        PatchOptimizerMixin   — 贴片参数优化策略
    - 状态层（session.py）：AgentSession — 单一 source of truth
                            session.artifacts  — 所有结果/VBA/贴片请求
                            session.memory     — 结构化工作记忆
                            session.trace      — 运行时追踪状态
    - 工具层（tool_runtime.py）：工具路由与执行
    """

    def __init__(self, cst_controller: CSTController):
        self.cst = cst_controller
        self.results = ResultsReader()
        self.opt_state = OptimizationState()
        self.session = AgentSession(session_id=uuid.uuid4().hex[:12])
        self.session.bind_optimization_state(self.opt_state)

        memory_dir = config.AGENT_MEMORY_DIR
        # session_id 每次进程随机生成，按它拼的路径必然是新文件：
        # session 文件只作为本次运行的调试产物写出，不做跨进程恢复；
        # 跨进程记忆走 persistent_memory.json（constraints/lessons/failures 白名单）。
        self._memory_path = os.path.join(memory_dir, "sessions", f"{self.session.session_id}.json")
        self._persistent_memory_path = os.path.join(memory_dir, "persistent_memory.json")
        self._tool_use_memory_path = os.path.join(memory_dir, "tool_use_memory.json")
        self.session.tool_use_memory = load_tool_use_memory(self._tool_use_memory_path)
        prune_session_memory_files(os.path.join(memory_dir, "sessions"), keep=20)
        persistent_memory = load_memory(self._persistent_memory_path)
        if persistent_memory is not None:
            merge_persistent_memory(self.session.memory, persistent_memory)
            logger.debug("persistent memory merged from %s", self._persistent_memory_path)
        self.session.metadata["memory_scope"] = {
            "session_memory_path": self._memory_path,
            "persistent_memory_path": self._persistent_memory_path,
            "session_memory_loaded": False,  # session 文件仅写出，不回读
            "persistent_memory_loaded": persistent_memory is not None,
            "tool_use_memory_path": self._tool_use_memory_path,
            "persistent_fields": [
                "conversation.constraints",
                "decisions.recent_strategies",
                "decisions.failure_reasons",
            ],
        }
        self.session.metadata["tool_use_memory_path"] = self._tool_use_memory_path

        self.token_stats = {"prompt": 0, "completion": 0, "calls": 0, "cached": 0, "cache_write": 0}
        self.tools = TOOLS
        self._optimization_mode = False  # 优化模式：使用精简上下文
        self._patch_feed_strategy = "microstrip"
        self._fast_path_counter = 0
        self.last_execution_mode = ""
        self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False}

        if OpenAI is None:
            self.client = None
        else:
            try:
                self.client = create_model_provider_client(
                    protocol=config.MODEL_API_PROTOCOL,
                    api_key=config.MODEL_API_KEY,
                    base_url=config.MODEL_BASE_URL,
                    max_output_tokens=config.MODEL_MAX_OUTPUT_TOKENS,
                    prompt_cache_enabled=config.MODEL_PROMPT_CACHE_ENABLED,
                    prompt_cache_key=config.MODEL_PROMPT_CACHE_KEY,
                    prompt_cache_ttl=config.MODEL_PROMPT_CACHE_TTL,
                )
            except RuntimeError as exc:
                logger.warning("model provider client unavailable: %s", exc)
                self.client = None
        self.model = config.MODEL_NAME

        self._agent_brain_name = config.AGENT_BRAIN
        self._pi_brain = None
        if self._agent_brain_name not in {"native", "pi"}:
            raise ValueError(
                f"未知 AGENT_BRAIN={self._agent_brain_name!r}；仅支持 'native' 或 'pi'。"
            )
        if self._agent_brain_name == "pi":
            from cst_agent_workbench.agent.pi_brain import PiAgentBrain

            self._pi_brain = PiAgentBrain.from_config()
        self.session.metadata["agent_brain"] = self._agent_brain_name
        self.session.metadata["model_provider"] = {
            "protocol": config.PI_API_PROTOCOL if self._agent_brain_name == "pi" else config.MODEL_API_PROTOCOL,
            "model": config.PI_MODEL if self._agent_brain_name == "pi" else self.model,
            "prompt_cache_enabled": (
                config.PI_PROMPT_CACHE_ENABLED
                if self._agent_brain_name == "pi"
                else config.MODEL_PROMPT_CACHE_ENABLED
            ),
        }

        self._failure_recovery_engine = FailureRecoveryEngine()
        self._failure_recovery_engine.register_default_actions()

    def _format_mm(self, value: float) -> str:
        return _format_mm(value)

    def _record_runtime_event(self, phase: str, name: str, success: bool, message: str, description: str):
        self.tool_events.append({
            "phase": phase,
            "tool_name": name,
            "success": success,
            "message": message,
            "description": description,
        })
        self._enforce_retention()

    def _enforce_retention(self) -> None:
        enforce = getattr(getattr(self, "session", None), "enforce_retention", None)
        if callable(enforce):
            enforce()

    # 快路径 mixin 使用的别名（历史上是一份逐字重复的实现）
    _record_fast_path_event = _record_runtime_event

    @property
    def history(self) -> List[Dict]:
        return self.session.history

    @history.setter
    def history(self, value: List[Dict]) -> None:
        self.session.history = list(value or [])

    @property
    def tool_events(self) -> List[Dict]:
        return self.session.tool_events

    @tool_events.setter
    def tool_events(self, value: List[Dict]) -> None:
        self.session.tool_events = list(value or [])

    @property
    def last_results(self) -> Dict:
        return self.session.artifacts.last_results

    @last_results.setter
    def last_results(self, value: Dict) -> None:
        self.session.artifacts.last_results = dict(value or {})
        self._refresh_session_memory_from_runtime()

    @property
    def last_farfield_results(self) -> Dict:
        return self.session.artifacts.last_farfield_results

    @last_farfield_results.setter
    def last_farfield_results(self, value: Dict) -> None:
        self.session.artifacts.last_farfield_results = dict(value or {})
        self._refresh_session_memory_from_runtime()

    @property
    def last_vba(self) -> str:
        return self.session.artifacts.last_vba

    @last_vba.setter
    def last_vba(self, value: str) -> None:
        self.session.artifacts.last_vba = value or ""

    @property
    def last_tool_message(self) -> str:
        return self.session.artifacts.last_tool_message

    @last_tool_message.setter
    def last_tool_message(self, value: str) -> None:
        self.session.artifacts.last_tool_message = value or ""

    @property
    def last_patch_request(self) -> Optional[Any]:
        return self.session.artifacts.last_patch_request

    @last_patch_request.setter
    def last_patch_request(self, value) -> None:
        self.session.artifacts.last_patch_request = value

    @property
    def current_run_id(self):
        return self.session.trace.current_run_id

    @current_run_id.setter
    def current_run_id(self, value) -> None:
        self.session.trace.current_run_id = value

    @property
    def current_trace(self):
        return self.session.trace.current_trace

    @current_trace.setter
    def current_trace(self, value) -> None:
        self.session.trace.current_trace = value

    @property
    def trace_history(self):
        return self.session.trace.trace_history

    @trace_history.setter
    def trace_history(self, value) -> None:
        self.session.trace.trace_history = list(value or [])

    @property
    def selected_trace_run_id(self):
        return self.session.trace.selected_trace_run_id

    @selected_trace_run_id.setter
    def selected_trace_run_id(self, value) -> None:
        self.session.trace.selected_trace_run_id = value

    @property
    def trace_enabled(self) -> bool:
        return self.session.trace.trace_enabled

    @trace_enabled.setter
    def trace_enabled(self, value: bool) -> None:
        self.session.trace.trace_enabled = bool(value)

    @property
    def trace_retention_limit(self) -> int:
        return self.session.trace.trace_retention_limit

    @trace_retention_limit.setter
    def trace_retention_limit(self, value: int) -> None:
        self.session.trace.trace_retention_limit = int(value)

    @property
    def _active_tool_call_id(self):
        return self.session.trace.active_tool_call_id

    @_active_tool_call_id.setter
    def _active_tool_call_id(self, value) -> None:
        self.session.trace.active_tool_call_id = value

    def _refresh_session_memory_from_runtime(self, persist: bool = False) -> None:
        """把运行时状态同步进 structured memory。

        persist=False（默认）只更新内存，供工具循环等热路径高频调用；
        persist=True 时额外落盘（session 调试文件 + persistent 白名单），
        只在轮次/优化轮结束等低频节点调用，避免每次工具调用写 4 次磁盘。
        """
        model_summary = get_model_summary()
        last_results_summary = self._summarize_last_results()
        farfield = self.last_farfield_results or {}
        if farfield.get("success"):
            last_results_summary = {
                "available": True,
                "success": True,
                "item": farfield.get("item", ""),
                "type": "farfield",
                "message": farfield.get("message", ""),
                "frequency_ghz": farfield.get("frequency_ghz"),
                "cut_type": farfield.get("cut_type", ""),
                "cut_value_deg": farfield.get("cut_value_deg"),
                "export_backend": farfield.get("export_backend", ""),
                "points": len(farfield.get("plot_data") or []),
            }
        MemoryManager.update_workspace(
            self.session.memory,
            project_path=self.cst.project_path or "",
            model_summary=model_summary,
            parameter_summary=model_summary.get("parameters", {}),
            last_results_summary=last_results_summary,
        )
        session_metadata = getattr(self.session, "metadata", None)
        if not isinstance(session_metadata, dict):
            session_metadata = {}
            self.session.metadata = session_metadata
        memory_scope_metadata = session_metadata.setdefault("memory_scope", {})
        memory_scope_metadata["project_scope"] = project_scope_from_path(
            self.session.memory.workspace.project_path
        )
        try:
            from cst_agent_workbench.rag.knowledge_base import design_signature_from_request

            self.session.metadata["current_design_signature"] = design_signature_from_request(
                self.session.artifacts.last_patch_request
            )
        except Exception as exc:
            logger.debug("current design signature unavailable: %s", exc)
            self.session.metadata["current_design_signature"] = ""
        best_record = self.opt_state.get_best_record() if hasattr(self.opt_state, "get_best_record") else None
        if best_record is None:
            best_round = getattr(self.opt_state, "best_round", 0) or 0
            history = list(getattr(self.opt_state, "history", []) or [])
            for item in history:
                if item.get("round") == best_round:
                    best_record = item
                    break
        best_so_far = {}
        if best_record:
            best_so_far = {
                "round": best_record.get("round", 0),
                "metric_value": best_record.get("metric_value"),
                "changed_params": best_record.get("changed_params", ""),
            }
        MemoryManager.update_decisions(self.session.memory, best_so_far=best_so_far)
        if not persist:
            return
        memory_path = getattr(self, "_memory_path", None)
        if memory_path:
            save_memory(self.session.memory, memory_path)
        persistent_memory_path = getattr(self, "_persistent_memory_path", None)
        if persistent_memory_path:
            save_persistent_memory(
                self.session.memory,
                persistent_memory_path,
                min_confidence=config.RAG_LESSON_MIN_CONFIDENCE,
            )

    def _remember_user_goal(self, user_message: str) -> None:
        text = (user_message or "").strip()
        if not text:
            return
        memory = self.session.memory
        goal = memory.conversation.user_goal
        if should_replace_user_goal(text, goal):
            MemoryManager.update_conversation(memory, user_goal=text)
            CSTAgent._record_memory_event(
                self,
                "user_goal_updated",
                {"previous": goal[:200], "current": text[:200]},
            )
        if memory.conversation.pending_questions:
            MemoryManager.update_conversation(memory, pending_questions=[])
            CSTAgent._record_memory_event(
                self, "pending_questions_cleared", {"reason": "new_user_turn"}
            )

    def _remember_constraints(self, user_message: str, planner_constraints: List[str] | None = None) -> None:
        memory = getattr(self.session, "memory", None)
        if memory is None:
            return
        session_metadata = getattr(self.session, "metadata", None)
        if not isinstance(session_metadata, dict):
            session_metadata = {}
            self.session.metadata = session_metadata
        scope = session_metadata.get("memory_scope", {}).get("project_scope", "")
        cst = getattr(self, "cst", None)
        delta = apply_constraints(
            memory,
            user_message=user_message,
            project_scope=scope or project_scope_from_path(getattr(cst, "project_path", "") or ""),
            planner_constraints=planner_constraints or [],
        )
        if delta["added"] or delta["removed"] or delta["clear_all"]:
            CSTAgent._record_memory_event(self, "constraints_updated", delta)

    def _record_memory_event(self, action: str, details: Dict[str, Any] | None = None) -> None:
        event = {"action": str(action), "details": dict(details or {})}
        session_metadata = getattr(self.session, "metadata", None)
        if not isinstance(session_metadata, dict):
            session_metadata = {}
            self.session.metadata = session_metadata
        current_turn = session_metadata.setdefault("memory_events_current_turn", [])
        current_turn.append(event)
        del current_turn[:-30]
        current_trace = getattr(self, "current_trace", None)
        if isinstance(current_trace, dict):
            trace_events = current_trace.setdefault("memory_events", [])
            trace_events.append(dict(event))
            del trace_events[:-50]

    def _remember_recent_exchange(self, user_message: str, assistant_message: str) -> None:
        user_text = (user_message or "").strip()
        assistant_text = (assistant_message or "").strip()
        summary_parts = []
        if user_text:
            summary_parts.append(f"用户最近请求: {user_text[:200]}")
        if assistant_text:
            summary_parts.append(f"最近回复摘要: {assistant_text[:300]}")
        if summary_parts:
            MemoryManager.update_conversation(self.session.memory, recent_summary="\n".join(summary_parts))
        pending_questions = extract_pending_questions(assistant_message)
        MemoryManager.update_conversation(
            self.session.memory,
            pending_questions=pending_questions,
        )
        if pending_questions:
            CSTAgent._record_memory_event(
                self,
                "pending_questions_updated",
                {"questions": pending_questions},
            )

    def _remember_failure(self, reason: str) -> None:
        if not reason:
            return
        metadata = getattr(self.session, "metadata", {}) or {}
        scope = metadata.get("memory_scope", {}).get("project_scope", "")
        if not scope:
            scope = project_scope_from_path(getattr(getattr(self, "cst", None), "project_path", ""))
        MemoryManager.update_decisions(
            self.session.memory,
            failure_reason=reason,
            project_scope=scope,
        )

    def _remember_strategy(self, *, round_num: int, strategy: str = "", proposal_reason: str = "", changed_params: str = "", metric_value=None, improved: bool = False, met: bool = False) -> None:
        metadata = getattr(self.session, "metadata", {}) or {}
        scope = metadata.get("memory_scope", {}).get("project_scope", "")
        if not scope:
            scope = project_scope_from_path(getattr(getattr(self, "cst", None), "project_path", ""))
        MemoryManager.update_decisions(
            self.session.memory,
            strategy_entry={
                "round": round_num,
                "strategy": strategy,
                "proposal_reason": proposal_reason,
                "changed_params": changed_params,
                "metric_value": metric_value,
                "improved": improved,
                "met": met,
            },
            project_scope=scope,
        )

    def _remember_rollback(self, *, round_num: int, reason: str, snapshot: Dict | None = None) -> None:
        MemoryManager.update_decisions(
            self.session.memory,
            rollback_point={
                "round": round_num,
                "reason": reason,
                "snapshot": dict(snapshot or {}),
            },
        )

    def set_optimization_mode(self, enabled: bool):
        """切换优化模式。优化模式下使用精简上下文以降低 token。"""
        self._optimization_mode = enabled
        self._refresh_session_memory_from_runtime()

    def _get_last_action_mode(self) -> str:
        mode = self.last_chat_status.get("mode", "") or self.last_execution_mode
        if mode == "meta_reply":
            return self.last_execution_mode
        return mode

    def _build_meta_llm_query_response(self, user_message: str) -> Optional[str]:
        if not is_meta_llm_query(user_message):
            return None

        mode = self._get_last_action_mode()
        if mode == "fast_path":
            return (
                "对，上一条没有调用大模型，走的是 fast path。"
                "\n\n这类追问现在应该只做解释，不会重建模型，也不会再次求解。"
            )
        if mode == "llm_routed_fast_path":
            return (
                "有，上一条用了大模型做路由判断，但真正的建模执行走的是 fast path。"
                "\n\n也就是说，大模型主要负责理解上下文和判断能不能套模板，具体尺寸计算和建模还是走确定性的快路径。"
            )
        if mode == "programmatic_optimizer":
            return (
                "上一条没有调用通用大模型，走的是程序化优化后端。"
                "\n\n这类追问现在应该只做解释，不会重建模型，也不会再次求解。"
            )
        if mode == "llm_path":
            return (
                "有，上一条走的是通用大模型路径。"
                "\n\n这类追问现在应该只做解释，不会重建模型，也不会再次求解。"
            )
        return (
            "这句更像是在追问上一条的执行方式。"
            "\n\n当前我不会因为这种追问去重建模型或再次求解；如果你要我重做，我会等你明确下指令。"
        )

    def _build_optimization_context(self, user_message: str, feed_strategy: str = "microstrip") -> List[Dict]:
        from cst_agent_workbench.agent.context_builder import build_optimization_context
        return build_optimization_context(
            user_message=user_message,
            feed_strategy=feed_strategy,
            session=self.session,
            opt_state=self.opt_state,
            last_results=self.last_results,
            history=self.history,
            system_prompt=SYSTEM_PROMPT,
        )

    def _finalize_short_circuit_response(
        self,
        *,
        user_entry: Dict,
        pending_history: List[Dict],
        user_message: str,
        final_text: str,
        mode: str,
        ok: bool,
        had_tool_failure: bool,
        tool_names: List[str] | None = None,
        observation: str = "",
        error: str = "",
        tool_event_start: int | None = None,
        replan_with_llm: bool = True,
    ) -> str:
        tool_names = list(tool_names or [])
        owns_trace = False
        if self.current_trace is not None and self.current_trace.get("status") == "running":
            pass
        else:
            start_trace_run(
                self,
                user_input=user_entry,
                working_messages=[{"role": "system", "content": SYSTEM_PROMPT}, user_entry],
                filtered_history=[],
                pending_history=pending_history,
            )
            owns_trace = True
        if isinstance(self.current_trace, dict):
            self.current_trace["memory_events"] = [
                dict(item)
                for item in getattr(self.session, "metadata", {}).get(
                    "memory_events_current_turn", []
                )
            ]

        pending_history.append({"role": "assistant", "content": final_text})
        self.history.extend(pending_history)
        self._enforce_retention()
        update_plan_after_turn(
            session=self.session,
            assistant_text=final_text,
            had_tool_calls=bool(tool_names),
            had_tool_failure=had_tool_failure,
            tool_names=tool_names,
            observation=observation or final_text[:200],
            final_action="tool_then_answer" if tool_names else "answer_only",
        )
        self._append_short_circuit_trace_turn(
            user_entry=user_entry,
            final_text=final_text,
            tool_event_start=tool_event_start,
        )
        plan_eval = evaluate_optimization_next_action(
            session=self.session,
            had_tool_failure=had_tool_failure,
            result_available=bool(self.last_results or self.last_farfield_results),
            client=self.client,
            model=self.model,
            user_message=user_message,
            replan_with_llm=replan_with_llm,
        )
        self._sync_current_trace_plan_state(plan_eval)
        self.last_chat_status = {
            "ok": ok,
            "error": error or "",
            "had_tool_failure": had_tool_failure,
            "mode": mode,
        }
        self.last_execution_mode = mode
        sync_plan_to_memory(session=self.session)
        self._remember_recent_exchange(user_message, final_text)
        if not ok or had_tool_failure:
            self._remember_failure(error or final_text)
        self._refresh_session_memory_from_runtime(persist=True)
        if owns_trace:
            finish_trace_run(
                self,
                status="completed" if ok else "failed",
                final_response=final_text,
                error=error or "",
            )
        return final_text

    def _append_short_circuit_trace_turn(
        self,
        *,
        user_entry: Dict,
        final_text: str,
        tool_event_start: int | None,
    ) -> None:
        """Project fast-path/runtime events into the trace detail view.

        Fast paths intentionally bypass the LLM tool loop, so they do not get
        tool-call trace entries from tool_runtime.execute_tool(). This keeps the
        UI trace explainable without changing the execution path.
        """
        if self.current_trace is None:
            return

        new_events: List[Dict[str, Any]] = []
        if tool_event_start is not None and 0 <= tool_event_start <= len(self.tool_events):
            new_events = [
                event for event in self.tool_events[tool_event_start:]
                if isinstance(event, dict)
            ]
        if not new_events:
            return

        event_tool_names = [
            str(event.get("tool_name") or "")
            for event in new_events
            if event.get("tool_name")
        ]
        tool_schemas = [
            tool for tool in (self.tools or [])
            if ((tool.get("function") or {}).get("name") if isinstance(tool, dict) else "") in event_tool_names
        ]
        tool_calls_raw = [
            {
                "id": f"event_{idx + 1}",
                "type": "function",
                "function": {
                    "name": str(event.get("tool_name") or "runtime_event"),
                    "arguments": "{}",
                },
            }
            for idx, event in enumerate(new_events)
        ]
        turn = append_llm_turn(
            self,
            turn_index=len((self.current_trace or {}).get("turns", [])) + 1,
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=datetime.now().isoformat(timespec="seconds"),
            model="fast-path",
            request_messages=[{"role": "system", "content": SYSTEM_PROMPT}, user_entry],
            tools=tool_schemas,
            assistant_content=final_text,
            tool_calls_raw=tool_calls_raw,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
        if turn is None:
            return

        for idx, event in enumerate(new_events):
            success = coerce_success(event.get("success", False))
            preview = str(event.get("description") or event.get("message") or event.get("tool_name") or "")
            turn["tool_calls"].append({
                "tool_call_id": f"event_{idx + 1}",
                "tool_name": str(event.get("tool_name") or "runtime_event"),
                "phase": str(event.get("phase") or ""),
                "arguments": {},
                "started_at": turn.get("started_at"),
                "finished_at": turn.get("finished_at"),
                "duration_ms": None,
                "success": success,
                "status_label": "success" if success else "failed",
                "result_kind": "runtime_event",
                "result_preview": preview[:1000],
                "result_full": dict(event),
                "error": "" if success else str(event.get("message") or ""),
                "source": "short_circuit_runtime_event",
            })

    def _build_user_entry(self, user_message: str, images: list | None) -> Dict:
        if not images:
            return {"role": "user", "content": user_message}

        import base64

        content_parts = []
        if user_message:
            content_parts.append({"type": "text", "text": user_message})
        mime_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }
        for img_path in images:
            try:
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                ext = img_path.rsplit(".", 1)[-1].lower()
                mime = mime_map.get(ext, "image/png")
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{img_data}"},
                    }
                )
            except Exception as exc:
                content_parts.append({"type": "text", "text": f"[图片编码失败: {exc}]"})
        return {"role": "user", "content": content_parts}

    def _ensure_active_plan_for_message(self, user_message: str) -> None:
        current_plan_summary = summarize_plan(self.session.active_plan)
        normalized_message = str(user_message or "").strip()
        session_metadata = getattr(self.session, "metadata", None)
        if not isinstance(session_metadata, dict):
            session_metadata = {}
            self.session.metadata = session_metadata
        planned_message = str(session_metadata.get("active_plan_user_message") or "").strip()
        # "needs_replan"：上一轮的重规划信号没有被编排层消费（如图路径预算耗尽后
        # 直接结束），新消息到来时重建计划，避免带着过期的失败 plan 继续执行。
        # 每条新的用户消息都是新的 plan goal。旧实现只看 plan.status；由于一整个
        # tool loop 每轮只推进一个 step，成功回答后 plan 常保持 active，下一条完全
        # 不同的目标会错误复用旧工具白名单。保存原始消息可让同一轮内重复调用保持
        # 幂等，同时确保跨轮目标一定重建。
        if (
            self.session.active_plan is None
            or not current_plan_summary.get("intent_kind")
            or current_plan_summary.get("status") in {"completed", "failed", "needs_replan"}
            or planned_message != normalized_message
        ):
            _, plan_usage = build_initial_plan_with_usage(session=self.session, user_message=user_message, optimization_mode=self._optimization_mode, client=self.client, model=self.model)
            session_metadata["active_plan_user_message"] = normalized_message
            # 新的 plan goal 意味着新的故障边界：恢复动作的尝试预算随之重置。
            session_metadata.pop("failure_recovery_attempts", None)
            intent = (self.session.active_plan or {}).get("intent", {})
            planner_constraints = list(intent.get("constraints") or []) if isinstance(intent, dict) else []
            self._remember_constraints(user_message, planner_constraints)
            memory = getattr(self.session, "memory", None)
            if isinstance(intent, dict) and memory is not None:
                intent["constraints"] = list(self.session.memory.conversation.constraints)
            if plan_usage:
                self.token_stats["prompt"] += plan_usage.get("prompt_tokens", 0)
                self.token_stats["completion"] += plan_usage.get("completion_tokens", 0)
                self.token_stats["cached"] += plan_usage.get("cached_tokens", 0)
                self.token_stats["cache_write"] += plan_usage.get("cache_write_tokens", 0)
                self.token_stats["calls"] += 1 if plan_usage.get("prompt_tokens", 0) > 0 else 0

    def _update_patch_feed_strategy_from_message(self, user_message: str) -> None:
        explicit_feed_strategy = get_patch_feed_strategy(user_message)
        if explicit_feed_strategy == "probe":
            self._patch_feed_strategy = "probe"
        elif not self._optimization_mode:
            self._patch_feed_strategy = "microstrip"

    def _try_short_circuit_response(
        self,
        *,
        user_entry: Dict,
        pending_history: List[Dict],
        user_message: str,
        images: list | None,
        replan_with_llm: bool = True,
    ) -> Optional[str]:
        if images:
            return None

        meta_reply = self._build_meta_llm_query_response(user_message)
        if meta_reply is not None:
            return self._finalize_short_circuit_response(
                user_entry=user_entry,
                pending_history=pending_history,
                user_message=user_message,
                final_text=meta_reply,
                mode="meta_reply",
                ok=True,
                had_tool_failure=False,
                replan_with_llm=replan_with_llm,
            )

        fast_paths = [
            (self._run_pixel_patch_fast_path, "build_pixel_patch_fast", "pixel_patch_fast_path"),
            (self._run_rectangular_patch_fast_path, "build_rectangular_patch_fast", "fast_path"),
            (self._run_dipole_fast_path, "build_dipole_fast", "dipole_fast_path"),
        ]
        for runner, tool_name, default_mode in fast_paths:
            tool_event_start = len(self.tool_events)
            path_text = runner(user_message)
            if path_text is None:
                continue
            ok = self.last_chat_status.get("ok", True)
            return self._finalize_short_circuit_response(
                user_entry=user_entry,
                pending_history=pending_history,
                user_message=user_message,
                final_text=path_text,
                mode=self.last_chat_status.get("mode", "") or default_mode,
                ok=ok,
                had_tool_failure=not ok,
                tool_names=[tool_name],
                observation=path_text[:200],
                error=self.last_chat_status.get("error", ""),
                tool_event_start=tool_event_start,
                replan_with_llm=replan_with_llm,
            )
        return None

    def _build_offline_notice(self) -> str:
        if self.cst.offline_mode or not self.cst.is_connected():
            return "[离线模式] CST 未连接，以下工具调用将在离线模式下运行。\n\n"
        return ""

    def _try_llm_unavailable_response(
        self,
        *,
        offline_notice: str,
        user_entry: Dict,
        pending_history: List[Dict],
        user_message: str,
    ) -> Optional[str]:
        if self._agent_brain_name == "pi":
            if config.PI_API_KEY:
                return None
            final_text = offline_notice + "Pi Harness 未配置 PI_API_KEY（可留空以复用 MODEL_API_KEY）。"
        elif self.client is None:
            final_text = offline_notice + "无法初始化当前模型协议客户端，请检查依赖与配置。"
        elif not config.MODEL_API_KEY:
            final_text = offline_notice + "未配置 MODEL_API_KEY（或兼容的 OPENAI_API_KEY），请在 .env 中设置。"
        else:
            return None
        return self._finalize_short_circuit_response(
            user_entry=user_entry,
            pending_history=pending_history,
            user_message=user_message,
            final_text=final_text,
            mode="llm_path",
            ok=False,
            had_tool_failure=False,
            error=final_text,
        )

    def _build_chat_working_messages(self, user_message: str, user_entry: Dict, filtered_history: List[Dict]) -> List[Dict]:
        feed_strategy = self._patch_feed_strategy
        if self._optimization_mode and self.opt_state.round > 0:
            return self._build_optimization_context(user_message, feed_strategy=feed_strategy)

        active_prompt = SYSTEM_PROMPT + ("\n\n" + OPTIMIZATION_SUFFIX if self._optimization_mode else "")
        working_messages: List[Dict] = [{"role": "system", "content": active_prompt}]
        from cst_agent_workbench.agent.memory import recall_memory
        try:
            recalled = recall_memory(
                self.session.memory, user_message,
                scopes=["project", "session"], k=3, entry_types=["lesson", "failure"],
                client=self.client,
                min_score=config.MEMORY_RECALL_MIN_SCORE,
                min_overlap=1,
                project_scope=self.session.metadata["memory_scope"].get("project_scope", ""),
            )
        except Exception as exc:
            logger.warning("chat memory recall failed; continuing without recalled memory: %s", exc)
            record_observability_degradation(
                self,
                component="chat_memory_recall",
                fallback="continue_without_recalled_memory",
                error=exc,
            )
            recalled = []
        lessons = [entry.text for entry in recalled]
        self.session.metadata["planner_memory_recall"] = [entry.to_dict() for entry in recalled]
        # chat 路径此前只把 lesson 拼进 prompt，没有任何"这条经验有没有起作用"的记录，
        # 于是"memory 让 agent 更好"在通用对话路径上不可证伪。这里记录本轮注入了什么、
        # 花了多少 token，轮末由 _record_chat_memory_impact 补上实际是否被采纳。
        self.session.metadata["chat_memory_impact"] = {
            "recalled_count": len(recalled),
            "recalled_ids": [entry.id for entry in recalled],
            "injected_chars": sum(len(text) for text in lessons),
            "referenced_params": sorted(self._memory_referenced_params(recalled)),
            "influenced": None,  # 轮末填充
            "influenced_by": [],
        }
        plan_context = build_plan_context_text(self.session.active_plan, lessons=lessons)
        if plan_context:
            working_messages.append({"role": "system", "content": plan_context})
        rag_context = self.session.metadata.get("last_rag_context") or {}
        if rag_context.get("query") == user_message:
            rag_lines: List[str] = []
            documents = list(rag_context.get("documents") or [])[:3]
            rules = list(rag_context.get("rules") or [])[:3]
            if rules:
                rag_lines.append("[本轮检索到的领域规则]")
                rag_lines.extend(f"- {text[:200]}" for text in rules)
            if documents:
                rag_lines.append("[本轮检索到的CST官方文档]")
                for hit in documents:
                    if isinstance(hit, dict):
                        source = str(hit.get("source_path") or hit.get("source") or "unknown")
                        chunk_idx = hit.get("chunk_idx")
                        dense_score = hit.get("dense_score", hit.get("score"))
                        rerank_score = hit.get("rerank_score")
                        location = f"source={source}"
                        if chunk_idx not in (None, ""):
                            location += f", chunk={chunk_idx}"
                        if rerank_score is not None:
                            location += f", rerank={float(rerank_score):.3f}"
                        if dense_score is not None:
                            location += f", dense={float(dense_score):.3f}"
                        rag_lines.append(
                            f"- [{location}] {str(hit.get('text') or '')[:500]}"
                        )
                    else:
                        rag_lines.append(f"- {str(hit)[:500]}")
            if rag_lines:
                working_messages.append(
                    {
                        "role": "system",
                        "content": (
                            "以下检索内容可用于回答和工具参数决策；不得把文档内容伪装成已执行结果。"
                            "对于官方文档问答，事实范围必须限制在这些检索片段直接支持的内容；证据不足时明确说"
                            "当前片段未覆盖。不得凭常识补充片段中没有出现的菜单步骤、字段、推荐值、参数含义或因果解释。"
                            "凡是依据官方文档给出的事实性结论，必须在对应句末引用检索结果中的原始来源，"
                            "格式为 [source=完整路径, chunk=编号]；不得编造未检索到的来源。\n"
                        )
                        + "\n".join(rag_lines),
                    }
                )
        last_patch_context = self._build_last_patch_request_context()
        if last_patch_context:
            working_messages.append({"role": "system", "content": last_patch_context})
        if feed_strategy == "probe":
            working_messages.append(
                {
                    "role": "system",
                    "content": "[贴片馈电策略]\n保持使用 probe-fed/coax-fed 结构，不要退化成理想竖直离散端口。保持 probe-fed，保留 ground clearance，使用 discrete port 同轴-到-贴片馈电。不要改为微带线馈电。",
                }
            )
        else:
            working_messages.append(
                {
                    "role": "system",
                    "content": "[贴片馈电策略]\n保持使用 microstrip line feed 结构，不要退化成地到贴片的理想竖直离散端口。保持微带线馈电，使用 discrete port 从微带线末端馈电。不要改为同轴馈电。",
                }
            )
        working_messages.extend([
            *filtered_history,
            user_entry,
        ])
        return working_messages

    @staticmethod
    def _memory_referenced_params(recalled: List[Any]) -> set:
        """收集召回经验里明确指向的参数/工具名。

        优先读 reflection 写入的结构化 rule（`metadata["rule"]["param"]`）；
        没有结构化 rule 的旧条目退回扫描文本中出现的已知参数名。
        """
        referenced: set = set()
        for entry in recalled or []:
            metadata = getattr(entry, "metadata", None) or {}
            rule = metadata.get("rule")
            if isinstance(rule, dict) and str(rule.get("param", "") or "").strip():
                referenced.add(str(rule["param"]).strip())
                continue
            text = str(getattr(entry, "text", "") or "")
            for name in ("patch_L", "patch_W", "feed_W", "feed_L", "inset_depth", "substrate_h", "copper_t"):
                if name in text:
                    referenced.add(name)
        return referenced

    def _record_chat_memory_impact(self, executed_tool_names: List[str]) -> None:
        """轮末判定本轮注入的经验是否真的影响了行为。

        判据是可观测的：召回经验指向的参数名，是否出现在本轮实际发生的工具调用
        参数里（或工具名本身）。这不是因果证明，但把"注入了 N 条经验"升级成
        "其中 M 条对应的动作真的发生了"，让 chat 路径的 memory 价值可被度量。
        """
        impact = self.session.metadata.get("chat_memory_impact")
        if not isinstance(impact, dict):
            return
        referenced = set(impact.get("referenced_params") or [])
        if not referenced:
            impact["influenced"] = False
            return
        haystack = " ".join(str(name) for name in (executed_tool_names or []))
        for event in self.tool_events[-len(executed_tool_names or []) or None:]:
            if isinstance(event, dict):
                haystack += " " + str(event.get("description", "")) + " " + str(event.get("message", ""))
        hits = sorted(name for name in referenced if name in haystack)
        impact["influenced"] = bool(hits)
        impact["influenced_by"] = hits

    def _start_chat_trace(self, user_entry: Dict, working_messages: List[Dict], filtered_history: List[Dict], pending_history: List[Dict]) -> tuple[bool, bool]:
        if self.current_trace is not None and self.current_trace.get("status") == "running":
            return True, False
        try:
            start_trace_run(
                self,
                user_input=user_entry,
                working_messages=working_messages,
                filtered_history=filtered_history,
                pending_history=pending_history,
            )
            # Planner retrieval runs before the Executor trace starts. Backfill
            # that already-computed context so a production ``agent.chat`` trace
            # remains a complete, replayable record of the RAG decision path.
            rag_context = self.session.metadata.get("last_rag_context") or {}
            if isinstance(self.current_trace, dict) and rag_context:
                query = str(rag_context.get("query") or "")
                trace_rag = {"query": query, "rules": [], "document": []}
                for rule in rag_context.get("rules") or []:
                    trace_rag["rules"].append(
                        {
                            "channel": "rule",
                            "query": query,
                            "text": str(rule or "")[:300],
                            "score": None,
                        }
                    )
                for raw_hit in rag_context.get("documents") or []:
                    hit = dict(raw_hit or {}) if isinstance(raw_hit, dict) else {"text": raw_hit}
                    record = {
                        "channel": "document",
                        "query": query,
                        "text": str(hit.get("text") or "")[:300],
                        "score": (
                            None if hit.get("score") is None else float(hit["score"])
                        ),
                    }
                    for field in (
                        "source",
                        "source_path",
                        "source_type",
                        "source_hash",
                        "page",
                        "chunk_idx",
                        "distance",
                        "matched_query",
                        "dense_score",
                        "rerank_score",
                        "rank_before",
                        "rank_after",
                        "reranker_model",
                        "rerank_matched_query",
                        "rerank_applied",
                    ):
                        if hit.get(field) not in (None, ""):
                            record[field] = hit[field]
                    trace_rag["document"].append(record)
                self.current_trace["rag_context"] = trace_rag
            if isinstance(self.current_trace, dict):
                self.current_trace["memory_events"] = [
                    dict(item)
                    for item in self.session.metadata.get("memory_events_current_turn", [])
                ]
            return True, True
        except Exception as _trace_exc:
            logger.warning("trace start failed (non-critical): %s", _trace_exc)
            record_observability_degradation(
                self,
                component="chat_trace_start",
                fallback="continue_without_run_trace",
                error=_trace_exc,
            )
            return False, False

    def _sync_current_trace_plan_state(self, plan_eval: Dict) -> None:
        if self.current_trace is None:
            return
        plan_summary = summarize_plan(self.session.active_plan)
        self.current_trace["plan_state"] = self.session.active_plan
        for turn in self.current_trace.get("turns", []):
            turn["active_step_title"] = plan_summary.get("active_step_title", "")
            turn["active_step_kind"] = plan_summary.get("active_step_kind", "")
            turn["replan_trigger"] = plan_eval.get("replan_trigger", "")

    def _append_runtime_llm_turn(self, payload: Dict[str, Any]):
        turn = append_llm_turn(
            self,
            turn_index=payload["turn_index"],
            started_at=payload["started_at"],
            finished_at=payload["finished_at"],
            model=payload["model"],
            request_messages=payload["request_messages"],
            tools=payload["tools"],
            assistant_content=payload["assistant_content"],
            tool_calls_raw=payload["tool_calls_raw"],
            usage=payload["usage"],
        )
        if isinstance(turn, dict):
            turn["context_budget"] = dict(payload.get("context_budget") or {})
        return turn

    def _set_active_tool_call_context(self, tool_call_id: str, tool_name: str) -> None:
        self._active_tool_call_id = tool_call_id

    def chat(self, user_message: str, images: list = None, skip_plan: bool = False) -> str:
        """处理用户消息，通过 Function Calling 执行工具调用并返回回复。

        skip_plan=True 仅用于已有上层计划的兼容调用，避免重复规划。生产路径默认
        False，由 agent.chat 建立计划，并允许一次有上限的同轮 replan retry；
        skip_plan 路径只返回 needs_replan 信号，不覆盖外部计划。
        """
        self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False}
        session_metadata = getattr(self.session, "metadata", None)
        if not isinstance(session_metadata, dict):
            session_metadata = {}
            self.session.metadata = session_metadata
        session_metadata["memory_events_current_turn"] = []
        self._refresh_session_memory_from_runtime()
        self._remember_user_goal(user_message)
        self._remember_constraints(user_message)
        replan_with_llm = not skip_plan

        user_entry = self._build_user_entry(user_message, images)
        pending_history: List[Dict] = [user_entry]
        if not skip_plan:
            self._ensure_active_plan_for_message(user_message)
        self._update_patch_feed_strategy_from_message(user_message)

        short_circuit_response = self._try_short_circuit_response(
            user_entry=user_entry,
            pending_history=pending_history,
            user_message=user_message,
            images=images,
            replan_with_llm=replan_with_llm,
        )
        if short_circuit_response is not None:
            return short_circuit_response

        offline_notice = self._build_offline_notice()
        llm_unavailable_response = self._try_llm_unavailable_response(
            offline_notice=offline_notice,
            user_entry=user_entry,
            pending_history=pending_history,
            user_message=user_message,
        )
        if llm_unavailable_response is not None:
            return llm_unavailable_response

        filtered_history = prepare_history_for_context(
            self.history,
            max_history_messages=MAX_HISTORY_MESSAGES,
            is_noise_message=_is_noise_message,
            truncate_tool_result=lambda msg: _truncate_tool_result(msg, optimization_mode=self._optimization_mode),
        )
        working_messages = self._build_chat_working_messages(user_message, user_entry, filtered_history)
        logger.debug("sending %d messages, optimization_mode=%s", len(working_messages), self._optimization_mode)

        trace_started = False
        trace_finished = False
        owns_trace = False
        try:
            trace_started, owns_trace = self._start_chat_trace(user_entry, working_messages, filtered_history, pending_history)
            loop_result = run_agent_turn(
                session=self.session,
                client=self.client,
                model=self.model,
                user_message=user_message,
                working_messages=working_messages,
                pending_history=pending_history,
                tools=self.tools,
                execute_tool_fn=self._execute_tool,
                token_stats=self.token_stats,
                offline_notice=offline_notice,
                optimization_mode=self._optimization_mode,
                max_tool_iterations=MAX_TOOL_ITERATIONS,
                result_available=bool(self.last_results or self.last_farfield_results),
                trace_turn_callback=self._append_runtime_llm_turn,
                trace_tool_context_callback=self._set_active_tool_call_context,
                replan_with_llm=replan_with_llm,
                loop_runner=self._pi_brain.run_tool_loop if self._pi_brain is not None else None,
            )
            plan_eval = getattr(loop_result, "plan_eval", {})
            if trace_started:
                self._sync_current_trace_plan_state(plan_eval)
            self._record_chat_memory_impact(loop_result.executed_tool_names)
            self.last_chat_status = {
                "ok": loop_result.ok,
                "error": loop_result.error,
                "had_tool_failure": loop_result.had_tool_failure,
                "mode": loop_result.final_mode,
            }
            self.history.extend(pending_history)
            self._enforce_retention()
            self.last_execution_mode = loop_result.final_mode
            sync_plan_to_memory(session=self.session)
            self._remember_recent_exchange(user_message, loop_result.final_text)
            if not loop_result.ok or loop_result.had_tool_failure:
                self._remember_failure(loop_result.error or loop_result.final_text)
            self._refresh_session_memory_from_runtime(persist=True)
            if trace_started and owns_trace:
                try:
                    finish_trace_run(
                        self,
                        status="completed" if loop_result.ok else "failed",
                        final_response=loop_result.final_text,
                        error=loop_result.error,
                    )
                    trace_finished = True
                except Exception as _trace_exc:
                    logger.warning("trace finish failed (non-critical): %s", _trace_exc)
                    record_observability_degradation(
                        self,
                        component="chat_trace_finish",
                        fallback="retain_current_trace_and_chat_result",
                        error=_trace_exc,
                    )
                    trace_finished = False
            return loop_result.final_text
        except Exception as exc:
            error_text = offline_notice + f"处理消息时发生错误：{exc}"
            self.last_chat_status = {
                "ok": False,
                "error": error_text,
                "had_tool_failure": self.last_chat_status.get("had_tool_failure", False),
                "mode": "llm_path",
            }
            self.history.extend(pending_history)
            self.history.append({"role": "assistant", "content": error_text})
            self._enforce_retention()
            self.last_execution_mode = "llm_path"
            self._remember_recent_exchange(user_message, error_text)
            self._remember_failure(str(exc))
            self._refresh_session_memory_from_runtime(persist=True)
            if trace_started and owns_trace and not trace_finished:
                try:
                    finish_trace_run(self, status="failed", final_response=error_text, error=str(exc))
                except Exception as _trace_exc:
                    logger.warning("trace finish (error path) failed (non-critical): %s", _trace_exc)
                    record_observability_degradation(
                        self,
                        component="chat_trace_finish_error_path",
                        fallback="retain_chat_error_without_finished_trace",
                        error=_trace_exc,
                    )
            return error_text

    def _get_phase(self, tool_name: str) -> str:
        return get_tool_phase(tool_name)

    def _execute_tool(self, tool_name: str, arguments: dict) -> str:
        return runtime_execute_tool(self, tool_name, arguments)

    def clear_history(self):
        """清空对话历史与最近一次脚本缓存。"""
        memory_scope = dict(self.session.metadata.get("memory_scope") or {})
        self.session.tool_approvals.clear()
        self.history = []
        self.session.active_plan = None
        self.last_vba = ""
        self.last_tool_message = ""
        self.tool_events = []
        self.last_results = {}
        self.last_farfield_results = {}
        self.session.artifacts.last_optimizer_result = {}
        self.session.artifacts.tool_results = {}
        self.session.artifacts.results_invalidated = False
        self.session.artifacts.results_invalidated_reason = ""
        self.opt_state.reset()
        self.token_stats = {"prompt": 0, "completion": 0, "calls": 0, "cached": 0, "cache_write": 0}
        self._optimization_mode = False
        self._patch_feed_strategy = "microstrip"
        self._fast_path_counter = 0
        self.last_execution_mode = ""
        self.last_chat_status = {"ok": True, "error": "", "had_tool_failure": False}
        self.last_patch_request = None
        self._active_tool_call_id = None
        for key in (
            "tool_filter",
            "planner_memory_recall",
            "chat_memory_impact",
            "optimizer_memory_recall",
            "optimizer_memory_impact",
            "active_plan_user_message",
            "last_rag_context",
            "failure_recovery_attempts",
        ):
            self.session.metadata.pop(key, None)
        # Result property setters refresh workspace memory and may recompute a
        # project scope. Clearing transient state must not rewrite the session's
        # persistence/scope contract, so restore the exact pre-clear metadata.
        self.session.metadata["memory_scope"] = memory_scope
        reset_trace_state(self)
        reset_created_objects()

    def _summarize_last_results(self) -> dict:
        return summarize_last_results(self.last_results, self.opt_state.target_freq)

    def get_runtime_snapshot(self) -> dict:
        return build_runtime_snapshot(self)

    def get_token_stats(self) -> dict:
        """返回累计 token 用量和估算费用。"""
        s = self.token_stats
        total = s["prompt"] + s["completion"]
        cost = (s["prompt"] * config.TOKEN_COST_PROMPT + s["completion"] * config.TOKEN_COST_COMPLETION) / 1_000_000
        cache_hit_rate = (s.get("cached", 0) / s["prompt"]) if s["prompt"] > 0 else 0.0
        return {
            **s,
            "total": total,
            "cache_hit_rate": round(cache_hit_rate, 4),
            # Provider-specific cache discounts differ; keep the historical
            # conservative estimate instead of pretending one universal price.
            "cost_usd": round(cost, 4),
        }
