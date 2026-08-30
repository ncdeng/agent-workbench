"""RAG 检索的 provenance 与阈值语义回归测试。

覆盖审计中确认的三处静默失效：
- filter_type="history" 拉取"运行时学到的经验"时混入 CST 手册 chunk（来源污染）；
- min_score 防噪阈值被"至少回一条"与无阈值关键词兜底架空；
- 动态知识库把新条目排序淘汰后仍返回 True，导致 reflection 经验静默丢失。
"""
from __future__ import annotations

import json

import pytest

from cst_agent_workbench.rag import knowledge_base as kb_module
from cst_agent_workbench.rag.knowledge_base import add_dynamic_entry


class _StubKB:
    """可控打分的 KB 替身，避免测试依赖真实 embedding。"""

    def __init__(self, pairs):
        self._pairs = pairs

    def retrieve(self, query, client, model="", top_k=3, min_score=0.0,
                 filter_type=None, with_scores=False, design_signature=""):
        pairs = [(text, score) for text, score in self._pairs if score >= min_score][:top_k]
        return pairs if with_scores else [text for text, _ in pairs]


class TestDocStoreProvenance:
    """手册 chunk 是"资料"，不能冒充 agent 自己积累的经验。"""

    def test_history_filter_excludes_doc_store(self, monkeypatch):
        called = []
        monkeypatch.setattr(kb_module, "_kb", _StubKB([("learned lesson", 0.9)]))
        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_pdf_knowledge",
            lambda query, top_k=3: called.append(query) or ["manual chunk"],
        )

        results = kb_module.retrieve_antenna_rules("q", object(), filter_type="history", top_k=3)

        assert results == ["learned lesson"]
        assert called == [], "doc store must not be queried for learned-experience recall"

    @pytest.mark.parametrize("filter_type", [None, "rule", "strategy"])
    def test_doc_store_still_merges_for_knowledge_queries(self, monkeypatch, filter_type):
        monkeypatch.setattr(kb_module, "_kb", _StubKB([("expert rule", 0.9)]))
        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_pdf_knowledge",
            lambda query, top_k=3: ["manual chunk"],
        )

        results = kb_module.retrieve_antenna_rules("q", object(), filter_type=filter_type, top_k=3)

        assert "expert rule" in results
        assert any(item.startswith("[CST官方文档] ") for item in results), "doc provenance marker must be kept"

    def test_unified_recall_labels_doc_entries_instead_of_stripping_marker(self, monkeypatch):
        """即便文档条目进入统一召回，也必须标成 source=doc 而不是伪装成 history。"""
        from cst_agent_workbench.agent.memory import StructuredMemory, unified_recall

        monkeypatch.setattr(
            kb_module,
            "retrieve_antenna_rules",
            lambda *a, **k: [("[CST官方文档] manual chunk", 0.9)],
        )

        entries = unified_recall(
            StructuredMemory(), "q", client=object(), k=3,
            include_legacy_dynamic=True,
        )

        assert [entry.metadata["source"] for entry in entries] == ["doc"]
        assert [entry.entry_type for entry in entries] == ["doc"]
        assert entries[0].text == "manual chunk"

    def test_structured_document_channel_keeps_source_score_and_chunk(self, monkeypatch):
        from cst_agent_workbench import config

        monkeypatch.setattr(config, "RAG_RERANK_ENABLED", False)
        hit = {
            "text": "Waveguide port definition",
            "source_path": "3D/ports/waveguide.htm",
            "source_type": "html",
            "chunk_idx": 4,
            "score": 0.81,
        }
        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_document_knowledge",
            lambda query, top_k=3, **kwargs: [hit],
        )

        results = kb_module.retrieve_official_document_hits(
            "waveguide port",
            top_k=3,
            rewrite=False,
        )

        assert results[0]["source_path"] == "3D/ports/waveguide.htm"
        assert results[0]["chunk_idx"] == 4
        assert results[0]["score"] == 0.81
        assert results[0]["matched_query"] == "waveguide port"

    def test_english_translations_are_fused_before_top_k(self, monkeypatch):
        from cst_agent_workbench import config

        monkeypatch.setattr(config, "RAG_DOCUMENT_QUERY_TRANSLATION", True)
        monkeypatch.setattr(config, "RAG_RERANK_ENABLED", False)
        monkeypatch.setattr(
            kb_module,
            "_translate_document_query",
            lambda *args, **kwargs: ["first English query", "second English query"],
        )

        def fake_query(query, top_k=3, **kwargs):
            if query == "first English query":
                return [{
                    "text": "weak",
                    "source_path": "weak.htm",
                    "chunk_idx": 0,
                    "score": 0.4,
                }]
            return [{
                "text": "strong",
                "source_path": "strong.htm",
                "chunk_idx": 0,
                "score": 0.9,
            }]

        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_document_knowledge",
            fake_query,
        )
        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.is_document_store_queryable",
            lambda: True,
        )

        results = kb_module.retrieve_official_document_hits(
            "中文问题",
            client=object(),
            top_k=1,
        )

        assert results[0]["source_path"] == "strong.htm"
        assert results[0]["matched_query"] == "second English query"

    def test_candidate_recall_reranks_before_source_deduplication(self, monkeypatch):
        from cst_agent_workbench import config
        from cst_agent_workbench.rag import reranker as reranker_module

        monkeypatch.setattr(config, "RAG_RERANK_ENABLED", True)
        monkeypatch.setattr(config, "RAG_RERANK_CANDIDATE_K", 20)
        observed = {}

        def fake_query(query, top_k=3, deduplicate_sources=True):
            observed["candidate_k"] = top_k
            observed["deduplicate_sources"] = deduplicate_sources
            return [
                {"text": "dense winner", "source_path": "same.htm", "chunk_idx": 0, "score": 0.9},
                {"text": "rerank winner", "source_path": "same.htm", "chunk_idx": 1, "score": 0.7},
                {"text": "other", "source_path": "other.htm", "chunk_idx": 0, "score": 0.6},
            ]

        def fake_rerank(queries, hits):
            observed["rerank_candidates"] = [hit["text"] for hit in hits]
            return [dict(hits[1], rerank_score=0.99), dict(hits[2], rerank_score=0.5), hits[0]]

        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_document_knowledge",
            fake_query,
        )
        monkeypatch.setattr(reranker_module, "rerank_document_hits", fake_rerank)

        results = kb_module.retrieve_official_document_hits(
            "waveguide port",
            top_k=2,
            rewrite=False,
        )

        assert observed == {
            "candidate_k": 20,
            "deduplicate_sources": False,
            "rerank_candidates": ["dense winner", "rerank winner", "other"],
        }
        assert [hit["text"] for hit in results] == ["rerank winner", "other"]

    def test_eval_controls_candidate_pool_and_can_disable_source_deduplication(self, monkeypatch):
        from cst_agent_workbench import config

        monkeypatch.setattr(config, "RAG_RERANK_ENABLED", False)
        observed = {}

        def fake_query(query, top_k=3, deduplicate_sources=True):
            observed["candidate_k"] = top_k
            observed["store_dedup"] = deduplicate_sources
            return [
                {"text": "first", "source_path": "same.htm", "chunk_idx": 0, "score": 0.9},
                {"text": "second", "source_path": "same.htm", "chunk_idx": 1, "score": 0.8},
            ]

        monkeypatch.setattr(
            "cst_agent_workbench.rag.chroma_store.query_document_knowledge",
            fake_query,
        )

        results = kb_module.retrieve_official_document_hits(
            "waveguide port",
            top_k=2,
            candidate_k=20,
            deduplicate_sources=False,
            rewrite=False,
        )

        assert observed == {"candidate_k": 20, "store_dedup": False}
        assert [hit["chunk_idx"] for hit in results] == [0, 1]


