"""Tests for cst_agent_workbench.rag.pdf_ingest."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


from cst_agent_workbench.rag.pdf_ingest import (
    classify_topic,
    extract_html_text,
    split_text,
)


# ---------------------------------------------------------------------------
# split_text tests
# ---------------------------------------------------------------------------

class TestSplitText:
    def test_empty_string(self):
        assert split_text("") == []

    def test_whitespace_only(self):
        assert split_text("   \n\n  ") == []

    def test_short_text_single_chunk(self):
        text = "矩形微带贴片天线"
        result = split_text(text, chunk_size=500)
        assert result == [text]

    def test_splits_on_paragraph(self):
        text = "第一段内容。" * 30 + "\n\n" + "第二段内容。" * 30
        result = split_text(text, chunk_size=200, chunk_overlap=40)
        assert len(result) >= 2
        # 第一个 chunk 不应包含第二段开头（如果段落分割生效）
        assert "第二段" not in result[0] or len(result[0]) <= 200

    def test_splits_on_chinese_period(self):
        text = "。".join([f"句子{i}" for i in range(50)])
        result = split_text(text, chunk_size=100, chunk_overlap=20)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 120  # chunk_size + some tolerance

    def test_overlap_present(self):
        text = "A" * 300 + "。" + "B" * 300
        result = split_text(text, chunk_size=200, chunk_overlap=50)
        assert len(result) >= 2
        # 有 overlap 意味着后一个 chunk 的开头可能包含前一个 chunk 的末尾
        # 至少验证没有内容丢失
        combined = "".join(result)
        # 因为 overlap 会重复字符，合并后长度应 >= 原文长度
        assert len(combined) >= len(text.strip())

    def test_respects_chunk_size(self):
        text = "字" * 2000
        result = split_text(text, chunk_size=500, chunk_overlap=80)
        for chunk in result:
            assert len(chunk) <= 500

    def test_separator_inside_overlap_does_not_create_one_character_windows(self):
        text = ("A" * 100 + "\n\n") * 84

        result = split_text(text, chunk_size=1200, chunk_overlap=200)

        assert len(result) < 20
        assert all(len(chunk) <= 1200 for chunk in result)
        assert all(len(chunk) > 200 for chunk in result[:-1])

    @pytest.mark.parametrize(
        ("chunk_size", "chunk_overlap"),
        [(0, 0), (-1, 0), (100, -1), (100, 100), (100, 101)],
    )
    def test_rejects_invalid_chunk_parameters(self, chunk_size, chunk_overlap):
        with pytest.raises(ValueError):
            split_text("text", chunk_size=chunk_size, chunk_overlap=chunk_overlap)


# ---------------------------------------------------------------------------
# classify_topic tests
# ---------------------------------------------------------------------------

class TestClassifyTopic:
    def test_phased_array(self):
        assert classify_topic("CST丛书18算例02_平面相控阵天线快速设计.pdf") == "phased_array"

    def test_radome(self):
        assert classify_topic("CST丛书18算例03_天线罩对透波率和测向精度的影响.pdf") == "radome"
        assert classify_topic("CST丛书18算例07_多层介质雷达罩的透波率和瞄准误差.pdf") == "radome"

    def test_rcs(self):
        assert classify_topic("CST丛书18算例05_相控阵天线的RCS.pdf") == "rcs"

    def test_emc(self):
        assert classify_topic("CST丛书18算例23_机电设备电气总成电磁兼容仿真分析.pdf") == "emc"

    def test_signal_integrity(self):
        assert classify_topic("CST丛书18算例29_高速连接器SI-TDR-眼图-EMI仿真.pdf") == "signal_integrity"

    def test_rfid(self):
        assert classify_topic("CST丛书18算例15_RFID天线.pdf") == "rfid"

    def test_generic_antenna(self):
        assert classify_topic("CST丛书18算例08_汽车玻璃天线.pdf") == "glass_antenna"

    def test_unknown_returns_general(self):
        assert classify_topic("random_document.pdf") == "general"

    def test_reflector(self):
        assert classify_topic("CST丛书18算例12_环焦天线.pdf") == "reflector"

    def test_esd(self):
        assert classify_topic("CST丛书18算例31_电子设备ESD抗扰度仿真.pdf") == "esd"

    def test_circulator(self):
        assert classify_topic("CST丛书18算例17_环流器.pdf") == "circulator"

    def test_stealth(self):
        assert classify_topic("CST丛书18算例37_隐身材料隐身性能及温度特性仿真.pdf") == "rcs"


# ---------------------------------------------------------------------------
# extract_pdf_text — only test importability (real PDF tests need fixtures)
# ---------------------------------------------------------------------------

class TestExtractPdfText:
    def test_nonexistent_file(self):
        from cst_agent_workbench.rag.pdf_ingest import extract_pdf_text
        result = extract_pdf_text("/nonexistent/path.pdf")
        assert result == []


class TestExtractHtmlText:
    def test_nested_inline_tags_do_not_fragment_short_text(self, tmp_path):
        html_path = tmp_path / "port.htm"
        html_path.write_text(
            "<html><body><nav>navigation noise</nav>"
            "<p>Define the <code>waveguide port</code> on the simulation boundary.</p>"
            "</body></html>",
            encoding="utf-8",
        )

        pages = extract_html_text(html_path)

        assert pages == [(1, "Define the waveguide port on the simulation boundary.")]


# ---------------------------------------------------------------------------
# ingest_pdf_directory — test with non-existent dir
# ---------------------------------------------------------------------------

class TestIngestPdfDirectory:
    def test_nonexistent_dir(self):
        from cst_agent_workbench.rag.pdf_ingest import ingest_pdf_directory
        result = ingest_pdf_directory("/nonexistent/dir")
        assert result == {"files": 0, "chunks": 0, "skipped": 0}

    def test_windows_rejects_non_ascii_chroma_persist_path(self, monkeypatch, tmp_path):
        from cst_agent_workbench import config
        from cst_agent_workbench.rag import pdf_ingest

        monkeypatch.setattr(pdf_ingest.sys, "platform", "win32")
        monkeypatch.setattr(config, "CHROMA_PERSIST_DIR", r"D:\含中文\chromadb")

        with pytest.raises(RuntimeError, match="ASCII-only"):
            pdf_ingest.ingest_pdf_directory(tmp_path)

    def test_html_only_ingestion_preserves_relative_provenance(self, monkeypatch, tmp_path):
        from cst_agent_workbench import config
        from cst_agent_workbench.rag import chroma_store
        from cst_agent_workbench.rag.pdf_ingest import ingest_pdf_directory

        html_root = tmp_path / "official-help"
        html_file = html_root / "mws" / "ports" / "waveguide.htm"
        html_file.parent.mkdir(parents=True)
        html_file.write_text(
            "<html><body><p>Waveguide ports are defined on a simulation boundary and excite modes.</p></body></html>",
            encoding="utf-8",
        )

        class FakeCollection:
            def __init__(self):
                self.rows = []

            def get(self, **kwargs):
                return {"ids": []}

            def upsert(self, *, ids, documents, metadatas):
                self.rows.extend(zip(ids, documents, metadatas))

            def count(self):
                return len(self.rows)

            def query(self, **kwargs):
                return {"ids": [[self.rows[0][0]]]}

        collection = FakeCollection()
        monkeypatch.setattr(chroma_store, "_get_collection", lambda: collection)
        cache_root = (
            Path("D:/cst_agent_rag_data/pytest_tmp/pdf_ingest") / tmp_path.name
            if sys.platform == "win32"
            else tmp_path / "cache"
        )
        monkeypatch.setattr(config, "CHROMA_PERSIST_DIR", str(cache_root / "chromadb"))

        try:
            result = ingest_pdf_directory(
                None,
                html_dir=html_root,
                cold_start_validation=False,
            )

            assert result == {"files": 1, "chunks": 1, "skipped": 0}
            metadata = collection.rows[0][2]
            assert collection.rows[0][1].startswith("CST Official Help | mws ports waveguide")
            assert metadata["source_path"] == "mws/ports/waveguide.htm"
            assert metadata["source_type"] == "html"
            assert (cache_root / "ingestion_manifest.json").exists()
        finally:
            shutil.rmtree(cache_root, ignore_errors=True)
