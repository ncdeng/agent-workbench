"""analyze_farfield_cut 单元测试 + reader.build_farfield_cut_result.summary 集成。

合成增益方向图（无 CST 依赖），Gaussian 主瓣 + 可选副瓣，验证 HPBW / F2B /
SLL 计算正确。
"""
from typing import Dict, List

import pytest

from cst_agent_workbench.results.summary import analyze_farfield_cut


def _gaussian_beam(angles: List[float], peak_angle: float, peak_gain: float,
                   sigma_deg: float, baseline_db: float = -30.0) -> List[float]:
    """真高斯方向图：线性功率 P(θ) = P_peak·exp(-(θ-θ₀)²/(2σ²))，转 dB。
    HPBW = 2σ·√(2·ln2) ≈ 2.355σ（标准结果）。下限截到 baseline_db。"""
    out = []
    for a in angles:
        # gain_dB = peak_dB - 4.343 · (θ-θ₀)²/(2σ²)（4.343 = 10/ln10）
        delta = a - peak_angle
        gain_db = peak_gain - 4.342944819 * (delta ** 2) / (2 * sigma_deg ** 2)
        out.append(max(baseline_db, gain_db))
    return out


def _make_cut(angles: List[float], gains: List[float]) -> List[Dict[str, float]]:
    return [{"angle_deg": a, "gain_dbi": g} for a, g in zip(angles, gains)]


# ── 基础边界 ──────────────────────────────────────────────────────────


def test_analyze_farfield_cut_empty_returns_all_none():
    result = analyze_farfield_cut([])
    assert result["peak_gain_dbi"] is None
    assert result["hpbw_deg"] is None


def test_analyze_farfield_cut_too_few_points():
    result = analyze_farfield_cut([{"angle_deg": 0, "gain_dbi": 5}])
    assert result["peak_gain_dbi"] is None


# ── 主瓣定位 ──────────────────────────────────────────────────────────


def test_peak_at_boresight_for_symmetric_gaussian():
    angles = [-90 + i for i in range(181)]  # -90 到 90，1° 步长
    gains = _gaussian_beam(angles, peak_angle=0.0, peak_gain=8.0, sigma_deg=30.0)
    cut = _make_cut(angles, gains)

    result = analyze_farfield_cut(cut)
    assert result["peak_gain_dbi"] == pytest.approx(8.0, abs=0.01)
    assert result["peak_angle_deg"] == pytest.approx(0.0, abs=1.0)


# ── HPBW ──────────────────────────────────────────────────────────────


def test_hpbw_matches_gaussian_formula():
    """高斯主瓣的 HPBW 理论值 ≈ 2.355·sigma。"""
    sigma = 25.0
    angles = [-90 + 0.5 * i for i in range(361)]  # 0.5° 步长保证插值精度
    gains = _gaussian_beam(angles, peak_angle=0.0, peak_gain=10.0, sigma_deg=sigma)

    result = analyze_farfield_cut(_make_cut(angles, gains))
    expected_hpbw = 2.355 * sigma
    # 容差：2° 充分覆盖插值误差
    assert result["hpbw_deg"] == pytest.approx(expected_hpbw, abs=2.0)


def test_hpbw_for_narrow_beam():
    """窄波束 sigma=10° → HPBW ≈ 23.5°。"""
    angles = [-60 + 0.5 * i for i in range(241)]
    gains = _gaussian_beam(angles, 0.0, 15.0, sigma_deg=10.0)
    result = analyze_farfield_cut(_make_cut(angles, gains))
    assert result["hpbw_deg"] == pytest.approx(23.5, abs=1.5)


# ── 前后比 ────────────────────────────────────────────────────────────


def test_front_to_back_for_full_360_pattern():
    """完整 0-360° 数据。Gaussian peak at 0°，背向 180°。"""
    angles = [i for i in range(361)]
    gains = _gaussian_beam(angles, peak_angle=0.0, peak_gain=10.0, sigma_deg=30.0,
                            baseline_db=-25.0)
    result = analyze_farfield_cut(_make_cut(angles, gains))
    # peak 10dBi，背向 ≈ -25 dBi → F2B ≈ 35 dB
    assert result["front_to_back_db"] is not None
    assert result["front_to_back_db"] == pytest.approx(35.0, abs=2.0)


