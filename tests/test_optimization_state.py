from cst_agent_workbench.optimization.state import OptimizationState


def _check(min_s11=None, min_freq=None, at_f0_s11=None, met=False):
    return {
        "min_s11": min_s11,
        "min_freq": min_freq,
        "at_f0_s11": at_f0_s11,
        "criteria_text": "criterion",
        "met": met,
    }



def test_optimization_state_tracks_baseline_rounds_and_best_snapshot():
    state = OptimizationState()
    state.target_mode = "at_f0"

    state.record_baseline(_check(min_s11=-12.0, min_freq=9.2, at_f0_s11=-8.0), {"patch_L": "10.0"})
    improved = state.record_round(_check(min_s11=-13.0, min_freq=9.3, at_f0_s11=-9.5), {"patch_L": "10.5"})

    assert improved is True
    assert state.round == 1
    assert state.best_round == 1
    assert state.best_metric_value == -9.5
    assert state.get_round_record(0)["param_snapshot"] == {"patch_L": "10.0"}
    assert state.get_best_record()["round"] == 1
    assert state.get_best_param_snapshot() == {"patch_L": "10.5"}



def test_optimization_state_updates_stagnation_and_stop_condition():
    state = OptimizationState()
    state.target_mode = "min_s11"

    state.record_baseline(_check(min_s11=-15.0, min_freq=8.9), {"patch_L": "10.0"})
    improved = state.record_round(_check(min_s11=-14.0, min_freq=9.0), {"patch_L": "10.2"})

    assert improved is False
    assert state.best_round == 0
    assert state.stagnation_count == 1
    assert state.should_stop(max_stagnation=1) is True



def test_optimization_state_records_rolled_back_attempt_metadata():
    state = OptimizationState()
    state.target_mode = "at_f0"

    state.record_baseline(_check(min_s11=-12.0, min_freq=9.2, at_f0_s11=-8.0), {"inset_depth": "3.7649"})
    state.record_round(
        _check(min_s11=-12.0, min_freq=9.2, at_f0_s11=-8.0),
        {"inset_depth": "3.7649"},
        optimizer_result={
            "rolled_back": True,
            "rollback_reason": "worse metric",
            "changed_params": {"inset_depth": {"old": "3.7649", "new": "3.9908"}},
        },
    )

    record = state.get_round_record(1)
    assert record["rolled_back"] is True
    assert record["rollback_reason"] == "worse metric"
    assert record["attempted_changed_params"]["inset_depth"] == {"old": "3.7649", "new": "3.9908"}



def test_optimization_state_diff_and_summary_text_include_best_round():
    state = OptimizationState()
    state.target_mode = "min_s11"
    state.record_baseline(_check(min_s11=-10.0, min_freq=9.0), {"patch_L": "10.0", "feed_W": "1.0"})
    state.record_round(
        _check(min_s11=-12.0, min_freq=9.1),
        {"patch_L": "10.3", "feed_W": "1.0"},
        strategy="heuristic",
        proposal_reason="优先增大 patch_L 做频率校正。",
    )

    rec = state.get_round_record(1)
    assert "patch_L: 10.0 -> 10.3" in rec["changed_params"]
    assert rec["strategy"] == "heuristic"
    assert rec["proposal_reason"] == "优先增大 patch_L 做频率校正。"

    history_rows = state.get_history_df_data()
    assert history_rows[1][1] == "heuristic"
    assert history_rows[1][2] == "优先增大 patch_L 做频率校正。"
    assert "patch_L: 10.0 -> 10.3" in history_rows[1][3]

    summary = state.get_summary_text()
    assert "第 1 轮" in summary
    assert "最佳 最小 S11: -12.00 dB" in summary
    assert "最近策略: heuristic" in summary
    assert "最近策略说明: 优先增大 patch_L 做频率校正。" in summary
