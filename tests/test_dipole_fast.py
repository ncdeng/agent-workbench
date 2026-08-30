"""Tests for dipole_fast.py"""

from cst_agent_workbench.cst.dipole_fast import DipoleRequest, synthesize_dipole, build_dipole_vba


def test_arm_length_formula():
    """arm_length 应等于 0.235 * λ₀，误差 < 0.01 mm。"""
    f0_ghz = 2.4
    req = DipoleRequest(f0_ghz=f0_ghz)
    dims = synthesize_dipole(req)
    lambda0_mm = 299.792458 / f0_ghz
    expected_arm_length = 0.235 * lambda0_mm
    assert abs(dims["arm_length"] - expected_arm_length) < 0.01


def test_arm_length_less_than_half_wavelength():
    """arm_length * 2 < λ₀ / 2（振子总长不超过半波长）。"""
    f0_ghz = 9.4
    req = DipoleRequest(f0_ghz=f0_ghz)
    dims = synthesize_dipole(req)
    lambda0_mm = 299.792458 / f0_ghz
    # arm_length * 2 是双臂总长，应小于半波长 λ₀/2
    assert dims["arm_length"] * 2 < lambda0_mm / 2


def test_build_dipole_vba_contains_brick_and_discreteport():
    """build_dipole_vba 返回字符串需包含 'Brick' 和 'DiscretePort'。"""
    f0_ghz = 5.8
    req = DipoleRequest(f0_ghz=f0_ghz)
    dims = synthesize_dipole(req)
    vba = build_dipole_vba(req, dims)
    assert "Brick" in vba
    assert "DiscretePort" in vba
