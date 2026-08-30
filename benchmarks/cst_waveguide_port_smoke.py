"""Run Free/Full waveguide-port typed tools against CST 2025.2 on D:."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.cst_monitor_mesh_smoke import _sha256, _stage
from cst_agent_workbench import config
from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.runtime_state import finish_trace_run, start_optimizer_trace_run
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.cst.controller import CSTController


TOOL_SEQUENCE: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "create_waveguide_port",
        {
            "port_number": 1,
            "coordinate_mode": "Free",
            "orientation": "zmax",
            "number_of_modes": 2,
            "ranges": {"x": [-1, 1], "y": [-0.3, 0.2], "z": [1.1, 1.1]},
            "port_on_bound": False,
            "reference_plane_distance": -5,
        },
    ),
    (
        "create_waveguide_port",
        {
            "port_number": 2,
            "coordinate_mode": "Full",
            "orientation": "zmin",
            "number_of_modes": 1,
            "port_on_bound": True,
        },
    ),
)

SOURCE_FILES = (
    "benchmarks/cst_waveguide_port_smoke.py",
    "cst_agent_workbench/agent/tool_contracts.py",
    "cst_agent_workbench/agent/tool_runtime.py",
    "cst_agent_workbench/agent/tools.py",
    "cst_agent_workbench/cst/primitives.py",
)


@contextmanager
def _isolated_runtime_dirs(run_dir: Path):
    """Bind transient Agent/CST artifacts to D: without leaking process globals."""

    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    original_env = {name: os.environ.get(name) for name in ("TEMP", "TMP", "AGENT_MEMORY_DIR")}
    original_memory_dir = config.AGENT_MEMORY_DIR
    original_tempdir = tempfile.tempdir
    try:
        os.environ["TEMP"] = str(temp_dir)
        os.environ["TMP"] = str(temp_dir)
        os.environ["AGENT_MEMORY_DIR"] = str(run_dir / "agent_memory")
        tempfile.tempdir = str(temp_dir)
        config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")
        yield
    finally:
        for name, value in original_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempfile.tempdir = original_tempdir
        config.AGENT_MEMORY_DIR = original_memory_dir


def _run_smoke_in_dir(run_dir: Path, run_id: str) -> tuple[dict[str, Any], Path]:
    repository_root = Path(__file__).resolve().parents[1]
    project_path = run_dir / "waveguide_port_smoke.cst"
    controller = CSTController()
    agent = CSTAgent(controller)
    stages: list[dict[str, Any]] = []

    create_result = controller.new_project(str(project_path), timeout=120)
    stages.append(_stage("create_project", create_result))
    trace = start_optimizer_trace_run(
        agent,
        user_input="real CST typed Free/Full waveguide port smoke",
        working_messages=[],
        pending_history=[],
    ) if create_result.get("success") else None
    if trace is not None:
        trace["turns"].append({
            "turn_index": 1,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "decision_summary": {},
            "tool_calls": [],
        })
    for index, (tool_name, arguments) in enumerate(TOOL_SEQUENCE, start=1):
        if not stages[-1]["success"]:
            break
        agent._active_tool_call_id = f"waveguide-{index}"
        prompt_result = json.loads(execute_tool(agent, tool_name, arguments))
        full_result = dict(agent.session.artifacts.tool_results.get(f"waveguide-{index}") or prompt_result)
        stages.append(_stage(f"{tool_name}_{arguments['coordinate_mode'].lower()}", full_result))

    save_result = controller.save_project(include_results=False, timeout=90)
    stages.append(_stage("save_project", save_result))
    close_result = controller.close_project(str(project_path), timeout=60) if save_result.get("success") else {"success": False, "message": "save failed"}
    stages.append(_stage("close_project", close_result))
    open_result = controller.open_project(str(project_path), timeout=120) if close_result.get("success") else {"success": False, "message": "close failed"}
    stages.append(_stage("reopen_project", open_result))
    final_close = controller.close_project(str(project_path), timeout=60) if open_result.get("success") else {"success": False, "message": "reopen failed"}
    stages.append(_stage("final_close", final_close))
    if trace is not None:
        trace["turns"][-1]["finished_at"] = datetime.now(timezone.utc).isoformat()
        finish_trace_run(
            agent,
            status="completed" if all(item["success"] for item in stages) else "failed",
            final_response="real CST Free/Full waveguide-port smoke completed",
        )

    project_sha256 = _sha256(project_path) if project_path.is_file() else ""
    trace_report = agent.trace_history[-1] if agent.trace_history else {}
    trace_calls = [
        call
        for turn in trace_report.get("turns", [])
        for call in turn.get("tool_calls", [])
    ]
    report = {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cst_version": {"image": "2025.0 RELEASE", "patch": "2025.2 RELEASE"},
        "python_command": list(controller.cst_python_command),
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": project_sha256,
        "execution_source_sha256": {
            relative_path: _sha256(repository_root / relative_path)
            for relative_path in SOURCE_FILES
        },
        "canonical_tools_exercised": [name for name, _ in TOOL_SEQUENCE],
        "tool_inputs": [dict(arguments) for _, arguments in TOOL_SEQUENCE],
        "stages": stages,
        "trace": trace_report,
        "controller_final_project_path": controller.project_path,
        "verification_semantics": {
            "history_accepted": "execute_vba returned success=true and executed=true; this is not port-object readback or a solver-result claim",
            "persisted_reopen": "the project file was saved, closed, reopened, and closed again; port objects and parameters were not read back after reopen",
        },
        "scope": "Free and Full modes only; Picks requires a real solid face and is deferred to a geometry-backed smoke",
    }
    tool_stages = [item for item in stages if item["name"].startswith("create_waveguide_port_")]
    expected_stage_names = [
        "create_project",
        "create_waveguide_port_free",
        "create_waveguide_port_full",
        "save_project",
        "close_project",
        "reopen_project",
        "final_close",
    ]
    report["success"] = (
        [item["name"] for item in stages] == expected_stage_names
        and all(item["success"] for item in stages)
        and len(tool_stages) == 2
        and all(item["verification"] == "history_accepted" for item in tool_stages)
        and report["project_exists"]
        and len(project_sha256) == 64
        and all(len(value) == 64 for value in report["execution_source_sha256"].values())
        and [item.get("tool_name") for item in trace_calls] == [name for name, _ in TOOL_SEQUENCE]
        and all(item.get("success") is True for item in trace_calls)
        and controller.project_path == ""
    )
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def run_smoke(output_root: Path) -> tuple[dict[str, Any], Path]:
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST smoke 输出必须位于 D 盘")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    with _isolated_runtime_dirs(run_dir):
        return _run_smoke_in_dir(run_dir, run_id)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=Path("D:/cst_agent_rag_data/agent_eval/waveguide_port"))
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root)
    print(json.dumps({"success": report["success"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
