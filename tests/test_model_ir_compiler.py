from __future__ import annotations

import json

import pytest

from cst_agent_workbench.model_ir import (
    AcceptanceCriterion,
    AntennaModelIR,
    GeometryOperation,
    MaterialSpec,
    ModelIRCompileError,
    ParameterSpec,
    SimulationObject,
    compile_model_ir,
    confirm_model_ir,
)


def _confirmed(**changes) -> AntennaModelIR:
    data = {
        "ir_id": "canonical_patch",
        "title": "Canonical compiler fixture",
        "task_mode": "requirement_synthesis",
        "source_ids": ("user-message-1",),
        "parameters": (
            ParameterSpec("half", "length/2", dependencies=("length",)),
            ParameterSpec("length", "28"),
        ),
        "materials": (MaterialSpec("sub", "FR4", {"epsilon": "4.4", "tand": "0.02"}),),
        "geometry": (
            GeometryOperation(
                "patch",
                "brick",
                {
                    "name": "patch",
                    "component": "antenna",
                    "material": "PEC",
                    "xmin": "-half",
                    "xmax": "half",
                    "ymin": "-10",
                    "ymax": "10",
                    "zmin": "1.6",
                    "zmax": "1.635",
                },
                depends_on=("substrate",),
            ),
            GeometryOperation(
                "substrate",
                "brick",
                {
                    "name": "substrate",
                    "component": "antenna",
                    "material": "FR4",
                    "xmin": "-20",
                    "xmax": "20",
                    "ymin": "-20",
                    "ymax": "20",
                    "zmin": "0",
                    "zmax": "1.6",
                },
            ),
        ),
        "ports": (
            SimulationObject(
                "port1",
                "discrete_port",
                {
                    "port_number": 1,
                    "p1_x": "0",
                    "p1_y": "0",
                    "p1_z": "0",
                    "p2_x": "0",
                    "p2_y": "0",
                    "p2_z": "1.6",
                },
            ),
        ),
        "boundaries": (SimulationObject("open", "boundary", {}),),
        "monitors": (
            SimulationObject(
                "ff",
                "farfield",
                {"name": "farfield (f=5.8)", "frequency": "5.8"},
            ),
        ),
        "solver": SimulationObject(
            "solver",
            "time_domain",
            {"fmin": "4.0", "fmax": "7.0"},
        ),
        "acceptance_criteria": (
            AcceptanceCriterion("s11", "s11_db", "<=", -10, "dB", frequency_ghz=5.8),
        ),
    }
    data.update(changes)
    return confirm_model_ir(AntennaModelIR(**data), confirmed_by="local-user")


def test_compiler_lowers_to_canonical_tools_in_stable_phase_order():
    plan = compile_model_ir(_confirmed())

    assert [call.tool_name for call in plan.calls] == [
        "set_units",
        "store_parameter",
        "store_parameter",
        "create_material",
        "create_brick",
        "create_brick",
        "set_boundary",
        "create_discrete_port",
        "create_farfield_monitor",
        "set_frequency_range",
        "change_solver_type",
    ]
    assert [call.arguments.get("name") for call in plan.calls[1:3]] == ["length", "half"]
    assert [call.arguments["name"] for call in plan.calls[4:6]] == ["substrate", "patch"]
    assert plan.calls[6].arguments == {
        side: "expanded open"
        for side in ("xmin", "xmax", "ymin", "ymax", "zmin", "zmax")
    }
    assert plan.calls[-1].arguments == {"solver": "HF Time Domain"}
    assert "run_solver" not in [call.tool_name for call in plan.calls]


def test_compiler_is_deterministic_and_round_trip_stable():
    model = _confirmed()
    restored = AntennaModelIR.from_dict(model.to_dict())

    first = json.dumps(compile_model_ir(model).to_dict(), sort_keys=True)
    second = json.dumps(compile_model_ir(restored).to_dict(), sort_keys=True)

    assert first == second


def test_compiler_reuses_canonical_defaults_for_geometry():
    cylinder = GeometryOperation(
        "probe",
        "cylinder",
        {
            "name": "probe",
            "component": "antenna",
            "material": "PEC",
            "axis": "z",
            "outer_radius": "0.5",
            "zmin": "0",
            "zmax": "1.6",
        },
    )
    plan = compile_model_ir(_confirmed(geometry=(cylinder,)))
    call = next(call for call in plan.calls if call.tool_name == "create_cylinder")

    assert call.arguments["inner_radius"] == "0"
    assert call.arguments["xcenter"] == "0"
    assert call.arguments["ycenter"] == "0"


def test_compiler_fails_closed_on_unconfirmed_ir():
    confirmed = _confirmed()
    draft = confirmed.with_revision(title="changed")

    with pytest.raises(ValueError, match="model_ir_not_confirmed"):
        compile_model_ir(draft)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"solver": SimulationObject("s", "unknown", {"fmin": "1", "fmax": "2"})}, "unsupported solver"),
        ({"ports": (SimulationObject("p", "magic", {}),)}, "unsupported ports"),
        (
            {"materials": (
                MaterialSpec("sub", "FR4", {"epsilon": "4.4"}),
                MaterialSpec("m", "Custom", {"epsilon": "nan"}),
            )},
            "must be finite",
        ),
        (
            {"materials": (
                MaterialSpec("sub", "FR4", {"epsilon": "4.4"}),
                MaterialSpec("m", "Custom", {"epsilon": "4", "magic": "1"}),
            )},
            "unknown material",
        ),
    ],
)
def test_compiler_rejects_unsupported_or_ambiguous_contracts(changes, match):
    with pytest.raises(ModelIRCompileError, match=match):
        compile_model_ir(_confirmed(**changes))


def test_compiler_rejects_noncanonical_legacy_aliases():
    model = _confirmed(
        monitors=(SimulationObject("ff", "farfield", {"frequency_ghz": 5.8}),)
    )

    with pytest.raises(ModelIRCompileError, match=r"monitors\[0\]"):
        compile_model_ir(model)


def test_compiler_rejects_missing_material_duplicate_port_and_equal_endpoints():
    missing_material = GeometryOperation(
        "shape",
        "brick",
        {
            "name": "shape", "component": "c", "material": "Missing",
            "xmin": "0", "xmax": "1", "ymin": "0", "ymax": "1",
            "zmin": "0", "zmax": "1",
        },
    )
    with pytest.raises(ModelIRCompileError, match="neither built-in nor declared"):
        compile_model_ir(_confirmed(geometry=(missing_material,)))

    port = SimulationObject(
        "p1", "discrete_port",
        {
            "port_number": 1, "p1_x": "0", "p1_y": "0", "p1_z": "0",
            "p2_x": "0", "p2_y": "0", "p2_z": "1",
        },
    )
    with pytest.raises(ModelIRCompileError, match="duplicate port_number"):
        compile_model_ir(_confirmed(ports=(port, SimulationObject("p2", port.kind, port.settings))))

    equal = SimulationObject(
        "equal", "discrete_port",
        {**port.settings, "p2_z": "0"},
    )
    with pytest.raises(ModelIRCompileError, match="endpoints must differ"):
        compile_model_ir(_confirmed(ports=(equal,)))
