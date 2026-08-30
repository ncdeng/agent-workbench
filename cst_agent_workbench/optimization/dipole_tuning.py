"""Physics-guided, single-parameter dipole resonance tuning decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Mapping


@dataclass(frozen=True)
class DipoleLengthProposal:
    parameter: str
    old_value_mm: float
    new_value_mm: float
    measured_resonance_ghz: float
    target_frequency_ghz: float
    scale: float
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def propose_arm_length(
    *,
    old_length_mm: float,
    measured_resonance_ghz: float,
    target_frequency_ghz: float,
) -> DipoleLengthProposal:
    values = (old_length_mm, measured_resonance_ghz, target_frequency_ghz)
    if not all(math.isfinite(value) and value > 0 for value in values):
        raise ValueError("dipole tuning inputs must be finite and positive")
    scale = measured_resonance_ghz / target_frequency_ghz
    new_value = old_length_mm * scale
    if not 0.5 <= scale <= 1.5:
        raise ValueError("measured resonance implies an unsafe arm-length scale outside [0.5, 1.5]")
    direction = "shorten" if new_value < old_length_mm else "lengthen"
    return DipoleLengthProposal(
        parameter="arm_length",
        old_value_mm=old_length_mm,
        new_value_mm=new_value,
        measured_resonance_ghz=measured_resonance_ghz,
        target_frequency_ghz=target_frequency_ghz,
        scale=scale,
        rationale=f"Use first-order dipole scaling f_res proportional to 1/L to {direction} the arm.",
    )


def nearest_resonance(summary: Mapping[str, Any], target_frequency_ghz: float) -> float | None:
    candidates = []
    for resonance in summary.get("resonances") or []:
        try:
            candidates.append(float(resonance["freq_ghz"]))
        except (KeyError, TypeError, ValueError):
            continue
    if candidates:
        return min(candidates, key=lambda frequency: abs(frequency - target_frequency_ghz))
    try:
        return float(summary["min_freq_ghz"])
    except (KeyError, TypeError, ValueError):
        return None


def resonance_error_ghz(summary: Mapping[str, Any], target_frequency_ghz: float) -> float | None:
    resonance = nearest_resonance(summary, target_frequency_ghz)
    return abs(resonance - target_frequency_ghz) if resonance is not None else None


def candidate_improves(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    target_frequency_ghz: float,
) -> bool:
    before_error = resonance_error_ghz(before, target_frequency_ghz)
    after_error = resonance_error_ghz(after, target_frequency_ghz)
    return before_error is not None and after_error is not None and after_error < before_error
