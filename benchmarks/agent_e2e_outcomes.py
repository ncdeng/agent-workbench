"""Infrastructure-aware outcome classification for Agent E2E reports.

The benchmark runner records raw task checks and provider diagnostics.  This
module adds a separate interpretation layer so terminal provider outages are
not counted as Agent decision failures.  It does not mutate source reports and
contains no model or CST calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from cst_agent_workbench.agent.error_model import ErrorEnvelope
from cst_agent_workbench.agent.provider_call import normalize_provider_exception


class SampleOutcome(str, Enum):
    TASK_SUCCESS = "task_success"
    TASK_FAILURE = "task_failure"
    PROVIDER_FAILURE = "provider_failure"


@dataclass(frozen=True)
class ClassifiedSample:
    outcome: SampleOutcome
    eligible_for_agent_metrics: bool
    provider_degraded: bool
    provider_errors: tuple[ErrorEnvelope, ...]
    partial_execution: bool


def _provider_errors(case: Mapping[str, Any]) -> tuple[ErrorEnvelope, ...]:
    errors: list[ErrorEnvelope] = []
    for diagnostic in case.get("provider_diagnostics") or []:
        if not isinstance(diagnostic, Mapping):
            continue
        message = str(diagnostic.get("error") or "").strip()
        if not message:
            continue
        errors.append(normalize_provider_exception(RuntimeError(message)))
    return tuple(errors)


def _terminal_provider_failure(case: Mapping[str, Any], errors: tuple[ErrorEnvelope, ...]) -> bool:
    if not errors or bool(case.get("task_success")):
        return False
    final_text = str(case.get("final_response") or "").lower()
    trace_status = str(case.get("trace_status") or "").lower()
    terminal_markers = (
        "service temporarily unavailable",
        "upstream service temporarily unavailable",
        "request timed out",
        "provider request",
        "rate limit",
        "authentication",
        '"type":"api_error"',
        '"type": "api_error"',
    )
    terminal_text = any(marker in final_text for marker in terminal_markers)
    no_model_usage = int((case.get("token_usage") or {}).get("total") or 0) == 0
    return terminal_text or (trace_status == "failed" and no_model_usage)


def classify_sample(case: Mapping[str, Any]) -> ClassifiedSample:
    """Classify one runner case without changing its raw oracle result."""

    errors = _provider_errors(case)
    tool_calls = tuple(case.get("tool_calls") or ())
    partial_execution = bool(tool_calls)
    if _terminal_provider_failure(case, errors):
        return ClassifiedSample(
            outcome=SampleOutcome.PROVIDER_FAILURE,
            eligible_for_agent_metrics=False,
            provider_degraded=True,
            provider_errors=errors,
            partial_execution=partial_execution,
        )
    if bool(case.get("task_success")):
        outcome = SampleOutcome.TASK_SUCCESS
    else:
        outcome = SampleOutcome.TASK_FAILURE
    return ClassifiedSample(
        outcome=outcome,
        eligible_for_agent_metrics=True,
        provider_degraded=bool(errors),
        provider_errors=errors,
        partial_execution=partial_execution,
    )


def summarize_outcomes(cases: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate capability and availability metrics over independent samples."""

    pairs = [(case, classify_sample(case)) for case in cases]
    total = len(pairs)
    eligible = [(case, outcome) for case, outcome in pairs if outcome.eligible_for_agent_metrics]
    successes = sum(outcome.outcome == SampleOutcome.TASK_SUCCESS for _, outcome in eligible)
    strict_successes = sum(bool(case.get("strict_grounded_success")) for case, _ in eligible)
    provider_failures = sum(outcome.outcome == SampleOutcome.PROVIDER_FAILURE for _, outcome in pairs)
    provider_degraded_eligible = sum(
        outcome.provider_degraded for _, outcome in eligible
    )
    agent_failures = len(eligible) - successes

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "total_sample_count": total,
        "eligible_sample_count": len(eligible),
        "agent_success_count": successes,
        "agent_failure_count": agent_failures,
        "provider_failure_count": provider_failures,
        "provider_degraded_eligible_count": provider_degraded_eligible,
        "eligible_task_success_rate": rate(successes, len(eligible)),
        "eligible_strict_grounded_success_rate": rate(strict_successes, len(eligible)),
        "provider_failure_rate": rate(provider_failures, total),
        "provider_availability_rate": rate(total - provider_failures, total),
        "end_to_end_task_success_rate": rate(successes, total),
    }
