from __future__ import annotations

from dataclasses import replace

import pytest

from cst_agent_workbench.model_ir import (
    AcceptanceCriterion,
    AntennaModelIR,
    Assumption,
    Clarification,
    Conflict,
    EvidenceRef,
    GeometryOperation,
    MaterialSpec,
    ParameterSpec,
    SimulationObject,
    assert_executable,
    confirm_model_ir,
    validate_model_ir,
)


def _evidence(locator: str = "Table I") -> EvidenceRef:
    return EvidenceRef(
        source_id="paper-sha256",
        page=3,
        locator=locator,
        quote="L = 28 mm",
        method="table",
        confidence=0.99,
    )


def make_valid_ir(**changes: object) -> AntennaModelIR:
    data: dict[str, object] = {
        "ir_id": "paper_patch_v1",
        "title": "Evidence-grounded slot patch reproduction",
        "task_mode": "paper_reproduction",
        "source_ids": ("paper-sha256",),
        "parameters": (
            ParameterSpec("patch_l", "28", evidence=(_evidence(),)),
            ParameterSpec("slot_l", "patch_l/2", dependencies=("patch_l",), evidence=(_evidence("Figure 2"),)),
        ),
        "materials": (MaterialSpec("substrate", "FR-4", {"epsilon": "4.4"}, evidence=(_evidence(),)),),
        "geometry": (
            GeometryOperation("substrate", "brick", {"material_id": "substrate"}, evidence=(_evidence(),)),
            GeometryOperation(
                "slot_profile",
                "extruded_polygon",
                {"points": [[0, 0], [1, 0], [1, 3], [0, 3]], "height": "0.035"},
                depends_on=("substrate",),
                evidence=(_evidence("Figure 2"),),
            ),
        ),
        "ports": (SimulationObject("port1", "discrete_port", {"impedance_ohm": 50}),),
        "boundaries": (SimulationObject("open_boundary", "expanded_open", {"all": True}),),
        "monitors": (SimulationObject("ff_5_8", "farfield", {"frequency_ghz": 5.8}),),
        "solver": SimulationObject("solver", "time_domain", {"fmin_ghz": 5.0, "fmax_ghz": 6.5}),
        "acceptance_criteria": (
            AcceptanceCriterion("s11", "s11_db", "<=", -10, "dB", frequency_ghz=5.8, evidence=(_evidence(),)),
        ),
    }
    data.update(changes)
    return AntennaModelIR(**data)


def test_model_ir_round_trip_preserves_nested_evidence() -> None:
    original = make_valid_ir()

    restored = AntennaModelIR.from_dict(original.to_dict())

    assert restored == original
    assert restored.parameters[0].evidence[0].locator == "Table I"
    assert restored.geometry[1].depends_on == ("substrate",)


def test_confirmation_is_required_before_execution() -> None:
    draft = make_valid_ir()

    draft_report = validate_model_ir(draft, require_confirmed=True)
    assert not draft_report.executable
    assert {issue.code for issue in draft_report.errors} == {"model_ir_not_confirmed"}
    with pytest.raises(ValueError, match="model_ir_not_confirmed"):
        assert_executable(draft)

    confirmed = confirm_model_ir(draft, confirmed_by="user")
    assert confirmed.status == "confirmed"
    assert confirmed.confirmed_at
    assert validate_model_ir(confirmed, require_confirmed=True).executable
    assert_executable(confirmed)


def test_revision_invalidates_confirmation() -> None:
    confirmed = confirm_model_ir(make_valid_ir(), confirmed_by="user")

    revised = confirmed.with_revision(title="Revised slot patch")

    assert revised.revision == 2
    assert revised.status == "draft"
    assert revised.confirmed_by == ""
    assert revised.confirmed_at == ""


