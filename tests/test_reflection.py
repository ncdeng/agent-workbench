"""Tests for agent/reflection.py — reflect_on_round."""
from __future__ import annotations
import json
from unittest.mock import MagicMock


from cst_agent_workbench.agent.reflection import reflect_on_round, should_write_reflection_memory
from cst_agent_workbench.agent.memory import StructuredMemory


def _make_client(content: str) -> MagicMock:
    """Build a minimal mock OpenAI-compatible client."""
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    client.chat.completions.create.return_value.choices = [choice]
    return client


def test_reflect_valid_json_writes_to_memory():
    """合法 JSON 响应 → strategy_entry 被写入 memory.decisions.recent_strategies。"""
    payload = json.dumps(
        {
            "strategy": "增大 patch_L 0.5 mm",
            "outcome": "改善",
            "lesson": "下轮继续增大",
            "failure_pattern": "",
            "effective_action": "小步增大 patch_L",
            "avoid_next": "不要一次增大超过 1mm",
            "reuse_condition": "谐振频率高于目标时",
            "confidence": 0.8,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()
    round_summary = {"round": 1, "param": "patch_L", "delta": 0.5, "s11_before": -8.0}

    result = reflect_on_round(client, "test-model", round_summary, memory)

    assert result is True
    assert len(memory.decisions.recent_strategies) == 1
    entry = memory.decisions.recent_strategies[0]
    assert entry["round"] == 1
    assert entry["strategy"] == "增大 patch_L 0.5 mm"
    assert entry["outcome"] == "改善"
    assert entry["lesson"] == "下轮继续增大"
    assert entry["failure_pattern"] == ""
    assert entry["effective_action"] == "小步增大 patch_L"
    assert entry["avoid_next"] == "不要一次增大超过 1mm"
    assert entry["reuse_condition"] == "谐振频率高于目标时"
    assert entry["confidence"] == 0.8


def test_reflect_invalid_json_returns_false():
    """非法 JSON 响应 → 返回 False，不抛异常，memory 不变。"""
    client = _make_client("这不是 JSON")
    memory = StructuredMemory()
    round_summary = {"round": 2}

    result = reflect_on_round(client, "test-model", round_summary, memory)

    assert result is False
    assert len(memory.decisions.recent_strategies) == 0


def test_reflect_client_none_returns_false():
    """client=None → 立即返回 False，不调用 LLM。"""
    memory = StructuredMemory()
    result = reflect_on_round(None, "test-model", {"round": 3}, memory)
    assert result is False


def test_reflect_markdown_wrapped_json():
    """Markdown 代码块包裹的 JSON → 正确解析并写入 memory。"""
    inner = json.dumps(
        {"strategy": "减小 inset_depth", "outcome": "无改善", "lesson": "换参数"}
    )
    markdown = f"```json\n{inner}\n```"
    client = _make_client(markdown)
    memory = StructuredMemory()
    round_summary = {"round": 4, "param": "inset_depth", "delta": -0.2}

    result = reflect_on_round(client, "test-model", round_summary, memory)

    assert result is True
    assert len(memory.decisions.recent_strategies) == 1
    entry = memory.decisions.recent_strategies[0]
    assert entry["strategy"] == "减小 inset_depth"
    assert entry["outcome"] == "无改善"


def test_reflect_extracts_json_from_prose_response():
    payload = json.dumps({"strategy": "调 feed_W", "outcome": "改善", "lesson": "保持小步"})
    client = _make_client(f"经验如下：\n{payload}")
    memory = StructuredMemory()

    result = reflect_on_round(client, "test-model", {"round": 5}, memory)

    assert result is True
    assert memory.decisions.recent_strategies[0]["strategy"] == "调 feed_W"


def test_reflect_low_confidence_does_not_write_structured_memory():
    payload = json.dumps(
        {
            "strategy": "随便调整",
            "outcome": "未知",
            "lesson": "低置信经验不应进入记忆",
            "confidence": 0.2,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()

    result = reflect_on_round(client, "test-model", {"round": 6}, memory, min_confidence=0.6)

    assert result is False
    assert memory.decisions.recent_strategies == []


def test_rolled_back_round_low_confidence():
    payload = json.dumps(
        {
            "strategy": "增大 feed_W",
            "outcome": "自评改善",
            "lesson": "回滚轮不能高置信进入记忆",
            "confidence": 0.9,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()

    result = reflect_on_round(
        client,
        "test-model",
        {"round": 7, "after_metric": -5.0, "improved": False, "rolled_back": True},
        memory,
    )

    assert result is True
    entry = memory.decisions.recent_strategies[0]
    assert entry["confidence"] <= 0.2
    assert entry["rolled_back"] is True
    assert entry["improved"] is False
    assert entry["after_metric"] == -5.0


def test_reflection_write_policy_requires_reusable_content():
    assert should_write_reflection_memory(
        {
            "strategy": "只描述动作",
            "outcome": "改善",
            "lesson": "",
            "failure_pattern": "",
            "effective_action": "",
            "confidence": 0.9,
        },
        min_confidence=0.6,
    ) is False
    assert should_write_reflection_memory(
        {
            "lesson": "feed_W 过小会恶化匹配",
            "confidence": 0.9,
        },
        min_confidence=0.6,
    ) is True


def test_no_improvement_lesson_survives_tiered_gate():
    """无改善经验自评 0.8 → 校准 0.4；统一 0.6 门槛会拦掉，分档门槛（0.3）应放行。"""
    payload = json.dumps(
        {
            "strategy": "增大 patch_L 0.3 mm",
            "outcome": "无改善",
            "lesson": "该方向已饱和，换参数",
            "confidence": 0.8,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()

    result = reflect_on_round(
        client,
        "test-model",
        {"round": 8, "improved": False},
        memory,
        min_confidence=0.6,
    )

    assert result is True
    entry = memory.decisions.recent_strategies[0]
    assert entry["confidence"] == 0.4
    assert entry["confidence_basis"] == "no_improvement"


def test_low_self_confidence_no_improvement_still_dropped():
    """自评 0.3 的无改善经验 → 校准 0.15 < 0.3 分档门槛 → 丢弃。"""
    payload = json.dumps(
        {
            "strategy": "微调",
            "outcome": "无改善",
            "lesson": "不确定是否有用",
            "confidence": 0.3,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()

    result = reflect_on_round(
        client,
        "test-model",
        {"round": 9, "improved": False},
        memory,
        min_confidence=0.6,
    )

    assert result is False
    assert memory.decisions.recent_strategies == []


def test_rolled_back_lesson_writes_under_production_gate():
    """回滚经验自评 0.9 → 封顶 0.2；生产 0.6 门槛下分档门槛（0.15）应放行。"""
    payload = json.dumps(
        {
            "strategy": "增大 feed_W 0.8 mm",
            "outcome": "退化",
            "lesson": "该幅度过大会恶化匹配，回滚",
            "avoid_next": "避免一次增大超过 0.5 mm",
            "confidence": 0.9,
        }
    )
    client = _make_client(payload)
    memory = StructuredMemory()

    result = reflect_on_round(
        client,
        "test-model",
        {"round": 10, "improved": False, "rolled_back": True},
        memory,
        min_confidence=0.6,
    )

    assert result is True
    entry = memory.decisions.recent_strategies[0]
    assert entry["confidence"] <= 0.2
    assert entry["confidence_basis"] == "rolled_back"
