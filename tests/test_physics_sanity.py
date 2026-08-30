"""Physics sanity baselines for the three fast paths (A-1).

Goal: catch obviously-broken synthesize_*() output without needing to run
CST. Two layers:

1. Wavelength-ratio sanity: ratios like patch_w/λ₀, feed_w/patch_w must fall
   inside published physical ranges. Independent of the specific Hammerstad
   formula choice — any reasonable microstrip patch implementation should
   satisfy these.

2. Numeric reference for one well-known case: 2.4GHz patch on FR-4 (εr=4.4,
   h=1.6mm) is a standard textbook example (Balanis Example 14.1) with
   widely-cited dimensions patch_w ≈ 38mm, patch_l ≈ 29mm.

Tolerances are wide-by-default (±10% on absolute values, generous bands on
ratios) so this isn't a regression test on our specific formula choice —
it's a "did we accidentally produce nonsense" gate. Snapshot tests
(test_vba_snapshot_*) handle exact-output regression separately.

Pure computation, no CST dependency.
"""

import pytest

from cst_agent_workbench.cst.dipole_fast import DipoleRequest, synthesize_dipole
from cst_agent_workbench.cst.pixel_patch import PixelPatchConfig
from cst_agent_workbench.cst.rectangular_patch_fast import (
    RectangularPatchRequest,
    synthesize_probe_position,
    synthesize_rectangular_patch,
)

C_MM_PER_NS = 299.792458  # 真空光速，mm/ns（GHz·mm 系统的基本常数）


# ── Rectangular patch ────────────────────────────────────────────────────


# (case_name, f0_ghz, epsilon_r, substrate_thickness_mm)
RECT_CASES = [
    ("FR4_2p4GHz", 2.4, 4.4, 1.6),
    ("Rogers5880_9p4GHz", 9.4, 2.2, 0.787),
    ("Rogers4350_5p8GHz", 5.8, 3.66, 0.762),
]


