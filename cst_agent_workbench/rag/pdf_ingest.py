"""Document knowledge ingestion pipeline.

从指定目录提取 PDF / HTML 文本 → 切片 → 存入 ChromaDB。
支持增量导入（按文件内容哈希去重）和 CLI 入口。

PDF 提取用 PyMuPDF（fitz），适合扫描版/纯文字 PDF。
HTML 提取用 html.parser（标准库），剥离 script/style/nav 等噪声标签，
保留正文段落文本。CST Studio Suite 的 Online Help 是 HTML 格式，
比 PDF 提取干净得多——PDF 多栏/表格/图片会导致 PyMuPDF 提取出碎片化文本，
而 HTML 的 DOM 结构天然提供段落边界。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text splitter (inline, ~25 lines, no langchain dependency)
# ---------------------------------------------------------------------------

_SEPARATORS = ["\n\n", "\n", "。", "；", ". ", "; "]


def split_text(
    text: str,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
) -> List[str]:
    """边界感知字符滑窗：按段落 > 换行 > 句子 > 字符级降级。"""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must satisfy 0 <= overlap < chunk_size")

    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            # Do not accept a semantic boundary from the overlap-only prefix.
            # Otherwise ``end - chunk_overlap`` may barely advance (sometimes
            # by one character), exploding a small document into hundreds of
            # almost-identical chunks.
            min_chunk_length = max(chunk_overlap + 1, chunk_size // 2)
            search_start = min(start + min_chunk_length, end)
            best_pos = -1
            for sep in _SEPARATORS:
                pos = text.rfind(sep, search_start, end)
                if pos >= search_start:
                    best_pos = pos + len(sep)
                    break
            if best_pos > start:
                end = best_pos
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        # 下一块起点 = 当前结束位置 - overlap。参数校验和最小切片长度
        # 共同保证每轮都有有意义的前进，而不是 1 字符滑窗退化。
        start = end - chunk_overlap
    return chunks


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_path: str | Path) -> List[Tuple[int, str]]:
    """提取 PDF 每页文本，返回 [(page_number_1based, text), ...]。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.error("PyMuPDF not installed. Run: pip install PyMuPDF")
        return []

    pages: List[Tuple[int, str]] = []
    try:
        doc = fitz.open(str(pdf_path))
        for i, page in enumerate(doc, start=1):
            text = page.get_text()
            # 压缩多余空白
            text = re.sub(r"\s+", " ", text).strip()
            if len(text) < 20:
                continue  # 跳过纯图片页
            pages.append((i, text))
        doc.close()
    except Exception as exc:
        logger.warning("extract_pdf_text failed for %s: %s", pdf_path, exc)
    return pages


# ---------------------------------------------------------------------------
# HTML text extraction (standard library only, no BeautifulSoup dependency)
# ---------------------------------------------------------------------------

# CST Online Help 的 HTML 文件里这些标签是噪声：导航栏、搜索框、脚本、样式表。
# 用 html.parser 标准库剥离它们，只保留正文 <p> / <li> / <td> / <h1>-<h6> 的文本。
_HTML_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "form", "input", "select", "button"}
_HTML_BLOCK_TAGS = {
    "p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6",
    "div", "pre", "dd", "dt", "blockquote", "main", "article", "section",
}


