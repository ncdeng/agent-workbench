from __future__ import annotations

from cst_agent_workbench.rag import reranker as reranker_module


class _FakeModel:
    def __init__(self, scores=None, error=None):
        self.scores = scores
        self.error = error

    def predict(self, pairs, **kwargs):
        if self.error:
            raise self.error
        assert pairs == [("english query", "dense A"), ("english query", "dense B")]
        return self.scores


def _hits():
    return [
        {"text": "dense A", "source_path": "a.htm", "score": 0.9},
        {"text": "dense B", "source_path": "b.htm", "score": 0.7},
    ]


def test_cross_encoder_can_reverse_dense_order(monkeypatch):
    monkeypatch.setattr(reranker_module, "_load_model", lambda name: _FakeModel([0.1, 0.95]))

    results = reranker_module.rerank_document_hits(
        "english query",
        _hits(),
        enabled=True,
        model_name="fake-reranker",
    )

    assert [hit["text"] for hit in results] == ["dense B", "dense A"]
    assert results[0]["dense_score"] == 0.7
    assert results[0]["rerank_score"] == 0.95
    assert results[0]["rank_before"] == 2
    assert results[0]["rank_after"] == 1
    assert results[0]["reranker_model"] == "fake-reranker"
    assert results[0]["rerank_applied"] is True


def test_predict_failure_returns_annotated_dense_order(monkeypatch):
    monkeypatch.setattr(
        reranker_module,
        "_load_model",
        lambda name: _FakeModel(error=RuntimeError("offline")),
    )

    results = reranker_module.rerank_document_hits(
        "english query",
        _hits(),
        enabled=True,
        model_name="fake-reranker",
    )

    assert [hit["text"] for hit in results] == ["dense A", "dense B"]
    assert [hit["rank_before"] for hit in results] == [1, 2]
    assert [hit["rank_after"] for hit in results] == [1, 2]
    assert all(hit["rerank_score"] is None for hit in results)
    assert all(hit["rerank_applied"] is False for hit in results)
    assert all("RuntimeError: offline" in hit["rerank_error"] for hit in results)