def test_front_to_back_none_when_no_back_angle_in_data():
    """只覆盖 ±90° 的 cut，找不到 peak+180°，应返回 None。"""
    angles = [-90 + i for i in range(181)]
    gains = _gaussian_beam(angles, 0.0, 8.0, 30.0)
    result = analyze_farfield_cut(_make_cut(angles, gains))
    assert result["front_to_back_db"] is None


# ── Side lobe level ───────────────────────────────────────────────────


def test_sidelobe_detected_when_present():
    """主瓣 0°，副瓣 60°。副瓣应被检测到，电平应是负值。"""
    angles = [-90 + i for i in range(181)]
    main = _gaussian_beam(angles, peak_angle=0.0, peak_gain=10.0, sigma_deg=15.0,
                           baseline_db=-30.0)
    side = _gaussian_beam(angles, peak_angle=60.0, peak_gain=-2.0, sigma_deg=10.0,
                           baseline_db=-30.0)
    # dB 不能直接相加，但用最大值（接近真实主+副瓣行为）
    gains = [max(m, s) for m, s in zip(main, side)]

    result = analyze_farfield_cut(_make_cut(angles, gains))
    assert result["sidelobe_level_db"] is not None
    assert result["sidelobe_level_db"] < 0  # 副瓣低于主瓣
    # 副瓣 -2 dBi vs 主瓣 10 dBi → SLL ≈ -12 dB
    assert result["sidelobe_level_db"] == pytest.approx(-12.0, abs=2.0)


def test_no_sidelobe_for_pure_gaussian():
    """单高斯主瓣（无副瓣峰）→ sidelobe_level_db 为 None 或非常深。"""
    angles = [-90 + i for i in range(181)]
    gains = _gaussian_beam(angles, peak_angle=0.0, peak_gain=8.0, sigma_deg=30.0,
                            baseline_db=-40.0)
    result = analyze_farfield_cut(_make_cut(angles, gains))
    # 主瓣外只有单调下降的尾，没有局部极大
    assert (result["sidelobe_level_db"] is None
            or result["sidelobe_level_db"] < -25.0)


# ── reader 集成（确认 summary 字段已注入）──────────────────────────────


def test_build_farfield_cut_result_includes_beam_metrics(monkeypatch):
    """reader.build_farfield_cut_result.summary 应包含 hpbw_deg/front_to_back_db/sidelobe_level_db。"""
    from cst_agent_workbench.results.reader import ResultsReader

    # 用 -180..180 对称区间，peak 在 0°。Gaussian 主瓣不撞边界。
    angles = [-180 + i for i in range(361)]
    gains = _gaussian_beam(angles, peak_angle=0.0, peak_gain=8.0, sigma_deg=30.0)
    plot_data = [{"x": a, "y": g} for a, g in zip(angles, gains)]

    reader = ResultsReader()
    read_result = {"success": True, "plot_data": plot_data}
    cut = reader.build_farfield_cut_result("Farfield Cut φ=0", read_result, "phi", 0.0)

    assert cut["success"] is True
    assert "hpbw_deg" in cut["summary"]
    assert "front_to_back_db" in cut["summary"]
    assert "sidelobe_level_db" in cut["summary"]
    assert cut["summary"]["hpbw_deg"] is not None
    # Gaussian σ=30 → HPBW ≈ 2.355·30 = 70.65°
    assert cut["summary"]["hpbw_deg"] == pytest.approx(70.65, abs=3.0)


def test_circular_data_with_peak_near_zero_handles_wrap():
    """0..360° 数据、peak 在 0° 附近时，HPBW 应通过环绕 wrap 正确计算。
    用 _circular_gaussian 让生成器自身正确处理 θ=0 与 θ=360 等价。"""
    angles = [float(i) for i in range(361)]

    def circ_gauss(a, peak_a, peak_g, sigma):
        # 角度差取最短弧（在 360° 圆上）
        diff = (a - peak_a + 540.0) % 360.0 - 180.0
        return peak_g - 4.342944819 * (diff ** 2) / (2 * sigma ** 2)

    gains = [max(-30.0, circ_gauss(a, peak_a=2.0, peak_g=10.0, sigma=20.0)) for a in angles]
    cut = _make_cut(angles, gains)
    result = analyze_farfield_cut(cut)

    # 主瓣中心 2°，σ=20 → HPBW ≈ 47°
    assert result["peak_angle_deg"] == pytest.approx(2.0, abs=0.5)
    assert result["hpbw_deg"] == pytest.approx(47.0, abs=3.0)
