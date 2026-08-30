from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cst_agent_workbench.optimization.models import OptimizationTarget, S11Summary


@dataclass(frozen=True)
class S11Diagnosis:
    resonance_shift: str
    matching_quality: str
    recommended_parameter_family: str
    recommended_delta_sign: int
    reason: str
    frequency_error_ghz: float | None = None
    frequency_error_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "resonance_shift": self.resonance_shift,
            "matching_quality": self.matching_quality,
            "recommended_parameter_family": self.recommended_parameter_family,
            "recommended_delta_sign": self.recommended_delta_sign,
            "reason": self.reason,
            "frequency_error_ghz": self.frequency_error_ghz,
            "frequency_error_pct": self.frequency_error_pct,
        }


def diagnose_s11(
    summary: S11Summary | dict[str, Any] | None,
    target: OptimizationTarget | dict[str, Any],
    *,
    frequency_tolerance_pct: float = 2.0,
    no_clear_resonance_db: float = -3.0,
) -> S11Diagnosis:
    s11 = _coerce_summary(summary)
    target_freq = _target_freq(target)
    target_db = _target_db(target)

    resonance_freq, resonance_depth = _reference_resonance(s11, target_freq)
    matching_quality = _matching_quality(s11, target_db)
    if resonance_freq is None or resonance_depth is None or resonance_depth > no_clear_resonance_db:
        return S11Diagnosis(
            resonance_shift="no_clear_resonance",
            matching_quality=matching_quality,
            recommended_parameter_family="mesh_or_solver",
            recommended_delta_sign=0,
            reason="S11 曲线没有明显谐振，优先检查端口连接、边界条件、mesh 或扫频范围，而不是继续黑盒调参。",
        )

    if target_freq <= 0:
        return S11Diagnosis(
            resonance_shift="near_target",
            matching_quality=matching_quality,
            recommended_parameter_family="inset_depth" if matching_quality != "good" else "none",
            recommended_delta_sign=0,
            reason="缺少有效目标频率，只能根据匹配深度做局部匹配调整。",
        )

    frequency_error_ghz = resonance_freq - target_freq
    frequency_error_pct = frequency_error_ghz / target_freq * 100.0
    if frequency_error_pct > frequency_tolerance_pct:
        return S11Diagnosis(
            resonance_shift="too_high",
            matching_quality=matching_quality,
            recommended_parameter_family="patch_L",
            recommended_delta_sign=1,
            reason=(
                f"谐振点 {resonance_freq:.3f} GHz 高于目标 {target_freq:.3f} GHz "
                f"({frequency_error_pct:+.2f}%)，谐振偏高，贴片电尺寸偏小，应优先增大 patch_L 降低谐振频率。"
            ),
            frequency_error_ghz=frequency_error_ghz,
            frequency_error_pct=frequency_error_pct,
        )
    if frequency_error_pct < -frequency_tolerance_pct:
        return S11Diagnosis(
            resonance_shift="too_low",
            matching_quality=matching_quality,
            recommended_parameter_family="patch_L",
            recommended_delta_sign=-1,
            reason=(
                f"谐振点 {resonance_freq:.3f} GHz 低于目标 {target_freq:.3f} GHz "
                f"({frequency_error_pct:+.2f}%)，谐振偏低，贴片电尺寸偏大，应优先减小 patch_L 抬高谐振频率。"
            ),
            frequency_error_ghz=frequency_error_ghz,
            frequency_error_pct=frequency_error_pct,
        )

    if matching_quality != "good":
        return S11Diagnosis(
            resonance_shift="near_target",
            matching_quality=matching_quality,
            recommended_parameter_family="inset_depth",
            recommended_delta_sign=0,
            reason=(
                f"谐振点 {resonance_freq:.3f} GHz 已接近目标 {target_freq:.3f} GHz "
                f"({frequency_error_pct:+.2f}%)，频率位置先不大动，优先调 inset_depth/feed_W 改善匹配深度。"
            ),
            frequency_error_ghz=frequency_error_ghz,
            frequency_error_pct=frequency_error_pct,
        )

    return S11Diagnosis(
        resonance_shift="near_target",
        matching_quality="good",
        recommended_parameter_family="none",
        recommended_delta_sign=0,
        reason="谐振频率和目标频点匹配均已满足当前目标，无需继续调参。",
        frequency_error_ghz=frequency_error_ghz,
        frequency_error_pct=frequency_error_pct,
    )


def _coerce_summary(summary: S11Summary | dict[str, Any] | None) -> S11Summary:
    if isinstance(summary, S11Summary):
        return summary
    data = summary or {}
    return S11Summary(
        min_freq_ghz=_coerce_float(_first_present(data, "min_freq_ghz", "min_freq")),
        min_s11_db=_coerce_float(_first_present(data, "min_s11_db", "min_s11")),
        target_s11_db=_coerce_float(_first_present(data, "target_s11_db", "at_f0_s11_db", "at_f0_s11")),
        resonances=list(data.get("resonances") or []),
    )


def _reference_resonance(summary: S11Summary, target_freq: float) -> tuple[float | None, float | None]:
    candidates = []
    for resonance in summary.resonances or []:
        freq = _coerce_float(resonance.get("freq_ghz"))
        depth = _coerce_float(resonance.get("depth_db"))
        if freq is not None and depth is not None:
            candidates.append((freq, depth))
    if candidates and target_freq > 0:
        return min(candidates, key=lambda item: abs(item[0] - target_freq))
    if candidates:
        return min(candidates, key=lambda item: item[1])
    return summary.min_freq_ghz, summary.min_s11_db


def _target_freq(target: OptimizationTarget | dict[str, Any]) -> float:
    if isinstance(target, OptimizationTarget):
        return float(target.target_freq_ghz or 0.0)
    return _coerce_float(_first_present(target, "target_freq_ghz", "effective_target_freq_ghz", "target_freq")) or 0.0


def _target_db(target: OptimizationTarget | dict[str, Any]) -> float:
    if isinstance(target, OptimizationTarget):
        return float(target.target_db)
    return _coerce_float((target or {}).get("target_db")) or -10.0


def _matching_quality(summary: S11Summary, target_db: float) -> str:
    if summary.target_s11_db is not None and summary.target_s11_db <= target_db:
        return "good"
    if summary.min_s11_db is not None and summary.min_s11_db <= target_db:
        return "shallow"
    return "poor"


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