class _HTMLTextExtractor:
    """基于 html.parser 的轻量 HTML 正文提取器。

    剥离 script/style/nav 等噪声标签，保留正文段落文本。
    按块（block-level tags）分段返回，保持段落边界——比 PyMuPDF 从 PDF
    多栏布局里提取出的碎片化文本干净得多。
    """

    def __init__(self):
        from html.parser import HTMLParser
        self._parser = HTMLParser
        self._reset_state()

    def _reset_state(self):
        self._pieces: List[str] = []
        self._current_piece: List[str] = []
        self._skip_depth = 0
        self._content_depth = 0

    def _flush_piece(self) -> None:
        if not self._current_piece:
            return
        text = " ".join(self._current_piece).strip()
        if len(text) >= 20:
            self._pieces.append(text)
        self._current_piece = []

    def extract(self, html_text: str) -> List[str]:
        """提取 HTML 正文段落，返回段落列表（每段是一段连续文本）。"""
        self._reset_state()
        from html.parser import HTMLParser

        class _Parser(HTMLParser):
            def __init__(self, outer):
                super().__init__(convert_charrefs=True)
                self._outer = outer

            def handle_starttag(self, tag, attrs):
                tag_lower = tag.lower()
                if tag_lower in _HTML_SKIP_TAGS:
                    self._outer._skip_depth += 1
                    return
                if tag_lower in _HTML_BLOCK_TAGS and self._outer._skip_depth == 0:
                    # Flush at block boundaries, but do not split inline tags
                    # such as <span>/<code> into tiny fragments.
                    self._outer._flush_piece()
                    self._outer._content_depth += 1

            def handle_endtag(self, tag):
                tag_lower = tag.lower()
                if tag_lower in _HTML_SKIP_TAGS and self._outer._skip_depth > 0:
                    self._outer._skip_depth -= 1
                    return
                if tag_lower in _HTML_BLOCK_TAGS and self._outer._skip_depth == 0:
                    self._outer._flush_piece()
                    if self._outer._content_depth > 0:
                        self._outer._content_depth -= 1

            def handle_data(self, data):
                if self._outer._skip_depth == 0 and self._outer._content_depth > 0:
                    cleaned = re.sub(r"\s+", " ", data).strip()
                    if cleaned:
                        self._outer._current_piece.append(cleaned)

        parser = _Parser(self)
        parser.feed(html_text)
        # 收尾
        self._flush_piece()
        return self._pieces


