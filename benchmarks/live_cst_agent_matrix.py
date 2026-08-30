"""Opt-in real-CST + real-model Agent evidence collector.

The default case is read-only. Mutating and solver cases require both explicit
CLI flags and environment gates, and they must target a D-drive project copy.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cst_agent_workbench import config  # noqa: E402
from cst_agent_workbench.agent.agent import CSTAgent  # noqa: E402
from cst_agent_workbench.cst.controller import CSTController  # noqa: E402


CASES: dict[str, dict[str, Any]] = {
    "status_readonly": {
        "risk": "read_only",
        "prompt": (
            "检查当前 CST 是否在线。只能调用只读状态工具，不得运行求解器、执行 VBA、"
            "建模或修改当前工程；严格依据工具结果回答。"
        ),
        "required_tool_evidence_any": [["check_cst_status"]],
        "forbidden_tools": ["run_solver", "execute_vba_script", "build_rectangular_patch_fast"],
    },
    "patch_build_copy": {
        "risk": "mutating_project_copy",
        "prompt": (
            "在当前明确指定的工程副本中建立一个 9.4 GHz、Rogers 5880、厚度 0.508 mm 的"
            "标准矩形微带贴片天线。只建模，不得运行求解器。"
        ),
        "required_tool_evidence_any": [
            ["build_rectangular_patch_fast"],
            ["new_project", "fast_patch_setup", "fast_patch_geometry", "fast_patch_port"],
        ],
        "forbidden_tools": ["run_solver"],
        "expected_patch_request": {
            "f0_ghz": 9.4,
            "substrate_name": "Rogers5880",
            "epsilon_r": 2.2,
            "loss_tangent": 0.0009,
            "substrate_thickness_mm": 0.508,
            "feed_strategy": "microstrip",
        },
    },
    "patch_solver_readback_copy": {
        "risk": "solver_on_project_copy",
        "prompt": (
            "在当前明确指定的工程副本中建立一个 9.4 GHz、Rogers 5880、厚度 0.508 mm 的"
            "标准矩形微带贴片天线，运行求解并读取 S11；必须报告工具执行和结果读取是否成功。"
        ),
        "required_tool_evidence_any": [
            ["build_rectangular_patch_fast"],
            ["new_project", "fast_patch_setup", "fast_patch_geometry", "fast_patch_port"],
        ],
        "forbidden_tools": [],
        "requires_solver": True,
        "requires_result_readback": True,
        "expected_patch_request": {
            "f0_ghz": 9.4,
            "substrate_name": "Rogers5880",
            "epsilon_r": 2.2,
            "loss_tangent": 0.0009,
            "substrate_thickness_mm": 0.508,
            "feed_strategy": "microstrip",
        },
    },
}


def _assert_d_drive(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if resolved.drive.upper() != "D:":
        raise ValueError(f"{label} must be on D:; got {resolved}")
    return resolved


def validate_live_request(
    *,
    case_names: list[str],
    artifact_root: Path,
    project_copy: Path | None,
    allow_mutating: bool,
    allow_solver: bool,
    environment: dict[str, str] | None = None,
) -> None:
    env = environment if environment is not None else os.environ
    _assert_d_drive(artifact_root, "artifact_root")
    if env.get("RUN_LIVE_CST") != "1":
        raise PermissionError("set RUN_LIVE_CST=1 for any real-CST run")
    if project_copy is None:
        raise ValueError(
            "all live cases require --project-copy on D: so CST cannot create an untitled project under C: temp"
        )
    resolved_project = _assert_d_drive(project_copy, "project_copy")
    if resolved_project.suffix.lower() != ".cst" or not resolved_project.is_file():
        raise ValueError("project_copy must be an existing .cst file on D:")
    risks = {str(CASES[name]["risk"]) for name in case_names}
    if risks - {"read_only"}:
        if not allow_mutating or env.get("RUN_LIVE_CST_MUTATING") != "1":
            raise PermissionError(
                "mutating cases require --allow-mutating and RUN_LIVE_CST_MUTATING=1"
            )
    if "solver_on_project_copy" in risks:
        if not allow_solver or env.get("RUN_LIVE_CST_SOLVER") != "1":
            raise PermissionError(
                "solver cases require --allow-solver and RUN_LIVE_CST_SOLVER=1"
            )


def _file_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _score_live_case(spec: dict[str, Any], agent: CSTAgent, response: str) -> dict[str, Any]:
    tool_names = [str(item.get("tool_name") or "") for item in agent.tool_events]
    evidence_alternatives = [
        [str(name) for name in alternative]
        for alternative in spec.get("required_tool_evidence_any") or []
    ]
    forbidden = [str(name) for name in spec.get("forbidden_tools") or []]
    actual_request = getattr(agent, "last_patch_request", None)
    expected_request = dict(spec.get("expected_patch_request") or {})

    def request_field_matches(name: str, expected: Any) -> bool:
        actual = getattr(actual_request, name, None)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            try:
                return abs(float(actual) - float(expected)) <= 1e-9
            except (TypeError, ValueError):
                return False
        return str(actual or "").casefold() == str(expected or "").casefold()

    result_summary = dict(getattr(agent, "last_results", None) or {})
    project_path = Path(str(getattr(getattr(agent, "cst", None), "project_path", "") or ""))
    return {
        "response_nonempty": bool(response.strip()),
        "agent_status_ok": bool(agent.last_chat_status.get("ok")),
        "required_tool_evidence_present": (
            any(all(name in tool_names for name in alternative) for alternative in evidence_alternatives)
            if evidence_alternatives
            else True
        ),
        "forbidden_tools_absent": all(name not in tool_names for name in forbidden),
        "all_tool_events_successful": all(
            bool(item.get("success")) for item in agent.tool_events
        ),
        "solver_event_present": (
            "run_solver" in tool_names if spec.get("requires_solver") else True
        ),
        "result_readback_successful": (
            bool(result_summary.get("success") and result_summary.get("plot_data"))
            if spec.get("requires_result_readback")
            else True
        ),
        "requested_parameters_satisfied": all(
            request_field_matches(name, expected)
            for name, expected in expected_request.items()
        ),
        "active_project_on_d": bool(
            str(project_path) and project_path.resolve().drive.upper() == "D:"
        ),
        "trace_completed": bool(
            agent.trace_history and agent.trace_history[-1].get("status") == "completed"
        ),
    }


def run_live_matrix(
    *,
    case_names: list[str],
    model: str,
    artifact_root: Path,
    project_copy: Path | None,
    allow_mutating: bool,
    allow_solver: bool,
) -> dict[str, Any]:
    validate_live_request(
        case_names=case_names,
        artifact_root=artifact_root,
        project_copy=project_copy,
        allow_mutating=allow_mutating,
        allow_solver=allow_solver,
    )
    run_id = uuid.uuid4().hex[:12]
    run_root = _assert_d_drive(artifact_root, "artifact_root") / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    original_config = {
        "AGENT_MEMORY_DIR": config.AGENT_MEMORY_DIR,
        "CST_DEFAULT_PROJECT": config.CST_DEFAULT_PROJECT,
    }
    original_tempdir = tempfile.tempdir
    original_environment = {name: os.environ.get(name) for name in ("TMP", "TEMP")}
    project_before = _file_snapshot(project_copy)
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    try:
        temp_root = run_root / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        for name in ("TMP", "TEMP"):
            os.environ[name] = str(temp_root)
        tempfile.tempdir = str(temp_root)
        config.AGENT_MEMORY_DIR = str(run_root / "memory")
        if project_copy is not None:
            config.CST_DEFAULT_PROJECT = str(project_copy.resolve())

        controller = CSTController()
        connect_result = controller.connect()
        if not connect_result.get("success"):
            raise RuntimeError(f"real CST connection failed: {connect_result.get('message', '')}")

        for case_name in case_names:
            spec = CASES[case_name]
            agent = CSTAgent(controller)
            agent.model = model
            token_before = dict(agent.token_stats)
            case_started = time.perf_counter()
            response = agent.chat(str(spec["prompt"]))
            checks = _score_live_case(spec, agent, response)
            execution_check_names = {
                "response_nonempty",
                "agent_status_ok",
                "all_tool_events_successful",
                "solver_event_present",
                "result_readback_successful",
                "active_project_on_d",
                "trace_completed",
            }
            execution_success = all(
                bool(value) for name, value in checks.items() if name in execution_check_names
            )
            task_success = execution_success and all(bool(value) for value in checks.values())
            token_after = dict(agent.token_stats)
            patch_request = getattr(agent, "last_patch_request", None)
            results.append(
                {
                    "case_id": case_name,
                    "risk": spec["risk"],
                    "prompt": spec["prompt"],
                    "response": response,
                    "checks": checks,
                    "execution_success": execution_success,
                    "task_success": task_success,
                    "success": task_success,
                    "tool_events": [dict(item) for item in agent.tool_events],
                    "last_chat_status": dict(agent.last_chat_status),
                    "trace": dict(agent.trace_history[-1]) if agent.trace_history else None,
                    "token_usage": {
                        key: int(token_after.get(key, 0) or 0)
                        - int(token_before.get(key, 0) or 0)
                        for key in ("prompt", "completion", "calls")
                    },
                    "latency_ms": round((time.perf_counter() - case_started) * 1000, 3),
                    "controller_status": controller.get_status(),
                    "project_path": controller.project_path,
                    "last_patch_request": (
                        asdict(patch_request)
                        if patch_request is not None and is_dataclass(patch_request)
                        else None
                    ),
                    "result_summary": dict(agent.last_results or {}),
                }
            )
    finally:
        config.AGENT_MEMORY_DIR = original_config["AGENT_MEMORY_DIR"]
        config.CST_DEFAULT_PROJECT = original_config["CST_DEFAULT_PROJECT"]
        tempfile.tempdir = original_tempdir
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    return {
        "schema_version": "live-cst-agent-evidence-v1",
        "run": {
            "run_id": run_id,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "platform": platform.platform(),
            "artifact_root": str(run_root),
            "project_before": project_before,
            "project_after": _file_snapshot(project_copy),
            "duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "secrets_recorded": False,
        },
        "cases": results,
        "summary": {
            "case_count": len(results),
            "success_count": sum(bool(item["success"]) for item in results),
            "success_rate": sum(bool(item["success"]) for item in results) / len(results),
        },
        "honest_boundary": [
            "This report uses a real local CST bridge and a real model provider.",
            "A status-only run does not prove solver or electromagnetic correctness.",
            "Mutating and solver evidence is valid only for the recorded D-drive project copy.",
            "Solver timeout cannot reliably abort CST Design Environment; do not inject a forced timeout into an unsupervised workstation run.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=sorted(CASES), dest="cases")
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/live_cst_agent_evidence"),
    )
    parser.add_argument("--project-copy", type=Path)
    parser.add_argument("--allow-mutating", action="store_true")
    parser.add_argument("--allow-solver", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_live_matrix(
        case_names=args.cases or ["status_readonly"],
        model=args.model,
        artifact_root=args.artifact_root,
        project_copy=args.project_copy,
        allow_mutating=args.allow_mutating,
        allow_solver=args.allow_solver,
    )
    output = _assert_d_drive(args.output, "output")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["success_count"] == report["summary"]["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
