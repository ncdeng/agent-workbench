from __future__ import annotations

import json

import pytest

from cst_agent_workbench.model_ir import (
    AcceptanceCriterion,
    AntennaModelIR,
    EvidenceRef,
    GeometryOperation,
    ModelIRRepository,
    ParameterSpec,
    SimulationObject,
)


def _model(ir_id: str = "paper_patch") -> AntennaModelIR:
    evidence = EvidenceRef("paper-sha", "Table I", page=2)
    return AntennaModelIR(
        ir_id=ir_id,
        title="Patch reproduction",
        task_mode="paper_reproduction",
        source_ids=("paper-sha",),
        parameters=(ParameterSpec("patch_l", "28", evidence=(evidence,)),),
        geometry=(GeometryOperation("patch", "brick", {}, evidence=(evidence,)),),
        solver=SimulationObject("solver", "time_domain", {"fmin_ghz": 2.0, "fmax_ghz": 3.0}),
        acceptance_criteria=(AcceptanceCriterion("s11", "s11_db", "<=", -10, "dB"),),
    )


def test_repository_round_trip_and_listing(tmp_path) -> None:
    repository = ModelIRRepository(tmp_path / "model_ir")
    original = _model()

    target = repository.save(original)

    assert target.parent == (tmp_path / "model_ir").resolve()
    assert repository.load(original.ir_id) == original
    assert repository.list_ids() == (original.ir_id,)
    assert not target.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize("unsafe_id", ["../escape", "a/b", "a\\b", "", ".hidden"])
def test_repository_rejects_unsafe_ids(tmp_path, unsafe_id: str) -> None:
    repository = ModelIRRepository(tmp_path)

    with pytest.raises(ValueError, match="safe filename"):
        repository.path_for(unsafe_id)


def test_repository_does_not_silently_accept_corruption(tmp_path) -> None:
    repository = ModelIRRepository(tmp_path)
    target = repository.path_for("broken")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not-json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        repository.load("broken")


def test_repository_detects_filename_payload_mismatch(tmp_path) -> None:
    repository = ModelIRRepository(tmp_path)
    target = repository.path_for("expected")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_model("other").to_dict()), encoding="utf-8")

    with pytest.raises(ValueError, match="do not match"):
        repository.load("expected")
