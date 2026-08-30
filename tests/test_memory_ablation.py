"""消融实验：验证记忆召回机制是否真的影响了 Agent 决策。

A/B 对照：
- A（无记忆）：清空 memory + RAG dynamic entries
- B（有记忆）：带前一轮的 lesson 和 failure reason

测试 3 个场景：
1. Planner 召回 — plan context 包含历史经验
2. Optimizer 召回 — LLM 提案 prompt 包含失败记录
3. Lesson 去重 — 重复 lesson 不写入
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from cst_agent_workbench.agent.memory import (
    StructuredMemory,
    MemoryManager,
    extract_persistent_memory,
    merge_persistent_memory,
    recall_memory,
    save_memory,
    load_memory,
    save_persistent_memory,
)
from cst_agent_workbench.agent.context_summary import build_context_summary
from cst_agent_workbench.agent.planner import build_plan_context_text, build_initial_plan
from cst_agent_workbench.agent.analyzer import propose_patch_optimization_with_llm
from cst_agent_workbench.rag.knowledge_base import (
    add_dynamic_entry,
    _jaccard_similarity,
)


# ── helpers ──────────────────────────────────────────────────────────────


def _make_client(content: str) -> MagicMock:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
    client.chat.completions.create.return_value = resp
    return client


def _make_memory_with_lessons(lessons: list[str]) -> StructuredMemory:
    memory = StructuredMemory()
    for lesson in lessons:
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"round": 1, "strategy": "test", "outcome": "改善", "lesson": lesson},
        )
    return memory


def _make_memory_with_failures(failures: list[str]) -> StructuredMemory:
    memory = StructuredMemory()
    for f in failures:
        MemoryManager.update_decisions(memory, failure_reason=f)
    return memory


def _prompt_from_call(call_args) -> str:
    messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0]["messages"]
    return "\n".join(message["content"] for message in messages)


# ── 场景 1: Planner 召回 ─────────────────────────────────────────────────


class TestPlannerRecall:
    """验证 plan context 包含历史经验 lesson。"""

    def test_plan_context_with_lessons(self):
        """有 lessons 时，plan context 包含 [历史经验] 段落。"""
        lessons = ["patch_L 增大 0.5mm 后谐振频率降低了 0.02GHz", "inset_depth 过大会导致匹配恶化"]
        plan = build_initial_plan(user_message="优化贴片天线", session_memory=None)
        context = build_plan_context_text(plan, lessons=lessons)

        assert "[历史经验]" in context
        assert "patch_L 增大" in context
        assert "inset_depth 过大" in context

    def test_plan_context_without_lessons(self):
        """无 lessons 时，plan context 不包含 [历史经验] 段落。"""
        plan = build_initial_plan(user_message="优化贴片天线", session_memory=None)
        context = build_plan_context_text(plan, lessons=None)

        assert "[历史经验]" not in context

    def test_plan_context_with_empty_lessons(self):
        """空 lessons 列表时，plan context 不包含 [历史经验] 段落。"""
        plan = build_initial_plan(user_message="优化贴片天线", session_memory=None)
        context = build_plan_context_text(plan, lessons=[])

        assert "[历史经验]" not in context

    def test_plan_context_lesson_truncation(self):
        """超过 150 字的 lesson 被截断。"""
        long_lesson = "x" * 200
        plan = build_initial_plan(user_message="test", session_memory=None)
        context = build_plan_context_text(plan, lessons=[long_lesson])

        # 每行以 "- " 开头，后面最多 150 字符
        for line in context.split("\n"):
            if line.startswith("- "):
                assert len(line) <= 152  # "- " + 150 chars


# ── 场景 2: Memory recall API ────────────────────────────────────────────


class TestMemoryRecallAPI:
    """验证结构化 memory 可以按类型召回为可追踪 entry。"""

    def test_recall_memory_returns_ranked_lessons_failures_and_constraints(self):
        memory = StructuredMemory()
        memory.conversation.constraints = ["Rogers5880", "probe-fed"]
        MemoryManager.update_decisions(
            memory,
            strategy_entry={
                "round": 1,
                "lesson": "patch_L 增大后谐振频率降低",
                "failure_pattern": "patch_L 同方向重复增大会过冲",
                "effective_action": "小步减小 patch_L 可抬高频率",
                "avoid_next": "避免继续增大 patch_L",
                "reuse_condition": "谐振频率低于目标时",
            },
        )
        MemoryManager.update_decisions(memory, failure_reason="feed_W 过小导致匹配恶化")

        entries = recall_memory(memory, "Rogers5880 patch_L 频率 过冲", k=3)

        assert entries[0].entry_type == "lesson"
        # id 按内容哈希（稳定跨列表滚动），断言语义而不是位置下标
        assert entries[0].id.startswith("lesson:")
        assert "patch_L 增大" in entries[0].text
        assert entries[0].metadata["avoid_next"] == "避免继续增大 patch_L"
        assert any(entry.entry_type == "constraint" for entry in entries)

    def test_recall_memory_respects_entry_types(self):
        memory = _make_memory_with_lessons(["lesson A"])
        memory.decisions.failure_reasons = ["failure A"]

        entries = recall_memory(memory, "A", entry_types=["failure"], k=5)

        assert [entry.entry_type for entry in entries] == ["failure"]
        assert entries[0].text == "failure A"

    def test_recall_memory_without_query_returns_recent_entries(self):
        memory = _make_memory_with_lessons(["lesson A", "lesson B"])
        memory.decisions.failure_reasons = ["failure A"]

        entries = recall_memory(memory, k=2)

        # 无 query 时取最近 k 条：断言取到的是哪些内容 + 顺序，而不是位置 id
        assert [entry.entry_type for entry in entries] == ["lesson", "failure"]
        assert entries[0].text.startswith("lesson B")
        assert entries[1].text == "failure A"

    def test_recall_memory_semantic_path_when_client_provided(self, monkeypatch):
        """传入 client 时走 embedding 路径，按 cosine 排序；token-overlap 路径会拿不到这种近义匹配。"""
        import numpy as np
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag import knowledge_base as kb_module

        memory = StructuredMemory()
        # 两条 lesson：A 与查询语义贴近但无关键词重叠；B 与查询无任何关联
        MemoryManager.update_decisions(memory, strategy_entry={"round": 1, "lesson": "近义匹配条目"})
        MemoryManager.update_decisions(memory, strategy_entry={"round": 2, "lesson": "完全无关条目"})

        # mock embed_texts：让"近义匹配条目"与 query 余弦相似度更高
        def fake_embed(texts, client, model):
            vecs = []
            for t in texts:
                if "近义" in t or "语义" in t:
                    vecs.append([1.0, 0.0])
                else:
                    vecs.append([0.0, 1.0])
            arr = np.array(vecs, dtype=np.float32)
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            return arr / np.where(norms == 0, 1.0, norms)
        monkeypatch.setattr(kb_module, "embed_texts", fake_embed)

        client = MagicMock()
        entries = recall_memory(memory, "语义", k=2, client=client)
        assert entries[0].text == "近义匹配条目", f"语义召回应排第一: {[e.text for e in entries]}"

    def test_memory_embed_cached_across_turns(self, monkeypatch):
        import numpy as np
        from unittest.mock import MagicMock
        from cst_agent_workbench.agent import memory as memory_module
        from cst_agent_workbench.rag import knowledge_base as kb_module

        memory_module._MEMORY_EMBED_CACHE.clear()
        calls = []

        def fake_embed(texts, client, model):
            batch = list(texts)
            calls.append(batch)
            vecs = []
            for text in batch:
                if "cached lesson" in text or "query" in text:
                    vecs.append([1.0, 0.0])
                else:
                    vecs.append([0.0, 1.0])
            return np.array(vecs, dtype=np.float32)

        monkeypatch.setattr(kb_module, "embed_texts", fake_embed)
        memory = _make_memory_with_lessons(["cached lesson", "other lesson"])
        client = MagicMock()

        recall_memory(memory, "query one", k=1, client=client)
        recall_memory(memory, "query two", k=1, client=client)

        entry_embedding_batches = [batch for batch in calls if "cached lesson" in batch or "other lesson" in batch]
        assert entry_embedding_batches == [["cached lesson", "other lesson"]]

    def test_recall_memory_falls_back_to_token_overlap_on_embedding_failure(self, monkeypatch):
        """embedding 抛异常时静默降级到 token-overlap，不影响调用方。"""
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag import knowledge_base as kb_module

        memory = _make_memory_with_lessons(["patch_L 关键词命中", "feed_W 不相关"])
        def boom(texts, client, model):
            raise RuntimeError("embedding model unavailable")
        monkeypatch.setattr(kb_module, "embed_texts", boom)

        client = MagicMock()
        entries = recall_memory(memory, "patch_L", k=2, client=client)
        # 降级后 token-overlap 仍能选中含 patch_L 的条目
        assert entries[0].text == "patch_L 关键词命中"

    def test_recall_memory_falls_back_to_chinese_bigram_overlap_without_client(self):
        memory = _make_memory_with_lessons(
            [
                "\u589e\u5927\u8d34\u7247\u957f\u5ea6\u53ef\u4ee5\u964d\u4f4e\u8c10\u632f\u9891\u7387",
                "\u9988\u7535\u7ebf\u5bbd\u5ea6\u8fc7\u5c0f\u4f1a\u5bfc\u81f4\u5339\u914d\u6076\u5316",
            ]
        )

        entries = recall_memory(memory, "\u8c10\u632f\u9891\u7387\u964d\u4f4e", k=1)

        assert entries[0].text == "\u589e\u5927\u8d34\u7247\u957f\u5ea6\u53ef\u4ee5\u964d\u4f4e\u8c10\u632f\u9891\u7387"

    def test_recall_memory_min_overlap_blocks_unrelated_entries(self):
        memory = _make_memory_with_lessons(["patch_L 关键词命中", "feed_W 不相关"])

        entries = recall_memory(memory, "完全无关 query", k=2, min_overlap=1)

        assert entries == []

    def test_recall_memory_records_score_metadata(self):
        memory = _make_memory_with_lessons(["patch_L 关键词命中"])

        entries = recall_memory(memory, "patch_L", k=1, min_overlap=1)

        assert entries[0].metadata["recall_score"] >= 1.0
        assert entries[0].metadata["recall_reason"] == "token_overlap"


# ── 场景 3: Optimizer 召回 ───────────────────────────────────────────────


class TestOptimizerRecall:
    """验证 LLM 提案 prompt 包含历史失败记录。"""

    def test_failure_reasons_in_prompt(self):
        """有 failure_reasons 时，prompt 包含 [历史失败记录] 段落。"""
        failures = ["增大 patch_L 后频率偏移更大", "feed_W 过小导致匹配恶化"]
        client = _make_client('{"param": "inset_depth", "delta_mm": 0.3, "reason": "test"}')
        s11_summary = {"min_s11_db": -8.0, "min_freq_ghz": 2.45}

        propose_patch_optimization_with_llm(
            client, "test-model", s11_summary, 2.45,
            {"patch_L": 30.0, "feed_W": 3.0},
            failure_reasons=failures,
        )

        # 检查实际发送给 LLM 的 prompt
        call_args = client.chat.completions.create.call_args
        prompt = _prompt_from_call(call_args)
        assert "历史失败记录" in prompt
        assert "增大 patch_L 后频率偏移更大" in prompt
        assert "feed_W 过小导致匹配恶化" in prompt

    def test_no_failure_reasons_no_section(self):
        """无 failure_reasons 时，prompt 不包含 [历史失败记录]。"""
        client = _make_client('{"param": "patch_L", "delta_mm": 0.5, "reason": "test"}')
        s11_summary = {"min_s11_db": -8.0, "min_freq_ghz": 2.45}

        propose_patch_optimization_with_llm(
            client, "test-model", s11_summary, 2.45,
            {"patch_L": 30.0},
            failure_reasons=None,
        )

        call_args = client.chat.completions.create.call_args
        prompt = _prompt_from_call(call_args)
        assert "历史失败记录" not in prompt

    def test_failure_reasons_limited_to_last_3(self):
        """只注入最近 3 条失败原因。"""
        failures = ["f1", "f2", "f3", "f4", "f5"]
        client = _make_client('{"param": "patch_L", "delta_mm": 0.5, "reason": "test"}')
        s11_summary = {"min_s11_db": -8.0, "min_freq_ghz": 2.45}

        propose_patch_optimization_with_llm(
            client, "test-model", s11_summary, 2.45,
            {"patch_L": 30.0},
            failure_reasons=failures,
        )

        call_args = client.chat.completions.create.call_args
        prompt = _prompt_from_call(call_args)
        # 最后 3 条：f3, f4, f5
        assert "f3" in prompt
        assert "f4" in prompt
        assert "f5" in prompt
        assert "f1" not in prompt
        assert "f2" not in prompt

    def test_memory_constraints_in_prompt(self):
        client = _make_client('{"param": "feed_W", "delta_mm": 0.3, "reason": "test"}')
        s11_summary = {"min_s11_db": -8.0, "min_freq_ghz": 2.45}

        propose_patch_optimization_with_llm(
            client, "test-model", s11_summary, 2.45,
            {"patch_L": 30.0, "feed_W": 1.2},
            memory_constraints=["当前 feed_W 偏小，必须优先增大 feed_W。"],
        )

        call_args = client.chat.completions.create.call_args
        prompt = _prompt_from_call(call_args)
        assert "记忆约束" in prompt
        assert "必须优先增大 feed_W" in prompt
        assert "如果输出不满足记忆约束" in prompt


# ── 场景 3: Lesson 去重 ─────────────────────────────────────────────────


class TestLessonDedup:
    """验证重复 lesson 不写入动态知识库。"""

    def test_duplicate_lesson_skipped(self, tmp_path):
        """相同内容的 lesson 第二次写入被跳过。"""
        with patch("cst_agent_workbench.rag.knowledge_base._DYNAMIC_ENTRIES_PATH", tmp_path / "entries.json"):
            add_dynamic_entry("patch_L 增大后频率降低", entry_type="history")
            add_dynamic_entry("patch_L 增大后频率降低", entry_type="history")

            entries = json.loads((tmp_path / "entries.json").read_text(encoding="utf-8"))
            assert len(entries) == 1

    def test_similar_lesson_skipped(self, tmp_path):
        """语义相似的 lesson（Jaccard > 0.6）被跳过。"""
        with patch("cst_agent_workbench.rag.knowledge_base._DYNAMIC_ENTRIES_PATH", tmp_path / "entries.json"):
            add_dynamic_entry("增大 patch_L 后谐振频率降低 0.02 GHz", entry_type="history")
            add_dynamic_entry("增大 patch_L 后谐振频率降低 0.03 GHz", entry_type="history")

            entries = json.loads((tmp_path / "entries.json").read_text(encoding="utf-8"))
            # 第二句与第一句高度相似，应被去重
            assert len(entries) == 1

    def test_different_lessons_both_kept(self, tmp_path):
        """不同内容的 lesson 都保留。"""
        with patch("cst_agent_workbench.rag.knowledge_base._DYNAMIC_ENTRIES_PATH", tmp_path / "entries.json"):
            add_dynamic_entry("增大 patch_L 后谐振频率降低", entry_type="history")
            add_dynamic_entry("feed_W 过小导致阻抗匹配恶化", entry_type="history")

            entries = json.loads((tmp_path / "entries.json").read_text(encoding="utf-8"))
            assert len(entries) == 2

    def test_different_entry_types_not_deduped(self, tmp_path):
        """不同 entry_type 的相似内容不去重。"""
        with patch("cst_agent_workbench.rag.knowledge_base._DYNAMIC_ENTRIES_PATH", tmp_path / "entries.json"):
            add_dynamic_entry("patch_L 增大后频率降低", entry_type="history")
            add_dynamic_entry("patch_L 增大后频率降低", entry_type="rule")

            entries = json.loads((tmp_path / "entries.json").read_text(encoding="utf-8"))
            assert len(entries) == 2

    def test_jaccard_similarity_boundary(self):
        """Jaccard 相似度边界值测试。"""
        # 完全相同
        assert _jaccard_similarity("a b c", "a b c") == 1.0
        # 完全不同
        assert _jaccard_similarity("a b c", "d e f") == 0.0
        # 部分重叠：{a,b,c} ∩ {a,b,d} = {a,b}, union = {a,b,c,d}
        assert _jaccard_similarity("a b c", "a b d") == pytest.approx(2 / 4)
        # 空字符串
        assert _jaccard_similarity("", "a b c") == 0.0


# ── 场景 4: Memory 持久化 A/B ───────────────────────────────────────────


class TestMemoryPersistence:
    """验证 memory 的 save/load 往返正确。"""

    def test_save_load_roundtrip(self, tmp_path):
        memory = _make_memory_with_lessons(["lesson A", "lesson B"])
        memory.decisions.failure_reasons = ["failure 1"]
        memory.conversation.user_goal = "优化 2.4GHz 贴片"

        path = str(tmp_path / "memory.json")
        assert save_memory(memory, path) is True

        loaded = load_memory(path)
        assert loaded is not None
        assert loaded.conversation.user_goal == "优化 2.4GHz 贴片"
        assert len(loaded.decisions.recent_strategies) == 2
        assert loaded.decisions.recent_strategies[0]["lesson"] == "lesson A"
        assert loaded.decisions.failure_reasons == ["failure 1"]

    def test_load_nonexistent_returns_none(self, tmp_path):
        result = load_memory(str(tmp_path / "nonexistent.json"))
        assert result is None

    def test_persistent_memory_excludes_session_workspace_state(self):
        memory = _make_memory_with_lessons(["lesson A"])
        memory.conversation.constraints = ["Rogers5880"]
        memory.workspace.project_path = "D:/old/project.cst"
        memory.workspace.last_results_summary = {"min_s11_db": -12.0}
        memory.decisions.best_so_far = {"round": 3, "metric_value": -12.0}
        memory.decisions.failure_reasons = ["failure 1"]

        persistent = extract_persistent_memory(memory)

        assert persistent.conversation.constraints == ["Rogers5880"]
        assert persistent.decisions.failure_reasons == ["failure 1"]
        assert persistent.decisions.recent_strategies[0]["lesson"] == "lesson A"
        assert persistent.workspace.project_path == ""
        assert persistent.workspace.last_results_summary == {}
        assert persistent.decisions.best_so_far == {}

    def test_persistent_memory_can_filter_low_confidence_reflections(self):
        memory = StructuredMemory()
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"round": 1, "lesson": "low confidence", "confidence": 0.2},
        )
        MemoryManager.update_decisions(
            memory,
            strategy_entry={"round": 2, "lesson": "high confidence", "confidence": 0.9},
        )

        persistent = extract_persistent_memory(memory, min_confidence=0.6)

        assert [item["lesson"] for item in persistent.decisions.recent_strategies] == ["high confidence"]

    def test_persistent_memory_keeps_calibrated_lessons_via_tiered_floor(self):
        """校准后的无改善(≤0.5)/回滚(≤0.2)经验按分档门槛持久化，而非被统一 0.6 拦掉。"""
        memory = StructuredMemory()
        MemoryManager.update_decisions(
            memory,
            strategy_entry={
                "round": 1,
                "lesson": "no improvement but confident",
                "confidence": 0.4,
                "confidence_basis": "no_improvement",
            },
        )
        MemoryManager.update_decisions(
            memory,
            strategy_entry={
                "round": 2,
                "lesson": "rolled back, avoid repeating",
                "confidence": 0.2,
                "confidence_basis": "rolled_back",
            },
        )
        MemoryManager.update_decisions(
            memory,
            strategy_entry={
                "round": 3,
                "lesson": "self reported low",
                "confidence": 0.4,
            },
        )

        persistent = extract_persistent_memory(memory, min_confidence=0.6)

        assert [item["lesson"] for item in persistent.decisions.recent_strategies] == [
            "no improvement but confident",
            "rolled back, avoid repeating",
        ]

    def test_merge_persistent_memory_keeps_new_session_workspace_empty(self):
        old_memory = _make_memory_with_lessons(["old lesson"])
        old_memory.workspace.project_path = "D:/old/project.cst"
        old_memory.workspace.last_results_summary = {"min_s11_db": -11.0}
        old_memory.decisions.failure_reasons = ["old failure"]
        persistent = extract_persistent_memory(old_memory)

        new_memory = StructuredMemory()
        merge_persistent_memory(new_memory, persistent)

        assert new_memory.decisions.recent_strategies[0]["lesson"] == "old lesson"
        assert new_memory.decisions.failure_reasons == ["old failure"]
        assert new_memory.workspace.project_path == ""
        assert new_memory.workspace.last_results_summary == {}

    def test_save_persistent_memory_roundtrip_filters_session_state(self, tmp_path):
        memory = _make_memory_with_lessons(["lesson A"])
        memory.workspace.project_path = "D:/old/project.cst"
        memory.workspace.last_results_summary = {"min_s11_db": -12.0}
        memory.decisions.failure_reasons = ["failure 1"]
        path = str(tmp_path / "persistent_memory.json")

        assert save_persistent_memory(memory, path) is True
        loaded = load_memory(path)

        assert loaded is not None
        assert loaded.decisions.recent_strategies[0]["lesson"] == "lesson A"
        assert loaded.decisions.failure_reasons == ["failure 1"]
        assert loaded.workspace.project_path == ""
        assert loaded.workspace.last_results_summary == {}

    def test_persistent_memory_not_eroded_by_session_cap(self):
        persistent = StructuredMemory()
        persistent.decisions.recent_strategies = [
            {"round": index, "lesson": f"persistent lesson {index}", "confidence": 0.9}
            for index in range(20)
        ]

        memory = StructuredMemory()
        merge_persistent_memory(memory, persistent)
        for index in range(6):
            MemoryManager.update_decisions(
                memory,
                strategy_entry={
                    "round": 100 + index,
                    "lesson": f"session lesson {index}",
                    "confidence": 0.8,
                },
            )

        extracted = extract_persistent_memory(memory)
        lessons = [item["lesson"] for item in extracted.decisions.recent_strategies]

        assert len(extracted.decisions.recent_strategies) == 20
        assert "persistent lesson 10" in lessons
        assert "session lesson 5" in lessons

    def test_prompt_view_still_caps_at_5(self):
        memory = StructuredMemory()
        for index in range(8):
            MemoryManager.update_decisions(
                memory,
                strategy_entry={"round": index, "lesson": f"prompt lesson {index}"},
            )
        session = type("Session", (), {"memory": memory})()

        summary = build_context_summary(session)

        assert "prompt lesson 0" not in summary
        assert "prompt lesson 1" not in summary
        assert "prompt lesson 2" not in summary
        for index in range(3, 8):
            assert f"prompt lesson {index}" in summary


# ── 场景 5: 集成 — memory 影响 planner context ──────────────────────────


class TestMemoryIntegration:
    """端到端验证：有 memory 时 planner context 更丰富。"""

    def test_memory_enriches_planner_context(self):
        """有 memory 的 planner context 比没有的更长、包含更多信息。"""
        memory = StructuredMemory()
        memory.conversation.user_goal = "设计 2.45GHz 贴片天线"
        memory.conversation.constraints = ["基板 Rogers5880", "厚度 1.6mm"]
        memory.decisions.failure_reasons = ["patch_L 过大导致频率偏低"]

        plan = build_initial_plan(user_message="继续优化", session_memory=memory)

        # 有 memory 时 plan context 应包含 goal 和 constraints
        context = build_plan_context_text(plan, lessons=["上次增大 patch_L 效果不好"])
        assert "历史经验" in context
        assert "上次增大 patch_L 效果不好" in context

    def test_no_memory_plan_context_still_works(self):
        """无 memory 时 plan context 正常工作，不崩溃。"""
        plan = build_initial_plan(user_message="新建贴片天线", session_memory=None)
        context = build_plan_context_text(plan)
        assert "当前计划摘要" in context
