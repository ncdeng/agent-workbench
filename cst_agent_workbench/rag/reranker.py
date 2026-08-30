"""Lazy, failure-tolerant cross-encoder reranking for official documents."""
from __future__ import annotations

import logging
import inspect
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cst_agent_workbench import config

logger = logging.getLogger(__name__)

_models: dict[str, Any] = {}
_model_lock = threading.Lock()


def _load_model(model_name: str):
    cached = _models.get(model_name)
    if cached is not None:
        return cached
    with _model_lock:
        cached = _models.get(model_name)
        if cached is not None:
            return cached
        from sentence_transformers import CrossEncoder

        cache_dir = Path(config.RAG_RERANK_CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)
        parameters = inspect.signature(CrossEncoder).parameters
        if "cache_folder" in parameters:
            cache_kwargs = {"cache_folder": str(cache_dir)}
        else:
            cache_kwargs = {
                "automodel_args": {"cache_dir": str(cache_dir)},
                "tokenizer_args": {"cache_dir": str(cache_dir)},
            }
        model = CrossEncoder(model_name, **cache_kwargs)
        _models[model_name] = model
        return model


def _dense_ranked(hits: Sequence[dict[str, Any]], model_name: str) -> list[dict[str, Any]]:
    def dense_score(item: dict[str, Any]) -> float:
        value = item.get("dense_score")
        if value is None:
            value = item.get("score")
        return float(value) if value is not None else -1.0

    ranked = sorted(
        (dict(hit) for hit in hits),
        key=dense_score,
        reverse=True,
    )
    for rank, hit in enumerate(ranked, start=1):
        if hit.get("dense_score") is None:
            hit["dense_score"] = hit.get("score")
        hit["rank_before"] = rank
        hit["rank_after"] = rank
        hit["rerank_score"] = None
        hit["reranker_model"] = model_name
        hit["rerank_applied"] = False
    return ranked


def rerank_document_hits(
    retrieval_queries: str | Sequence[str],
    hits: Sequence[dict[str, Any]],
    *,
    enabled: bool | None = None,
    model_name: str | None = None,
) -> list[dict[str, Any]]:
    """Rerank dense candidates, preserving cosine ``score`` semantics.

    Every candidate is scored against every canonical English retrieval query;
    its maximum cross-encoder score is used. Import, model loading, and predict
    failures all degrade to the original dense order so Agent execution remains
    available when the optional model is missing or offline.
    """
    selected_model = str(model_name or config.RAG_RERANK_MODEL).strip()
    dense_ranked = _dense_ranked(hits, selected_model)
    use_reranker = config.RAG_RERANK_ENABLED if enabled is None else bool(enabled)
    queries = (
        [str(query).strip() for query in retrieval_queries]
        if not isinstance(retrieval_queries, str)
        else [retrieval_queries.strip()]
    )
    queries = list(dict.fromkeys(query for query in queries if query))
    if not use_reranker or not selected_model or not queries or not dense_ranked:
        return dense_ranked

    try:
        model = _load_model(selected_model)
        pairs = [
            (query, str(hit.get("text") or ""))
            for hit in dense_ranked
            for query in queries
        ]
        raw_scores = model.predict(
            pairs,
            batch_size=config.RAG_RERANK_BATCH_SIZE,
            show_progress_bar=False,
        )
        scores = [float(value) for value in raw_scores]
        query_count = len(queries)
        for hit_index, hit in enumerate(dense_ranked):
            start = hit_index * query_count
            candidate_scores = scores[start : start + query_count]
            if len(candidate_scores) != query_count:
                raise ValueError("reranker returned an unexpected number of scores")
            best_index = max(range(query_count), key=candidate_scores.__getitem__)
            hit["rerank_score"] = candidate_scores[best_index]
            hit["rerank_matched_query"] = queries[best_index]
            hit["rerank_applied"] = True
        reranked = sorted(
            dense_ranked,
            key=lambda item: (
                float(item["rerank_score"]),
                float(item["dense_score"] if item.get("dense_score") is not None else -1.0),
            ),
            reverse=True,
        )
        for rank, hit in enumerate(reranked, start=1):
            hit["rank_after"] = rank
        return reranked
    except Exception as exc:
        logger.warning("Document reranker unavailable; using dense order: %s", exc)
        for hit in dense_ranked:
            hit["rerank_error"] = f"{type(exc).__name__}: {exc}"
        return dense_ranked
