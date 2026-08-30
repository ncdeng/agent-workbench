"""Tests for antenna knowledge base retrieval.

使用 mock client 避免真实 API 调用。
"""
import numpy as np
import pytest
from unittest.mock import MagicMock

from cst_agent_workbench.rag.knowledge_base import AntennaKnowledgeBase, KNOWLEDGE_ENTRIES


@pytest.fixture(autouse=True)
def _force_openai_embedding(monkeypatch):
    """Tests use mock OpenAI client, so force openai embedding provider."""
    from cst_agent_workbench import config
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "openai")


def _make_mock_client(n_entries: int, query_vec=None):
    """构造 mock openai client，返回确定性 embedding。"""
    client = MagicMock()
    rng = np.random.RandomState(42)
    entry_vecs = rng.randn(n_entries, 64).astype(np.float32)
    # 归一化
    entry_vecs /= np.linalg.norm(entry_vecs, axis=1, keepdims=True)

    call_count = 0

    def fake_create(input, model):
        nonlocal call_count
        n = len(input)
        if call_count == 0:
            # 第一次调用：返回知识库向量
            vecs = entry_vecs[:n]
        else:
            # 后续调用：返回 query 向量（使其与第0条最相似）
            q = entry_vecs[0].copy()
            q += rng.randn(64).astype(np.float32) * 0.01
            q /= np.linalg.norm(q)
            vecs = q[np.newaxis, :]
        call_count += 1
        resp = MagicMock()
        resp.data = [MagicMock(embedding=v.tolist()) for v in vecs]
        return resp

    client.embeddings.create.side_effect = fake_create
    return client


def test_knowledge_entries_not_empty():
    assert len(KNOWLEDGE_ENTRIES) >= 15


def _fresh_kb_no_dynamic(monkeypatch=None):
    """创建只含静态条目、已预计算 embedding 的干净 KB 实例，完全跳过磁盘缓存。"""
    kb = AntennaKnowledgeBase.__new__(AntennaKnowledgeBase)
    kb._entries = [e.text for e in KNOWLEDGE_ENTRIES]
    kb._entry_types = [e.entry_type for e in KNOWLEDGE_ENTRIES]
    # 预计算确定性 embedding，ensure_indexed 会直接跳过
    rng = np.random.RandomState(42)
    n = len(kb._entries)
    vecs = rng.randn(n, 64).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    kb._embeddings = vecs
    kb._embedding_model = "text-embedding-3-small"
    return kb


def test_retrieve_returns_top_k():
    kb = _fresh_kb_no_dynamic()
    client = _make_mock_client(len(kb._entries))
    results = kb.retrieve("调节谐振频率", client, top_k=3, min_score=-1.0)
    assert len(results) == 3
    for r in results:
        assert r in kb._entries


def test_retrieve_returns_most_similar_first():
    kb = _fresh_kb_no_dynamic()
    client = _make_mock_client(len(kb._entries))
    results = kb.retrieve("调节谐振频率", client, top_k=1, min_score=-1.0)
    assert results[0] == KNOWLEDGE_ENTRIES[0].text


def test_retrieve_caches_embeddings():
    kb = _fresh_kb_no_dynamic()
    client = _make_mock_client(len(kb._entries))
    kb.retrieve("query1", client, top_k=2, min_score=-1.0)
    call_count_after_first = client.embeddings.create.call_count
    kb.retrieve("query2", client, top_k=2, min_score=-1.0)
    assert client.embeddings.create.call_count == call_count_after_first + 1


def test_retrieve_returns_empty_on_exception():
    kb = _fresh_kb_no_dynamic()
    client = MagicMock()
    client.embeddings.create.side_effect = RuntimeError("API error")
    results = kb.retrieve("query", client, top_k=3)
    assert results == []


def test_retrieve_top_k_bounded_by_entries():
    kb = _fresh_kb_no_dynamic()
    client = _make_mock_client(len(kb._entries))
    results = kb.retrieve("query", client, top_k=100, min_score=-1.0)
    assert 0 < len(results) <= len(kb._entries)


# ---------------------------------------------------------------------------
# ChromaDB merge behavior tests
# ---------------------------------------------------------------------------


def test_retrieve_merges_pdf_results(monkeypatch):
    """Chroma results appear after rules with an accurate official-doc prefix."""
    from cst_agent_workbench.rag import knowledge_base as kb_mod

    # Mock _kb.retrieve to return a known rule
    monkeypatch.setattr(kb_mod, "_kb", _fresh_kb_no_dynamic())
    fake_client = _make_mock_client(len(KNOWLEDGE_ENTRIES))

    # Mock ChromaDB query to return a PDF result
    monkeypatch.setattr(
        "cst_agent_workbench.rag.chroma_store.query_pdf_knowledge",
        lambda query, top_k=3, filter_topic=None: ["相控阵天线馈电网络设计方法"],
    )

    results = kb_mod.retrieve_antenna_rules("调节谐振频率", fake_client, top_k=3)
    # Should have rules + PDF results
    assert len(results) >= 2
    document_results = [r for r in results if r.startswith("[CST官方文档]")]
    assert len(document_results) >= 1
    assert "[CST官方文档] 相控阵天线馈电网络设计方法" in results


def test_retrieve_works_without_chromadb(monkeypatch):
    """ChromaDB import failure → still returns hardcoded rules only."""
    from cst_agent_workbench.rag import knowledge_base as kb_mod

    monkeypatch.setattr(kb_mod, "_kb", _fresh_kb_no_dynamic())
    fake_client = _make_mock_client(len(KNOWLEDGE_ENTRIES))

    # Make ChromaDB import fail
    monkeypatch.setattr(
        "cst_agent_workbench.rag.chroma_store.query_pdf_knowledge",
        MagicMock(side_effect=ImportError("no chromadb")),
    )

    results = kb_mod.retrieve_antenna_rules("调节谐振频率", fake_client, top_k=3)
    # Should still return hardcoded rules
    assert len(results) >= 1
    assert all(not r.startswith("[CST官方文档]") for r in results)


def test_document_store_has_independent_quota_when_curated_is_empty(monkeypatch):
    from types import SimpleNamespace
    from cst_agent_workbench.rag import knowledge_base as kb_mod

    monkeypatch.setattr(kb_mod, "_kb", SimpleNamespace(retrieve=lambda *args, **kwargs: []))
    query = MagicMock(return_value=["official document A", "official document B"])
    monkeypatch.setattr("cst_agent_workbench.rag.chroma_store.query_pdf_knowledge", query)

    results = kb_mod.retrieve_antenna_rules("waveguide port boundary", MagicMock(), top_k=2)

    assert query.call_args.kwargs["top_k"] == 2
    assert "[CST官方文档] official document A" in results
    assert "[CST官方文档] official document B" in results
