"""Verify and aggregate dipole and rectangular-patch real CST reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from benchmarks.cst_solver_evidence import sha256_path, verify_source_bindings
except ModuleNotFoundError:  # direct: python benchmarks/cst_cross_structure_aggregate.py
    from cst_solver_evidence import sha256_path, verify_source_bindings


SUPPORTED_SCHEMAS = {
    "cst-dipole-solver-smoke-v2": "dipole",
    "cst-patch-solver-smoke-v2": "rectangular_patch",
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_artifact(report: dict[str, Any], *, path_key: str, sha_key: str, label: str) -> dict[str, str]:
    path = Path(str(report.get(path_key) or ""))
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"missing or empty {label} artifact: {path}")
    actual_sha = sha256_path(path)
    if actual_sha != report.get(sha_key):
        raise ValueError(f"{label} artifact SHA mismatch: {path}")
    return {"role": label, "path": str(path), "sha256": actual_sha}


def _verify_report(report_path: Path, *, repo_root: Path, target_db: float) -> tuple[dict[str, Any], dict[str, Any]]:
    report_path = report_path.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    schema = str(report.get("schema_version") or "")
    topology = SUPPORTED_SCHEMAS.get(schema)
    if topology is None:
        raise ValueError(f"unsupported report schema: {schema}")
    input_data = dict(report.get("input") or {})
    s11 = dict(report.get("s11") or {})
    execution_success = bool(report.get("success"))
    target_value = float(s11["target_s11_db"])
    target_metric_met = target_value <= target_db
    artifacts = [
        _verify_artifact(report, path_key="project_file", sha_key="project_sha256", label="project"),
        _verify_artifact(report, path_key="s11_curve_file", sha_key="s11_curve_sha256", label="s11_curve"),
    ]
    verify_source_bindings(
        repo_root,
        commit=str(report.get("repository_commit") or ""),
        bindings=report.get("source_bindings") or {},
    )
    if topology == "dipole":
        geometry_variant = str(input_data["wire_or_plate"])
    else:
        geometry_variant = f"{input_data['feed_strategy']}_inset_patch"
    case_identity = {
        "topology": topology,
        "geometry_variant": geometry_variant,
        "case_parameters": input_data,
    }
    case = {
        "case_id": f"{topology}_{geometry_variant}_{_canonical_hash(case_identity)[:12]}",
        **case_identity,
        "execution_success": execution_success,
        "target_eligible": execution_success,
        "target_metric_value_db": target_value,
        "target_metric_met": target_metric_met,
        "target_met": execution_success and target_metric_met,
        "min_s11_db": float(s11["min_s11_db"]),
        "min_freq_ghz": float(s11["min_freq_ghz"]),
        "s11_point_count": int(report.get("s11_point_count") or 0),
        "elapsed_sec": float(report.get("elapsed_sec") or 0.0),
        "source_report_sha256": sha256_path(report_path),
    }
    source = {
        "report_file": str(report_path),
        "report_sha256": case["source_report_sha256"],
        "schema_version": schema,
        "repository_commit": report["repository_commit"],
        "source_bindings": report["source_bindings"],
        "artifacts": artifacts,
    }
    return case, source


def _counts(cases: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(cases)
    return {
        "case_count": count,
        "execution_success_count": sum(case["execution_success"] for case in cases),
        "target_eligible_count": sum(case["target_eligible"] for case in cases),
        "target_metric_met_count": sum(case["target_metric_met"] for case in cases),
        "target_success_count": sum(case["target_met"] for case in cases),
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
        raise ValueError("cross-structure aggregate 必须写入 D 盘")
    if output_path.exists():
        raise ValueError(f"aggregate 已存在，拒绝覆盖: {output_path}")
    verified = [_verify_report(path, repo_root=repo_root.resolve(), target_db=target_db) for path in report_paths]
    if not verified:
        raise ValueError("至少需要一份 solver report")
    cases = [item[0] for item in verified]
    source_reports = [item[1] for item in verified]
    case_ids = [case["case_id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("cross-structure aggregate case_id 必须唯一")
    topology_counts = Counter(case["topology"] for case in cases)
    if len(topology_counts) < 2:
        raise ValueError("cross-structure aggregate 至少需要两个 topology")
    by_topology = {
        topology: _counts([case for case in cases if case["topology"] == topology])
        for topology in sorted(topology_counts)
    }
    aggregate = {
        "schema_version": "cst-cross-structure-solver-aggregate-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_contract": {"metric": "S11@f0", "operator": "<=", "threshold_db": target_db},
        "topology_count": len(topology_counts),
        "geometry_variant_count": len({(case["topology"], case["geometry_variant"]) for case in cases}),
        "summary": {**_counts(cases), "by_topology": by_topology},
        "cases": cases,
        "source_reports": source_reports,
        "aggregator": {"path": "benchmarks/cst_cross_structure_aggregate.py", "sha256": sha256_path(Path(__file__))},
        "limitations": [
            "Four developer-selected synthetic cases cover two antenna topologies; they are not blinded or representative.",
            "Dipole plate/wire are geometry variants of one topology, not separate antenna topologies.",
            "Execution and S11@f0 target success are separate endpoints; failed executions are ineligible for target success.",
            "No mesh-convergence or independent geometry/port validity study is included yet.",
            "The rectangular patch is one canonical composite tool whose internal host stages are not separate model tool calls.",
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
    summary = aggregate["summary"]
    print(json.dumps({
        "topologies": aggregate["topology_count"],
        "execution": f"{summary['execution_success_count']}/{summary['case_count']}",
        "target": f"{summary['target_success_count']}/{summary['case_count']}",
        "output": str(args.output.resolve()),
    }, ensure_ascii=False))
    return 0 if summary["execution_success_count"] == summary["case_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
