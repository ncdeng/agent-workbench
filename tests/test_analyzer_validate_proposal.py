from cst_agent_workbench.agent.analyzer import validate_llm_proposal


def test_valid_proposal_passes():
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": 0.5, "reason": "降频"}, None)
    assert ok
    assert reason == ""


def test_invalid_param_rejected():
    ok, reason = validate_llm_proposal({"param": "unknown_param", "delta_mm": 0.5}, None)
    assert not ok
    assert "允许列表" in reason


def test_delta_too_small_rejected():
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": 0.01}, None)
    assert not ok
    assert "最小步长" in reason


def test_delta_too_large_rejected():
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": 3.0}, None)
    assert not ok
    assert "最大步长" in reason


def test_negative_delta_within_range_passes():
    ok, reason = validate_llm_proposal({"param": "inset_depth", "delta_mm": -1.2}, None)
    assert ok


def test_non_dict_rejected():
    ok, reason = validate_llm_proposal(None, None)
    assert not ok


def test_missing_delta_rejected():
    ok, reason = validate_llm_proposal({"param": "patch_L"}, None)
    assert not ok
    assert "delta_mm" in reason


def test_history_same_direction_failed_rejected():
    history = [{"param_name": "patch_L", "delta_mm": 0.5, "improved": False}]
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": 0.3}, history)
    assert not ok
    assert "同方向" in reason


def test_history_same_direction_but_improved_passes():
    history = [{"param_name": "patch_L", "delta_mm": 0.5, "improved": True}]
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": 0.3}, history)
    assert ok


def test_history_opposite_direction_after_failure_passes():
    history = [{"param_name": "patch_L", "delta_mm": 0.5, "improved": False}]
    ok, reason = validate_llm_proposal({"param": "patch_L", "delta_mm": -0.5}, history)
    assert ok


def test_history_different_param_not_blocked():
    history = [{"param_name": "patch_L", "delta_mm": 0.5, "improved": False}]
    ok, reason = validate_llm_proposal({"param": "inset_depth", "delta_mm": 0.5}, history)
    assert ok