class TestMinScoreGate:
    """显式阈值意味着"宁可不给"，不能被兜底路径绕开。"""

    @staticmethod
    def _kb_with_scores(scores):
        """构造直接可控 cosine 的真实 KB 实例（不经 stub，覆盖 retrieve 内部的阈值分支）。

        直接锁定 _embeddings 与 _embedding_model，并让 ensure_indexed 成为 no-op，
        避免触碰共享的磁盘 embedding 缓存（会因维度不匹配而重建）。
        """
        import numpy as np

        kb = kb_module.AntennaKnowledgeBase.__new__(kb_module.AntennaKnowledgeBase)
        kb._entries = [f"entry {i}" for i in range(len(scores))]
        kb._entry_types = ["rule"] * len(scores)
        kb._confidences = [1.0] * len(scores)
        kb._design_signatures = [""] * len(scores)
        kb._embeddings = np.array([[float(s)] for s in scores], dtype=np.float32)
        kb._embedding_model = "stub"
        kb._embedding_provider = "stub"
        kb._embedding_effective_model = "stub"
        kb.ensure_indexed = lambda client, model="": None
        kb._rebuild_if_dim_mismatch = lambda client, model, expected_dim: None
        kb._embed = lambda texts, client, model: np.array([[1.0]], dtype=np.float32)
        return kb

    def test_kb_retrieve_returns_nothing_when_all_below_threshold(self):
        """KB 内部的 min_score 分支：不能再"至少回一条"把噪声塞进 prompt。"""
        kb = self._kb_with_scores([0.05, 0.02])

        assert kb.retrieve("q", object(), min_score=0.30) == []

    def test_kb_retrieve_keeps_legacy_fallback_without_threshold(self):
        kb = self._kb_with_scores([0.05, 0.02])

        assert kb.retrieve("q", object(), min_score=0.0) != []

    def test_below_threshold_returns_nothing(self, monkeypatch):
        monkeypatch.setattr(kb_module, "_kb", _StubKB([("barely related", 0.05)]))
        fallback_calls = []
        monkeypatch.setattr(
            kb_module,
            "_keyword_fallback",
            lambda *a, **k: fallback_calls.append(a) or ["keyword noise"],
        )

        results = kb_module.retrieve_antenna_rules("q", object(), top_k=3, min_score=0.30)

        assert results == []
        assert fallback_calls == [], "explicit min_score must not fall back to unfiltered keyword match"

    def test_without_threshold_keeps_legacy_fallback(self, monkeypatch):
        monkeypatch.setattr(kb_module, "_kb", _StubKB([]))
        monkeypatch.setattr(kb_module, "_keyword_fallback", lambda *a, **k: ["keyword result"])

        results = kb_module.retrieve_antenna_rules("q", object(), top_k=3, min_score=0.0)

        assert results == ["keyword result"]