def extract_html_text(html_path: str | Path) -> List[Tuple[int, str]]:
    """提取 HTML 文件正文段落，返回 [(1, paragraph_text), ...]。

    页码固定为 1（HTML 没有"页"的概念），但保持和 PDF 的接口一致。
    跳过 CST Online Help 的导航/搜索/脚本噪声，只保留正文段落（≥20 字符）。
    """
    try:
        raw = Path(html_path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("extract_html_text failed for %s: %s", html_path, exc)
        return []

    extractor = _HTMLTextExtractor()
    paragraphs = extractor.extract(raw)
    if not paragraphs:
        return []
    return [(1, "\n\n".join(paragraphs))]


# ---------------------------------------------------------------------------
# Topic auto-classification (by filename keywords)
# ---------------------------------------------------------------------------

_TOPIC_KEYWORDS: List[Tuple[List[str], str]] = [
    (["RCS", "隐身", "隐身材料"], "rcs"),
    (["相控阵"], "phased_array"),
    (["天线罩", "雷达罩"], "radome"),
    (["LTCC"], "ltcc"),
    (["玻璃天线"], "glass_antenna"),
    (["喇叭"], "horn_antenna"),
    (["螺旋天线"], "spiral_antenna"),
    (["RFID"], "rfid"),
    (["环流器"], "circulator"),
    (["反射面", "反射阵", "卡塞格伦", "环焦"], "reflector"),
    (["微带"], "microstrip"),
    (["EMC", "电磁兼容", "电磁辐射", "辐射噪声"], "emc"),
    (["SI", "信号完整性", "眼图", "差分对", "高速连接器"], "signal_integrity"),
    (["ESD"], "esd"),
    (["屏效", "屏蔽"], "shielding"),
    (["微放电"], "multipaction"),
    (["穿墙", "成像", "SAR"], "imaging"),
    (["等离子体", "光学"], "photonics"),
    (["滤波器"], "filter"),
    (["天线"], "antenna"),
]


def classify_topic(filename: str) -> str:
    """根据文件名关键词自动分类主题。"""
    for keywords, topic in _TOPIC_KEYWORDS:
        for kw in keywords:
            if kw in filename:
                return topic
    return "general"


# ---------------------------------------------------------------------------
# Main ingestion pipeline
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    """计算文件内容 MD5 前 12 位，用于增量去重。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            h.update(block)
    return h.hexdigest()[:12]


def _document_embedding_text(chunk: str, source_path: str) -> str:
    """Prefix body text with stable CST path terms used by product queries."""
    path_without_suffix = str(Path(source_path).with_suffix(""))
    path_terms = re.sub(r"[^A-Za-z0-9]+", " ", path_without_suffix).strip()
    if not path_terms:
        return chunk
    return f"CST Official Help | {path_terms}\n{chunk}"


def _collection_has_source_hash(collection: Any, source_hash: str) -> bool:
    """Check one source hash without loading metadata for the entire corpus."""
    try:
        result = collection.get(
            where={"source_hash": source_hash},
            limit=1,
            include=[],
        )
        return bool(result.get("ids"))
    except Exception as exc:
        logger.warning("source hash lookup failed; file will be upserted: %s", exc)
        return False


def _write_ingestion_manifest(payload: Dict[str, Any]) -> None:
    """Atomically persist the last ingestion run beside the Chroma directory."""
    try:
        from cst_agent_workbench import config

        manifest_path = Path(config.CHROMA_PERSIST_DIR).parent / "ingestion_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = manifest_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(manifest_path)
    except Exception as exc:
        logger.warning("failed to write RAG ingestion manifest: %s", exc)


def ingest_pdf_directory(
    pdf_dir: str | Path | None,
    reset: bool = False,
    chunk_size: int = 1200,
    chunk_overlap: int = 200,
    html_dir: str | Path | None = None,
    max_files: int | None = None,
    cold_start_validation: bool = True,
) -> Dict[str, int]:
    """从指定目录导入 PDF（和可选 HTML）到 ChromaDB 知识库。

    Args:
        pdf_dir: PDF 文件目录
        reset: 是否清空已有数据
        chunk_size / chunk_overlap: 切片参数
        html_dir: 可选的 HTML 文件目录（如 CST Online Help），
                  HTML 提取比 PDF 干净（DOM 段落边界 vs PDF 多栏碎片）
        max_files: 可选 smoke-test 上限；正式构建保持 None 导入全部文件
        cold_start_validation: 完成后用独立 Python 进程重新打开并查询索引；
                               仅单元测试替身应关闭

    Returns: {"files": 处理文件数, "chunks": 新增块数, "skipped": 跳过文件数}
    """
    from cst_agent_workbench.rag.chroma_store import (
        COLLECTION_SCHEMA_VERSION,
        _get_collection,
        get_build_marker_path,
        reset_collection,
    )
    from cst_agent_workbench import config as rag_config

    pdf_root = Path(pdf_dir) if pdf_dir else None
    html_root = Path(html_dir) if html_dir else None
    if pdf_root is None and html_root is None:
        logger.error("PDF or HTML directory is required")
        return {"files": 0, "chunks": 0, "skipped": 0}
    if pdf_root is not None and not pdf_root.is_dir():
        logger.error("PDF directory not found: %s", pdf_root)
        return {"files": 0, "chunks": 0, "skipped": 0}
    if html_root is not None and not html_root.is_dir():
        logger.error("HTML directory not found: %s", html_root)
        return {"files": 0, "chunks": 0, "skipped": 0}

    if sys.platform == "win32":
        try:
            str(Path(rag_config.CHROMA_PERSIST_DIR)).encode("ascii")
        except UnicodeEncodeError as exc:
            raise RuntimeError(
                "CHROMA_PERSIST_DIR must use an ASCII-only path on Windows; "
                "native HNSW persistence can create an unreadable index under non-ASCII paths"
            ) from exc

    if reset:
        if not reset_collection():
            logger.error("ChromaDB collection could not be reset")
            return {"files": 0, "chunks": 0, "skipped": 0}

    build_marker = get_build_marker_path()
    build_marker.parent.mkdir(parents=True, exist_ok=True)
    build_marker.write_text(
        json.dumps(
            {
                "started_at": datetime.now(timezone.utc).isoformat(),
                "pdf_root": str(pdf_root.resolve()) if pdf_root is not None else "",
                "html_root": str(html_root.resolve()) if html_root is not None else "",
                "chunk_size": int(chunk_size),
                "chunk_overlap": int(chunk_overlap),
                "max_files": max_files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    col = _get_collection()
    if col is None:
        logger.error("ChromaDB collection unavailable, cannot ingest")
        return {"files": 0, "chunks": 0, "skipped": 0}

    pending_ids: List[str] = []
    pending_docs: List[str] = []
    pending_metas: List[dict] = []
    run_source_hashes: set[str] = set()
    embedding_batch_size = 100

    def _flush_pending(*, force: bool = False) -> None:
        """Embed/upsert full cross-file batches instead of tiny per-file calls."""
        while pending_ids and (force or len(pending_ids) >= embedding_batch_size):
            take = min(embedding_batch_size, len(pending_ids))
            col.upsert(
                ids=pending_ids[:take],
                documents=pending_docs[:take],
                metadatas=pending_metas[:take],
            )
            del pending_ids[:take]
            del pending_docs[:take]
            del pending_metas[:take]

    def _ingest_one_file(
        file_path: Path,
        extract_fn,
        stats: dict,
        *,
        source_root: Path,
        source_type: str,
    ) -> None:
        """通用的单文件导入逻辑，PDF 和 HTML 共用。"""
        source_hash = _file_hash(file_path)
        if source_hash in run_source_hashes or _collection_has_source_hash(col, source_hash):
            logger.debug("skip already ingested: %s", file_path.name)
            stats["skipped"] += 1
            return

        pages = extract_fn(file_path)
        if not pages:
            stats["skipped"] += 1
            return

        topic = classify_topic(file_path.name)
        try:
            source_path = file_path.relative_to(source_root).as_posix()
        except ValueError:
            source_path = file_path.name
        batch_ids: List[str] = []
        batch_docs: List[str] = []
        batch_metas: List[dict] = []

        for page_num, page_text in pages:
            chunks = split_text(page_text, chunk_size, chunk_overlap)
            for ci, chunk in enumerate(chunks):
                doc_id = f"{source_hash}_p{page_num}_c{ci}"
                batch_ids.append(doc_id)
                batch_docs.append(_document_embedding_text(chunk, source_path))
                batch_metas.append({
                    "source": file_path.name,
                    "source_path": source_path,
                    "source_type": source_type,
                    "source_hash": source_hash,
                    "page": page_num,
                    "chunk_idx": ci,
                    "topic": topic,
                })

        # Deterministic chunk IDs make interrupted imports resumable. Keeping
        # pending buffers across files avoids one model.encode call per small
        # HTML page, which dominated CPU build time for the English base model.
        pending_ids.extend(batch_ids)
        pending_docs.extend(batch_docs)
        pending_metas.extend(batch_metas)
        run_source_hashes.add(source_hash)
        _flush_pending()

        stats["files"] += 1
        stats["chunks"] += len(batch_ids)
        logger.debug("ingested %s: %d chunks (topic=%s)", file_path.name, len(batch_ids), topic)
        if stats["files"] % 100 == 0:
            logger.info(
                "ingestion progress: %d files, %d chunks, %d skipped",
                stats["files"],
                stats["chunks"],
                stats["skipped"],
            )

    stats = {"files": 0, "chunks": 0, "skipped": 0}
    remaining = None if max_files is None else max(0, int(max_files))

    # 导入 PDF
    if pdf_root is not None:
        pdf_files = sorted(pdf_root.rglob("*.pdf"))
        if remaining is not None:
            pdf_files = pdf_files[:remaining]
        for pdf_path in pdf_files:
            _ingest_one_file(
                pdf_path,
                extract_pdf_text,
                stats,
                source_root=pdf_root,
                source_type="pdf",
            )
        if remaining is not None:
            remaining = max(0, remaining - len(pdf_files))

    # 导入 HTML（如果指定了 html_dir）
    if html_root is not None:
        html_dir_path = html_root
        if html_dir_path.is_dir():
            # 递归扫描所有 .htm 和 .html 文件，跳过框架/导航/索引文件
            skip_names = {"cshdat_robohelp.htm", "cshdat_webhelp.htm", "index.htm",
                         "index_csh.htm", "index_rhc.htm", "search_redirect.htm",
                         "whcsh_home.htm", "whfbody.htm", "whfdhtml.htm", "whfform.htm",
                         "whgbody.htm", "whgdef.htm", "whgdhtml.htm", "whibody.htm",
                         "whidhtml.htm", "whiform.htm", "whnjs.htm", "whproj.htm",
                         "whskin_banner.htm", "whskin_blank.htm", "menu.htm",
                         "user_scipts.htm", "cst_studio_suite_help.htm",
                         "cst_studio_suite_help_csh.htm", "cst_studio_suite_help_rhc.htm"}
            html_files = sorted(
                f for f in html_dir_path.rglob("*.htm*")
                if f.name.lower().endswith((".htm", ".html")) and f.name not in skip_names
            )
            if remaining is not None:
                html_files = html_files[:remaining]
            logger.info("found %d HTML files in %s", len(html_files), html_dir_path)
            for html_path in html_files:
                _ingest_one_file(
                    html_path,
                    extract_html_text,
                    stats,
                    source_root=html_dir_path,
                    source_type="html",
                )
        else:
            logger.warning("HTML directory not found: %s", html_dir)

    _flush_pending(force=True)

    logger.info(
        "ingestion complete: %d files, %d chunks, %d skipped",
        stats["files"],
        stats["chunks"],
        stats["skipped"],
    )
    # A SQLite row count alone does not prove that Chroma's HNSW segment is
    # readable. A previously corrupted persist directory can accept metadata
    # writes and still fail on the first ANN query, so validate the actual
    # retrieval path before declaring the build complete or removing marker.
    index_count = int(col.count())
    if index_count <= 0:
        raise RuntimeError("RAG build produced an empty collection")
    smoke = col.query(
        query_texts=["CST waveguide port"],
        n_results=1,
        include=["documents", "metadatas", "distances"],
    )
    if not (smoke.get("ids") or [[]])[0]:
        raise RuntimeError("RAG build smoke query returned no hits")
    if cold_start_validation:
        validation_code = """
import sys
from cst_agent_workbench.rag import chroma_store
hits = chroma_store.query_document_knowledge('CST waveguide port', top_k=1)
if not hits:
    print(chroma_store._last_error, file=sys.stderr)
    raise SystemExit(2)
print(hits[0].get('source_path', ''))
"""
        completed = subprocess.run(
            [sys.executable, "-c", validation_code],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown cold-start failure").strip()
            raise RuntimeError(f"RAG cold-start validation failed: {detail[-1000:]}")

    from cst_agent_workbench import config as _config

    _write_ingestion_manifest(
        {
            "status": "ready",
            "schema_version": COLLECTION_SCHEMA_VERSION,
            "pdf_root": str(pdf_root.resolve()) if pdf_root is not None else "",
            "html_root": str(html_root.resolve()) if html_root is not None else "",
            "chunk_size": int(chunk_size),
            "chunk_overlap": int(chunk_overlap),
            "max_files": max_files,
            "embedding_provider": _config.EMBEDDING_PROVIDER,
            "embedding_model": (
                _config.EMBEDDING_LOCAL_MODEL
                if _config.EMBEDDING_PROVIDER == "local"
                else _config.EMBEDDING_MODEL
            ),
            "embedding_query_instruction": _config.EMBEDDING_QUERY_INSTRUCTION.strip(),
            "document_language": _config.RAG_DOCUMENT_LANGUAGE,
            "index_count": index_count,
            "cold_start_validated": bool(cold_start_validation),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            **stats,
        }
    )
    build_marker.unlink(missing_ok=True)
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from cst_agent_workbench import config as _cfg

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Import PDF/HTML files into ChromaDB knowledge base")
    parser.add_argument("--pdf-dir", default=_cfg.PDF_KNOWLEDGE_DIR or None, help="PDF directory path")
    parser.add_argument("--html-dir", default=None, help="HTML directory path (e.g. CST Online Help)")
    parser.add_argument("--reset", action="store_true", help="Delete existing data before import")
    parser.add_argument("--chunk-size", type=int, default=1200)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    parser.add_argument("--max-files", type=int, default=None, help="Optional smoke-test file limit")
    args = parser.parse_args()

    if not args.pdf_dir and not args.html_dir:
        parser.error("--pdf-dir or --html-dir is required")

    result = ingest_pdf_directory(
        args.pdf_dir,
        reset=args.reset,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        html_dir=args.html_dir,
        max_files=args.max_files,
    )
    print(f"Done: {result['files']} files, {result['chunks']} chunks, {result['skipped']} skipped")
