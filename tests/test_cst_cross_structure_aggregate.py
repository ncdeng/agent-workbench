from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks import cst_cross_structure_aggregate as aggregate_mod

pytestmark = pytest.mark.windows_d_drive
from benchmarks.cst_solver_evidence import sha256_path


def _write_report(root: Path, *, schema: str, topology_input: dict, success: bool, target_s11: float) -> Path:
    root.mkdir(parents=True)
    project = root / "case.cst"
    curve = root / "curve.json"
    project.write_bytes(b"project")
    curve.write_text('[{"freq":2.4,"s_db":-11}]', encoding="utf-8")
    report = {
        "schema_version": schema,
        "repository_commit": "deadbeef",
        "source_bindings": {"runner": {"path": "runner.py", "sha256_lf": "a" * 64}},
        "success": success,
        "input": topology_input,
        "project_file": str(project),
        "project_sha256": sha256_path(project),
        "s11_curve_file": str(curve),
        "s11_curve_sha256": sha256_path(curve),
        "s11_point_count": 1,
        "elapsed_sec": 10.0,
        "s11": {"min_s11_db": -15.0, "min_freq_ghz": 2.3, "target_s11_db": target_s11},
    }
    path = root / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_cross_structure_aggregate_separates_topology_variant_and_target(monkeypatch, tmp_path):
    monkeypatch.setattr(aggregate_mod, "verify_source_bindings", lambda *_args, **_kwargs: None)
    dipole = _write_report(
        tmp_path / "dipole",
        schema="cst-dipole-solver-smoke-v2",
        topology_input={"f0_ghz": 2.4, "wire_or_plate": "plate"},
        success=True,
        target_s11=-11.0,
    )
    patch = _write_report(
        tmp_path / "patch",
        schema="cst-patch-solver-smoke-v2",
        topology_input={"f0_ghz": 9.4, "feed_strategy": "microstrip"},
        success=False,
        target_s11=-12.0,
    )

    aggregate = aggregate_mod.aggregate_reports(
        [dipole, patch],
        output_path=tmp_path / "aggregate.json",
        repo_root=Path(__file__).resolve().parents[1],
    )

    assert aggregate["topology_count"] == 2
    assert aggregate["geometry_variant_count"] == 2
    assert aggregate["summary"]["execution_success_count"] == 1
    assert aggregate["summary"]["target_metric_met_count"] == 2
    assert aggregate["summary"]["target_success_count"] == 1
    assert aggregate["summary"]["by_topology"]["dipole"]["case_count"] == 1
    assert aggregate["summary"]["by_topology"]["rectangular_patch"]["case_count"] == 1


def test_cross_structure_aggregate_requires_two_topologies(monkeypatch, tmp_path):
    monkeypatch.setattr(aggregate_mod, "verify_source_bindings", lambda *_args, **_kwargs: None)
    dipole = _write_report(
        tmp_path / "dipole",
        schema="cst-dipole-solver-smoke-v2",
        topology_input={"f0_ghz": 2.4, "wire_or_plate": "wire"},
        success=True,
        target_s11=-11.0,
    )

    with pytest.raises(ValueError, match="至少需要两个 topology"):
        aggregate_mod.aggregate_reports(
            [dipole],
            output_path=tmp_path / "aggregate.json",
            repo_root=Path(__file__).resolve().parents[1],
        )
