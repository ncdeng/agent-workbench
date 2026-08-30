from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.cst_dipole_optimization_comparison import _target_met


def test_joint_target_requires_execution_resonance_and_s11():
    passing = {"min_freq_ghz": 5.78, "target_s11_db": -12.0}
    off_resonance = {"min_freq_ghz": 5.65, "target_s11_db": -15.0}

    assert _target_met(
        execution_success=True,
        summary=passing,
        target_ghz=5.8,
        target_db=-10.0,
        tolerance_ghz=0.05,
    )
    assert not _target_met(
        execution_success=True,
        summary=off_resonance,
        target_ghz=5.8,
        target_db=-10.0,
        tolerance_ghz=0.05,
    )
    assert not _target_met(
        execution_success=False,
        summary=passing,
        target_ghz=5.8,
        target_db=-10.0,
        tolerance_ghz=0.05,
    )


def test_comparison_module_rejects_c_drive_reports():
    from benchmarks.cst_dipole_optimization_comparison import _load

    path = Path("C:/not-a-real-comparison/report.json")
    with pytest.raises(ValueError, match="must be on D"):
        _load(Path(path))
