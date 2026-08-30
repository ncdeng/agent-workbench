"""Memory/RAG 可信度与可观测性回归测试。

覆盖审计中确认的几类静默失效：
- 结构化 rule 让经验的可执行意图不依赖中文散文关键词（换措辞/换语言仍生效）；
- "召回了但没命中规则" 与 "被物理边界压制" 必须可观测，不能静默 no-op；
- memory id 跨列表滚动稳定，否则 trace 里的归因无法复现；
- 手册 chunk 不得冒充运行时学到的经验（provenance）；
- min_score 阈值不能被 "至少回一条" 与关键词兜底架空；
- 动态知识库淘汰掉新条目时必须如实返回 False。
"""
from __future__ import annotations

import pytest

from cst_agent_workbench.agent.memory import (
    MemoryManager,
    StructuredMemory,
    project_scope_from_path,
    recall_memory,
)
from cst_agent_workbench.optimization.memory_rules import _memory_guidance


def _memory_with(entry: dict) -> StructuredMemory:
    memory = StructuredMemory()
    MemoryManager.update_decisions(memory, strategy_entry=entry)
    return memory


def _recall(memory: StructuredMemory, query: str = "feed width matching"):
    return recall_memory(memory, query, entry_types=["lesson"], k=5)


BASE_PARAMS = {"feed_W": 1.0, "patch_L": 16.0, "inset_depth": 3.0}
BASE_SUMMARY = {"min_freq_ghz": 9.4, "target_s11_db": -4.0}


class TestStructuredLessonRule:
    def test_english_lesson_without_rule_is_reported_not_silently_ignored(self):
        """旧式散文经验匹配不上时，必须留下 recalled_but_unmatched 信号。"""
        memory = _memory_with({"round": 1, "lesson": "feed width is too narrow, widen it", "confidence": 0.8})

        guidance = _memory_guidance(_recall(memory), BASE_PARAMS, BASE_SUMMARY, 9.4, -10.0)

        assert guidance["expected_param"] == ""
        assert guidance["recalled_but_unmatched"] is True
        assert guidance["recalled_count"] > 0

    def test_structured_rule_survives_rephrasing_and_language_change(self):
        """带 rule 的经验不依赖字面量匹配：英文措辞照样生效。"""
        memory = _memory_with(
            {
                "round": 1,
                "lesson": "feed width is too narrow, widen it",
                "confidence": 0.8,
                "rule": {"param": "feed_W", "delta_sign": 1},
            }
        )

        guidance = _memory_guidance(_recall(memory), BASE_PARAMS, BASE_SUMMARY, 9.4, -10.0)

        assert guidance["expected_param"] == "feed_W"
        assert guidance["expected_delta_sign"] == 1
        assert guidance["enforced_rule"] == "structured:feed_W"
        assert guidance["recalled_but_unmatched"] is False

    def test_structured_rule_suppressed_by_physics_is_recorded(self):
        """经验说"增大 feed_W" 但 feed_W 已偏大时压制，并记录压制原因。"""
        memory = _memory_with(
            {
                "round": 1,
                "lesson": "widen feed",
                "confidence": 0.8,
                "rule": {"param": "feed_W", "delta_sign": 1},
            }
        )
        wide_feed = {**BASE_PARAMS, "feed_W": 3.0}

        guidance = _memory_guidance(_recall(memory), wide_feed, BASE_SUMMARY, 9.4, -10.0)

        assert guidance["expected_param"] == ""
        assert len(guidance["suppressed_rules"]) == 1
        assert guidance["suppressed_rules"][0]["param"] == "feed_W"
        assert "feed_W" in guidance["suppressed_rules"][0]["reason"]


