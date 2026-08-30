"""ChromaDB vector store for CST official-document retrieval.

The legacy public helper ``query_pdf_knowledge`` is kept for compatibility, but
the primary interface is ``query_document_knowledge`` which preserves source,
page, topic, distance and score for trace/debug UIs.

ChromaDB is optional. Runtime queries degrade to an empty list, while
``get_collection_stats`` exposes the real initialization/query error instead of
silently presenting a broken index as an empty healthy store.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from cst_agent_workbench import config

logger = logging.getLogger(__name__)

COLLECTION_SCHEMA_VERSION = 4
QUERY_EXPANSION_VERSION = "english_corpus_contract_v1"

_client = None
_collection = None
_init_attempted = False
_last_error = ""

_CST_QUERY_TERM_MAP = (
    ("波导端口", "waveguide port"),
    ("离散端口", "discrete port"),
    ("频域求解器", "frequency domain solver"),
    ("时域求解器", "time domain solver"),
    ("自适应网格加密", "adaptive mesh refinement"),
    ("网格加密", "mesh refinement"),
    ("参数扫描", "parameter sweep"),
    ("扫描参数", "sweep parameters"),
    ("边界条件", "boundary conditions"),
    ("对称面", "symmetry planes"),
    ("远场", "farfield"),
    ("相位中心", "phase center"),
    ("集总元件", "lumped element"),
    ("电路参数", "circuit parameters"),
    ("优化目标", "optimization goal"),
    ("结果模板", "result template"),
    ("周期结构", "periodic structure"),
    ("单元边界", "unit cell boundary"),
)


def _build_query_variants(query: str) -> List[str]:
    """Build a deterministic fallback query for the English CST corpus.

    The glossary is not a translator. Runtime retrieval normally asks the LLM
    for a concise English search query; this fallback only keeps retrieval
    usable when that optional call is unavailable.
    """
    translated: List[str] = []
    matched_chinese: List[str] = []
    for chinese, english in _CST_QUERY_TERM_MAP:
        if chinese in query and not any(chinese in broader for broader in matched_chinese):
            matched_chinese.append(chinese)
            translated.append(english)
    if not translated:
        return [query]
    latin_spans = re.findall(r"[A-Za-z][A-Za-z0-9_+./()-]*(?:\s+[A-Za-z0-9_+./()-]+)*", query)
    terms: List[str] = []
    for term in [*translated, *latin_spans]:
        clean = " ".join(str(term).split()).strip()
        if clean and clean.lower() not in {existing.lower() for existing in terms}:
            terms.append(clean)
    expanded = " ".join(terms)
    if not expanded or expanded == query:
        return [query]
    if str(config.RAG_DOCUMENT_LANGUAGE or "").lower() == "en":
        return [expanded]
    return [query, expanded]


def _embedding_identity() -> tuple[str, str]:
    provider = str(config.EMBEDDING_PROVIDER or "local").strip().lower()
    model = (
        str(config.EMBEDDING_LOCAL_MODEL or "").strip()
        if provider == "local"
        else str(config.EMBEDDING_MODEL or "").strip()
    )
    return provider, model


def _query_instruction_identity() -> str:
    """Canonical instruction identity independent of dotenv whitespace."""
    return str(config.EMBEDDING_QUERY_INSTRUCTION or "").strip()


def _collection_metadata() -> Dict[str, Any]:
    provider, model = _embedding_identity()
    return {
        "hnsw:space": "cosine",
        "cst_rag_schema": COLLECTION_SCHEMA_VERSION,
        "embedding_provider": provider,
        "embedding_model": model,
        "embedding_query_instruction": _query_instruction_identity(),
        "document_language": str(config.RAG_DOCUMENT_LANGUAGE or ""),
        "query_expansion": QUERY_EXPANSION_VERSION,
        "multi_query_fusion": "max_cosine",
    }


def get_build_marker_path() -> Path:
    safe_name = str(config.CHROMA_COLLECTION_NAME).replace("/", "_").replace("\\", "_")
    return Path(config.CHROMA_PERSIST_DIR).parent / f"{safe_name}.building.json"


def _make_embedding_function():
    from chromadb.utils import embedding_functions

    provider, model = _embedding_identity()
    if provider == "local":
        embedding_function = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model,
            normalize_embeddings=True,
        )
        query_instruction = _query_instruction_identity()
        if query_instruction:
            # Chroma calls embed_query() for query_texts and __call__() for
            # documents, allowing the asymmetric BGE input contract without
            # adding the retrieval instruction to indexed passages.
            def _embed_query(input):
                instructed = [f"{query_instruction} {str(text)}" for text in input]
                return embedding_function(instructed)

            embedding_function.embed_query = _embed_query
        return embedding_function

    kwargs: dict = {"model_name": model}
    if config.EMBEDDING_API_KEY:
        kwargs["api_key"] = config.EMBEDDING_API_KEY
    if config.EMBEDDING_BASE_URL:
        kwargs["api_base"] = config.EMBEDDING_BASE_URL
    return embedding_functions.OpenAIEmbeddingFunction(**kwargs)


def _set_error(exc: Exception | str) -> None:
    global _last_error
    _last_error = str(exc)


def _get_collection():
    """Lazily initialize the Chroma collection; return ``None`` on failure."""
    global _client, _collection, _init_attempted, _last_error
    if _collection is not None:
        return _collection
    if _init_attempted:
        return None
    _init_attempted = True
    _last_error = ""

    try:
        import chromadb
    except ImportError as exc:
        _set_error("chromadb is not installed; install the 'rag' extra")
        logger.debug("chromadb not installed, document knowledge disabled: %s", exc)
        return None

    try:
        _client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        _collection = _client.get_or_create_collection(
            name=config.CHROMA_COLLECTION_NAME,
            embedding_function=_make_embedding_function(),
            metadata=_collection_metadata(),
        )
        count = _collection.count()
        logger.info(
            "CST document collection ready: name=%s count=%d",
            config.CHROMA_COLLECTION_NAME,
            count,
        )
        return _collection
    except Exception as exc:
        _collection = None
        _set_error(exc)
        logger.warning("ChromaDB init failed: %s", exc)
        return None


def retry_collection_init() -> bool:
    """Clear a cached transient failure and retry collection initialization."""
    global _collection, _init_attempted, _last_error
    _collection = None
    _init_attempted = False
    _last_error = ""
    return _get_collection() is not None


def is_document_store_queryable() -> bool:
    """Initialize once and report whether official-document retrieval can run."""
    return _get_collection() is not None


def query_document_knowledge(
    query_text: str,
    top_k: int = 3,
    filter_topic: Optional[str] = None,
    *,
    deduplicate_sources: bool = True,
) -> List[Dict[str, Any]]:
    """Return CST document hits with provenance and cosine score.

    The function never raises so the Agent can still operate when the optional
    document index is unavailable. Callers that need diagnostics should inspect
    ``get_collection_stats()['error']``.
    """
    global _last_error
    query = str(query_text or "").strip()
    if not query or top_k <= 0:
        return []
    try:
        col = _get_collection()
        if col is None:
            return []
        count = int(col.count())
        if count == 0:
            return []
        where = {"topic": filter_topic} if filter_topic else None
        candidate_k = min(max(int(top_k) * 2, int(top_k)), count)
        pool: Dict[tuple[str, Any, str], Dict[str, Any]] = {}
        for retrieval_query in _build_query_variants(query):
            results = col.query(
                query_texts=[retrieval_query],
                n_results=candidate_k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            documents = (results.get("documents") or [[]])[0]
            metadatas = (results.get("metadatas") or [[]])[0]
            distances = (results.get("distances") or [[]])[0]
            for index, document in enumerate(documents):
                if not document:
                    continue
                metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
                distance = distances[index] if index < len(distances) else None
                score = None if distance is None else max(-1.0, min(1.0, 1.0 - float(distance)))
                hit = {
                    "text": str(document),
                    "source": str(metadata.get("source") or ""),
                    "source_path": str(metadata.get("source_path") or metadata.get("source") or ""),
                    "source_type": str(metadata.get("source_type") or "document"),
                    "source_hash": str(metadata.get("source_hash") or ""),
                    "page": metadata.get("page"),
                    "chunk_idx": metadata.get("chunk_idx"),
                    "topic": str(metadata.get("topic") or "general"),
                    "distance": None if distance is None else float(distance),
                    "score": score,
                    "dense_score": score,
                    "matched_query": retrieval_query,
                }
                key = (hit["source_path"], hit["chunk_idx"], hit["text"])
                previous = pool.get(key)
                previous_score = previous.get("score") if previous else None
                if previous is None or (score is not None and (previous_score is None or score > previous_score)):
                    pool[key] = hit
        ranked_hits = sorted(
            pool.values(),
            key=lambda item: float(item.get("score") if item.get("score") is not None else -1.0),
            reverse=True,
        )
        hits: List[Dict[str, Any]] = []
        seen_sources: set[str] = set()
        for hit in ranked_hits:
            source_key = str(hit.get("source_path") or hit.get("source") or hit.get("text") or "")
            if deduplicate_sources and source_key in seen_sources:
                continue
            seen_sources.add(source_key)
            hits.append(hit)
            if len(hits) >= int(top_k):
                break
        _last_error = ""
        return hits
    except Exception as exc:
        _set_error(exc)
        logger.warning("CST document query failed: %s", exc)
        return []


def _format_hit_for_prompt(hit: Dict[str, Any]) -> str:
    text = str(hit.get("text") or "").strip()
    source = str(hit.get("source_path") or hit.get("source") or "").strip()
    page = hit.get("page")
    if not source:
        return text
    location = source
    if page not in (None, "", 0, "0"):
        location += f"#page={page}"
    return f"{text}\n[来源: {location}]"


def query_pdf_knowledge(
    query_text: str,
    top_k: int = 3,
    filter_topic: Optional[str] = None,
) -> List[str]:
    """Backward-compatible text-only wrapper with inline source citations."""
    return [
        _format_hit_for_prompt(hit)
        for hit in query_document_knowledge(query_text, top_k=top_k, filter_topic=filter_topic)
    ]


def get_collection_stats() -> Dict[str, Any]:
    """Return actionable index health information for API/UI diagnostics."""
    provider, model = _embedding_identity()
    stats: Dict[str, Any] = {
        "available": False,
        "healthy": False,
        "count": 0,
        "error": _last_error,
        "persist_dir": str(Path(config.CHROMA_PERSIST_DIR)),
        "collection": str(config.CHROMA_COLLECTION_NAME),
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "embedding_provider": provider,
        "embedding_model": model,
        "embedding_query_instruction": _query_instruction_identity(),
        "document_language": str(config.RAG_DOCUMENT_LANGUAGE or ""),
        "query_expansion": QUERY_EXPANSION_VERSION,
        "multi_query_fusion": "max_cosine",
    }
    marker_path = get_build_marker_path()
    if marker_path.exists():
        stats["status"] = "building"
        stats["build_marker"] = str(marker_path)
        try:
            import json

            stats["build"] = json.loads(marker_path.read_text(encoding="utf-8"))
        except Exception:
            stats["build"] = {}
        return stats

    col = _get_collection()
    if col is None:
        # `_get_collection` may have populated `_last_error` after the stats
        # skeleton was created. Refresh it so the health API does not hide a
        # corrupt HNSW error behind a vague "unavailable" state.
        stats["error"] = _last_error
        stats["status"] = "failed" if stats["error"] else "unavailable"
        return stats
    try:
        stats["count"] = int(col.count())
        stats["available"] = True
        stats["healthy"] = True
        stats["error"] = ""
        stats["status"] = "ready" if stats["count"] else "empty"
        metadata = getattr(col, "metadata", None) or {}
        stats["stored_schema_version"] = metadata.get("cst_rag_schema")
        stats["stored_embedding_provider"] = metadata.get("embedding_provider")
        stats["stored_embedding_model"] = metadata.get("embedding_model")
        stats["stored_embedding_query_instruction"] = metadata.get("embedding_query_instruction")
        stats["stored_document_language"] = metadata.get("document_language")
        stats["stored_query_expansion"] = metadata.get("query_expansion")
        stats["stored_multi_query_fusion"] = metadata.get("multi_query_fusion")
        mismatches = []
        if metadata.get("cst_rag_schema") not in (None, COLLECTION_SCHEMA_VERSION):
            mismatches.append(
                f"schema={metadata.get('cst_rag_schema')} (expected {COLLECTION_SCHEMA_VERSION})"
            )
        if metadata.get("embedding_provider") not in (None, provider):
            mismatches.append(
                f"embedding_provider={metadata.get('embedding_provider')} (expected {provider})"
            )
        if metadata.get("embedding_model") not in (None, model):
            mismatches.append(
                f"embedding_model={metadata.get('embedding_model')} (expected {model})"
            )
        expected_instruction = _query_instruction_identity()
        stored_instruction = str(metadata.get("embedding_query_instruction") or "").strip()
        if metadata.get("embedding_query_instruction") is not None and stored_instruction != expected_instruction:
            mismatches.append("embedding_query_instruction does not match the active configuration")
        expected_language = str(config.RAG_DOCUMENT_LANGUAGE or "")
        if metadata.get("document_language") not in (None, expected_language):
            mismatches.append(
                f"document_language={metadata.get('document_language')} (expected {expected_language})"
            )
        if mismatches:
            stats["healthy"] = False
            stats["status"] = "mismatch"
            stats["error"] = "Collection identity mismatch: " + "; ".join(mismatches)
        return stats
    except Exception as exc:
        _set_error(exc)
        stats["error"] = str(exc)
        stats["status"] = "failed"
        return stats


def reset_collection() -> bool:
    """Delete the configured collection and clear cached runtime state.

    Unlike the previous implementation, this works from a fresh process where
    ``_client`` is still ``None``. That is essential for recovering a corrupted
    on-disk HNSW index via ``python -m ...pdf_ingest --reset``.
    """
    global _client, _collection, _init_attempted, _last_error
    try:
        if _client is None:
            import chromadb

            _client = chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)
        try:
            _client.delete_collection(config.CHROMA_COLLECTION_NAME)
        except Exception as exc:
            # Chroma raises when the collection does not exist. Treat that exact
            # situation as an already-clean store, but preserve other failures.
            message = str(exc).lower()
            if "does not exist" not in message and "not found" not in message:
                raise
        _collection = None
        _init_attempted = False
        _last_error = ""
        try:
            get_build_marker_path().unlink(missing_ok=True)
        except OSError:
            pass
        logger.info("ChromaDB collection reset: %s", config.CHROMA_COLLECTION_NAME)
        return True
    except Exception as exc:
        _collection = None
        _init_attempted = True
        _set_error(exc)
        logger.error("reset_collection failed: %s", exc)
        return False
