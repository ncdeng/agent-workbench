"""
RAG Retrieval Quality Evaluation
Metrics: Recall@3, MRR
"""

import json
from typing import List

import numpy as np
import pytest

import cst_agent_workbench.rag.knowledge_base as kb_module
from cst_agent_workbench.rag.knowledge_base import (
    AntennaKnowledgeBase,
    add_dynamic_entry,
    _keyword_fallback,
)

# ---------------------------------------------------------------------------
# Annotated evaluation dataset
# (query, expected_keywords_in_result)
# ---------------------------------------------------------------------------
ANNOTATED_CASES = [
    ("谐振频率偏高如何调整", ["patch_L", "增大"]),
    ("S11 深度不足匹配差", ["inset_depth", "feed_W"]),
    ("优化步长建议", ["2%", "5%", "0.1"]),
    ("带宽如何提升", ["substrate_h", "带宽"]),
    ("patch_W 的作用", ["patch_W", "辐射"]),
]


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def recall_at_k(results: List[str], keywords: List[str]) -> float:
    """results 中至少一条包含任意 keyword 则 recall=1.0，否则 0.0。"""
    for r in results:
        if any(kw in r for kw in keywords):
            return 1.0
    return 0.0


def reciprocal_rank(results: List[str], keywords: List[str]) -> float:
    """第一条含 keyword 的结果排名的倒数。"""
    for i, r in enumerate(results, 1):
        if any(kw in r for kw in keywords):
            return 1.0 / i
    return 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_keyword_fallback_recall():
    """Recall@3 >= 0.8 across all annotated cases."""
    recalls = []
    for query, keywords in ANNOTATED_CASES:
        results = _keyword_fallback(query, top_k=3)
        recalls.append(recall_at_k(results, keywords))
    mean_recall = sum(recalls) / len(recalls)
    assert mean_recall >= 0.8, (
        f"Recall@3 = {mean_recall:.2f} < 0.8. Per-case: "
        + str(list(zip([q for q, _ in ANNOTATED_CASES], recalls)))
    )


def test_keyword_fallback_mrr():
    """MRR >= 0.6 across all annotated cases."""
    rrs = []
    for query, keywords in ANNOTATED_CASES:
        results = _keyword_fallback(query, top_k=3)
        rrs.append(reciprocal_rank(results, keywords))
    mrr = sum(rrs) / len(rrs)
    assert mrr >= 0.6, (
        f"MRR = {mrr:.2f} < 0.6. Per-case: "
        + str(list(zip([q for q, _ in ANNOTATED_CASES], rrs)))
    )


def _expected_overlap_fallback(query: str, top_k: int = 3) -> List[str]:
    texts = [entry.text for entry in kb_module.KNOWLEDGE_ENTRIES]
    scored = [
        (kb_module._token_overlap_score(query, text), index, text)
        for index, text in enumerate(texts)
    ]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(reverse=True)
    if not scored:
        return texts[:top_k]
    return [text for _, _, text in scored[:top_k]]


def test_keyword_fallback_no_positional_index(monkeypatch):
    monkeypatch.setattr(
        kb_module,
        "KNOWLEDGE_ENTRIES",
        [
            kb_module.KnowledgeEntry("unrelated first entry", "rule"),
            kb_module.KnowledgeEntry("another unrelated entry", "rule"),
            kb_module.KnowledgeEntry("频率 target overlap entry", "rule"),
        ],
    )

    results = _keyword_fallback("频率 target", top_k=1)

    assert results == ["频率 target overlap entry"]


def test_keyword_fallback_high_index_entries():
    cases = [
        ("Pozar 解析 初始化 patch_W", "Pozar"),
        ("MIMO S21 隔离", "MIMO"),
        ("毫米波 77GHz Rogers 加工公差", "毫米波"),
        ("双频 U形缝隙 第二谐振", "双频"),
        ("阵列 单元间距 栅瓣", "阵列"),
    ]

    for query, expected in cases:
        results = _keyword_fallback(query, top_k=3)
        assert any(expected in result for result in results), (query, results)


