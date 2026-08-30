"""Run a canonical rectangular-patch build, real solver, and typed S11 readback on D:."""

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
from cst_agent_workbench.results.summary import summarize_s11_result
try:
    from benchmarks.cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )
except ModuleNotFoundError:  # direct: python benchmarks/cst_patch_solver_smoke.py
    from cst_solver_evidence import (
        bound_sources_match_commit,
        build_source_bindings,
        repository_commit,
        sha256_path,
    )


DEFAULT_REQUEST: dict[str, Any] = {
    "f0_ghz": 9.4,
    "substrate_name": "Rogers5880",
    "epsilon_r": 2.2,
    "loss_tangent": 0.0009,
    "substrate_thickness_mm": 0.508,
    "conductor_name": "Copper (annealed)",
    "conductor_thickness_mm": 0.035,
    "feed_strategy": "microstrip",
    "run_solver": True,
}


SOURCE_PATHS = {
    "runner": "benchmarks/cst_patch_solver_smoke.py",
    "evidence": "benchmarks/cst_solver_evidence.py",
    "agent": "cst_agent_workbench/agent/agent.py",
    "tools": "cst_agent_workbench/agent/tools.py",
    "tool_runtime": "cst_agent_workbench/agent/tool_runtime.py",
    "fast_path": "cst_agent_workbench/agent/fast_path.py",
    "patch_fast_executor": "cst_agent_workbench/cst/patch_fast_executor.py",
    "rectangular_patch": "cst_agent_workbench/cst/rectangular_patch_fast.py",
    "solver_safety": "cst_agent_workbench/cst/solver_safety.py",
    "primitives": "cst_agent_workbench/cst/primitives.py",
    "controller": "cst_agent_workbench/cst/controller.py",
    "results_summary": "cst_agent_workbench/results/summary.py",
}


def run_smoke(
    output_root: Path,
    *,
    request: dict[str, Any] | None = None,
    target_db: float = -10.0,
) -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST rectangular-patch solver smoke 必须位于 D 盘")
    normalized_request = {**DEFAULT_REQUEST, **dict(request or {})}
    normalized_request["run_solver"] = True
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
    tool_result: dict[str, Any] = {}
    agent_status: dict[str, Any] = {}
    raw_s11: dict[str, Any] = {}
    internal_events: list[dict[str, Any]] = []
    active_project_path = ""
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
        agent._active_tool_call_id = "patch-live-1"
        tool_result = json.loads(execute_tool(
            agent,
            "build_rectangular_patch_fast",
            normalized_request,
        ))
        agent_status = dict(agent.last_chat_status)
        raw_s11 = dict(agent.last_results)
        internal_events = [dict(event) for event in agent.tool_events]
        active_project_path = str(controller.project_path or "")
    except Exception as exc:
        tool_result = {"success": False, "message": f"{type(exc).__name__}: {exc}"}
    finally:
        if controller is not None and controller.project_path:
            if not active_project_path:
                active_project_path = str(controller.project_path)
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

    s11_summary = summarize_s11_result(raw_s11, float(normalized_request["f0_ghz"])).to_dict()
    curve = list(s11_summary.get("plot_data") or [])
    curve_path = run_dir / "s11_curve.json"
    curve_path.write_text(json.dumps(curve, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    project_path = Path(active_project_path)
    solver_events = [event for event in internal_events if event.get("tool_name") == "run_solver"]
    solver_event_ok = any(bool(event.get("success")) for event in solver_events)
    execution_success = (
        bool(tool_result.get("success"))
        and bool(agent_status.get("ok"))
        and not bool(agent_status.get("had_tool_failure"))
        and solver_event_ok
        and bool(s11_summary.get("success"))
        and bool(curve)
        and project_path.is_file()
        and project_path.drive.upper() == "D:"
        and bool(close_result.get("success"))
        and (controller.project_path if controller is not None else "") == ""
    )
    target_s11_db = s11_summary.get("target_s11_db")
    target_metric_met = target_s11_db is not None and float(target_s11_db) <= target_db
    repo_root = Path(__file__).resolve().parents[1]
    commit = repository_commit(repo_root)
    source_bindings = build_source_bindings(repo_root, SOURCE_PATHS, snapshot_root=run_dir / "source_snapshot")
    sources_committed = bound_sources_match_commit(repo_root, commit, source_bindings)
    source_snapshot_complete = all(
        Path(str(binding.get("snapshot_file") or "")).is_file()
        and sha256_path(Path(str(binding["snapshot_file"]))) == binding.get("snapshot_sha256")
        for binding in source_bindings.values()
    )
    evidence_success = execution_success and source_snapshot_complete
    report = {
        "schema_version": "cst-patch-solver-smoke-v2",
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": {key: value for key, value in normalized_request.items() if key != "run_solver"},
        "tool_name": "build_rectangular_patch_fast",
        "execution_granularity": "one canonical composite tool; internal project/setup/geometry/port/monitor/solver/readback are deterministic host stages",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "tool_result": tool_result,
        "agent_status": agent_status,
        "internal_events": internal_events,
        "solver_event_count": len(solver_events),
        "solver_event_success": solver_event_ok,
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": sha256_path(project_path) if project_path.is_file() else "",
        "s11": {
            key: s11_summary.get(key)
            for key in (
                "success", "min_s11_db", "min_freq_ghz", "target_s11_db",
                "target_freq_ghz", "bandwidth_ghz", "bandwidth_pct", "resonances",
            )
        },
        "s11_point_count": len(curve),
        "s11_curve_file": str(curve_path),
        "s11_curve_sha256": sha256_path(curve_path),
        "close": close_result,
        "controller_final_project_path": controller.project_path if controller is not None else "",
        "target_contract": {"metric": "S11@f0", "operator": "<=", "threshold_db": target_db},
        "target_metric_met": target_metric_met,
        "target_eligible": evidence_success,
        "target_met": evidence_success and target_metric_met,
        "repository_commit": commit,
        "source_bindings": source_bindings,
        "bound_sources_match_commit": sources_committed,
        "source_snapshot_complete": source_snapshot_complete,
        "source_provenance_note": "Each bound source is archived in the D-drive report. commit_blob_match identifies which bytes are also reproducible from repository_commit.",
        "verification_boundary": "Success requires the canonical composite call, host solver event, non-empty typed S11, saved D-drive project, and clean close. Internal stages are not separate model tool calls; target performance is reported separately.",
        "success": evidence_success,
    }
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/patch_solver"))
    parser.add_argument("--target-db", type=float, default=-10.0)
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root, target_db=args.target_db)
    print(json.dumps({
        "success": report["success"],
        "target_met": report["target_met"],
        "report": str(report_path),
    }, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
