"""Application service for executing confirmed Model-IR through the Host runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import threading
from typing import Any, Literal, Mapping
from uuid import uuid4
from weakref import WeakKeyDictionary

from cst_agent_workbench.agent.helpers import coerce_success
from cst_agent_workbench.agent.runtime_state import record_observability_degradation
from cst_agent_workbench.agent.tool_approval import requires_tool_approval

from .compiler import (
    MODEL_IR_CONFIGURATION_TOOLS,
    CompiledModelPlan,
    compile_model_ir,
)
from .models import AntennaModelIR


EXECUTION_SCHEMA_VERSION = "model-ir-execution-v1"
CallStatus = Literal["succeeded", "failed"]
ExecutionStatus = Literal[
    "compile_failed",
    "configuration_failed",
    "configuration_completed",
]


class ModelIRExecutionBusyError(RuntimeError):
    pass


class ModelIRExecutionContractError(ValueError):
    pass


_LOCKS_GUARD = threading.Lock()
_EXECUTION_LOCKS: WeakKeyDictionary[Any, threading.Lock] = WeakKeyDictionary()
_USED_EXECUTION_IDS: WeakKeyDictionary[Any, set[str]] = WeakKeyDictionary()


@dataclass(frozen=True)
class ModelIRCallResult:
    call_index: int
    tool_call_id: str
    tool_name: str
    source_path: str
    status: CallStatus
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelIRExecutionReport:
    execution_id: str
    ir_id: str
    revision: int
    status: ExecutionStatus
    compile_success: bool
    configuration_execution_success: bool | None
    solver_execution_success: bool | None
    typed_result_success: bool | None
    cleanup_success: bool | None
    physical_target_success: bool | None
    planned_call_count: int
    completed_call_count: int
    call_results: tuple[ModelIRCallResult, ...]
    failed_call_index: int | None = None
    pending_call_index: int | None = None
    error_type: str = ""
    message: str = ""
    schema_version: str = EXECUTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["call_results"] = [item.to_dict() for item in self.call_results]
        return data


def execute_model_ir(
    agent: Any,
    model: AntennaModelIR,
    *,
    execution_id: str | None = None,
) -> ModelIRExecutionReport:
    """Compile and configure a confirmed Model-IR without implicitly solving.

    Compilation is pure. Every compiled call is then delegated to
    ``CSTAgent._execute_tool`` so the canonical allowlist, schema validation,
    approval, recovery, result storage and Trace lifecycle remain authoritative.
    """

    resolved_execution_id = _execution_id(execution_id)
    try:
        plan = compile_model_ir(model)
    except Exception as exc:
        report = ModelIRExecutionReport(
            execution_id=resolved_execution_id,
            ir_id=model.ir_id,
            revision=model.revision,
            status="compile_failed",
            compile_success=False,
            configuration_execution_success=None,
            solver_execution_success=None,
            typed_result_success=None,
            cleanup_success=None,
            physical_target_success=None,
            planned_call_count=0,
            completed_call_count=0,
            call_results=(),
            error_type=type(exc).__name__,
            message=str(exc),
        )
        _save_session_projection(agent, report)
        return report
    return execute_compiled_model_plan(
        agent,
        plan,
        execution_id=resolved_execution_id,
    )


def execute_compiled_model_plan(
    agent: Any,
    plan: CompiledModelPlan,
    *,
    execution_id: str | None = None,
) -> ModelIRExecutionReport:
    """Execute a compiled configuration plan sequentially and stop on first issue."""

    if not hasattr(agent, "_execute_tool"):
        raise TypeError("agent must expose the canonical _execute_tool entry point")
    session = getattr(agent, "session", None)
    if session is None:
        raise TypeError("agent must expose a session for scoped tool authorization")

    resolved_execution_id = _execution_id(execution_id)
    _validate_compiled_plan(plan)
    execution_lock = _reserve_execution(agent, resolved_execution_id)
    if not execution_lock.acquire(blocking=False):
        _release_execution_id(agent, resolved_execution_id)
        raise ModelIRExecutionBusyError(
            "another Model-IR execution is already active for this agent session"
        )
    try:
        previous_plan = getattr(session, "active_plan", None)
        previous_tool_call_id = getattr(agent, "_active_tool_call_id", None)
        session.active_plan = _temporary_execution_plan(plan, resolved_execution_id)
        call_results: list[ModelIRCallResult] = []

        try:
            for call_index, call in enumerate(plan.calls):
                tool_call_id = f"{resolved_execution_id}:call:{call_index:04d}"
                agent._active_tool_call_id = tool_call_id
                try:
                    result = _decode_host_result(
                        agent._execute_tool(call.tool_name, dict(call.arguments))
                    )
                except Exception as exc:
                    result = {
                        "success": False,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                    }

                status = _call_status(result)
                call_results.append(
                    ModelIRCallResult(
                        call_index=call_index,
                        tool_call_id=tool_call_id,
                        tool_name=call.tool_name,
                        source_path=call.source_path,
                        status=status,
                        result=result,
                    )
                )
                if status != "succeeded":
                    break
        finally:
            session.active_plan = previous_plan
            agent._active_tool_call_id = previous_tool_call_id

        report = _build_execution_report(
            plan=plan,
            execution_id=resolved_execution_id,
            call_results=tuple(call_results),
        )
        _save_session_projection(agent, report)
        return report
    finally:
        execution_lock.release()


def _validate_compiled_plan(plan: CompiledModelPlan) -> None:
    for index, call in enumerate(plan.calls):
        if call.tool_name not in MODEL_IR_CONFIGURATION_TOOLS:
            raise ModelIRExecutionContractError(
                f"calls[{index}]: tool {call.tool_name!r} is not emitted by the Model-IR compiler"
            )
        if requires_tool_approval(call.tool_name):
            raise ModelIRExecutionContractError(
                f"calls[{index}]: approval-gated tools require an explicit resumable workflow"
            )


def _reserve_execution(agent: Any, execution_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _EXECUTION_LOCKS.setdefault(agent, threading.Lock())
        used_ids = _USED_EXECUTION_IDS.setdefault(agent, set())
        if execution_id in used_ids:
            raise ModelIRExecutionContractError(
                f"execution_id {execution_id!r} was already used in this agent process"
            )
        used_ids.add(execution_id)
        return lock


def _release_execution_id(agent: Any, execution_id: str) -> None:
    with _LOCKS_GUARD:
        used_ids = _USED_EXECUTION_IDS.get(agent)
        if used_ids is not None:
            used_ids.discard(execution_id)


def _execution_id(value: str | None) -> str:
    text = str(value or "").strip()
    return text or f"model-ir-{uuid4().hex}"


def _temporary_execution_plan(
    plan: CompiledModelPlan,
    execution_id: str,
) -> dict[str, Any]:
    allowed_tools = list(dict.fromkeys(call.tool_name for call in plan.calls))
    return {
        "plan_id": execution_id,
        "intent": {
            "kind": "model_ir_configuration",
            "user_goal": f"Configure confirmed Model-IR {plan.ir_id} revision {plan.revision}",
            "constraints": ["configuration_only", "canonical_typed_tools_only"],
        },
        "steps": [
            {
                "step_id": "configure-model-ir",
                "kind": "tool",
                "title": "Configure confirmed Model-IR",
                "status": "in_progress",
                "allowed_tools": allowed_tools,
                # Planner completion is name-based and cannot represent repeated
                # calls. This application service owns call-index completion.
                "required_tools": [],
                "completed_tools": [],
                "completion_contract": "application_managed_call_sequence",
                "expected_output": "All compiled configuration calls succeed",
            }
        ],
        "current_step_id": "configure-model-ir",
        "status": "active",
        "needs_replan": False,
    }


def _decode_host_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        return {
            "success": False,
            "error_type": "invalid_host_result",
            "message": f"canonical Host runtime returned invalid JSON: {exc}",
        }
    if not isinstance(decoded, dict):
        return {
            "success": False,
            "error_type": "invalid_host_result",
            "message": "canonical Host runtime result must be a JSON object",
        }
    return decoded


def _call_status(result: Mapping[str, Any]) -> CallStatus:
    if coerce_success(result.get("success", False)):
        return "succeeded"
    return "failed"


def _build_execution_report(
    *,
    plan: CompiledModelPlan,
    execution_id: str,
    call_results: tuple[ModelIRCallResult, ...],
) -> ModelIRExecutionReport:
    terminal = call_results[-1] if call_results else None
    completed = sum(item.status == "succeeded" for item in call_results)
    if terminal is not None and terminal.status == "failed":
        status: ExecutionStatus = "configuration_failed"
        configuration_success = False
        failed_index = terminal.call_index
        pending_index = None
    elif len(call_results) == len(plan.calls):
        status = "configuration_completed"
        configuration_success = True
        failed_index = None
        pending_index = None
    else:
        status = "configuration_failed"
        configuration_success = False
        failed_index = len(call_results)
        pending_index = None

    terminal_result = terminal.result if terminal is not None else {}
    return ModelIRExecutionReport(
        execution_id=execution_id,
        ir_id=plan.ir_id,
        revision=plan.revision,
        status=status,
        compile_success=True,
        configuration_execution_success=configuration_success,
        solver_execution_success=None,
        typed_result_success=None,
        cleanup_success=None,
        physical_target_success=None,
        planned_call_count=len(plan.calls),
        completed_call_count=completed,
        call_results=call_results,
        failed_call_index=failed_index,
        pending_call_index=pending_index,
        error_type=str(terminal_result.get("error_type") or "") if terminal else "",
        message=str(terminal_result.get("message") or "") if terminal else "",
    )


def _save_session_projection(agent: Any, report: ModelIRExecutionReport) -> None:
    session = getattr(agent, "session", None)
    metadata = getattr(session, "metadata", None)
    if metadata is None:
        return
    try:
        metadata["model_ir_execution"] = {
            "ir_id": report.ir_id,
            "revision": report.revision,
            "status": report.status,
            "last_execution_id": report.execution_id,
        }
    except Exception as exc:
        try:
            record_observability_degradation(
                agent,
                component="model_ir_execution_projection",
                fallback="return_execution_report_without_session_projection",
                error=exc,
            )
        except Exception:
            # Observability must never turn a completed CST side effect into an
            # apparent execution failure that callers might retry.
            pass