def test_keyword_fallback_is_overlap_consistent():
    for query in [
        "Pozar 解析 初始化 patch_W",
        "MIMO S21 隔离",
        "毫米波 77GHz Rogers 加工公差",
        "双频 U形缝隙 第二谐振",
        "阵列 单元间距 栅瓣",
    ]:
        assert _keyword_fallback(query, top_k=3) == _expected_overlap_fallback(query, top_k=3)


def test_rag_eval_summary():
    """Print full Recall@3 and MRR summary (visible with -s)."""
    recalls = []
    rrs = []
    for query, keywords in ANNOTATED_CASES:
        results = _keyword_fallback(query, top_k=3)
        r = recall_at_k(results, keywords)
        rr = reciprocal_rank(results, keywords)
        recalls.append(r)
        rrs.append(rr)
    recall = sum(recalls) / len(recalls)
    mrr = sum(rrs) / len(rrs)
    print(f"\nRAG Keyword Fallback — Recall@3: {recall:.2f}, MRR: {mrr:.2f}")
    for (query, keywords), r, rr in zip(ANNOTATED_CASES, recalls, rrs):
        print(f"  [{r:.1f}/{rr:.2f}] {query!r} -> keywords={keywords}")
    # This test always passes — it only reports metrics
    assert True


def test_dynamic_entry_added_to_retrieval(monkeypatch, tmp_path):
    """Dynamic entry persists and is loaded by a fresh AntennaKnowledgeBase instance."""
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)

    add_dynamic_entry("测试动态条目：增大 inset_depth 改善匹配")

    kb = AntennaKnowledgeBase()
    assert any("inset_depth" in t for t in kb._entries), (
        "Dynamic entry not found in _entries after add_dynamic_entry"
    )


def test_confidence_gate_blocks_low_confidence(monkeypatch, tmp_path):
    """低置信度的 lesson 不应写入动态库（防 RAG 毒化）。"""
    from cst_agent_workbench import config
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
    monkeypatch.setattr(config, "RAG_LESSON_MIN_CONFIDENCE", 0.6)

    written_low = add_dynamic_entry("低置信度幻觉经验", confidence=0.3)
    written_high = add_dynamic_entry("高置信度有效经验：增大 patch_L 降低频率", confidence=0.9)
    written_none = add_dynamic_entry("人工导入条目无置信度字段")

    assert written_low is False, "低置信度应被拦截"
    assert written_high is True, "高置信度应入库"
    assert written_none is True, "未提供 confidence 应保持向后兼容（不过滤）"

    persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
    persisted_texts = [e["text"] for e in persisted]
    assert "低置信度幻觉经验" not in persisted_texts
    assert any("patch_L" in t for t in persisted_texts)
    # 高置信度条目应携带 confidence 字段，便于后续审计
    high_entry = next(e for e in persisted if "patch_L" in e["text"])
    assert high_entry.get("confidence") == 0.9


def test_jaccard_dedup_chinese_no_spaces(monkeypatch, tmp_path):
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)

    first = "增大贴片长度可以降低谐振频率并改善对频"
    near_duplicate = "增大贴片长度可以降低谐振频率并改善对频。"

    assert add_dynamic_entry(first, entry_type="history", confidence=0.9) is True
    assert add_dynamic_entry(near_duplicate, entry_type="history", confidence=0.9) is False

    persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
    assert [entry["text"] for entry in persisted] == [first]


def test_dynamic_entry_is_retrievable_in_same_process(monkeypatch, tmp_path):
    """Dynamic history writes should be visible to module-level retrieval without process restart."""
    from cst_agent_workbench import config
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")

    client = _failing_embedding_client()
    assert add_dynamic_entry(
        "unique_history_token feed_W 过小导致匹配恶化",
        entry_type="history",
        confidence=0.9,
        source="test",
    ) is True

    results = kb_module.retrieve_antenna_rules(
        "unique_history_token",
        client,
        filter_type="history",
        top_k=1,
    )

    assert results == ["unique_history_token feed_W 过小导致匹配恶化"]