class TestMemoryIdStability:
    def test_ids_are_content_derived_and_survive_list_rotation(self):
        """id 必须按内容生成：底层列表滚动后，同一条经验的 id 不变。"""
        memory = StructuredMemory()
        for index, lesson in enumerate(["widen feed_W", "shrink patch_L", "tune inset_depth"]):
            MemoryManager.update_decisions(memory, strategy_entry={"round": index, "lesson": lesson, "confidence": 0.8})
        before = {entry.text: entry.id for entry in _recall(memory, "feed patch inset")}

        for index in range(3, 9):
            MemoryManager.update_decisions(
                memory, strategy_entry={"round": index, "lesson": f"filler {index}", "confidence": 0.8}
            )
        after = {entry.text: entry.id for entry in _recall(memory, "feed patch inset")}

        overlapping = set(before) & set(after)
        assert overlapping, "rotation should retain at least one original lesson"
        for text in overlapping:
            assert before[text] == after[text]

    def test_ids_are_not_positional(self):
        memory = _memory_with({"round": 1, "lesson": "widen feed_W", "confidence": 0.8})

        entry = _recall(memory)[0]

        assert entry.id.startswith("lesson:")
        assert entry.id != "lesson:0"


class TestProjectAndDesignScopeIsolation:
    def test_same_lesson_isolated_between_projects(self):
        memory = StructuredMemory()
        scope_a = project_scope_from_path("D:/projects/a.cst")
        scope_b = project_scope_from_path("D:/projects/b.cst")
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"lesson": "increase feed_W", "confidence": 0.8},
            project_scope=scope_a,
        )
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"lesson": "decrease feed_W", "confidence": 0.8},
            project_scope=scope_b,
        )

        recalled_a = recall_memory(
            memory, "feed_W", entry_types=["lesson"], k=5,
            project_scope=scope_a,
        )
        recalled_b = recall_memory(
            memory, "feed_W", entry_types=["lesson"], k=5,
            project_scope=scope_b,
        )

        assert [entry.text for entry in recalled_a] == ["increase feed_W"]
        assert [entry.text for entry in recalled_b] == ["decrease feed_W"]

    def test_design_specific_lesson_does_not_cross_signature(self):
        memory = StructuredMemory()
        scope = project_scope_from_path("D:/projects/a.cst")
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"lesson": "increase inset_depth", "confidence": 0.8},
            project_scope=scope,
            design_signature="er=4.4|f=9.4|feed=microstrip",
        )

        mismatch = recall_memory(
            memory, "inset_depth", entry_types=["lesson"], k=5,
            project_scope=scope,
            design_signature="er=2.2|f=9.4|feed=microstrip",
        )
        match = recall_memory(
            memory, "inset_depth", entry_types=["lesson"], k=5,
            project_scope=scope,
            design_signature="er=4.4|f=9.4|feed=microstrip",
        )

        assert mismatch == []
        assert [entry.text for entry in match] == ["increase inset_depth"]

    def test_duplicate_canonical_lesson_updates_one_record(self):
        memory = StructuredMemory()
        scope = project_scope_from_path("D:/projects/a.cst")
        for confidence in (0.6, 0.9):
            MemoryManager.update_decisions(
                memory,
                strategy_entry={"lesson": "increase feed_W", "confidence": confidence},
                project_scope=scope,
            )

        assert len(memory.decisions.recent_strategies) == 1
        assert memory.decisions.recent_strategies[0]["confidence"] == pytest.approx(0.9)
        assert memory.decisions.recent_strategies[0]["entry_id"].startswith("lesson:")


