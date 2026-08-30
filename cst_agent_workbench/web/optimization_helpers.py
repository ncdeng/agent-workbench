"""Programmatic optimization helpers for the React web layer.

Extracted from ui/common.py so that web/optimization_routes.py does not depend
on the deprecated Gradio UI package.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from cst_agent_workbench.agent.runtime_state import (
    append_trace_turn,
    finish_trace_run,
    record_observability_degradation,
    start_optimizer_trace_run,
)
from cst_agent_workbench.agent.runtime import absorb_baseline_result, absorb_round_result
from cst_agent_workbench.results.summary import (
    evaluate_optimization_target,
    format_round_status,
    format_target_description,
    resolve_target_frequency,
)

logger = logging.getLogger(__name__)


def _append_optimization_trace_turn(
    agent: Any,
    *,
    user_entry: Dict[str, Any],
    final_text: str,
    event_index: int | None,
) -> None:
    """Project programmatic optimization runtime events into the current trace turn."""
    if agent.current_trace is None:
        return

    tool_events = getattr(agent, "tool_events", [])
    if event_index is None or event_index < 0 or event_index > len(tool_events):
        new_events = []
    else:
        new_events = [event for event in tool_events[event_index:] if isinstance(event, dict)]
    if not new_events:
        return

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
    turn = append_trace_turn(
        agent,
        assistant_content=final_text,
        tool_calls_raw=tool_calls_raw,
        request_messages=[{"role": "system", "content": "[优化流程]"}, user_entry],
        tools=getattr(agent, "tools", []) or [],
        model="programmatic_optimizer",
        usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    if turn is None:
        return

    for idx, event in enumerate(new_events):
        success = bool(event.get("success", False))
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
            "source": "programmatic_optimizer_runtime_event",
        })


def run_programmatic_optimization_round(
    state: Any,
    algorithm: str = "auto",
) -> Dict[str, Any] | None:
    """Run one programmatic optimization round and record a trace run.

    ``state`` is the app state object returned by bootstrap.get_app_state().
    """
    agent = state.agent
    session = state.session
    opt_settings = state.opt_settings

    if not agent.can_use_programmatic_patch_optimizer():
        return None

    target_mode = opt_settings.get("mode", "at_f0")
    if target_mode == "at_f0":
        from cst_agent_workbench.cst.primitives import get_parameters

        target_freq_ghz = resolve_target_frequency(
            mode=target_mode,
            configured_target_freq=float(opt_settings.get("target_freq") or 0.0),
            parameter_lookup=get_parameters,
        )
    else:
        target_freq_ghz = 0.0
    target_db = float(opt_settings.get("target_db") or -10.0)
    opt_state = getattr(agent, "opt_state", None)
    if opt_state is not None:
        opt_state.target_mode = target_mode
        opt_state.target_freq = target_freq_ghz
        opt_state.target_db = target_db

    try:
        from cst_agent_workbench.cst.primitives import get_parameters

        before_params = dict(get_parameters() or {})
    except Exception:
        before_params = {}

    # Capture round-0 before the optimizer mutates parameters. This restores the
    # state transition that existed in the legacy UI and is still used by CLI.
    if opt_state is not None and not opt_state.history and hasattr(agent, "_collect_s11_summary"):
        try:
            baseline = agent._collect_s11_summary(target_freq_ghz)
            if baseline.get("success") and baseline.get("raw"):
                baseline_check = evaluate_optimization_target(
                    baseline["raw"],
                    mode=target_mode,
                    target_db=target_db,
                    effective_target_freq=target_freq_ghz,
                    configured_target_freq=float(opt_settings.get("target_freq") or 0.0),
                ).to_dict()
                baseline_check["resonances"] = list(baseline.get("resonances") or [])
                absorb_baseline_result(
                    session=session,
                    opt_state=opt_state,
                    check=baseline_check,
                    param_snapshot=before_params,
                    format_status=format_round_status,
                )
        except Exception as baseline_exc:
            logger.warning("optimization baseline capture failed: %s", baseline_exc)

    target_desc = format_target_description(
        mode=target_mode,
        target_db=target_db,
        effective_target_freq=target_freq_ghz,
        configured_target_freq=float(opt_settings.get("target_freq") or 0.0),
    )
    user_goal = f"执行一轮优化。当前目标：{target_desc}"
    user_entry = {"role": "user", "content": user_goal}
    owns_trace = False
    trace_finished = False
    trace_event_start = len(agent.tool_events)

    try:
        active_trace = getattr(agent, "current_trace", None)
        if (
            getattr(agent, "trace_enabled", True)
            and not (isinstance(active_trace, dict) and active_trace.get("status") == "running")
        ):
            start_optimizer_trace_run(
                agent,
                user_input=user_entry,
                working_messages=[{"role": "system", "content": f"[优化流程]\n{user_goal}"}, user_entry],
                pending_history=[user_entry],
            )
            owns_trace = True
    except Exception as trace_exc:
        logger.warning("optimization trace start failed (non-critical): %s", trace_exc)
        record_observability_degradation(
            agent,
            component="web_optimizer_trace_start",
            fallback="continue_without_new_optimizer_trace",
            error=trace_exc,
        )

    result = None
    ok = False
    try:
        result = agent.run_programmatic_patch_optimization_round(
            target_mode=target_mode,
            target_freq_ghz=target_freq_ghz,
            target_db=target_db,
            preferred_algorithm=algorithm,
        )
        ok = bool(result.get("success", False))
        agent.last_execution_mode = "programmatic_optimizer"

        memory_impact = session.metadata.get("optimizer_memory_impact") or {}
        session.artifacts.last_optimizer_result = dict(result or {})
        if result.get("results_raw"):
            session.artifacts.last_results = result["results_raw"]

        target_met = False
        improved = False
        if result.get("results_raw"):
            try:
                check = evaluate_optimization_target(
                    result["results_raw"],
                    mode=target_mode,
                    target_db=target_db,
                    effective_target_freq=target_freq_ghz,
                    configured_target_freq=float(opt_settings.get("target_freq") or 0.0),
                ).to_dict()
                try:
                    from cst_agent_workbench.cst.primitives import get_parameters

                    after_params = dict(get_parameters() or {})
                except Exception:
                    after_params = before_params
                absorbed = absorb_round_result(
                    session=session,
                    opt_state=opt_state,
                    check=check,
                    param_snapshot=after_params,
                    changed_params=dict(result.get("changed_params") or {}),
                    strategy=str(result.get("strategy") or ""),
                    proposal_reason=str(result.get("proposal_reason") or ""),
                    backend="programmatic_patch",
                    optimizer_result=result,
                    format_status=format_round_status,
                    refresh_memory=(
                        (lambda: agent._refresh_session_memory_from_runtime(persist=True))
                        if hasattr(agent, "_refresh_session_memory_from_runtime")
                        else None
                    ),
                )
                target_met = bool(absorbed.get("target_met"))
                improved = bool(absorbed.get("improved"))
            except Exception as absorb_exc:
                logger.warning("optimization state absorption failed: %s", absorb_exc)

        final_text = str(result.get("message", ""))
        if owns_trace:
            try:
                _append_optimization_trace_turn(
                    agent,
                    user_entry=user_entry,
                    final_text=final_text,
                    event_index=trace_event_start,
                )
            except Exception as trace_exc:
                logger.warning("optimization trace turn append failed (non-critical): %s", trace_exc)
                record_observability_degradation(
                    agent,
                    component="web_optimizer_trace_events",
                    fallback="finish_trace_without_optimizer_events",
                    error=trace_exc,
                )

        return_value = {
            "success": ok,
            "message": final_text,
            "backend": result.get("strategy", "programmatic_patch"),
            "changed_params": result.get("changed_params", {}),
            "proposal_reason": result.get("proposal_reason", ""),
            "strategy": result.get("strategy", "programmatic_patch"),
            "rolled_back": bool(result.get("rolled_back")),
            "rollback_reason": result.get("rollback_reason", ""),
            "memory_impact": dict(memory_impact),
            "target_met": target_met,
            "improved": improved,
            "round": getattr(opt_state, "round", 0),
            "stagnation_hit": bool(
                opt_state is not None
                and opt_state.should_stop(int(opt_settings.get("stagnation", 3)))
            ),
            "no_change": not bool(result.get("changed_params")),
        }
        if owns_trace and not trace_finished and result is not None:
            try:
                finish_trace_run(
                    agent,
                    status="completed" if ok else "failed",
                    final_response=final_text,
                    error="",
                )
                trace_finished = True
            except Exception as trace_exc:
                logger.warning("optimization trace finish failed (non-critical): %s", trace_exc)
                record_observability_degradation(
                    agent,
                    component="web_optimizer_trace_finish",
                    fallback="retain_optimizer_result_with_unfinished_trace",
                    error=trace_exc,
                )
        return return_value
    except Exception as exc:
        if owns_trace and not trace_finished:
            try:
                finish_trace_run(agent, status="failed", final_response="", error=str(exc))
                trace_finished = True
            except Exception as trace_exc:
                logger.warning("optimization trace finish (error path) failed (non-critical): %s", trace_exc)
                record_observability_degradation(
                    agent,
                    component="web_optimizer_trace_finish_error_path",
                    fallback="raise_original_optimizer_error",
                    error=trace_exc,
                )
        raise
    finally:
        if owns_trace and not trace_finished and result is not None:
            try:
                final_text = str(result.get("message", ""))
                finish_trace_run(
                    agent,
                    status="completed" if ok else "failed",
                    final_response=final_text,
                    error="",
                )
                trace_finished = True
            except Exception as trace_exc:
                logger.warning("optimization trace finish finally failed (non-critical): %s", trace_exc)
                record_observability_degradation(
                    agent,
                    component="web_optimizer_trace_finish_finally",
                    fallback="retain_optimizer_result_with_unfinished_trace",
                    error=trace_exc,
                )