def test_dynamic_entry_metadata_is_persisted(monkeypatch, tmp_path):
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)

    assert add_dynamic_entry(
        "metadata_token patch_L experience",
        entry_type="history",
        confidence=0.8,
        source="reflection",
        project_scope="project-a",
        task_scope="optimize_patch",
    ) is True

    persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
    entry = persisted[0]
    assert entry["source"] == "reflection"
    assert entry["confidence"] == 0.8
    assert entry["project_scope"] == "project-a"
    assert entry["task_scope"] == "optimize_patch"
    assert entry["timestamp"]


def test_dynamic_eviction_keeps_high_confidence(monkeypatch, tmp_path):
    from cst_agent_workbench import config
    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
    monkeypatch.setattr(config, "RAG_LESSON_MIN_CONFIDENCE", 0.6)

    high_text = "high_value_legacy_token confidence 0.95 lesson"
    assert add_dynamic_entry(high_text, entry_type="history", confidence=0.95)
    for index in range(55):
        assert add_dynamic_entry(
            f"low_noise_token_{index:02d} confidence 0.61 filler",
            entry_type="history",
            confidence=0.61,
        )

    persisted = json.loads(tmp_file.read_text(encoding="utf-8"))
    persisted_texts = [entry["text"] for entry in persisted]

    assert len(persisted) == 50
    assert high_text in persisted_texts
    assert "low_noise_token_00 confidence 0.61 filler" not in persisted_texts
    assert "low_noise_token_54 confidence 0.61 filler" in persisted_texts


def _failing_embedding_client():
    from unittest.mock import MagicMock
    client = MagicMock()
    client.embeddings.create.side_effect = RuntimeError("embedding down")
    return client


def test_retrieve_antenna_rules_falls_back_when_embedding_returns_empty(monkeypatch):
    from cst_agent_workbench import config
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
    monkeypatch.setattr(kb_module, "_kb", AntennaKnowledgeBase())
    client = _failing_embedding_client()

    results = kb_module.retrieve_antenna_rules("S11 深度不足匹配差", client, top_k=3)

    assert results
    assert any("inset_depth" in result or "feed_W" in result for result in results)


def test_retrieve_prefilters_history_before_ranking():
    import numpy as np
    from unittest.mock import MagicMock

    kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
    kb._entries = [
        "rule high similarity",
        "strategy high similarity",
        "history low similarity target_history_token",
    ]
    kb._entry_types = ["rule", "strategy", "history"]
    kb._confidences = [1.0, 1.0, 1.0]
    kb._embeddings = np.array(
        [
            [1.0, 0.0],
            [0.9, 0.1],
            [0.1, 0.9],
        ],
        dtype=np.float32,
    )
    kb._embedding_model = "text-embedding-3-small"
    kb._embed = lambda texts, client, model: np.array([[1.0, 0.0]], dtype=np.float32)
    client = MagicMock()
    resp = MagicMock()
    resp.data = [MagicMock(embedding=[1.0, 0.0])]
    client.embeddings.create.return_value = resp

    results = kb.retrieve(
        "query",
        client,
        top_k=1,
        filter_type="history",
        min_score=-1.0,
    )

    assert results == ["history low similarity target_history_token"]


def test_dynamic_entry_scope_filtering(monkeypatch, tmp_path):
    """Dynamic RAG entries from another design signature must not leak into recall."""
    from cst_agent_workbench import config

    tmp_file = tmp_path / "dynamic_entries.json"
    monkeypatch.setattr(kb_module, "_DYNAMIC_ENTRIES_PATH", tmp_file)
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")

    signature_a = kb_module.make_design_signature(4.4, 2.4, "microstrip")
    signature_b = kb_module.make_design_signature(2.2, 9.4, "probe")
    assert signature_a != signature_b

    assert add_dynamic_entry(
        "scope_a_token FR4 lesson: increase inset_depth",
        entry_type="history",
        confidence=0.9,
        source="test",
        design_signature=signature_a,
    )
    assert add_dynamic_entry(
        "scope_b_token Rogers lesson: decrease patch_L",
        entry_type="history",
        confidence=0.9,
        source="test",
        design_signature=signature_b,
    )
    assert add_dynamic_entry(
        "scope_common_token universal lesson",
        entry_type="history",
        confidence=0.9,
        source="test",
        design_signature="",
    )

    results = kb_module.retrieve_antenna_rules(
        "scope_a_token scope_b_token scope_common_token",
        _failing_embedding_client(),
        filter_type="history",
        top_k=5,
        min_score=-1.0,
        design_signature=signature_a,
    )

    assert any("scope_a_token" in result for result in results)
    assert any("scope_common_token" in result for result in results)
    assert not any("scope_b_token" in result for result in results)


