from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class OptimizationTarget:
    mode: str = "at_f0"
    target_freq_ghz: float = 0.0
    target_db: float = -10.0


@dataclass(frozen=True)
class S11Summary:
    min_freq_ghz: float | None = None
    min_s11_db: float | None = None
    target_s11_db: float | None = None
    resonances: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "S11Summary":
        data = data or {}
        return cls(
            min_freq_ghz=_coerce_float(data.get("min_freq_ghz")),
            min_s11_db=_coerce_float(data.get("min_s11_db")),
            target_s11_db=_coerce_float(data.get("target_s11_db")),
            resonances=list(data.get("resonances") or []),
        )


@dataclass(frozen=True)
class ParameterUpdate:
    name: str
    old: float
    new: float


@dataclass(frozen=True)
class OptimizationProposal:
    strategy: str
    reason: str
    updates: list[ParameterUpdate] = field(default_factory=list)
    handled: bool = True
    message: str = ""

    @property
    def has_updates(self) -> bool:
        return bool(self.updates)


@dataclass(frozen=True)
class OptimizationContext:
    target: OptimizationTarget
    parameters: dict[str, float]
    s11_summary: S11Summary
    history: list[dict[str, Any]] = field(default_factory=list)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
