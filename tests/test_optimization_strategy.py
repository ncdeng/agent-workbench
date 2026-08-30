from cst_agent_workbench.optimization.models import OptimizationContext, OptimizationTarget, S11Summary
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy


def _strategy(history=None):
    def infer_direction(past_history, param_name, default_sign):
        assert past_history == (history or [])
        return default_sign

    return HeuristicPatchOptimizationStrategy(infer_direction)


def _context(*, min_freq, target_s11, min_s11=-18.0, patch_l=10.0, inset_depth=2.0, feed_w=1.0, target_freq=9.4, target_db=-10.0, history=None):
    return OptimizationContext(
        target=OptimizationTarget(mode="at_f0", target_freq_ghz=target_freq, target_db=target_db),
        parameters={
            "f0": target_freq,
            "patch_L": patch_l,
            "inset_depth": inset_depth,
            "feed_W": feed_w,
            "substrate_h": 0.51,
            "copper_t": 0.035,
        },
        s11_summary=S11Summary(min_freq_ghz=min_freq, min_s11_db=min_s11, target_s11_db=target_s11),
        history=history or [],
    )


def test_strategy_increases_patch_l_when_resonance_is_above_target():
    proposal = _strategy().propose_next_step(_context(min_freq=9.8, target_s11=-8.0))

    assert proposal.strategy == "heuristic"
    assert proposal.updates[0].name == "patch_L"
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "偏高" in proposal.reason


def test_strategy_prefers_patch_l_for_deep_resonance_that_is_still_frequency_shifted():
    proposal = _strategy().propose_next_step(_context(min_freq=9.682, target_s11=-6.37, target_freq=9.4))

    assert proposal.updates[0].name == "patch_L"
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "增大 patch_L" in proposal.reason


def test_strategy_keeps_tuning_inset_for_near_target_useful_resonance():
    history = [
        {"round": 0, "param_snapshot": {"inset_depth": "3.5518"}, "metric_value": -6.37},
        {"round": 1, "param_snapshot": {"inset_depth": "3.7649"}, "metric_value": -6.83},
    ]

    proposal = _strategy(history).propose_next_step(
        _context(min_freq=9.569, target_s11=-6.83, target_freq=9.4, inset_depth=3.7649, history=history)
    )

    assert proposal.updates[0].name == "inset_depth"
    assert proposal.updates[0].new > proposal.updates[0].old


def test_strategy_switches_to_feed_width_when_near_target_inset_step_worsened():
    history = [
        {"round": 1, "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971"}, "metric_value": -6.83},
        {"round": 2, "param_snapshot": {"inset_depth": "3.9908", "feed_W": "4.971"}, "metric_value": -5.68},
    ]

    def infer_direction(past_history, param_name, default_sign):
        assert past_history == history
        if param_name == "inset_depth":
            assert default_sign == 1
            return -1
        assert param_name == "feed_W"
        assert default_sign == 1
        return 1

    proposal = HeuristicPatchOptimizationStrategy(infer_direction).propose_next_step(
        _context(min_freq=9.438, min_s11=-5.76, target_s11=-5.68, target_freq=9.4, inset_depth=3.9908, feed_w=4.971, history=history)
    )

    assert [update.name for update in proposal.updates] == ["inset_depth", "feed_W"]
    assert proposal.updates[0].new == 3.7649
    assert proposal.updates[1].new > proposal.updates[1].old
    assert "历史最佳 inset_depth" in proposal.reason


def test_strategy_uses_rollback_history_to_avoid_repeating_inset_step():
    history = [
        {"round": 0, "param_snapshot": {"inset_depth": "3.5518", "feed_W": "4.971"}, "metric_value": -6.37},
        {"round": 1, "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971"}, "metric_value": -6.83},
        {
            "round": 2,
            "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971"},
            "metric_value": -6.83,
            "rolled_back": True,
            "attempted_changed_params": {"inset_depth": {"old": "3.7649", "new": "3.9908"}},
        },
    ]

    def infer_direction(past_history, param_name, default_sign):
        from cst_agent_workbench.optimization.optimizer import PatchOptimizerMixin

        return PatchOptimizerMixin._infer_param_direction_from_history(past_history, param_name, default_sign)

    proposal = HeuristicPatchOptimizationStrategy(infer_direction).propose_next_step(
        _context(min_freq=9.5692, min_s11=-9.58, target_s11=-6.83, target_freq=9.4, inset_depth=3.7649, feed_w=4.971, history=history)
    )

    assert [update.name for update in proposal.updates] == ["feed_W"]
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "上一轮 inset_depth 同方向匹配变差" in proposal.reason


