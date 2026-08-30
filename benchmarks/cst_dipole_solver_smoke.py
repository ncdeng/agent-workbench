"""Run a canonical dipole build, real solver, and typed S11 readback on D:."""

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
try:
    from benchmarks.cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )
except ModuleNotFoundError:  # direct: python benchmarks/cst_dipole_solver_smoke.py
    from cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )

SOURCE_PATHS = {
    "runner": "benchmarks/cst_dipole_solver_smoke.py",
    "evidence": "benchmarks/cst_solver_evidence.py",
    "tools": "cst_agent_workbench/agent/tools.py",
    "tool_runtime": "cst_agent_workbench/agent/tool_runtime.py",
    "fast_path": "cst_agent_workbench/agent/fast_path.py",
    "dipole": "cst_agent_workbench/cst/dipole_fast.py",
    "solver_safety": "cst_agent_workbench/cst/solver_safety.py",
    "primitives": "cst_agent_workbench/cst/primitives.py",
    "controller": "cst_agent_workbench/cst/controller.py",
    "results_summary": "cst_agent_workbench/results/summary.py",
}


def run_smoke(output_root: Path, *, f0_ghz: float = 2.4, wire_or_plate: str = "plate") -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST dipole solver smoke 必须位于 D 盘")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()

    keys = ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")
    previous_env = {key: os.environ.get(key) for key in keys}
    previous_tempdir = tempfile.tempdir
    previous_config_temp = config.CST_TEMP_DIR
    previous_config_memory = config.AGENT_MEMORY_DIR
    controller: CSTController | None = None
    started = time.perf_counter()
    full_result: dict[str, Any] = {}
    close_result: dict[str, Any] = {"success": False, "message": "controller not created"}
    try:
        os.environ.update({
            "TEMP": str(temp_dir),
            "TMP": str(temp_dir),
            "CST_TEMP_DIR": str(temp_dir),
            "AGENT_MEMORY_DIR": str(run_dir / "agent_memory"),
        })
        tempfile.tempdir = str(temp_dir)
        config.CST_TEMP_DIR = str(temp_dir)
        config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")

        controller = CSTController()
        agent = CSTAgent(controller)
        agent.client = None
        agent._active_tool_call_id = "dipole-live-1"
        prompt_result = json.loads(execute_tool(agent, "build_dipole_fast", {
            "f0_ghz": f0_ghz,
            "wire_or_plate": wire_or_plate,
            "run_solver": True,
        }))
        full_result = dict(agent.session.artifacts.tool_results.get("dipole-live-1") or prompt_result)
    except Exception as exc:
        full_result = {"success": False, "message": f"{type(exc).__name__}: {exc}"}
    finally:
        if controller is not None and controller.project_path:
            try:
                close_result = controller.close_project(controller.project_path, timeout=60)
            except Exception as exc:
                close_result = {"success": False, "message": f"{type(exc).__name__}: {exc}"}
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tempfile.tempdir = previous_tempdir
        config.CST_TEMP_DIR = previous_config_temp
        config.AGENT_MEMORY_DIR = previous_config_memory

    s11 = dict(full_result.get("s11") or {})
    curve = list(s11.get("plot_data") or [])
    curve_path = run_dir / "s11_curve.json"
    curve_path.write_text(json.dumps(curve, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    project_path = Path(str((full_result.get("solver") or {}).get("project_file") or ""))
    if not project_path.is_file() and controller is not None:
        candidate = Path(str((full_result.get("project") or {}).get("project_file") or ""))
        project_path = candidate
    repo_root = Path(__file__).resolve().parents[1]
    commit = repository_commit(repo_root)
    source_bindings = build_source_bindings(repo_root, SOURCE_PATHS)
    report = {
        "schema_version": "cst-dipole-solver-smoke-v2",
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {"f0_ghz": f0_ghz, "wire_or_plate": wire_or_plate},
        "tool_name": "build_dipole_fast",
        "execution_granularity": "one canonical composite tool; internal build/solver/readback are deterministic host stages",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": sha256_path(project_path) if project_path.is_file() else "",
        "build": full_result.get("build") or {},
        "preflight": full_result.get("preflight") or {},
        "solver": full_result.get("solver") or {},
        "s11": {
            key: s11.get(key)
            for key in ("success", "min_s11_db", "min_freq_ghz", "target_s11_db", "target_freq_ghz", "bandwidth_ghz", "bandwidth_pct", "resonances")
        },
        "s11_point_count": len(curve),
        "s11_curve_file": str(curve_path),
        "s11_curve_sha256": sha256_path(curve_path),
        "close": close_result,
        "controller_final_project_path": controller.project_path if controller is not None else "",
        "repository_commit": commit,
        "source_bindings": source_bindings,
        "bound_sources_match_commit": bound_sources_match_commit(repo_root, commit, source_bindings),
        "verification_boundary": "Success requires CST solver success plus non-empty typed S11 readback and a saved/closed D-drive project; it does not claim convergence quality or target performance.",
    }
    report["success"] = (
        bool(full_result.get("success"))
        and bool(report["preflight"].get("success"))
        and bool(report["solver"].get("success"))
        and bool(report["s11"].get("success"))
        and report["s11_point_count"] > 0
        and report["project_exists"]
        and project_path.drive.upper() == "D:"
        and bool(close_result.get("success"))
        and report["controller_final_project_path"] == ""
        and report["bound_sources_match_commit"]
    )
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/dipole_solver"))
    parser.add_argument("--f0-ghz", type=float, default=2.4)
    parser.add_argument("--wire-or-plate", choices=("plate", "wire"), default="plate")
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root, f0_ghz=args.f0_ghz, wire_or_plate=args.wire_or_plate)
    print(json.dumps({"success": report["success"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
