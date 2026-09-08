import json
import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List

from cst_agent_workbench import config
from cst_agent_workbench.agent.llm_planner import call_planner_llm
logger = logging.getLogger(__name__)

TOOL_FILTER_ENABLED = True
ALLOW_ALL_TOOLS_FOR_DEBUG = False

from cst_agent_workbench.agent.context_summary import make_context_summary_message
from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.agent.memory import MemoryManager
from cst_agent_workbench.agent.plan_models import plan_from_dict
from cst_agent_workbench.agent.runtime_state import record_observability_degradation
from cst_agent_workbench.agent.planner import (
    build_initial_plan as planner_build_initial_plan,
    evaluate_replan_or_stop as planner_evaluate_replan_or_stop,
    summarize_plan,
    update_plan_after_turn as planner_update_plan_after_turn,
)


@dataclass
class ToolLoopResult:
    final_text: str
    final_mode: str
    ok: bool
    error: str
    had_tool_failure: bool
    executed_tool_names: List[str]
    successful_tool_names: List[str] | None = None
    approval_pending: bool = False
    offline_notice_cleared: bool = False


def _build_planner_context(session: Any, client: Any = None, user_message: str = "") -> str:
    """将 session memory + opt_state + 近3轮对话摘要拼成纯文字，供 Planner LLM 消费。"""
    lines: List[str] = []
    try:
        from cst_agent_workbench.agent.tools import TOOLS as _PLANNER_TOOLS

        tool_names = [_tool_name(tool) for tool in _PLANNER_TOOLS if _tool_name(tool)]
        if tool_names:
            lines.append("[Available tools] " + ", ".join(tool_names))
    except Exception as exc:
        logger.debug("planner tool catalog unavailable: %s", exc)
    mem = getattr(session, "memory", None)
    if mem is not None:
        if hasattr(mem, "conversation") and hasattr(mem, "workspace") and hasattr(mem, "decisions"):
            # StructuredMemory 结构化读取
            conv = mem.conversation
            ws = mem.workspace
            dec = mem.decisions
            user_goal = getattr(conv, "user_goal", "") or ""
            constraints = getattr(conv, "constraints", []) or []
            recent_summary = getattr(conv, "recent_summary", "") or ""
            if user_goal:
                lines.append(f"[Goal] {user_goal}")
            if constraints:
                lines.append(f"[Constraints] {', '.join(str(c) for c in constraints)}")
            if recent_summary:
                lines.append(f"[RecentSummary] {recent_summary}")
            project_path = getattr(ws, "project_path", "") or ""
            last_results = getattr(ws, "last_results_summary", {}) or {}
            if project_path:
                lines.append(f"[ProjectPath] {project_path}")
            if last_results:
                key_fields = {k: last_results[k] for k in ("min_s11_db", "min_freq_ghz") if k in last_results}
                result_str = str(key_fields if key_fields else last_results)[:200]
                lines.append(f"[LastResults] {result_str}")
            best_so_far = getattr(dec, "best_so_far", {}) or {}
            failure_reasons = getattr(dec, "failure_reasons", []) or []
            if best_so_far:
                lines.append(f"[BestSoFar] {str(best_so_far)[:150]}")
            if failure_reasons:
                recent_failures = failure_reasons[-2:]
                lines.append(f"[RecentFailures] {'; '.join(str(r) for r in recent_failures)}")
        else:
            summary = mem.get_summary() if hasattr(mem, "get_summary") else str(mem)[:300]
            lines.append(f"[Memory] {summary}")
    opt = getattr(session, "optimization_state", None)
    if opt:
        lines.append(f"[OptState] active={getattr(opt, 'active', False)}, "
                     f"round={getattr(opt, 'round', 0)}, "
                     f"best={getattr(opt, 'best_metric_value', None)}")
    history = getattr(session, "history", []) or []
    recent = [m for m in history if isinstance(m, dict) and m.get("role") in {"user", "assistant"}][-6:]
    for m in recent:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        lines.append(f"[{role}] {str(content)[:200]}")

    def _record_rag_trace(channel: str, scored_items: list) -> None:
        trace_state = getattr(session, "trace", None)
        current_trace = getattr(trace_state, "current_trace", None)
        if not isinstance(current_trace, dict):
            return
        key = "rules" if channel == "rule" else channel
        records = current_trace.setdefault("rag_context", {}).setdefault(key, [])
        for item in scored_items:
            if isinstance(item, dict):
                text = item.get("text")
                score = item.get("score")
            elif isinstance(item, tuple):
                text, score = item[0], item[1] if len(item) > 1 else None
            else:
                text, score = item, None
            record = {
                "channel": channel,
                "query": str(user_message or ""),
                "text": str(text or "")[:300],
                "score": None if score is None else float(score),
            }
            if isinstance(item, dict):
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
                    if item.get(field) not in (None, ""):
                        record[field] = item[field]
            records.append(record)

    def _format_document_hit(hit: dict, *, text_limit: int) -> str:
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
        return f"[{location}] {str(hit.get('text') or '')[:text_limit]}"

    # RAG: inject relevant design rules if client available
    try:
        from cst_agent_workbench.rag.knowledge_base import (
            design_signature_from_request,
            retrieve_official_document_hits,
            retrieve_antenna_rules,
        )
        rag_query = user_message or ""
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            metadata["last_rag_context"] = {"query": rag_query, "rules": [], "documents": []}
        artifacts = getattr(session, "artifacts", None)
        design_signature = design_signature_from_request(
            getattr(artifacts, "last_patch_request", None)
        )

        # P5 query 分类路由：按 query 类型路由到不同检索路径。
        # 术语类（"S11是什么"、"f0怎么算"）→ 静态规则 + PDF 检索
        # 操作类（"怎么建 waveport"、"VBA 怎么写"）→ 静态规则检索
        # 经验类（默认，含优化/调参/失败）→ 统一记忆检索
        rag_query_lower = rag_query.lower()
        is_operational = any(kw in rag_query or kw in rag_query_lower for kw in (
            "怎么", "如何", "vba", "waveport", "wave guide", "创建", "建",
            "execute", "build", "create", "script",
        ))

        rag_rule_pairs = (
            retrieve_antenna_rules(
                rag_query,
                client,
                design_signature=design_signature,
                with_scores=True,
                include_documents=False,
            )
            if client and rag_query else []
        )
        document_hits = (
            retrieve_official_document_hits(rag_query, client, top_k=3)
            if rag_query else []
        )
        if rag_rule_pairs or document_hits:
            curated_pairs = list(rag_rule_pairs)
            if curated_pairs:
                _record_rag_trace("rule", curated_pairs)
                lines.append("[相关设计规则]")
                for rule, _score in curated_pairs:
                    lines.append(f"- {rule[:160]}")
            if document_hits:
                _record_rag_trace("document", document_hits)
                lines.append("[CST官方文档]")
                for hit in document_hits:
                    lines.append(f"- {_format_document_hit(hit, text_limit=320)}")
            metadata = getattr(session, "metadata", None)
            if isinstance(metadata, dict):
                metadata["last_rag_context"] = {
                    "query": rag_query,
                    "rules": [str(text) for text, _score in curated_pairs],
                    "documents": [dict(hit) for hit in document_hits],
                }
        # P5: 操作类 query 跳过历史经验检索（操作类是"怎么做"，不需要优化 lesson）
        should_recall_memory = not is_operational
        if client and rag_query and should_recall_memory:
            # 统一记忆检索：生产默认只读取 canonical StructuredMemory lesson/failure。
            # legacy dynamic entries 仅由显式迁移开关启用，新反思不再双写。
            from cst_agent_workbench.agent.memory import project_scope_from_path, unified_recall
            recalled_lessons = unified_recall(
                mem,
                rag_query,
                client=client,
                design_signature=design_signature,
                k=3,
                min_score=config.MEMORY_RECALL_MIN_SCORE,
                project_scope=project_scope_from_path(mem.workspace.project_path),
            )
            if recalled_lessons:
                lines.append("[历史经验]")
                for entry in recalled_lessons:
                    source_tag = entry.metadata.get("source", "")
                    lines.append(f"- [{source_tag}] {entry.text[:150]}")
    except Exception as exc:
        logger.warning("planner RAG injection failed: %s", exc)
        record_observability_degradation(
            session,
            component="planner_rag",
            fallback="build_plan_without_rag_context",
            error=exc,
        )
    return "\n".join(lines) if lines else "（无历史状态）"


