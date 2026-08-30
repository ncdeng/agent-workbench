"""Typed adapter for the official CST 3D ``Optimizer`` VBA object.

The installed CST 2024/2025 VBA help documents the optimizer as a history/VBA
object rather than as a typed ``cst.interface`` Python wrapper.  This module
keeps the official object behind a validated Python contract so callers never
need to assemble free-form optimizer VBA.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Protocol


class NativeOptimizerAlgorithm(StrEnum):
    TRUST_REGION = "Trust_Region"
    CMA_ES = "CMAES"
    NELDER_MEAD = "Nelder_Mead_Simplex"


class GoalRangeType(StrEnum):
    SINGLE = "single"
    RANGE = "range"
    TOTAL = "total"


@dataclass(frozen=True)
class NativeParameterRange:
    name: str
    initial: float
    minimum: float
    maximum: float
    anchors: int = 5

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError(f"invalid CST parameter name: {self.name!r}")
        values = (self.initial, self.minimum, self.maximum)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"parameter {self.name!r} contains a non-finite value")
        if not self.minimum < self.maximum:
            raise ValueError(f"parameter {self.name!r} requires minimum < maximum")
        if not self.minimum <= self.initial <= self.maximum:
            raise ValueError(f"parameter {self.name!r} initial value is outside its range")
        if self.anchors < 2:
            raise ValueError("anchors must be at least 2")


@dataclass(frozen=True)
class SParameterGoal:
    result_name: str = r"1D Results\S-Parameters\S1,1"
    target_db: float = -10.0
    range_type: GoalRangeType = GoalRangeType.SINGLE
    minimum_ghz: float = 9.4
    maximum_ghz: float = 9.4
    operator: str = "<"
    weight: float = 1.0
    norm: str = "MaxDiff"

    def __post_init__(self) -> None:
        _validate_vba_text(self.result_name, "result_name")
        if self.operator not in {"<", ">", "=", "min", "max"}:
            raise ValueError(f"unsupported CST goal operator: {self.operator!r}")
        if self.norm not in {"MaxDiff", "MaxDiffSq", "SumDiff", "SumDiffSq", "Diff", "DiffSq"}:
            raise ValueError(f"unsupported CST goal norm: {self.norm!r}")
        values = (self.target_db, self.minimum_ghz, self.maximum_ghz, self.weight)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("S-parameter goal contains a non-finite value")
        if self.weight <= 0:
            raise ValueError("goal weight must be positive")
        if self.range_type == GoalRangeType.SINGLE:
            if self.minimum_ghz != self.maximum_ghz:
                raise ValueError("single-frequency goal requires minimum_ghz == maximum_ghz")
        elif self.range_type == GoalRangeType.RANGE:
            if not self.minimum_ghz < self.maximum_ghz:
                raise ValueError("range goal requires minimum_ghz < maximum_ghz")


@dataclass(frozen=True)
class NativeOptimizerSpec:
    parameters: tuple[NativeParameterRange, ...]
    goals: tuple[SParameterGoal, ...]
    algorithm: NativeOptimizerAlgorithm = NativeOptimizerAlgorithm.TRUST_REGION
    max_evaluations: int = 8
    always_start_from_current: bool = True
    reuse_previous_calculations: bool = False
    data_storage: str = "Automatic"
    domain_accuracy: float = 0.01
    cma_sigma: float = 0.3
    # CST 2025 Optimizer help documents these four exact enum values.
    nelder_initial_distribution: str = "Noisy_Latin_Hyper_Cube"
    nelder_include_initial_point: bool = True
    nelder_min_simplex_size: float = 0.000001

    def __post_init__(self) -> None:
        if not self.parameters:
            raise ValueError("at least one varying parameter is required")
        if not self.goals:
            raise ValueError("at least one optimizer goal is required")
        names = [parameter.name for parameter in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("varying parameter names must be unique")
        if self.max_evaluations <= 1:
            raise ValueError("max_evaluations must be greater than 1")
        if self.data_storage not in {"All", "Automatic", "None"}:
            raise ValueError(f"unsupported CST data storage strategy: {self.data_storage!r}")
        if not 0 < self.domain_accuracy <= 1:
            raise ValueError("domain_accuracy must be in (0, 1]")
        if not 0 < self.cma_sigma <= 1:
            raise ValueError("cma_sigma must be in (0, 1]")
        if self.nelder_initial_distribution not in {
            "Uniform_Random_Numbers",
            "Latin_Hyper_Cube",
            "Noisy_Latin_Hyper_Cube",
            "Cube_Distribution",
        }:
            raise ValueError(
                "unsupported Nelder-Mead initial distribution: "
                f"{self.nelder_initial_distribution!r}"
            )
        if not 0 < self.nelder_min_simplex_size <= 1:
            raise ValueError("nelder_min_simplex_size must be in (0, 1]")


class _VbaController(Protocol):
    def execute_vba_immediate(self, vba_code: str, timeout: int = 60) -> dict: ...


class CstNativeOptimizerAdapter:
    """Configure and start CST's official optimizer through one controller."""

    def __init__(self, controller: _VbaController):
        self._controller = controller

    def start(
        self,
        spec: NativeOptimizerSpec,
        *,
        timeout: int = 1800,
        label: str = "cst_agent_native_optimizer",
    ) -> dict:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        vba = build_native_optimizer_vba(spec)
        result = self._controller.execute_vba_immediate(vba, timeout=timeout)
        output = dict(result or {})
        output.setdefault("backend", "cst_native_optimizer")
        output.setdefault("execution_mode", "immediate_vba")
        output.setdefault("label", label)
        output.setdefault("algorithm", spec.algorithm.value)
        output.setdefault("max_evaluations", spec.max_evaluations)
        output.setdefault("parameter_count", len(spec.parameters))
        output.setdefault("goal_count", len(spec.goals))
        return output