def _rect_dims(f0_ghz, epsilon_r, h):
    request = RectangularPatchRequest(
        f0_ghz=f0_ghz,
        substrate_name="any",
        epsilon_r=epsilon_r,
        loss_tangent=0.02,
        substrate_thickness_mm=h,
        conductor_name="Copper (annealed)",
        conductor_thickness_mm=0.035,
        feed_strategy="microstrip",
    )
    dims = synthesize_rectangular_patch(request)
    lambda0 = C_MM_PER_NS / f0_ghz
    return dims, lambda0


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_patch_w_lambda_ratio_in_range(name, f0, er, h):
    """patch_w / λ₀ 应该在 [0.25, 0.50]：宽度大约 λ₀/2 / sqrt((εr+1)/2)，
    对常见 εr 取值落入此范围。"""
    dims, lambda0 = _rect_dims(f0, er, h)
    ratio = dims["patch_w"] / lambda0
    assert 0.25 <= ratio <= 0.50, (
        f"{name}: patch_w/λ₀ = {ratio:.3f}, expected 0.25..0.50"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_patch_l_lambda_ratio_in_range(name, f0, er, h):
    """patch_l / λ₀ 应该在 [0.18, 0.40]：长度约 λ₀/(2·sqrt(εeff))，
    高 εr 偏小，低 εr 偏大；考虑边缘延伸修正后取此范围。"""
    dims, lambda0 = _rect_dims(f0, er, h)
    ratio = dims["patch_l"] / lambda0
    assert 0.18 <= ratio <= 0.40, (
        f"{name}: patch_l/λ₀ = {ratio:.3f}, expected 0.18..0.40"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_feed_width_to_patch_width_ratio(name, f0, er, h):
    """50Ω 微带馈线宽度 vs patch 宽度，常见值 0.05..0.25。"""
    dims, _ = _rect_dims(f0, er, h)
    ratio = dims["feed_w"] / dims["patch_w"]
    assert 0.04 <= ratio <= 0.25, (
        f"{name}: feed_w/patch_w = {ratio:.3f}, expected 0.04..0.25"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_inset_depth_within_patch(name, f0, er, h):
    """inset_depth 必须在 patch 长度的 [0.10, 0.50]：太小匹配阻抗高，
    太大穿透 patch 后缘，物理上失去意义。"""
    dims, _ = _rect_dims(f0, er, h)
    ratio = dims["inset_depth"] / dims["patch_l"]
    assert 0.10 <= ratio <= 0.50, (
        f"{name}: inset_depth/patch_l = {ratio:.3f}, expected 0.10..0.50"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_substrate_h_below_surface_wave_threshold(name, f0, er, h):
    """h/λ₀ > 0.15 容易激发表面波，恶化带宽。常见薄基板设计远低于此阈。"""
    _, lambda0 = _rect_dims(f0, er, h)
    ratio = h / lambda0
    assert ratio < 0.15, (
        f"{name}: h/λ₀ = {ratio:.4f} ≥ 0.15 (surface wave risk)"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_all_dimensions_positive(name, f0, er, h):
    """所有几何尺寸必须为正数，否则 builder 会生成无效 VBA。"""
    dims, _ = _rect_dims(f0, er, h)
    for key in ("patch_w", "patch_l", "feed_w", "feed_l", "inset_depth",
                "sub_w", "sub_l", "ground_w", "ground_l"):
        assert dims[key] > 0, f"{name}: dim {key} = {dims[key]} not positive"


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_substrate_larger_than_patch(name, f0, er, h):
    """sub_w / sub_l 必须大于 patch_w / patch_l，否则 patch 超出基板。"""
    dims, _ = _rect_dims(f0, er, h)
    assert dims["sub_w"] > dims["patch_w"]
    assert dims["sub_l"] > dims["patch_l"]


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_rect_frequency_range_brackets_f0(name, f0, er, h):
    """fmin < f0 < fmax，用于 CST 频率扫描。"""
    dims, _ = _rect_dims(f0, er, h)
    assert dims["fmin"] < f0 < dims["fmax"]


def test_rect_balanis_example_2p4ghz_fr4_absolute_values():
    """2.4GHz FR-4 patch 的绝对尺寸参照 Balanis Antenna Theory Example 14.1：
    patch_w ≈ 38mm, patch_l ≈ 29mm。容差 ±10% 覆盖不同书目和工艺差异。"""
    dims, _ = _rect_dims(2.4, 4.4, 1.6)
    assert 34.2 < dims["patch_w"] < 41.8, (
        f"patch_w = {dims['patch_w']:.2f}, expected 38mm ±10%"
    )
    assert 26.1 < dims["patch_l"] < 31.9, (
        f"patch_l = {dims['patch_l']:.2f}, expected 29mm ±10%"
    )


# ── Dipole ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("f0", [1.0, 2.4, 5.8])
def test_dipole_arm_length_matches_classic_formula(f0):
    """半波振子臂长公式：arm_length = 0.235·λ₀（经验修正后）。
    实现照此公式，所以独立计算与实现应严格一致到浮点精度。"""
    req = DipoleRequest(f0_ghz=f0)
    dims = synthesize_dipole(req)
    lambda0 = C_MM_PER_NS / f0
    expected = 0.235 * lambda0
    assert dims["arm_length"] == pytest.approx(expected, rel=1e-9)


@pytest.mark.parametrize("f0", [1.0, 2.4, 5.8])
def test_dipole_total_length_in_resonant_band(f0):
    """振子总长 2·arm = 0.47·λ₀，落在半波振子谐振区 [0.45, 0.50]。"""
    req = DipoleRequest(f0_ghz=f0)
    dims = synthesize_dipole(req)
    lambda0 = C_MM_PER_NS / f0
    ratio = 2 * dims["arm_length"] / lambda0
    assert 0.45 <= ratio <= 0.50, (
        f"dipole {f0}GHz: 2*arm/λ₀ = {ratio:.4f}, expected 0.45..0.50"
    )


@pytest.mark.parametrize("f0", [1.0, 2.4, 5.8])
def test_dipole_arm_geometry_positive(f0):
    """所有几何尺寸为正数。"""
    dims = synthesize_dipole(DipoleRequest(f0_ghz=f0))
    for key in ("arm_length", "arm_width", "gap", "lambda0_mm"):
        assert dims[key] > 0


@pytest.mark.parametrize("f0", [1.0, 2.4, 5.8])
def test_dipole_gap_smaller_than_arm(f0):
    """馈电间隙必须远小于臂长，否则不再是"半波"振子。"""
    dims = synthesize_dipole(DipoleRequest(f0_ghz=f0))
    assert dims["gap"] < dims["arm_length"] * 0.1


# ── Pixel patch ──────────────────────────────────────────────────────────


def test_pixel_grid_size_is_rows_times_cell():
    """像素贴片无解析公式，总尺寸应等于 (n_rows·cell_size, n_cols·cell_size)。"""
    config = PixelPatchConfig(
        f0_ghz=5.8, n_rows=8, n_cols=6, cell_size_mm=2.5,
    )
    w, l = config.grid_size_mm
    assert w == pytest.approx(8 * 2.5)
    assert l == pytest.approx(6 * 2.5)


def test_pixel_feed_position_at_center():
    """馈点固定在阵列中心：feed_row = n_rows//2, feed_col = n_cols//2。"""
    config = PixelPatchConfig(f0_ghz=5.8, n_rows=8, n_cols=8, cell_size_mm=3.0)
    assert config.feed_row == 4
    assert config.feed_col == 4


# ── Probe-fed (coax) patch ──────────────────────────────────────────────


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_probe_y_offset_inside_patch(name, f0, er, h):
    """探针位置必须落在 patch 内部（|probe_y| < patch_l/2）。"""
    dims, _ = _rect_dims(f0, er, h)
    probe = synthesize_probe_position(dims)
    assert abs(probe["probe_y_offset"]) < dims["patch_l"] / 2, (
        f"{name}: probe_y_offset={probe['probe_y_offset']:.3f} "
        f"超出 patch_l/2={dims['patch_l']/2:.3f}"
    )


@pytest.mark.parametrize("name,f0,er,h", RECT_CASES)
def test_probe_y_from_edge_in_matching_band(name, f0, er, h):
    """y_from_edge / patch_l 应在 [0.20, 0.45]：
    50Ω 匹配通常在 patch 长度的 1/3 ~ 2/5 处取得，落入此带说明阻抗模型合理。"""
    dims, _ = _rect_dims(f0, er, h)
    probe = synthesize_probe_position(dims)
    ratio = probe["y_from_edge"] / dims["patch_l"]
    assert 0.20 <= ratio <= 0.45, (
        f"{name}: y_from_edge/patch_l={ratio:.3f}, 期望 0.20..0.45"
    )


def test_probe_ground_hole_clearance():
    """ground hole 半径必须严格大于探针半径（保证电气隔离）。"""
    dims, _ = _rect_dims(2.4, 4.4, 1.6)
    probe = synthesize_probe_position(dims, probe_radius_mm=0.5)
    assert probe["ground_hole_radius_mm"] > probe["probe_radius_mm"]


def test_probe_position_rejects_invalid_inputs():
    dims, _ = _rect_dims(2.4, 4.4, 1.6)
    with pytest.raises(ValueError):
        synthesize_probe_position(dims, probe_radius_mm=0)
    with pytest.raises(ValueError):
        synthesize_probe_position(dims, edge_resistance_ohm=49)


def test_pixel_total_size_versus_wavelength_at_resonance():
    """对 5.8GHz εr=4.4 h=1.6mm 的 8×8 阵列，总边长大致与有效半波长可比。
    用宽松区间避免对 cell_size 默认值过敏。"""
    config = PixelPatchConfig(
        f0_ghz=5.8, epsilon_r=4.4, substrate_thickness_mm=1.6,
        n_rows=8, n_cols=8, cell_size_mm=3.0,
    )
    lambda0 = C_MM_PER_NS / config.f0_ghz
    w, l = config.grid_size_mm
    # 8×3 = 24mm，λ₀ ≈ 51.7mm；阵列约 0.46 λ₀，介于半波前后
    assert 0.30 * lambda0 < w < 0.70 * lambda0
    assert 0.30 * lambda0 < l < 0.70 * lambda0
