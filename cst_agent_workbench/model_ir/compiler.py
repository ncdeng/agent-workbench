"""Pure lowering from confirmed Antenna Model-IR to canonical typed tool calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping, Sequence

from cst_agent_workbench.agent.tool_contracts import (
    ToolArgumentValidationError,
    normalize_and_validate_tool_arguments,
)

from .models import AntennaModelIR, GeometryOperation, MaterialSpec, SimulationObject
from .validation import assert_executable


GEOMETRY_TOOL_BY_KIND = {
    "brick": "create_brick",
    "cylinder": "create_cylinder",
    "extruded_polygon": "create_extruded_polygon",
    "transform": "transform_shape",
    "boolean_add": "boolean_add",
    "boolean_subtract": "boolean_subtract",
    "set_wcs": "set_wcs",
}
PORT_TOOL_BY_KIND = {
    "discrete_port": "create_discrete_port",
    "waveguide_port": "create_waveguide_port",
}
BOUNDARY_TOOL_BY_KIND = {
    "boundary": "set_boundary",
    "background": "set_background",
}
MONITOR_TOOL_BY_KIND = {
    "farfield": "create_farfield_monitor",
    "frequency_field": "create_frequency_field_monitor",
}
SOLVER_VALUE_BY_KIND = {
    "time_domain": "HF Time Domain",
    "frequency_domain": "HF Frequency Domain",
    "integral_equation": "HF IntegralEq",
    "multilayer": "HF Multilayer",
    "eigenmode": "HF Eigenmode",
    "asymptotic": "HF Asymptotic",
}
BUILTIN_MATERIALS = frozenset({"pec", "vacuum", "copper (annealed)"})
MODEL_IR_CONFIGURATION_TOOLS = frozenset(
    {
        "set_units",
        "store_parameter",
        "create_material",
        "set_frequency_range",
        "change_solver_type",
        *GEOMETRY_TOOL_BY_KIND.values(),
        *PORT_TOOL_BY_KIND.values(),
        *BOUNDARY_TOOL_BY_KIND.values(),
        *MONITOR_TOOL_BY_KIND.values(),
    }
)


class ModelIRCompileError(ValueError):
    def __init__(self, source_path: str, message: str):
        self.source_path = source_path
        super().__init__(f"{source_path}: {message}")


@dataclass(frozen=True)
class CompiledToolCall:
    tool_name: str
    arguments: dict[str, Any]
    source_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompiledModelPlan:
    ir_id: str
    revision: int
    calls: tuple[CompiledToolCall, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ir_id": self.ir_id,
            "revision": self.revision,
            "calls": [call.to_dict() for call in self.calls],
        }


def compile_model_ir(model: AntennaModelIR) -> CompiledModelPlan:
    """Compile without CST, filesystem, session, or solver side effects."""

    assert_executable(model)
    _validate_material_references(model)
    _validate_ports(model.ports)
    calls: list[CompiledToolCall] = []
    calls.append(_canonical_call("set_units", _unit_arguments(model), "parameters[*].unit"))

    parameter_indices = _stable_topological_order(
        [item.name for item in model.parameters],
        [item.dependencies for item in model.parameters],
        source_path="parameters",
    )
    for index in parameter_indices:
        parameter = model.parameters[index]
        calls.append(
            _canonical_call(
                "store_parameter",
                {"name": parameter.name, "value": parameter.expression},
                f"parameters[{index}]",
            )
        )

    for index, material in enumerate(model.materials):
        call = _compile_material(material, f"materials[{index}]")
        if call is not None:
            calls.append(call)

    geometry_indices = _stable_topological_order(
        [item.operation_id for item in model.geometry],
        [item.depends_on for item in model.geometry],
        source_path="geometry",
    )
    for index in geometry_indices:
        operation = model.geometry[index]
        calls.append(_compile_geometry(operation, f"geometry[{index}]"))

    calls.extend(
        _compile_objects(model.boundaries, BOUNDARY_TOOL_BY_KIND, "boundaries")
    )
    calls.extend(_compile_objects(model.ports, PORT_TOOL_BY_KIND, "ports"))
    calls.extend(_compile_objects(model.monitors, MONITOR_TOOL_BY_KIND, "monitors"))
    calls.extend(_compile_solver(model.solver))
    return CompiledModelPlan(model.ir_id, model.revision, tuple(calls))


def _validate_material_references(model: AntennaModelIR) -> None:
    declared = {item.name.strip().lower() for item in model.materials}
    available = declared | BUILTIN_MATERIALS
    for index, operation in enumerate(model.geometry):
        if operation.kind not in {"brick", "cylinder", "extruded_polygon"}:
            continue
        material = str(operation.arguments.get("material") or "").strip().lower()
        if material and material not in available:
            raise ModelIRCompileError(
                f"geometry[{index}].arguments.material",
                f"material {material!r} is neither built-in nor declared",
            )


def _validate_ports(ports: Sequence[SimulationObject]) -> None:
    numbers: set[int] = set()
    for index, port in enumerate(ports):
        number = port.settings.get("port_number")
        if isinstance(number, int):
            if number in numbers:
                raise ModelIRCompileError("ports", f"duplicate port_number: {number}")
            numbers.add(number)
        if port.kind != "discrete_port":
            continue
        p1 = tuple(port.settings.get(f"p1_{axis}") for axis in "xyz")
        p2 = tuple(port.settings.get(f"p2_{axis}") for axis in "xyz")
        if None not in p1 and p1 == p2:
            raise ModelIRCompileError(f"ports[{index}].settings", "discrete port endpoints must differ")


def _unit_arguments(model: AntennaModelIR) -> dict[str, Any]:
    units = {item.unit for item in model.parameters if item.unit}
    if len(units) > 1:
        raise ModelIRCompileError("parameters[*].unit", "mixed geometry units are unsupported")
    return {
        "geometry": next(iter(units), "mm"),
        "frequency": "GHz",
        "time": "ns",
    }


def _compile_material(material: MaterialSpec, source_path: str) -> CompiledToolCall | None:
    if material.name.strip().lower() in BUILTIN_MATERIALS:
        if material.properties:
            raise ModelIRCompileError(source_path, "built-in materials cannot override properties")
        return None
    allowed = {"epsilon", "mue", "tand", "tand_freq"}
    unknown = set(material.properties) - allowed
    if unknown:
        raise ModelIRCompileError(source_path, f"unknown material properties: {sorted(unknown)}")
    if "epsilon" not in material.properties:
        raise ModelIRCompileError(source_path, "custom material requires epsilon")
    arguments: dict[str, Any] = {"name": material.name}
    for key, value in material.properties.items():
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ModelIRCompileError(source_path, f"material property {key} is not numeric") from exc
        if not math.isfinite(number):
            raise ModelIRCompileError(source_path, f"material property {key} must be finite")
        arguments[key] = number
    return _canonical_call("create_material", arguments, source_path)


def _compile_geometry(operation: GeometryOperation, source_path: str) -> CompiledToolCall:
    tool_name = GEOMETRY_TOOL_BY_KIND.get(operation.kind)
    if tool_name is None:
        raise ModelIRCompileError(source_path, f"unsupported geometry kind: {operation.kind}")
    return _canonical_call(tool_name, operation.arguments, source_path)


def _compile_objects(
    objects: Sequence[SimulationObject],
    mapping: Mapping[str, str],
    category: str,
) -> tuple[CompiledToolCall, ...]:
    calls = []
    for index, item in enumerate(objects):
        source_path = f"{category}[{index}]"
        tool_name = mapping.get(item.kind)
        if tool_name is None:
            raise ModelIRCompileError(source_path, f"unsupported {category} kind: {item.kind}")
        calls.append(_canonical_call(tool_name, item.settings, source_path))
    return tuple(calls)


def _compile_solver(solver: SimulationObject | None) -> tuple[CompiledToolCall, ...]:
    if solver is None:
        raise ModelIRCompileError("solver", "solver is required")
    value = SOLVER_VALUE_BY_KIND.get(solver.kind)
    if value is None:
        raise ModelIRCompileError("solver.kind", f"unsupported solver kind: {solver.kind}")
    if set(solver.settings) != {"fmin", "fmax"}:
        raise ModelIRCompileError("solver.settings", "solver settings require exactly fmin and fmax")
    return (
        _canonical_call("set_frequency_range", solver.settings, "solver.settings"),
        _canonical_call("change_solver_type", {"solver": value}, "solver.kind"),
    )


def _canonical_call(
    tool_name: str,
    arguments: Mapping[str, Any],
    source_path: str,
) -> CompiledToolCall:
    try:
        normalized = normalize_and_validate_tool_arguments(tool_name, arguments)
    except ToolArgumentValidationError as exc:
        raise ModelIRCompileError(source_path, str(exc)) from exc
    return CompiledToolCall(tool_name, normalized, source_path)


def _stable_topological_order(
    ids: Sequence[str],
    dependencies: Sequence[Sequence[str]],
    *,
    source_path: str,
) -> tuple[int, ...]:
    index_by_id = {name: index for index, name in enumerate(ids)}
    if len(index_by_id) != len(ids):
        raise ModelIRCompileError(source_path, "duplicate identifiers")
    remaining = [set(values) for values in dependencies]
    for index, values in enumerate(remaining):
        unknown = values - set(index_by_id)
        if unknown:
            raise ModelIRCompileError(f"{source_path}[{index}]", f"unknown dependencies: {sorted(unknown)}")
    ordered: list[int] = []
    completed: set[str] = set()
    while len(ordered) < len(ids):
        ready = [
            index
            for index, name in enumerate(ids)
            if index not in ordered and remaining[index] <= completed
        ]
        if not ready:
            raise ModelIRCompileError(source_path, "dependency graph contains a cycle")
        index = ready[0]
        ordered.append(index)
        completed.add(ids[index])
    return tuple(ordered)