def build_initial_plan_with_usage(
    *,
    session: Any,
    user_message: str,
    optimization_mode: bool = False,
    client: Any = None,
    model: str = "",
) -> tuple:
    """返回 (plan_dict, usage_dict)，usage_dict 可能为空 dict（heuristic fallback 时）。"""
    if client and model:
        context_text = _build_planner_context(session, client=client, user_message=user_message)
        plan_dict, usage = call_planner_llm(client, model, user_message, context_text)
        if plan_dict:
            plan_obj = plan_from_dict(plan_dict)
            if plan_obj:
                plan = plan_obj.to_dict()
                session.active_plan = plan
                return plan, usage
        # LLM 可用但没产出合法 plan：记录降级事件（可观测性），再走 heuristic
        logger.warning("LLM planner produced no valid plan; falling back to heuristic planner")
        metadata = getattr(session, "metadata", None)
        if metadata is not None:
            metadata["planner_llm_fallbacks"] = int(metadata.get("planner_llm_fallbacks", 0) or 0) + 1
        record_observability_degradation(
            session,
            component="planner_llm",
            fallback="heuristic_planner",
            error="no_valid_plan",
        )
    # fallback: heuristic planner
    plan = planner_build_initial_plan(
        user_message=user_message,
        session_memory=getattr(session, "memory", None),
        optimization_state=getattr(session, "optimization_state", None),
        optimization_mode=optimization_mode,
    )
    session.active_plan = plan
    return plan, {}


def build_initial_plan(
    *,
    session: Any,
    user_message: str,
    optimization_mode: bool = False,
    client: Any = None,
    model: str = "",
) -> Dict[str, Any]:
    """向后兼容包装，只返回 plan_dict。"""
    plan, _ = build_initial_plan_with_usage(
        session=session,
        user_message=user_message,
        optimization_mode=optimization_mode,
        client=client,
        model=model,
    )
    return plan


def build_optimization_plan(*, session: Any, user_message: str, intent_kind: str) -> Dict[str, Any]:
    plan = build_initial_plan(session=session, user_message=user_message, optimization_mode=True)
    updated = dict(plan or {})
    intent = dict(updated.get("intent") or {})
    intent["kind"] = intent_kind
    updated["intent"] = intent
    session.active_plan = updated
    return updated


def update_plan_after_turn(
    *,
    session: Any,
    assistant_text: str = "",
    had_tool_calls: bool = False,
    had_tool_failure: bool = False,
    tool_names: List[str] | None = None,
    observation: str = "",
    final_action: str = "",
    turn_failed_soft: bool = False,
) -> Dict[str, Any] | None:
    updated = planner_update_plan_after_turn(
        session.active_plan,
        assistant_text=assistant_text,
        had_tool_calls=had_tool_calls,
        had_tool_failure=had_tool_failure,
        tool_names=tool_names or [],
        observation=observation,
        final_action=final_action,
        turn_failed_soft=turn_failed_soft,
    )
    session.active_plan = updated
    return updated


def evaluate_replan_or_stop(
    *,
    session: Any,
    had_tool_failure: bool = False,
    result_available: bool = False,
    target_met: bool = False,
    stagnation_hit: bool = False,
    max_round_reached: bool = False,
) -> Dict[str, Any]:
    evaluation = planner_evaluate_replan_or_stop(
        session.active_plan,
        had_tool_failure=had_tool_failure,
        result_available=result_available,
        target_met=target_met,
        stagnation_hit=stagnation_hit,
        max_round_reached=max_round_reached,
    )
    if evaluation.get("plan") is not None:
        session.active_plan = evaluation["plan"]
    return evaluation