def test_strategy_switches_to_coupled_patch_length_after_inset_and_feed_rollbacks():
    history = [
        {"round": 1, "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971", "patch_L": "9.7008"}, "metric_value": -6.83},
        {
            "round": 2,
            "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971", "patch_L": "9.7008"},
            "metric_value": -6.83,
            "rolled_back": True,
            "attempted_changed_params": {"inset_depth": {"old": "3.7649", "new": "3.9908"}},
        },
        {
            "round": 3,
            "param_snapshot": {"inset_depth": "3.7649", "feed_W": "4.971", "patch_L": "9.7008"},
            "metric_value": -6.83,
            "rolled_back": True,
            "attempted_changed_params": {"feed_W": {"old": "4.971", "new": "5.1698"}},
        },
    ]

    def infer_direction(past_history, param_name, default_sign):
        from cst_agent_workbench.optimization.optimizer import PatchOptimizerMixin

        return PatchOptimizerMixin._infer_param_direction_from_history(past_history, param_name, default_sign)

    proposal = HeuristicPatchOptimizationStrategy(infer_direction).propose_next_step(
        _context(min_freq=9.5692, min_s11=-9.58, target_s11=-6.83, target_freq=9.4, patch_l=9.7008, inset_depth=3.7649, feed_w=4.971, history=history)
    )

    assert [update.name for update in proposal.updates] == ["patch_L"]
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "近期尝试均被回滚" in proposal.reason



def test_strategy_decreases_patch_l_when_resonance_is_below_target():
    proposal = _strategy().propose_next_step(_context(min_freq=9.0, target_s11=-8.0))

    assert proposal.updates[0].name == "patch_L"
    assert proposal.updates[0].new < proposal.updates[0].old
    assert "偏低" in proposal.reason


def test_strategy_only_changes_patch_l_when_tuning_frequency():
    proposal = _strategy().propose_next_step(_context(min_freq=9.8, target_s11=-8.0, patch_l=10.0, inset_depth=2.0))

    assert [update.name for update in proposal.updates] == ["patch_L"]
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "只调整 patch_L" in proposal.reason



def test_strategy_does_not_reverse_physical_diagnosis_after_patch_l_worsened():
    history = [
        {"round": 0, "param_snapshot": {"patch_L": 10.0}, "improved": False},
        {
            "round": 1,
            "param_snapshot": {"patch_L": 10.0},
            "metric_value": -8.0,
            "rolled_back": True,
            "attempted_changed_params": {"patch_L": {"old": "10.0", "new": "10.4"}},
        },
    ]

    def infer_direction(past_history, param_name, default_sign):
        assert past_history == history
        assert param_name == "patch_L"
        assert default_sign == 1
        return -1

    proposal = HeuristicPatchOptimizationStrategy(infer_direction).propose_next_step(
        _context(min_freq=9.8, target_s11=-8.0, patch_l=10.0, history=history)
    )

    assert proposal.updates[0].name == "patch_L"
    assert proposal.updates[0].new > proposal.updates[0].old
    assert proposal.updates[0].new - proposal.updates[0].old <= 0.11
    assert "保持物理诊断方向" in proposal.reason



def test_strategy_switches_to_decreasing_wide_feed_width_after_patch_l_rollback():
    history = [
        {
            "round": 1,
            "param_snapshot": {"patch_L": "9.7008", "feed_W": "4.971", "notch_W": "6.251"},
            "metric_value": -6.37,
            "rolled_back": True,
            "attempted_changed_params": {"patch_L": {"old": "9.7008", "new": "9.9918"}},
        },
    ]
    context = _context(min_freq=9.682, target_s11=-6.37, patch_l=9.7008, feed_w=4.971, history=history)
    context.parameters["substrate_h"] = 1.6
    context.parameters["inset_gap"] = 0.64
    context.parameters["notch_W"] = 6.251

    proposal = _strategy(history).propose_next_step(context)

    assert [update.name for update in proposal.updates] == ["feed_W", "notch_W"]
    assert proposal.updates[0].new < proposal.updates[0].old
    assert round(proposal.updates[0].new, 4) == 3.4797
    assert round(proposal.updates[1].new, 4) == round(proposal.updates[0].new + 2 * 0.64, 4)
    assert "减小 feed_W" in proposal.reason


def test_strategy_tunes_inset_depth_when_frequency_is_close_but_match_is_poor():
    history = [{"round": 0}, {"round": 1}]
    proposal = _strategy(history).propose_next_step(_context(min_freq=9.41, target_s11=-7.5, history=history))

    assert proposal.updates[0].name == "inset_depth"
    assert proposal.updates[0].new > proposal.updates[0].old
    assert "优先微调 inset_depth" in proposal.reason


def test_strategy_falls_back_to_feed_width_when_inset_depth_hits_boundary():
    history = [{"round": 0}, {"round": 1}]
    proposal = _strategy(history).propose_next_step(
        _context(min_freq=9.41, target_s11=-7.5, inset_depth=4.8, history=history)
    )

    assert proposal.updates[0].name == "feed_W"
    assert proposal.updates[0].new < proposal.updates[0].old
    assert "feed_W" in proposal.reason


def test_strategy_returns_no_update_when_target_is_met():
    proposal = _strategy().propose_next_step(_context(min_freq=9.4, target_s11=-12.0))

    assert proposal.handled is True
    assert proposal.updates == []
    assert "已满足目标" in proposal.message
