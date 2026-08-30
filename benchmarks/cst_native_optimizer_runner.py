"""Run an opt-in CST native-optimizer experiment on a D-drive project clone."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from typing import Any, Mapping

from cst_agent_workbench import config
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.optimization.native_optimizer import (
    CstNativeOptimizerAdapter,
    GoalRangeType,
    NativeOptimizerAlgorithm,
    NativeOptimizerSpec,
    NativeParameterRange,
    SParameterGoal,
)
from cst_agent_workbench.results.reader import ResultsReader
from cst_agent_workbench.results.summary import summarize_s11_result

try:
    from benchmarks.cst_solver_evidence import (
        build_source_bindings,
        repository_commit,
        verify_source_bindings,
    )
except ModuleNotFoundError:
    from cst_solver_evidence import (
        build_source_bindings,
        repository_commit,
        verify_source_bindings,
    )


REQUIRED_GATES = (
    "RUN_LIVE_CST",
    "RUN_LIVE_CST_MUTATING",
    "RUN_LIVE_CST_SOLVER",
    "RUN_LIVE_CST_OPTIMIZER",
)

SOURCE_PATHS = {
    "runner": "benchmarks/cst_native_optimizer_runner.py",
    "evidence": "benchmarks/cst_solver_evidence.py",
    "adapter": "cst_agent_workbench/optimization/native_optimizer.py",
    "controller": "cst_agent_workbench/cst/controller.py",
    "results_reader": "cst_agent_workbench/results/reader.py",
    "results_summary": "cst_agent_workbench/results/summary.py",
}

OPTIMIZER_EVIDENCE_NAMES = ("Model.opt", "Model_ui.opt", "output.txt")
# CST 2025 Model_ui.opt renders best parameters to four decimal places.
OPTIMIZER_REPORTED_PARAMETER_ABS_TOL = 5e-5


def _assert_d_drive(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if resolved.drive.upper() != "D:":
        raise ValueError(f"{label} must be on D:; got {resolved}")
    return resolved


def validate_native_optimizer_request(
    *,
    source_project: Path,
    artifact_root: Path,
    environment: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    env = environment if environment is not None else os.environ
    missing = [name for name in REQUIRED_GATES if env.get(name) != "1"]
    if missing:
        raise PermissionError(
            "native optimizer requires explicit gates: " + ", ".join(missing)
        )
    source = _assert_d_drive(source_project, "source_project")
    artifacts = _assert_d_drive(artifact_root, "artifact_root")
    if source.suffix.lower() != ".cst" or not source.is_file():
        raise ValueError("source_project must be an existing .cst file on D:")
    return source, artifacts


def clone_cst_project(source_project: Path, run_root: Path) -> Path:
    """Copy both the CST container and its same-stem companion directory."""

    run_root.mkdir(parents=True, exist_ok=False)
    target_project = run_root / source_project.name
    shutil.copy2(source_project, target_project)
    source_companion = source_project.with_suffix("")
    if source_companion.is_dir():
        shutil.copytree(source_companion, run_root / source_companion.name)
    return target_project


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_path(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(Path(path).resolve())))


def _tree_digest(root: Path, *, exclude_optimizer_evidence: bool = False) -> dict[str, Any]:
    """Bind a companion/result tree by relative path and file bytes."""

    files: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if exclude_optimizer_evidence and path.name in OPTIMIZER_EVIDENCE_NAMES:
                continue
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    encoded = json.dumps(files, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {
        "root": str(root),
        "file_count": len(files),
        "manifest_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def clear_stale_optimizer_evidence(project_path: Path) -> dict[str, Any]:
    """Remove optimizer summaries only from the disposable D-drive clone."""

    result_root = project_path.with_suffix("") / "Result"
    removed: dict[str, Any] = {}
    for name in OPTIMIZER_EVIDENCE_NAMES:
        path = result_root / name
        removed[name] = {
            "existed": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
        }
        if path.is_file():
            path.unlink()
    return removed


def validate_baseline_report(
    report_path: Path,
    *,
    source_project: Path,
    repo_root: Path,
) -> dict[str, Any]:
    path = _assert_d_drive(report_path, "baseline_report")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "cst-dipole-solver-smoke-v2":
        raise ValueError("baseline_report must use cst-dipole-solver-smoke-v2")
    if not payload.get("success"):
        raise ValueError("baseline_report is not successful")
    inputs = payload.get("input") or {}
    if inputs.get("wire_or_plate") != "plate" or not math.isclose(
        float(inputs.get("f0_ghz", math.nan)), 5.8, rel_tol=0.0, abs_tol=1e-9
    ):
        raise ValueError("baseline_report must be the 5.8 GHz plate dipole case")
    if _normalized_path(payload.get("project_file", "")) != _normalized_path(source_project):
        raise ValueError("baseline_report project_file does not match source_project")
    if _sha256(source_project) != payload.get("project_sha256"):
        raise ValueError("baseline_report project SHA does not match source_project")
    verify_source_bindings(
        repo_root,
        commit=str(payload.get("repository_commit") or ""),
        bindings=payload.get("source_bindings") or {},
    )
    curve_path = Path(str(payload.get("s11_curve_file") or ""))
    if not curve_path.is_file() or _sha256(curve_path) != payload.get("s11_curve_sha256"):
        raise ValueError("baseline_report S11 curve binding is invalid")
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "project_sha256": payload["project_sha256"],
        "s11_curve_sha256": payload["s11_curve_sha256"],
        "repository_commit": payload["repository_commit"],
    }


def _read_s11(project_path: Path, target_freq_ghz: float) -> dict[str, Any]:
    reader = ResultsReader()
    opened = reader.open(str(project_path))
    if not opened.get("success"):
        return {"success": False, "open": opened}
    raw = reader.get_s_parameter(1, 1)
    summary = summarize_s11_result(raw, target_freq_ghz)
    curve = list(summary.plot_data or [])
    return {
        "success": summary.success,
        "open": opened,
        "raw_success": bool(raw.get("success")),
        "points": len(curve),
        "curve_sha256": hashlib.sha256(
            json.dumps(curve, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "min_s11_db": summary.min_s11_db,
        "min_freq_ghz": summary.min_freq_ghz,
        "target_freq_ghz": summary.target_freq_ghz,
        "target_s11_db": summary.target_s11_db,
        "bandwidth_ghz": summary.bandwidth_ghz,
        "bandwidth_pct": summary.bandwidth_pct,
        "primary_band": summary.primary_band,
        "resonances": summary.resonances,
        "message": raw.get("message", ""),
    }


def _read_parameter_value(project_path: Path, parameter_name: str) -> float:
    parameters_path = project_path.with_suffix("") / "Model" / "Parameters.json"
    payload = json.loads(parameters_path.read_text(encoding="utf-8"))
    for item in payload.get("parameters", []):
        if item.get("name") == parameter_name:
            return float(item.get("value"))
    raise ValueError(f"parameter {parameter_name!r} not found in {parameters_path}")


def _read_text_if_present(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_native_optimizer_evidence(project_path: Path) -> dict[str, Any]:
    """Parse CST's saved optimizer summary instead of trusting VBA return status."""

    result_root = project_path.with_suffix("") / "Result"
    model_path = result_root / "Model.opt"
    ui_path = result_root / "Model_ui.opt"
    output_path = result_root / "output.txt"
    model_text = _read_text_if_present(model_path)
    ui_text = _read_text_if_present(ui_path)
    output_text = _read_text_if_present(output_path)
    combined = "\n".join((model_text, ui_text, output_text))

    def _integer(pattern: str, text: str) -> int | None:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        return int(match.group(1)) if match else None

    evaluation_count = _integer(r"Number of evaluations:\s*(\d+)", ui_text)
    solver_evaluations = _integer(r"\(solver:\s*(\d+)", ui_text)
    reloaded_evaluations = _integer(r"reloaded:\s*(\d+)\)", ui_text)
    if evaluation_count is None:
        evaluation_count = _integer(r"\((\d+)\s+evaluations\)", model_text)

    algorithm_match = re.search(r"Algorithm:\s*(.+)", ui_text)
    if not algorithm_match:
        algorithm_match = re.search(r"Optimizer type:\s*(.+)", model_text)
    algorithm = algorithm_match.group(1).strip() if algorithm_match else ""

    aborted = bool(re.search(r"Optimization aborted", combined, re.IGNORECASE))
    goal_satisfied = bool(
        re.search(
            r"Optimization was able to satisfy the defined goals",
            combined,
            re.IGNORECASE,
        )
    )
    terminal_state_observed = bool(
        re.search(
            r"Optimization(?:\s+process)?\s+(?:was able to satisfy|aborted|completed|finished|stopped)",
            combined,
            re.IGNORECASE,
        )
    )
    improved = bool(
        re.search(r"improved goal value|improve(?:d)? the goal value", combined, re.IGNORECASE)
        and not re.search(r"without improved goal value", combined, re.IGNORECASE)
    )
    termination_lines = [
        line.strip()
        for line in combined.splitlines()
        if re.search(
            r"(?:exceed the maximal number|algorithm will be aborted|Optimization"
            r"(?:\s+process)?\s+(?:was able to satisfy|aborted|completed|finished|stopped))",
            line,
            re.IGNORECASE,
        )
    ]
    best_parameters: dict[str, float] = {}
    best_block = re.search(
        r"Best parameters so far:\s*(.*?)(?:\n\s*\(Corresponding run ID:|\Z)",
        ui_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if best_block:
        for name, value in re.findall(
            r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
            r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$",
            best_block.group(1),
            flags=re.MULTILINE,
        ):
            best_parameters[name] = float(value)
    artifacts = {}
    for name, path in {
        "model": model_path,
        "summary": ui_path,
        "output": output_path,
    }.items():
        artifacts[name] = {
            "path": str(path),
            "exists": path.is_file(),
            "sha256": _sha256(path) if path.is_file() else None,
            "size_bytes": path.stat().st_size if path.is_file() else None,
            "mtime_ns": path.stat().st_mtime_ns if path.is_file() else None,
        }
    return {
        "artifacts": artifacts,
        "optimizer_started": bool(ui_text and algorithm),
        "optimizer_evaluation_count": evaluation_count,
        "solver_evaluation_count": solver_evaluations,
        "reloaded_evaluation_count": reloaded_evaluations,
        "optimizer_completed": terminal_state_observed,
        "termination_status": (
            "goal_satisfied"
            if goal_satisfied
            else "aborted"
            if aborted
            else "completed"
            if terminal_state_observed
            else "unknown"
        ),
        "termination_reason": " ".join(dict.fromkeys(termination_lines)),
        "reported_improved_goal": improved,
        "goal_satisfied": goal_satisfied,
        "best_parameters": best_parameters,
        "corresponding_run_id": (
            int(match.group(1))
            if (match := re.search(r"Corresponding run ID:\s*(\d+)", ui_text, re.IGNORECASE))
            else None
        ),
        "algorithm": algorithm,
    }


def run_native_optimizer_experiment(
    *,
    source_project: Path,
    artifact_root: Path,
    parameter_name: str,
    minimum: float,
    maximum: float,
    additional_parameters: tuple[tuple[str, float, float], ...] = (),
    target_freq_ghz: float,
    target_db: float,
    optimizer_goal_db: float | None = None,
    algorithm: NativeOptimizerAlgorithm = NativeOptimizerAlgorithm.NELDER_MEAD,
    max_evaluations: int = 4,
    timeout_seconds: int = 1800,
    baseline_report: Path | None = None,
    resonance_tolerance_ghz: float = 0.05,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    source, artifacts = validate_native_optimizer_request(
        source_project=source_project,
        artifact_root=artifact_root,
        environment=environment,
    )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if resonance_tolerance_ghz <= 0:
        raise ValueError("resonance_tolerance_ghz must be positive")
    goal_db = target_db if optimizer_goal_db is None else optimizer_goal_db
    if not math.isfinite(goal_db):
        raise ValueError("optimizer_goal_db must be finite")

    run_id = uuid.uuid4().hex[:12]
    run_root = artifacts / run_id
    project_copy = clone_cst_project(source, run_root)
    temp_root = run_root / "tmp"
    temp_root.mkdir()
    repo_root = Path(__file__).resolve().parents[1]
    baseline_binding = (
        validate_baseline_report(
            baseline_report,
            source_project=source,
            repo_root=repo_root,
        )
        if baseline_report is not None
        else None
    )
    source_sha_at_clone = _sha256(source)
    companion_before = _tree_digest(project_copy.with_suffix(""))
    stale_optimizer_evidence = clear_stale_optimizer_evidence(project_copy)
    initial = _read_parameter_value(project_copy, parameter_name)
    parameter_ranges = [
        NativeParameterRange(
            name=parameter_name,
            initial=initial,
            minimum=minimum,
            maximum=maximum,
        )
    ]
    for name, lower, upper in additional_parameters:
        parameter_ranges.append(
            NativeParameterRange(
                name=name,
                initial=_read_parameter_value(project_copy, name),
                minimum=lower,
                maximum=upper,
            )
        )
    spec = NativeOptimizerSpec(
        parameters=tuple(parameter_ranges),
        goals=(
            SParameterGoal(
                target_db=goal_db,
                range_type=GoalRangeType.SINGLE,
                minimum_ghz=target_freq_ghz,
                maximum_ghz=target_freq_ghz,
            ),
        ),
        algorithm=algorithm,
        max_evaluations=max_evaluations,
        always_start_from_current=False,
    )
    before = _read_s11(project_copy, target_freq_ghz)
    baseline_readback_matches = bool(
        baseline_binding is None
        or before.get("curve_sha256") == baseline_binding["s11_curve_sha256"]
    )
    previous_environment = {
        name: os.environ.get(name)
        for name in ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")
    }
    previous_tempdir = tempfile.tempdir
    original_default_project = config.CST_DEFAULT_PROJECT
    original_config_temp = config.CST_TEMP_DIR
    original_config_memory = config.AGENT_MEMORY_DIR
    started = time.perf_counter()
    started_at_utc = datetime.now(timezone.utc).isoformat()
    started_wall_ns = time.time_ns()
    controller_result: dict[str, Any] = {}
    optimizer_result: dict[str, Any] = {}
    close_result: dict[str, Any] = {"success": False, "message": "controller not created"}
    run_exception = ""
    connected_project_matches = False
    controller: CSTController | None = None
    try:
        os.environ.update(
            {
                "TEMP": str(temp_root),
                "TMP": str(temp_root),
                "CST_TEMP_DIR": str(temp_root),
                "AGENT_MEMORY_DIR": str(run_root / "agent_memory"),
            }
        )
        tempfile.tempdir = str(temp_root)
        config.CST_TEMP_DIR = str(temp_root)
        config.AGENT_MEMORY_DIR = str(run_root / "agent_memory")
        config.CST_DEFAULT_PROJECT = str(project_copy)
        controller = CSTController()
        controller_result = controller.connect()
        connected_project_matches = bool(
            controller_result.get("success")
            and controller.project_path
            and _normalized_path(controller.project_path) == _normalized_path(project_copy)
        )
        if connected_project_matches:
            optimizer_result = CstNativeOptimizerAdapter(controller).start(
                spec,
                timeout=timeout_seconds,
            )
        else:
            optimizer_result = {
                "success": False,
                "message": "CST connection failed or opened a project other than the pinned clone",
            }
    except Exception as exc:
        run_exception = f"{type(exc).__name__}: {exc}"
        optimizer_result = {
            **optimizer_result,
            "success": False,
            "message": run_exception,
        }
    finally:
        try:
            if controller is not None and connected_project_matches:
                try:
                    close_result = controller.close_project(str(project_copy), timeout=60)
                except Exception as exc:
                    close_result = {
                        "success": False,
                        "message": f"{type(exc).__name__}: {exc}",
                    }
        finally:
            config.CST_DEFAULT_PROJECT = original_default_project
            config.CST_TEMP_DIR = original_config_temp
            config.AGENT_MEMORY_DIR = original_config_memory
            tempfile.tempdir = previous_tempdir
            for name, old_value in previous_environment.items():
                if old_value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = old_value

    after = _read_s11(project_copy, target_freq_ghz)
    try:
        persisted_parameters = {
            parameter.name: _read_parameter_value(project_copy, parameter.name)
            for parameter in spec.parameters
        }
    except Exception:
        persisted_parameters = {}
    optimizer_evidence = parse_native_optimizer_evidence(project_copy)
    optimizer_best_parameters = optimizer_evidence.get("best_parameters", {})
    optimizer_best_parameter = optimizer_best_parameters.get(parameter_name)
    best_parameters_applied = bool(
        optimizer_best_parameters
        and all(
            name in persisted_parameters
            and math.isclose(
                float(value),
                float(persisted_parameters[name]),
                rel_tol=0.0,
                abs_tol=OPTIMIZER_REPORTED_PARAMETER_ABS_TOL,
            )
            for name, value in optimizer_best_parameters.items()
        )
    )
    s11_design_point_parameters = dict(persisted_parameters) if best_parameters_applied else None
    persisted_final_parameter = persisted_parameters.get(parameter_name)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    s11_target_met = bool(
        after.get("target_s11_db") is not None
        and float(after["target_s11_db"]) <= target_db
    )
    resonance_error_ghz = (
        abs(float(after["min_freq_ghz"]) - target_freq_ghz)
        if after.get("min_freq_ghz") is not None
        else None
    )
    joint_target_met = bool(
        s11_target_met
        and resonance_error_ghz is not None
        and resonance_error_ghz <= resonance_tolerance_ghz
    )
    physical_improvement_db = None
    if before.get("target_s11_db") is not None and after.get("target_s11_db") is not None:
        physical_improvement_db = float(before["target_s11_db"]) - float(
            after["target_s11_db"]
        )
    interface_invocation_success = bool(
        controller_result.get("success") and optimizer_result.get("success")
    )
    result_readback_success = bool(after.get("success"))
    parameter_changed = bool(
        persisted_final_parameter is not None
        and not math.isclose(
            initial, float(persisted_final_parameter), rel_tol=0.0, abs_tol=1e-12
        )
    )
    physical_improvement = bool(
        physical_improvement_db is not None and physical_improvement_db > 1e-9
    )
    solver_evaluation_count = optimizer_evidence.get("solver_evaluation_count")
    optimizer_evaluation_count = optimizer_evidence.get("optimizer_evaluation_count")
    reloaded_evaluation_count = optimizer_evidence.get("reloaded_evaluation_count")
    evaluation_counts_consistent = bool(
        isinstance(optimizer_evaluation_count, int)
        and isinstance(solver_evaluation_count, int)
        and isinstance(reloaded_evaluation_count, int)
        and optimizer_evaluation_count == solver_evaluation_count + reloaded_evaluation_count
    )
    within_declared_budget = bool(
        isinstance(optimizer_evaluation_count, int)
        and 1 <= optimizer_evaluation_count <= spec.max_evaluations
    )
    evidence_fresh = bool(
        optimizer_evidence["optimizer_started"]
        and all(
            not artifact["exists"] or int(artifact["mtime_ns"]) >= started_wall_ns - 5_000_000_000
            for artifact in optimizer_evidence["artifacts"].values()
        )
        and any(artifact["exists"] for artifact in optimizer_evidence["artifacts"].values())
    )
    cleanup_success = bool(
        close_result.get("success")
        and controller is not None
        and controller.project_path == ""
    )
    parameter_result_binding_success = bool(best_parameters_applied and s11_design_point_parameters)
    execution_success = bool(
        interface_invocation_success
        and connected_project_matches
        and evidence_fresh
        and isinstance(solver_evaluation_count, int) and solver_evaluation_count >= 1
        and evaluation_counts_consistent
        and within_declared_budget
        and optimizer_evidence["optimizer_completed"]
        and result_readback_success
        and baseline_readback_matches
        and parameter_result_binding_success
        and cleanup_success
    )
    physical_target_met = bool(execution_success and joint_target_met)
    source_bindings = build_source_bindings(
        repo_root,
        SOURCE_PATHS,
        snapshot_root=run_root / "source_snapshot",
    )
    report = {
        "schema_version": "cst-native-optimizer-run-v3",
        "run": {
            "run_id": run_id,
            "started_at_utc": started_at_utc,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "temp_root": str(temp_root),
            "all_configured_temp_paths_on_d": all(
                Path(value).resolve().drive.upper() == "D:"
                for value in (temp_root, run_root / "agent_memory")
            ),
            "source_project": str(source),
            "source_sha256_at_clone": source_sha_at_clone,
            "project_copy": str(project_copy),
            "project_copy_sha256": _sha256(project_copy),
            "companion_manifest_before": companion_before,
            "companion_manifest_after_without_optimizer_text": _tree_digest(
                project_copy.with_suffix(""), exclude_optimizer_evidence=True
            ),
            "stale_optimizer_evidence_removed_from_clone": stale_optimizer_evidence,
            "baseline_report": baseline_binding,
            "baseline_readback_matches_report_curve": baseline_readback_matches,
        },
        "spec": {
            "algorithm": spec.algorithm.value,
            "max_evaluations": spec.max_evaluations,
            "parameters": [asdict(item) for item in spec.parameters],
            "goals": [
                {
                    **asdict(item),
                    "range_type": item.range_type.value,
                }
                for item in spec.goals
            ],
        },
        "controller": controller_result,
        "connected_project_matches_clone": connected_project_matches,
        "optimizer": optimizer_result,
        "run_exception": run_exception,
        "project_close": close_result,
        "optimizer_evidence": optimizer_evidence,
        "before": before,
        "after": after,
        "comparison": {
            "initial_parameter": initial,
            "persisted_parameter_after": persisted_final_parameter,
            "persisted_parameters_after": persisted_parameters,
            "optimizer_best_parameter": optimizer_best_parameter,
            "optimizer_best_parameters": optimizer_best_parameters,
            "best_parameters_applied": best_parameters_applied,
            "s11_design_point_parameters": s11_design_point_parameters,
            "physical_improvement_db": physical_improvement_db,
            "optimizer_goal_db": goal_db,
            "acceptance_s11_threshold_db": target_db,
            "s11_target_met": s11_target_met,
            "resonance_error_ghz": resonance_error_ghz,
            "resonance_tolerance_ghz": resonance_tolerance_ghz,
            "joint_target_met": joint_target_met,
            "target_met": joint_target_met,
            "solver_result_readback_success": result_readback_success,
        },
        "status": {
            "interface_invocation_success": interface_invocation_success,
            "optimizer_started": optimizer_evidence["optimizer_started"],
            "optimizer_evaluation_count": optimizer_evidence[
                "optimizer_evaluation_count"
            ],
            "solver_evaluation_count": solver_evaluation_count,
            "reloaded_evaluation_count": reloaded_evaluation_count,
            "evaluation_counts_consistent": evaluation_counts_consistent,
            "within_declared_evaluation_budget": within_declared_budget,
            "optimizer_evidence_fresh": evidence_fresh,
            "optimizer_completed": optimizer_evidence["optimizer_completed"],
            "result_readback_success": result_readback_success,
            "baseline_readback_matches_report_curve": baseline_readback_matches,
            "parameter_result_binding_success": parameter_result_binding_success,
            "cleanup_success": cleanup_success,
            "parameter_changed": parameter_changed,
            "physical_improvement": physical_improvement,
            "joint_target_met": joint_target_met,
        },
        "repository_commit": repository_commit(repo_root),
        "source_bindings": source_bindings,
        "execution_success": execution_success,
        "physical_target_met": physical_target_met,
        "success": execution_success,
        "honest_boundary": [
            "execution success requires fresh CST optimizer evidence, at least one solver evaluation, typed S11 readback, parameter/result binding, and clean project close",
            "physical target is independently recomputed as resonance error plus S11 at the frozen target frequency",
            "optimizer_goal_db is a CST search surrogate and may be stricter than the final S11 acceptance threshold",
            "CST Optimizer has no documented typed best/status/evaluation getter; completion and best-point evidence are parsed from version-specific saved artifacts",
            "physical_improvement and parameter_changed are reported separately from execution success",
            "one run does not establish optimizer superiority or convergence reliability",
        ],
    }
    (run_root / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", type=Path, required=True)
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/native_optimizer/live_runs"),
    )
    parser.add_argument("--parameter", default="patch_L")
    parser.add_argument("--minimum", type=float, required=True)
    parser.add_argument("--maximum", type=float, required=True)
    parser.add_argument("--target-freq-ghz", type=float, required=True)
    parser.add_argument("--target-db", type=float, default=-10.0)
    parser.add_argument("--optimizer-goal-db", type=float)
    parser.add_argument(
        "--algorithm",
        choices=[item.value for item in NativeOptimizerAlgorithm],
        default=NativeOptimizerAlgorithm.NELDER_MEAD.value,
    )
    parser.add_argument("--max-evaluations", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--baseline-report", type=Path)
    parser.add_argument("--resonance-tolerance-ghz", type=float, default=0.05)
    args = parser.parse_args()

    report = run_native_optimizer_experiment(
        source_project=args.source_project,
        artifact_root=args.artifact_root,
        parameter_name=args.parameter,
        minimum=args.minimum,
        maximum=args.maximum,
        target_freq_ghz=args.target_freq_ghz,
        target_db=args.target_db,
        optimizer_goal_db=args.optimizer_goal_db,
        algorithm=NativeOptimizerAlgorithm(args.algorithm),
        max_evaluations=args.max_evaluations,
        timeout_seconds=args.timeout_seconds,
        baseline_report=args.baseline_report,
        resonance_tolerance_ghz=args.resonance_tolerance_ghz,
    )
    print(
        json.dumps(
            {
                "success": report["success"],
                "target_met": report["physical_target_met"],
                "before_s11_db": report["before"].get("target_s11_db"),
                "after_s11_db": report["after"].get("target_s11_db"),
                "improvement_db": report["comparison"]["physical_improvement_db"],
                "project_copy": report["run"]["project_copy"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