def test_signature_empty_is_universal():
    import numpy as np
    from unittest.mock import MagicMock

    signature_a = kb_module.make_design_signature(4.4, 2.4, "microstrip")
    signature_b = kb_module.make_design_signature(2.2, 9.4, "probe")

    kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
    kb._entries = [
        "history scoped to A",
        "history universal signature",
    ]
    kb._entry_types = ["history", "history"]
    kb._confidences = [1.0, 1.0]
    kb._design_signatures = [signature_a, ""]
    kb._embeddings = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
    kb._embedding_model = "text-embedding-3-small"
    kb._embed = lambda texts, client, model: np.array([[1.0, 0.0]], dtype=np.float32)

    results = kb.retrieve(
        "query",
        MagicMock(),
        top_k=5,
        filter_type="history",
        min_score=-1.0,
        design_signature=signature_b,
    )

    assert results == ["history universal signature"]


def test_signature_parse_failure_is_warning_not_raise(caplog):
    import logging
    import numpy as np
    from unittest.mock import MagicMock

    signature_a = kb_module.make_design_signature(4.4, 2.4, "microstrip")

    kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
    kb._entries = [
        "static rule survives malformed signature",
        "history scoped to A",
        "history universal signature",
    ]
    kb._entry_types = ["rule", "history", "history"]
    kb._confidences = [1.0, 1.0, 1.0]
    kb._design_signatures = ["", signature_a, ""]
    kb._embeddings = np.array(
        [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]],
        dtype=np.float32,
    )
    kb._embedding_model = "text-embedding-3-small"
    kb._embed = lambda texts, client, model: np.array([[1.0, 0.0]], dtype=np.float32)

    caplog.set_level(logging.WARNING, logger="cst_agent_workbench.rag.knowledge_base")
    results = kb.retrieve(
        "query",
        MagicMock(),
        top_k=5,
        min_score=-1.0,
        design_signature="not/a/valid/signature",
    )

    assert "static rule survives malformed signature" in results
    assert "history universal signature" in results
    assert "history scoped to A" not in results
    assert any("invalid design_signature" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Extended annotated cases (10 queries, covers new knowledge entries)
# ---------------------------------------------------------------------------

ANNOTATED_CASES_EXTENDED = [
    ("谐振频率偏高如何调整", ["patch_L", "增大"]),
    ("S11 深度不足匹配差", ["inset_depth", "feed_W"]),
    ("优化步长建议", ["2%", "5%", "0.1"]),
    ("带宽如何提升", ["substrate_h", "带宽"]),
    ("探针馈电阻抗控制", ["探针", "馈点"]),
    ("圆极化怎么实现", ["截角", "圆极化"]),
    ("表面波如何抑制", ["表面波"]),
    ("inset_depth 初始估算公式", ["inset_depth", "patch_L"]),
    ("patch_L 初始值", ["patch_L", "εr_eff"]),
    ("叠层贴片带宽", ["叠层", "带宽"]),
]


class TestExtendedAnnotatedCases:
    def test_extended_recall_at_3(self):
        hits = sum(
            1 for q, kws in ANNOTATED_CASES_EXTENDED
            if recall_at_k(_keyword_fallback(q, top_k=3), kws) == 1.0
        )
        recall = hits / len(ANNOTATED_CASES_EXTENDED)
        print(f"\nExtended Keyword Recall@3: {recall:.2f} ({hits}/{len(ANNOTATED_CASES_EXTENDED)})")
        assert recall >= 0.60

    def test_extended_mrr(self):
        mrr = sum(
            reciprocal_rank(_keyword_fallback(q, top_k=3), kws)
            for q, kws in ANNOTATED_CASES_EXTENDED
        ) / len(ANNOTATED_CASES_EXTENDED)
        print(f"\nExtended Keyword MRR: {mrr:.2f}")
        assert mrr >= 0.40


class TestEmbeddingCacheIdentity:
    def _cache_test_kb(self, dim_ref):
        kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
        kb._entries = ["cache identity entry"]
        kb._entry_types = ["rule"]
        kb._confidences = [1.0]
        kb._embeddings = None
        kb._embedding_model = ""

        def fake_embed(texts, client, model):
            import numpy as np
            dim = dim_ref["dim"]
            vecs = np.zeros((len(texts), dim), dtype=np.float32)
            vecs[:, 0] = 1.0
            return vecs

        kb._embed = fake_embed
        return kb

    @pytest.fixture(autouse=True)
    def _isolated_embedding_cache(self, monkeypatch, tmp_path):
        from cst_agent_workbench import config
        self._cache_dir = tmp_path / "cst_agent_rag"
        monkeypatch.setattr(config, "RAG_CACHE_DIR", str(self._cache_dir))
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        monkeypatch.setattr(config, "EMBEDDING_API_KEY", "")
        monkeypatch.setattr(config, "EMBEDDING_MODEL", "api-embedding-model")
        monkeypatch.setattr(config, "EMBEDDING_LOCAL_MODEL", "local-embedding-model")

    def test_cache_key_changes_with_provider(self, monkeypatch):
        from unittest.mock import MagicMock
        from cst_agent_workbench import config

        dim_ref = {"dim": 2}
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
        self._cache_test_kb(dim_ref).ensure_indexed(MagicMock(), model="shared-model")
        cache_files_after_openai = set(self._cache_dir.glob("*.npy"))

        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "local")
        self._cache_test_kb(dim_ref).ensure_indexed(MagicMock(), model="shared-model")
        cache_files_after_local = set(self._cache_dir.glob("*.npy"))

        assert len(cache_files_after_openai) == 1
        assert len(cache_files_after_local) == 2
        assert cache_files_after_openai != cache_files_after_local

    def test_stale_dim_cache_is_rebuilt(self, monkeypatch):
        from unittest.mock import MagicMock
        from cst_agent_workbench import config

        dim_ref = {"dim": 2}
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
        self._cache_test_kb(dim_ref).ensure_indexed(MagicMock(), model="shared-model")

        dim_ref["dim"] = 3
        kb = self._cache_test_kb(dim_ref)
        results = kb.retrieve("query", MagicMock(), model="shared-model", top_k=1, min_score=-1.0)

        assert results == ["cache identity entry"]
        assert kb._embeddings.shape == (1, 3)
        cached_shapes = [np.load(str(path)).shape for path in self._cache_dir.glob("*.npy")]
        assert (1, 3) in cached_shapes

    def test_provider_switch_reindexes(self, monkeypatch):
        from unittest.mock import MagicMock
        from cst_agent_workbench import config

        dim_ref = {"dim": 2}
        kb = self._cache_test_kb(dim_ref)
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")
        kb.ensure_indexed(MagicMock(), model="shared-model")
        assert kb._embeddings.shape == (1, 2)

        dim_ref["dim"] = 3
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "local")
        kb.ensure_indexed(MagicMock(), model="shared-model")

        assert kb._embeddings.shape == (1, 3)


