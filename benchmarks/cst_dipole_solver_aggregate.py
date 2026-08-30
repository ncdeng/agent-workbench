"""Verify and aggregate real CST dipole solver reports without rerunning CST."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from benchmarks.cst_solver_evidence import sha256_path, verify_source_bindings
except ModuleNotFoundError:  # direct: python benchmarks/cst_dipole_solver_aggregate.py
    from cst_solver_evidence import sha256_path, verify_source_bindings

def _verified_case(report_path: Path, *, repo_root: Path, target_db: float) -> dict[str, Any]:
    report_path = report_path.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema_version") != "cst-dipole-solver-smoke-v2":
        raise ValueError(f"unsupported report schema: {report_path}")

    project_path = Path(str(report.get("project_file") or ""))
    curve_path = Path(str(report.get("s11_curve_file") or ""))
    if not project_path.is_file() or sha256_path(project_path) != report.get("project_sha256"):
        raise ValueError(f"project artifact mismatch: {report_path}")
    if not curve_path.is_file() or sha256_path(curve_path) != report.get("s11_curve_sha256"):
        raise ValueError(f"S11 curve artifact mismatch: {report_path}")
    verify_source_bindings(
        repo_root,
        commit=str(report.get("repository_commit") or ""),
        bindings=report.get("source_bindings") or {},
    )

    input_data = report.get("input") or {}
    s11 = report.get("s11") or {}
    f0 = float(input_data["f0_ghz"])
    min_freq = float(s11["min_freq_ghz"])
    target_s11 = float(s11["target_s11_db"])
    execution_success = bool(report.get("success"))
    target_metric_met = target_s11 <= target_db
    return {
        "case_id": f"dipole_{input_data['wire_or_plate']}_{f0:g}ghz",
        "report_file": str(report_path),
        "report_sha256": sha256_path(report_path),
        "structure": input_data["wire_or_plate"],
        "f0_ghz": f0,
        "execution_success": execution_success,
        "target_s11_db": target_s11,
        "target_metric_met": target_metric_met,
        "target_eligible": execution_success,
        "target_met": execution_success and target_metric_met,
        "min_s11_db": float(s11["min_s11_db"]),
        "min_freq_ghz": min_freq,
        "resonance_error_pct": 100.0 * (min_freq - f0) / f0,
        "bandwidth_ghz": s11.get("bandwidth_ghz"),
        "s11_point_count": int(report.get("s11_point_count") or 0),
        "elapsed_sec": float(report.get("elapsed_sec") or 0.0),
        "project_sha256": report["project_sha256"],
        "s11_curve_sha256": report["s11_curve_sha256"],
    }


def aggregate_reports(
    report_paths: Iterable[Path],
    *,
    output_path: Path,
    repo_root: Path,
    target_db: float = -10.0,
) -> dict[str, Any]:
    output_path = output_path.resolve()
    if output_path.drive.upper() != "D:":
        raise ValueError("dipole aggregate 必须写入 D 盘")
    if output_path.exists():
        raise ValueError(f"aggregate 已存在，拒绝覆盖: {output_path}")
    cases = [_verified_case(path, repo_root=repo_root.resolve(), target_db=target_db) for path in report_paths]
    if not cases:
        raise ValueError("至少需要一份 dipole solver report")
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("dipole aggregate case_id 必须唯一")

    execution_passes = sum(case["execution_success"] for case in cases)
    target_passes = sum(case["target_met"] for case in cases)
    aggregate = {
        "schema_version": "cst-dipole-solver-aggregate-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_contract": {"metric": "S11@f0", "operator": "<=", "threshold_db": target_db},
        "case_count": len(cases),
        "execution_success_count": execution_passes,
        "execution_success_rate": execution_passes / len(cases),
        "target_success_count": target_passes,
        "target_success_rate": target_passes / len(cases),
        "mean_abs_resonance_error_pct": sum(abs(case["resonance_error_pct"]) for case in cases) / len(cases),
        "mean_elapsed_sec": sum(case["elapsed_sec"] for case in cases) / len(cases),
        "cases": cases,
        "aggregator_sha256": sha256_path(Path(__file__)),
        "limitations": [
            "These are three developer-selected synthetic cases, not a blinded or representative antenna benchmark.",
            "Execution success requires solver, non-empty S11, saved D-drive project, and clean close.",
            "Target success additionally requires execution success; a numeric S11 threshold hit from an incomplete run is not counted.",
            "No mesh-convergence or independent geometry/port validity study is included.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    return aggregate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-db", type=float, default=-10.0)
    args = parser.parse_args()
    aggregate = aggregate_reports(
        args.report,
        output_path=args.output,
        repo_root=Path(__file__).resolve().parents[1],
        target_db=args.target_db,
    )
    print(json.dumps({
        "success": aggregate["execution_success_count"] == aggregate["case_count"],
        "execution": f"{aggregate['execution_success_count']}/{aggregate['case_count']}",
        "target": f"{aggregate['target_success_count']}/{aggregate['case_count']}",
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if aggregate["execution_success_count"] == aggregate["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