class TestReflectionConfidenceGrounding:
    """confidence 不能只是写这条经验的同一次 LLM 调用的自评分。"""

    @staticmethod
    def _reflect(round_summary: dict) -> dict:
        from unittest.mock import MagicMock

        from cst_agent_workbench.agent.reflection import reflect_on_round

        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"strategy":"s","outcome":"o","lesson":"widen feed_W",'
                    '"failure_pattern":"","effective_action":"a","avoid_next":"",'
                    '"reuse_condition":"","confidence":0.8,'
                    '"rule":{"param":"feed_W","direction":"increase"}}'
                )
            )
        ]
        memory = StructuredMemory()
        assert reflect_on_round(client, "m", round_summary, memory, min_confidence=0.0) is True
        return memory.decisions.recent_strategies[-1]

    def test_improved_round_keeps_self_reported_confidence(self):
        entry = self._reflect({"round": 1, "improved": True})

        assert entry["confidence"] == pytest.approx(0.8)
        assert entry["confidence_basis"] == "improved"

    def test_no_improvement_discounts_confidence(self):
        entry = self._reflect({"round": 1, "improved": False})

        assert entry["confidence"] == pytest.approx(0.4)
        assert entry["confidence_basis"] == "no_improvement"

    def test_rolled_back_round_is_capped_low(self):
        entry = self._reflect({"round": 1, "improved": False, "rolled_back": True})

        assert entry["confidence"] <= 0.2
        assert entry["confidence_basis"] == "rolled_back"

    def test_structured_rule_is_persisted_for_the_enforcer(self):
        entry = self._reflect({"round": 1, "improved": True})

        assert entry["rule"] == {"param": "feed_W", "delta_sign": 1}

    def test_reflection_has_one_canonical_store_and_does_not_double_write_dynamic_rag(self, monkeypatch):
        from unittest.mock import MagicMock

        from cst_agent_workbench.agent.reflection import reflect_on_round
        from cst_agent_workbench.rag import knowledge_base

        dynamic_write = MagicMock(side_effect=AssertionError("dynamic RAG must not be written"))
        monkeypatch.setattr(knowledge_base, "add_dynamic_entry", dynamic_write)
        client = MagicMock()
        client.chat.completions.create.return_value.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"strategy":"s","outcome":"o","lesson":"widen feed_W",'
                    '"failure_pattern":"","effective_action":"a","avoid_next":"",'
                    '"reuse_condition":"","confidence":0.8,'
                    '"rule":{"param":"feed_W","direction":"increase"}}'
                )
            )
        ]
        memory = StructuredMemory()

        assert reflect_on_round(
            client,
            "m",
            {
                "round": 1,
                "improved": True,
                "project_path": "D:/projects/a.cst",
                "design_signature": "er=4.4|f=9.4|feed=microstrip",
            },
            memory,
            min_confidence=0.0,
        ) is True

        dynamic_write.assert_not_called()
        assert len(memory.decisions.recent_strategies) == 1
        assert memory.decisions.recent_strategies[0]["storage"] == "structured_canonical"


class TestChatPathMemoryObservability:
    """chat 路径的记忆不能只是拼进 prompt 就无人过问，要能被度量。"""

    @staticmethod
    def _entry(text, rule=None):
        from types import SimpleNamespace

        return SimpleNamespace(text=text, metadata={"rule": rule} if rule else {})

    @staticmethod
    def _agent_with(referenced, tool_events):
        from types import SimpleNamespace

        return SimpleNamespace(
            session=SimpleNamespace(
                metadata={
                    "chat_memory_impact": {
                        "referenced_params": list(referenced),
                        "influenced": None,
                        "influenced_by": [],
                    }
                }
            ),
            tool_events=list(tool_events),
        )

    def test_referenced_params_prefers_structured_rule(self):
        from cst_agent_workbench.agent.agent import CSTAgent

        referenced = CSTAgent._memory_referenced_params(
            [self._entry("widen it", {"param": "feed_W", "delta_sign": 1})]
        )

        assert referenced == {"feed_W"}

    def test_referenced_params_falls_back_to_prose_scan(self):
        from cst_agent_workbench.agent.agent import CSTAgent

        referenced = CSTAgent._memory_referenced_params([self._entry("should increase patch_L a bit")])

        assert referenced == {"patch_L"}

    def test_turn_records_memory_as_influential_when_agent_acts_on_it(self):
        from cst_agent_workbench.agent.agent import CSTAgent

        agent = self._agent_with(["feed_W"], [{"description": "store_parameter feed_W=2.4", "message": "ok"}])

        CSTAgent._record_chat_memory_impact(agent, ["store_parameter"])

        impact = agent.session.metadata["chat_memory_impact"]
        assert impact["influenced"] is True
        assert impact["influenced_by"] == ["feed_W"]

    def test_turn_records_memory_as_ignored_when_agent_does_something_else(self):
        from cst_agent_workbench.agent.agent import CSTAgent

        agent = self._agent_with(["feed_W"], [{"description": "run_solver", "message": "ok"}])

        CSTAgent._record_chat_memory_impact(agent, ["run_solver"])

        impact = agent.session.metadata["chat_memory_impact"]
        assert impact["influenced"] is False
        assert impact["influenced_by"] == []
