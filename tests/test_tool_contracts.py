from __future__ import annotations

from benchmarks.agent_e2e_ablation_runner import _evaluate_argument_oracle
from cst_agent_workbench.agent.tool_contracts import (
    ToolArgumentValidationError,
    apply_declared_argument_defaults,
    declared_argument_defaults,
    normalize_and_validate_tool_arguments,
)
from cst_agent_workbench.agent.tools import TOOLS
import pytest


def test_declared_defaults_come_from_canonical_tool_schema():
    assert declared_argument_defaults("create_farfield_monitor") == {
        "use_subvolume": False
    }


def test_explicit_argument_overrides_schema_default():
    normalized = apply_declared_argument_defaults(
        "create_farfield_monitor",
        {"name": "ff", "frequency": "8.5", "use_subvolume": True},
    )

    assert normalized["use_subvolume"] is True


def test_argument_oracle_accepts_omitted_optional_default():
    oracle = {
        "expected_tool_arguments": [
            {
                "tool_name": "create_farfield_monitor",
                "occurrence": 1,
                "arguments": {
                    "name": "ff_1",
                    "frequency": "8.5",
                    "use_subvolume": False,
                },
            }
        ]
    }
    events = [
        {
            "tool_name": "create_farfield_monitor",
            "arguments": {"name": "ff_1", "frequency": "8.5"},
        }
    ]

    checks = _evaluate_argument_oracle(oracle, events)

    assert checks[0]["passed"] is True
    assert checks[0]["actual"]["use_subvolume"] is False


def test_unknown_tool_arguments_are_preserved_without_defaults():
    assert apply_declared_argument_defaults("unknown", {"x": 1}) == {"x": 1}


def test_all_canonical_tool_argument_objects_are_closed():
    assert all(
        tool["function"]["parameters"].get("additionalProperties") is False
        for tool in TOOLS
    )


def test_validation_applies_defaults_and_rejects_unknown_fields():
    assert normalize_and_validate_tool_arguments(
        "create_farfield_monitor",
        {"name": "ff", "frequency": "8.5"},
    )["use_subvolume"] is False

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        normalize_and_validate_tool_arguments(
            "create_farfield_monitor",
            {"name": "ff", "frequency": "8.5", "frequecy": "9.0"},
        )
    assert exc_info.value.violations[0].validator == "additionalProperties"


def test_mesh_contract_defaults_and_nested_schema_are_canonical():
    normalized = normalize_and_validate_tool_arguments(
        "set_global_hexahedral_mesh", {}
    )
    assert normalized == {"lines_per_wavelength": 15, "minimum_step_number": 5}

    with pytest.raises(ToolArgumentValidationError) as exc_info:
        normalize_and_validate_tool_arguments(
            "add_solids_to_mesh_group",
            {"group_name": "g", "solids": [{"component": "A", "name": "P", "raw": "solid$A:P"}]},
        )
    assert exc_info.value.violations[0].validator == "additionalProperties"


@pytest.mark.parametrize(
    "arguments",
    [
        {"port_number": 1, "coordinate_mode": "Free", "orientation": "zmax"},
        {"port_number": 1, "coordinate_mode": "Free", "orientation": "Positive", "ranges": {"x": [0, 1], "y": [0, 1], "z": [0, 0]}},
        {"port_number": 1, "coordinate_mode": "Full", "orientation": "zmin", "pick": {"solid": "A:B", "face_id": 1}},
        {"port_number": 1, "coordinate_mode": "Picks", "orientation": "Positive"},
        {"port_number": 1, "coordinate_mode": "Picks", "orientation": "Positive", "pick": {"solid": "A:B", "face_id": 1}, "range_add": {}},
        {"port_number": 1, "coordinate_mode": "Picks", "orientation": "Positive", "pick": {"solid": "A:B", "face_id": 1, "raw_vba": "bad"}},
    ],
)
def test_waveguide_port_contract_rejects_invalid_mode_specific_arguments(arguments):
    with pytest.raises(ToolArgumentValidationError):
        normalize_and_validate_tool_arguments(
            "create_waveguide_port",
            arguments,
        )


@pytest.mark.parametrize(
    "arguments",
    [
        {"port_number": 1, "coordinate_mode": "Free", "orientation": "zmax", "ranges": {"x": [0, 1], "y": [0, 1], "z": [0, 0]}},
        {"port_number": 2, "coordinate_mode": "Full", "orientation": "zmin"},
        {"port_number": 3, "coordinate_mode": "Picks", "orientation": "Negative", "pick": {"solid": "A:B", "face_id": 1}},
    ],
)
def test_waveguide_port_contract_accepts_each_coordinate_mode(arguments):
    normalized = normalize_and_validate_tool_arguments("create_waveguide_port", arguments)
    assert normalized["coordinate_mode"] == arguments["coordinate_mode"]


@pytest.mark.parametrize(
    "arguments",
    [None, "not-json", ["not", "an", "object"]],
)
def test_validation_rejects_non_object_arguments(arguments):
    with pytest.raises(ToolArgumentValidationError):
        normalize_and_validate_tool_arguments("check_cst_status", arguments)