def evaluate_optimization_next_action(
    *,
    session: Any,
    had_tool_failure: bool = False,
    result_available: bool = False,
    target_met: bool = False,
    stagnation_hit: bool = False,
    max_round_reached: bool = False,
    client: Any = None,
    model: str = "",
    user_message: str = "",
    replan_with_llm: bool = True,
) -> Dict[str, Any]:
    """评估下一步动作。

    replan_with_llm=False 时只返回 needs_replan 信号、不在本函数内静默重建 plan——
    图路由路径（已撤销）曾用它把重规划决策上交给图；
    当前生产路径直接调用时保持默认 True 的原地重建行为，False 保留给等价编排层或测试。
    """
    evaluation = evaluate_replan_or_stop(
        session=session,
        had_tool_failure=had_tool_failure,
        result_available=result_available,
        target_met=target_met,
        stagnation_hit=stagnation_hit,
        max_round_reached=max_round_reached,
    )
    next_action = "continue"
    replanned_via_llm = False
    if evaluation.get("needs_replan"):
        next_action = "replan"
        if replan_with_llm and client and model:
            build_initial_plan(
                session=session,
                user_message=user_message,
                client=client,
                model=model,
            )
            replanned_via_llm = True
    elif evaluation.get("stop_reason") == "target_met":
        next_action = "stop_target_met"
    elif evaluation.get("stop_reason") == "stagnation_limit":
        next_action = "stop_stagnation"
    elif evaluation.get("stop_reason") == "max_round_limit":
        next_action = "stop_max_round"
    result = {**evaluation, "next_action": next_action}
    if replanned_via_llm:
        result["replanned_via_llm"] = True
    return result


def absorb_baseline_result(
    *,
    session: Any,
    opt_state: Any,
    check: Dict[str, Any],
    param_snapshot: Dict[str, Any],
    format_status: Callable[..., str],
) -> Dict[str, Any]:
    """记录基线，推进 plan，返回 {status_text, already_met}。"""
    opt_state.record_baseline(check, param_snapshot)
    status_text = format_status(check)
    update_plan_after_turn(
        session=session,
        assistant_text="已记录优化基线",
        had_tool_calls=True,
        had_tool_failure=False,
        tool_names=["get_s_parameter"],
        observation=status_text,
        final_action="tool_only",
    )
    return {"status_text": status_text, "already_met": bool(check.get("met"))}


