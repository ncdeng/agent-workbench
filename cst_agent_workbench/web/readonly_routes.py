from __future__ import annotations

from typing import Any, Callable

from cst_agent_workbench.results.summary import summarize_s11_result


def register_readonly_routes(
    app: Any,
    *,
    dry_run: bool,
    get_app_state: Callable[..., Any],
    build_dashboard_snapshot: Callable[[Any], dict[str, Any]],
    serialize_trace_detail: Callable[[dict[str, Any] | None], dict[str, Any] | None],
) -> None:
    @app.get("/api/dashboard/snapshot")
    def dashboard_snapshot():
        return build_dashboard_snapshot(get_app_state(dry_run=dry_run))

    @app.get("/api/results/s11")
    def results_s11():
        state = get_app_state(dry_run=dry_run)
        last_results = state.session.artifacts.last_results or {}
        from cst_agent_workbench.web_api import _resolve_dashboard_target_frequency

        target_freq = _resolve_dashboard_target_frequency(state, state.opt_settings)
        plot_data = last_results.get("plot_data") or []
        summary = summarize_s11_result(last_results, target_freq) if plot_data else None
        return {
            "available": bool(plot_data),
            "plotData": plot_data,
            "summary": {
                "minS11Db": summary.min_s11_db if summary else None,
                "minFreqGhz": summary.min_freq_ghz if summary else None,
                "targetS11Db": summary.target_s11_db if summary else None,
                "bandwidthGhz": summary.bandwidth_ghz if summary else None,
                "bandwidthPct": summary.bandwidth_pct if summary else None,
                "primaryBand": summary.primary_band if summary else None,
                "bands10Db": summary.bands_10db if summary else None,
            } if summary else None,
            "targetFreqGhz": target_freq,
            "targetDb": float(state.opt_settings.get("target_db") or -10.0),
        }

    @app.get("/api/results/farfield")
    def results_farfield():
        state = get_app_state(dry_run=dry_run)
        farfield_res = state.session.artifacts.last_farfield_results or {}
        if not farfield_res.get("success") or farfield_res.get("result_kind") != "farfield_cut":
            return {"available": False, "plotData": [], "summary": None}
        summary = farfield_res.get("summary") or {}
        return {
            "available": True,
            "plotData": farfield_res.get("plot_data") or [],
            "title": farfield_res.get("title", "Farfield"),
            "cutType": farfield_res.get("cut_type", "phi"),
            "cutValueDeg": farfield_res.get("cut_value_deg", 0.0),
            "frequencyGhz": farfield_res.get("frequency_ghz"),
            "summary": {
                "peakGainDbi": summary.get("peak_gain_dbi"),
                "peakAngleDeg": summary.get("peak_angle_deg"),
                "angleRange": summary.get("angle_range"),
                "points": summary.get("points"),
            },
        }

    @app.get("/api/agent/trace")
    def agent_trace_list():
        state = get_app_state(dry_run=dry_run)
        session = state.session
        traces = []
        for entry in (session.trace.trace_history or []):
            detail = serialize_trace_detail(entry) or {}
            traces.append({
                "runId": detail.get("runId", ""),
                "status": detail.get("status", ""),
                "turnCount": detail.get("turnCount", 0),
                "toolCallCount": detail.get("toolCallCount", 0),
                "decisionSummary": detail.get("decisionSummary", ""),
            })
        current = serialize_trace_detail(session.trace.current_trace)
        current_id = session.trace.current_run_id or ""
        return {
            "currentRunId": current_id,
            "traces": traces,
            "current": current,
        }

    @app.get("/api/agent/trace/{run_id}")
    def agent_trace_detail(run_id: str):
        state = get_app_state(dry_run=dry_run)
        session = state.session
        for entry in (session.trace.trace_history or []):
            if entry.get("run_id") == run_id:
                return serialize_trace_detail(entry)
        return {"error": f"Trace {run_id} not found"}

    @app.get("/api/state/token-stats")
    def state_token_stats():
        state = get_app_state(dry_run=dry_run)
        stats = state.agent.get_token_stats()
        return stats

    @app.get("/api/state/vba")
    def state_vba():
        state = get_app_state(dry_run=dry_run)
        return {"code": state.session.artifacts.last_vba or ""}

    @app.get("/api/state/plan")
    def state_plan():
        state = get_app_state(dry_run=dry_run)
        session = state.session
        plan = session.active_plan
        if plan is None:
            return {"active": False, "steps": []}
        from cst_agent_workbench.agent.planner import summarize_plan
        return {
            "active": True,
            "intent": plan.get("intent", {}),
            "steps": plan.get("steps", []),
            "summary": summarize_plan(plan),
        }

    @app.get("/api/state/parameters")
    def state_parameters():
        from cst_agent_workbench.cst.primitives import get_parameters, get_model_summary
        return {
            "parameters": get_parameters(),
            "summary": get_model_summary(),
        }

    @app.get("/api/rag/status")
    def rag_status():
        try:
            from cst_agent_workbench.rag.chroma_store import get_collection_stats
            stats = get_collection_stats()
            return {"available": True, **stats}
        except Exception as exc:
            return {"available": False, "error": str(exc)}
