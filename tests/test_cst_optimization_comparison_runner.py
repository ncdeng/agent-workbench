from __future__ import annotations

from benchmarks.cst_optimization_comparison_runner import _proposal_history


def test_proposal_history_preserves_physical_outcome_and_parameter_snapshot():
    history = _proposal_history(
        [
            {
                "proposal": {"param": "patch_L", "delta_mm": -0.1},
                "improved": True,
                "after": {"target_s11_db": -18.2},
                "parameters_after": {"patch_L": 10.4},
            }
        ]
    )

    assert history == [
        {
            "param_name": "patch_L",
            "delta_mm": -0.1,
            "improved": True,
            "metric_value": -18.2,
            "param_snapshot": {"patch_L": 10.4},
        }
    ]
