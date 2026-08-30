"""Run three independent mesh levels for the tuned 5.8 GHz dipole on D:."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from benchmarks.cst_dipole_tuning_smoke import _summary
    from benchmarks.cst_solver_evidence import sha256_path
except ModuleNotFoundError:
    from cst_dipole_tuning_smoke import _summary
    from cst_solver_evidence import sha256_path
from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.controller import CSTController


def evaluate_convergence(
    cases: list[dict[str, Any]],
    *,
    resonance_tolerance_ghz: float = 0.02,
    target_s11_tolerance_db: float = 1.0,
) -> dict[str, Any]:
    if len(cases) < 2:
        raise ValueError("mesh convergence 至少需要两档")
    ordered = sorted(cases, key=lambda case: case["lines_per_wavelength"])
    medium, fine = ordered[-2:]
    resonance_delta = abs(float(fine["min_freq_ghz"]) - float(medium["min_freq_ghz"]))
    target_s11_delta = abs(float(fine["target_s11_db"]) - float(medium["target_s11_db"]))
    curve_hashes = [case.get("curve_sha256") for case in ordered if case.get("curve_sha256")]
    numerical_change_observed = len(set(curve_hashes)) > 1 if curve_hashes else None
    mesh_realization_verified = all(bool(case.get("mesh_signature")) for case in ordered)
    tolerance_met = resonance_delta <= resonance_tolerance_ghz and target_s11_delta <= target_s11_tolerance_db
    conclusive = mesh_realization_verified or numerical_change_observed is True
    return {
        "medium_lines_per_wavelength": medium["lines_per_wavelength"],
        "fine_lines_per_wavelength": fine["lines_per_wavelength"],
        "resonance_delta_ghz": resonance_delta,
        "target_s11_delta_db": target_s11_delta,
        "resonance_tolerance_ghz": resonance_tolerance_ghz,
        "target_s11_tolerance_db": target_s11_tolerance_db,
        "tolerance_met": tolerance_met,
        "numerical_change_observed": numerical_change_observed,
        "mesh_realization_verified": mesh_realization_verified,
        "conclusive": conclusive,
        "converged": conclusive and tolerance_met,
        "status": "converged" if conclusive and tolerance_met else ("not_converged" if conclusive else "inconclusive"),
        "inconclusive_reason": (
            "All serialized S11 curves are byte-identical and no realized mesh signature/cell count was read back."
            if not conclusive else ""
        ),
    }


def run_convergence(tuning_report: Path, output_root: Path) -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("mesh convergence 必须位于 D 盘")
    tuning_report = tuning_report.resolve()
    tuning = json.loads(tuning_report.read_text(encoding="utf-8"))
    if not tuning.get("success") or not tuning.get("physical_target_met"):
        raise ValueError("tuning source report 必须已执行成功并达到物理目标")
    if sha256_path(tuning_report) != "7f998bb5db3e8367bd0b55f582d0df5778fe454b8f0a148c6560129d9f6d76a4":
        raise ValueError("unexpected tuning source report SHA")
    target = float(tuning["target_contract"]["frequency_ghz"])
    arm_length = float(tuning["proposal"]["new_value_mm"])
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    previous = {key: os.environ.get(key) for key in ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")}
    previous_tempdir = tempfile.tempdir
    previous_config_temp = config.CST_TEMP_DIR
    previous_config_memory = config.AGENT_MEMORY_DIR
    os.environ.update({
        "TEMP": str(temp_dir), "TMP": str(temp_dir), "CST_TEMP_DIR": str(temp_dir),
        "AGENT_MEMORY_DIR": str(run_dir / "agent_memory"),
    })
    tempfile.tempdir = str(temp_dir)
    config.CST_TEMP_DIR = str(temp_dir)
    config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")
    cases: list[dict[str, Any]] = []

    try:
        for lines in (10, 15, 20):
            controller = CSTController()
            agent = CSTAgent(controller)
            agent.client = None
            stages = []

            def call(name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
                call_id = f"mesh-{lines}-{len(stages) + 1}-{name}"
                agent._active_tool_call_id = call_id
                bounded = json.loads(execute_tool(agent, name, arguments))
                full = dict(agent.session.artifacts.tool_results.get(call_id) or bounded)
                stages.append({"tool_name": name, "arguments": arguments, "success": bool(bounded.get("success"))})
                return bounded, full

            project_path = ""
            close_result = {"success": False, "message": "not closed"}
            summary: dict[str, Any] = {}
            try:
                build, _ = call("build_dipole_fast", {"f0_ghz": target, "wire_or_plate": "plate", "run_solver": False})
                call("store_parameter", {"name": "arm_length", "value": f"{arm_length:.6f}"})
                call("set_global_hexahedral_mesh", {"lines_per_wavelength": lines, "minimum_step_number": 5})
                call("run_solver", {})
                read, read_full = call("get_s_parameter", {"port_i": 1, "port_j": 1, "max_points": 5000})
                signature_result, signature_full = call("get_mesh_signature", {})
                summary = _summary(read_full, target) if read.get("success") else {}
                mesh_signature = signature_full.get("signature") if signature_result.get("success") else None
                mesh_signature_capability = {
                    "available": bool(signature_result.get("success")),
                    "error_type": signature_result.get("error_type"),
                    "message": signature_result.get("message", ""),
                }
                project_path = str(controller.project_path or "")
                if not build.get("success"):
                    raise RuntimeError("build failed")
            except Exception as exc:
                stages.append({"tool_name": "workflow_exception", "success": False, "message": f"{type(exc).__name__}: {exc}"})
            finally:
                if controller.project_path:
                    if not project_path:
                        project_path = str(controller.project_path)
                    close_result = controller.close_project(controller.project_path, timeout=60)
            curve_path = run_dir / f"mesh_{lines}_s11_curve.json"
            curve = list(summary.get("plot_data") or [])
            curve_path.write_text(json.dumps(curve, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            project = Path(project_path)
            required_stages = [stage for stage in stages if stage.get("tool_name") != "get_mesh_signature"]
            success = (
                all(stage.get("success") for stage in required_stages)
                and bool(summary.get("success")) and bool(curve)
                and project.is_file() and project.drive.upper() == "D:"
                and bool(close_result.get("success")) and controller.project_path == ""
            )
            cases.append({
                "lines_per_wavelength": lines,
                "minimum_step_number": 5,
                "mesh_signature": mesh_signature,
                "mesh_signature_capability": mesh_signature_capability,
                "success": success,
                "min_freq_ghz": summary.get("min_freq_ghz"),
                "min_s11_db": summary.get("min_s11_db"),
                "target_s11_db": summary.get("target_s11_db"),
                "s11_point_count": len(curve),
                "curve_file": str(curve_path),
                "curve_sha256": sha256_path(curve_path),
                "project_file": str(project),
                "project_sha256": sha256_path(project) if project.is_file() else "",
                "stages": stages,
                "close": close_result,
            })
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tempfile.tempdir = previous_tempdir
        config.CST_TEMP_DIR = previous_config_temp
        config.AGENT_MEMORY_DIR = previous_config_memory
    convergence = evaluate_convergence(cases) if all(case["success"] for case in cases) else {"converged": False}
    report = {
        "schema_version": "cst-dipole-mesh-convergence-v1",
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_tuning_report": str(tuning_report),
        "source_tuning_report_sha256": sha256_path(tuning_report),
        "target_frequency_ghz": target,
        "arm_length_mm": arm_length,
        "case_count": len(cases),
        "execution_success_count": sum(case["success"] for case in cases),
        "cases": cases,
        "convergence": convergence,
        "execution_success": all(case["success"] for case in cases),
        "convergence_claim_eligible": bool(convergence.get("conclusive")),
        "success": all(case["success"] for case in cases) and bool(convergence.get("converged")),
        "limitations": "History accepted three mesh settings, but convergence is inconclusive unless the realized mesh is independently observed or numerical output changes across levels.",
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tuning-report", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/dipole_mesh"))
    args = parser.parse_args()
    report, path = run_convergence(args.tuning_report, args.output_root)
    print(json.dumps({"success": report["success"], "convergence": report["convergence"], "report": str(path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
