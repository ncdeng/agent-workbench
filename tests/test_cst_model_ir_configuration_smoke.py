from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks.cst_model_ir_configuration_smoke import (
    EXPECTED_PARAMETERS,
    EXPECTED_SOLIDS,
    _read_parameters,
    build_public_slot_patch_ir,
)
from cst_agent_workbench.model_ir import compile_model_ir


def test_public_fixture_compiles_to_typed_configuration_without_solver_run():
    model = build_public_slot_patch_ir()
    plan = compile_model_ir(model)
    tool_names = [call.tool_name for call in plan.calls]

    assert model.status == "confirmed"
    assert model.source_ids == ("public-synthetic-requirement-v1",)
    assert tool_names.count("store_parameter") == len(EXPECTED_PARAMETERS)
    assert "boolean_subtract" in tool_names
    assert "create_cylinder" in tool_names
    assert "create_discrete_port" in tool_names
    assert "create_farfield_monitor" in tool_names
    assert tool_names[-2:] == ["set_frequency_range", "change_solver_type"]
    assert "run_solver" not in tool_names


def test_public_fixture_declares_expected_persisted_solids():
    model = build_public_slot_patch_ir()
    created = {
        f"{operation.arguments['component']}:{operation.arguments['name']}"
        for operation in model.geometry
        if operation.kind in {"brick", "cylinder", "extruded_polygon"}
    }
    subtract = next(operation for operation in model.geometry if operation.kind == "boolean_subtract")

    assert created - {subtract.arguments["obj2"]} == EXPECTED_SOLIDS


def test_parameter_reader_binds_saved_cst_values(tmp_path):
    project = tmp_path / "fixture.cst"
    parameters_path = tmp_path / "fixture" / "Model" / "Parameters.json"
    parameters_path.parent.mkdir(parents=True)
    parameters_path.write_text(
        json.dumps(
            {
                "parameters": [
                    {"name": name, "value": str(value)}
                    for name, value in EXPECTED_PARAMETERS.items()
                ]
                + [{"name": "unrelated", "value": "999"}]
            }
        ),
        encoding="utf-8",
    )

    assert _read_parameters(project) == EXPECTED_PARAMETERS


def test_real_smoke_output_is_forced_to_d_drive():
    from benchmarks.cst_model_ir_configuration_smoke import run_smoke

    with pytest.raises(ValueError, match="D 盘"):
        run_smoke(Path("E:/must-not-be-created/model-ir-smoke"))


def test_runner_direct_script_entrypoint_imports_before_argument_validation():
    repository_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            str(repository_root / "benchmarks" / "cst_model_ir_configuration_smoke.py"),
            "--output-root",
            "E:/must-not-be-created/model-ir-smoke",
        ],
        cwd=repository_root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "ModuleNotFoundError" not in result.stderr
    assert "D 盘" in result.stderr
