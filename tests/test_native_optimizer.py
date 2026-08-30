from __future__ import annotations

import pytest

from cst_agent_workbench.optimization.native_optimizer import (
    CstNativeOptimizerAdapter,
    GoalRangeType,
    NativeOptimizerAlgorithm,
    NativeOptimizerSpec,
    NativeParameterRange,
    SParameterGoal,
    build_native_optimizer_vba,
)


def _spec(**overrides) -> NativeOptimizerSpec:
    values = {
        "parameters": (
            NativeParameterRange(
                name="patch_L",
                initial=10.489,
                minimum=9.44,
                maximum=11.54,
            ),
        ),
        "goals": (
            SParameterGoal(
                target_db=-10.0,
                range_type=GoalRangeType.SINGLE,
                minimum_ghz=9.4,
                maximum_ghz=9.4,
            ),
        ),
        "algorithm": NativeOptimizerAlgorithm.TRUST_REGION,
        "max_evaluations": 4,
    }
    values.update(overrides)
    return NativeOptimizerSpec(**values)


def test_builds_documented_trust_region_call_sequence():
    vba = build_native_optimizer_vba(_spec())

    expected_in_order = [
        '.SetOptimizerType ("Trust_Region")',
        ".InitParameterList",
        ".ResetParameterList",
        '.SelectParameter ("patch_L", True)',
        ".SetParameterInit (10.489)",
        ".SetParameterMin (9.44)",
        ".SetParameterMax (11.54)",
        '.AddGoal ("1DC Primary Result")',
        '.SetGoal1DCResultName ("1D Results\\S-Parameters\\S1,1")',
        '.SetGoalScalarType ("magdb20")',
        '.SetGoalRangeType ("single")',
        ".SetGoalRange (9.4, 9.4)",
        '.SetUseMaxEval (True, "Trust_Region")',
        '.SetMaxEval (4, "Trust_Region")',
        '.SetDomainAccuracy (0.01, "Trust_Region")',
        "    .Start\nEnd With",
    ]
    positions = [vba.index(fragment) for fragment in expected_in_order]
    assert positions == sorted(positions)


def test_cma_es_uses_sigma_and_range_goal():
    spec = _spec(
        algorithm=NativeOptimizerAlgorithm.CMA_ES,
        goals=(
            SParameterGoal(
                target_db=-10,
                range_type=GoalRangeType.RANGE,
                minimum_ghz=9.2,
                maximum_ghz=9.6,
            ),
        ),
    )

    vba = build_native_optimizer_vba(spec)

    assert '.SetOptimizerType ("CMAES")' in vba
    assert '.SetSigma (0.3, "CMAES")' in vba
    assert '.SetGoalRangeType ("range")' in vba
    assert ".SetGoalRange (9.2, 9.6)" in vba
    assert "SetDomainAccuracy" not in vba


def test_nelder_mead_configures_deterministic_initial_simplex():
    vba = build_native_optimizer_vba(
        _spec(
            algorithm=NativeOptimizerAlgorithm.NELDER_MEAD,
            max_evaluations=4,
            always_start_from_current=False,
        )
    )

    assert '.SetOptimizerType ("Nelder_Mead_Simplex")' in vba
    assert '.SetAlwaysStartFromCurrent (False)' in vba
    assert (
        '.SetInitialDistribution ("Noisy_Latin_Hyper_Cube", "Nelder_Mead_Simplex")'
        in vba
    )
    assert (
        '.SetUsePreDefPointInInitDistribution (True, "Nelder_Mead_Simplex")'
        in vba
    )
    assert ".SetMinSimplexSize (1e-06)" in vba


def test_accepts_all_optimizer_distributions_from_installed_help():
    assert _spec(nelder_initial_distribution="Cube_Distribution")
    assert _spec(nelder_initial_distribution="Uniform_Random_Numbers")


def test_rejects_undocumented_optimizer_distribution_spelling():
    with pytest.raises(ValueError, match="unsupported Nelder-Mead"):
        _spec(nelder_initial_distribution="Uniform_Random")


def test_rejects_invalid_parameter_ranges_and_vba_text():
    with pytest.raises(ValueError, match="outside"):
        NativeParameterRange("patch_L", 12, 9, 11)
    with pytest.raises(ValueError, match="invalid CST parameter"):
        NativeParameterRange('patch_L"\n.Start', 10, 9, 11)
    with pytest.raises(ValueError, match="unsafe VBA"):
        SParameterGoal(result_name='S1,1"\n.Start')


def test_rejects_missing_goals_and_unbounded_budget():
    with pytest.raises(ValueError, match="at least one optimizer goal"):
        _spec(goals=())
    with pytest.raises(ValueError, match="greater than 1"):
        _spec(max_evaluations=1)


def test_adapter_is_the_only_controller_execution_boundary():
    class FakeController:
        def __init__(self):
            self.calls = []

        def execute_vba_immediate(self, vba_code, timeout=0):
            self.calls.append((vba_code, timeout))
            return {"success": True, "executed": True}

    controller = FakeController()
    result = CstNativeOptimizerAdapter(controller).start(_spec(), timeout=900)

    assert len(controller.calls) == 1
    vba, timeout = controller.calls[0]
    assert vba.endswith("    .Start\nEnd With")
    assert timeout == 900
    assert result == {
        "success": True,
        "executed": True,
        "backend": "cst_native_optimizer",
        "execution_mode": "immediate_vba",
        "label": "cst_agent_native_optimizer",
        "algorithm": "Trust_Region",
        "max_evaluations": 4,
        "parameter_count": 1,
        "goal_count": 1,
    }
