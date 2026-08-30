"""Run a physics-guided dipole tuning turn through canonical CST tools on D:."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst.dipole_fast import DipoleRequest, synthesize_dipole
from cst_agent_workbench.optimization.dipole_tuning import (
    candidate_improves,
    nearest_resonance,
    propose_arm_length,
    resonance_error_ghz,
)
from cst_agent_workbench.results.summary import summarize_s11_result

try:
    from benchmarks.cst_solver_evidence import build_source_bindings, repository_commit, sha256_path
except ModuleNotFoundError:
    from cst_solver_evidence import build_source_bindings, repository_commit, sha256_path


SOURCE_PATHS = {
    "runner": "benchmarks/cst_dipole_tuning_smoke.py",
    "evidence": "benchmarks/cst_solver_evidence.py",
    "tools": "cst_agent_workbench/agent/tools.py",
    "tool_runtime": "cst_agent_workbench/agent/tool_runtime.py",
    "fast_path": "cst_agent_workbench/agent/fast_path.py",
    "dipole": "cst_agent_workbench/cst/dipole_fast.py",
    "dipole_tuning": "cst_agent_workbench/optimization/dipole_tuning.py",
    "solver_safety": "cst_agent_workbench/cst/solver_safety.py",
    "primitives": "cst_agent_workbench/cst/primitives.py",
    "controller": "cst_agent_workbench/cst/controller.py",
    "results_summary": "cst_agent_workbench/results/summary.py",
}


def _summary(raw: dict[str, Any], target: float) -> dict[str, Any]:
    return summarize_s11_result(raw, target).to_dict()


def _curve_artifact(run_dir: Path, name: str, summary: dict[str, Any]) -> dict[str, Any]:
    path = run_dir / f"{name}_s11_curve.json"
    curve = list(summary.get("plot_data") or [])
    path.write_text(json.dumps(curve, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"path": str(path), "sha256": sha256_path(path), "point_count": len(curve)}


def run_tuning(
    output_root: Path,
    *,
    target_frequency_ghz: float = 5.8,
    target_db: float = -10.0,
    resonance_tolerance_ghz: float = 0.05,
) -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST dipole tuning 必须位于 D 盘")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    previous = {key: os.environ.get(key) for key in ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")}
    previous_tempdir = tempfile.tempdir
    previous_config_temp = config.CST_TEMP_DIR
    previous_config_memory = config.AGENT_MEMORY_DIR
    controller: CSTController | None = None
    started = time.perf_counter()
    stages: list[dict[str, Any]] = []
    before: dict[str, Any] = {}
    attempted_after: dict[str, Any] = {}
    final: dict[str, Any] = {}
    proposal: dict[str, Any] = {}
    rolled_back = False
    rollback_verified = False
    project_path = ""
    close_result: dict[str, Any] = {"success": False, "message": "controller not created"}

    def call(agent: CSTAgent, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        call_id = f"tuning-{len(stages) + 1}-{name}"
        agent._active_tool_call_id = call_id
        bounded = json.loads(execute_tool(agent, name, arguments))
        full = dict(agent.session.artifacts.tool_results.get(call_id) or bounded)
        stages.append({"tool_name": name, "arguments": arguments, "bounded": bounded, "success": bool(bounded.get("success"))})
        return bounded, full

    try:
        os.environ.update({
            "TEMP": str(temp_dir), "TMP": str(temp_dir), "CST_TEMP_DIR": str(temp_dir),
            "AGENT_MEMORY_DIR": str(run_dir / "agent_memory"),
        })
        tempfile.tempdir = str(temp_dir)
        config.CST_TEMP_DIR = str(temp_dir)
        config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")
        controller = CSTController()
        agent = CSTAgent(controller)
        agent.client = None

        build, build_full = call(agent, "build_dipole_fast", {
            "f0_ghz": target_frequency_ghz, "wire_or_plate": "plate", "run_solver": True,
        })
        before = dict(build_full.get("s11") or {})
        project_path = str(controller.project_path or "")
        if not build.get("success") or not before.get("success") or not before.get("plot_data"):
            raise RuntimeError("baseline canonical dipole build/solver/S11 failed")
        old_length = synthesize_dipole(DipoleRequest(target_frequency_ghz))["arm_length"]
        measured = nearest_resonance(before, target_frequency_ghz)
        if measured is None:
            raise RuntimeError("baseline has no comparable resonance")
        proposal_obj = propose_arm_length(
            old_length_mm=old_length,
            measured_resonance_ghz=measured,
            target_frequency_ghz=target_frequency_ghz,
        )
        proposal = proposal_obj.to_dict()

        update, _ = call(agent, "store_parameter", {
            "name": proposal_obj.parameter,
            "value": f"{proposal_obj.new_value_mm:.6f}",
        })
        solve, _ = call(agent, "run_solver", {})
        read, read_full = call(agent, "get_s_parameter", {"port_i": 1, "port_j": 1, "max_points": 5000})
        attempted_after = _summary(read_full, target_frequency_ghz) if read.get("success") else {}
        improved = (
            update.get("success") and solve.get("success") and read.get("success")
            and attempted_after.get("success") and candidate_improves(
                before, attempted_after, target_frequency_ghz=target_frequency_ghz,
            )
        )
        if improved:
            final = attempted_after
        else:
            rolled_back = True
            restore, _ = call(agent, "store_parameter", {
                "name": proposal_obj.parameter,
                "value": f"{proposal_obj.old_value_mm:.6f}",
            })
            rollback_solve, _ = call(agent, "run_solver", {})
            rollback_read, rollback_full = call(
                agent, "get_s_parameter", {"port_i": 1, "port_j": 1, "max_points": 5000},
            )
            final = _summary(rollback_full, target_frequency_ghz) if rollback_read.get("success") else {}
            rollback_verified = (
                restore.get("success") and rollback_solve.get("success") and rollback_read.get("success")
                and final.get("success")
                and resonance_error_ghz(final, target_frequency_ghz) is not None
            )
    except Exception as exc:
        stages.append({"tool_name": "workflow_exception", "success": False, "message": f"{type(exc).__name__}: {exc}"})
    finally:
        if controller is not None and controller.project_path:
            if not project_path:
                project_path = str(controller.project_path)
            try:
                close_result = controller.close_project(controller.project_path, timeout=60)
            except Exception as exc:
                close_result = {"success": False, "message": f"{type(exc).__name__}: {exc}"}
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tempfile.tempdir = previous_tempdir
        config.CST_TEMP_DIR = previous_config_temp
        config.AGENT_MEMORY_DIR = previous_config_memory

    before_artifact = _curve_artifact(run_dir, "before", before)
    attempted_artifact = _curve_artifact(run_dir, "attempted_after", attempted_after)
    final_artifact = _curve_artifact(run_dir, "final", final)
    before_error = resonance_error_ghz(before, target_frequency_ghz)
    after_error = resonance_error_ghz(attempted_after, target_frequency_ghz)
    target_s11 = attempted_after.get("target_s11_db")
    physical_target_met = (
        not rolled_back and after_error is not None and after_error <= resonance_tolerance_ghz
        and target_s11 is not None and float(target_s11) <= target_db
    )
    project = Path(project_path)
    workflow_success = (
        bool(stages) and all(stage.get("success") for stage in stages)
        and bool(before.get("success")) and bool(final.get("success"))
        and (not rolled_back or rollback_verified)
        and project.is_file() and project.drive.upper() == "D:"
        and bool(close_result.get("success"))
        and (controller.project_path if controller is not None else "") == ""
    )
    repo_root = Path(__file__).resolve().parents[1]
    source_bindings = build_source_bindings(repo_root, SOURCE_PATHS, snapshot_root=run_dir / "source_snapshot")
    report = {
        "schema_version": "cst-dipole-tuning-smoke-v1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow": "physics diagnosis/proposal -> canonical store_parameter -> canonical run_solver -> canonical get_s_parameter -> conditional verified rollback",
        "target_contract": {
            "frequency_ghz": target_frequency_ghz,
            "resonance_tolerance_ghz": resonance_tolerance_ghz,
            "s11_at_target_threshold_db": target_db,
        },
        "proposal": proposal,
        "before": {key: before.get(key) for key in ("min_s11_db", "min_freq_ghz", "target_s11_db", "target_freq_ghz", "resonances")},
        "attempted_after": {key: attempted_after.get(key) for key in ("min_s11_db", "min_freq_ghz", "target_s11_db", "target_freq_ghz", "resonances")},
        "final": {key: final.get(key) for key in ("min_s11_db", "min_freq_ghz", "target_s11_db", "target_freq_ghz", "resonances")},
        "before_resonance_error_ghz": before_error,
        "attempted_after_resonance_error_ghz": after_error,
        "resonance_error_improved": before_error is not None and after_error is not None and after_error < before_error,
        "rolled_back": rolled_back,
        "rollback_verified": rollback_verified,
        "stages": stages,
        "artifacts": {"before_curve": before_artifact, "attempted_after_curve": attempted_artifact, "final_curve": final_artifact},
        "project_file": str(project),
        "project_sha256": sha256_path(project) if project.is_file() else "",
        "close": close_result,
        "controller_final_project_path": controller.project_path if controller is not None else "",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "repository_commit": repository_commit(repo_root),
        "source_bindings": source_bindings,
        "execution_success": workflow_success,
        "physical_target_met": workflow_success and physical_target_met,
        "success": workflow_success and physical_target_met,
        "verification_boundary": "The agent proposes one interpretable arm-length change; CST remains the source of physical truth. This is one developer-selected case, not evidence that the formula or agent generalizes.",
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/dipole_tuning"))
    args = parser.parse_args()
    report, path = run_tuning(args.output_root)
    print(json.dumps({
        "success": report["success"], "execution_success": report["execution_success"],
        "physical_target_met": report["physical_target_met"], "report": str(path),
    }, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
