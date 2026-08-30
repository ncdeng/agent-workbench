"""Fail-closed physical acceptance evaluation for Model-IR results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import PureWindowsPath
from typing import Any, Literal, Mapping, Sequence

from cst_agent_workbench.results.contracts import ResultKind, validate_result_envelope
from cst_agent_workbench.results.summary import interpolate_s11_at_freq

from .models import AcceptanceCriterion


ACCEPTANCE_SCHEMA_VERSION = "model-ir-acceptance-v1"
SUPPORTED_ACCEPTANCE_METRICS = frozenset({"s11_db"})
CriterionStatus = Literal[
    "passed",
    "failed",
    "not_evaluable",
    "unsupported_metric",
    "invalid_result",
    "ambiguous_result",
]


@dataclass(frozen=True)
class AcceptanceContext:
    """Expected identity and fresh solver boundary for one acceptance run."""

    execution_id: str
    ir_id: str
    revision: int
    solver_run_id: str
    project_path: str
    project_sha256: str
    design_point_sha256: str
    solver_started_at: str


@dataclass(frozen=True)
class ModelIRResultEvidence:
    """A typed payload bound to the solver run and design point that produced it."""

    payload: Mapping[str, Any]
    execution_id: str
    ir_id: str
    revision: int
    solver_run_id: str
    project_path: str
    project_sha256: str
    design_point_sha256: str
    solver_tool_call_id: str
    result_tool_call_id: str
    solver_completed_at: str
    result_captured_at: str
    payload_sha256: str
    fresh_solver_boundary: bool


@dataclass(frozen=True)
class CriterionEvaluation:
    criterion_id: str
    status: CriterionStatus
    passed: bool | None
    actual: float | None
    target: float
    operator: str
    tolerance: float | None
    unit: str
    result_kind: str | None
    result_ref: str
    reason_code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AcceptanceEvaluation:
    typed_result_success: bool
    criteria_evaluable: bool
    physical_target_success: bool | None
    criteria: tuple[CriterionEvaluation, ...]
    schema_version: str = ACCEPTANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["criteria"] = [item.to_dict() for item in self.criteria]
        return data


def canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_acceptance_criteria(
    criteria: Sequence[AcceptanceCriterion],
    results: Sequence[ModelIRResultEvidence],
    *,
    context: AcceptanceContext,
) -> AcceptanceEvaluation:
    """Evaluate v1 acceptance only from provenance-bound, fresh S1,1 results."""

    required_results = [
        evidence
        for evidence in results
        if any(criterion.metric == "s11_db" for criterion in criteria)
        and _looks_like_s11_payload(evidence.payload)
    ]
    candidate_validity = [
        _validate_evidence(evidence, context=context) for evidence in required_results
    ]
    typed_result_success = bool(criteria) and bool(required_results) and all(
        error is None for error in candidate_validity
    )
    evaluations = tuple(
        _evaluate_criterion(
            criterion,
            candidates=required_results,
            candidate_validity=candidate_validity,
        )
        for criterion in criteria
    )
    criteria_evaluable = bool(evaluations) and all(
        item.passed is not None for item in evaluations
    )
    physical_target_success = (
        all(bool(item.passed) for item in evaluations)
        if criteria_evaluable
        else None
    )
    return AcceptanceEvaluation(
        typed_result_success=typed_result_success,
        criteria_evaluable=criteria_evaluable,
        physical_target_success=physical_target_success,
        criteria=evaluations,
    )


def _evaluate_criterion(
    criterion: AcceptanceCriterion,
    *,
    candidates: list[ModelIRResultEvidence],
    candidate_validity: list[str | None],
) -> CriterionEvaluation:
    base = {
        "criterion_id": criterion.criterion_id,
        "target": criterion.target,
        "operator": criterion.operator,
        "tolerance": criterion.tolerance,
        "unit": criterion.unit,
    }
    if criterion.metric not in SUPPORTED_ACCEPTANCE_METRICS:
        return CriterionEvaluation(
            **base,
            status="unsupported_metric",
            passed=None,
            actual=None,
            result_kind=None,
            result_ref="",
            reason_code="unsupported_metric",
            message=f"unsupported acceptance metric: {criterion.metric!r}",
        )
    if criterion.unit != "dB":
        return _not_evaluable(base, "unit_mismatch", "s11_db requires canonical unit 'dB'")
    if (
        criterion.frequency_ghz is None
        or not math.isfinite(criterion.frequency_ghz)
        or criterion.frequency_ghz <= 0
    ):
        return _not_evaluable(
            base,
            "frequency_required",
            "s11_db requires a finite positive frequency_ghz",
        )
    if not candidates:
        return _not_evaluable(base, "s11_result_missing", "no typed S1,1 evidence was supplied")
    if len(candidates) > 1:
        return CriterionEvaluation(
            **base,
            status="ambiguous_result",
            passed=None,
            actual=None,
            result_kind=ResultKind.S_PARAMETER.value,
            result_ref="",
            reason_code="multiple_s11_results",
            message="multiple S1,1 evidence objects match one criterion",
        )

    evidence, validity_error = candidates[0], candidate_validity[0]
    payload = dict(evidence.payload)
    result_ref = evidence.result_tool_call_id
    if validity_error is not None:
        return CriterionEvaluation(
            **base,
            status="invalid_result",
            passed=None,
            actual=None,
            result_kind=str(payload.get("result_kind") or "") or None,
            result_ref=result_ref,
            reason_code="invalid_result_evidence",
            message=validity_error,
        )

    points = sorted(payload["plot_data"], key=lambda item: float(item["freq"]))
    minimum_frequency = float(points[0]["freq"])
    maximum_frequency = float(points[-1]["freq"])
    if not minimum_frequency <= criterion.frequency_ghz <= maximum_frequency:
        return CriterionEvaluation(
            **base,
            status="not_evaluable",
            passed=None,
            actual=None,
            result_kind=ResultKind.S_PARAMETER.value,
            result_ref=result_ref,
            reason_code="frequency_outside_result_range",
            message=(
                f"criterion frequency {criterion.frequency_ghz} GHz is outside "
                f"[{minimum_frequency}, {maximum_frequency}] GHz"
            ),
        )

    actual = interpolate_s11_at_freq(points, criterion.frequency_ghz)
    if actual is None or not math.isfinite(float(actual)):
        return CriterionEvaluation(
            **base,
            status="invalid_result",
            passed=None,
            actual=None,
            result_kind=ResultKind.S_PARAMETER.value,
            result_ref=result_ref,
            reason_code="s11_interpolation_failed",
            message="could not obtain a finite S11 value at the target frequency",
        )
    passed, operator_error = _compare(
        float(actual), criterion.operator, criterion.target, criterion.tolerance
    )
    if operator_error:
        return CriterionEvaluation(
            **base,
            status="not_evaluable",
            passed=None,
            actual=float(actual),
            result_kind=ResultKind.S_PARAMETER.value,
            result_ref=result_ref,
            reason_code="unsupported_operator_contract",
            message=operator_error,
        )
    return CriterionEvaluation(
        **base,
        status="passed" if passed else "failed",
        passed=passed,
        actual=float(actual),
        result_kind=ResultKind.S_PARAMETER.value,
        result_ref=result_ref,
        reason_code="criterion_met" if passed else "criterion_not_met",
        message=(
            f"S11@{criterion.frequency_ghz}GHz={float(actual):.6g}dB "
            f"{criterion.operator} {criterion.target:.6g}dB"
        ),
    )


def _not_evaluable(
    base: dict[str, Any], reason_code: str, message: str
) -> CriterionEvaluation:
    return CriterionEvaluation(
        **base,
        status="not_evaluable",
        passed=None,
        actual=None,
        result_kind=None,
        result_ref="",
        reason_code=reason_code,
        message=message,
    )


def _looks_like_s11_payload(payload: Mapping[str, Any]) -> bool:
    item = str(payload.get("item") or "").replace("/", "\\").strip().lower()
    return (
        payload.get("result_kind") == ResultKind.S_PARAMETER.value
        or item.endswith("\\s1,1")
        or item == "s1,1"
    )


def _validate_evidence(
    evidence: ModelIRResultEvidence, *, context: AcceptanceContext
) -> str | None:
    payload = dict(evidence.payload)
    expected_values = {
        "execution_id": context.execution_id,
        "ir_id": context.ir_id,
        "revision": context.revision,
        "solver_run_id": context.solver_run_id,
        "project_sha256": context.project_sha256,
        "design_point_sha256": context.design_point_sha256,
    }
    for field_name, expected in expected_values.items():
        if getattr(evidence, field_name) != expected:
            return f"provenance mismatch for {field_name}"
    if _normalize_windows_path(evidence.project_path) != _normalize_windows_path(context.project_path):
        return "provenance mismatch for project_path"
    if not evidence.solver_tool_call_id.strip() or not evidence.result_tool_call_id.strip():
        return "solver and result tool_call_id are required"
    if not evidence.fresh_solver_boundary:
        return "result is not bound to a fresh solver boundary"
    try:
        solver_started = _parse_utc(context.solver_started_at)
        solver_completed = _parse_utc(evidence.solver_completed_at)
        result_captured = _parse_utc(evidence.result_captured_at)
    except ValueError as exc:
        return str(exc)
    if not solver_started <= solver_completed <= result_captured:
        return "solver/result timestamps violate the fresh execution boundary"
    try:
        actual_payload_sha = canonical_payload_sha256(payload)
    except (TypeError, ValueError) as exc:
        return f"typed result payload cannot be hashed canonically: {exc}"
    if evidence.payload_sha256.lower() != actual_payload_sha:
        return "typed result payload SHA-256 mismatch"
    return _validate_s11_payload(payload)


def _validate_s11_payload(payload: dict[str, Any]) -> str | None:
    if payload.get("success") is not True:
        return "S1,1 payload did not report success=true"
    try:
        validate_result_envelope(payload)
    except (TypeError, ValueError) as exc:
        return str(exc)
    if payload.get("result_kind") != ResultKind.S_PARAMETER.value:
        return "S1,1 payload must use result_kind='s_parameter'"
    item = str(payload.get("item") or "").replace("/", "\\").strip().lower()
    if not (item.endswith("\\s1,1") or item == "s1,1"):
        return "v1 acceptance requires an explicit S1,1 item identity"
    if payload.get("downsampled") is not False:
        return "v1 acceptance refuses downsampled S-parameter curves"
    points = payload.get("plot_data")
    if not isinstance(points, list) or not points:
        return "S1,1 plot_data must be a non-empty list"
    frequencies: list[float] = []
    for index, point in enumerate(points):
        if not isinstance(point, Mapping):
            return f"plot_data[{index}] must be an object"
        try:
            frequency = float(point["freq"])
            s11_db = float(point["s_db"])
        except (KeyError, TypeError, ValueError):
            return f"plot_data[{index}] requires numeric freq and s_db"
        if not math.isfinite(frequency) or not math.isfinite(s11_db):
            return f"plot_data[{index}] values must be finite"
        frequencies.append(frequency)
    if len(set(frequencies)) != len(frequencies):
        return "S1,1 frequencies must be unique"
    return None


def _parse_utc(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("evidence timestamps must be valid ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _normalize_windows_path(value: str) -> str:
    return str(PureWindowsPath(str(value or ""))).casefold()


def _compare(
    actual: float, operator: str, target: float, tolerance: float | None
) -> tuple[bool | None, str]:
    if operator == "<":
        return actual < target, ""
    if operator == "<=":
        return actual <= target, ""
    if operator == ">":
        return actual > target, ""
    if operator == ">=":
        return actual >= target, ""
    if operator == "within":
        if tolerance is None:
            return None, "within requires an explicit tolerance"
        return abs(actual - target) <= tolerance, ""
    return None, "exact floating-point equality is unsupported; use within+tolerance"