class TestDynamicEntryEviction:
    def test_returns_false_when_new_entry_is_immediately_evicted(self, monkeypatch, tmp_path):
        """被排序淘汰的经验必须如实返回 False，否则 reflection 的丢失诊断永远不触发。"""
        tmp_file = tmp_path / "dynamic_entries.json"
        monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
        monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRY_LIMIT", 2)

        assert add_dynamic_entry("high confidence A", entry_type="history", confidence=0.95) is True
        assert add_dynamic_entry("high confidence B", entry_type="history", confidence=0.94) is True
        assert add_dynamic_entry("low confidence C", entry_type="history", confidence=0.61) is False

        persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
        assert "low confidence C" not in [entry["text"] for entry in persisted]

    def test_confidenceless_entries_no_longer_outrank_reflection_lessons(self, monkeypatch, tmp_path):
        """无 confidence 的批量导入条目过去默认 1.0，会永久挤掉带自评分的新经验。"""
        tmp_file = tmp_path / "dynamic_entries.json"
        monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
        monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRY_LIMIT", 1)

        assert add_dynamic_entry("bulk imported line", entry_type="history") is True
        assert add_dynamic_entry("reflection lesson", entry_type="history", confidence=0.8) is True

        persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
        assert [entry["text"] for entry in persisted] == ["reflection lesson"]
