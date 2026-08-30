"""Run a real CST 2025 project-lifecycle smoke and persist evidence on D:."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cst_agent_workbench.cst.controller import CSTController


def _stage(name: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "success": bool(result.get("success")),
        "message": str(result.get("message") or ""),
        "project_file": str(result.get("project_file") or ""),
        "raw": dict(result),
    }


def run_smoke(output_root: Path) -> tuple[dict[str, Any], Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = output_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=False)
    temp_dir = run_dir / "tmp"
    temp_dir.mkdir()
    os.environ["TEMP"] = str(temp_dir)
    os.environ["TMP"] = str(temp_dir)
    tempfile.tempdir = str(temp_dir)

    original_path = run_dir / "lifecycle_original.cst"
    saved_as_path = run_dir / "lifecycle_saved_as.cst"
    controller = CSTController()
    stages: list[dict[str, Any]] = []

    create_result = controller.new_project(str(original_path), timeout=120)
    stages.append(_stage("create", create_result))
    if create_result.get("success"):
        save_result = controller.save_project(include_results=False, timeout=90)
        stages.append(_stage("save_without_results", save_result))
    if stages[-1]["success"]:
        save_as_result = controller.save_project_as(
            str(saved_as_path),
            include_results=False,
            timeout=120,
        )
        stages.append(_stage("save_as_without_results", save_as_result))
    if stages[-1]["success"]:
        close_result = controller.close_project(str(saved_as_path), timeout=60)
        stages.append(_stage("close_saved_as", close_result))
    if stages[-1]["success"]:
        open_result = controller.open_project(str(saved_as_path), timeout=120)
        stages.append(_stage("reopen_saved_as", open_result))
    if stages[-1]["success"]:
        final_close = controller.close_project(str(saved_as_path), timeout=60)
        stages.append(_stage("final_close", final_close))

    report = {
        "schema_version": 1,
        "run_id": timestamp,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cst_version": {"image": "2025.0 RELEASE", "patch": "2025.2 RELEASE"},
        "python_command": list(controller.cst_python_command),
        "output_root": str(run_dir),
        "artifacts": {
            "original_project": str(original_path),
            "saved_as_project": str(saved_as_path),
            "original_exists": original_path.is_file(),
            "saved_as_exists": saved_as_path.is_file(),
        },
        "stages": stages,
        "controller_final_project_path": controller.project_path,
    }
    report["success"] = (
        len(stages) == 6
        and all(stage["success"] for stage in stages)
        and report["artifacts"]["original_exists"]
        and report["artifacts"]["saved_as_exists"]
        and controller.project_path == ""
    )
    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report, report_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("D:/cst_agent_rag_data/agent_eval/project_lifecycle"),
    )
    args = parser.parse_args()
    report, report_path = run_smoke(args.output_root)
    print(json.dumps({"success": report["success"], "report": str(report_path)}, ensure_ascii=False))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