# ---------------------------------------------------------------------------
# Embedding retrieval tests (mock client, validates retrieve() path)
# ---------------------------------------------------------------------------

class TestEmbeddingRetrievalWithMockClient:
    """Test AntennaKnowledgeBase.retrieve using deterministic mock embeddings.
    Uses isolated KB instances (no dynamic entries, no disk cache).
    """

    @pytest.fixture(autouse=True)
    def _force_openai_embedding(self, monkeypatch):
        from cst_agent_workbench import config
        monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")

    def _fresh_kb(self):
        import numpy as np
        from cst_agent_workbench.rag.knowledge_base import KNOWLEDGE_ENTRIES as KE
        kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
        kb._entries = [e.text for e in KE]
        kb._entry_types = [e.entry_type for e in KE]
        rng = np.random.RandomState(99)
        n = len(kb._entries)
        vecs = rng.randn(n + 5, 32).astype(np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
        kb._embeddings = vecs[:n]
        kb._embedding_model = "text-embedding-3-small"
        return kb

    def _make_client(self, kb, target_idx):
        """Return a client whose query embedding == kb._embeddings[target_idx],
        guaranteeing cosine similarity = 1.0 for that entry."""
        from unittest.mock import MagicMock
        target_vec = kb._embeddings[target_idx].tolist()
        def fake_create(input, model):
            resp = MagicMock()
            resp.data = [MagicMock(embedding=target_vec)]
            return resp
        client = MagicMock()
        client.embeddings.create.side_effect = fake_create
        return client

    def test_retrieve_returns_target_entry_first(self):
        kb = self._fresh_kb()
        target_idx = 3
        client = self._make_client(kb, target_idx)
        results = kb.retrieve("test query", client, top_k=1, min_score=-1.0)
        assert len(results) == 1
        assert results[0] == kb._entries[target_idx]

    def test_retrieve_filter_type_restricts_to_strategy(self):
        from cst_agent_workbench.rag.knowledge_base import KNOWLEDGE_ENTRIES
        kb = self._fresh_kb()
        strategy_idx = next(i for i, e in enumerate(KNOWLEDGE_ENTRIES) if e.entry_type == "strategy")
        client = self._make_client(kb, strategy_idx)
        results = kb.retrieve("strategy query", client, top_k=3, filter_type="strategy", min_score=-1.0)
        for r in results:
            idx = kb._entries.index(r)
            assert kb._entry_types[idx] == "strategy"

    def test_retrieve_respects_min_score(self):
        kb = self._fresh_kb()
        target_idx = 0
        client = self._make_client(kb, target_idx)
        results = kb.retrieve("exact match", client, top_k=5, min_score=0.99)
        assert len(results) >= 1
        assert results[0] == kb._entries[target_idx]

    def test_retrieve_logs_on_failure(self, caplog):
        import logging
        from unittest.mock import MagicMock

        kb = self._fresh_kb()

        def fail_index(client, model):
            raise RuntimeError("embedding down")

        kb.ensure_indexed = fail_index

        with caplog.at_level(logging.WARNING, logger="cst_agent_workbench.rag.knowledge_base"):
            results = kb.retrieve("query", MagicMock())

        assert results == []
        assert any(
            "retrieve failed" in record.message and "embedding down" in record.message
            for record in caplog.records
        )

    def test_min_score_filters_on_raw_cosine(self):
        import numpy as np
        from unittest.mock import MagicMock

        kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
        kb._entries = [
            "raw_pass_low_confidence",
            "raw_fail_high_confidence",
        ]
        kb._entry_types = ["history", "history"]
        kb._confidences = [0.6, 1.0]
        kb._design_signatures = ["", ""]
        vec_a = np.array([0.70, np.sqrt(1.0 - 0.70**2)], dtype=np.float32)
        vec_b = np.array([0.64, np.sqrt(1.0 - 0.64**2)], dtype=np.float32)
        kb._embeddings = np.stack([vec_a, vec_b])
        kb._embedding_model = "text-embedding-3-small"
        kb._embed = lambda texts, client, model: np.array([[1.0, 0.0]], dtype=np.float32)

        results = kb.retrieve(
            "query",
            MagicMock(),
            top_k=2,
            min_score=0.65,
            with_scores=True,
        )

        assert [text for text, _ in results] == ["raw_pass_low_confidence"]
        assert results[0][1] == pytest.approx(0.56, abs=1e-6)

    def test_query_rewrite_parses_json_array(self):
        """LLM 返回纯 JSON 数组时，_rewrite_query 应解析出字符串列表。"""
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag.knowledge_base import _rewrite_query, _query_rewrite_cache
        _query_rewrite_cache.clear()
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='["如何调整 patch_L", "贴片长度优化"]'))]
        client.chat.completions.create.return_value = resp
        result = _rewrite_query("调谐振频率", client, "gpt-x", n=2)
        assert result == ["如何调整 patch_L", "贴片长度优化"]

    def test_query_rewrite_handles_code_fence(self):
        """LLM 返回 ```json [...] ``` 包裹的内容也能解析。"""
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag.knowledge_base import _rewrite_query, _query_rewrite_cache
        _query_rewrite_cache.clear()
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='```json\n["a", "b"]\n```'))]
        client.chat.completions.create.return_value = resp
        result = _rewrite_query("query", client, "gpt-x", n=2)
        assert result == ["a", "b"]

    def test_query_rewrite_failure_returns_empty_list(self):
        """LLM 异常时返回 [] 而不是抛出（让上游降级到原 query）。"""
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag.knowledge_base import _rewrite_query, _query_rewrite_cache
        _query_rewrite_cache.clear()
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("LLM down")
        assert _rewrite_query("query", client, "gpt-x", n=2) == []

    def test_query_rewrite_cached(self):
        """同一 query 第二次调用应命中缓存，不重复调 LLM。"""
        from unittest.mock import MagicMock
        from cst_agent_workbench.rag.knowledge_base import _rewrite_query, _query_rewrite_cache
        _query_rewrite_cache.clear()
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='["x"]'))]
        client.chat.completions.create.return_value = resp
        _rewrite_query("cached-query", client, "gpt-x", n=1)
        _rewrite_query("cached-query", client, "gpt-x", n=1)
        assert client.chat.completions.create.call_count == 1

    def test_query_rewrite_cache_is_bounded(self):
        """rewrite 缓存按 FIFO 淘汰，长会话不会无界增长。"""
        from unittest.mock import MagicMock
        import cst_agent_workbench.rag.knowledge_base as kb_module
        kb_module._query_rewrite_cache.clear()
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content='["x"]'))]
        client.chat.completions.create.return_value = resp
        limit = kb_module._QUERY_REWRITE_CACHE_LIMIT
        for index in range(limit + 25):
            kb_module._rewrite_query(f"query-{index}", client, "gpt-x", n=1)
        assert len(kb_module._query_rewrite_cache) == limit
        # 最老的条目被淘汰，最新的仍在
        assert ("query-0", "gpt-x", 1) not in kb_module._query_rewrite_cache
        assert (f"query-{limit + 24}", "gpt-x", 1) in kb_module._query_rewrite_cache
        kb_module._query_rewrite_cache.clear()

    def test_confidence_weighting_promotes_high_confidence(self):
        """两个相似度相近的条目，高 confidence 应排在前面。"""
        import numpy as np
        from unittest.mock import MagicMock
        kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
        # 两条候选：A 相似度 1.0 但置信度 0.6；B 相似度 0.95 但置信度 1.0
        # 未加权：A 胜；加权：A=1.0*0.8=0.8，B=0.95*1.0=0.95 → B 胜
        kb._entries = ["A: 低置信经验", "B: 高置信经验"]
        kb._entry_types = ["history", "history"]
        kb._confidences = [0.6, 1.0]
        vec_a = np.array([1.0, 0.0], dtype=np.float32)
        vec_b = np.array([0.95, np.sqrt(1.0 - 0.95**2)], dtype=np.float32)
        kb._embeddings = np.stack([vec_a, vec_b])
        kb._embedding_model = "text-embedding-3-small"
        # query 完全对齐 A，所以原始相似度 A=1.0、B≈0.95
        def fake_create(input, model):
            resp = MagicMock()
            resp.data = [MagicMock(embedding=vec_a.tolist())]
            return resp
        client = MagicMock()
        client.embeddings.create.side_effect = fake_create
        results = kb.retrieve("query", client, top_k=2, min_score=-1.0, with_scores=True)
        assert results[0][0].startswith("B:"), f"高置信度条目应排第一，实际: {results}"
        # 同时验证 with_scores=True 返回 (text, score) 元组
        assert isinstance(results[0], tuple) and len(results[0]) == 2
        assert isinstance(results[0][1], float)

