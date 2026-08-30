"""Aggregate selected real-CST arm reports into a SHA-bound canonical summary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


EXPECTED_ARMS = ("heuristic", "llm", "cst_native", "hybrid")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def aggregate_reports(
    *, selected: dict[str, dict[str, Path]], output_path: Path
) -> dict[str, Any]:
    cases: dict[str, Any] = {}
    for case_id, arm_paths in selected.items():
        if set(arm_paths) != set(EXPECTED_ARMS):
            raise ValueError(f"{case_id}: expected exactly {EXPECTED_ARMS}")
        case: dict[str, Any] = {"arms": {}}
        source_project = None
        protocol = None
        for arm in EXPECTED_ARMS:
            path = arm_paths[arm].resolve()
            payload = json.loads(path.read_text(encoding="utf-8"))
            arm_payload = payload.get("arms", {}).get(arm)
            if not isinstance(arm_payload, dict):
                raise ValueError(f"{case_id}/{arm}: arm missing from {path}")
            if not arm_payload.get("success"):
                raise ValueError(f"{case_id}/{arm}: selected arm is not successful")
            if arm_payload.get("protocol_violation"):
                raise ValueError(f"{case_id}/{arm}: selected arm violates budget")
            budget = int(payload["protocol"]["solver_budget_upper_bound"])
            evaluations = arm_payload.get("physical_solver_evaluations")
            if not isinstance(evaluations, int) or not 1 <= evaluations <= budget:
                raise ValueError(f"{case_id}/{arm}: invalid physical solver count")
            if source_project is None:
                source_project = payload["source_project"]
                protocol = payload["protocol"]
            elif source_project != payload["source_project"] or protocol != payload["protocol"]:
                raise ValueError(f"{case_id}: arm protocols are not paired")
            case["arms"][arm] = {
                "source_report": str(path),
                "source_report_sha256": _sha256(path),
                "physical_solver_evaluations": evaluations,
                "target_met": bool(arm_payload.get("target_met")),
                "improvement_db": arm_payload.get("improvement_db"),
            }
        case["source_project"] = source_project
        case["protocol"] = protocol
        cases[case_id] = case

    arm_summary = {}
    for arm in EXPECTED_ARMS:
        rows = [case["arms"][arm] for case in cases.values()]
        arm_summary[arm] = {
            "case_count": len(rows),
            "target_met_count": sum(bool(row["target_met"]) for row in rows),
            "mean_improvement_db": sum(float(row["improvement_db"]) for row in rows)
            / len(rows),
            "mean_physical_solver_evaluations": sum(
                int(row["physical_solver_evaluations"]) for row in rows
            )
            / len(rows),
        }
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    report = {
        "schema_version": "cst-optimization-canonical-v1",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": commit,
        "case_count": len(cases),
        "cases": cases,
        "summary": arm_summary,
        "honest_boundary": [
            "two cases are development-visible and share one antenna geometry",
            "results support failure-mode analysis, not statistical superiority claims",
            "solver failures consume budget; selected reports exclude infrastructure-invalid runs",
            "all project copies, solver artifacts, logs, and reports are stored on D drive",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected_raw = json.loads(args.selection.read_text(encoding="utf-8"))
    selected = {
        case_id: {arm: Path(path) for arm, path in arms.items()}
        for case_id, arms in selected_raw.items()
    }
    report = aggregate_reports(selected=selected, output_path=args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
