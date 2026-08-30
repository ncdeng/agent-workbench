"""find_resonances 单元测试 + S11Summary.resonances 集成验证。

合成 S11 曲线（无 CST 依赖）：用对数 dB 形式构造单/多谐振，验证检测正确性。
"""
from typing import List, Dict

import pytest

from cst_agent_workbench.results.summary import find_resonances, summarize_s11_result


def _lorentzian_dip(freqs: List[float], f0: float, depth_db: float, q_factor: float = 30.0) -> List[float]:
    """以 f0 为中心、给定深度和品质因数生成一个 dB 形式的 dip。
    深度处恰好为 depth_db；远离 f0 时回到 0 dB（baseline）。"""
    out = []
    for f in freqs:
        # 标准 Lorentzian: shape = 1 / (1 + (2Q(f-f0)/f0)^2)
        x = 2.0 * q_factor * (f - f0) / f0
        shape = 1.0 / (1.0 + x * x)  # ∈ [0, 1]，f=f0 处=1
        # 转 dB：在 f0 处达到 depth_db，远离时回 0
        # 使用 db = depth_db * shape 让 dip 形状与 shape 一一对应
        db = depth_db * shape
        out.append(db)
    return out


def _make_plot_data(freqs: List[float], s_db: List[float]) -> List[Dict[str, float]]:
    return [{"freq": f, "s_db": s} for f, s in zip(freqs, s_db)]


# ── find_resonances 基础行为 ─────────────────────────────────────────────


def test_find_resonances_empty_input():
    assert find_resonances([]) == []


def test_find_resonances_too_few_points():
    """少于 3 个点无法判断局部极小，返回空。"""
    plot = _make_plot_data([1.0, 2.0], [-15.0, -20.0])
    assert find_resonances(plot) == []


def test_find_resonances_single_dip():
    freqs = [2.0 + 0.01 * i for i in range(81)]  # 2.0 ~ 2.8 GHz
    s_db = _lorentzian_dip(freqs, f0=2.4, depth_db=-25.0, q_factor=30.0)
    plot = _make_plot_data(freqs, s_db)

    resonances = find_resonances(plot)

    assert len(resonances) == 1
    assert resonances[0]["freq_ghz"] == pytest.approx(2.4, abs=0.02)
    assert resonances[0]["depth_db"] == pytest.approx(-25.0, abs=0.5)


def test_find_resonances_two_dips():
    """双谐振：2.4GHz 和 5.2GHz，应都被检出。"""
    freqs = [1.5 + 0.02 * i for i in range(251)]  # 1.5 ~ 6.5 GHz
    dip1 = _lorentzian_dip(freqs, 2.4, -20.0, q_factor=20.0)
    dip2 = _lorentzian_dip(freqs, 5.2, -15.0, q_factor=25.0)
    s_db = [a + b for a, b in zip(dip1, dip2)]
    plot = _make_plot_data(freqs, s_db)

    resonances = find_resonances(plot)

    assert len(resonances) == 2
    assert resonances[0]["freq_ghz"] == pytest.approx(2.4, abs=0.05)
    assert resonances[1]["freq_ghz"] == pytest.approx(5.2, abs=0.05)
    # 频率升序
    assert resonances[0]["freq_ghz"] < resonances[1]["freq_ghz"]


def test_find_resonances_filters_by_min_depth():
    """浅 dip（-2 dB）不应当作谐振，深 dip（-15 dB）应当。"""
    freqs = [2.0 + 0.01 * i for i in range(81)]
    shallow = _lorentzian_dip(freqs, 2.2, -2.0, q_factor=40.0)
    deep = _lorentzian_dip(freqs, 2.6, -15.0, q_factor=40.0)
    s_db = [a + b for a, b in zip(shallow, deep)]
    plot = _make_plot_data(freqs, s_db)

    resonances = find_resonances(plot, min_depth_db=-3.0)

    assert len(resonances) == 1
    assert resonances[0]["freq_ghz"] == pytest.approx(2.6, abs=0.05)


