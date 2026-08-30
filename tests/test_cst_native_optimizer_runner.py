from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.cst_native_optimizer_runner import (
    OPTIMIZER_REPORTED_PARAMETER_ABS_TOL,
    REQUIRED_GATES,
    clear_stale_optimizer_evidence,
    clone_cst_project,
    parse_native_optimizer_evidence,
    validate_native_optimizer_request,
)


def test_optimizer_parameter_binding_tolerance_matches_four_decimal_artifact():
    assert abs(11.5767 - 11.576719153703) < OPTIMIZER_REPORTED_PARAMETER_ABS_TOL
    assert abs(11.5767 - 11.5768) > OPTIMIZER_REPORTED_PARAMETER_ABS_TOL


def test_live_native_optimizer_requires_all_four_explicit_gates(tmp_path):
    project = tmp_path / "source.cst"
    project.write_bytes(b"fixture")

    with pytest.raises(PermissionError, match="RUN_LIVE_CST_OPTIMIZER"):
        validate_native_optimizer_request(
            source_project=project,
            artifact_root=tmp_path / "artifacts",
            environment={name: "1" for name in REQUIRED_GATES[:-1]},
        )


def test_live_native_optimizer_rejects_non_d_drive_even_with_gates():
    project = Path("C:/definitely-not-a-live-cst-project/source.cst")

    with pytest.raises(ValueError, match="must be on D"):
        validate_native_optimizer_request(
            source_project=project,
            artifact_root=Path("D:/cst_agent_rag_data/agent_eval/native_optimizer/test"),
            environment={name: "1" for name in REQUIRED_GATES},
        )


def test_clone_copies_container_and_companion_directory(tmp_path):
    source = tmp_path / "source.cst"
    source.write_bytes(b"cst")
    companion = tmp_path / "source"
    (companion / "Model").mkdir(parents=True)
    (companion / "Model" / "Parameters.json").write_text(
        json.dumps({"parameters": []}), encoding="utf-8"
    )

    cloned = clone_cst_project(source, tmp_path / "run")

    assert cloned.read_bytes() == b"cst"
    assert (cloned.with_suffix("") / "Model" / "Parameters.json").is_file()


def test_stale_optimizer_artifacts_are_removed_only_from_disposable_clone(tmp_path):
    source = tmp_path / "source.cst"
    source.write_bytes(b"cst")
    result = source.with_suffix("") / "Result"
    result.mkdir(parents=True)
    for name in ("Model.opt", "Model_ui.opt", "output.txt"):
        (result / name).write_text(name, encoding="utf-8")
    (result / "Model.res").write_bytes(b"solver-result")
    cloned = clone_cst_project(source, tmp_path / "run")

    removed = clear_stale_optimizer_evidence(cloned)

    assert all(item["existed"] for item in removed.values())
    assert all((result / name).is_file() for name in removed)
    assert all(not (cloned.with_suffix("") / "Result" / name).exists() for name in removed)
    assert (cloned.with_suffix("") / "Result" / "Model.res").read_bytes() == b"solver-result"


def test_parse_optimizer_evidence_distinguishes_reload_from_solver(tmp_path):
    project = tmp_path / "antenna.cst"
    project.write_bytes(b"cst")
    result = project.with_suffix("") / "Result"
    result.mkdir(parents=True)
    (result / "Model_ui.opt").write_text(
        "Algorithm: Trust Region Framework\n"
        "Number of evaluations: 1\n"
        "              (solver: 0, reloaded: 1)\n",
        encoding="utf-8",
    )
    (result / "Model.opt").write_text(
        "Trust Region Framework: Exploring a new trust region model would exceed "
        "the maximal number of evaluations.\n"
        "The algorithm will be aborted internally.\n"
        "Optimization aborted without improved goal value.\n",
        encoding="utf-8",
    )

    evidence = parse_native_optimizer_evidence(project)

    assert evidence["optimizer_started"] is True
    assert evidence["optimizer_evaluation_count"] == 1
    assert evidence["solver_evaluation_count"] == 0
    assert evidence["reloaded_evaluation_count"] == 1
    assert evidence["optimizer_completed"] is True
    assert evidence["termination_status"] == "aborted"
    assert evidence["reported_improved_goal"] is False
    assert evidence["corresponding_run_id"] is None


def test_parse_optimizer_evidence_accepts_new_solver_evaluations(tmp_path):
    project = tmp_path / "antenna.cst"
    project.write_bytes(b"cst")
    result = project.with_suffix("") / "Result"
    result.mkdir(parents=True)
    (result / "Model_ui.opt").write_text(
        "Algorithm: Nelder Mead Simplex\n"
        "Number of evaluations: 4\n"
        "              (solver: 3, reloaded: 1)\n",
        encoding="utf-8",
    )
    (result / "Model.opt").write_text(
        "Optimization completed with improved goal value.\n", encoding="utf-8"
    )

    evidence = parse_native_optimizer_evidence(project)

    assert evidence["solver_evaluation_count"] == 3
    assert evidence["optimizer_completed"] is True
    assert evidence["termination_status"] == "completed"
    assert evidence["reported_improved_goal"] is True


def test_parse_optimizer_evidence_accepts_cst_successfully_improved_wording(tmp_path):
    project = tmp_path / "antenna.cst"
    project.write_bytes(b"cst")
    result = project.with_suffix("") / "Result"
    result.mkdir(parents=True)
    (result / "Model_ui.opt").write_text(
        "Algorithm: Nelder Mead Simplex Algorithm\n"
        "Number of evaluations: 4\n"
        "              (solver: 2, reloaded: 2)\n",
        encoding="utf-8",
    )
    (result / "Model.opt").write_text(
        "Maximum number of solver evaluations reached. "
        "Nelder Mead Simplex optimization completed.\n"
        "Optimization successfully improved the goal value.\n",
        encoding="utf-8",
    )

    evidence = parse_native_optimizer_evidence(project)

    assert evidence["optimizer_completed"] is True
    assert evidence["reported_improved_goal"] is True


def test_parse_optimizer_evidence_recognizes_goal_satisfied_abort(tmp_path):
    project = tmp_path / "antenna.cst"
    project.write_bytes(b"cst")
    result = project.with_suffix("") / "Result"
    result.mkdir(parents=True)
    (result / "Model_ui.opt").write_text(
        "Algorithm: Nelder Mead Simplex Algorithm\n"
        "Number of evaluations: 3\n"
        "              (solver: 2, reloaded: 1)\n\n"
        "Best parameters so far:\n\n patch_L = 10.4317\n\n"
        "(Corresponding run ID: 3)\n",
        encoding="utf-8",
    )
    (result / "Model.opt").write_text(
        "Optimization process aborted.\n"
        "Optimization was able to satisfy the defined goals. "
        "Best parameter set is stored.\n",
        encoding="utf-8",
    )

    evidence = parse_native_optimizer_evidence(project)

    assert evidence["optimizer_completed"] is True
    assert evidence["termination_status"] == "goal_satisfied"
    assert evidence["goal_satisfied"] is True
    assert evidence["best_parameters"] == {"patch_L": 10.4317}
    assert evidence["corresponding_run_id"] == 3
