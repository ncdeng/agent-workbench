"""Deterministic validation and execution gating for Antenna Model-IR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import AntennaModelIR


_PARAMETER_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SUPPORTED_GEOMETRY_KINDS = frozenset(
    {
        "brick",
        "cylinder",
        "extruded_polygon",
        "transform",
        "boolean_add",
        "boolean_subtract",
        "set_wcs",
    }
)


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    executable: bool

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "executable": self.executable,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_model_ir(model: AntennaModelIR, *, require_confirmed: bool = False) -> ValidationReport:
    issues: list[ValidationIssue] = []

    if model.task_mode == "paper_reproduction" and not model.source_ids:
        _error(issues, "paper_source_missing", "source_ids", "paper reproduction requires at least one source")

    parameter_names = [parameter.name for parameter in model.parameters]
    _check_unique(issues, parameter_names, "parameters", "duplicate_parameter")
    parameter_set = set(parameter_names)
    dependency_graph: dict[str, tuple[str, ...]] = {}
    for index, parameter in enumerate(model.parameters):
        path = f"parameters[{index}]"
        if not _PARAMETER_NAME_RE.fullmatch(parameter.name):
            _error(issues, "invalid_parameter_name", f"{path}.name", "name must be a valid CST identifier")
        missing = [name for name in parameter.dependencies if name not in parameter_set]
        if missing:
            _error(
                issues,
                "unknown_parameter_dependency",
                f"{path}.dependencies",
                f"unknown dependencies: {', '.join(missing)}",
            )
        if parameter.name in parameter.dependencies:
            _error(issues, "self_parameter_dependency", f"{path}.dependencies", "parameter cannot depend on itself")
        if model.task_mode == "paper_reproduction" and parameter.provenance in {"stated", "derived"}:
            if not parameter.evidence:
                _error(issues, "parameter_evidence_missing", f"{path}.evidence", "paper-derived parameter needs evidence")
        dependency_graph[parameter.name] = parameter.dependencies
    if _has_cycle(dependency_graph):
        _error(issues, "parameter_dependency_cycle", "parameters", "parameter dependency graph contains a cycle")

    material_ids = [material.material_id for material in model.materials]
    _check_unique(issues, material_ids, "materials", "duplicate_material")
    operation_ids = [operation.operation_id for operation in model.geometry]
    _check_unique(issues, operation_ids, "geometry", "duplicate_geometry_operation")
    operation_set = set(operation_ids)
    operation_graph: dict[str, tuple[str, ...]] = {}
    for index, operation in enumerate(model.geometry):
        path = f"geometry[{index}]"
        if operation.kind not in SUPPORTED_GEOMETRY_KINDS:
            _error(
                issues,
                "unsupported_geometry_kind",
                f"{path}.kind",
                f"compiler does not support {operation.kind!r}",
            )
        missing = [name for name in operation.depends_on if name not in operation_set]
        if missing:
            _error(
                issues,
                "unknown_geometry_dependency",
                f"{path}.depends_on",
                f"unknown operations: {', '.join(missing)}",
            )
        operation_graph[operation.operation_id] = operation.depends_on
    if _has_cycle(operation_graph):
        _error(issues, "geometry_dependency_cycle", "geometry", "geometry operation graph contains a cycle")

    object_ids = [
        *(item.object_id for item in model.ports),
        *(item.object_id for item in model.boundaries),
        *(item.object_id for item in model.monitors),
        *((model.solver.object_id,) if model.solver else ()),
    ]
    _check_unique(issues, object_ids, "simulation", "duplicate_simulation_object")

    criterion_ids = [criterion.criterion_id for criterion in model.acceptance_criteria]
    _check_unique(issues, criterion_ids, "acceptance_criteria", "duplicate_acceptance_criterion")
    if not model.acceptance_criteria:
        _error(issues, "acceptance_criteria_missing", "acceptance_criteria", "at least one success criterion is required")
    if not model.geometry:
        _error(issues, "geometry_missing", "geometry", "at least one geometry operation is required")
    if model.solver is None:
        _error(issues, "solver_missing", "solver", "solver configuration is required")

    for index, assumption in enumerate(model.assumptions):
        if assumption.status == "proposed":
            _error(
                issues,
                "assumption_unconfirmed",
                f"assumptions[{index}].status",
                "assumption must be accepted or rejected before execution",
            )
    for index, clarification in enumerate(model.clarifications):
        if clarification.blocking and clarification.status == "open":
            _error(
                issues,
                "blocking_clarification_open",
                f"clarifications[{index}].status",
                "blocking clarification must be resolved or dismissed",
            )
    for index, conflict in enumerate(model.conflicts):
        if conflict.status == "open":
            _error(
                issues,
                "source_conflict_open",
                f"conflicts[{index}].status",
                "source conflict must be resolved before execution",
            )

    if require_confirmed and model.status != "confirmed":
        _error(issues, "model_ir_not_confirmed", "status", "Model-IR must be explicitly confirmed before execution")
    if model.status == "confirmed" and (not model.confirmed_by or not model.confirmed_at):
        _error(issues, "confirmation_metadata_missing", "status", "confirmed Model-IR needs actor and timestamp")

    errors = any(issue.severity == "error" for issue in issues)
    executable = not errors and model.status == "confirmed"
    return ValidationReport(tuple(issues), executable=executable)


def confirm_model_ir(model: AntennaModelIR, *, confirmed_by: str) -> AntennaModelIR:
    """Validate and freeze a review decision before any CST side effect."""
    report = validate_model_ir(model, require_confirmed=False)
    if not report.valid:
        codes = ", ".join(issue.code for issue in report.errors)
        raise ValueError(f"Model-IR cannot be confirmed: {codes}")
    confirmed = model.mark_confirmed(confirmed_by)
    report = validate_model_ir(confirmed, require_confirmed=True)
    if not report.executable:
        raise ValueError("Model-IR confirmation did not produce an executable document")
    return confirmed


def assert_executable(model: AntennaModelIR) -> None:
    report = validate_model_ir(model, require_confirmed=True)
    if not report.executable:
        codes = ", ".join(issue.code for issue in report.errors)
        raise ValueError(f"Model-IR execution blocked: {codes}")


def _error(issues: list[ValidationIssue], code: str, path: str, message: str) -> None:
    issues.append(ValidationIssue("error", code, path, message))


def _check_unique(issues: list[ValidationIssue], values: Iterable[str], path: str, code: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        _error(issues, code, path, f"duplicate identifiers: {', '.join(sorted(duplicates))}")


def _has_cycle(graph: dict[str, tuple[str, ...]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for dependency in graph.get(node, ()):
            if dependency in graph and visit(dependency):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)
