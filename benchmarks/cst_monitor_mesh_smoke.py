"""Run typed monitor/mesh tools against real CST and save evidence on D:."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.runtime_state import finish_trace_run, start_optimizer_trace_run
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.agent.tools import TOOLS
from cst_agent_workbench.cst.controller import CSTController


TOOL_SEQUENCE: tuple[tuple[str, dict[str, Any]], ...] = (
    ("create_frequency_field_monitor", {"name": "efield (f=9.4)", "frequency": 9.4, "field_type": "Efield"}),
    ("create_frequency_field_monitor", {"name": "hfield (f=9.4)", "frequency": 9.4, "field_type": "Hfield"}),
    ("create_frequency_field_monitor", {"name": "farfield (f=9.4)", "frequency": 9.4, "field_type": "Farfield"}),
    ("create_mesh_refinement", {"name": "agent_mesh_probe", "step_mm": 0.5}),
    ("set_global_hexahedral_mesh", {"lines_per_wavelength": 20, "minimum_step_number": 5}),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "success": bool(result.get("success")),
        "message": str(result.get("message") or ""),
        "verification": str(result.get("verification") or ""),
        "raw": dict(result),
    }


def run_smoke(output_root: Path) -> tuple[dict[str, Any], Path]:
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST smoke 输出必须位于 D 盘")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    os.environ["TEMP"] = str(temp_dir)
    os.environ["TMP"] = str(temp_dir)
    tempfile.tempdir = str(temp_dir)
    os.environ["AGENT_MEMORY_DIR"] = str(run_dir / "agent_memory")
    config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")

    project_path = run_dir / "monitor_mesh_smoke.cst"
    controller = CSTController()
    agent = CSTAgent(controller)
    stages: list[dict[str, Any]] = []
    trace = None

    create_result = controller.new_project(str(project_path), timeout=120)
    stages.append(_stage("create_project", create_result))
    if create_result.get("success"):
        trace = start_optimizer_trace_run(
            agent,
            user_input="real CST typed monitor and mesh smoke",
            working_messages=[],
            pending_history=[],
        )
        if trace is not None:
            trace["turns"].append({
                "turn_index": 1,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "decision_summary": {},
                "tool_calls": [],
            })
        for index, (tool_name, arguments) in enumerate(TOOL_SEQUENCE, start=1):
            agent._active_tool_call_id = f"smoke-{index}"
            prompt_result = json.loads(execute_tool(agent, tool_name, arguments))
            full_result = dict(agent.session.artifacts.tool_results.get(f"smoke-{index}") or prompt_result)
            stages.append(_stage(tool_name, full_result))
            if not full_result.get("success"):
                break

    save_result = controller.save_project(include_results=False, timeout=90)
    stages.append(_stage("save_project", save_result))
    close_result = controller.close_project(str(project_path), timeout=60) if save_result.get("success") else {
        "success": False,
        "message": "save failed; close skipped",
    }
    stages.append(_stage("close_project", close_result))
    open_result = controller.open_project(str(project_path), timeout=120) if close_result.get("success") else {
        "success": False,
        "message": "close failed; reopen skipped",
    }
    stages.append(_stage("reopen_project", open_result))
    final_close = controller.close_project(str(project_path), timeout=60) if open_result.get("success") else {
        "success": False,
        "message": "reopen failed; final close skipped",
    }
    stages.append(_stage("final_close", final_close))

    if trace is not None:
        trace["turns"][-1]["finished_at"] = datetime.now(timezone.utc).isoformat()
        finish_trace_run(
            agent,
            status="completed" if all(stage["success"] for stage in stages) else "failed",
            final_response="real CST monitor/mesh smoke completed",
        )

    canonical_tool_names = [tool["function"]["name"] for tool in TOOLS]
    report = {
        "schema_version": 1,
        "run_id": timestamp,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cst_version": {"image": "2025.0 RELEASE", "patch": "2025.2 RELEASE"},
        "python_command": list(controller.cst_python_command),
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": _sha256(project_path) if project_path.is_file() else "",
        "canonical_tool_count": len(canonical_tool_names),
        "canonical_tools_exercised": [name for name, _ in TOOL_SEQUENCE],
        "stages": stages,
        "trace": agent.trace_history[-1] if agent.trace_history else {},
        "controller_final_project_path": controller.project_path,
        "verification_semantics": {
            "history_accepted": "CST accepted the command in model history; no solver result claim",
            "persisted_reopen": "project saved, closed, and reopened; no physical-quality claim",
        },
    }
    report["success"] = (
        len(stages) == 10
        and all(stage["success"] for stage in stages)
        and all(
            stage["verification"] == "history_accepted"
            for stage in stages
            if stage["name"] in {name for name, _ in TOOL_SEQUENCE}
        )
        and report["project_exists"]
        and controller.project_path == ""
    )
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/monitor_mesh"),
    )
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root)
    print(json.dumps({"success": report["success"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
