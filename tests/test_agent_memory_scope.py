from __future__ import annotations

from pathlib import Path

from cst_agent_workbench.agent.agent import CSTAgent
from cst_agent_workbench.agent.memory import (
    MemoryManager,
    StructuredMemory,
    load_memory,
    save_persistent_memory,
)
from fakes import FakeCSTController


def test_agent_initializes_memory_scope_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR",
        str(tmp_path / "agent-memory"),
    )

    agent = CSTAgent(FakeCSTController())
    projection = agent.session.get_projection()
    scope = projection["memory_scope"]

    session_path = Path(scope["session_memory_path"])
    assert session_path.name == f"{agent.session.session_id}.json"
    assert session_path.parent.name == "sessions"
    assert Path(scope["persistent_memory_path"]).name == "persistent_memory.json"
    assert scope["session_memory_loaded"] is False
    assert scope["persistent_memory_loaded"] is False
    assert "workspace.project_path" not in scope["persistent_fields"]


def test_agent_merges_persistent_memory_without_workspace_state(monkeypatch, tmp_path):
    cache_dir = tmp_path / "agent-memory"
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR",
        str(cache_dir),
    )
    persistent_path = cache_dir / "persistent_memory.json"
    memory = StructuredMemory()
    memory.conversation.constraints = ["Rogers5880"]
    memory.workspace.project_path = "D:/old/project.cst"
    memory.workspace.last_results_summary = {"min_s11_db": -12.0}
    MemoryManager.update_decisions(
        memory,
        strategy_entry={
            "round": 1,
            "lesson": "feed_W 过小导致匹配恶化",
            "failure_pattern": "feed_W mismatch",
        },
    )
    MemoryManager.update_decisions(memory, failure_reason="old failure")
    assert save_persistent_memory(memory, str(persistent_path)) is True

    agent = CSTAgent(FakeCSTController())
    projection = agent.session.get_projection()

    assert projection["memory_scope"]["persistent_memory_loaded"] is True
    assert agent.session.memory.conversation.constraints == ["Rogers5880"]
    assert agent.session.memory.workspace.project_path == ""
    assert agent.session.memory.workspace.last_results_summary == {}
    assert agent.session.memory.decisions.recent_strategies[0]["lesson"] == "feed_W 过小导致匹配恶化"
    assert agent.session.memory.decisions.failure_reasons == ["old failure"]


def test_short_circuit_persists_current_exchange_after_memory_updates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR",
        str(tmp_path / "agent-memory"),
    )
    monkeypatch.setattr(
        CSTAgent,
        "_build_meta_llm_query_response",
        lambda self, message: None,
    )
    monkeypatch.setattr(
        CSTAgent,
        "_run_rectangular_patch_fast_path",
        lambda self, message: "fast path current reply",
    )
    agent = CSTAgent(FakeCSTController())

    assert agent.chat("创建一个7GHz矩形贴片天线") == "fast path current reply"

    saved = load_memory(agent._memory_path)
    assert saved is not None
    assert "创建一个7GHz矩形贴片天线" in saved.conversation.recent_summary
    assert "fast path current reply" in saved.conversation.recent_summary


def test_exception_path_persists_current_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR",
        str(tmp_path / "agent-memory"),
    )
    agent = CSTAgent(FakeCSTController())
    agent.client = object()
    monkeypatch.setattr(
        CSTAgent,
        "_build_chat_working_messages",
        lambda self, user_message, user_entry, filtered_history: [user_entry],
    )
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.run_agent_turn",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("current turn exploded")),
    )

    response = agent.chat("ordinary request", skip_plan=True)

    assert "current turn exploded" in response
    saved = load_memory(agent._memory_path)
    assert saved is not None
    assert "ordinary request" in saved.conversation.recent_summary
    assert "current turn exploded" in saved.decisions.failure_reasons[-1]


def test_chat_persists_explicit_constraint_and_traces_memory_event(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "cst_agent_workbench.agent.agent.config.AGENT_MEMORY_DIR",
        str(tmp_path / "agent-memory"),
    )
    monkeypatch.setattr(
        CSTAgent,
        "_build_meta_llm_query_response",
        lambda self, message: None,
    )
    monkeypatch.setattr(
        CSTAgent,
        "_run_rectangular_patch_fast_path",
        lambda self, message: "fast path constrained reply",
    )
    agent = CSTAgent(FakeCSTController())

    result = agent.chat("创建7GHz贴片天线，必须保持 microstrip feed")

    assert result == "fast path constrained reply"
    saved = load_memory(agent._memory_path)
    assert saved is not None
    assert saved.conversation.constraints == ["创建7GHz贴片天线，必须保持 microstrip feed"]
    trace = agent.trace_history[-1]
    assert any(
        event["action"] == "constraints_updated"
        for event in trace["memory_events"]
    )
