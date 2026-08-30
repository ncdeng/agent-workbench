from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FakeCSTCase:
    """一个 fake CST 仿真 case。

    ideal_params 是该 case 的凸碗最优点（谐振 patch_L、匹配 inset_depth、馈线 feed_W）。
    不同 case 的 ideal_params 不同，避免所有 case 共享同一个最优点导致成功率结论不可外推。
    ideal_params 由 make_benchmark_cases 按 seed 确定性生成。
    """

    name: str
    seed: int
    initial_params: dict[str, float]
    target_freq_ghz: float = 9.4
    target_db: float = -10.0
    ideal_params: dict[str, float] = field(
        default_factory=lambda: {"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.8}
    )
    description: str = ""


# ideal_params 各维度的合理取值范围。
# patch_L 决定谐振频率（f ∝ 1/L），inset_depth / feed_W 决定匹配。
# 这三个范围覆盖常见微带天线尺寸，随机化后凸碗中心每个 case 不同。
_IDEAL_PATCH_L_RANGE = (14.5, 17.5)
_IDEAL_INSET_DEPTH_RANGE = (2.4, 3.8)
_IDEAL_FEED_W_RANGE = (2.4, 3.4)


def _sample_ideal_params(rng: random.Random) -> dict[str, float]:
    """按 rng 确定性采样一组 ideal_params。"""
    return {
        "patch_L": round(rng.uniform(*_IDEAL_PATCH_L_RANGE), 4),
        "inset_depth": round(rng.uniform(*_IDEAL_INSET_DEPTH_RANGE), 4),
        "feed_W": round(rng.uniform(*_IDEAL_FEED_W_RANGE), 4),
    }


class FakeCSTSimulator:
    def __init__(self, case: FakeCSTCase):
        self.case = case
        self.params = dict(case.initial_params)
        self._rng = random.Random(case.seed)
        self._freq_bias = self._rng.uniform(-0.006, 0.006)
        self._s11_bias = self._rng.uniform(-0.15, 0.15)

    def snapshot(self) -> dict[str, float]:
        return dict(self.params)

    def apply_delta(self, param: str, delta_mm: float) -> None:
        if param not in self.params:
            return
        bounds = {
            "patch_L": (8.0, 28.0),
            "inset_depth": (0.2, 8.0),
            "feed_W": (0.3, 6.0),
        }
        lo, hi = bounds.get(param, (-math.inf, math.inf))
        self.params[param] = min(max(self.params[param] + float(delta_mm), lo), hi)

    def evaluate(self, params: dict[str, float] | None = None) -> dict[str, Any]:
        p = dict(params or self.params)
        target = self.case.target_freq_ghz
        ideal = self.case.ideal_params
        patch_l = max(float(p["patch_L"]), 0.1)
        min_freq = target * float(ideal["patch_L"]) / patch_l + self._freq_bias
        freq_error = min_freq - target
        inset_error = float(p["inset_depth"]) - float(ideal["inset_depth"])
        feed_error = float(p["feed_W"]) - float(ideal["feed_W"])
        match_penalty = 3.0 * inset_error**2 + 4.0 * feed_error**2
        min_s11 = -18.0 + match_penalty + 4.0 * abs(freq_error) + self._s11_bias
        target_s11 = min_s11 + 48.0 * freq_error**2
        bandwidth = max(0.03, 0.42 - 0.015 * match_penalty - 0.05 * abs(freq_error))
        plot_data = []
        for i in range(41):
            freq = target - 0.5 + i * 0.025
            s_db = min_s11 + 90.0 * (freq - min_freq) ** 2
            plot_data.append({"freq": round(freq, 4), "s_db": round(s_db, 3)})
        return {
            "success": True,
            "min_freq_ghz": round(min_freq, 4),
            "min_s11_db": round(min_s11, 3),
            "target_s11_db": round(target_s11, 3),
            "bandwidth_ghz": round(bandwidth, 4),
            "plot_data": plot_data,
            "met": target_s11 <= self.case.target_db,
        }


def make_benchmark_cases(count: int = 20) -> list[FakeCSTCase]:
    """生成 count 个确定性 benchmark case。

    每个 case 有独立的 ideal_params（凸碗最优点），由 case seed 确定性生成。
    base_cases 的 initial_params 仍按方向分类（freq_too_high / freq_too_low / ...），
    但相对 ideal_params 的偏移方向保留，绝对最优点每个 case 不同。
    """
    base_cases = [
        # (name, 描述, 初始参数相对 ideal_params 的偏移方向)
        # freq_too_high: patch_L 比理想小 ~1.4mm（谐振偏高），其余接近理想
        (
            "freq_too_high",
            {"patch_L_delta": -1.4, "inset_depth_delta": 0.0, "feed_W_delta": 0.0},
            "谐振频率偏高，需要增大 patch_L。",
        ),
        # freq_too_low: patch_L 比理想大 ~1.7mm（谐振偏低）
        (
            "freq_too_low",
            {"patch_L_delta": 1.7, "inset_depth_delta": 0.0, "feed_W_delta": 0.0},
            "谐振频率偏低，需要减小 patch_L。",
        ),
        # poor_inset_match: 谐振接近，inset_depth 偏离最优（偏浅）
        (
            "poor_inset_match",
            {"patch_L_delta": 0.0, "inset_depth_delta": -1.8, "feed_W_delta": 0.0},
            "谐振已接近目标但 inset_depth 偏离最优，匹配较差。",
        ),
        # poor_feed_match: 谐振接近，feed_W 偏小
        (
            "poor_feed_match",
            {"patch_L_delta": 0.0, "inset_depth_delta": 0.0, "feed_W_delta": -1.6},
            "谐振已接近目标但 feed_W 偏小，匹配较差。",
        ),
        # mixed_offset: 频率偏高 + 匹配都偏离
        (
            "mixed_offset",
            {"patch_L_delta": -0.8, "inset_depth_delta": -1.5, "feed_W_delta": -1.2},
            "频率偏高且匹配较差，需要先校频再调匹配。",
        ),
    ]
    cases: list[FakeCSTCase] = []
    for index in range(count):
        base_name, deltas, description = base_cases[index % len(base_cases)]
        # ideal_params 用 case 自己的 seed 生成，保证确定性 + 每 case 不同
        ideal_rng = random.Random(10_000 + index)
        ideal = _sample_ideal_params(ideal_rng)
        # initial_params = ideal + base 方向偏移 + 小扰动
        jitter_rng = random.Random(9000 + index)
        params = {
            "patch_L": ideal["patch_L"] + deltas["patch_L_delta"] + jitter_rng.uniform(-0.12, 0.12),
            "inset_depth": ideal["inset_depth"] + deltas["inset_depth_delta"] + jitter_rng.uniform(-0.08, 0.08),
            "feed_W": ideal["feed_W"] + deltas["feed_W_delta"] + jitter_rng.uniform(-0.06, 0.06),
            "substrate_h": 0.51,
            "copper_t": 0.035,
        }
        cases.append(
            FakeCSTCase(
                name=f"{base_name}_{index:02d}",
                seed=10_000 + index,
                initial_params={key: round(value, 4) for key, value in params.items()},
                ideal_params=ideal,
                description=description,
            )
        )
    return cases
