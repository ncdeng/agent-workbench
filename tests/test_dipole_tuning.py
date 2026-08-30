import pytest

from cst_agent_workbench.optimization.dipole_tuning import (
    candidate_improves,
    nearest_resonance,
    propose_arm_length,
    resonance_error_ghz,
)


def test_proposal_uses_inverse_resonance_scaling():
    proposal = propose_arm_length(
        old_length_mm=12.146763,
        measured_resonance_ghz=5.42068,
        target_frequency_ghz=5.8,
    )

    assert proposal.parameter == "arm_length"
    assert proposal.new_value_mm == pytest.approx(11.352365, abs=1e-6)
    assert proposal.scale == pytest.approx(5.42068 / 5.8)


def test_nearest_resonance_is_used_instead_of_unrelated_global_minimum():
    summary = {
        "min_freq_ghz": 2.0,
        "resonances": [{"freq_ghz": 2.0}, {"freq_ghz": 5.75}],
    }

    assert nearest_resonance(summary, 5.8) == 5.75
    assert resonance_error_ghz(summary, 5.8) == pytest.approx(0.05)


def test_candidate_requires_strict_resonance_error_improvement():
    before = {"resonances": [{"freq_ghz": 5.42}]}

    assert candidate_improves(before, {"resonances": [{"freq_ghz": 5.79}]}, target_frequency_ghz=5.8)
    assert not candidate_improves(before, {"resonances": [{"freq_ghz": 5.3}]}, target_frequency_ghz=5.8)


def test_proposal_rejects_unsafe_scale():
    with pytest.raises(ValueError, match="unsafe"):
        propose_arm_length(old_length_mm=10, measured_resonance_ghz=1, target_frequency_ghz=5.8)
