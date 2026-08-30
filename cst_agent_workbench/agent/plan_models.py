from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


@dataclass
class PlanIntent:
    kind: str
    user_goal: str
    constraints: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PlanStep:
    step_id: str
    kind: str
    title: str
    status: str = "pending"
    tool_name: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    completed_tools: List[str] = field(default_factory=list)
    completion_contract: str = "legacy_any_call"
    expected_output: str = ""
    observation: str = ""
    decision_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    plan_id: str
    intent: PlanIntent
    steps: List[PlanStep] = field(default_factory=list)
    current_step_id: str = ""
    stop_condition: str = ""
    stop_conditions: List[str] = field(default_factory=list)
    needs_replan: bool = False
    final_action: str = ""
    status: str = "active"
    stop_reason: str = ""
    replan_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["intent"] = self.intent.to_dict()
        data["steps"] = [step.to_dict() for step in self.steps]
        return data


def plan_intent_from_dict(data: Dict[str, Any] | None) -> PlanIntent:
    payload = dict(data or {})
    return PlanIntent(
        kind=str(payload.get("kind", "chat_task") or "chat_task"),
        user_goal=str(payload.get("user_goal", "") or ""),
        constraints=[str(item) for item in (payload.get("constraints") or [])],
    )


def plan_step_from_dict(data: Dict[str, Any] | None) -> PlanStep:
    payload = dict(data or {})
    allowed_tools = [str(item) for item in (payload.get("allowed_tools") or []) if str(item)]
    required_tools = [str(item) for item in (payload.get("required_tools") or []) if str(item)]
    completion_contract = str(payload.get("completion_contract") or "").strip()
    if not completion_contract:
        if "required_tools" in payload:
            completion_contract = "required_tools_all" if required_tools else "any_tool_call"
        else:
            completion_contract = "legacy_any_call"
    return PlanStep(
        step_id=str(payload.get("step_id", "") or ""),
        kind=str(payload.get("kind", "analyze") or "analyze"),
        title=str(payload.get("title", "") or ""),
        status=str(payload.get("status", "pending") or "pending"),
        tool_name=str(payload.get("tool_name", "") or ""),
        allowed_tools=allowed_tools,
        required_tools=required_tools,
        completed_tools=[
            str(item) for item in (payload.get("completed_tools") or []) if str(item)
        ],
        completion_contract=completion_contract,
        expected_output=str(payload.get("expected_output", "") or ""),
        observation=str(payload.get("observation", "") or ""),
        decision_note=str(payload.get("decision_note", "") or ""),
    )


def plan_from_dict(data: Dict[str, Any] | None) -> Plan | None:
    if not data:
        return None
    payload = dict(data)
    return Plan(
        plan_id=str(payload.get("plan_id", "") or ""),
        intent=plan_intent_from_dict(payload.get("intent")),
        steps=[plan_step_from_dict(item) for item in (payload.get("steps") or [])],
        current_step_id=str(payload.get("current_step_id", "") or ""),
        stop_condition=str(payload.get("stop_condition", "") or ""),
        stop_conditions=[str(item) for item in (payload.get("stop_conditions") or []) if str(item)],
        needs_replan=bool(payload.get("needs_replan", False)),
        final_action=str(payload.get("final_action", "") or ""),
        status=str(payload.get("status", "active") or "active"),
        stop_reason=str(payload.get("stop_reason", "") or ""),
        replan_count=int(payload.get("replan_count", 0) or 0),
    )