def test_unresolved_human_decisions_block_confirmation() -> None:
    draft = make_valid_ir(
        assumptions=(Assumption("a1", "Use 35 um copper", "Changes conductor loss", ("materials.copper",)),),
        clarifications=(Clarification("q1", "Which feed reference plane?", ("ports.port1",)),),
        conflicts=(
            Conflict(
                "c1",
                "parameters.patch_l",
                "Table and figure disagree",
                (_evidence("Table I"), _evidence("Figure 2")),
            ),
        ),
    )

    report = validate_model_ir(draft)

    assert {issue.code for issue in report.errors} == {
        "assumption_unconfirmed",
        "blocking_clarification_open",
        "source_conflict_open",
    }
    with pytest.raises(ValueError, match="cannot be confirmed"):
        confirm_model_ir(draft, confirmed_by="user")


def test_resolved_human_decisions_allow_confirmation() -> None:
    draft = make_valid_ir(
        assumptions=(
            Assumption(
                "a1",
                "Use 35 um copper",
                "Changes conductor loss",
                ("materials.copper",),
                status="accepted",
            ),
        ),
        clarifications=(
            Clarification(
                "q1",
                "Which feed reference plane?",
                ("ports.port1",),
                status="resolved",
                answer="substrate edge",
            ),
        ),
        conflicts=(
            Conflict(
                "c1",
                "parameters.patch_l",
                "Table and figure disagree",
                (_evidence("Table I"), _evidence("Figure 2")),
                status="resolved",
                resolution="Use the tabulated value",
            ),
        ),
    )

    confirmed = confirm_model_ir(draft, confirmed_by="user")

    assert validate_model_ir(confirmed, require_confirmed=True).executable


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ({"source_ids": ()}, "paper_source_missing"),
        ({"geometry": ()}, "geometry_missing"),
        ({"solver": None}, "solver_missing"),
        ({"acceptance_criteria": ()}, "acceptance_criteria_missing"),
    ],
)
def test_required_sections_are_validated(change: dict[str, object], expected_code: str) -> None:
    report = validate_model_ir(make_valid_ir(**change))

    assert expected_code in {issue.code for issue in report.errors}


def test_parameter_and_geometry_dependency_cycles_are_rejected() -> None:
    model = make_valid_ir(
        parameters=(
            ParameterSpec("a", "b", dependencies=("b",), evidence=(_evidence(),)),
            ParameterSpec("b", "a", dependencies=("a",), evidence=(_evidence(),)),
        ),
        geometry=(
            GeometryOperation("a", "brick", {}, depends_on=("b",)),
            GeometryOperation("b", "brick", {}, depends_on=("a",)),
        ),
    )

    report = validate_model_ir(model)

    assert {"parameter_dependency_cycle", "geometry_dependency_cycle"}.issubset(
        {issue.code for issue in report.errors}
    )


def test_paper_stated_parameter_needs_evidence() -> None:
    model = make_valid_ir(parameters=(ParameterSpec("patch_l", "28"),))

    report = validate_model_ir(model)

    assert "parameter_evidence_missing" in {issue.code for issue in report.errors}


def test_unsupported_geometry_is_reported_before_compilation() -> None:
    model = make_valid_ir(geometry=(GeometryOperation("helix", "freeform_magic", {}),))

    report = validate_model_ir(model)

    assert "unsupported_geometry_kind" in {issue.code for issue in report.errors}


def test_confirmation_metadata_cannot_be_forged_by_status_only() -> None:
    model = replace(make_valid_ir(), status="confirmed")

    report = validate_model_ir(model, require_confirmed=True)

    assert "confirmation_metadata_missing" in {issue.code for issue in report.errors}


def test_confirmed_status_cannot_bypass_open_clarification() -> None:
    confirmed = confirm_model_ir(make_valid_ir(), confirmed_by="user")
    forged = replace(
        confirmed,
        clarifications=(Clarification("q1", "What is the feed location?", ("ports.port1",)),),
    )

    report = validate_model_ir(forged, require_confirmed=True)

    assert not report.executable
    assert "blocking_clarification_open" in {issue.code for issue in report.errors}