def absorb_round_result(
    *,
    session: Any,
    opt_state: Any,
    check: Dict[str, Any],
    param_snapshot: Dict[str, Any],
    changed_params: Dict[str, Any],
    strategy: str = "",
    proposal_reason: str = "",
    backend: str = "programmatic_patch",
    optimizer_result: Dict[str, Any] | None = None,
    format_status: Callable[..., str],
    remember_strategy: Callable[..., None] | None = None,
    refresh_memory: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    """记录单轮结果，推进 plan，同步 memory，返回 {status_text, improved, target_met}。"""
    if changed_params:
        previous_best = getattr(opt_state, "best_metric_value", None)
        record_kwargs = {
            "strategy": strategy,
            "proposal_reason": proposal_reason,
        }
        if optimizer_result is not None:
            record_kwargs["optimizer_result"] = optimizer_result
        improved = opt_state.record_round(check, param_snapshot, **record_kwargs)
        latest = opt_state.history[-1]
        if remember_strategy is not None:
            remember_strategy(
                round_num=latest.get("round", opt_state.round),
                strategy=latest.get("strategy", ""),
                proposal_reason=latest.get("proposal_reason", ""),
                changed_params=latest.get("changed_params", ""),
                metric_value=latest.get("metric_value"),
                improved=improved,
                met=latest.get("met", False),
            )
        if refresh_memory is not None:
            refresh_memory()
        _remember_optimization_tool_use(
            session=session,
            backend=backend,
            latest=latest,
            improved=improved,
            previous_best=previous_best,
        )
        status_text = format_status(check, opt_state)
    else:
        improved = False
        status_text = format_status(check)

    update_plan_after_turn(
        session=session,
        assistant_text=status_text,
        had_tool_calls=True,
        had_tool_failure=False,
        tool_names=[backend],
        observation=status_text,
        final_action="tool_then_answer",
    )
    return {"status_text": status_text, "improved": improved, "target_met": bool(check.get("met"))}


def _remember_optimization_tool_use(
    *,
    session: Any,
    backend: str,
    latest: Dict[str, Any],
    improved: bool,
    previous_best: Any,
) -> None:
    if not improved:
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
        metric_value = latest.get("metric_value")
        metric_delta = None
        try:
            if previous_best is not None and metric_value is not None:
                metric_delta = float(previous_best) - float(metric_value)
        except (TypeError, ValueError):
            metric_delta = None
        try:
            from cst_agent_workbench.rag.knowledge_base import design_signature_from_request

            artifacts = getattr(session, "artifacts", None)
            design_signature = design_signature_from_request(getattr(artifacts, "last_patch_request", None))
        except Exception as sig_exc:
            logger.warning("optimization tool-use design signature unavailable: %s", sig_exc)
            record_observability_degradation(
                session,
                component="tool_use_memory_design_scope",
                fallback="universal_design_signature",
                error=sig_exc,
            )
            design_signature = ""
        task_signature = f"optimization_round:{design_signature or 'universal'}"
        # 改善幅度是这条记录可信度的直接证据：改善越大越确定该动作有效。
        # 此前恒为 0.8，与 tool_runtime 的失败记录同分，无法区分。
        confidence = 0.6
        if metric_delta is not None:
            confidence = min(0.95, 0.6 + min(abs(float(metric_delta)), 6.0) * 0.05)
        record = ToolUseMemoryRecord(
            task_signature=task_signature,
            selected_tools=(backend,),
            success=True,
            corrective_hint=str(latest.get("proposal_reason") or latest.get("strategy") or ""),
            metric_delta=metric_delta,
            confidence=round(confidence, 4),
            metadata={
                "design_signature": design_signature,
                "project_scope": str(
                    ((getattr(session, "metadata", {}) or {}).get("memory_scope") or {}).get("project_scope") or ""
                ),
                "round": latest.get("round", 0),
                "changed_params": latest.get("changed_params", ""),
                "metric_value": metric_value,
            },
        )
        stored = store.add(record, improved=True, improvement_delta=metric_delta)
        metadata = getattr(session, "metadata", None)
        if metadata is not None:
            metadata["last_tool_use_memory_write"] = record.to_dict() if stored else {}
        if stored:
            persist_session_tool_use_memory(session)
    except Exception as exc:
        logger.warning("optimization tool-use memory write failed: %s", exc)
        record_observability_degradation(
            session,
            component="optimization_tool_use_memory_write",
            fallback="continue_without_persisting_success_experience",
            error=exc,
        )


def sync_plan_to_memory(*, session: Any) -> None:
    summary = summarize_plan(getattr(session, "active_plan", None))
    if not summary.get("intent_kind"):
        return
    notes = [f"intent={summary['intent_kind']}"]
    if summary.get("active_step_title"):
        notes.append(f"step={summary['active_step_title']}")
    if summary.get("stop_reason"):
        notes.append(f"stop={summary['stop_reason']}")
    if summary.get("final_action"):
        notes.append(f"action={summary['final_action']}")
    MemoryManager.update_conversation(session.memory, recent_summary="\n".join(notes))


def _copy_message(message: Dict[str, Any]) -> Dict[str, Any]:
    copied = {k: v for k, v in message.items() if not k.startswith("_")}

    content = copied.get("content")
    if isinstance(content, list):
        copied["content"] = [dict(part) if isinstance(part, dict) else part for part in content]

    tool_calls = copied.get("tool_calls")
    if isinstance(tool_calls, list):
        copied["tool_calls"] = [dict(call) if isinstance(call, dict) else call for call in tool_calls]

    return copied


def prepare_history_for_context(
    history: List[Dict[str, Any]],
    *,
    max_history_messages: int,
    is_noise_message: Callable[[Dict[str, Any]], bool],
    truncate_tool_result: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if len(history) > max_history_messages:
        trimmed_history = [_copy_message(msg) for msg in history[-max_history_messages:]]
    else:
        trimmed_history = [_copy_message(msg) for msg in history]

    while trimmed_history and trimmed_history[0].get("role") == "tool":
        trimmed_history = trimmed_history[1:]

    while (
        trimmed_history
        and trimmed_history[0].get("role") == "assistant"
        and trimmed_history[0].get("tool_calls")
    ):
        expected_ids = {
            tc["id"]
            for tc in trimmed_history[0]["tool_calls"]
            if isinstance(tc, dict) and "id" in tc
        }
        found_ids = set()
        for msg in trimmed_history[1:]:
            if msg.get("role") == "tool" and msg.get("tool_call_id") in expected_ids:
                found_ids.add(msg["tool_call_id"])
            elif msg.get("role") != "tool":
                break
        if found_ids == expected_ids:
            break
        trimmed_history = trimmed_history[1:]
        while trimmed_history and trimmed_history[0].get("role") == "tool":
            trimmed_history = trimmed_history[1:]

    for msg in trimmed_history:
        content = msg.get("content")
        if isinstance(content, list):
            msg["content"] = [
                part
                for part in content
                if not (isinstance(part, dict) and part.get("type") == "image_url")
            ]

    filtered_history = []
    for msg in trimmed_history:
        # 带 tool_calls 的 assistant 消息命中噪声词也不能丢：它的 tool 结果还留在
        # 序列里，丢掉会制造中间位置的孤儿 tool 消息（API 会 400）。只有头部孤儿
        # 有专门的剥离逻辑，中部没有。
        if msg.get("role") != "tool" and not msg.get("tool_calls") and is_noise_message(msg):
            continue
        filtered_history.append(truncate_tool_result(msg))
    return filtered_history


def _log(log_fn: Callable[[str], None] | None, message: str) -> None:
    if log_fn is not None:
        log_fn(message)


def _usage_token(usage: Any, name: str) -> int:
    value = getattr(usage, name, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def run_chat_completion_loop(
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
    successful_tool_call_count = 0
    routed_execution_mode = ""
    had_tool_failure = False
    executed_tool_names: List[str] = []
    successful_tool_names: List[str] = []
    approval_pending = False
    offline_notice_cleared = False

    for iteration in range(max_tool_iterations):
        active_tools = filter_tools_for_active_step(session, tools) if session is not None else tools
        if session is not None and tools and not active_tools:
            # 未知 step kind 或 allowed_tools 与静态白名单交集为空时 fail-closed：
            # 把 tools=[] + tool_choice="auto" 发给 API 会 400（OpenAI 拒绝空数组），
            # responses/anthropic 则静默变成零工具 —— 与上下文裁剪占位符
            # "use recall_tool_result to re-fetch" 的召回指令直接矛盾。
            message = (
                "当前计划步骤的工具白名单为空（step kind 未登记或 allowed_tools "
                "与静态白名单无交集），已停止请求模型；请重新规划该步骤。"
            )
            _log(log_fn, f"[Agent] {message}")
            record_observability_degradation(
                session,
                component="step_tool_allowlist",
                fallback="refuse_model_call_empty_allowlist",
                error=ValueError(f"step_kind={_active_step_kind(session) or 'unknown'}"),
            )
            pending_history.append({"role": "assistant", "content": message})
            return ToolLoopResult(
                final_text=message,
                final_mode="empty_step_allowlist",
                ok=False,
                error=message,
                had_tool_failure=False,
                executed_tool_names=executed_tool_names,
            )
        if session is not None:
            try:
                from cst_agent_workbench.agent.tool_use_memory import (
                    build_tool_use_memory_guidance,
                    rerank_safe_tools,
                )

                guidance, recalled_tool_memories = build_tool_use_memory_guidance(
                    session,
                    tool_memory_query,
                    allowed_tool_names=[_tool_name(tool) for tool in active_tools],
                )
                active_tools = rerank_safe_tools(active_tools, recalled_tool_memories)
                working_messages[:] = [
                    message for message in working_messages
                    if not (
                        message.get("role") == "system"
                        and str(message.get("content") or "").startswith("[工具使用经验；")
                    )
                ]
                if guidance:
                    working_messages.append({"role": "system", "content": guidance})
                metadata = getattr(session, "metadata", None)
                if isinstance(metadata, dict):
                    metadata["tool_use_memory_recall"] = [
                        record.to_dict() for record in recalled_tool_memories
                    ]
                    metadata["tool_use_memory_reranked_tools"] = [
                        _tool_name(tool) for tool in active_tools
                    ]
            except Exception as exc:
                logger.warning("tool-use memory recall degraded to static order: %s", exc)
                record_observability_degradation(
                    session,
                    component="tool_use_memory_recall",
                    fallback="static_safe_tool_order",
                    error=exc,
                )
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
            events.append(dict(budget_metrics))
            del events[:-50]
        if not budget_metrics.get("within_budget", False):
            message = (
                "当前系统提示、工具定义和本轮必要上下文超过 token 预算，"
                "已停止请求模型；请缩小工具范围或提高 AGENT_CONTEXT_MAX_TOKENS。"
            )
            _log(log_fn, f"[Agent] {message}")
            pending_history.append({"role": "assistant", "content": message})
            return ToolLoopResult(
                final_text=message,
                final_mode="context_budget_exceeded",
                ok=False,
                error=message,
                had_tool_failure=False,
                executed_tool_names=executed_tool_names,
            )
        _log(
            log_fn,
            f"[Agent] 第{iteration + 1}轮 API 调用, 消息数 {len(working_messages)}, 优化模式={optimization_mode}",
        )
        request_messages = deepcopy(working_messages)
        turn_started_at = datetime.now().isoformat(timespec="seconds")
        response = client.chat.completions.create(
            model=model,
            messages=working_messages,
            tools=active_tools,
            tool_choice="auto",
            timeout=60,
        )
        turn_finished_at = datetime.now().isoformat(timespec="seconds")
        assistant_message = response.choices[0].message

        usage_dict: Dict[str, Any] = {}
        if response.usage:
            prompt_tokens = _usage_token(response.usage, "prompt_tokens")
            completion_tokens = _usage_token(response.usage, "completion_tokens")
            cached_tokens = _usage_token(response.usage, "cached_tokens")
            cache_write_tokens = _usage_token(response.usage, "cache_write_tokens")
            total_tokens = _usage_token(response.usage, "total_tokens") or prompt_tokens + completion_tokens
            token_stats["prompt"] += prompt_tokens
            token_stats["completion"] += completion_tokens
            token_stats["calls"] += 1
            token_stats["cached"] = int(token_stats.get("cached", 0)) + cached_tokens
            token_stats["cache_write"] = int(token_stats.get("cache_write", 0)) + cache_write_tokens
            usage_dict = {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "cached_tokens": cached_tokens,
                "cache_write_tokens": cache_write_tokens,
            }

        tool_calls_raw = [tc.model_dump() for tc in assistant_message.tool_calls] if assistant_message.tool_calls else []
        if trace_turn_callback is not None:
            trace_turn_callback(
                {
                    "turn_index": iteration + 1,
                    "started_at": turn_started_at,
                    "finished_at": turn_finished_at,
                    "model": model,
                    "model_protocol": str(getattr(response, "protocol", "chat_completions")),
                    "request_messages": request_messages,
                    "tools": deepcopy(active_tools),
                    "assistant_content": assistant_message.content or "",
                    "tool_calls_raw": tool_calls_raw,
                    "usage": usage_dict,
                    "context_budget": budget_metrics,
                }
            )

        _log(
            log_fn,
            f"[Agent] 收到回复, content长度: {len(assistant_message.content or '')}, tool_calls: {bool(assistant_message.tool_calls)}",
        )

        assistant_dict: Dict[str, Any] = {
            "role": "assistant",
            "content": assistant_message.content or "",
        }
        if assistant_message.tool_calls:
            assistant_dict["tool_calls"] = tool_calls_raw

        working_messages.append(assistant_dict)
        pending_history.append(assistant_dict)

        if assistant_message.tool_calls:
            batch_successful_tool_names: List[str] = []
            batch_approval_pending = False
            for tool_call in assistant_message.tool_calls:
                if trace_tool_context_callback is not None:
                    trace_tool_context_callback(tool_call.id, tool_call.function.name)
                executed_tool_names.append(tool_call.function.name)
                try:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError as exc:
                    had_tool_failure = True
                    # Preserve malformed provider output so the canonical host
                    # runtime can reject and trace it with the same semantics as Pi.
                    arguments = tool_call.function.arguments
                    _log(log_fn, f"[Agent] 工具参数 JSON 解析失败，将交由统一契约层拒绝: {exc}")
                try:
                    tool_result = execute_tool(tool_call.function.name, arguments)
                except Exception as exc:
                    had_tool_failure = True
                    _log(log_fn, f"[Agent] 工具执行异常 {tool_call.function.name}: {exc}")
                    tool_result = json.dumps(
                        {
                            "success": False,
                            "message": f"工具执行异常: {exc}",
                            "error_type": type(exc).__name__,
                            "tool_name": tool_call.function.name,
                        },
                        ensure_ascii=False,
                    )
                try:
                    tool_result_dict = json.loads(tool_result)
                except Exception as _json_exc:
                    logger.warning("tool result JSON parse failed: %s", _json_exc)
                    # 这条降级会驱动 had_tool_failure 与 plan 状态，
                    # 必须留下观测记录，不能只有 warning。
                    record_observability_degradation(
                        session,
                        component="tool_result_parse",
                        fallback="treat_as_failed_tool_result",
                        error=_json_exc,
                    )
                    tool_result_dict = {}
                recovery_result = dict(tool_result_dict.get("recovery_result") or {})
                if (
                    recovery_result.get("action_name") == "reconnect_cst"
                    and coerce_success(recovery_result.get("recovered", False))
                    and coerce_success(recovery_result.get("retry_success", False))
                ):
                    # The notice describes entry state. Once the recovery engine
                    # has reconnected and the target retry succeeded, keeping it
                    # on the final answer would contradict the trace.
                    offline_notice = ""
                    offline_notice_cleared = True
                if coerce_success(tool_result_dict.get("success", False)):
                    successful_tool_call_count += 1
                    successful_tool_names.append(tool_call.function.name)
                    batch_successful_tool_names.append(tool_call.function.name)
                else:
                    # Approval is a suspended side effect, not an execution
                    # failure. Keep it out of completed_tools so the Plan step
                    # remains authorized for the server-owned approval replay.
                    if tool_result_dict.get("error_type") != "approval_required":
                        had_tool_failure = True
                    else:
                        approval_pending = True
                        batch_approval_pending = True
                if tool_result_dict.get("mode") == "llm_routed_fast_path":
                    routed_execution_mode = "llm_routed_fast_path"

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
                working_messages.append(tool_message)
                pending_history.append(tool_message)
            if session is not None:
                update_plan_after_turn(
                    session=session,
                    assistant_text=assistant_message.content or "",
                    had_tool_calls=bool(batch_successful_tool_names),
                    had_tool_failure=had_tool_failure,
                    tool_names=batch_successful_tool_names,
                    observation="tool batch completed",
                    final_action=("approval_pending" if batch_approval_pending else "tool_batch"),
                )
            continue

        final_text = assistant_message.content or ""
        if optimization_mode and successful_tool_call_count == 0:
            no_op_text = "本轮优化未执行有效调参或仿真操作，未记录结果。"
            if final_text:
                no_op_text += f"\n\n模型回复：{final_text}"
            if offline_notice and not no_op_text.startswith("提示：当前为离线模式"):
                no_op_text = offline_notice + no_op_text
            pending_history[-1] = {"role": "assistant", "content": no_op_text}
            return ToolLoopResult(
                final_text=no_op_text,
                final_mode="llm_path",
                ok=False,
                error=no_op_text,
                had_tool_failure=had_tool_failure,
                executed_tool_names=executed_tool_names,
                successful_tool_names=successful_tool_names,
                approval_pending=approval_pending,
            )

        if offline_notice and not final_text.startswith("提示：当前为离线模式"):
            final_text = offline_notice + final_text
            pending_history[-1] = {"role": "assistant", "content": final_text}

        return ToolLoopResult(
            final_text=final_text,
            final_mode=routed_execution_mode or "llm_path",
            ok=True,
            error="",
            had_tool_failure=had_tool_failure,
            executed_tool_names=executed_tool_names,
            successful_tool_names=successful_tool_names,
            approval_pending=approval_pending,
            offline_notice_cleared=offline_notice_cleared,
        )

    final_text = offline_notice + "本轮工具调用次数过多，已停止继续执行。请缩小需求范围后重试。"
    pending_history.append({"role": "assistant", "content": final_text})
    return ToolLoopResult(
        final_text=final_text,
        final_mode="llm_path",
        ok=False,
        error=final_text,
        had_tool_failure=had_tool_failure,
        executed_tool_names=executed_tool_names,
        successful_tool_names=successful_tool_names,
        approval_pending=approval_pending,
        offline_notice_cleared=offline_notice_cleared,
    )


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str((tool.get("function") or {}).get("name", ""))
    return str(getattr(getattr(tool, "function", None), "name", "") or getattr(tool, "name", ""))


_STEP_ALLOWED_TOOL_NAMES = {
    "analyze": {"check_cst_status", "list_project_materials", "open_results", "list_results", "read_result", "get_s_parameter", "export_result_ascii", "recall_tool_result"},
    "tool": {"create_cst_project", "open_cst_project", "save_cst_project", "save_cst_project_as", "save_and_close_cst_project", "set_units", "store_parameter", "delete_parameter", "create_material", "create_brick", "create_cylinder", "create_extruded_polygon", "transform_shape", "set_wcs", "boolean_add", "boolean_subtract", "set_frequency_range", "set_boundary", "set_background", "create_discrete_port", "create_waveguide_port", "create_farfield_monitor", "create_frequency_field_monitor", "create_mesh_refinement", "add_solids_to_mesh_group", "set_global_hexahedral_mesh", "change_solver_type", "run_solver", "use_template", "execute_vba_script", "check_cst_status", "build_rectangular_patch_fast", "build_dipole_fast", "build_pixel_patch_fast", "list_project_materials", "recall_tool_result"},
    "geometry": {"create_brick", "create_cylinder", "create_extruded_polygon", "transform_shape", "set_wcs", "boolean_add", "boolean_subtract", "store_parameter", "delete_parameter", "create_material", "create_mesh_refinement", "add_solids_to_mesh_group", "list_project_materials", "build_rectangular_patch_fast", "build_dipole_fast", "build_pixel_patch_fast", "recall_tool_result"},
    "solve": {"run_solver", "change_solver_type", "create_waveguide_port", "create_farfield_monitor", "create_frequency_field_monitor", "create_mesh_refinement", "add_solids_to_mesh_group", "set_global_hexahedral_mesh", "set_frequency_range", "set_boundary", "set_background", "check_cst_status", "recall_tool_result"},
    "read_result": {"open_results", "list_results", "read_result", "get_s_parameter", "export_result_ascii", "check_cst_status", "recall_tool_result"},
    "optimize": {"get_s_parameter", "read_result", "run_solver", "build_rectangular_patch_fast", "build_dipole_fast", "build_pixel_patch_fast", "check_cst_status", "recall_tool_result"},
    "respond": {"recall_tool_result"},
    "judge": {"get_s_parameter", "read_result", "check_cst_status", "recall_tool_result"},
}
# 不变量：上下文裁剪会把旧 tool 结果替换成占位符并提示模型用 recall_tool_result 取回
# （见 _clear_old_tool_results）。若某个 step 的白名单里没有这个工具，模型就会拿到
# "可以召回"的指令却无法执行 —— 上下文一旦被裁剪就永久丢失。
# 用显式补齐而不是 assert：assert 在 python -O 下会被剥掉，不变量就成了空话。
for _step_kind, _names in _STEP_ALLOWED_TOOL_NAMES.items():
    _names.add("recall_tool_result")


def _active_step_kind(session: Any) -> str:
    step = _active_step(session)
    return str(step.get("kind", "") or "") if step else ""


def _active_step(session: Any) -> Dict[str, Any]:
    plan = getattr(session, "active_plan", None) or {}
    current_step_id = str(plan.get("current_step_id", "") or "")
    for step in plan.get("steps") or []:
        if step.get("step_id") == current_step_id:
            return dict(step)
    return {}


def _plan_intent_kind(session: Any) -> str:
    plan = getattr(session, "active_plan", None) or {}
    return str((plan.get("intent") or {}).get("kind", "") or "")


def allowed_tool_names_for_active_step(session: Any) -> set[str] | None:
    if not TOOL_FILTER_ENABLED or ALLOW_ALL_TOOLS_FOR_DEBUG:
        return None
    if session is None or not getattr(session, "active_plan", None):
        # Direct internal/programmatic calls do not originate from an LLM plan;
        # their callers remain responsible for choosing the exact tool.
        return None
    step = _active_step(session)
    step_kind = str(step.get("kind", "") or "")
    base_allowed = _STEP_ALLOWED_TOOL_NAMES.get(step_kind)
    if base_allowed is None:
        return set()
    explicit = step.get("allowed_tools") or step.get("selected_tools")
    if isinstance(explicit, str):
        explicit = [explicit]
    allowed_names = (
        {str(name) for name in explicit if str(name)} & set(base_allowed)
        if explicit else set(base_allowed)
    )
    if step_kind == "analyze" and not explicit:
        intent_kind = _plan_intent_kind(session)
        expanded = set(allowed_names or set())
        if intent_kind in {"direct_action", "chat_task"}:
            expanded.update(_STEP_ALLOWED_TOOL_NAMES["tool"])
            expanded.update(_STEP_ALLOWED_TOOL_NAMES["read_result"])
        elif intent_kind in {"optimization_round", "continuous_optimization"}:
            expanded.update(_STEP_ALLOWED_TOOL_NAMES["optimize"])
            expanded.update(_STEP_ALLOWED_TOOL_NAMES["judge"])
        allowed_names = expanded
    return set(allowed_names)


def filter_tools_for_active_step(session: Any, tools: List[Any]) -> List[Any]:
    allowed_names = allowed_tool_names_for_active_step(session)
    if allowed_names is None:
        return tools
    filtered = [tool for tool in tools if _tool_name(tool) in allowed_names]
    metadata = getattr(session, "metadata", None)
    if metadata is not None:
        metadata["tool_filter"] = {
            "active_step_kind": _active_step_kind(session),
            "allowed_tools": [_tool_name(tool) for tool in filtered],
            "filtered_out_count": max(0, len(tools) - len(filtered)),
            "enabled": True,
        }
    return filtered


def _estimate_tokens(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]] | None = None,
    model: str = "",
    estimator_info: Dict[str, Any] | None = None,
) -> int:
    """Estimate complete request tokens, including function schemas.

    ``tiktoken`` is used when available for an OpenAI-compatible model. Other
    providers fall back to a conservative CJK/ASCII estimator that still counts
    message framing, tool names/call ids and the serialized tool schemas.
    """
    serialized_messages = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    serialized_tools = json.dumps(tools or [], ensure_ascii=False, separators=(",", ":"))
    if not messages and not tools:
        if estimator_info is not None:
            estimator_info["estimator"] = "empty"
        return 0
    try:
        import tiktoken  # type: ignore[import-not-found]

        try:
            encoding = tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("cl100k_base")
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        total = len(encoding.encode(serialized_messages)) + len(encoding.encode(serialized_tools))
        if estimator_info is not None:
            estimator_info["estimator"] = "tiktoken"
        return total
    except Exception as exc:
        if estimator_info is not None:
            estimator_info["estimator"] = "conservative_fallback"
            estimator_info["fallback_reason"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    import re
    _CJK_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef]')
    total = 0
    for text in (serialized_messages, serialized_tools):
        cjk_count = len(_CJK_RE.findall(text))
        ascii_count = len(text) - cjk_count
        total += int(cjk_count * 1.5) + (ascii_count + 3) // 4
    total += 4 * len(messages) + 2
    if estimator_info is not None:
        estimator_info["estimator"] = "heuristic_with_schema"
    return max(0, total)


def _clear_old_tool_results(messages: List[Dict[str, Any]], keep_recent: int = 6) -> List[Dict[str, Any]]:
    """P2 tool-result clearing: 把旧的 tool 消息内容替换成短占位符。

    保留最近 keep_recent 条非 tool 消息对应的 tool 结果不变；更早的 tool 结果
    替换成 ``[cleared to save context; use recall_tool_result to re-fetch]``。
    保留 tool_use（assistant 消息的 tool_calls）记录，模型知道它调过这个工具。
    """
    if not messages:
        return messages
    result = list(messages)
    # 找到"最近 keep_recent 条非 tool 消息"的分界点
    non_tool_seen = 0
    cutoff = len(result)
    for i in range(len(result) - 1, -1, -1):
        if result[i].get("role") != "tool":
            non_tool_seen += 1
            if non_tool_seen >= keep_recent:
                cutoff = i
                break
    # 清空 cutoff 之前的 tool 消息内容
    cleared_marker = "[cleared to save context; use recall_tool_result to re-fetch]"
    for i in range(cutoff):
        if result[i].get("role") == "tool":
            original = str(result[i].get("content", ""))
            if len(original) > len(cleared_marker):
                result[i] = {**result[i], "content": cleared_marker}
    return result


def _trim_messages_to_budget(
    messages: List[Dict[str, Any]],
    max_tokens: int = 6000,
    context_summary_message: Dict[str, Any] | None = None,
    tools: List[Dict[str, Any]] | None = None,
    model: str = "",
    metrics: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    metrics = metrics if metrics is not None else {}
    estimator_info: Dict[str, Any] = {}
    before_tokens = _estimate_tokens(messages, tools, model, estimator_info)
    metrics.update({
        "budget": int(max_tokens),
        "before_tokens": before_tokens,
        "estimator": estimator_info.get("estimator", "heuristic_with_schema"),
        "trimmed": False,
    })
    if before_tokens <= max_tokens:
        metrics["after_tokens"] = before_tokens
        metrics["within_budget"] = True
        return messages
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]
    summary_message = context_summary_message or {"role": "system", "content": "[注意：历史上下文已因 token 预算限制被截断]"}
    primary_system = system_msgs[:1]
    auxiliary_system = system_msgs[1:]

    # Preserve the latest user turn and everything after it. The canonical
    # long-running goal/constraints live in the structured summary, so an old
    # positional "first user" is no longer mistaken for the original goal.
    latest_user_index = max(
        (index for index, item in enumerate(non_system) if item.get("role") == "user"),
        default=-1,
    )
    protected_tail = non_system[latest_user_index:] if latest_user_index >= 0 else non_system[-2:]
    remaining = non_system[:latest_user_index] if latest_user_index >= 0 else non_system[:-2]

    # P2 tool-result clearing：先把旧 tool 结果替换成短占位符，保留 tool_use 记录。
    remaining = _clear_old_tool_results(remaining)
    candidate = primary_system + auxiliary_system + [summary_message] + remaining + protected_tail

    # Drop complete old conversation prefixes before touching current-turn data.
    while remaining and _estimate_tokens(candidate, tools, model) > max_tokens:
        remaining = remaining[1:]
        # 盲砍可能把带 tool_calls 的 assistant 消息砍掉而留下孤儿 tool 消息，
        # OpenAI API 会对无配对的 tool 消息直接报 400，这里剥掉开头的孤儿。
        while remaining and remaining[0].get("role") == "tool":
            remaining = remaining[1:]
        candidate = primary_system + auxiliary_system + [summary_message] + remaining + protected_tail

    # Auxiliary RAG/plan system messages are lower priority than the canonical
    # summary and current user/tool chain. Remove oldest auxiliary messages.
    while auxiliary_system and _estimate_tokens(candidate, tools, model) > max_tokens:
        auxiliary_system = auxiliary_system[1:]
        candidate = primary_system + auxiliary_system + [summary_message] + remaining + protected_tail

    # A recalled full payload can itself exceed the budget. Replace oversized
    # tool payloads with an honest marker instead of sending malformed/truncated JSON.
    if _estimate_tokens(candidate, tools, model) > max_tokens:
        compact_tail = []
        for item in protected_tail:
            if item.get("role") == "tool" and len(str(item.get("content") or "")) > 1200:
                compact_tail.append({
                    **item,
                    "content": "[tool payload omitted: exceeds context budget; request a narrower result slice]",
                })
            else:
                compact_tail.append(item)
        protected_tail = compact_tail
        candidate = primary_system + [summary_message] + protected_tail

    after_tokens = _estimate_tokens(candidate, tools, model)
    metrics.update({
        "after_tokens": after_tokens,
        "within_budget": after_tokens <= max_tokens,
        "trimmed": True,
        "dropped_messages": max(0, len(messages) - len(candidate)),
    })
    return candidate


def run_agent_turn(
    *,
    session: Any,
    client: Any,
    model: str,
    user_message: str,
    working_messages: List[Dict[str, Any]],
    pending_history: List[Dict[str, Any]],
    tools: List[Any],
    execute_tool_fn: Callable,
    token_stats: Dict[str, Any],
    offline_notice: str = "",
    optimization_mode: bool = False,
    max_tool_iterations: int = 16,
    result_available: bool = False,
    trace_turn_callback: Callable | None = None,
    trace_tool_context_callback: Callable | None = None,
    max_context_tokens: int | None = None,
    replan_with_llm: bool = True,
    loop_runner: Callable[..., ToolLoopResult] | None = None,
) -> ToolLoopResult:
    """核心编排：tool loop → plan 更新 → plan eval → 返回结果。

    消息组装由调用方（agent.py）负责，本函数只做执行闭环。
    trace 相关（start_trace_run / finish_trace_run）保留在 agent.py。
    replan_with_llm=False（图路径已撤销）时只返回 needs_replan 信号、不在本函数内静默重建 plan——
    """
    effective_context_tokens = int(max_context_tokens or config.AGENT_CONTEXT_MAX_TOKENS)
    effective_loop_runner = loop_runner or run_chat_completion_loop
    loop_result = effective_loop_runner(
        client=client,
        model=model,
        tools=tools,
        working_messages=working_messages,
        pending_history=pending_history,
        execute_tool=execute_tool_fn,
        token_stats=token_stats,
        offline_notice=offline_notice,
        optimization_mode=optimization_mode,
        max_tool_iterations=max_tool_iterations,
        trace_turn_callback=trace_turn_callback,
        trace_tool_context_callback=trace_tool_context_callback,
        session=session,
        max_context_tokens=effective_context_tokens,
        context_summary_message=make_context_summary_message(session),
        tool_memory_query=user_message,
    )

    successful_tool_names = (
        loop_result.successful_tool_names
        if loop_result.successful_tool_names is not None
        else loop_result.executed_tool_names
    )
    update_plan_after_turn(
        session=session,
        assistant_text=loop_result.final_text,
        had_tool_calls=bool(successful_tool_names),
        had_tool_failure=loop_result.had_tool_failure,
        tool_names=successful_tool_names,
        observation="",
        final_action=(
            "approval_pending"
            if loop_result.approval_pending
            else (
                "tool_then_answer"
                if successful_tool_names and loop_result.final_text
                else ("tool_only" if successful_tool_names else "answer_only")
            )
        ),
        # 软失败：轮次未产出有效结果（如 tool loop 耗尽、优化轮无有效调参）
        # 但没有工具硬错误 → 标记 needs_replan，让编排层可以重规划重试一次。
        turn_failed_soft=not loop_result.ok and not loop_result.had_tool_failure,
    )

    plan_eval = evaluate_optimization_next_action(
        session=session,
        had_tool_failure=loop_result.had_tool_failure,
        result_available=result_available,
        client=client,
        model=model,
        user_message=user_message,
        replan_with_llm=replan_with_llm,
    )

    if (
        config.AGENT_MAX_REPLAN_RETRIES > 0
        and replan_with_llm
        and plan_eval.get("needs_replan")
        and plan_eval.get("replanned_via_llm")
    ):
        working_messages.append(
            {
                "role": "system",
                "content": "[同轮重规划] 上一执行尝试未完成；请按新计划修正工具选择，最多重试一次。",
            }
        )
        retry_result = effective_loop_runner(
            client=client,
            model=model,
            tools=tools,
            working_messages=working_messages,
            pending_history=pending_history,
            execute_tool=execute_tool_fn,
            token_stats=token_stats,
            # 第一遍 loop 里 reconnect_cst 恢复成功后已把 notice 清掉；
            # retry 必须沿用清除后的状态，否则恢复成功的回答又带上离线前缀，
            # 与 trace 矛盾（见 loop 内对 offline_notice 的清除注释）。
            offline_notice="" if loop_result.offline_notice_cleared else offline_notice,
            optimization_mode=optimization_mode,
            max_tool_iterations=max_tool_iterations,
            trace_turn_callback=trace_turn_callback,
            trace_tool_context_callback=trace_tool_context_callback,
            session=session,
            max_context_tokens=effective_context_tokens,
            context_summary_message=make_context_summary_message(session),
            tool_memory_query=user_message,
        )
        retry_result.executed_tool_names = [
            *loop_result.executed_tool_names,
            *retry_result.executed_tool_names,
        ]
        first_successful = (
            loop_result.successful_tool_names
            if loop_result.successful_tool_names is not None
            else loop_result.executed_tool_names
        )
        retry_successful = (
            retry_result.successful_tool_names
            if retry_result.successful_tool_names is not None
            else retry_result.executed_tool_names
        )
        retry_result.successful_tool_names = [*first_successful, *retry_successful]
        update_plan_after_turn(
            session=session,
            assistant_text=retry_result.final_text,
            had_tool_calls=bool(retry_result.successful_tool_names),
            had_tool_failure=retry_result.had_tool_failure,
            tool_names=retry_result.successful_tool_names,
            observation="same-turn replan retry",
            final_action="tool_then_answer" if retry_result.successful_tool_names else "answer_only",
            turn_failed_soft=not retry_result.ok and not retry_result.had_tool_failure,
        )
        retry_result.plan_eval = evaluate_optimization_next_action(
            session=session,
            had_tool_failure=retry_result.had_tool_failure,
            result_available=result_available,
            client=client,
            model=model,
            user_message=user_message,
            replan_with_llm=False,
        )
        retry_result.plan_eval["same_turn_replan_retry"] = True
        return retry_result

    loop_result.plan_eval = plan_eval  # type: ignore[attr-defined]
    return loop_result
