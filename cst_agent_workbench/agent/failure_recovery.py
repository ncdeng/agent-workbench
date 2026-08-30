"""Failure recovery for the agent tool loop.

This module turns the existing ``tool_events`` + ``classify_error`` pipeline into
a closed recovery loop: when a tool call fails, the runtime asks the
``FailureRecoveryEngine`` whether a known recovery action can fix the situation.
Each recovery attempt is recorded as an additional tool event (and trace entry),
so the resulting chain is fully auditable.

The engine is intentionally generic over the agent object. Recovery actions are
registered as small callables that receive the agent and the failure event, and
return a structured result. This keeps the failure domain (CST COM, VBA, results,
parameters, ...) separate from the orchestration domain.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from cst_agent_workbench.errors import ErrorType, classify_error


RecoveryRunner = Callable[[Any, "FailureEvent"], "RecoveryResult"]


@dataclass
class FailureEvent:
    """Structured description of a single tool failure."""

    error_type: ErrorType
    phase: str
    tool_name: str
    message: str
    arguments: Dict[str, Any]
    tool_event_id: Optional[str] = None


@dataclass
class RecoveryResult:
    """Outcome of one recovery attempt."""

    action_name: str
    recovered: bool
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    retry_tool: Optional[str] = None
    retry_arguments: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_name": self.action_name,
            "recovered": self.recovered,
            "message": self.message,
            "details": dict(self.details),
            "retry_tool": self.retry_tool,
            "retry_arguments": dict(self.retry_arguments) if self.retry_arguments else None,
        }


@dataclass
class RecoveryAction:
    """A registered recovery action with a matching predicate."""

    name: str
    description: str
    predicate: Callable[[FailureEvent], bool]
    run: RecoveryRunner
    max_attempts: int = 1


class FailureRecoveryEngine:
    """Registry + executor for tool-failure recovery actions.

    Usage::

        engine = FailureRecoveryEngine()
        engine.register_default_actions()
        result = engine.attempt_recovery(agent, failure_event)

    The engine keeps per-action attempt counters on the agent so that recovery
    loops are bounded and do not explode.
    """

    def __init__(self) -> None:
        self._actions: List[RecoveryAction] = []

    def register(self, action: RecoveryAction) -> None:
        self._actions.append(action)

    def _attempt_counters(self, agent: Any) -> Dict[str, int]:
        session = getattr(agent, "session", None)
        if session is None:
            return {}
        metadata = getattr(session, "metadata", None)
        if metadata is None:
            session.metadata = {}
            metadata = session.metadata
        key = "failure_recovery_attempts"
        if key not in metadata or not isinstance(metadata[key], dict):
            metadata[key] = {}
        return metadata[key]

    def _record_attempt(self, agent: Any, action_name: str) -> None:
        counters = self._attempt_counters(agent)
        counters[action_name] = counters.get(action_name, 0) + 1

    def _reset_attempt(self, agent: Any, action_name: str) -> None:
        counters = self._attempt_counters(agent)
        counters[action_name] = 0

    def attempt_recovery(self, agent: Any, event: FailureEvent) -> RecoveryResult:
        """Try the first matching action (in registration order) that still has budget.

        The attempt budget bounds an incident, not the session lifetime: a successful
        recovery closes the current incident and resets that action's counter, while
        consecutive failures still exhaust ``max_attempts`` and stop. Plan rebuilds and
        ``clear_history`` also reset counters explicitly.

        Returns a ``RecoveryResult`` with ``recovered=False`` when no action
        matches or all matching actions have exhausted their budget.
        """
        counters = self._attempt_counters(agent)
        for action in self._actions:
            if not action.predicate(event):
                continue
            if counters.get(action.name, 0) >= action.max_attempts:
                continue
            self._record_attempt(agent, action.name)
            try:
                result = action.run(agent, event)
            except Exception as exc:
                result = RecoveryResult(
                    action_name=action.name,
                    recovered=False,
                    message=f"recovery action raised: {exc}",
                    details={"exception": str(exc)},
                )
            if result.action_name != action.name:
                result.action_name = action.name
            if result.recovered:
                self._reset_attempt(agent, action.name)
            return result
        return RecoveryResult(
            action_name="none",
            recovered=False,
            message=f"no recovery action matched {event.error_type.value} for {event.tool_name}",
            details={"error_type": event.error_type.value},
        )

    # ------------------------------------------------------------------
    # Default recovery actions for the CST workbench domain.
    # These are illustrative and can be extended without touching the core loop.
    # ------------------------------------------------------------------

    def register_default_actions(self) -> None:
        self.register(_reconnect_on_connection_failure())
        self.register(_retry_after_timeout())
        self.register(_ensure_farfield_monitor_on_result_read())
        self.register(_fallback_to_dry_run_on_offline())
        self.register(_repair_missing_parameter())


def _reconnect_on_connection_failure() -> RecoveryAction:
    def predicate(event: FailureEvent) -> bool:
        return event.error_type == ErrorType.CST_CONNECTION

    def run(agent: Any, event: FailureEvent) -> RecoveryResult:
        cst = getattr(agent, "cst", None)
        if cst is None:
            return RecoveryResult(
                action_name="reconnect_cst",
                recovered=False,
                message="agent has no cst controller",
            )
        try:
            # ``CSTController.connect`` is the production public contract.
            # Keep legacy reconnect names as compatibility fallbacks for
            # external controller adapters, but never infer success merely
            # from a non-empty result dictionary.
            reconnect = (
                getattr(cst, "reconnect", None)
                or getattr(cst, "connect", None)
                or getattr(cst, "connect_to_any_or_new", None)
            )
            if reconnect is None:
                return RecoveryResult(
                    action_name="reconnect_cst",
                    recovered=False,
                    message="cst controller has no reconnect method",
                )
            outcome = reconnect()
            outcome_success: Optional[bool]
            if isinstance(outcome, dict):
                outcome_success = outcome.get("success") is True
            elif outcome is None:
                outcome_success = None
            else:
                outcome_success = bool(outcome)

            is_connected = getattr(cst, "is_connected", None)
            has_connection_state = callable(is_connected) or hasattr(cst, "connected")
            if callable(is_connected):
                state_connected = bool(is_connected())
            else:
                state_connected = bool(getattr(cst, "connected", False)) and not bool(
                    getattr(cst, "offline_mode", False)
                )
            connected = (
                state_connected and outcome_success is not False
                if has_connection_state
                else outcome_success is True
            )
            return RecoveryResult(
                action_name="reconnect_cst",
                recovered=connected,
                message="CST reconnected" if connected else "CST reconnect attempt failed",
                details={
                    "method": getattr(reconnect, "__name__", reconnect.__class__.__name__),
                    "outcome": outcome,
                    "project_path": getattr(cst, "project_path", ""),
                },
                retry_tool=event.tool_name if connected else None,
                retry_arguments=dict(event.arguments) if connected else None,
            )
        except Exception as exc:
            return RecoveryResult(
                action_name="reconnect_cst",
                recovered=False,
                message=f"reconnect raised: {exc}",
            )

    return RecoveryAction(
        name="reconnect_cst",
        description="Reconnect to CST when connection or timeout errors occur",
        predicate=predicate,
        run=run,
        max_attempts=2,
    )


def _retry_after_timeout() -> RecoveryAction:
    def predicate(event: FailureEvent) -> bool:
        return event.error_type == ErrorType.CST_TIMEOUT

    def run(agent: Any, event: FailureEvent) -> RecoveryResult:
        # Timeout is handled by reconnect first; this action is a fallback that
        # records a conservative recommendation and asks the planner to wait.
        return RecoveryResult(
            action_name="timeout_wait_and_reconnect",
            recovered=False,
            message=(
                "CST solver timed out. The DE process may still be running; "
                "reconnect is recommended before the next command."
            ),
            details={"recommendation": "reconnect_and_wait"},
        )

    return RecoveryAction(
        name="timeout_wait_and_reconnect",
        description="Advise wait/reconnect after CST timeout (honest degradation per ADR-006)",
        predicate=predicate,
        run=run,
        max_attempts=1,
    )


def _ensure_farfield_monitor_on_result_read() -> RecoveryAction:
    def predicate(event: FailureEvent) -> bool:
        if event.error_type != ErrorType.RESULT_READ:
            return False
        msg = str(event.message).lower()
        return "farfield" in msg or "monitor" in msg

    def run(agent: Any, event: FailureEvent) -> RecoveryResult:
        cst = getattr(agent, "cst", None)
        if cst is None:
            return RecoveryResult(
                action_name="ensure_farfield_monitor",
                recovered=False,
                message="agent has no cst controller",
            )
        try:
            from cst_agent_workbench.cst.primitives import (
                PRIMITIVES,
                get_frequency_range,
                register_farfield_monitor,
            )

            freq_range = get_frequency_range()
            auto_freq = freq_range.get("fmax") or freq_range.get("fmin") or "1.0"
            _, vba_code = PRIMITIVES["create_farfield_monitor"](f"farfield (f={auto_freq})", auto_freq, False)
            exec_result = cst.execute_vba(vba_code, label="recovery_auto_farfield_monitor", timeout=60)
            success = bool(exec_result.get("success")) and bool(exec_result.get("executed", False))
            if success:
                register_farfield_monitor(f"farfield (f={auto_freq})", auto_freq, False)
            return RecoveryResult(
                action_name="ensure_farfield_monitor",
                recovered=success,
                message="Auto-created farfield monitor" if success else "Farfield monitor creation failed",
                details={"auto_freq": auto_freq, "exec_result": exec_result},
                retry_tool=event.tool_name if success else None,
                retry_arguments=dict(event.arguments) if success else None,
            )
        except Exception as exc:
            return RecoveryResult(
                action_name="ensure_farfield_monitor",
                recovered=False,
                message=f"farfield monitor recovery raised: {exc}",
            )

    return RecoveryAction(
        name="ensure_farfield_monitor",
        description="Create a missing farfield monitor before retrying result read",
        predicate=predicate,
        run=run,
        max_attempts=1,
    )


def _fallback_to_dry_run_on_offline() -> RecoveryAction:
    def predicate(event: FailureEvent) -> bool:
        return event.error_type == ErrorType.OFFLINE_MODE

    def run(agent: Any, event: FailureEvent) -> RecoveryResult:
        cst = getattr(agent, "cst", None)
        if cst is None:
            return RecoveryResult(
                action_name="dry_run_fallback",
                recovered=True,
                message="No CST controller; operating in dry-run mode by default",
                details={"dry_run": True},
            )
        offline = getattr(cst, "offline_mode", True)
        if not offline:
            return RecoveryResult(
                action_name="dry_run_fallback",
                recovered=False,
                message="CST is connected; dry-run fallback not applicable",
            )
        return RecoveryResult(
            action_name="dry_run_fallback",
            recovered=True,
            message="CST is offline; proceeding with VBA artifact generation only",
            details={"dry_run": True, "offline_mode": True},
        )

    return RecoveryAction(
        name="dry_run_fallback",
        description="Accept dry-run execution when CST is offline",
        predicate=predicate,
        run=run,
        max_attempts=1,
    )


def _repair_missing_parameter() -> RecoveryAction:
    def predicate(event: FailureEvent) -> bool:
        return event.error_type == ErrorType.PARAMETER_INVALID

    def run(agent: Any, event: FailureEvent) -> RecoveryResult:
        # Minimal repair: if the original call omitted a required value,
        # fill it from the current model parameters keyed by the parameter name.
        try:
            from cst_agent_workbench.cst.primitives import get_parameters

            params = get_parameters()
        except Exception:
            params = {}

        repaired_arguments = dict(event.arguments)
        repaired_any = False

        # Typical tool shape: {"name": "patch_L", "value": ...}
        param_name = str(repaired_arguments.get("name", "")).strip()
        value = repaired_arguments.get("value")
        if param_name and (value is None or value == ""):
            if param_name in params:
                repaired_arguments["value"] = params[param_name]
                repaired_any = True

        # Also repair any top-level argument whose value is None/empty and
        # whose key exists in the current model parameters (fallback for
        # tools that do not use the name/value convention).
        for key, val in list(repaired_arguments.items()):
            if val is None or val == "":
                if key in params:
                    repaired_arguments[key] = params[key]
                    repaired_any = True

        return RecoveryResult(
            action_name="repair_missing_parameter",
            recovered=repaired_any,
            message="Filled missing parameter value from current model" if repaired_any else "No repairable parameters found",
            details={"repaired_arguments": repaired_arguments, "source_parameters": params},
            retry_tool=event.tool_name if repaired_any else None,
            retry_arguments=repaired_arguments if repaired_any else None,
        )

    return RecoveryAction(
        name="repair_missing_parameter",
        description="Fill missing/invalid parameter values from the current model state",
        predicate=predicate,
        run=run,
        max_attempts=1,
    )


def make_failure_event(
    tool_name: str,
    message: str,
    phase: str,
    arguments: Optional[Dict[str, Any]] = None,
    tool_event_id: Optional[str] = None,
) -> FailureEvent:
    """Factory used by the runtime to build a failure event from a raw tool error."""
    return FailureEvent(
        error_type=classify_error(message),
        phase=phase or "unknown",
        tool_name=tool_name,
        message=message or "",
        arguments=dict(arguments or {}),
        tool_event_id=tool_event_id,
    )
