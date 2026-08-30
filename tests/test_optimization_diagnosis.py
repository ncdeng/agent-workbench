from cst_agent_workbench.optimization.diagnosis import diagnose_s11
from cst_agent_workbench.optimization.models import OptimizationTarget, S11Summary


def _target():
    return OptimizationTarget(mode="at_f0", target_freq_ghz=9.4, target_db=-10.0)


def test_diagnosis_prefers_patch_l_increase_when_resonance_is_too_high():
    diagnosis = diagnose_s11(
        S11Summary(min_freq_ghz=9.682, min_s11_db=-15.39, target_s11_db=-6.83),
        _target(),
    )

    assert diagnosis.resonance_shift == "too_high"
    assert diagnosis.matching_quality == "shallow"
    assert diagnosis.recommended_parameter_family == "patch_L"
    assert diagnosis.recommended_delta_sign == 1
    assert "增大 patch_L" in diagnosis.reason


def test_diagnosis_prefers_patch_l_decrease_when_resonance_is_too_low():
    diagnosis = diagnose_s11(
        S11Summary(min_freq_ghz=9.1, min_s11_db=-14.0, target_s11_db=-5.0),
        _target(),
    )

    assert diagnosis.resonance_shift == "too_low"
    assert diagnosis.recommended_parameter_family == "patch_L"
    assert diagnosis.recommended_delta_sign == -1
    assert "减小 patch_L" in diagnosis.reason


def test_diagnosis_prefers_matching_when_resonance_is_near_target():
    diagnosis = diagnose_s11(
        S11Summary(min_freq_ghz=9.5, min_s11_db=-13.0, target_s11_db=-7.0),
        _target(),
    )

    assert diagnosis.resonance_shift == "near_target"
    assert diagnosis.matching_quality == "shallow"
    assert diagnosis.recommended_parameter_family == "inset_depth"


def test_diagnosis_flags_no_clear_resonance_before_parameter_search():
    diagnosis = diagnose_s11(
        S11Summary(min_freq_ghz=11.7, min_s11_db=-0.01, target_s11_db=-0.0),
        _target(),
    )

    assert diagnosis.resonance_shift == "no_clear_resonance"
    assert diagnosis.recommended_parameter_family == "mesh_or_solver"
    assert diagnosis.recommended_delta_sign == 0
    assert "端口连接" in diagnosis.reason


def test_diagnosis_uses_nearest_resonance_instead_of_global_minimum():
    diagnosis = diagnose_s11(
        S11Summary(
            min_freq_ghz=11.0,
            min_s11_db=-18.0,
            target_s11_db=-7.0,
            resonances=[
                {"freq_ghz": 9.55, "depth_db": -6.5},
                {"freq_ghz": 11.0, "depth_db": -18.0},
            ],
        ),
        _target(),
    )

    assert diagnosis.resonance_shift == "near_target"
    assert diagnosis.recommended_parameter_family == "inset_depth"
    assert diagnosis.frequency_error_ghz == 0.15000000000000036
    assert "9.550 GHz" in diagnosis.reason