def test_find_resonances_separation_filter_dedupes_close_minima():
    """两个非常近的局部最小（频率间隔 < min_separation_ghz）应只保留更深的那个。"""
    # 在频率 2.40 和 2.42 各放一个 dip，间隔仅 0.02
    freqs = [2.0 + 0.005 * i for i in range(161)]
    dip_a = _lorentzian_dip(freqs, 2.40, -10.0, q_factor=80.0)
    dip_b = _lorentzian_dip(freqs, 2.42, -20.0, q_factor=80.0)
    s_db = [a + b for a, b in zip(dip_a, dip_b)]
    plot = _make_plot_data(freqs, s_db)

    # min_separation_ghz=0.05 比两 dip 间隔大，应去重为 1 个（更深的胜出）。
    # 由于两 dip 频率非常近，曲线叠加深度约 -23..-25 dB（不是单条 dip 的 -20）
    resonances = find_resonances(plot, min_separation_ghz=0.05)
    assert len(resonances) == 1
    assert resonances[0]["depth_db"] < -18.0  # 比浅 dip(-10) 深，确实胜出
    assert resonances[0]["freq_ghz"] == pytest.approx(2.42, abs=0.02)


def test_find_resonances_assigns_bandwidth_from_passed_bands():
    """带宽信息从 bands_10db 关联：谐振点落在哪个带内，就拿哪个带的宽度。"""
    freqs = [2.0 + 0.02 * i for i in range(51)]  # 2.0 ~ 3.0
    s_db = _lorentzian_dip(freqs, 2.4, -25.0, q_factor=15.0)
    plot = _make_plot_data(freqs, s_db)

    bands_10db = [(2.32, 2.50)]  # mock 一个 10dB 带宽
    resonances = find_resonances(plot, bands_10db=bands_10db)

    assert len(resonances) == 1
    r = resonances[0]
    assert r["bandwidth_10db_ghz"] == pytest.approx(0.18, abs=1e-6)
    assert r["bandwidth_10db_pct"] == pytest.approx(100 * 0.18 / r["freq_ghz"], rel=1e-3)


def test_find_resonances_no_bandwidth_when_outside_passed_bands():
    """谐振不在任何 10dB 带内（深度浅但满足 min_depth_db）→ bandwidth_10db_ghz=None。"""
    freqs = [2.0 + 0.02 * i for i in range(51)]
    s_db = _lorentzian_dip(freqs, 2.4, -5.0, q_factor=15.0)  # 浅 dip
    plot = _make_plot_data(freqs, s_db)

    resonances = find_resonances(plot, min_depth_db=-3.0, bands_10db=[])
    assert len(resonances) == 1
    assert resonances[0]["bandwidth_10db_ghz"] is None
    assert resonances[0]["bandwidth_10db_pct"] is None


# ── S11Summary 集成 ─────────────────────────────────────────────────────


def test_summarize_s11_result_includes_resonances_field():
    """summarize_s11_result 应填充 resonances，且与独立调 find_resonances 一致。"""
    freqs = [1.5 + 0.02 * i for i in range(251)]
    dip1 = _lorentzian_dip(freqs, 2.4, -25.0, q_factor=30.0)
    dip2 = _lorentzian_dip(freqs, 5.5, -18.0, q_factor=40.0)
    s_db = [a + b for a, b in zip(dip1, dip2)]
    plot_data = _make_plot_data(freqs, s_db)

    summary = summarize_s11_result({"plot_data": plot_data})

    assert summary.resonances is not None
    assert len(summary.resonances) == 2
    assert summary.resonances[0]["freq_ghz"] < summary.resonances[1]["freq_ghz"]


def test_summarize_s11_result_resonances_empty_when_no_data():
    summary = summarize_s11_result({"plot_data": []})
    # 数据为空时 resonances 字段也应为 None / [] 一致
    assert summary.resonances in (None, [])


def test_to_dict_includes_resonances_key():
    summary = summarize_s11_result({"plot_data": _make_plot_data([2.0, 2.4, 2.8], [-1, -25, -1])})
    d = summary.to_dict()
    assert "resonances" in d
    assert isinstance(d["resonances"], list)
