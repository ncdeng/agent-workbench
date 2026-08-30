from __future__ import annotations

from cst_agent_workbench.agent.agent import CSTAgent
from tests.fakes import FakeCSTController


def _agent(monkeypatch, tmp_path):
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path) if path == "~" else path)
    return CSTAgent(FakeCSTController(offline_mode=True, connected=False))


def _hit():
    return {
        "text": "Waveguide ports are defined on the simulation boundary.",
        "source_path": "3D/ports/waveguide.htm",
        "source_type": "html",
        "source_hash": "hash-1",
        "chunk_idx": 4,
        "score": 0.7,
        "dense_score": 0.7,
        "rerank_score": 0.95,
        "rank_before": 5,
        "rank_after": 1,
        "reranker_model": "test-reranker",
        "rerank_applied": True,
    }


def test_executor_prompt_requires_retrieved_source_citations(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path)
    agent.session.metadata["last_rag_context"] = {
        "query": "How is a waveguide port defined?",
        "rules": [],
        "documents": [_hit()],
    }

    messages = agent._build_chat_working_messages(
        "How is a waveguide port defined?",
        {"role": "user", "content": "How is a waveguide port defined?"},
        [],
    )
    rag_prompt = next(
        message["content"]
        for message in messages
        if "本轮检索到的CST官方文档" in str(message.get("content"))
    )

    assert "[source=完整路径, chunk=编号]" in rag_prompt
    assert "不得编造未检索到的来源" in rag_prompt
    assert "事实范围必须限制在这些检索片段直接支持的内容" in rag_prompt
    assert "不得凭常识补充" in rag_prompt
    assert "source=3D/ports/waveguide.htm, chunk=4, rerank=0.950, dense=0.700" in rag_prompt


def test_chat_trace_backfills_planner_rag_context(monkeypatch, tmp_path):
    agent = _agent(monkeypatch, tmp_path)
    agent.session.metadata["last_rag_context"] = {
        "query": "How is a waveguide port defined?",
        "rules": ["Ports belong on a boundary."],
        "documents": [_hit()],
    }

    started, owns_trace = agent._start_chat_trace(
        {"role": "user", "content": "How is a waveguide port defined?"},
        [{"role": "user", "content": "How is a waveguide port defined?"}],
        [],
        [{"role": "user", "content": "How is a waveguide port defined?"}],
    )

    assert started is True
    assert owns_trace is True
    trace_rag = agent.current_trace["rag_context"]
    assert trace_rag["query"] == "How is a waveguide port defined?"
    assert trace_rag["rules"][0]["text"] == "Ports belong on a boundary."
    trace_hit = trace_rag["document"][0]
    assert trace_hit["source_path"] == "3D/ports/waveguide.htm"
    assert trace_hit["dense_score"] == 0.7
    assert trace_hit["rerank_score"] == 0.95
    assert trace_hit["rank_before"] == 5
    assert trace_hit["rank_after"] == 1
