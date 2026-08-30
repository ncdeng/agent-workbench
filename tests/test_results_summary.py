from cst_agent_workbench.results.summary import (
    evaluate_optimization_target,
    interpolate_s11_at_freq,
    resolve_target_frequency,
)





def test_interpolate_s11_at_freq_interpolates_midpoint_and_edges():
    plot_data = [
        {"freq": 9.0, "s_db": -5.0},
        {"freq": 9.2, "s_db": -9.0},
        {"freq": 9.4, "s_db": -13.0},
    ]

    assert interpolate_s11_at_freq(plot_data, 9.1) == -7.0
    assert interpolate_s11_at_freq(plot_data, 8.9) == -5.0
    assert interpolate_s11_at_freq(plot_data, 9.5) == -13.0



def test_evaluate_optimization_target_at_f0_uses_interpolated_value():
    s11_result = {
        "plot_data": [
            {"freq": 9.3, "s_db": -8.0},
            {"freq": 9.5, "s_db": -12.0},
        ]
    }

    evaluation = evaluate_optimization_target(
        s11_result,
        mode="at_f0",
        target_db=-10.0,
        effective_target_freq=9.4,
        configured_target_freq=9.4,
    )

    assert evaluation.at_f0_s11 == -10.0
    assert evaluation.met is True
    assert "S11@9.4GHz = -10.00 dB | PASS" == evaluation.status_text



def test_evaluate_optimization_target_handles_missing_target_frequency():
    evaluation = evaluate_optimization_target(
        {"plot_data": [{"freq": 9.4, "s_db": -11.0}]},
        mode="at_f0",
        target_db=-10.0,
        effective_target_freq=0.0,
        configured_target_freq=0.0,
    )

    assert evaluation.met is False
    assert evaluation.at_f0_s11 is None
    assert "frequency not set" in evaluation.criteria_text
    assert evaluation.status_text == "Please set the target frequency (GHz)"



def test_resolve_target_frequency_uses_parameter_lookup_fallback():
    freq = resolve_target_frequency(
        mode="at_f0",
        configured_target_freq=0.0,
        parameter_lookup=lambda: {"f0": "9.75"},
    )

    assert freq == 9.75
