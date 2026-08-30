from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from benchmarks.cst_batch_solver_runner import (
    build_batch_command,
    run_cst_batch,
    validate_batch_request,
)


GATES = {
    "RUN_LIVE_CST": "1",
    "RUN_LIVE_CST_SOLVER": "1",
    "RUN_LIVE_CST_BATCH": "1",
}


def _fixtures(tmp_path: Path) -> tuple[Path, Path, Path]:
    executable = tmp_path / "CST DESIGN ENVIRONMENT.exe"
    executable.write_bytes(b"test executable fixture")
    project = tmp_path / "solver_copy.cst"
    project.write_bytes(b"test project fixture")
    return executable, project, tmp_path / "artifacts"


def test_batch_request_is_fail_closed_and_requires_d_drive_copy(tmp_path):
    executable, project, artifacts = _fixtures(tmp_path)
    with pytest.raises(PermissionError, match="RUN_LIVE_CST"):
        validate_batch_request(
            executable=executable,
            project_copy=project,
            artifact_root=artifacts,
            mode="active_solver",
            environment={},
        )

    incomplete_gates = {"RUN_LIVE_CST": "1", "RUN_LIVE_CST_SOLVER": "1"}
    with pytest.raises(PermissionError, match="RUN_LIVE_CST_BATCH"):
        validate_batch_request(
            executable=executable,
            project_copy=project,
            artifact_root=artifacts,
            mode="active_solver",
            environment=incomplete_gates,
        )

    validate_batch_request(
        executable=executable,
        project_copy=project,
        artifact_root=artifacts,
        mode="active_solver",
        environment=GATES,
    )


def test_batch_command_uses_documented_mode_flags(tmp_path):
    executable, project, _ = _fixtures(tmp_path)
    assert build_batch_command(
        executable=executable,
        project_copy=project,
        mode="active_solver",
    ) == [str(executable), "-as", str(project)]
    assert build_batch_command(
        executable=executable,
        project_copy=project,
        mode="time_domain",
    ) == [str(executable), "-m", "-r", str(project)]


def test_batch_runner_redirects_temp_to_d_and_records_process_evidence(tmp_path):
    executable, project, artifacts = _fixtures(tmp_path)
    observed: dict[str, object] = {}

    def fake_process(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="Start solver <Solver_HF_TD>\nSolver finished successfully",
            stderr="",
        )

    report = run_cst_batch(
        executable=executable,
        project_copy=project,
        artifact_root=artifacts,
        mode="active_solver",
        timeout_seconds=60,
        environment=GATES,
        process_runner=fake_process,
    )

    assert report["success"] is True
    assert report["process"]["exit_status"] == "success"
    assert report["process"]["solver_started"] is True
    assert report["process"]["solver_finished"] is True
    assert report["project_before"]["path"].startswith("D:\\")
    assert report["project_after"]["path"] == report["project_before"]["path"]
    assert observed["command"][-2:] == ["-as", str(project.resolve())]
    process_env = observed["env"]
    assert isinstance(process_env, dict)
    assert str(process_env["TEMP"]).startswith("D:\\")
    assert process_env["TMP"] == process_env["TEMP"]


def test_batch_runner_maps_official_license_exit_code(tmp_path):
    executable, project, artifacts = _fixtures(tmp_path)

    def no_license(command, **kwargs):
        return subprocess.CompletedProcess(command, 3, stdout="", stderr="license unavailable")

    report = run_cst_batch(
        executable=executable,
        project_copy=project,
        artifact_root=artifacts,
        environment=GATES,
        process_runner=no_license,
    )

    assert report["success"] is False
    assert report["process"]["return_code"] == 3
    assert report["process"]["exit_status"] == "no_license"


def test_zero_exit_without_solver_markers_is_not_reported_as_solver_success(tmp_path):
    executable, project, artifacts = _fixtures(tmp_path)

    def no_solver(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="Batch process exited", stderr="")

    report = run_cst_batch(
        executable=executable,
        project_copy=project,
        artifact_root=artifacts,
        environment=GATES,
        process_runner=no_solver,
    )

    assert report["process"]["return_code"] == 0
    assert report["process"]["solver_started"] is False
    assert report["process"]["solver_finished"] is False
    assert report["success"] is False
