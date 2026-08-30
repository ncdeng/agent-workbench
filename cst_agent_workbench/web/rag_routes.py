from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class RAGImportRequest(BaseModel):
    folder: str = ""
    pdfFolder: str = ""
    htmlFolder: str = ""
    reset: bool = False
    maxFiles: int | None = None


class RAGQueryRequest(BaseModel):
    query: str
    topK: int = 5
    topic: str = ""


def register_rag_routes(app: Any) -> None:
    @app.post("/api/rag/import")
    def rag_import(req: RAGImportRequest):
        try:
            from cst_agent_workbench.rag.pdf_ingest import ingest_pdf_directory

            pdf_folder = req.pdfFolder or req.folder or ""
            html_folder = req.htmlFolder or ""
            if not pdf_folder and not html_folder:
                from cst_agent_workbench import config
                pdf_folder = config.PDF_KNOWLEDGE_DIR
            if not pdf_folder and not html_folder:
                return {"ok": False, "error": "No PDF or HTML folder specified"}
            result = ingest_pdf_directory(
                pdf_folder or None,
                html_dir=html_folder or None,
                reset=req.reset,
                max_files=req.maxFiles,
            )
            processed = int(result.get("files", 0)) + int(result.get("skipped", 0))
            if processed <= 0:
                return {
                    "ok": False,
                    **result,
                    "error": "No documents were imported; check the source folders and RAG index health",
                }
            return {"ok": True, **result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/rag/query")
    def rag_query(req: RAGQueryRequest):
        query = req.query.strip()
        if not query:
            return {"ok": False, "error": "Query must not be empty", "hits": []}
        try:
            from cst_agent_workbench.rag.chroma_store import (
                get_collection_stats,
                query_document_knowledge,
            )

            hits = query_document_knowledge(
                query,
                top_k=max(1, min(int(req.topK), 20)),
                filter_topic=req.topic.strip() or None,
            )
            stats = get_collection_stats()
            return {
                "ok": bool(stats.get("healthy")),
                "query": query,
                "hits": hits,
                "index": stats,
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc), "hits": []}
