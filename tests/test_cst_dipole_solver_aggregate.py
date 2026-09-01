from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import cst_dipole_solver_aggregate as aggregate_mod

pytestmark = pytest.mark.windows_d_drive


def _write_case(
    root: Path,
    repo_root: Path,
    *,
    structure: str,
    f0: float,
    target_s11: float,
    success: bool = True,
) -> Path:
    root.mkdir(parents=True)
    project = root / "case.cst"
    curve = root / "curve.json"
    project.write_bytes(b"project")
    curve.write_text('[{"freq":2.4,"s_db":-11}]', encoding="utf-8")
    from benchmarks.cst_dipole_solver_smoke import SOURCE_PATHS
    from benchmarks.cst_solver_evidence import build_source_bindings, repository_commit, sha256_path

    source_bindings = build_source_bindings(repo_root, SOURCE_PATHS)
    report = {
        "schema_version": "cst-dipole-solver-smoke-v2",
        "success": success,
        "input": {"wire_or_plate": structure, "f0_ghz": f0},
        "project_file": str(project),
        "project_sha256": sha256_path(project),
        "s11_curve_file": str(curve),
        "s11_curve_sha256": sha256_path(curve),
        "s11_point_count": 1,
        "elapsed_sec": 10.0,
        "s11": {
            "min_s11_db": -15.0,
            "min_freq_ghz": f0 * 0.95,
            "target_s11_db": target_s11,
            "bandwidth_ghz": 0.1,
        },
        "source_bindings": source_bindings,
        "repository_commit": repository_commit(repo_root),
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def test_aggregate_separates_execution_and_target_success(monkeypatch, tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "aggregate"
    report_a = _write_case(root / "a", repo_root, structure="plate", f0=2.4, target_s11=-11.0)
    report_b = _write_case(root / "b", repo_root, structure="wire", f0=5.8, target_s11=-9.0)
    output = root / "aggregate.json"
    monkeypatch.setattr(aggregate_mod, "verify_source_bindings", lambda *_args, **_kwargs: None)

    aggregate = aggregate_mod.aggregate_reports(
        [report_a, report_b],
        output_path=output,
        repo_root=repo_root,
    )

    assert aggregate["execution_success_count"] == 2
    assert aggregate["target_success_count"] == 1
    assert aggregate["target_success_rate"] == 0.5
    assert output.is_file()


def test_aggregate_does_not_count_target_when_execution_failed(monkeypatch, tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "aggregate-ineligible"
    report = _write_case(
        root / "failed",
        repo_root,
        structure="plate",
        f0=2.4,
        target_s11=-20.0,
        success=False,
    )
    monkeypatch.setattr(aggregate_mod, "verify_source_bindings", lambda *_args, **_kwargs: None)

    aggregate = aggregate_mod.aggregate_reports(
        [report],
        output_path=root / "aggregate.json",
        repo_root=repo_root,
    )

    assert aggregate["execution_success_count"] == 0
    assert aggregate["target_success_count"] == 0
    assert aggregate["cases"][0]["target_metric_met"] is True
    assert aggregate["cases"][0]["target_eligible"] is False
    assert aggregate["cases"][0]["target_met"] is False


def test_aggregate_rejects_tampered_curve(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    root = tmp_path / "aggregate-tamper"
    report_path = _write_case(root / "case", repo_root, structure="plate", f0=2.4, target_s11=-11.0)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    Path(report["s11_curve_file"]).write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="curve artifact mismatch"):
        aggregate_mod.aggregate_reports(
            [report_path],
            output_path=root / "aggregate.json",
            repo_root=repo_root,
        )
