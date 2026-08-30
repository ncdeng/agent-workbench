"""Build a solved D-drive CST optimization case from a frozen source project."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from benchmarks.cst_native_optimizer_runner import (
    _read_parameter_value,
    _read_s11,
    _sha256,
    clone_cst_project,
    validate_native_optimizer_request,
)
from cst_agent_workbench import config
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.cst.primitives import store_parameter


def build_solved_case(
    *,
    source_project: Path,
    case_root: Path,
    case_id: str,
    patch_l: float,
    target_freq_ghz: float,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if not case_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for char in case_id):
        raise ValueError("case_id must use lowercase ASCII letters, digits, '_' or '-'")
    source, root = validate_native_optimizer_request(
        source_project=source_project,
        artifact_root=case_root,
        environment=environment,
    )
    run_root = root / case_id
    project = clone_cst_project(source, run_root)
    temp_root = run_root / "tmp"
    temp_root.mkdir()
    old_patch_l = _read_parameter_value(project, "patch_L")
    old_temp = os.environ.get("TEMP"), os.environ.get("TMP")
    original_default = config.CST_DEFAULT_PROJECT
    controller: CSTController | None = None
    connection: dict[str, Any] = {}
    update: dict[str, Any] = {}
    solver: dict[str, Any] = {}
    close: dict[str, Any] = {}
    try:
        os.environ["TEMP"] = str(temp_root)
        os.environ["TMP"] = str(temp_root)
        config.CST_DEFAULT_PROJECT = str(project)
        controller = CSTController()
        connection = controller.connect()
        controller.project_path = str(project)
        controller._query_best_effort_project_path = lambda: str(project)  # type: ignore[method-assign]
        if connection.get("success"):
            _, vba = store_parameter("patch_L", format(patch_l, ".15g"))
            update = controller.execute_vba(vba, label=f"case_{case_id}_patch_L", timeout=120)
        if update.get("success"):
            solver = controller.run_solver(timeout=600)
    finally:
        if controller is not None and connection.get("success"):
            close = controller.close_project(str(project), timeout=60)
        config.CST_DEFAULT_PROJECT = original_default
        for name, value in zip(("TEMP", "TMP"), old_temp):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    s11 = _read_s11(project, target_freq_ghz)
    final_patch_l = _read_parameter_value(project, "patch_L")
    success = bool(
        connection.get("success")
        and update.get("success")
        and solver.get("success")
        and s11.get("success")
        and abs(final_patch_l - patch_l) <= 1e-9
    )
    report = {
        "schema_version": "cst-optimization-case-v1",
        "case_id": case_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_project": str(source),
        "source_sha256": _sha256(source),
        "project": str(project),
        "project_sha256": _sha256(project),
        "temp_root": str(temp_root),
        "parameter": {
            "name": "patch_L",
            "source_value": old_patch_l,
            "requested_value": patch_l,
            "persisted_value": final_patch_l,
        },
        "connection": connection,
        "update": update,
        "solver": solver,
        "close": close,
        "baseline_s11": s11,
        "physical_solver_evaluations": 1,
        "success": success,
    }
    (run_root / "case.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-project", type=Path, required=True)
    parser.add_argument(
        "--case-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/optimization_cases"),
    )
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--patch-l", type=float, required=True)
    parser.add_argument("--target-freq-ghz", type=float, default=9.4)
    args = parser.parse_args()
    report = build_solved_case(
        source_project=args.source_project,
        case_root=args.case_root,
        case_id=args.case_id,
        patch_l=args.patch_l,
        target_freq_ghz=args.target_freq_ghz,
    )
    print(
        json.dumps(
            {
                "success": report["success"],
                "project": report["project"],
                "patch_L": report["parameter"]["persisted_value"],
                "s11_at_target_db": report["baseline_s11"].get("target_s11_db"),
                "min_s11_db": report["baseline_s11"].get("min_s11_db"),
                "min_freq_ghz": report["baseline_s11"].get("min_freq_ghz"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
