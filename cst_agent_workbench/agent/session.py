from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from cst_agent_workbench.agent.memory import StructuredMemory
from cst_agent_workbench.agent.planner import summarize_plan
from cst_agent_workbench.agent.tool_use_memory import ToolUseMemoryStore
from cst_agent_workbench.agent.tool_approval import ToolApprovalStore


@dataclass
class SessionArtifacts:
    last_results: Dict[str, Any] = field(default_factory=dict)
    last_farfield_results: Dict[str, Any] = field(default_factory=dict)
    last_vba: str = ""
    last_tool_message: str = ""
    results_invalidated: bool = False
    results_invalidated_reason: str = ""
    last_patch_request: Any = None  # RectangularPatchRequest | None
    last_optimizer_result: Dict[str, Any] = field(default_factory=dict)
    tool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class SessionTraceState:
    current_run_id: Optional[str] = None
    current_trace: Optional[Dict[str, Any]] = None
    trace_history: List[Dict[str, Any]] = field(default_factory=list)
    selected_trace_run_id: Optional[str] = None
    trace_enabled: bool = True
    trace_retention_limit: int = 10
    active_tool_call_id: Optional[str] = None


@dataclass
class AgentSession:
    session_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: SessionArtifacts = field(default_factory=SessionArtifacts)
    trace: SessionTraceState = field(default_factory=SessionTraceState)
    memory: StructuredMemory = field(default_factory=StructuredMemory)
    tool_use_memory: ToolUseMemoryStore = field(default_factory=ToolUseMemoryStore)
    tool_approvals: ToolApprovalStore = field(default_factory=ToolApprovalStore)
    active_plan: Optional[Dict[str, Any]] = None
    optimization_state: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def bind_optimization_state(self, optimization_state: Any) -> None:
        self.optimization_state = optimization_state

    def clear_tool_approvals(self) -> None:
        """Revoke session-owned high-risk capabilities at a lifecycle boundary."""

        self.tool_approvals.clear()

    def enforce_retention(self) -> None:
        """Bound long-lived desktop-session state without affecting current prompts."""
        from cst_agent_workbench import config

        del self.history[:-config.AGENT_HISTORY_RETENTION]
        del self.tool_events[:-config.AGENT_TOOL_EVENT_RETENTION]
        tool_results = self.artifacts.tool_results
        while len(tool_results) > config.AGENT_TOOL_RESULT_RETENTION:
            tool_results.pop(next(iter(tool_results)))

    def get_projection(self) -> Dict[str, Any]:
        optimization = self.optimization_state
        latest_trace = self.trace.trace_history[-1] if self.trace.trace_history else None
        latest_trace_summary = (latest_trace or {}).get("decision_summary") or {}
        active_plan_summary = summarize_plan(self.active_plan)
        optimizer_result = self.artifacts.last_optimizer_result or {}
        memory_impact = self.metadata.get("optimizer_memory_impact") or {}
        memory_recall = self.metadata.get("optimizer_memory_recall") or []
        return {
            "session_id": self.session_id,
            "history_length": len(self.history),
            "tool_event_count": len(self.tool_events),
            "has_last_results": bool(self.artifacts.last_results),
            "has_last_farfield_results": bool(self.artifacts.last_farfield_results),
            "tool_result_count": len(self.artifacts.tool_results),
            "results_invalidated": bool(self.artifacts.results_invalidated),
            "results_invalidated_reason": self.artifacts.results_invalidated_reason,
            "trace_run_id": self.trace.current_run_id,
            "selected_trace_run_id": self.trace.selected_trace_run_id,
            "trace_history_count": len(self.trace.trace_history),
            "trace_current_status": (self.trace.current_trace or {}).get("status") or (latest_trace or {}).get("status") or "idle",
            "trace_last_run_summary": {
                "run_id": (latest_trace or {}).get("run_id"),
                "status": (latest_trace or {}).get("status", "idle"),
                "turn_count": ((latest_trace or {}).get("run_metrics") or {}).get("turn_count", len((latest_trace or {}).get("turns", []))),
                "tool_call_count": ((latest_trace or {}).get("run_metrics") or {}).get("tool_call_count", (latest_trace or {}).get("tool_call_count", 0)),
                "failed_tool_call_count": latest_trace_summary.get("failed_tool_call_count", 0),
                "final_action": latest_trace_summary.get("final_action", "empty"),
                "plan_summary": latest_trace_summary.get("plan_summary", {}),
            },
            "active_plan": active_plan_summary,
            "memory": self.memory.to_dict(),
            "tool_use_memory_count": len(self.tool_use_memory.records),
            "tool_approvals": self.tool_approvals.projection(),
            "memory_scope": dict(self.metadata.get("memory_scope") or {}),
            "observability_degradations": list(
                (self.metadata.get("observability_degradations") or [])[-10:]
            ),
            "optimization": {
                "active": bool(getattr(optimization, "active", False)),
                "round": int(getattr(optimization, "round", 0) or 0),
                "best_round": int(getattr(optimization, "best_round", 0) or 0),
                "best_metric_value": getattr(optimization, "best_metric_value", None),
                "last_success": optimizer_result.get("success"),
                "last_strategy": optimizer_result.get("strategy", ""),
                "last_proposal_reason": optimizer_result.get("proposal_reason", ""),
                "last_changed_params": dict(optimizer_result.get("changed_params") or {}),
                "last_rolled_back": bool(optimizer_result.get("rolled_back")),
                "last_rollback_failed": bool(optimizer_result.get("rollback_failed")),
                "last_rollback_reason": optimizer_result.get("rollback_reason", ""),
                "last_llm_parse_error": memory_impact.get("llm_parse_error", ""),
                "last_memory_violation": bool(memory_impact.get("memory_violation")),
                "last_memory_enforced_by_validator": bool(memory_impact.get("memory_enforced_by_validator")),
                "last_memory_recall_count": len(memory_recall),
                "memory_impact": dict(memory_impact),
            },
        }
