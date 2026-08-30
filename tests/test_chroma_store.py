"""Tests for cst_agent_workbench.rag.chroma_store."""
from __future__ import annotations

import pytest


def _make_ephemeral_collection(tmp_path):
    """创建临时目录的 ChromaDB collection 供测试使用。"""
    import chromadb
    client = chromadb.PersistentClient(path=str(tmp_path / "test_chroma"))
    col = client.get_or_create_collection(
        name="test_cst_pdf",
        metadata={"hnsw:space": "cosine"},
    )
    return client, col


@pytest.fixture()
def _patch_collection(tmp_path, monkeypatch):
    """Monkeypatch chroma_store 使其使用临时 collection。"""
    import cst_agent_workbench.rag.chroma_store as cs

    client, col = _make_ephemeral_collection(tmp_path)
    monkeypatch.setattr(cs.config, "CHROMA_PERSIST_DIR", str(tmp_path / "test_chroma"))
    monkeypatch.setattr(cs.config, "CHROMA_COLLECTION_NAME", "test_cst_pdf")
    monkeypatch.setattr(cs, "_client", client)
    monkeypatch.setattr(cs, "_collection", col)
    monkeypatch.setattr(cs, "_init_attempted", True)
    return col


class TestQueryPdfKnowledge:
    def test_chinese_cst_terms_add_an_english_query_variant(self):
        import cst_agent_workbench.rag.chroma_store as cs

        assert cs._build_query_variants("频域求解器的自适应网格加密如何配置？") == [
            "frequency domain solver adaptive mesh refinement",
        ]

    def test_bge_query_instruction_is_not_added_to_documents(self, monkeypatch):
        import cst_agent_workbench.rag.chroma_store as cs
        from chromadb.utils import embedding_functions

        calls = []

        class FakeSentenceTransformerEmbeddingFunction:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            def __call__(self, input):
                calls.append(list(input))
                return [[0.0, 1.0] for _ in input]

        monkeypatch.setattr(
            embedding_functions,
            "SentenceTransformerEmbeddingFunction",
            FakeSentenceTransformerEmbeddingFunction,
        )
        monkeypatch.setattr(cs.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(cs.config, "EMBEDDING_LOCAL_MODEL", "BAAI/bge-base-en-v1.5")
        monkeypatch.setattr(
            cs.config,
            "EMBEDDING_QUERY_INSTRUCTION",
            "Represent this sentence for searching relevant passages: ",
        )

        embedding_function = cs._make_embedding_function()
        embedding_function(["Waveguide port passage"])
        embedding_function.embed_query(["How is a waveguide port defined?"])

        assert calls == [
            ["Waveguide port passage"],
            [
                "Represent this sentence for searching relevant passages: "
                "How is a waveguide port defined?"
            ],
        ]
        assert embedding_function.kwargs["normalize_embeddings"] is True

    def test_returns_empty_when_chromadb_unavailable(self, monkeypatch):
        import cst_agent_workbench.rag.chroma_store as cs
        monkeypatch.setattr(cs, "_collection", None)
        monkeypatch.setattr(cs, "_init_attempted", True)
        assert cs.query_pdf_knowledge("test query") == []

    def test_returns_empty_when_collection_empty(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_pdf_knowledge
        assert query_pdf_knowledge("test query") == []

    def test_returns_results_after_add(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_pdf_knowledge

        col = _patch_collection
        col.add(
            ids=["doc1", "doc2", "doc3"],
            documents=[
                "矩形微带贴片天线设计方法",
                "Python 列表排序算法",
                "相控阵天线波束扫描原理",
            ],
            metadatas=[
                {"topic": "antenna", "source": "a.pdf", "source_hash": "aaa", "page": 1, "chunk_idx": 0},
                {"topic": "general", "source": "b.pdf", "source_hash": "bbb", "page": 1, "chunk_idx": 0},
                {"topic": "phased_array", "source": "c.pdf", "source_hash": "ccc", "page": 1, "chunk_idx": 0},
            ],
        )
        results = query_pdf_knowledge("天线设计", top_k=2)
        assert len(results) == 2
        assert isinstance(results[0], str)

    def test_filter_by_topic(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_pdf_knowledge

        col = _patch_collection
        col.add(
            ids=["d1", "d2"],
            documents=["EMC 电磁兼容测试", "天线增益测量"],
            metadatas=[
                {"topic": "emc", "source": "e.pdf", "source_hash": "eee", "page": 1, "chunk_idx": 0},
                {"topic": "antenna", "source": "f.pdf", "source_hash": "fff", "page": 1, "chunk_idx": 0},
            ],
        )
        results = query_pdf_knowledge("测试", top_k=5, filter_topic="emc")
        assert len(results) == 1
        assert "EMC" in results[0]

    def test_structured_hits_preserve_provenance_and_score(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_document_knowledge

        _patch_collection.add(
            ids=["official-1"],
            documents=["Waveguide ports are defined on the simulation boundary."],
            metadatas=[{
                "topic": "general",
                "source": "waveguide_port.htm",
                "source_path": "mws/ports/waveguide_port.htm",
                "source_type": "html",
                "source_hash": "hash-1",
                "page": 1,
                "chunk_idx": 2,
            }],
        )

        hits = query_document_knowledge("waveguide port", top_k=1)

        assert len(hits) == 1
        assert hits[0]["source_path"] == "mws/ports/waveguide_port.htm"
        assert hits[0]["source_type"] == "html"
        assert hits[0]["chunk_idx"] == 2
        assert isinstance(hits[0]["score"], float)

    def test_structured_hits_use_distinct_source_slots(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_document_knowledge

        _patch_collection.add(
            ids=["same-1", "same-2", "other-1"],
            documents=["waveguide port alpha", "waveguide port beta", "waveguide port gamma"],
            metadatas=[
                {"source": "same.htm", "source_path": "same.htm", "chunk_idx": 0, "topic": "general"},
                {"source": "same.htm", "source_path": "same.htm", "chunk_idx": 1, "topic": "general"},
                {"source": "other.htm", "source_path": "other.htm", "chunk_idx": 0, "topic": "general"},
            ],
        )

        hits = query_document_knowledge("waveguide port", top_k=3)

        sources = [hit["source_path"] for hit in hits]
        assert len(sources) == len(set(sources))

    def test_structured_candidates_can_keep_multiple_chunks_per_source(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import query_document_knowledge

        _patch_collection.add(
            ids=["same-1", "same-2"],
            documents=["waveguide port definition", "waveguide port boundary setup"],
            metadatas=[
                {"source": "same.htm", "source_path": "same.htm", "chunk_idx": 0, "topic": "general"},
                {"source": "same.htm", "source_path": "same.htm", "chunk_idx": 1, "topic": "general"},
            ],
        )

        hits = query_document_knowledge(
            "waveguide port",
            top_k=2,
            deduplicate_sources=False,
        )

        assert len(hits) == 2
        assert {hit["chunk_idx"] for hit in hits} == {0, 1}
        assert all(hit["dense_score"] == hit["score"] for hit in hits)


class TestCollectionStats:
    def test_stats_unavailable(self, monkeypatch):
        import cst_agent_workbench.rag.chroma_store as cs
        monkeypatch.setattr(cs, "_collection", None)
        monkeypatch.setattr(cs, "_init_attempted", True)
        stats = cs.get_collection_stats()
        assert stats["available"] is False
        assert stats["count"] == 0

    def test_stats_with_data(self, _patch_collection):
        from cst_agent_workbench.rag.chroma_store import get_collection_stats

        col = _patch_collection
        col.add(ids=["x1"], documents=["test doc"], metadatas=[{"topic": "general", "source": "t.pdf", "source_hash": "xxx", "page": 1, "chunk_idx": 0}])
        stats = get_collection_stats()
        assert stats["available"] is True
        assert stats["count"] == 1

    def test_stats_refreshes_error_set_during_initialization(self, monkeypatch, tmp_path):
        import cst_agent_workbench.rag.chroma_store as cs

        monkeypatch.setattr(cs, "_last_error", "")
        monkeypatch.setattr(cs, "get_build_marker_path", lambda: tmp_path / "missing-marker.json")

        def fail_init():
            cs._set_error("Error loading hnsw index")
            return None

        monkeypatch.setattr(cs, "_get_collection", fail_init)

        stats = cs.get_collection_stats()

        assert stats["status"] == "failed"
        assert stats["error"] == "Error loading hnsw index"

    def test_stats_rejects_embedding_identity_mismatch(self, monkeypatch, tmp_path):
        import cst_agent_workbench.rag.chroma_store as cs

        class FakeCollection:
            metadata = {
                "cst_rag_schema": cs.COLLECTION_SCHEMA_VERSION,
                "embedding_provider": "local",
                "embedding_model": "different-model",
            }

            @staticmethod
            def count():
                return 10

        monkeypatch.setattr(cs, "_collection", FakeCollection())
        monkeypatch.setattr(cs, "get_build_marker_path", lambda: tmp_path / "missing-marker.json")
        monkeypatch.setattr(cs.config, "EMBEDDING_PROVIDER", "local")
        monkeypatch.setattr(cs.config, "EMBEDDING_LOCAL_MODEL", "expected-model")

        stats = cs.get_collection_stats()

        assert stats["available"] is True
        assert stats["healthy"] is False
        assert stats["status"] == "mismatch"
        assert "different-model" in stats["error"]

    def test_stats_reports_building_without_opening_collection(self, monkeypatch, tmp_path):
        import cst_agent_workbench.rag.chroma_store as cs

        monkeypatch.setattr(cs.config, "CHROMA_PERSIST_DIR", str(tmp_path / "chromadb"))
        monkeypatch.setattr(cs.config, "CHROMA_COLLECTION_NAME", "building_test")
        marker = cs.get_build_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text('{"started_at": "2026-08-09T00:00:00Z"}', encoding="utf-8")
        monkeypatch.setattr(cs, "_get_collection", lambda: (_ for _ in ()).throw(AssertionError("must not open")))

        stats = cs.get_collection_stats()

        assert stats["status"] == "building"
        assert stats["healthy"] is False
        assert stats["build"]["started_at"] == "2026-08-09T00:00:00Z"


class TestResetCollection:
    def test_reset_clears_state(self, _patch_collection, monkeypatch):
        import cst_agent_workbench.rag.chroma_store as cs

        col = _patch_collection
        col.add(ids=["r1"], documents=["data"], metadatas=[{"topic": "general", "source": "r.pdf", "source_hash": "rrr", "page": 1, "chunk_idx": 0}])
        assert col.count() == 1

        cs.reset_collection()
        assert cs._collection is None
        assert cs._init_attempted is False

    def test_reset_uses_persistent_client_from_cold_state(self, monkeypatch, tmp_path):
        import chromadb
        import cst_agent_workbench.rag.chroma_store as cs

        deleted = []

        class FakeClient:
            def delete_collection(self, name):
                deleted.append(name)

        monkeypatch.setattr(chromadb, "PersistentClient", lambda path: FakeClient())
        monkeypatch.setattr(cs.config, "CHROMA_PERSIST_DIR", str(tmp_path / "cold-store"))
        monkeypatch.setattr(cs, "_client", None)
        monkeypatch.setattr(cs, "_collection", None)
        monkeypatch.setattr(cs, "_init_attempted", False)

        assert cs.reset_collection() is True
        assert deleted == [cs.config.CHROMA_COLLECTION_NAME]
        assert cs._init_attempted is False
