"""diagnose_antenna 规则式诊断测试。

合成各种 S11 / farfield 场景，验证规则正确触发对应 issue 和 suggestion。
"""
from typing import List


from cst_agent_workbench.results.summary import (
    S11Summary,
    diagnose_antenna,
    summarize_s11_result,
)


def _lorentzian_dip(freqs: List[float], f0: float, depth_db: float, q: float = 30.0) -> List[float]:
    out = []
    for f in freqs:
        x = 2.0 * q * (f - f0) / f0
        shape = 1.0 / (1.0 + x * x)
        out.append(depth_db * shape)
    return out


def _make_s11_summary(freqs: List[float], s_db: List[float], target_freq: float = 0.0) -> S11Summary:
    plot_data = [{"freq": f, "s_db": s} for f, s in zip(freqs, s_db)]
    return summarize_s11_result({"plot_data": plot_data}, target_freq)


# ── S11 诊断 ─────────────────────────────────────────────────────────


def test_no_s11_data_returns_data_missing_issue():
    summary = S11Summary(plot_data=None)
    result = diagnose_antenna(summary)
    assert result["ok"] is False
    assert any("数据缺失" in i for i in result["issues"])


def test_no_resonance_detected_when_flat_s11():
    """S11 全程在 -1 ~ 0 dB，没有谐振"""
    freqs = [2.0 + 0.05 * i for i in range(21)]
    s_db = [-0.5] * len(freqs)
    summary = _make_s11_summary(freqs, s_db)
    result = diagnose_antenna(summary, target_freq_ghz=2.4)
    assert result["ok"] is False
    assert any("未检测到" in i for i in result["issues"])


def test_resonance_off_target_high():
    """谐振在 2.6 GHz，目标 2.4 GHz → 偏高"""
    freqs = [2.0 + 0.01 * i for i in range(81)]
    s_db = _lorentzian_dip(freqs, f0=2.6, depth_db=-25.0, q=30.0)
    summary = _make_s11_summary(freqs, s_db, target_freq=2.4)
    result = diagnose_antenna(summary, target_freq_ghz=2.4)
    assert any("偏高" in i for i in result["issues"])
    assert any("增加 patch_L" in s for s in result["suggestions"])
    assert result["details"]["freq_offset_pct"] > 5


def test_resonance_off_target_low():
    """谐振在 2.2 GHz，目标 2.4 GHz → 偏低"""
    freqs = [2.0 + 0.01 * i for i in range(81)]
    s_db = _lorentzian_dip(freqs, f0=2.2, depth_db=-25.0, q=30.0)
    summary = _make_s11_summary(freqs, s_db, target_freq=2.4)
    result = diagnose_antenna(summary, target_freq_ghz=2.4)
    assert any("偏低" in i for i in result["issues"])
    assert any("减小 patch_L" in s for s in result["suggestions"])


def test_resonance_on_target_with_good_depth_passes():
    """谐振在目标频率附近、深度 -25 dB，应该 ok。"""
    freqs = [2.0 + 0.005 * i for i in range(161)]
    s_db = _lorentzian_dip(freqs, f0=2.4, depth_db=-25.0, q=20.0)
    summary = _make_s11_summary(freqs, s_db, target_freq=2.4)
    result = diagnose_antenna(summary, target_freq_ghz=2.4)
    assert result["ok"] is True
    assert result["issues"] == []


def test_shallow_resonance_flagged():
    """有谐振但只 -5 dB，达不到 -10 dB 目标"""
    freqs = [2.0 + 0.01 * i for i in range(81)]
    s_db = _lorentzian_dip(freqs, f0=2.4, depth_db=-5.0, q=20.0)
    summary = _make_s11_summary(freqs, s_db, target_freq=2.4)
    result = diagnose_antenna(summary, target_freq_ghz=2.4, target_s11_db=-10.0)
    assert any("深度不达标" in i for i in result["issues"])
    assert any("馈线" in s or "inset" in s or "probe" in s for s in result["suggestions"])


# ── farfield 诊断 ───────────────────────────────────────────────────


def _good_s11(target=2.4):
    freqs = [target - 0.4 + 0.005 * i for i in range(161)]
    s_db = _lorentzian_dip(freqs, f0=target, depth_db=-25.0, q=20.0)
    return _make_s11_summary(freqs, s_db, target_freq=target)


def test_low_front_to_back_flagged():
    summary = _good_s11()
    metrics = {"peak_gain_dbi": 7.0, "front_to_back_db": 6.5,
               "hpbw_deg": 70.0, "sidelobe_level_db": -15.0}
    result = diagnose_antenna(summary, farfield_metrics=metrics, target_freq_ghz=2.4)
    assert any("前后比偏低" in i for i in result["issues"])
    assert any("ground_W" in s or "ground_L" in s for s in result["suggestions"])


def test_low_peak_gain_flagged():
    summary = _good_s11()
    metrics = {"peak_gain_dbi": 2.5, "front_to_back_db": 18.0, "hpbw_deg": 70.0}
    result = diagnose_antenna(summary, farfield_metrics=metrics, target_freq_ghz=2.4)
    assert any("增益偏低" in i for i in result["issues"])


def test_high_sidelobe_flagged():
    summary = _good_s11()
    metrics = {"peak_gain_dbi": 7.0, "front_to_back_db": 18.0,
               "hpbw_deg": 70.0, "sidelobe_level_db": -7.0}
    result = diagnose_antenna(summary, farfield_metrics=metrics, target_freq_ghz=2.4)
    assert any("副瓣电平偏高" in i for i in result["issues"])


def test_too_wide_beam_flagged():
    summary = _good_s11()
    metrics = {"peak_gain_dbi": 7.0, "front_to_back_db": 18.0, "hpbw_deg": 150.0}
    result = diagnose_antenna(summary, farfield_metrics=metrics, target_freq_ghz=2.4)
    assert any("波束过宽" in i for i in result["issues"])


def test_all_good_returns_ok():
    summary = _good_s11()
    metrics = {"peak_gain_dbi": 7.5, "front_to_back_db": 20.0,
               "hpbw_deg": 75.0, "sidelobe_level_db": -18.0}
    result = diagnose_antenna(summary, farfield_metrics=metrics, target_freq_ghz=2.4)
    assert result["ok"] is True
    assert result["issues"] == []
