"""Build and verify a synthetic complex geometry through canonical CST tools."""

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
from cst_agent_workbench.agent.tool_runtime import execute_tool
from cst_agent_workbench.agent.tools import TOOLS
from cst_agent_workbench.cst.controller import CSTController


EXPECTED_FINAL_SOLIDS = {"complex:body", "complex:array"}
TOOL_SEQUENCE: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "create_extruded_polygon",
        {
            "name": "body",
            "component": "complex",
            "material": "PEC",
            "points": [["-6", "-4"], ["6", "-4"], ["6", "4"], ["2", "4"], ["2", "1"], ["-2", "1"], ["-2", "4"], ["-6", "4"]],
            "height": "1",
        },
    ),
    ("set_wcs", {"mode": "local", "origin": ["0", "0", "1"], "u_vector": ["1", "0", "0"], "normal": ["0", "0", "1"]}),
    (
        "create_extruded_polygon",
        {
            "name": "tab",
            "component": "complex",
            "material": "PEC",
            "points": [["-1", "-1"], ["3", "-1"], ["3", "1"], ["-1", "1"]],
            "height": "1",
            "origin": ["8", "0", "0"],
        },
    ),
    ("set_wcs", {"mode": "global"}),
    ("transform_shape", {"shape": "complex:tab", "operation": "translate", "vector": ["-6", "0", "0"]}),
    ("boolean_add", {"obj1": "complex:body", "obj2": "complex:tab"}),
    ("create_cylinder", {"name": "hole", "component": "complex", "material": "PEC", "axis": "z", "outer_radius": "1", "xcenter": "0", "ycenter": "0", "zmin": "-1", "zmax": "3"}),
    ("boolean_subtract", {"obj1": "complex:body", "obj2": "complex:hole"}),
    ("create_brick", {"name": "array", "component": "complex", "material": "PEC", "xmin": "-1", "xmax": "1", "ymin": "-1", "ymax": "1", "zmin": "0", "zmax": "1"}),
    ("transform_shape", {"shape": "complex:array", "operation": "translate", "vector": ["4", "0", "0"], "copy": True, "repetitions": 3, "unite": True}),
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stage(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "success": bool(result.get("success")),
        "message": str(result.get("message") or ""),
        "verification": str(result.get("verification") or ""),
        "raw": dict(result),
    }


def _failed_stage(name: str, message: str) -> dict[str, Any]:
    return _stage(name, {"success": False, "message": message})


