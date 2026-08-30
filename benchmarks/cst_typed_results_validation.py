"""Live, SHA-bound validation for the CST Agent typed result/export tools.

This runner proves result-tree access and ASCII artifact creation.  It does not
claim solver convergence or electromagnetic correctness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cst_agent_workbench import config
from cst_agent_workbench.cst.controller import CSTController
from cst_agent_workbench.results.contracts import validate_result_envelope
from cst_agent_workbench.results.reader import ResultsReader
from cst_agent_workbench.results.service import export_project_result_ascii


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_SOURCES = (
    "cst_agent_workbench/results/contracts.py",
    "cst_agent_workbench/results/reader.py",
    "cst_agent_workbench/results/service.py",
    "cst_agent_workbench/cst/controller.py",
    "cst_agent_workbench/agent/tools.py",
    "cst_agent_workbench/agent/tool_runtime.py",
)
OFFICIAL_ASCII_HELP = Path(
    r"D:\Program Files (x86)\CST Studio Suite 2025\Online Help"
    r"\mergedProjects\VBA_3D\common_vbaimpexp\asciiexport_object.htm"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_d_drive(path: Path, *, kind: str) -> Path:
    resolved = path.resolve()
    if resolved.drive.upper() != "D:":
        raise ValueError(f"{kind} must be on D:; got {resolved}")
    return resolved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_validation(
    *,
    project: Path,
    artifact_dir: Path,
    item_path: str,
    max_points: int,
    close_after: bool,
) -> dict[str, Any]:
    project = _require_d_drive(project, kind="project")
    artifact_dir = _require_d_drive(artifact_dir, kind="artifact directory")
    if not project.is_file() or project.suffix.lower() != ".cst":
        raise ValueError(f"existing .cst project required: {project}")
    if artifact_dir.exists() and any(artifact_dir.iterdir()):
        raise ValueError(f"artifact directory must be new or empty: {artifact_dir}")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = artifact_dir / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    config.CST_TEMP_DIR = str(temp_dir)
    os.environ["CST_TEMP_DIR"] = str(temp_dir)
    os.environ["TMP"] = str(temp_dir)
    os.environ["TEMP"] = str(temp_dir)

    reader = ResultsReader()
    opened = reader.open(str(project))
    listed = reader.list_results(limit=100)
    read_result = reader.read_result(item_path, max_points=max_points) if opened.get("success") else {}
    if read_result.get("success"):
        validate_result_envelope(read_result)

    export_path = artifact_dir / "selected_result.txt"
    controller = CSTController()
    controller.project_path = str(project)
    controller.connected = True
    controller.offline_mode = False
    exported = export_project_result_ascii(
        controller,
        item_path,
        str(export_path),
        timeout=120,
    )
    closed = None
    if close_after:
        closed = controller.close_project(str(project), timeout=30)

    source_hashes = {
        relative: _sha256(ROOT / relative)
        for relative in EVIDENCE_SOURCES
    }
    official_help = {
        "path": str(OFFICIAL_ASCII_HELP),
        "exists": OFFICIAL_ASCII_HELP.is_file(),
        "sha256": _sha256(OFFICIAL_ASCII_HELP) if OFFICIAL_ASCII_HELP.is_file() else None,
    }
    checks = {
        "project_opened": bool(opened.get("success")),
        "result_tree_listed": bool(listed.get("success")) and int(listed.get("total", 0)) > 0,
        "selected_item_present": item_path in (listed.get("items") or []),
        "selected_item_read": bool(read_result.get("success")),
        "typed_result_contract_valid": bool(read_result.get("success")),
        "ascii_export_reported_success": bool(exported.get("success")),
        "ascii_export_nonempty": export_path.is_file() and export_path.stat().st_size > 0,
        "temp_artifacts_on_d_drive": temp_dir.drive.upper() == "D:",
    }
    if close_after:
        checks["project_closed_after_validation"] = bool((closed or {}).get("success"))
    return {
        "schema_version": "cst-typed-results-live-validation-v1",
        "generated_at": _utc_now(),
        "scope": {
            "proves": [
                "cst.results can open the saved project",
                "the result tree can be listed with bounded pagination",
                "the selected 1D result satisfies the typed envelope",
                "official ASCIIExport creates a non-empty D-drive artifact",
            ],
            "does_not_prove": [
                "solver convergence in this validation run",
                "electromagnetic or antenna physical correctness",
                "farfield tensor/cut support for this project",
            ],
        },
        "project": {
            "path": str(project),
            "bytes": project.stat().st_size,
            "sha256": _sha256(project),
        },
        "request": {"item_path": item_path, "max_points": max_points},
        "opened": opened,
        "listed": listed,
        "read_result": read_result,
        "exported": exported,
        "closed_after_validation": closed,
        "artifact": {
            "path": str(export_path),
            "bytes": export_path.stat().st_size if export_path.is_file() else 0,
            "sha256": _sha256(export_path) if export_path.is_file() else None,
        },
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "official_help": official_help,
        "source_sha256": source_hashes,
        "runner_sha256": _sha256(Path(__file__)),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--item-path", default=r"1D Results\S-Parameters\S1,1")
    parser.add_argument("--max-points", type=int, default=64)
    parser.add_argument("--keep-project-open", action="store_true")
    args = parser.parse_args()
    if not 4 <= args.max_points <= 5000:
        parser.error("--max-points must be between 4 and 5000")

    report = run_validation(
        project=args.project,
        artifact_dir=args.artifact_dir,
        item_path=args.item_path,
        max_points=args.max_points,
        close_after=not args.keep_project_open,
    )
    report_path = args.artifact_dir.resolve() / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "report": str(report_path),
        "all_checks_passed": report["all_checks_passed"],
        "checks": report["checks"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
