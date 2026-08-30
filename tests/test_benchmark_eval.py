"""Benchmark evaluation tests for CST agent optimization convergence.

验证 agent 优化收敛逻辑，量化展示 agent 表现。
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from cst_agent_workbench.agent.analyzer import propose_patch_optimization_with_llm, validate_llm_proposal
from cst_agent_workbench.agent.llm_planner import call_planner_llm
from cst_agent_workbench.optimization.models import OptimizationContext, OptimizationTarget, S11Summary
from cst_agent_workbench.optimization.strategy import HeuristicPatchOptimizationStrategy

# ---------------------------------------------------------------------------
# Benchmark dataset
# ---------------------------------------------------------------------------

BENCHMARK_CASES = [
    {
        "name": "freq_too_high",
        "description": "谐振频率偏高，需要增大 patch_L",
        "current_params": {"patch_L": 15.0, "patch_W": 18.0, "inset_depth": 3.5, "feed_W": 2.8},
        "s11_summary": {"min_s11_db": -18.5, "min_freq_ghz": 9.8, "bandwidth_ghz": 0.4, "target_s11_db": -3.0},
        "target_freq_ghz": 9.4,
        "expected_param": "patch_L",
        "expected_direction": "increase",  # delta > 0
    },
    {
        "name": "freq_too_low",
        "description": "谐振频率偏低，需要减小 patch_L",
        "current_params": {"patch_L": 17.0, "patch_W": 18.0, "inset_depth": 3.5, "feed_W": 2.8},
        "s11_summary": {"min_s11_db": -16.0, "min_freq_ghz": 9.0, "bandwidth_ghz": 0.35, "target_s11_db": -2.5},
        "target_freq_ghz": 9.4,
        "expected_param": "patch_L",
        "expected_direction": "decrease",  # delta < 0
    },
    {
        "name": "poor_matching",
        "description": "频率对准但匹配差，需要调 inset_depth",
        "current_params": {"patch_L": 16.0, "patch_W": 18.0, "inset_depth": 2.0, "feed_W": 2.8},
        "s11_summary": {"min_s11_db": -8.0, "min_freq_ghz": 9.41, "bandwidth_ghz": 0.1, "target_s11_db": -8.0},
        "target_freq_ghz": 9.4,
        "expected_param": "inset_depth",
        "expected_direction": "increase",
    },
]


# ---------------------------------------------------------------------------
# Helper: build a mock openai-style client that returns a fixed JSON string
# ---------------------------------------------------------------------------

def _make_mock_client(response_json: dict) -> MagicMock:
    """Return a mock client whose chat.completions.create() returns response_json as text."""
    return _make_text_client(json.dumps(response_json))


def _make_text_client(content: str) -> MagicMock:
    mock_choice = MagicMock()
    mock_choice.message.content = content
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 50
    mock_resp.usage.completion_tokens = 20
    client = MagicMock()
    client.chat.completions.create.return_value = mock_resp
    return client


def _no_history_direction_inferer(history, param_name, default_sign):
    """direction_inferer 桩：忽略历史，返回 default_sign，隔离测 diagnose_s11 的方向判断。"""
    return default_sign


# ---------------------------------------------------------------------------
# 测试1: Planner intent_kind 分类准确性
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("user_msg,expected_intent", [
    ("帮我优化天线谐振频率到 9.4 GHz", "optimization_round"),
    ("当前仿真状态如何？", "chat_task"),
    ("持续优化直到 S11 < -15 dB", "continuous_optimization"),
])
def test_planner_intent_classification(user_msg: str, expected_intent: str):
    """验证 Planner LLM 对不同用户目标输出正确的 intent_kind。"""
    plan_json = {
        "intent_kind": expected_intent,
        "user_goal": user_msg,
        "steps": [
            {"step_id": "s1", "kind": "analyze", "title": "分析状态", "expected_output": "状态摘要"},
            {"step_id": "s2", "kind": "respond", "title": "回复用户", "expected_output": "回复"},
        ],
        "stop_condition": "完成",
    }
    client = _make_mock_client(plan_json)
    plan, usage = call_planner_llm(
        client=client,
        model="gpt-4o-mini",
        user_message=user_msg,
        context_text="仿真未运行",
    )
    assert plan is not None, f"Planner 返回 None，expected intent={expected_intent}"
    assert plan["intent"]["kind"] == expected_intent, (
        f"intent_kind 错误: got {plan['intent']['kind']}, expected {expected_intent}"
    )


# ---------------------------------------------------------------------------
# 测试2: Heuristic strategy 方向准确性（真实物理诊断，非循环 mock）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", BENCHMARK_CASES, ids=[c["name"] for c in BENCHMARK_CASES])
def test_proposal_direction_accuracy(case: dict):
    """验证 HeuristicPatchOptimizationStrategy 在给定 s11_summary 下的参数和方向符合物理诊断。

    原 B5 问题：此测试曾把答案塞进 mock LLM client 再断言 mock 返回了答案（循环永真）。
    现改为直接测真实 HeuristicPatchOptimizationStrategy + diagnose_s11 物理诊断逻辑：
    freq_too_high → patch_L 增大（降频），freq_too_low → patch_L 减小（升频），
    poor_matching（频率接近）→ inset_depth 调整。
    """
    strategy = HeuristicPatchOptimizationStrategy(_no_history_direction_inferer)
    context = OptimizationContext(
        target=OptimizationTarget(mode="at_f0", target_freq_ghz=case["target_freq_ghz"], target_db=-10.0),
        parameters=case["current_params"],
        s11_summary=S11Summary.from_mapping(case["s11_summary"]),
        history=[],
    )

    proposal = strategy.propose_next_step(context)

    assert proposal.has_updates, f"[{case['name']}] strategy 未产生更新：{proposal.message or proposal.reason}"
    update = proposal.updates[0]
    expected_param = case["expected_param"]
    expected_direction = case["expected_direction"]
    assert update.name == expected_param, (
        f"[{case['name']}] param 错误: got {update.name}, expected {expected_param}"
    )
    actual_delta = update.new - update.old
    if expected_direction == "increase":
        assert actual_delta > 0, f"[{case['name']}] delta 应 > 0，got {actual_delta}"
    else:
        assert actual_delta < 0, f"[{case['name']}] delta 应 < 0，got {actual_delta}"


def test_proposal_request_uses_json_mode_when_supported():
    client = _make_mock_client({"param": "feed_W", "delta_mm": 0.3, "reason": "json mode"})

    proposal, _ = propose_patch_optimization_with_llm(
        client=client,
        model="gpt-4o-mini",
        s11_summary={"min_s11_db": -8.0, "min_freq_ghz": 9.4, "at_f0_s11_db": -8.0},
        target_freq_ghz=9.4,
        current_params={"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.0},
    )

    assert proposal["param"] == "feed_W"
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {"type": "json_object"}



def test_proposal_request_falls_back_when_json_mode_is_unsupported():
    client = _make_mock_client({"param": "feed_W", "delta_mm": 0.3, "reason": "fallback"})
    client.chat.completions.create.side_effect = [RuntimeError("unsupported response_format"), client.chat.completions.create.return_value]

    proposal, _ = propose_patch_optimization_with_llm(
        client=client,
        model="gpt-4o-mini",
        s11_summary={"min_s11_db": -8.0, "min_freq_ghz": 9.4, "at_f0_s11_db": -8.0},
        target_freq_ghz=9.4,
        current_params={"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.0},
    )

    assert proposal["param"] == "feed_W"
    assert client.chat.completions.create.call_count == 2
    assert "response_format" not in client.chat.completions.create.call_args.kwargs



def test_proposal_parser_accepts_json_like_llm_responses():
    client = _make_text_client(
        "<think>这里有不应进入报告的推理过程。</think>\n建议如下：\n```json\n{'parameter': 'feed_W', 'delta': '+0.3 mm', 'rationale': '增大馈线宽度改善匹配',}\n```"
    )

    proposal, usage = propose_patch_optimization_with_llm(
        client=client,
        model="gpt-4o-mini",
        s11_summary={"min_s11_db": -8.0, "min_freq_ghz": 9.4, "at_f0_s11_db": -8.0},
        target_freq_ghz=9.4,
        current_params={"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.0},
    )

    assert proposal["param"] == "feed_W"
    assert proposal["delta_mm"] == 0.3
    assert proposal["reason"] == "增大馈线宽度改善匹配"
    assert usage == {"prompt": 50, "completion": 20}


def test_proposal_parse_failure_excerpt_redacts_think_blocks():
    client = _make_text_client("<think>不要把这段推理写入报告。</think>")

    proposal, usage = propose_patch_optimization_with_llm(
        client=client,
        model="gpt-4o-mini",
        s11_summary={"min_s11_db": -8.0, "min_freq_ghz": 9.4, "at_f0_s11_db": -8.0},
        target_freq_ghz=9.4,
        current_params={"patch_L": 16.0, "inset_depth": 3.0, "feed_W": 2.0},
    )

    assert proposal is None
    assert usage["parse_error"] == "no_parseable_optimization_proposal"
    assert "不要把这段推理" not in usage["raw_text_excerpt"]
    assert usage["raw_text_excerpt"] == "[model output only contained reasoning tags; no parseable proposal was emitted]"


# ---------------------------------------------------------------------------
# 测试3: validate_llm_proposal 物理合法性
# ---------------------------------------------------------------------------

def test_validate_proposal_valid_cases():
    """合法 proposal 应通过校验。"""
    valid_cases = [
        {"param": "patch_L", "delta_mm": 0.5, "reason": "增大贴片长度"},
        {"param": "inset_depth", "delta_mm": -0.3, "reason": "减小馈入深度"},
        {"param": "feed_W", "delta_mm": 0.1, "reason": "最小步长边界"},
        {"param": "patch_W", "delta_mm": 2.0, "reason": "最大步长边界"},
    ]
    for prop in valid_cases:
        ok, reason = validate_llm_proposal(prop, history=None)
        assert ok, f"合法 proposal 被拒绝: {prop} — {reason}"


def test_validate_proposal_invalid_cases():
    """非法 proposal 应被拒绝。"""
    invalid_cases = [
        # 步长超出 _DELTA_MAX=2.0
        ({"param": "patch_L", "delta_mm": 10.0, "reason": "过大步长"}, "超过最大步长"),
        # 未知 param
        ({"param": "unknown_param", "delta_mm": 0.5, "reason": "未知参数"}, "不在允许列表"),
        # 步长低于 _DELTA_MIN=0.05
        ({"param": "patch_L", "delta_mm": 0.01, "reason": "过小步长"}, "小于最小步长"),
        # delta_mm 缺失
        ({"param": "patch_L", "reason": "缺少 delta"}, "delta_mm 缺失"),
    ]
    for prop, expected_reason_fragment in invalid_cases:
        ok, reason = validate_llm_proposal(prop, history=None)
        assert not ok, f"非法 proposal 应被拒绝: {prop}"
        assert expected_reason_fragment in reason, (
            f"拒绝原因不匹配: got '{reason}', expected fragment '{expected_reason_fragment}'"
        )


# ---------------------------------------------------------------------------
# 测试4: 收敛率统计 (benchmark summary, 真实物理诊断)
# ---------------------------------------------------------------------------

def test_benchmark_summary():
    """汇总 HeuristicPatchOptimizationStrategy 在 benchmark case 上的方向准确率。

    原 B5 问题：此测试曾把答案塞进 mock LLM client 再统计 mock 命中率（循环永真，永远 100%）。
    现改为统计真实物理诊断策略的方向准确率，作为 agent 性能基线指标。
    """
    strategy = HeuristicPatchOptimizationStrategy(_no_history_direction_inferer)
    correct = 0
    total = len(BENCHMARK_CASES)

    for case in BENCHMARK_CASES:
        context = OptimizationContext(
            target=OptimizationTarget(mode="at_f0", target_freq_ghz=case["target_freq_ghz"], target_db=-10.0),
            parameters=case["current_params"],
            s11_summary=S11Summary.from_mapping(case["s11_summary"]),
            history=[],
        )
        proposal = strategy.propose_next_step(context)
        expected_param = case["expected_param"]
        expected_direction = case["expected_direction"]

        direction_correct = False
        if proposal.has_updates and proposal.updates[0].name == expected_param:
            actual_delta = proposal.updates[0].new - proposal.updates[0].old
            if expected_direction == "increase" and actual_delta > 0:
                direction_correct = True
            elif expected_direction == "decrease" and actual_delta < 0:
                direction_correct = True

        if direction_correct:
            correct += 1

    convergence_rate = correct / total
    print(f"\nBenchmark convergence rate: {correct}/{total} = {convergence_rate:.0%}")
    assert convergence_rate >= 2 / 3, f"Convergence rate too low: {convergence_rate:.0%}"