def run_smoke(output_root: Path) -> tuple[dict[str, Any], Path]:
    output_root = output_root.resolve()
    if output_root.drive.upper() != "D:":
        raise ValueError("真实 CST smoke 输出必须位于 D 盘")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    environment_keys = ("TEMP", "TMP", "CST_TEMP_DIR", "AGENT_MEMORY_DIR")
    previous_environment = {key: os.environ.get(key) for key in environment_keys}
    previous_tempdir = tempfile.tempdir
    previous_config_temp = config.CST_TEMP_DIR
    previous_config_memory = config.AGENT_MEMORY_DIR

    project_path = run_dir / "complex_geometry_smoke.cst"
    controller: CSTController | None = None
    stages: list[dict[str, Any]] = []
    inventory_before: dict[str, Any] = {}
    inventory_after: dict[str, Any] = {}
    try:
        os.environ["TEMP"] = str(temp_dir)
        os.environ["TMP"] = str(temp_dir)
        os.environ["CST_TEMP_DIR"] = str(temp_dir)
        os.environ["AGENT_MEMORY_DIR"] = str(run_dir / "agent_memory")
        tempfile.tempdir = str(temp_dir)
        config.CST_TEMP_DIR = str(temp_dir)
        config.AGENT_MEMORY_DIR = str(run_dir / "agent_memory")

        controller = CSTController()
        agent = CSTAgent(controller)
        create_result = controller.new_project(str(project_path), timeout=120)
        stages.append(_stage("create_project", create_result))
        workflow_ready = bool(create_result.get("success"))

        if workflow_ready:
            for index, (tool_name, arguments) in enumerate(TOOL_SEQUENCE, start=1):
                agent._active_tool_call_id = f"geometry-{index}"
                prompt_result = json.loads(execute_tool(agent, tool_name, arguments))
                full_result = dict(
                    agent.session.artifacts.tool_results.get(f"geometry-{index}") or prompt_result
                )
                stages.append(_stage(tool_name, full_result))
                if not full_result.get("success"):
                    workflow_ready = False
                    break

        if workflow_ready:
            inventory_before = controller.list_solids(timeout=60)
            stages.append(_stage("inventory_before_save", inventory_before))
            workflow_ready = bool(inventory_before.get("success"))
        if workflow_ready:
            save_result = controller.save_project(include_results=False, timeout=90)
            stages.append(_stage("save_project", save_result))
            workflow_ready = bool(save_result.get("success"))
        if workflow_ready:
            close_result = controller.close_project(str(project_path), timeout=60)
            stages.append(_stage("close_project", close_result))
            workflow_ready = bool(close_result.get("success"))
        if workflow_ready:
            reopen_result = controller.open_project(str(project_path), timeout=120)
            stages.append(_stage("reopen_project", reopen_result))
            workflow_ready = bool(reopen_result.get("success"))
        if workflow_ready:
            inventory_after = controller.list_solids(timeout=60)
            stages.append(_stage("inventory_after_reopen", inventory_after))
    except Exception as exc:
        stages.append(_failed_stage("unexpected_exception", f"{type(exc).__name__}: {exc}"))
    finally:
        if controller is not None and controller.project_path:
            try:
                final_close = controller.close_project(controller.project_path, timeout=60)
            except Exception as exc:
                final_close = {"success": False, "message": f"{type(exc).__name__}: {exc}"}
            stages.append(_stage("final_close", final_close))

    for key, previous in previous_environment.items():
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous
    tempfile.tempdir = previous_tempdir
    config.CST_TEMP_DIR = previous_config_temp
    config.AGENT_MEMORY_DIR = previous_config_memory

    names_before = set(map(str, inventory_before.get("solids") or []))
    names_after = set(map(str, inventory_after.get("solids") or []))
    canonical_names = [tool["function"]["name"] for tool in TOOLS]
    report = {
        "schema_version": "cst-complex-geometry-smoke-v1",
        "run_id": timestamp,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "validation_target": {
            "product": "CST Studio Suite",
            "expected_image": "2025.0 RELEASE",
            "expected_patch": "2025.2 RELEASE",
            "version_source": "docs/CST_AGENT_RESEARCH_SCOPE.md; not runtime-probed",
        },
        "python_command": list(controller.cst_python_command) if controller is not None else [],
        "project_file": str(project_path),
        "project_exists": project_path.is_file(),
        "project_sha256": _sha256(project_path) if project_path.is_file() else "",
        "canonical_tool_count": len(canonical_names),
        "canonical_tools_exercised": [name for name, _ in TOOL_SEQUENCE],
        "expected_final_solids": sorted(EXPECTED_FINAL_SOLIDS),
        "inventory_before_save": sorted(names_before),
        "inventory_after_reopen": sorted(names_after),
        "stages": stages,
        "controller_final_project_path": controller.project_path if controller is not None else "",
        "source_bindings": {
            "runner": _sha256(Path(__file__)),
            "tools": _sha256(Path(config.__file__).parent / "agent" / "tools.py"),
            "tool_runtime": _sha256(Path(config.__file__).parent / "agent" / "tool_runtime.py"),
            "agent_runtime": _sha256(Path(config.__file__).parent / "agent" / "runtime.py"),
            "primitives": _sha256(Path(config.__file__).parent / "cst" / "primitives.py"),
            "controller": _sha256(Path(config.__file__).parent / "cst" / "controller.py"),
        },
        "verification_semantics": {
            "history_accepted": "CST add_to_history returned success and the Host marked the command executed; geometry values were not independently measured.",
            "inventory_before_save": "Solid.GetNameOfShapeFromIndex enumerated the expected final solids before save.",
            "inventory_after_reopen": "The same expected solid inventory was read after close/reopen.",
            "boundary": "No solver, convergence, port validity, or electromagnetic target is claimed.",
        },
    }
    typed_stages = [stage for stage in stages if stage["name"] in {name for name, _ in TOOL_SEQUENCE}]
    report["success"] = (
        len(typed_stages) == len(TOOL_SEQUENCE)
        and all(stage["success"] for stage in stages)
        and all(stage["verification"] == "history_accepted" for stage in typed_stages)
        and names_before == EXPECTED_FINAL_SOLIDS
        and names_after == EXPECTED_FINAL_SOLIDS
        and report["project_exists"]
        and controller is not None
        and controller.project_path == ""
        and all(path.resolve().drive.upper() == "D:" for path in (run_dir, project_path, temp_dir))
    )
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/complex_geometry"),
    )
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root)
    print(json.dumps({"success": report["success"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