def build_native_optimizer_vba(spec: NativeOptimizerSpec) -> str:
    """Render the documented CST 3D Optimizer object call sequence."""

    bool_text = lambda value: "True" if value else "False"
    lines = [
        "Dim goalID As Long",
        "With Optimizer",
        f'    .SetOptimizerType ("{spec.algorithm.value}")',
        "    .InitParameterList",
        "    .ResetParameterList",
        "    .StartActiveSolver (True)",
        f"    .SetAlwaysStartFromCurrent ({bool_text(spec.always_start_from_current)})",
        f"    .SetUseDataOfPreviousCalculations ({bool_text(spec.reuse_previous_calculations)})",
        f'    .SetDataStorageStrategy ("{spec.data_storage}")',
    ]

    for parameter in spec.parameters:
        lines.extend(
            [
                f'    .SelectParameter ("{parameter.name}", True)',
                f"    .SetParameterInit ({_number(parameter.initial)})",
                f"    .SetParameterMin ({_number(parameter.minimum)})",
                f"    .SetParameterMax ({_number(parameter.maximum)})",
            ]
        )

    lines.extend(
        [
            '    .SetGoalSummaryType ("Sum_All_Goals")',
            "    .DeleteAllGoals",
        ]
    )
    for goal in spec.goals:
        lines.extend(
            [
                '    goalID = .AddGoal ("1DC Primary Result")',
                "    .SelectGoal (goalID, True)",
                f'    .SetGoal1DCResultName ("{_escape_vba(goal.result_name)}")',
                '    .SetGoalScalarType ("magdb20")',
                f'    .SetGoalOperator ("{goal.operator}")',
                f"    .SetGoalTarget ({_number(goal.target_db)})",
                f"    .SetGoalWeight ({_number(goal.weight)})",
                f'    .SetGoalNormNew ("{goal.norm}")',
                f'    .SetGoalRangeType ("{goal.range_type.value}")',
            ]
        )
        if goal.range_type != GoalRangeType.TOTAL:
            lines.append(
                f"    .SetGoalRange ({_number(goal.minimum_ghz)}, {_number(goal.maximum_ghz)})"
            )

    lines.extend(
        [
            f'    .SetUseMaxEval (True, "{spec.algorithm.value}")',
            f'    .SetMaxEval ({spec.max_evaluations}, "{spec.algorithm.value}")',
        ]
    )
    if spec.algorithm == NativeOptimizerAlgorithm.TRUST_REGION:
        lines.append(
            f'    .SetDomainAccuracy ({_number(spec.domain_accuracy)}, "Trust_Region")'
        )
    elif spec.algorithm == NativeOptimizerAlgorithm.CMA_ES:
        lines.append(f'    .SetSigma ({_number(spec.cma_sigma)}, "CMAES")')
    elif spec.algorithm == NativeOptimizerAlgorithm.NELDER_MEAD:
        lines.extend(
            [
                "    .SetInitialDistribution "
                f'("{spec.nelder_initial_distribution}", "Nelder_Mead_Simplex")',
                "    .SetUsePreDefPointInInitDistribution "
                f'({bool_text(spec.nelder_include_initial_point)}, "Nelder_Mead_Simplex")',
                f"    .SetMinSimplexSize ({_number(spec.nelder_min_simplex_size)})",
            ]
        )

    lines.extend(["    .Start", "End With"])
    return "\n".join(lines)


def _number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("CST optimizer numbers must be finite")
    return format(value, ".15g")


def _validate_vba_text(value: str, field: str) -> None:
    if not value or any(char in value for char in ("\r", "\n", '"')):
        raise ValueError(f"{field} contains unsafe VBA text")


def _escape_vba(value: str) -> str:
    _validate_vba_text(value, "VBA string")
    return value
